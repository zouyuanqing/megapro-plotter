"""纸面场景（Qt）—— 场景坐标 ≡ 纸面 mm y-up（§2.1）。

drawBackground 在**场景 mm**坐标直接画（rect 已是场景 mm，无任何 y 换算）：
网格（minor 浅 / major 深两级，``major*ppm < 6px`` 只画 major 防糊）、
床框 ``(0,0,210,210)``、原点十字，可选材料/可用区/安全边距三框。

sceneRect 取床外大余量：实测（PySide6 6.11.2 offscreen）当
``viewTransform.mapRect(sceneRect)`` 放得进视口时，QGraphicsView 会走
「对齐缩进」账目（recalculateContentSize 的 indent 分支），使
``viewportTransform`` 与 ``setTransform`` 之间出现整数像素偏移 —— 显式
set_zoom 锚点公式在 ppm≈0.4 实测漂移 165mm。取 3210mm 见方的 sceneRect
使支持的缩放区间（[fit_ppm, 100]）内永不「放得进视口」，锚点公式恢复
浮点精确（80/80 探针 drift ≤ 1.1e-13）。副作用：网格可在床外延续绘制
（§8.3 要求），绘制背景覆盖任意常规视图。
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from megapro.gui.canvas.coords import BED_H, BED_W, grid_steps

__all__ = ["PaperScene"]

#: 纸面外围余量（mm）：见模块 docstring 的实测依据，保证缩放区间内不触发
#: QGraphicsView 的「内容放得进视口 → 对齐缩进」账目。
_SCENE_MARGIN = 1500.0
_SCENE_SPAN = BED_W + 2 * _SCENE_MARGIN + 400.0  # 3210mm（含床 + 余量）


class PaperScene(QtWidgets.QGraphicsScene):
    """纸面场景：drawBackground 网格 + 床框 + 原点十字（+ 可选材料三框）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(
            QtCore.QRectF(-_SCENE_MARGIN, -_SCENE_MARGIN,
                          _SCENE_SPAN, _SCENE_SPAN),
            parent,
        )
        self._material: tuple[float, float] | None = None
        self._margin_mm = 0.0

    def set_material(
        self, w: float | None, h: float | None = None, margin_mm: float = 0.0,
    ) -> None:
        """设置材料尺寸（锚在纸面 (0,0)）；None 清除三框。"""
        if w is None or h is None:
            self._material = None
            self._margin_mm = 0.0
        else:
            self._material = (float(w), float(h))
            self._margin_mm = float(margin_mm)
        self.update()

    def drawBackground(  # noqa: N802
        self, painter: QtGui.QPainter, rect: QtCore.QRectF,
    ) -> None:
        super().drawBackground(painter, rect)
        ppm = abs(painter.transform().m11())
        if ppm <= 1e-12:
            return
        major, minor = grid_steps(ppm)
        show_minor = major * ppm >= 6.0

        pen_minor = QtGui.QPen(QtGui.QColor("#d8dde2"))
        pen_minor.setWidthF(0)  # cosmetic
        pen_major = QtGui.QPen(QtGui.QColor("#b8c0c8"))
        pen_major.setWidthF(0)

        def _vgrid(pitch: float) -> None:
            gx = math.floor(rect.left() / pitch) * pitch
            while gx <= rect.right():
                painter.drawLine(QtCore.QPointF(gx, rect.top()),
                                 QtCore.QPointF(gx, rect.bottom()))
                gx += pitch

        def _hgrid(pitch: float) -> None:
            # QRectF 数值端：top()=y 小端、bottom()=大端（y-up 场景只影响显示，
            # 不影响 QRectF 的数值序）——从 top() 起步扫到 bottom()。
            gy = math.floor(rect.top() / pitch) * pitch
            while gy <= rect.bottom():
                painter.drawLine(QtCore.QPointF(rect.left(), gy),
                                 QtCore.QPointF(rect.right(), gy))
                gy += pitch

        if show_minor:
            painter.setPen(pen_minor)
            _vgrid(minor)
            _hgrid(minor)
        painter.setPen(pen_major)
        _vgrid(major)
        _hgrid(major)

        # 床框（纸面 0..210）
        painter.setPen(QtGui.QPen(QtGui.QColor("#4a4a4a"), 0))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(QtCore.QRectF(0.0, 0.0, BED_W, BED_H))

        # 原点十字（纸面 (0,0) = 床左下）
        painter.setPen(QtGui.QPen(QtGui.QColor("#c00"), 0))
        cr = 8.0
        painter.drawLine(QtCore.QPointF(-cr, 0.0), QtCore.QPointF(cr, 0.0))
        painter.drawLine(QtCore.QPointF(0.0, -cr), QtCore.QPointF(0.0, cr))

        if self._material is not None:
            w, h = self._material
            m = self._margin_mm
            painter.setPen(QtGui.QPen(QtGui.QColor("#2a7"), 0))
            painter.drawRect(QtCore.QRectF(0.0, 0.0, w, h))  # 材料框
            pen_d = QtGui.QPen(QtGui.QColor("#2a7"), 0)
            pen_d.setStyle(QtCore.Qt.DashLine)
            painter.setPen(pen_d)
            painter.drawRect(  # 可用区（材料内缩边距）
                QtCore.QRectF(m, m, max(w - 2 * m, 0.0), max(h - 2 * m, 0.0)))
            pen_s = QtGui.QPen(QtGui.QColor("#a2a"), 0)
            pen_s.setStyle(QtCore.Qt.DotLine)
            painter.setPen(pen_s)
            painter.drawRect(  # 安全边距框（床内缩边距）
                QtCore.QRectF(m, m,
                              max(BED_W - 2 * m, 0.0),
                              max(BED_H - 2 * m, 0.0)))
