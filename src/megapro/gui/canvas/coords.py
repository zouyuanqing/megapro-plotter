"""坐标权威 —— 全仓库唯一换算/翻转数学（纯函数，零 Qt，可单测）。

约定（docs/preview-layout-blueprint.md §2.1）：
- 机器坐标系唯一权威；纸面坐标 ≡ 机器 XY（M114 logical 恒等）；
- 工件原点 = 编译期纯平移（translate_paths，不发 G92）；
- y 翻转数学只在 :func:`flip_y_scalar` 一处实现，仅在 SVG 互换层
  （y-down 格式编解码）被调用；视图层翻转在 ``canvas/view_transform.py``
  （阶段 2，Qt 薄封装）。除这两个边界外任何文件出现翻转写法按 bug 处理。

``BED_W``/``BED_H`` 的数值字面全仓库只在本文件出现一处，其余一律引用常量。
"""

from __future__ import annotations

import math

__all__ = [
    "BED_W",
    "BED_H",
    "flip_y_scalar",
    "mirror_scalar",
    "paper_to_svg_ydown",
    "paper_from_svg_ydown",
    "machine_from_paper",
    "paper_from_machine",
    "translate_paths",
    "bbox_of",
    "anchor_point",
    "place_at_anchor",
    "grid_steps",
    "snap",
]

#: 床/纸行程 210×210 mm（全仓库唯一数值定义处）。
BED_W = BED_H = 210.0

Point = tuple[float, float]
Path2D = list[list[Point]]
BBox = tuple[float, float, float, float]

#: 九宫格锚点：首字母行（b=下 / m=中 / t=上），次字母列（l=左 / c=中 / r=右）。
_ANCHOR_ROWS = {"b": "min", "m": "mid", "t": "max"}
_ANCHOR_COLS = {"l": "min", "c": "mid", "r": "max"}
ANCHORS = ("bl", "bc", "br", "ml", "mc", "mr", "tl", "tc", "tr")


def flip_y_scalar(y: float, span: float) -> float:
    """全仓库唯一翻转实现：``y' = span - y``（对合，即自反）。

    仅供 SVG 互换层（:func:`paper_to_svg_ydown` / :func:`paper_from_svg_ydown`）
    与归一化域格式编码调用；其他任何地方不得手写翻转。
    """
    return span - y


def mirror_scalar(v: float, lo: float, hi: float) -> float:
    """区间 ``[lo, hi]`` 内的镜像（对合）：把 ``v`` 映到关于区间中点的对称点。

    复用唯一翻转实现 :func:`flip_y_scalar`（D3：镜像数学必须落本文件）::

        v' = flip_y_scalar(v - lo, hi - lo) + lo
           = (hi - lo) - (v - lo) + lo = hi - v

    减 ``lo`` 再加回是为了写成**任意区间**的形式（而非「0 起点」专用）——
    FR-08 v1.3 定稿：镜像支点 = **本地 bbox 中心**（一般式），不依赖
    ``normalize_local`` 的 ``y0 == 0`` 不变量（它只在创建/导入入口调用，
    直接构造的 Item 同样要正确）。``lo == 0`` 时退化为 ``flip_y_scalar(v, hi)``。

    水平/垂直同式：把 v 换成对应分量、区间换成对应轴的 bbox 跨度即可。
    """
    return flip_y_scalar(v - lo, hi - lo) + lo


def paper_to_svg_ydown(paths: Path2D, bed_h: float = BED_H) -> Path2D:
    """纸面 y-up → SVG y-down（格式编解码；对合）。

    SVG 用户单位 = mm，y 向下（浏览器所见 = 从上方看床）。返回新列表。
    """
    return [[(x, flip_y_scalar(y, bed_h)) for x, y in p] for p in paths]


def paper_from_svg_ydown(paths: Path2D, bed_h: float = BED_H) -> Path2D:
    """SVG y-down → 纸面 y-up（格式编解码；对合，与 paper_to_svg_ydown 同式）。"""
    return [[(x, flip_y_scalar(y, bed_h)) for x, y in p] for p in paths]


