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
from megapro.gui.layout.model import (
    DetachInfo, Item, effectively_visible, iter_leaves,
)

__all__ = [
    "make_gi",
    "new_gesture_token",
    "AddItemsCommand",
    "RemoveItemsCommand",
    "MoveItemsCommand",
    "ChangeItemPropsCommand",
    "EditTextCommand",
    "RetraceImageCommand",
    "ClearCommand",
    "GroupCommand",
    "UngroupCommand",
]

#: 手势 token 序号（press→release 一个 token；同时充当 mergeWith 的 id）
_GESTURE_SEQ = count(1)

#: **改变画笔几何**的字段（FR-08）：走 setattr 通道时必须额外
#: ``gi.rebuild_path()``，否则场景不更新（``_sync_gi`` → ``apply_model_state``
#: 只应用 pos/scale/rotation，不重建 QPainterPath）。
#: - ``mirror_x``/``mirror_y``：镜像烘在**画笔路径**里（绕本地 bbox 中心）。
#: - ``paths``：直接换折线集合（同 :class:`EditTextCommand` 的场景）。
#: pos/scale/angle_deg **不在**此列 —— 它们由 Qt 场景变换承担，``_sync_gi``
#: 已正确应用。
_GEOMETRY_FIELDS = frozenset({"mirror_x", "mirror_y", "paths"})


def new_gesture_token() -> int:
    """发新手势 token（press→release 一个 token）。

    同 token 的连续命令可被 QUndoStack 合并；token 不同绝不合并。
    """
    return next(_GESTURE_SEQ)


def make_gi(page, item: Item) -> PathItem | None:
    """重建式构造场景项并挂进 page（Add/Remove undo 的唯一重建入口）。

    **容器不建场景项**（M2 兑现 M1 画布侧硬约束，``model.py`` 模块 docstring）：
    容器 ``paths`` 为空 ⇒ ``PathItem`` 画不出东西，而 ``bbox()`` 递归后非零
    ⇒ 覆盖整组的隐形可点矩形（画不出、点得到）。渲染单元 = :func:`iter_leaves`。

    **入模判据用** ``doc.contains_anywhere``（**全文档**身份查找）**而非
    「是否在顶层列表」**（M1 评审实测的「同一几何切两遍」）：组内叶子不在
    ``doc.items`` 里，旧判据 ``item not in page.doc.items`` 会把每个组内叶子
    **追加成第二个顶层 Item** → ``doc.items=['G','a','b']`` 而真实几何单元
    只有 2 条 ⇒ 切纸机上重复下刀。也**不能**用当前页的 ``doc.contains``：
    页被删/重排后命令可能在**别的页**上跑，对象挂在**另一页** ⇒ 当前页判
    False ⇒ ``doc.add`` 把它挂成第二份（A1：同一几何切两遍）。

    可见性用**祖先链全 visible**（M1 连带契约）：``iter_leaves`` 默认
    ``visible_only=False``（隐藏→显示可切换），故 ``item.visible`` 单独不足以
    表达「容器隐藏、叶子 visible」—— 那样会把组内几何画出来。
    """
    if item.is_container():
        return None
    gi = PathItem(item, color="#0a5cff")
    gi._page = page  # 供拖动回写 / 双击重编文字回调
    gi.setZValue(item.z)
    gi.setVisible(effectively_visible(page.doc, item))
    page.scene.addItem(gi)
    page._scene_items.append(gi)
    if not page.doc.contains_anywhere(item):
        page.doc.add(item)
    return gi


