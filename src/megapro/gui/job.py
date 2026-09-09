"""Job 编译器 —— 把 SVG/文件转成可直接发给机器的 G-code（纯逻辑，无 Qt）。

关键：**不用 toolchain 的 emit_gcode**（它产 Z0/Z1 会撞床）。这里只读复用
toolchain 的 parse_svg + nearest_neighbor_sort 取 polylines，然后用**机器
绝对 Z** 生成——空移/抬笔在 safe_z、落笔到 pen_down_z（写字）或 cut_down_z
（裁刀下压）。Z 是绝对机器坐标（safe_z 高 / 落刀低），杜绝 Z0 顶床。

本机 Z 语义（profile 标定）：绝对 Z 越大越高；safe_z≈30 抬笔安全高度，
pen_down_z≈17 笔尖触纸，cut_down_z（裁刀）按材料下压。所有坐标视作
工件坐标（若设了 G92 工件原点，则由调用方按偏移平移后再交给 check_bounds）。
"""

from __future__ import annotations

__all__ = [
    "polylines_for_svg",
    "check_bounds",
    "gcode_for_drawing",
    "gcode_for_cutting",
    "cut_z_for_depth",
    "flip_y",
]

_DEFAULT_XY_FEED = 1200.0
_DEFAULT_Z_FEED = 300.0


def polylines_for_svg(path) -> list[list[tuple[float, float]]]:
    """读 SVG → 多段线 + 最近邻排序（复用 toolchain，纯数据）。"""
    from megapro.toolchain.svg_to_gcode import nearest_neighbor_sort, parse_svg

    return nearest_neighbor_sort(parse_svg(path))


def check_bounds(
    paths: list[list[tuple[float, float]]],
    *,
    max_x: float = 210.0,
    max_y: float = 210.0,
    margin: float = 0.0,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    pen_radius: float = 0.0,
) -> list[str]:
    """越界预检：返回超出 [origin, origin+max-margin] 的路径描述（空=全过）。

    坐标按工件坐标；若设了工件原点偏移，调用方应把路径先平移（或传 origin）。
    margin 为安全边距；pen_radius 为笔尖半径 → 判定区再内缩（笔心距边界留半径，
    防笔尖出界/笔画压边）。
    """
    x0, y0 = origin_x, origin_y
    # 笔径只内缩**远端**上界（max 边）：原点 0 是物理归位端，笔心到 0 即可
    # （纸从 0 铺起，靠 0 边的半笔尖超出纸缘无妨）；远端超程才需留半径防出界。
    x1, y1 = origin_x + max_x - margin - pen_radius, origin_y + max_y - margin - pen_radius
    bad: list[str] = []
    for i, p in enumerate(paths):
        if not p:
            continue
        for x, y in p:
            if x < x0 or x > x1 or y < y0 or y > y1:
                bad.append(f"路径#{i} 越界: ({x:g},{y:g}) 超出 [{x0:g},{x1:g}]×[{y0:g},{y1:g}]")
                break
    return bad