def machine_from_paper(p: Point) -> Point:
    """纸面 → 机器（**恒等**）：M114 logical X/Y 与纸面 mm 恒等，单测锁死。"""
    return p


def paper_from_machine(p: Point) -> Point:
    """机器 → 纸面（**恒等**，与 machine_from_paper 同义）。"""
    return p


def translate_paths(
    paths: Path2D,
    dx: float,
    dy: float,
) -> Path2D:
    """把多段线整体平移 (dx, dy)。用于把工件坐标路径平移到机器坐标。

    P1c 工件原点采用 GUI 维护偏移（不发 G92 给固件）：SVG 路径是工件坐标，
    发机器前加偏移 = 机器坐标。纯平移（无旋转/缩放/镜像），保段序与方向。
    返回新列表，不改入参。
    """
    if dx == 0.0 and dy == 0.0:
        return [list(p) for p in paths]
    return [
        [(x + dx, y + dy) for x, y in p]
        for p in paths
    ]


def bbox_of(paths: Path2D) -> BBox | None:
    """内容 bbox ``(x0, y0, x1, y1)``；空输入/无点返回 None。"""
    xs: list[float] = []
    ys: list[float] = []
    for p in paths:
        for x, y in p:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def anchor_point(bbox: BBox, anchor: str = "bl") -> Point:
    """九宫格锚点在 bbox 内的坐标（纯几何，无翻转）。

    ``bl/bc/br/ml/mc/mr/tl/tc/tr``：b/m/t = 下/中/上（y-up），l/c/r = 左/中/右。
    """
    code = (anchor or "").lower()
    if code not in ANCHORS:
        raise ValueError(f"未知锚点: {anchor!r}（可用: {', '.join(ANCHORS)}）")
    x0, y0, x1, y1 = bbox
    pick = {"min": lambda a, b: a, "mid": lambda a, b: (a + b) / 2.0,
            "max": lambda a, b: b}
    x = pick[_ANCHOR_COLS[code[1]]](x0, x1)
    y = pick[_ANCHOR_ROWS[code[0]]](y0, y1)
    return (x, y)


def place_at_anchor(
    paths: Path2D,
    anchor: str = "bl",
    target: Point = (0.0, 0.0),
) -> Path2D:
    """把内容按九宫格锚点平移放置到 target（纯平移，无翻转/缩放）。

    整组共用一次平移（多段线相对布局保持）；空输入返回拷贝。
    """
    bbox = bbox_of(paths)
    if bbox is None:
        return [list(p) for p in paths]
    ax, ay = anchor_point(bbox, anchor)
    return translate_paths(paths, target[0] - ax, target[1] - ay)


def grid_steps(ppm: float, target_px: float = 40.0) -> tuple[float, float]:
    """Heckbert nice numbers（Graphics Gems 1990）网格步距。

    major = 满足 ``major * ppm >= target_px`` 的最小 1/2/5×10^k（mm）；
    minor = major / 5。返回 ``(major_mm, minor_mm)``。
    """
    if ppm <= 0:
        raise ValueError(f"ppm 须 > 0: {ppm}")
    if target_px <= 0:
        raise ValueError(f"target_px 须 > 0: {target_px}")
    need = target_px / ppm
    for k in range(-12, 24):
        for m in (1.0, 2.0, 5.0):
            cand = m * (10.0 ** k)
            if cand >= need * (1.0 - 1e-12):
                return (cand, cand / 5.0)
    raise ValueError(f"步距超出支持范围: need={need}")


def snap(v: float, pitch: float) -> float:
    """吸附到 pitch 网格（最近格点，纯标量）。"""
    if pitch <= 0:
        raise ValueError(f"pitch 须 > 0: {pitch}")
    return float(round(v / pitch) * pitch)