class _PageCommand(QtGui.QUndoCommand):
    """基类：持 layout page 引用，便于增删场景项。

    **页归属（FR-09 / PRD §11-2 定稿：单栈 + 命令页归属）**：命令在构造时
    记下**当时的页对象**；``redo``/``undo`` 执行前先切到那一页，执行后恢复
    调用者当时所在的页。

    不用「每页独立栈」的理由：用户按 Ctrl+Z 的心智是**时间上的上一步**，
    拆成每页一栈后「撤销」在页 B 上只能退 B 的历史、无法退「刚才在页 A 上
    做的编辑」，且工具条「撤销」按钮文案/可用性要在多栈间仲裁 —— 反而更
    易错。单栈保持一条严格时间线，页归属只保证**模型改在正确的页上**。

    场景是**一个**扁平场景，故切页必然重建（``page._rebuild_scene()``）：
    撤销一条别的页的命令时，用户会看到画面切到那一页 —— 这正是「撤销在
    撤销那一步」的正确表现。

    **归属锚是页对象身份，不是下标**（A1 修）。页操作**不进撤销栈**
    （PRD 未要求），故删页/重排后栈里残留命令的下标必然漂移：删首屏会让
    每条残留命令的 home 整体少 1、越界的那条又退回 ``doc.current``。
    实跑（3 页各 1 图元 → 删首屏 → undo 一次 + redo 一次）：``p2a`` 的
    home=2 越界退回 0，在 p0 上做入模判定 → **同一个对象同时挂在 p0 与
    p1**，合并两页几何同一条线出现两次 = 切纸机下两遍刀；删中间/末页则是
    被删页的内容**复活**到某个存活页。``move_page`` 重排是同一根因
    （下标漂移，撤销打到别的页）。改用页对象身份后两者一并消失：

    - ``add_page``/``duplicate_page`` 造**新** ``Page`` 对象、``remove_page``
      只是丢引用、``move_page`` 移动引用 ⇒ **身份在增删/重排下稳定**；
    - 锚对象被删则**永不回来** ⇒ :meth:`_resolve_home` 每次都返回 ``None``，
      命令稳定地降级为空操作 ⇒ **不累积错位**（改前每次 undo 都把
      ``_home_page`` 改写成 current，多个来回逐次漂移）。

    :meth:`_run` 在锚页已删时**整个空操作**（不动模型、不动场景、不动视图
    页）—— 这是唯一语义正确的选择：命令改的图元随它的页一起消失了，撤销
    「编辑一个不存在的页」只能是「什么都不做」。旧实现的「退回当前页执行」
    恰恰是**错**的：它把命令应用到不相干的页上。
    """

    def __init__(self, page, text: str):
        super().__init__(text)
        self.page = page
        doc = getattr(page, "doc", None)
        self._home_page: int = getattr(doc, "current", 0) if doc else 0
        # 页锚 = 页对象身份（活引用）。``None`` 只在 page 无 doc 时出现，
        # 此时命令本就无处执行（与旧 ``_home_page`` 退化成 0 的情形一致）。
        self._home_page_obj = doc.page if doc is not None else None

    def _resolve_home(self) -> int | None:
        """本命令归属页的**当前**下标；锚页已删（或无 doc）返回 ``None``。

        刻意用显式循环 + ``is`` 而非 ``doc.pages.index(obj)``：**``Page`` 是
        dataclass（``eq=True``）**，两个空页 ``Page() == Page()`` 为真 ⇒
        ``index()`` 会把另一个页当成锚页命中。同理不得写 ``pg == anchor``。
        """
        if self._home_page_obj is None:
            return None
        for i, pg in enumerate(self.page.doc.pages):
            if pg is self._home_page_obj:
                return i
        return None

    def _activate_page(self, home: int) -> int:
        """切到本命令的归属页（若已在该页则无副作用）；返回切换前的页下标。"""
        doc = self.page.doc
        prev = doc.current
        if prev != home:
            doc.switch_page(home)
            self.page._rebuild_scene()
        return prev

    def _restore_page(self, prev: int) -> None:
        """执行完把视图页恢复成调用者所在的页（跨页撤销时不抢走用户位置）。"""
        doc = self.page.doc
        if doc.current != prev:
            doc.switch_page(prev)
            self.page._rebuild_scene()

    def _run(self, fn) -> None:
        """在归属页上执行 ``fn``，随后恢复调用者的页。

        **归属页已被删除 ⇒ 整个命令空操作**（不动模型/场景/视图页，不抛）。
        旧实现是「下标越界就退回 ``doc.current`` 执行」，那是 A1 的病灶：
        残留命令被应用到**不相干的页**上，把图元挂错页甚至挂成两份。

        空操作**不累积**：锚页对象一旦被删就永不再进 ``doc.pages``，故每次
        redo 都解析到同一个 ``None``，反复 undo/redo 结果稳定。
        """
        home = self._resolve_home()
        if home is None:
            return
        self._home_page = home  # 供调试/子类读取的最近一次归属下标
        prev = self._activate_page(home)
        try:
            fn()
        finally:
            self._restore_page(prev)


