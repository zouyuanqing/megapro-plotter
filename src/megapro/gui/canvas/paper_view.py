"""纸面视图（Qt）—— 缩放/平移/工具事件，坐标一律经 coords 浮点通道。

契约（§2.2 canvas/paper_view.py）：
- :meth:`PaperView.set_zoom` 显式锚点公式
  ``setTransform(view_transform(ppm_new, mm_from_view(anchor), anchor))``
  （红线：缩放走显式 set_zoom 公式；offscreen 无真光标，不用 AnchorUnderMouse
  —— 负行列式 + AnchorUnderMouse 有锚点漂移史，见蓝图坑清单）。
- 鼠标事件一律 ``mm_from_view(view, event.position())``（浮点；**禁止**
  mapToScene/mapFromScene 的 int 量化）。
- 滚轮缩放 factor 1.15，ppm 钳位 ``[fit_ppm, 100]``；中键平移。

【Qt 账目说明（本次实测 + qgraphicsview.cpp 源码核对）】``viewportTransform``
= ``transform`` 减去滚动条账目 (h, v)（``mapToScene(p) = matrix⁻¹(p+scroll)``）；
且 ``setTransform`` 内部会 ``recalculateContentSize`` + ``centerView(anchor)``
改写该账目。这里滚动条停用 + ``NoAnchor``，并在每次 setTransform 后把滚动
账目钉回 0（``setRange(0,0); setValue(0)``），使 ``viewportTransform ==
transform`` 浮点精确 —— set_zoom 锚点不变性实测 80/80 drift ≤ 1.1e-13。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from megapro.gui.canvas.coords import BED_H, BED_W
from megapro.gui.canvas.view_transform import mm_from_view, view_transform

__all__ = ["PaperView"]


class PaperView(QtWidgets.QGraphicsView):
    """纸面视图：set_zoom / fit / 滚轮缩放 / 中键平移 / 绘制工具路由。"""

    #: 任何视图变化（缩放/平移）后发一次，供标尺/手柄刷新
    viewChanged = QtCore.Signal()
    #: 缩放变化（px/mm），供旧消费者使用
    zoomChanged = QtCore.Signal(float)

    def __init__(self, scene, page=None, parent=None) -> None:
        super().__init__(scene, parent)
        self._page = page
        self._tool = "select"
        self._drawing = False
        self._panning = False
        self._last_vp = QtCore.QPointF()
        self._ppm = 2.0
        self._fit_ppm = 0.0
        self.setRenderHints(QtGui.QPainter.Antialiasing)
        # 显式锚点公式：不用 AnchorUnderMouse（负行列式下有漂移史）
        self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        # 无对齐 → 不触发「内容放得进视口」的对齐缩进账目（见模块 docstring）
        self.setAlignment(QtCore.Qt.Alignment(0))
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)

    # -- 工具模式 -----------------------------------------------------------

    def set_tool(self, tool: str) -> None:
        """select=框选/拖拽；绘制工具=十字光标+画图（回调 page.begin/…_draw）。"""
        self._tool = tool
        if tool == "select":
            self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
            self.setCursor(QtCore.Qt.ArrowCursor)
        else:
            self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
            self.setCursor(QtCore.Qt.CrossCursor)

    # -- 变换（唯一入口 _apply_transform） -----------------------------------

    def _apply_transform(self, desired: QtGui.QTransform) -> None:
        """令 ``viewportTransform() == desired``（浮点精确，见模块 docstring）。"""
        self.setTransform(desired)
        for bar in (self.horizontalScrollBar(), self.verticalScrollBar()):
            bar.blockSignals(True)
            bar.setRange(0, 0)
            bar.setValue(0)
            bar.blockSignals(False)
        self._ppm = abs(self.transform().m11())
        self.viewChanged.emit()
        self.zoomChanged.emit(self._ppm)

    def mm_of(self, vp_pt: QtCore.QPointF) -> QtCore.QPointF:
        """视口（浮点）→ 纸面 mm（浮点）。"""
        return mm_from_view(self, vp_pt)

    def set_zoom(self, ppm: float, anchor_view: QtCore.QPointF | None = None) -> None:
        """显式锚点缩放：anchor_view 下的 mm 点在缩放后保持不动。

        ``setTransform(view_transform(ppm_new, mm_from_view(anchor), anchor))``
        —— 蓝图 §2.1 的唯一允许负比例尺公式。
        """
        if anchor_view is None:
            anchor_view = QtCore.QPointF(self.viewport().rect().center())
        anchor_mm = mm_from_view(self, anchor_view)
        ppm = self._clamp_ppm(ppm)
        self._apply_transform(view_transform(ppm, anchor_mm, anchor_view))

    def _clamp_ppm(self, ppm: float) -> float:
        fit = self.fit_ppm()
        return max(fit, min(float(ppm), 100.0))

    def fit_ppm(self) -> float:
        """床 210×210 恰好放进视口的 px/mm（缩放下限）。"""
        if self._fit_ppm <= 0:
            vr = self.viewport().rect()
            if vr.width() > 0 and vr.height() > 0:
                self._fit_ppm = min(vr.width() / BED_W, vr.height() / BED_H)
            else:  # pragma: no cover - 无视口尺寸的防御
                self._fit_ppm = 0.05
        return self._fit_ppm

    def fit(self, rect: QtCore.QRectF | None = None) -> float:
        """把 rect（默认床框）适配并居中到视口；返回使用到的 ppm。"""
        rect = rect or QtCore.QRectF(0.0, 0.0, BED_W, BED_H)
        vr = self.viewport().rect()
        if vr.width() <= 0 or vr.height() <= 0 or rect.width() <= 0 \
                or rect.height() <= 0:  # pragma: no cover
            return self._ppm
        ppm = min(vr.width() / rect.width(), vr.height() / rect.height())
        self._fit_ppm = ppm
        center_view = QtCore.QPointF(vr.center())
        self._apply_transform(view_transform(ppm, rect.center(), center_view))
        return ppm

    def pan_by(self, dx_px: float, dy_px: float) -> None:
        """按视口像素平移（中键拖动）：保持 ppm，只改平移项。"""
        t = self.transform()
        self._apply_transform(QtGui.QTransform(
            t.m11(), t.m12(), t.m21(), t.m22(),
            t.dx() + dx_px, t.dy() + dy_px))

    def px_per_mm(self) -> float:
        return abs(self.transform().m11())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_ppm = 0.0  # 视口尺寸变化后重算缩放下限

    # -- 事件（坐标一律 mm_from_view 浮点） ----------------------------------

    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.set_zoom(self._ppm * factor, QtCore.QPointF(event.position()))
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.MiddleButton:
            self._panning = True
            self._last_vp = QtCore.QPointF(event.position())
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        mm = mm_from_view(self, QtCore.QPointF(event.position()))
        if (self._tool != "select" and event.button() == QtCore.Qt.LeftButton
                and self._page is not None):
            if self._tool in ("poly", "pencil") and self._drawing:
                self._page.add_poly_point(mm)
            else:
                self._drawing = True
                self._page.begin_draw(mm)
            event.accept()
            return
        if (self._tool != "select" and event.button() == QtCore.Qt.RightButton
                and self._page is not None):
            if self._drawing and self._tool in ("poly", "pencil"):
                self._drawing = False
                self._page.finish_draw(mm)
            else:
                self._drawing = False
                self._page.cancel_draw()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._panning:
            pos = QtCore.QPointF(event.position())
            self.pan_by(pos.x() - self._last_vp.x(), pos.y() - self._last_vp.y())
            self._last_vp = pos
            event.accept()
            return
        if self._drawing and self._tool != "select" and self._page is not None:
            mm = mm_from_view(self, QtCore.QPointF(event.position()))
            self._page.update_draw(mm)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._panning and event.button() == QtCore.Qt.MiddleButton:
            self._panning = False
            self.setCursor(QtCore.Qt.ArrowCursor
                           if self._tool == "select" else QtCore.Qt.CrossCursor)
            event.accept()
            return
        mm = mm_from_view(self, QtCore.QPointF(event.position()))
        if (self._drawing and event.button() == QtCore.Qt.LeftButton
                and self._page is not None):
            if self._tool in ("poly", "pencil"):
                self._page.update_draw(mm)
                event.accept()
                return
            self._drawing = False
            self._page.finish_draw(mm)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._drawing and self._tool in ("poly", "pencil") and self._page is not None:
            mm = mm_from_view(self, QtCore.QPointF(event.position()))
            self._drawing = False
            self._page.finish_draw(mm)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