def gcode_for_drawing(
    paths: list[list[tuple[float, float]]],
    *,
    pen_down_z: float,
    safe_z: float,
    feed_xy: float = _DEFAULT_XY_FEED,
    feed_z: float = _DEFAULT_Z_FEED,
    travel_lift_mm: float = 5.0,
) -> list[str]:
    """写字：每段 = 空移起点 → 落笔 pen_down_z → 沿路径画 → 抬离再跳段。

    抬笔策略：首段前抬到 safe_z（安全高度）一次；之后**段间只抬离纸面
    travel_lift_mm**（绝对 = pen_down_z + travel_lift_mm，封顶 safe_z）——
    默认 5mm 就够跳段，不必每段回 safe_z(30)，省时省磨损。
    Z 全为绝对机器坐标（safe_z > pen_down_z）。
    """
    if safe_z <= pen_down_z:
        raise ValueError(f"safe_z({safe_z}) 须 > pen_down_z({pen_down_z})")
    lift_z = min(safe_z, pen_down_z + travel_lift_mm)
    lines = ["G90", "G21"]
    lines.append(f"G0 Z{_fmt(safe_z)} F{_fmt(feed_z)}")  # 首段前抬到安全高度
    cur = (0.0, 0.0)
    for p in paths:
        if not p:
            continue
        if p[0] != cur:
            lines.append(f"G0 X{_fmt(p[0][0])} Y{_fmt(p[0][1])} F{_fmt(feed_xy)}")
        lines.append(f"G1 Z{_fmt(pen_down_z)} F{_fmt(feed_z)}")  # 落笔
        for x, y in p:
            lines.append(f"G1 X{_fmt(x)} Y{_fmt(y)} F{_fmt(feed_xy)}")
        lines.append(f"G0 Z{_fmt(lift_z)} F{_fmt(feed_z)}")  # 段间抬离（低抬）
        cur = p[-1]
    lines.append("M400")  # 排空：等所有移动完成
    return lines


def gcode_for_cutting(
    paths: list[list[tuple[float, float]]],
    *,
    cut_down_z: float,
    safe_z: float,
    feed_xy: float = _DEFAULT_XY_FEED,
    feed_z: float = _DEFAULT_Z_FEED,
    passes: int = 1,
    travel_lift_mm: float = 5.0,
) -> list[str]:
    """裁纸：闭合轮廓整圈下压切。

    每段：空移到起点（首段前抬 safe_z）→ 下压 cut_down_z → 沿整圈 G1 →
    抬离 travel_lift_mm（绝对 = cut_down_z + travel_lift_mm，封顶 safe_z）再跳段。
    默认单遍（passes=1）；多遍 = 每遍重新下压沿圈（依赖机器重复性，慎用）。
    """
    if safe_z <= cut_down_z:
        raise ValueError(f"safe_z({safe_z}) 须 > cut_down_z({cut_down_z})")
    lift_z = min(safe_z, cut_down_z + travel_lift_mm)
    lines = ["G90", "G21"]
    lines.append(f"G0 Z{_fmt(safe_z)} F{_fmt(feed_z)}")
    cur = (0.0, 0.0)
    for p in paths:
        if not p:
            continue
        if p[0] != cur:
            lines.append(f"G0 X{_fmt(p[0][0])} Y{_fmt(p[0][1])} F{_fmt(feed_xy)}")
        for _ in range(passes):
            lines.append(f"G1 Z{_fmt(cut_down_z)} F{_fmt(feed_z)}")  # 下压
            for x, y in p:
                lines.append(f"G1 X{_fmt(x)} Y{_fmt(y)} F{_fmt(feed_xy)}")
            lines.append(f"G0 Z{_fmt(lift_z)} F{_fmt(feed_z)}")  # 段间抬离（低抬）
        cur = p[-1]
    lines.append("M400")
    return lines


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def cut_z_for_depth(touch_z: float, depth: float) -> float:
    """裁刀下压的绝对 Z = 触纸 Z − 下压深度。

    P1b 语义（用户决策）：刀尖触纸的绝对 Z（cut_touch_z，由标定向导测得）
    减去相对纸面下压深度 depth → 得到下压到的绝对机器 Z。
    """
    if depth < 0:
        raise ValueError(f"下压深度不能为负: {depth}")
    return touch_z - depth


def flip_y(paths, bed_h: float = 210.0):
    """Y 翻转：y' = bed_h − y。把 SVG y-down 内容翻成「左下原点、+Y 向纸深」的
    机器纸面约定（CAD 式：排版所见=纸面所见）。

    用于作业载入：SVG 习惯 y-down（顶=小 y），而机器若原点在纸左下且 +Y 远离
    操作者，则 y-down 内容画出来上下颠倒。翻转后文字正读。返回新列表。
    """
    return [[(x, bed_h - y) for x, y in p] for p in paths]