class AddItemsCommand(_PageCommand):
    """添加图元（含文字/图片/绘制/导入/**编组容器**）。

    场景项按 :func:`iter_leaves` 建（容器自身不建 PathItem，``make_gi`` 跳过），
    模型侧只挂**顶层**输入 —— 组内叶子不可重复 ``doc.add``（见 :func:`make_gi`）。
    """

    def __init__(self, page, items: list[Item], text: str = "添加"):
        super().__init__(page, text)
        self.items = list(items)

    def _rebuild_scene(self) -> None:
        for top in self.items:
            # **全文档**判据（``contains_anywhere``，不是当前页 ``contains``）：
            # 页删除/重排后本命令可能跑在归属页之外的页上，此时对象挂在别的
            # 页里；用当前页判「不在」⇒ ``doc.add`` 挂成第二份 ⇒ 同一几何切
            # 两遍（A1）。
            if not self.page.doc.contains_anywhere(top):
                self.page.doc.add(top)
            for leaf in iter_leaves([top]):
                if self.page._gi_for(leaf) is None:
                    make_gi(self.page, leaf)

    def _drop_scene(self) -> None:
        for top in self.items:
            for leaf in iter_leaves([top]):
                gi = self.page._gi_for(leaf)
                if gi is not None:
                    self.page.scene.removeItem(gi)
                    if gi in self.page._scene_items:
                        self.page._scene_items.remove(gi)
            self.page.doc.remove(top)

    def redo(self) -> None:
        self._run(self._do_redo)

    def _do_redo(self) -> None:
        self._rebuild_scene()
        self.page._after_change()

    def undo(self) -> None:
        self._run(self._do_undo)

    def _do_undo(self) -> None:
        self._drop_scene()
        self.page._after_change()


class RemoveItemsCommand(_PageCommand):
    """删除图元（顶层或**组内**，组感知，M2）。

    **归属回执必需**（M1 评审裁决）：``doc.remove`` 返回 :class:`DetachInfo`
    （原容器 + 原下标），undo 据此 ``attach`` 回原容器 —— 否则 ``make_gi``
    走 ``doc.add`` 把叶子挂成顶层项、**组被静默解散**（无异常、几何还看得见，
    用户更不易察觉）。``DetachInfo`` 缺失的成员（redo 前就不在文档里）退回
    ``doc.add``。
    """

    def __init__(self, page, items: list[Item], text: str = "删除"):
        super().__init__(page, text)
        self.items = list(items)
        self._infos: list[DetachInfo | None] = []

    def redo(self) -> None:
        self._run(self._do_redo)

    def _do_redo(self) -> None:
        self._infos = []
        for it in self.items:
            self.page._remove_item_obj(it, info=self._infos)
        self.page._after_change()

    def undo(self) -> None:
        self._run(self._do_undo)

    def _do_undo(self) -> None:
        for it, info in zip(self.items, self._infos):
            if info is not None:
                self.page.doc.attach(it, owner=info.owner, index=info.index)
            for leaf in iter_leaves([it]):
                if self.page._gi_for(leaf) is None:
                    make_gi(self.page, leaf)
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
        self._run(self._do_redo)

    def _do_redo(self) -> None:
        for it, _old, new in self.moves:
            it.pos = (float(new[0]), float(new[1]))
            self.page._sync_gi(it)
        self.page._after_change()

    def undo(self) -> None:
        self._run(self._do_undo)

    def _do_undo(self) -> None:
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
            # **几何字段必须显式重建画笔路径**（FR-08）：``_sync_gi`` →
            # ``apply_model_state`` 只应用 pos/scale/rotation，**不重建路径**。
            # 只 setattr 镜像标志而画布纹丝不动 ⇒ 用户点了「水平镜像」没反应。
            # 与 :class:`EditTextCommand` 同一类问题的同一修法。
            if set(d) & _GEOMETRY_FIELDS:
                gi = self.page._gi_for(it)
                if gi is not None:
                    gi.rebuild_path()  # 内含 prepareGeometryChange()
        self.page._after_change()

    def redo(self) -> None:
        self._run(self._do_redo)

    def _do_redo(self) -> None:
        self._apply(1)

    def undo(self) -> None:
        self._run(self._do_undo)

    def _do_undo(self) -> None:
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
        self._run(self._do_redo)

    def _do_redo(self) -> None:
        self._apply(self.new_paths, self.new_spec)

    def undo(self) -> None:
        self._run(self._do_undo)

    def _do_undo(self) -> None:
        self._apply(self.old_paths, self.old_spec)


