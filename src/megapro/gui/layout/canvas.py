"""CAD 式排版画布（Qt）。

坐标约定：
- **模型/Item 存 paper 坐标**：y-up，原点左下，床 0..210（与导出/打印一致）。
- **场景 = 显示空间**：QGraphicsView 场景 y 向下，(0,0) 在显示区左上。
  显示映射 disp_y = BED_H − paper_y（纸顶 大 paper_y → 小 disp_y = 屏幕上方）。
  所有绘制（网格/床框/原点十字）与图元在此映射下画。

网格随缩放用 1/2/5×10^k mm 阶梯（drawBackground 重写）。标尺/交互在 view。
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from .model import BED_H, BED_W

#: paper(y-up) → 显示(场景, y-down)：disp_y = BED_H − paper_y
def disp_y(py: float) -> float:
    return BED_H - py


def grid_pitch_mm(px_per_mm: float) -> float:
    if px_per_mm <= 0:
        return 1.0
    base = 1.0
    for _ in range(8):
        for m in (1.0, 2.0, 5.0):
            pitch = m * base
            if 24 <= pitch * px_per_mm <= 80:
                return pitch
        base *= 10.0
    return max(50.0 / px_per_mm, 0.01)


class GridScene(QtWidgets.QGraphicsScene):
    """场景(显示空间,y-down)。drawBackground 画网格 + 床框 + 原点十字。"""

    def drawBackground(self, painter: QtGui.QPainter, rect) -> None:  # noqa: N802
        super().drawBackground(painter, rect)
        ppm = abs(painter.transform().m11())
        pitch = grid_pitch_mm(ppm)
        # 可见显示范围
        dx0 = max(rect.left(), 0.0)
        dx1 = min(rect.right(), BED_W)
        dy0 = max(rect.top(), 0.0)
        dy1 = min(rect.bottom(), BED_H)
        # 竖网格（x 同 paper）
        pen = QtGui.QPen(QtGui.QColor("#d8dde2"))
        pen.setWidthF(0)
        painter.setPen(pen)
        gx = math.floor(dx0 / pitch) * pitch
        while gx <= dx1:
            painter.drawLine(QtCore.QPointF(gx, dy0), QtCore.QPointF(gx, dy1))
            gx += pitch
        # 横网格：paper y 在 [BED_H−dy1, BED_H−dy0]
        py_lo = BED_H - dy1
        py_hi = BED_H - dy0
        gy = math.floor(py_lo / pitch) * pitch
        while gy <= py_hi:
            sdy = disp_y(gy)
            painter.drawLine(QtCore.QPointF(dx0, sdy), QtCore.QPointF(dx1, sdy))
            gy += pitch
        # 床框（paper 0..210 → 显示）
        painter.setPen(QtGui.QPen(QtGui.QColor("#4a4a4a"), 0))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(QtCore.QRectF(0, disp_y(BED_H), BED_W, BED_H))
        # 原点十字（paper 0,0 → 显示 (0, BED_H) 左下）
        painter.setPen(QtGui.QPen(QtGui.QColor("#c00"), 0))
        ox, oy = 0.0, disp_y(0.0)
        cr = 8.0
        painter.drawLine(QtCore.QPointF(ox - cr, oy), QtCore.QPointF(ox + cr, oy))
        painter.drawLine(QtCore.QPointF(ox, oy - cr), QtCore.QPointF(ox, oy + cr))


class CanvasView(QtWidgets.QGraphicsView):
    zoomChanged = QtCore.Signal(float)

    def __init__(self, scene: GridScene, parent=None) -> None:
        super().__init__(scene, parent)
        self._panning = False
        self._zoom = 2.0
        self._tool = "select"
        self._drawing = False
        self._page = parent  # LayoutPage（提供 begin/update/finish_draw）
        self.setRenderHints(QtGui.QPainter.Antialiasing)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setSceneRect(-40, -40, BED_W + 80, BED_H + 80)
        self.reset_transform()

    def set_tool(self, tool: str) -> None:
        """切换工具：select=可框选/拖拽；绘制工具=十字光标+画图。"""
        self._tool = tool
        if tool == "select":
            self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
            self.setCursor(QtCore.Qt.ArrowCursor)
        else:
            self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
            self.setCursor(QtCore.Qt.CrossCursor)

    def reset_transform(self) -> None:
        t = QtGui.QTransform()
        t.scale(self._zoom, self._zoom)
        self.setTransform(t)
        self.zoomChanged.emit(self._zoom)

    def fit_bed(self) -> None:
        self.fitInView(QtCore.QRectF(0, 0, BED_W, BED_H),
                       QtCore.Qt.KeepAspectRatio)
        self._zoom = self.transform().m11()
        self.zoomChanged.emit(self._zoom)

    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self._zoom = max(0.05, min(self._zoom * factor, 200.0))
        self.scale(factor, factor)
        self.zoomChanged.emit(self._zoom)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.MiddleButton:
            self._panning = True
            self._last = event.position()
            self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        if (self._tool != "select" and event.button() == QtCore.Qt.LeftButton
                and self._page is not None):
            sp = self.mapToScene(event.position().toPoint())
            if self._tool in ("poly", "pencil") and self._drawing:
                self._page.add_poly_point(sp)
            else:
                self._drawing = True
                self._page.begin_draw(sp)
            event.accept()
            return
        # 右键：结束折线/自由笔（或取消其它绘制）
        if (self._tool != "select" and event.button() == QtCore.Qt.RightButton
                and self._page is not None):
            sp = self.mapToScene(event.position().toPoint())
            if self._drawing and self._tool in ("poly", "pencil"):
                self._drawing = False
                self._page.finish_draw(sp)
            else:
                self._drawing = False
                self._page.cancel_draw()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._panning:
            delta = event.position() - self._last
            self._last = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return
        if self._drawing and self._tool != "select" and self._page is not None:
            sp = self.mapToScene(event.position().toPoint())
            self._page.update_draw(sp)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._panning and event.button() == QtCore.Qt.MiddleButton:
            self._panning = False
            self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
            self.setCursor(QtCore.Qt.ArrowCursor)
            event.accept()
            return
        if (self._drawing and event.button() == QtCore.Qt.LeftButton
                and self._page is not None):
            # 折线/自由笔：单击续点，双击/右键结束
            if self._tool in ("poly", "pencil"):
                sp = self.mapToScene(event.position().toPoint())
                self._page.update_draw(sp)
                event.accept()
                return
            sp = self.mapToScene(event.position().toPoint())
            self._drawing = False
            self._page.finish_draw(sp)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._drawing and self._tool in ("poly", "pencil") and self._page is not None:
            sp = self.mapToScene(event.position().toPoint())
            self._drawing = False
            self._page.finish_draw(sp)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def px_per_mm(self) -> float:
        return abs(self.transform().m11())


class Ruler(QtWidgets.QWidget):
    """mm 标尺（水平或垂直），读 view transform 画刻度。"""

    def __init__(self, view: CanvasView, orientation="h", parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self.orientation = orientation
        self.setFixedHeight(20) if orientation == "h" else self.setFixedWidth(20)
        view.horizontalScrollBar().valueChanged.connect(self.update)
        view.verticalScrollBar().valueChanged.connect(self.update)
        view.zoomChanged.connect(lambda _z: self.update())

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor("#f4f6f8"))
        p.setPen(QtGui.QColor("#888"))
        ppm = self.view.px_per_mm()
        if ppm <= 0:
            return
        pitch = grid_pitch_mm(ppm)
        if self.orientation == "h":
            origin = self.view.mapFromScene(QtCore.QPointF(0, 0)).x()
            length = self.width()
            for mmv in range(0, int(BED_W) + 1, max(1, int(pitch))):
                x = origin + mmv * ppm
                if -20 <= x <= length + 20:
                    p.drawLine(int(x), 12, int(x), 20)
                    p.drawText(int(x) + 2, 11, str(mmv))
        else:
            origin = self.view.mapFromScene(QtCore.QPointF(0, 0)).y()
            length = self.height()
            for mmv in range(0, int(BED_H) + 1, max(1, int(pitch))):
                y = origin - mmv * ppm  # 显示 y-down，paper y-up
                if -20 <= y <= length + 20:
                    p.drawLine(12, int(y), 20, int(y))
                    p.drawText(1, int(y) - 2, str(mmv))
