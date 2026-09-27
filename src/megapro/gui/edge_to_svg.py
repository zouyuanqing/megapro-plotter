"""图片 → 可画**中心线**线条 SVG（纯逻辑；替代 potrace 区域轮廓，消双线）。

管线（研究结论 2026-09）：预滤波 → 骨架化(thin) → 骨架图追踪成折线 → SVG。
- 骨架化：skimage.morphology.thin → 1px 中心线。
- 追踪：自写骨架图游走（分支点断开成路径），不依赖 skan 易变 API。
- 照片/素描模式（PRD FR-10）：`cv2.Canny` 边缘图 → 同样 thin → 同样游走，
  只是前段用梯度边缘代替阈值二值（见文末 `trace_centerline_canny`）。
输出 mm 描边折线 SVG（y-down；由排版/作业按坐标约定处理）。
（DL 检测器曾接入后因效果不佳回滚移除，仅保留纯算法中心线。）
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["trace_centerline", "trace_centerline_multi",
           "image_to_centerline_svg", "image_to_centerline_svg_multi",
           "CANNY_LOW", "CANNY_HIGH", "CANNY_APERTURE",
           "trace_centerline_canny", "image_to_canny_centerline_svg"]

import numpy as np

#: 多阈值灰阶分档（0–255 灰度值，**与床尺寸无关**）：90/130/170/210（等差 40）。
#: §8.4 常量收敛后 src 不留裸 210 数值字面（那是 canvas/coords.py 的
#: BED_W/BED_H 唯一定义处），故写成等差式生成，取值逐个不变。
GRAY_THRESHOLDS = tuple(90 + 40 * k for k in range(4))


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
                           thresholds=GRAY_THRESHOLDS,
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
                                  thresholds=GRAY_THRESHOLDS,
                                  simplify_tol: float = 0.8) -> str:
    """图片 → 中心线 SVG（**多阈值合并**，一次提全线条）。"""
    paths = trace_centerline_multi(img, max_px=max_px,
                                   thresholds=thresholds,
                                   simplify_tol=simplify_tol)
    return _paths_to_svg(paths, target_mm)


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# 照片/素描模式（PRD FR-10 算法部分）：Canny → thin → 游走 → 简化
# ---------------------------------------------------------------------------
#
# 三个默认参数由 `tests/test_gui_edge.py` 的**合成样张 golden** 定基线
# （PRD §11-3：low/high、aperture 以 golden 定基线，真机调优**需现场**）。
# 取值理由（样张 = 240×288：上带纹理噪声 + 低对比软斑 + 硬边圆 + 硬边三角）：
#
# - ``aperture_size=3``（cv2 默认 Sobel）：实测 5 / 7 在带纹理的样张上把噪声
#   放大成 740 / 730 条碎路径（其中 732 / 632 条全在噪声带上）——大核把高频
#   纹理当边缘。3 是唯一稳定档，故锁死。cv2 自行校验只接受 3/5/7（传 4/9/1
#   直接抛错），故本模块不再重复校验。
# - ``low=50``：**噪声抑制的下界**。实测 low=45 时顶部纹理带冒出 2 条假线
#   （共 5 条），low=50 起干净；而 (50,120)/(60,140)/(70,160)/(80,180)/
#   (100,200)/(50,200) **六组输出逐位相同**（各 3 条 85 点），故取 50 ——
#   白拿低对比软边，不付噪声代价。
# - ``high=120``：取同一稳定平台的**低端**（120~200 输出逐位相同），偏向
#   低对比边缘；再往上只会丢软边不换任何东西。
#
# 整段刻意**不引入**任何新依赖：cv2 / skimage / numpy 均为
# requirements-gui.txt:5-7 已声明（opencv-python>=4.10 / scikit-image>=0.24）。
CANNY_LOW = 50
CANNY_HIGH = 120
CANNY_APERTURE = 3


def _paths_from_skel(skel, simplify_tol: float) -> list:
    """1px 骨架图 → 折线列表（游走 + DP 简化），像素 (x, y)。

    与 `_trace_one`(:135) 的「二值 → thin → 游走 → 简化」同构，只是把
    前段的阈值二值换成调用方给的骨架图。**刻意不抽公共函数去改
    `_trace_one`** —— 本里程碑只新增、不改既有函数。
    """
    out = []
    for p in _skeleton_paths(skel):
        pts = _dp_simplify([(float(x), float(y)) for (y, x) in p], simplify_tol)
        if len(pts) >= 2:
            out.append(pts)
    return out


def trace_centerline_canny(img, *, max_px: int = 1200,
                           low: int = CANNY_LOW, high: int = CANNY_HIGH,
                           aperture_size: int = CANNY_APERTURE,
                           simplify_tol: float = 0.8):
    """照片/素描 → **单像素宽**中心线折线（像素 (x, y)），PRD FR-10。

    管线：`cv2.Canny` 边缘图 → `skimage.morphology.thin` 骨架化 → 复用
    `_skeleton_paths`(:57) 游走 + `_dp_simplify`(:105) 简化。与既有的
    `trace_centerline`(:151) 唯一区别是**前段**：阈值二值 → Canny 梯度边缘，
    故对明暗渐变/低对比（照片、素描）比单一阈值更宽容。

    **为什么不用 findContours / approxPolyDP**（PRD FR-10 明文否决）：
    Canny 输出的是 1px 边缘**带**，findContours 沿带两侧各描一条轮廓。实测
    r=60 圆：同一张 1px 带上 `findContours` 返 **2 条** 336/340 点轮廓
    ≈ 双线，而本函数返 **1 条 338 点**路径（恰等于骨架像素数，即每个骨架
    像素只走一次）。这与本文件 :1-8 docstring 立管的「消双线」目标直接冲突。

    **已知边界（实测，勿当 bug 也勿当特性）**：**实心笔画** Canny 只看得见它
    的两条轮廓边 —— 1px 横线（长 250px）的 edge_px 实测 504 ≈ 2× 线长，宽笔画
    同理。thin 无法合并相距 >2px 的两条 1px 线，于是走一圈 = 沿轮廓来回各画
    一遍（视觉上仍是两条平行笔迹）。要宽笔画的真中心线请用 `trace_centerline`
    （阈值二值 → thin，把笔画当实心区域）。本函数覆盖的是照片/素描的**区域
    轮廓**与细线，正是 FR-10 的场景。

    ``low > high`` 抛 ValueError：当前 OpenCV 自己会把两值归一化（不报错、
    输出不变），此处是给即将接线的加图对话框（可手输阈值）一个显式契约。
    """
    import cv2
    from skimage.morphology import thin

    if low > high:
        raise ValueError(f"Canny low 不得大于 high: low={low}, high={high}")

    arr = _preprocess_gray(img)
    h, w = arr.shape
    scale = min(1.0, max_px / max(h, w))
    if scale < 1.0:
        arr = _resize(arr, scale)
    edges = cv2.Canny(arr, low, high, apertureSize=aperture_size) > 0
    return _paths_from_skel(thin(edges), simplify_tol)


def image_to_canny_centerline_svg(img, *, target_mm: float = 180.0,
                                  max_px: int = 1200,
                                  low: int = CANNY_LOW, high: int = CANNY_HIGH,
                                  aperture_size: int = CANNY_APERTURE,
                                  simplify_tol: float = 0.8) -> str:
    """照片/素描 → 中心线 SVG（**Canny+骨架**模式，FR-10 接线用出口）。"""
    paths = trace_centerline_canny(img, max_px=max_px, low=low, high=high,
                                   aperture_size=aperture_size,
                                   simplify_tol=simplify_tol)
    return _paths_to_svg(paths, target_mm)