class RetraceImageCommand(_PageCommand):
    """图片**就地重追**（FR-10/T10）：换新 paths + image_spec（FR-08 坑 2 同源）。

    结构照 :class:`EditTextCommand`（old/new paths + spec）——**必须**走这条
    显式 ``rebuild_path`` 通道：setattr 通道只应用 pos/scale/rotation，
    换 ``paths`` 不重画的话画布纹丝不动（重追对用户等于没发生）。

    **重追保持 pos / scale / angle_deg 不变**（FR-10 验收③）：只换几何与
    spec，位置/缩放/角度一个字段都不碰 ⇒ 新线条出现在原位置、原缩放、原角度。
    """

    def __init__(self, page, item: Item, new_paths, new_spec: dict | None,
                 text: str = "重追图片"):
        super().__init__(page, text)
        self.item = item
        self.new_paths = new_paths
        self.new_spec = new_spec
        self.old_paths = copy.deepcopy(item.paths)
        self.old_spec = copy.deepcopy(item.image_spec)

    def _apply(self, paths, spec) -> None:
        self.item.paths = copy.deepcopy(paths)
        self.item.image_spec = copy.deepcopy(spec)
        gi = self.page._gi_for(self.item)
        if gi is not None:
            gi.rebuild_path()  # 内含 prepareGeometryChange()
        self.page._after_change()

    def redo(self) -> None:
        self._run(self._do_redo)

    def _do_redo(self) -> None:
        self._apply(self.new_paths, self.new_spec)

    def undo(self) -> None:
        self._run(self._do_undo)

    def _do_undo(self) -> None:
        self._apply(self.old_paths, self.old_spec)


class ClearCommand(_PageCommand):
    """清空全部（可撤销）。"""

    def __init__(self, page, text: str = "清空"):
        super().__init__(page, text)
        self.saved: list[Item] = []
        self._infos: list[DetachInfo | None] = []

    def redo(self) -> None:
        self._run(self._do_redo)

    def _do_redo(self) -> None:
        self.saved = list(self.page.doc.items)
        self._infos = []
        for it in list(self.saved):
            self.page._remove_item_obj(it, info=self._infos)
        self.page._after_change()

    def undo(self) -> None:
        self._run(self._do_undo)

    def _do_undo(self) -> None:
        for it, info in zip(self.saved, self._infos):
            if info is not None:
                self.page.doc.attach(it, owner=info.owner, index=info.index)
            for leaf in iter_leaves([it]):
                if self.page._gi_for(leaf) is None:
                    make_gi(self.page, leaf)
        self.page._after_change()


class GroupCommand(_PageCommand):
    """编组（FR-03/T3 + FR-04/T4）：几何恒等重组，重建式。

    **场景项集合不变**（编组不增删几何）：容器不建 PathItem、成员 PathItem
    原样留存 ⇒ 只需重排 z 值 + 重建选中。undo 走 :meth:`Document.ungroup`
    精确恢复原顶层序。
    """

    def __init__(self, page, items: list[Item], *, name: str = "组",
                 text: str = "编组"):
        super().__init__(page, text)
        self.items = list(items)
        self.name = name
        self.container: Item | None = None

    def _after_group(self) -> None:
        # 场景项已在（编组不改几何集合）；只需让 z 与选中跟上模型
        for leaf in iter_leaves([self.container]) if self.container else ():
            self.page._sync_gi(leaf)

    def redo(self) -> None:
        self._run(self._do_redo)

    def _do_redo(self) -> None:
        self.container = self.page.doc.group_items(self.items, name=self.name)
        if self.container is None:
            return
        self._after_group()
        self.page._after_change()

    def undo(self) -> None:
        self._run(self._do_undo)

    def _do_undo(self) -> None:
        if self.container is None:
            return
        self.page.doc.ungroup(self.container)
        self._after_group()
        self.page._after_change()


