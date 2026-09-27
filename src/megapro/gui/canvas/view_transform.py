"""视图（屏幕）↔ 纸面 mm 变换（Qt 值类型薄封装）。

契约（docs/preview-layout-blueprint.md §2.1/§2.2）：
- :func:`view_transform` 是**全仓库唯一允许负比例尺**的函数（唯一显示翻转
  边界，屏幕上方 = 纸面 +Y）：显式六元矩阵 ``QTransform(ppm, 0, 0, -ppm,
  dx, dy)``（规避链式 scale/rotate 的组合顺序歧义）。格式侧的翻转数学
  本体仍是 ``coords.flip_y_scalar`` 一处（SVG 互换层调用）。
- :func:`mm_from_view` / :func:`view_from_mm` 一律走 ``viewportTransform()``
  **浮点**通道；**禁止** ``mapToScene``/``mapFromScene``（int 重载，量化误差
  实测可达 (1.0, 1.333)mm，见蓝图坑清单）。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QTransform

__all__ = [
    "view_transform",
    "mm_from_view",
    "view_from_mm",
]


def view_transform(
    ppm: float,
    anchor_mm: QPointF,
    anchor_view: QPointF,
) -> QTransform:
    """mm → 视口的显式矩阵：``QTransform(ppm, 0, 0, -ppm, dx, dy)``。

    锚点不变：``anchor_mm`` 恰好映到 ``anchor_view``（供 set_zoom 显式锚点公式）。
    全仓库唯一负比例尺处（屏幕 y 向下、纸面 y 向上）。
    """
    dx = anchor_view.x() - ppm * anchor_mm.x()
    dy = anchor_view.y() + ppm * anchor_mm.y()
    return QTransform(ppm, 0, 0, -ppm, dx, dy)


def mm_from_view(view, vp_pt: QPointF) -> QPointF:
    """视口（浮点）→ 纸面 mm（浮点）。走 viewportTransform() 反矩阵。"""
    inv, ok = view.viewportTransform().inverted()
    if not ok:  # pragma: no cover - 奇异矩阵防御
        raise ValueError("view 变换不可逆")
    return inv.map(vp_pt)


def view_from_mm(view, mm: QPointF) -> QPointF:
    """纸面 mm → 视口（浮点）。标尺/手柄/测试取点一律走此通道。"""
    return view.viewportTransform().map(mm)
