"""轨迹优化（纯逻辑，无 Qt）：重叠去重 + 顺序优化。

决策（用户 2026-09-07）：
- 去重：整线级（完全相同的多段线，含反向）+ 段级（完全重叠段，端点相同
  ±容差、含反向；容差=笔尖半径）。保守：不合并部分重叠，避免误删正常笔划。
- 笔尖直径 → 去重容差（tol = 直径/2）。
- 顺序优化：可选；内部用 toolchain 的 nearest_neighbor_sort（NN）。
"""

from __future__ import annotations

import math

__all__ = ["dedup_overlap", "dedup_and_optimize"]


def _same_pt(p, q, tol: float) -> bool:
    return math.hypot(p[0] - q[0], p[1] - q[1]) <= tol


def _path_key(p: list, tol_round: int = 3) -> tuple:
    """整线键：点序列规范化（含反向），用于整线级去重。"""
    pts = [(round(x, tol_round), round(y, tol_round)) for x, y in p]
    rev = pts[::-1]
    return tuple(min(pts, rev))  # 方向无关


def _segments(paths):
    """产出 (path_idx, seg_idx, (x0,y0),(x1,y1)) —— 保留去重后的引用。"""
    for pi, p in enumerate(paths):
        for si in range(len(p) - 1):
            yield pi, si, p[si], p[si + 1]


def dedup_overlap(
    paths: list[list[tuple[float, float]]],
    tol: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """去重：整线级（完全相同多段线）→ 段级（完全重叠段）。

    保守判定：
    - 整线级：两条折线点序列相同（含反向）→ 只留第一条。
    - 段级：遍历所有段，若某段与已保留的任一**更早**段「完全重叠」——
      两端点距离都 ≤ tol（方向一致或反向皆可）→ 跳过该段。
    结果：每条折线仍保留（去掉其中被判重复的段后若只剩 <2 点则丢弃）。
    """
    if tol < 0:
        raise ValueError(f"容差不能为负: {tol}")
    # 1) 整线级去重（保序）
    seen_lines: set[tuple] = set()
    kept_paths: list[list[tuple[float, float]]] = []
    for p in paths:
        if len(p) < 2:
            continue
        k = _path_key(p)
        if k in seen_lines:
            continue
        seen_lines.add(k)
        kept_paths.append([(float(x), float(y)) for x, y in p])

    # 2) 段级去重：收集已保留的段（规范化，方向无关，±tol）
    kept_segs: list[tuple[tuple, tuple]] = []  # 存两端点元组

    def seg_overlaps(x0, y0, x1, y1) -> bool:
        for (ax, ay), (bx, by) in kept_segs:
            # 同向：a≈(x0,y0) 且 b≈(x1,y1)；反向：a≈(x1,y1) 且 b≈(x0,y0)
            if (_same_pt((ax, ay), (x0, y0), tol) and _same_pt((bx, by), (x1, y1), tol)) \
               or (_same_pt((ax, ay), (x1, y1), tol) and _same_pt((bx, by), (x0, y0), tol)):
                return True
        return False

    out: list[list[tuple[float, float]]] = []
    for p in kept_paths:
        cur: list[tuple[float, float]] = []
        for si in range(len(p) - 1):
            x0, y0 = p[si]
            x1, y1 = p[si + 1]
            if seg_overlaps(x0, y0, x1, y1):
                # 跳过重叠段：若当前已积累 → 收尾（断成一条）；中间跳段不桥接
                if cur:
                    out.append(cur)
                    cur = []
                continue
            kept_segs.append(((x0, y0), (x1, y1)))
            if not cur:
                cur.append((x0, y0))
            cur.append((x1, y1))
        if cur:
            # 去掉尾部自交重复点（若闭合方块的收尾段恰好是首段重叠被去）
            while len(cur) >= 2 and _same_pt(cur[-1], cur[-2], tol):
                cur.pop()
            if len(cur) >= 2:
                out.append(cur)
    return out


def dedup_and_optimize(
    paths: list[list[tuple[float, float]]],
    *,
    dedup: bool = False,
    tol: float = 0.0,
    optimize: bool = True,
) -> list[list[tuple[float, float]]]:
    """组合入口：可选去重 → 可选 NN 顺序优化。

    tol = 去重容差（笔尖半径）。optimize 用 toolchain nearest_neighbor_sort
    （从 (0,0) 起的贪心最近邻，每段可反转）。去重在优化前（工件坐标）。
    """
    if dedup:
        paths = dedup_overlap(paths, tol=tol)
    else:
        paths = [list(p) for p in paths]
    if optimize:
        from megapro.toolchain.svg_to_gcode import nearest_neighbor_sort

        paths = nearest_neighbor_sort(paths)
    return paths