class UngroupCommand(_PageCommand):
    """解组（FR-03/T3 + FR-04/T4）：容器摘除、子项原地提升，几何逐位不变。

    **undo 把组完整复原**（FR-03 的 undo 强契约）。:meth:`Document.ungroup` 是
    **破坏性**的：把 children 原地提升后 ``container.children = []``（"它不再是
    组"）⇒ 那个容器对象在 undo 之前是**空壳**。旧 ``_do_undo`` 开头一句
    ``if not self.container.children: return`` 于是让**每次撤销都在这里返回**
    —— 本 docstring 曾声称的"undo 据此原位挂回"从未实现。实测：真实窗口路径
    按一次 Ctrl+Z，index 3→2、undoText 已变成「编组」，但顶层仍是
    ``['a','b']``、组没回来（monkeypatch 数 ``Page.attach`` 调用次数 3→3
    **零增长** ⇒ 那行 attach 是死代码）；第二次撤销撤 :class:`GroupCommand`
    时操作的也是这个空壳容器，同样空转 —— 用户要按三次才见到第一次真实效果。

    **复原顺序不可颠倒：先摘孩子 → 挂回 children → 挂容器**。只把空容器
    ``attach`` 回去得到「空组 + 孩子全被提为顶层」—— 顶层凭空多一个无子项的
    幽灵容器（实测 ``items=['G','a','b']`` 而 ``G.children=[]``）；只补 children
    而不先摘除孩子，则它们**同时**留在顶层和容器里 ⇒ 同一几何被拍平两次 =
    切纸机切两遍。故 :meth:`_do_undo` 严格按上述三步。

    归属（owner 容器 + 下标）**每次 redo 都重新读**（:meth:`owner_of` +
    :meth:`_slot`）：undo 已把容器放回原位，重读得到的就是原位。旧实现用
    ``if self._owner is None and self._index is None`` 判「首次」，而顶层容器
    恰好 ``_owner is None``，第二次 redo 只因 ``_index`` 非 None 才偶然成立。
    """

    def __init__(self, page, container: Item, text: str = "解组"):
        super().__init__(page, text)
        self.container = container
        self._owner: Item | None = None
        self._index: int | None = None
        #: redo 从 :meth:`Document.ungroup` 收回来的孩子快照 —— undo 复原
        #: children 的**唯一**依据（容器自身已被清空，问它问不出来）。
        self._kids: list[Item] = []

    def _after_ungroup(self, kids: list[Item]) -> None:
        for leaf in iter_leaves(kids):
            self.page._sync_gi(leaf)

    def redo(self) -> None:
        self._run(self._do_redo)

    def _do_redo(self) -> None:
        # 归属每次重读：undo 已把容器放回原 owner/下标，重读即原位。
        self._owner = self.page.doc.owner_of(self.container)
        self._index = self._slot()
        kids = self.page.doc.ungroup(self.container)
        if kids:
            # 只在真解组成功时更新快照。ungroup 返回 []（容器已非容器 / 不在
            # 文档里）时保留上一次的记录，undo 仍能复原 —— 否则一次失败的
            # redo 会把 undo 的依据清空，又变成静默空操作。
            self._kids = list(kids)
        self._after_ungroup(kids)
        self.page._after_change()

    def _slot(self) -> int | None:
        """容器当前所在下标（顶层列表或 owner.children）。"""
        if self._owner is not None:
            for i, ch in enumerate(self._owner.children):
                if ch is self.container:
                    return i
            return None
        for i, it in enumerate(self.page.doc.items):
            if it is self.container:
                return i
        return None

    def undo(self) -> None:
        self._run(self._do_undo)

    def _do_undo(self) -> None:
        if not self._kids:
            # 从未 redo 成功过（无孩子可复原）—— 防御：不产生错误结构。
            return
        # ① 先把孩子从当前所在位置摘除（组感知、按身份页内定位）：不摘的话
        #    它们会同时留在顶层和容器里 → 同一几何被拍平两次。
        for ch in self._kids:
            self.page.doc.remove(ch)
        # ② 按解组前的顺序挂回 children（index=None ⇒ append，天然保序；
        #    Page.attach 自带身份幂等，重跑不会重复挂）。
        for ch in self._kids:
            self.page.doc.attach(ch, owner=self.container)
        # ③ 最后挂容器（owner + 原下标）：顺序反过来就成了「空组 + 孩子在顶层」。
        self.page.doc.attach(self.container, owner=self._owner, index=self._index)
        self._after_ungroup(list(self.container.children))
        self.page._after_change()
