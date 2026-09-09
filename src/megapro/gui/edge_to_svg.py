"""图片 → 可画**中心线**线条 SVG（纯逻辑；替代 potrace 区域轮廓，消双线）。

管线（研究结论 2026-09）：预滤波 → 骨架化(thin) → 骨架图追踪成折线 → SVG。
- 骨架化：skimage.morphology.thin → 1px 中心线。
- 追踪：自写骨架图游走（分支点断开成路径），不依赖 skan 易变 API。
输出 mm 描边折线 SVG（y-down；由排版/作业按坐标约定处理）。
（DL 检测器曾接入后因效果不佳回滚移除，仅保留纯算法中心线。）
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["trace_centerline", "trace_centerline_multi",
           "image_to_centerline_svg", "image_to_centerline_svg_multi"]

import numpy as np


def _preprocess_gray(img):
    """灰度化。img 为 ndarray / PIL 图 / 路径。"""
    if isinstance(img, np.ndarray):
        if img.ndim == 3:
            import cv2
            return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        return img.astype(np.uint8)
    from PIL import Image

    if isinstance(img, (str, Path)):
        img = Image.open(img)
    return np.asarray(img.convert("L"), dtype=np.uint8)


def _resize(arr, scale):
    import cv2

    h, w = arr.shape
    return cv2.resize(arr, (max(1, int(w * scale)), max(1, int(h * scale))),
                      interpolation=cv2.INTER_AREA)


def _neighbors8(y, x, shape):
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            ny, nx = y + dy, x + dx
            if 0 <= ny < shape[0] and 0 <= nx < shape[1]:
                yield ny, nx


def _skeleton_paths(skel) -> list:
    """把 1px 骨架(True=线)游走成折线（(y,x) 像素序）。分支点断开。"""
    h, w = skel.shape
    visited = np.zeros_like(skel, dtype=bool)
    paths: list = []

    def walk(start):
        path = []
        stack = [start]
        while stack:
            cur = stack.pop()
            if visited[cur]:
                continue
            visited[cur] = True
            path.append(cur)
            nxt = [n for n in _neighbors8(cur[0], cur[1], skel.shape)
                   if skel[n] and not visited[n]]
            if nxt:  # 分支多叉只取一条继续；其余留待其他起点/兜底
                stack.append(nxt[0])
        return path

    starts = []
    for y in range(h):
        for x in range(w):
            if not skel[y, x]:
                continue
            deg = sum(1 for n in _neighbors8(y, x, skel.shape) if skel[n])
            if deg <= 1:
                starts.append((y, x))
    if not starts:
        ys, xs = np.nonzero(skel)
        if len(ys):
            starts.append((int(ys[0]), int(xs[0])))
    for s in starts:
        if not visited[s]:
            p = walk(s)
            if len(p) >= 2:
                paths.append(p)
    # 兜底：分支漏网未访问点
    ys, xs = np.nonzero(skel & ~visited)
    for y, x in zip(ys, xs):
        if not visited[(y, x)]:
            p = walk((int(y), int(x)))
            if len(p) >= 2:
                paths.append(p)
    return paths


def _dp_simplify(pts, tol):
    """Douglas–Peucker，(x,y) 折线。"""
    if len(pts) <= 2:
        return pts

    def dist(p, a, b):
        (x1, y1), (x2, y2) = a, b
        dx, dy = x2 - x1, y2 - y1
        den = (dx * dx + dy * dy) ** 0.5
        if den == 0:
            return ((p[0] - x1) ** 2 + (p[1] - y1) ** 2) ** 0.5
        return abs(dy * p[0] - dx * p[1] + x2 * y1 - y2 * x1) / den

    def rec(pl):
        if len(pl) <= 2:
            return pl
        dmax, idx = 0.0, 0
        for i in range(1, len(pl) - 1):
            d = dist(pl[i], pl[0], pl[-1])
            if d > dmax:
                dmax, idx = d, i
        if dmax > tol:
            left = rec(pl[: idx + 1])
            right = rec(pl[idx:])
            return left[:-1] + right
        return [pl[0], pl[-1]]

    return rec(pts)


def _trace_one(arr, threshold, invert, simplify_tol):
    """单阈值：二值 → thin → 骨架游走 → 简化折线。"""
    from skimage.morphology import thin

    fg = arr < threshold if invert else arr > threshold
    skel = thin(fg)
    raw = _skeleton_paths(skel)
    out = []
    for p in raw:
        pts = [(float(x), float(y)) for (y, x) in p]
        pts = _dp_simplify(pts, simplify_tol)
        if len(pts) >= 2:
            out.append(pts)
    return out


def trace_centerline(img, *, max_px: int = 1200, threshold: int = 160,
                     invert: bool = True, simplify_tol: float = 0.8):
    """图片 → 中心线折线（像素 (x,y)）。暗<阈值=前景线。"""
    arr = _preprocess_gray(img)
    h, w = arr.shape
    scale = min(1.0, max_px / max(h, w))
    if scale < 1.0:
        arr = _resize(arr, scale)
    return _trace_one(arr, threshold, invert, simplify_tol)


def trace_centerline_multi(img, *, max_px: int = 1200,
                           thresholds=(90, 130, 170, 210),
                           invert: bool = True, simplify_tol: float = 0.8):
    """**多阈值拼接**：对多个阈值(淡→浓)各提一次中心线，合并去重。

    每档独立 thin+游走 → 提取全（各亮度层的线都在），重叠路径去重避免重复画。
    注：各档独立 trace 可能在档间接缝处不连续（拼接固有），但提取最全。
    """
    arr = _preprocess_gray(img)
    h, w = arr.shape
    scale = min(1.0, max_px / max(h, w))
    if scale < 1.0:
        arr = _resize(arr, scale)
    merged: list = []
    for th in sorted(thresholds):
        pts = _trace_one(arr, th, invert, simplify_tol)
        merged.extend(pts)
    if not merged:
        return []
    # 去重叠（完全重叠段只留一次）—— 多阈值会在浓线上重复提同一中心线
    from megapro.gui.opt import dedup_overlap

    return dedup_overlap(merged, tol=1.5)


def _paths_to_svg(paths, target_mm: float) -> str:
    if not paths:
        return '<svg xmlns="http://www.w3.org/2000/svg"/>\n'
    allx = [x for p in paths for x, y in p]
    ally = [y for p in paths for x, y in p]
    wpx = (max(allx) - min(allx)) or 1
    hpx = (max(ally) - min(ally)) or 1
    mm_per_px = target_mm / max(wpx, hpx)
    parts = []
    for p in paths:
        pts = " ".join(f"{_fmt(x * mm_per_px)},{_fmt(y * mm_per_px)}" for x, y in p)
        parts.append(f'<polyline points="{pts}" fill="none"/>')
    return '<svg xmlns="http://www.w3.org/2000/svg">\n  ' \
        + "\n  ".join(parts) + "\n</svg>\n"


def image_to_centerline_svg(img, *, target_mm: float = 180.0,
                            max_px: int = 1200, threshold: int = 160,
                            simplify_tol: float = 0.8) -> str:
    """图片 → 中心线 SVG（单阈值）。"""
    paths = trace_centerline(img, max_px=max_px, threshold=threshold,
                             simplify_tol=simplify_tol)
    return _paths_to_svg(paths, target_mm)


def image_to_centerline_svg_multi(img, *, target_mm: float = 180.0,
                                  max_px: int = 1200,
                                  thresholds=(90, 130, 170, 210),
                                  simplify_tol: float = 0.8) -> str:
    """图片 → 中心线 SVG（**多阈值合并**，一次提全线条）。"""
    paths = trace_centerline_multi(img, max_px=max_px,
                                   thresholds=thresholds,
                                   simplify_tol=simplify_tol)
    return _paths_to_svg(paths, target_mm)


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")
