"""可交互排版图元（Qt）—— 一个 QGraphicsItem 对应 model.Item。

模型 Item 存 paper 坐标（y-up, 原点左下 0..210）。显示空间 y-down：
- item 的**场景位置** = (pos.x, BED_H − pos.y)（paper→显示）。
- paint 画本地路径时 y 翻：disp_y = BED_H − paper_y（纸顶在上屏）。
- itemChange 吸附按 paper 网格；sync_to_model 把显示 pos 转回 paper。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .model import BED_H, Item


class PolylineItem(QtWidgets.QGraphicsItem):
    """绘制模型 Item 的折线；可选中、可移动（网格吸附，paper 坐标）。"""

    def __init__(self, model_item: Item, grid_pitch: float = 1.0,
                 color: str = "#0a5cff", parent=None) -> None:
        super().__init__(parent)
        self.model_item = model_item
        self.grid_pitch = grid_pitch
        self.color = color
        self.setFlags(QtWidgets.QGraphicsItem.ItemIsSelectable
                      | QtWidgets.QGraphicsItem.ItemIsMovable
                      | QtWidgets.QGraphicsItem.ItemSendsGeometryChanges)
        self._syncing = False
        px, py = model_item.pos
        self.setPos(px, BED_H - py)  # paper → 显示
        self.setScale(model_item.scale)
        self.setRotation(model_item.angle_deg)

    def boundingRect(self) -> QtCore.QRectF:  # noqa: N802
        x0, y0, x1, y1 = self.model_item.bbox()  # 本地 paper(y-up)
        w = max(x1 - x0, 1e-6)
        h = max(abs(y1 - y0), 1e-6)
        # 本地显示坐标 = (x, −y)：占据 y ∈ [−max_y, −min_y]
        return QtCore.QRectF(x0 - w * 0.05, -max(y1, y0) - h * 0.05,
                             w * 1.1, h * 1.1)

    def paint(self, painter, option, widget=None) -> None:  # noqa: N802
        pen = QtGui.QPen(QtGui.QColor(self.color))
        pen.setWidthF(0)
        if self.isSelected():
            pen.setColor(QtGui.QColor("#e66"))
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        for p in self.model_item.paths:
            if len(p) < 2:
                continue
            # 本地内容 = paper(y-up, 顶=大y) 相对 item 原点。
            # item 场景位置已把 paper 原点映到显示 (px, BED_H−py)；
            # 本地点画成 (x, −y)：paper 顶(大y) → 显示上方。合成后
            # scene = (px+x, BED_H−py−y) = paper 点的显示位置 ✓
            pts = [QtCore.QPointF(x, -y) for x, y in p]
            painter.drawPolyline(QtGui.QPolygonF(pts))

    def itemChange(self, change, value):  # noqa: N802
        if change == QtWidgets.QGraphicsItem.ItemPositionChange and not self._syncing:
            pitch = self.grid_pitch
            if pitch and pitch > 0:
                v = value
                px = v.x()
                py = BED_H - v.y()  # → paper
                nx = round(px / pitch) * pitch
                ny = round(py / pitch) * pitch
                value = QtCore.QPointF(nx, BED_H - ny)
        return super().itemChange(change, value)

    def sync_to_model(self) -> None:
        """把 QGraphicsItem 的 pos/scale/rotation 写回 model.Item（paper pos）。"""
        self._syncing = True
        try:
            self.model_item.pos = (self.pos().x(), BED_H - self.pos().y())
            self.model_item.scale = self.scale()
            self.model_item.angle_deg = self.rotation()
        finally:
            self._syncing = False

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """双击文字项 → 通知排版页重编（通过 page 回调）。"""
        page = getattr(self, "_page", None)
        if page is not None and self.model_item.text_spec:
            page.edit_text_item(self.model_item)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
