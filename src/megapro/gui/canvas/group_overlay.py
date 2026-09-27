"""组框 overlay（Qt）—— 扁平场景下的组可视/命中（FR-05 / T5）。

契约（D1 + 蓝图 :149）：
- **不引入 QGraphicsItem 父子成组**：``pos()`` 是唯一返回父坐标系的接口，
  一旦成组即错位（``items.py:129-134`` 的 ``commit_move`` 与
  ``handles.py:122-127`` 的 ``pivot`` 都按 ``gi.pos()`` 读父坐标语义）。
  故模型树 + **扁平场景** + 组框 overlay（**自管** gi，仿
  :class:`~megapro.gui.canvas.handles.SelectionHandles` 的 ``_HandleItem``）。
- overlay **只管显示与命中**，**不新增容器变换渲染通路**：组级移动/缩放/旋转
  走既有多选机制（选中 = 叶子集，overlay 不进 ``_selected()`` 的
  ``isinstance(gi, PathItem)`` 过滤）。组变换 = 叶子变换集合（FR-04/FR-05）。
- 组框画的是**容器 page_bbox 的页面系矩形**（:meth:`Item.unit_page_bbox` 带祖先
  链 —— M1 缺口 #1：裸 ``page_bbox()`` 不走祖先，编组后会漏报/错位）。
- 命中 = 整组叶子被点中 → 选中整组（组选中 = 叶子集）。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from megapro.gui.layout.model import Item, iter_units

__all__ = ["GroupOverlay", "GroupFrameItem"]

#: 组框线：虚线、蓝色 1px（与手柄同色系；容器不建 PathItem，故此框即组的唯一可视提示）
_FRAME_COLOR = "#0a5cff"
_FRAME_DASH = (4.0, 3.0)


class GroupFrameItem(QtWidgets.QGraphicsItem):
    """单个组框（自管 gi，不进选择集、不建 PathItem）。"""

    def __init__(self, rect: QtCore.QRectF, owner) -> None:
        super().__init__(None)  # 显式无 parent（D1：禁止 Qt 父子）
        self._rect = rect
        self.owner = owner
        self.setZValue(1e5)  # 在 PathItem 之上、SelectionHandles 之下
        self.setAcceptedMouseButtons(QtCore.Qt.NoButton)  # 纯显示

    def boundingRect(self) -> QtCore.QRectF:  # noqa: N802
        r = self._rect
        return QtCore.QRectF(r.x() - 2.0, r.y() - 2.0,
                             r.width() + 4.0, r.height() + 4.0)

    def set_frame(self, rect: QtCore.QRectF) -> None:
        self.prepareGeometryChange()
        self._rect = rect

    def paint(self, painter, option, widget=None) -> None:  # noqa: N802
        pen = QtGui.QPen(QtGui.QColor(_FRAME_COLOR))
        pen.setWidthF(0)  # cosmetic 发丝线：随缩放恒 1px
        pen.setStyle(QtCore.Qt.DashLine)
        pen.setDashPattern(_FRAME_DASH)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(self._rect)


class GroupOverlay:
    """管理「当前选择里包含整组」的组框（自管 QGraphicsItem）。

    只在**组选中**（该组的**全部叶子**都在选择集里）时画框；组编辑态（FR-05
    选择态二分）下即使选中了整组叶子也不画 —— 那时用户操作单元就是叶子，
    组框会误导为「整组将被操作」。
    """

    def __init__(self, scene, view, page=None) -> None:
        self._scene = scene
        self._view = view
        self._page = page
        self._frames: list[GroupFrameItem] = []
        #: 组编辑态：当前进组的容器（其子项按叶子语义操作）
        self.editing: Item | None = None

    # -- 摆位 ---------------------------------------------------------------

    def sync(self, selected_items, *, page_box) -> None:
        """按当前选择刷新组框。

        ``selected_items`` = 选中的 model.Item 列表；``page_box`` = 把一个
        model.Item 的**页面系** bbox 解析成 ``(x0,y0,x1,y1)`` 的可调用对象
        （走 :func:`iter_units` 的祖先链，M1 缺口 #1 的正解）。
        """
        self.clear()
        if self.editing is not None:
            return  # 组编辑态：操作单元 = 叶子，不画组框
        doc = getattr(self._page, "doc", None)
        if doc is None or not selected_items:
            return
        sel = {id(it) for it in selected_items}
        for cont, leaves in self._complete_groups(doc, sel):
            rect = self._rect_of(cont, page_box)
            if rect is None:
                continue
            f = GroupFrameItem(rect, self)
            self._frames.append(f)
            self._scene.addItem(f)

    def _complete_groups(self, doc, sel: set[int]) -> list[tuple[Item, list[Item]]]:
        """找出「成员全被选中」的容器（直接子容器即可，嵌套时内层先画）。

        判据 = 该容器的**直接子项**全部在选择集里。组选中 = 叶子集（FR-05），
        故普通态下用户点一个成员即选中整组（见 :meth:`LayoutPage._expand_group_selection`），
        到这里必然是「全选」。
        """
        out: list[tuple[Item, list[Item]]] = []
        for cont in doc.items:
            if not cont.is_container():
                continue
            kids = list(cont.children)
            if kids and all(id(k) in sel for k in kids):
                out.append((cont, kids))
        return out

    def _rect_of(self, cont: Item, page_box) -> QtCore.QRectF | None:
        bb = page_box(cont)
        x0, y0, x1, y1 = bb
        if x1 <= x0 and y1 <= y0:
            return None
        return QtCore.QRectF(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1))

    def clear(self) -> None:
        for f in self._frames:
            self._scene.removeItem(f)
        self._frames.clear()

    def update_sizes(self) -> None:
        """组框恒定屏幕线宽：整体 1/ppm（不改变几何，只让线宽看着一致）。"""
        ppm = abs(self._view.transform().m11()) or 1.0
        for f in self._frames:
            f.setScale(1.0 / ppm)

    # -- 编辑态 -------------------------------------------------------------

    def set_editing(self, container: Item | None) -> None:
        """进入/退出组编辑态（FR-05：双击进组选子项）。"""
        self.editing = container
        self.clear()

    def refresh(self, page_box) -> None:
        """编辑后按当前选择重画（几何可能已变）。"""
        page = self._page
        if page is None:
            return
        self.sync([gi.model_item for gi in page._selected()], page_box=page_box)
        self.update_sizes()
