"""排版页撤销/重做命令（QUndoStack）—— 自 layout/undo.py 迁入（阶段 2）。

契约（§2.2 canvas/undo_cmds.py）：
- 命令只写 ``model.Item``，场景同步唯一入口 ``page._sync_gi``（setPos 无
  ``bed_h − y`` 翻转 —— 场景 ≡ 纸面 y-up）；Add/Remove 保持重建式
  :func:`make_gi`；命令不长期持 gi（QGraphicsItem 非 QObject）。
- :class:`MoveItemsCommand` 带**手势 token**（press→release 一个 token）：
  ``id()`` 不再恒 1001 而是手势唯一 id，``mergeWith`` **先比 token** ——
  两次独立拖拽产生两条独立可撤销命令（旧实现会误并成一条）。
- QUndoStack::push 会立即执行 redo → redo 必须幂等（setPos(new) ✓）。
"""

from __future__ import annotations

import copy
from itertools import count

from PySide6 import QtGui

from megapro.gui.canvas.items import PathItem
from megapro.gui.layout.model import Item

__all__ = [
    "make_gi",
    "new_gesture_token",
    "AddItemsCommand",
    "RemoveItemsCommand",
    "MoveItemsCommand",
    "ChangeItemPropsCommand",
    "EditTextCommand",
    "ClearCommand",
]

#: 手势 token 序号（press→release 一个 token；同时充当 mergeWith 的 id）
_GESTURE_SEQ = count(1)


def new_gesture_token() -> int:
    """发新手势 token（press→release 一个 token）。

    同 token 的连续命令可被 QUndoStack 合并；token 不同绝不合并。
    """
    return next(_GESTURE_SEQ)


def make_gi(page, item: Item) -> PathItem:
    """重建式构造场景项并挂进 page（Add/Remove undo 的唯一重建入口）。"""
    gi = PathItem(item, color="#0a5cff")
    gi._page = page  # 供拖动回写 / 双击重编文字回调
    gi.setZValue(item.z)
    gi.setVisible(item.visible)
    page.scene.addItem(gi)
    page._scene_items.append(gi)
    if item not in page.doc.items:
        page.doc.add(item)
    return gi


class _PageCommand(QtGui.QUndoCommand):
    """基类：持 layout page 引用，便于增删场景项。"""

    def __init__(self, page, text: str):
        super().__init__(text)
        self.page = page


class AddItemsCommand(_PageCommand):
    """添加图元（含文字/图片/绘制/导入）。"""

    def __init__(self, page, items: list[Item], text: str = "添加"):
        super().__init__(page, text)
        self.items = list(items)

    def redo(self) -> None:
        for it in self.items:
            make_gi(self.page, it)
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
            make_gi(self.page, it)
        self.page._after_change()


class MoveItemsCommand(_PageCommand):
    """移动图元（记录新旧 pos，用于拖拽/箭头微调）。

    带手势 token：同一次 press→release 产生的命令共享 token 并可合并；
    不同手势（两次独立拖拽）token 不同 → 不合并、各自成条。
    """

    def __init__(
        self, page, moves: list[tuple[Item, tuple, tuple]],
        text: str = "移动", token: int | None = None,
    ):
        # moves: [(item, old_pos, new_pos), ...]
        super().__init__(page, text)
        self.moves = list(moves)
        self._token = int(token) if token is not None else new_gesture_token()

    def redo(self) -> None:
        for it, _old, new in self.moves:
            it.pos = (float(new[0]), float(new[1]))
            self.page._sync_gi(it)
        self.page._after_change()

    def undo(self) -> None:
        for it, old, _new in self.moves:
            it.pos = (float(old[0]), float(old[1]))
            self.page._sync_gi(it)
        self.page._after_change()

    def id(self) -> int:
        """手势唯一 id（不再恒 1001）；同手势才可能被 QUndoStack 询问合并。"""
        return self._token

    def mergeWith(self, other) -> bool:  # noqa: N802
        if getattr(other, "_token", None) != self._token:
            return False  # 先比 token：不同手势绝不合并
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
    """改图元属性（pos/scale/angle/z/visible/locked/name）。

    可选手势 token（评审 low #6）：同 token 的连续命令合并为一条 —— 数值输入
    （QDoubleSpinBox 键入 "100" = 1→10→100 三次 valueChanged）按手势聚合，
    撤销栈不再按击键碎片化；token 不同（换字段/换选中/编辑结束）绝不合并。
    手柄/对齐/层序等一次性命令不传 token（各自独立成条）。
    """

    def __init__(self, page, changes: list[tuple[Item, dict, dict]],
                 text: str = "修改属性", token: int | None = None):
        # changes: [(item, old_dict, new_dict), ...]
        super().__init__(page, text)
        self.changes = changes
        self._token = int(token) if token is not None else new_gesture_token()

    def id(self) -> int:
        """手势唯一 id；同手势才可能被 QUndoStack 询问合并。"""
        return self._token

    def mergeWith(self, other) -> bool:  # noqa: N802
        if getattr(other, "_token", None) != self._token:
            return False  # 先比 token：不同手势绝不合并
        if other.id() != self.id() or len(other.changes) != len(self.changes):
            return False
        merged = []
        for (it, o1, n1), (it2, o2, n2) in zip(self.changes, other.changes):
            if it is not it2 or set(o1) != set(o2):
                return False  # 目标/字段集不同不合并
            merged.append((it, o1, n2))  # 等效两命令之和：保最早 old + 最新 new
        self.changes = merged
        return True

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
            gi.rebuild_path()  # 内含 prepareGeometryChange()
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
            make_gi(self.page, it)
        self.page._after_change()
