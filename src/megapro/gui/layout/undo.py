"""排版页撤销/重做命令（QUndoStack）。

命令直接操作 model.Item 与 PolylineItem（QGraphicsItem 非 QObject，命令持引用）。
布局页的每个编辑动作都应包成命令 push 到 QUndoStack。
"""

from __future__ import annotations

import copy

from PySide6 import QtGui

from .items import PolylineItem
from .model import Document, Item

__all__ = [
    "AddItemsCommand",
    "RemoveItemsCommand",
    "MoveItemsCommand",
    "ChangeItemPropsCommand",
    "EditTextCommand",
    "ClearCommand",
]


class _PageCommand(QtGui.QUndoCommand):
    """基类：持 layout page 引用，便于增删场景项。"""

    def __init__(self, page, text: str):
        super().__init__(text)
        self.page = page


def _make_gi(page, item: Item) -> PolylineItem:
    gi = PolylineItem(item, grid_pitch=page.snap_pitch, color="#0a5cff")
    gi._page = page  # 供双击重编文字回调
    gi.setZValue(item.z)
    gi.setVisible(item.visible)
    page.scene.addItem(gi)
    page._scene_items.append(gi)
    if item not in page.doc.items:
        page.doc.add(item)
    return gi


class AddItemsCommand(_PageCommand):
    """添加图元（含文字/图片/绘制/导入）。"""

    def __init__(self, page, items: list[Item], text: str = "添加"):
        super().__init__(page, text)
        self.items = list(items)

    def redo(self) -> None:
        for it in self.items:
            _make_gi(self.page, it)
        self.page._after_change()

    def undo(self) -> None:
        for it in self.items:
            self.page._remove_item_obj(it)
        self.page._after_change()


class RemoveItemsCommand(_PageCommand):
    """删除图元。"""

    def __init__(self, page, items: list[Item], text: str = "删除"):
        super().__init__(page, text)
        self.items = list(items)

    def redo(self) -> None:
        for it in self.items:
            self.page._remove_item_obj(it)
        self.page._after_change()

    def undo(self) -> None:
        for it in self.items:
            _make_gi(self.page, it)
        self.page._after_change()


class MoveItemsCommand(_PageCommand):
    """移动图元（记录新旧 pos，用于拖拽/箭头微调）。"""

    def __init__(self, page, moves: list[tuple[Item, tuple, tuple]],
                 text: str = "移动"):
        # moves: [(item, old_pos, new_pos), ...]
        super().__init__(page, text)
        self.moves = moves

    def redo(self) -> None:
        for it, _old, new in self.moves:
            it.pos = new
            gi = self.page._gi_for(it)
            if gi is not None:
                gi.setPos(new[0], self.page._bed_h - new[1])
        self.page._after_change()

    def undo(self) -> None:
        for it, old, _new in self.moves:
            it.pos = old
            gi = self.page._gi_for(it)
            if gi is not None:
                gi.setPos(old[0], self.page._bed_h - old[1])
        self.page._after_change()

    def id(self) -> int:  # 拖拽连续移动合并为一条
        return 1001

    def mergeWith(self, other) -> bool:  # noqa: N802
        if other.id() != self.id() or len(other.moves) != len(self.moves):
            return False
        merged = []
        for (it, o1, n1), (it2, o2, n2) in zip(self.moves, other.moves):
            if it is not it2:
                return False
            merged.append((it, o1, n2))
        self.moves = merged
        return True


class ChangeItemPropsCommand(_PageCommand):
    """改图元属性（pos/scale/angle/z/visible/locked/name）。"""

    def __init__(self, page, changes: list[tuple[Item, dict, dict]],
                 text: str = "修改属性"):
        # changes: [(item, old_dict, new_dict), ...]
        super().__init__(page, text)
        self.changes = changes

    def _apply(self, idx: int) -> None:
        for it, old, new in self.changes:
            d = old if idx == 0 else new
            for k, v in d.items():
                setattr(it, k, v)
            self.page._sync_gi(it)
        self.page._after_change()

    def redo(self) -> None:
        self._apply(1)

    def undo(self) -> None:
        self._apply(0)


class EditTextCommand(_PageCommand):
    """重编文字：换新 paths + text_spec。"""

    def __init__(self, page, item: Item, new_paths, new_spec: dict,
                 text: str = "编辑文字"):
        super().__init__(page, text)
        self.item = item
        self.new_paths = new_paths
        self.new_spec = new_spec
        self.old_paths = copy.deepcopy(item.paths)
        self.old_spec = copy.deepcopy(item.text_spec)

    def _apply(self, paths, spec) -> None:
        self.item.paths = copy.deepcopy(paths)
        self.item.text_spec = copy.deepcopy(spec)
        gi = self.page._gi_for(self.item)
        if gi is not None:
            gi.prepareGeometryChange()
            gi.update()
        self.page._after_change()

    def redo(self) -> None:
        self._apply(self.new_paths, self.new_spec)

    def undo(self) -> None:
        self._apply(self.old_paths, self.old_spec)


class ClearCommand(_PageCommand):
    """清空全部（可撤销）。"""

    def __init__(self, page, text: str = "清空"):
        super().__init__(page, text)
        self.saved: list[Item] = []

    def redo(self) -> None:
        self.saved = list(self.page.doc.items)
        for it in list(self.saved):
            self.page._remove_item_obj(it)
        self.page._after_change()

    def undo(self) -> None:
        for it in self.saved:
            _make_gi(self.page, it)
        self.page._after_change()
