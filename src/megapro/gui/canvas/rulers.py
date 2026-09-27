"""mm 标尺（Qt）—— tick 定位走 view_from_mm 浮点，0 刻度对齐纸面原点。

契约（§2.2 canvas/rulers.py，含 §10-10 修 bug 定性）：
- 纸面 mm 刻度位置 = ``view_from_mm(view, QPointF(0, mmv)).y()``（竖）/
  ``.x()``（横）—— **浮点**，不 int 截断、不用 ``mapFromScene``（两者都会在
  小数缩放下抖动）。
- 0 刻度对齐 paper y=0（旧实现 ``canvas.py:236-242`` 取
  ``mapFromScene(0,0)`` 再 ``origin - mmv*ppm``：label L 实落 paper y=210+L
  的恒偏，整段脱床）。回归测试只断言修复后正确位置。
- 刻度范围 = 视口四角 mm 区间（床外延续）；major 带数字、minor 只画刻度线；
  删 ``max(1, int(pitch))``（pitch<1mm 时退化 1mm 刷屏）与 ``int()`` 截断。
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from megapro.gui.canvas.coords import grid_steps
from megapro.gui.canvas.view_transform import mm_from_view, view_from_mm

__all__ = ["RulerWidget"]


class RulerWidget(QtWidgets.QWidget):
    """mm 标尺（orientation='h'|'v'）。假设本控件原点与视口原点对齐（同旧约定）。"""

    def __init__(self, view, orientation: str = "h", parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self.orientation = orientation
        if orientation == "h":
            self.setFixedHeight(20)
        else:
            self.setFixedWidth(20)
        view.viewChanged.connect(self.update)

    # -- 契约接口（测试直断言） ---------------------------------------------

    def tick_pos(self, mm: float) -> float:
        """纸面 mm 刻度在本控件里的**浮点**位置。

        竖标尺 = paper y=mm 的视口 y（``view_from_mm(view, (0, mm)).y()``）；
        0 刻度因此恒对齐 paper y=0。横标尺同理（x）。
        """
        if self.orientation == "h":
            return view_from_mm(self.view, QtCore.QPointF(float(mm), 0.0)).x()
        return view_from_mm(self.view, QtCore.QPointF(0.0, float(mm))).y()

    def visible_mm(self) -> tuple[float, float]:
        """本轴可见 mm 区间（视口四角映射，床外延续）。"""
        vr = self.view.viewport().rect()
        corners = [
            mm_from_view(self.view, QtCore.QPointF(float(x), float(y)))
            for x in (vr.left(), vr.right())
            for y in (vr.top(), vr.bottom())
        ]
        vals = [p.x() if self.orientation == "h" else p.y() for p in corners]
        return (min(vals), max(vals))

    # -- 绘制 ---------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#f4f6f8"))
        painter.setPen(QtGui.QColor("#888"))
        ppm = abs(self.view.transform().m11())
        if ppm <= 1e-12:
            return
        major, minor = grid_steps(ppm)
        lo, hi = self.visible_mm()
        if self.orientation == "h":
            self._paint_ticks(painter, lo, hi, major, minor, horizontal=True)
        else:
            self._paint_ticks(painter, lo, hi, major, minor, horizontal=False)

    def _paint_ticks(
        self, painter: QtGui.QPainter, lo: float, hi: float,
        major: float, minor: float, *, horizontal: bool,
    ) -> None:
        length = self.width() if horizontal else self.height()
        k0 = math.floor(lo / minor)
        k1 = math.ceil(hi / minor)
        for k in range(k0, k1 + 1):
            mmv = k * minor
            pos = self.tick_pos(mmv)
            if pos < -20 or pos > length + 20:
                continue
            is_major = abs(mmv / major - round(mmv / major)) < 1e-6
            tick_len = 8.0 if is_major else 4.0
            if horizontal:
                painter.drawLine(QtCore.QPointF(pos, 20.0 - tick_len),
                                 QtCore.QPointF(pos, 20.0))
                if is_major:
                    painter.drawText(int(pos) + 2, 11, self._label(mmv))
            else:
                painter.drawLine(QtCore.QPointF(20.0 - tick_len, pos),
                                 QtCore.QPointF(20.0, pos))
                if is_major:
                    painter.drawText(1, int(pos) - 2, self._label(mmv))

    @staticmethod
    def _label(mmv: float) -> str:
        if abs(mmv - round(mmv)) < 1e-6:
            return str(int(round(mmv)))
        return f"{mmv:g}"
