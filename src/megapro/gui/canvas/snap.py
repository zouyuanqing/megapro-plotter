"""网格/对象吸附（纯函数，零 Qt）。

契约（§2.2 canvas/snap.py）：
- :class:`SnapEngine`：网格吸附（pitch 取 ``coords.grid_steps(ppm)`` 的 minor）
  与对象吸附（端点/中点/边，关键点缓存）；绘制工具同样接入
  （layout_page 的 ``_snap_mm``）。
- 另保留旧版网格定价 :func:`grid_pitch_mm`（24–80px 带宽阶梯）——
  ``layout/canvas.py`` 阶段 2 删除后迁于此；场景网格/标尺一律用
  ``coords.grid_steps``，本函数仅供旧调用方（tests/test_gui_layout.py 的
  定价阶梯用例）直接 import 取用（layout.model 的旧模块别名已删除，
  阶段 5 C2 核查无该块）。
"""

from __future__ import annotations

from megapro.gui.canvas.coords import snap

__all__ = [
    "SnapEngine",
    "snap_point",
    "key_points",
    "snap_to_objects",
    "grid_pitch_mm",
]

Point = tuple[float, float]
Path2D = list[list[Point]]


def grid_pitch_mm(px_per_mm: float) -> float:
    """旧版网格定价：屏幕格距落在 24–80px 的 1/2/5×10^k mm 阶梯。

    已被 :func:`megapro.gui.canvas.coords.grid_steps`（Heckbert 单一
    target_px）取代；保留仅为兼容旧 import（见模块 docstring）。
    """
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


def snap_point(pt: Point, pitch: float) -> Point:
    """网格吸附（最近格点；pitch 来自 grid_steps 的 minor）。"""
    return (snap(pt[0], pitch), snap(pt[1], pitch))


def key_points(paths: Path2D) -> list[Point]:
    """对象吸附关键点缓存：折线端点 + 相邻中点（零成本，paths 是折线）。"""
    out: list[Point] = []
    for p in paths:
        if not p:
            continue
        out.append((float(p[0][0]), float(p[0][1])))
        if len(p) >= 2:
            out.append((float(p[-1][0]), float(p[-1][1])))
        for (x0, y0), (x1, y1) in zip(p, p[1:]):
            out.append(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
    return out


def _project_on_segment(
    px: float, py: float, x0: float, y0: float, x1: float, y1: float,
) -> Point:
    """点到线段的最近点（边吸附用）。"""
    dx, dy = x1 - x0, y1 - y0
    seg_len2 = dx * dx + dy * dy
    if seg_len2 <= 1e-18:
        return (x0, y0)
    t = ((px - x0) * dx + (py - y0) * dy) / seg_len2
    t = max(0.0, min(1.0, t))
    return (x0 + t * dx, y0 + t * dy)


def snap_to_objects(
    pt: Point, paths: Path2D, tol: float = 2.0,
) -> Point | None:
    """对象吸附：端点/中点/边投影，命中 tol 内最近者；无命中返回 None。"""
    if tol <= 0:
        return None
    px, py = pt
    best: Point | None = None
    best_d2 = tol * tol

    def _offer(cand: Point) -> None:
        nonlocal best, best_d2
        d2 = (cand[0] - px) ** 2 + (cand[1] - py) ** 2
        if d2 <= best_d2:
            best, best_d2 = cand, d2

    for kx, ky in key_points(paths):
        _offer((kx, ky))
    for p in paths:
        for (x0, y0), (x1, y1) in zip(p, p[1:]):
            _offer(_project_on_segment(px, py, x0, y0, x1, y1))
    return best


class SnapEngine:
    """吸附引擎（纯函数集合 + 参数；无 Qt、无可变全局状态）。

    优先对象吸附（端点/中点/边），其次网格吸附。
    """

    def __init__(
        self, pitch: float = 1.0, *, enabled: bool = True, obj_tol_mm: float = 2.0,
    ) -> None:
        self.pitch = float(pitch)
        self.enabled = bool(enabled)
        self.obj_tol_mm = float(obj_tol_mm)

    @staticmethod
    def minor_pitch(ppm: float) -> float:
        """缩放对应的网格 minor 步距（``grid_steps`` 的 minor）。"""
        from megapro.gui.canvas.coords import grid_steps

        return grid_steps(ppm)[1]

    def snap(self, pt: Point, paths: Path2D | None = None) -> Point:
        """吸附一个点：对象优先、其次网格；禁用时原样返回。"""
        if not self.enabled:
            return (float(pt[0]), float(pt[1]))
        if paths:
            hit = snap_to_objects(pt, paths, self.obj_tol_mm)
            if hit is not None:
                return hit
        return snap_point(pt, self.pitch)
