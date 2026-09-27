"""排版图元（Qt）—— PathItem 直画 model.Item（场景 ≡ 纸面 mm y-up）。

契约（§2.2 canvas/items.py）：
- 一图元一条 QPainterPath（moveTo/lineTo 串联，pen 宽 0）；paths 以本地纸面
  y-up **直画**（旧 ``layout/items.py:56`` 的 ``(x, -y)`` 本地翻画已删——
  那是与 model.transformed_paths 的 CCW 数学互为镜像的根因）。
- ``boundingRect`` 直接用 ``model.Item.bbox()``；改前 ``prepareGeometryChange()``。
- ``transformOriginPoint=(0,0)``（= pos，与 model.transformed_paths 旋转
  支点严格一致：mapToScene ≡ transformed_paths，+θ = 纸面逆时针）。
- ``itemChange(ItemPositionChange)`` 只 return 吸附值（Qt 文档禁止在此
  回调内 setPos）。
- 拖动结束（mouseRelease）一次性回写 model 并进撤销栈：构造
  :class:`~megapro.gui.canvas.undo_cmds.MoveItemsCommand`（手势 token，
  mergeWith 先比 token）——命令写 model.Item，场景同步唯一入口 page._sync_gi。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from megapro.gui.canvas.snap import SnapEngine, snap_candidate_paths
from megapro.gui.layout.model import Item

__all__ = ["PathItem"]


class PathItem(QtWidgets.QGraphicsPathItem):
    """模型 Item 的折线绘制 + 选择/拖动/网格吸附（场景坐标 = 纸面 mm y-up）。"""

    def __init__(
        self, model_item: Item, *, color: str = "#0a5cff", parent=None,
    ) -> None:
        super().__init__(parent)
        self.model_item = model_item
        self.color = color
        self._syncing = False
        self.setFlags(QtWidgets.QGraphicsItem.ItemIsSelectable
                      | QtWidgets.QGraphicsItem.ItemIsMovable
                      | QtWidgets.QGraphicsItem.ItemSendsGeometryChanges)
        # 旋转/缩放支点 = 本地 (0,0)（= pos），与 model.transformed_paths 一致
        self.setTransformOriginPoint(0.0, 0.0)
        pen = QtGui.QPen(QtGui.QColor(self.color))
        pen.setWidthF(0)  # cosmetic 发丝线：随缩放恒 1px
        self.setPen(pen)
        self.setBrush(QtCore.Qt.NoBrush)
        self.rebuild_path()
        self.apply_model_state()

    # -- 几何 ---------------------------------------------------------------

    def _local_rect(self) -> QtCore.QRectF:
        """本地 bbox（= model.Item.bbox()；退化尺寸保护）。"""
        x0, y0, x1, y1 = self.model_item.bbox()
        return QtCore.QRectF(
            QtCore.QPointF(x0, y0),
            QtCore.QPointF(max(x1, x0 + 1e-6), max(y1, y0 + 1e-6)),
        )

    def boundingRect(self) -> QtCore.QRectF:  # noqa: N802
        return self._local_rect()

    def shape(self) -> QtGui.QPainterPath:  # noqa: N802
        """可点/可选区域 = 本地 bbox（与旧 PolylineItem 的可点语义一致）。

        不用 QGraphicsPathItem 的描边 shape（宽 0 描边几乎点不中）。
        """
        p = QtGui.QPainterPath()
        p.addRect(self._local_rect())
        return p

    def rebuild_path(self) -> None:
        """model.paths → 单条 QPainterPath（直画 y-up 本地坐标）。"""
        self.prepareGeometryChange()
        path = QtGui.QPainterPath()
        for poly in self.model_item.paths:
            if not poly:
                continue
            path.moveTo(QtCore.QPointF(float(poly[0][0]), float(poly[0][1])))
            for x, y in poly[1:]:
                path.lineTo(QtCore.QPointF(float(x), float(y)))
        self.setPath(path)
        self.update()

    def apply_model_state(self) -> None:
        """model → 场景（无翻转；场景 ≡ 纸面）。程序化 setPos 不触发吸附。"""
        self._syncing = True
        try:
            self.setPos(float(self.model_item.pos[0]), float(self.model_item.pos[1]))
            self.setScale(self.model_item.scale)
            self.setRotation(self.model_item.angle_deg)
        finally:
            self._syncing = False

    # -- 拖动吸附 + 回写 ----------------------------------------------------

    def _snap_value(self, v: QtCore.QPointF) -> QtCore.QPointF:
        """拖动位置吸附（Q4）：对象吸附（端点/中点/边）优先，其次网格。

        对象候选 = 其余图元的**页面系**关键点（FR-06 前置修复：候选源切
        :func:`snap_candidate_paths` 的页面域 —— ``transformed_paths`` 即时
        计算）。旧实现遍历 ``doc.items`` 拿 ``it.paths``（**局部**坐标）而喂入
        的是页面坐标 ``v``，域不一致：``pos=(50,60)`` 的图元页面点 (51,61)
        对局部候选零命中、对页面域命中 (51.0,60.0)；且 ``doc.items`` 漏掉
        **组内叶子**（不在顶层列表里）并把**隐藏图元**也算进候选。

        pitch = page.snap_pitch（随缩放 = grid_steps 的 minor，见
        layout_page._sync_snap_pitch）。
        """
        page = getattr(self, "_page", None)
        if page is None or not getattr(page, "snap_enabled", False):
            return v
        pitch = float(getattr(page, "snap_pitch", 0.0) or 0.0)
        engine = SnapEngine(pitch, enabled=True)
        x, y = engine.snap((float(v.x()), float(v.y())),
                           snap_candidate_paths(getattr(page, "doc", None),
                                                exclude=self.model_item))
        return QtCore.QPointF(x, y)

    def itemChange(self, change, value):  # noqa: N802
        if (change == QtWidgets.QGraphicsItem.ItemPositionChange
                and not self._syncing):
            value = self._snap_value(value)
        return super().itemChange(change, value)

    def commit_move(self) -> None:
        """手势结束（mouseRelease）：位移一次性回写 model 并进撤销栈。

        读取源永远是 model；gi→model 的回写只发生在构建本命令时（§2.2）。
        多选拖动时 Qt 同步移动所有选中项 —— 按「gi 与 model 已漂移」收集整组。
        """
        page = getattr(self, "_page", None)
        if page is None:
            return
        moves = []
        for gi in list(getattr(page, "_scene_items", ())):
            it = gi.model_item
            cur = (float(gi.pos().x()), float(gi.pos().y()))
            if (abs(cur[0] - it.pos[0]) > 1e-9 or abs(cur[1] - it.pos[1]) > 1e-9):
                moves.append((it, it.pos, cur))
        if moves:
            from megapro.gui.canvas.undo_cmds import MoveItemsCommand

            page._undo.push(MoveItemsCommand(page, moves, "移动"))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self.commit_move()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """双击：先问排版页（组编辑态进入），否则重编文字（page.edit_text_item 回调）。"""
        page = getattr(self, "_page", None)
        if page is not None and getattr(page, "on_item_double_clicked", None):
            if page.on_item_double_clicked(self.model_item):
                event.accept()
                return
        if page is not None and self.model_item.text_spec:
            page.edit_text_item(self.model_item)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
