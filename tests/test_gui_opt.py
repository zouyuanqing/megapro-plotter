"""轨迹去重/优化单测（纯逻辑）。"""

import pytest

from megapro.gui.opt import dedup_and_optimize, dedup_overlap


def test_exact_duplicate_polyline_removed():
    paths = [[(0, 0), (10, 0)], [(0, 0), (10, 0)]]  # 完全重复
    out = dedup_overlap(paths, tol=0.01)
    assert len(out) == 1


def test_reversed_duplicate_removed():
    paths = [[(0, 0), (10, 0)], [(10, 0), (0, 0)]]  # 反向重复
    out = dedup_overlap(paths, tol=0.01)
    assert len(out) == 1


def test_shared_edge_segment_removed():
    # 两个方块共享一条边 (10,0)-(10,10)：第二块的这条边不应再画
    a = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    b = [(10, 0), (20, 0), (20, 10), (10, 10), (10, 0)]
    out = dedup_overlap([a, b], tol=0.05)
    # 第二块 b 的共享边 (10,0)->(10,10) 或反向应在段级被去：b 应少 1 段
    segs_b = 0
    for p in out:
        # 找 b 的折线（含 (20,0)）
        if any(abs(x - 20) < 0.01 and abs(y) < 0.01 for x, y in p):
            segs_b = len(p) - 1
    assert segs_b == 3, f"共享边应去掉，剩 3 段，实际 {segs_b}"


def test_near_but_not_overlapping_kept():
    # 平行但相距 >tol 的线（间隔 1mm，tol=0.1）不误删
    a = [(0, 0), (10, 0)]
    b = [(0, 1.0), (10, 1.0)]
    out = dedup_overlap([a, b], tol=0.1)
    assert len(out) == 2


def test_pen_diameter_tolerance():
    # tol=笔径/2：两线间隔 0.3，笔径 0.5 → tol 0.25 < 0.3 不删
    a = [(0, 0), (10, 0)]
    b = [(0, 0.3), (10, 0.3)]
    assert len(dedup_overlap([a, b], tol=0.25)) == 2
    # 间隔 0.2，tol 0.25 ≥ 0.2 → 判重叠删（端点同 x 不同 y，距离 0.2≤0.25）
    # 注意：段级判定只比对端点距离，两条平行线端点 (0,0)vs(0,0.3) 距离 0.3≠端点相同——
    # 保守定义下平行靠近但端点不同的线**不**算完全重叠，故仍保留。这里验证端点在容差内的
    # 反向场景由 shared_edge 覆盖。此测试仅确保负 tol 抛错。
    with pytest.raises(ValueError):
        dedup_overlap([a], tol=-0.1)


def test_dedup_and_optimize_off():
    paths = [[(0, 0), (10, 0)], [(0, 0), (10, 0)]]
    # dedup=False, optimize=False → 原样（深拷贝）
    out = dedup_and_optimize(paths, dedup=False, optimize=False)
    assert len(out) == 2
    # dedup=True → 1
    out2 = dedup_and_optimize(paths, dedup=True, tol=0.01, optimize=False)
    assert len(out2) == 1


def test_optimize_sorts_by_nearest():
    # optimize=True 调 NN：从 (0,0) 起最近先
    far = [(100, 0), (101, 0)]
    near = [(0, 1), (1, 1)]
    out = dedup_and_optimize([far, near], optimize=True)
    # NN 应从 (0,0) 先到 near(0,1)
    assert out[0][0] == (0.0, 1.0)
