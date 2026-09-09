"""P1 job 编译器单测（纯逻辑，无 Qt/无真机）。

覆盖：SVG→polylines、写字 Z 映射（落=pen_down_z/抬=safe_z 绝对）、
越界预检、A4 超程、裁纸闭合轮廓 + 深度、G92 无关（坐标即工件）。
"""

from pathlib import Path

import pytest

from megapro.gui.job import (
    check_bounds,
    cut_z_for_depth,
    flip_y,
    gcode_for_cutting,
    gcode_for_drawing,
    polylines_for_svg,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SQUARE = EXAMPLES / "square_20mm.svg"


def test_polylines_for_svg_square():
    paths = polylines_for_svg(str(SQUARE))
    assert len(paths) == 1
    sq = paths[0]
    xs = [x for x, _ in sq]
    ys = [y for _, y in sq]
    assert abs(max(xs) - min(xs) - 20.0) < 1e-6
    assert abs(max(ys) - min(ys) - 20.0) < 1e-6


# --- 写字 Z 映射 -------------------------------------------------------------

def test_drawing_uses_machine_abs_z():
    # 方块：(0,0)-(20,20)，画线往返。应：空移到起点在 safe_z；落笔 pen_down_z
    paths = polylines_for_svg(str(SQUARE))
    lines = gcode_for_drawing(paths, pen_down_z=17.0, safe_z=30.0)
    assert lines[0] == "G90"
    # 开头抬到 safe_z（仅一次，首段前）
    assert lines.count("G0 Z30 F300") == 1
    # 落笔到 pen_down_z(17) —— 绝无 Z0
    assert any(l.startswith("G1 Z17") for l in lines)
    assert not any(" Z0 " in l for l in lines)
    assert not any("Z0.000" in l for l in lines)
    # 段间抬笔是低抬（pen_down_z+lift=17+5=22），不是每次回 safe_z(30)
    # 方块只有 1 段闭合，收尾无二次抬；用多段验证低抬
    assert lines.count("G0 Z30 F300") == 1  # 只有首段前一次抬到 30
    # 收尾 M400 排空
    assert lines[-1] == "M400"


def test_drawing_segment_lift_is_low_not_safe_z():
    # 两段：段间抬离应到 pen_down_z+lift(17+5=22)，而非 safe_z(30)
    lines = gcode_for_drawing(
        [[(0, 0), (10, 0)], [(20, 0), (30, 0)]],
        pen_down_z=17.0, safe_z=30.0)
    # 段间低抬 G0 Z22 出现（每段画完抬离）
    assert any(l == "G0 Z22 F300" for l in lines)
    # safe_z(30) 只在最开头抬一次（首段前），不作段间抬
    assert lines.count("G0 Z30 F300") == 1
    # 可调 travel_lift_mm：默认 5；传 3 → 抬到 20
    lines3 = gcode_for_drawing(
        [[(0, 0), (10, 0)], [(20, 0), (30, 0)]],
        pen_down_z=17.0, safe_z=30.0, travel_lift_mm=3.0)
    assert any(l == "G0 Z20 F300" for l in lines3)
    # 封顶：pen_down+lift 超 safe_z 则用 safe_z
    lines_cap = gcode_for_drawing(
        [[(0, 0), (10, 0)], [(20, 0), (30, 0)]],
        pen_down_z=17.0, safe_z=30.0, travel_lift_mm=99.0)
    assert lines_cap.count("G0 Z30 F300") >= 1  # 封顶到 30


def test_drawing_invalid_z_order():
    with pytest.raises(ValueError):
        gcode_for_drawing([[(0, 0), (1, 0)]], pen_down_z=30.0, safe_z=17.0)


# --- 越界预检 ----------------------------------------------------------------

def test_bounds_reject_outside():
    paths = [[(0, 0), (20, 0)], [(200, 200), (211, 210)]]  # 211 > 210 越界
    bad = check_bounds(paths, max_x=210, max_y=210)
    assert len(bad) == 1
    assert "越界" in bad[0]


def test_bounds_pass_inside_with_margin():
    paths = [[(5, 5), (200, 200)]]
    assert check_bounds(paths, max_x=210, max_y=210, margin=5) == []


def test_bounds_pen_radius_compensation():
    # 远端：x=209.8 无笔径过；笔径1(半径0.5) → 上界 209.5 → 拦
    paths = [[(209.8, 5.0), (200.0, 5.0)]]
    assert check_bounds(paths, max_x=210, max_y=210) == []
    assert len(check_bounds(paths, max_x=210, max_y=210, pen_radius=0.5)) == 1
    # 靠原点 (0,5)：笔径不拦（0 是物理归位端，纸从 0 铺起）
    assert check_bounds([[(0.0, 5.0)]], max_x=210, max_y=210,
                        pen_radius=0.5) == []


def test_bounds_a4_warning():
    # A4 = 210×297；Y=297 > 行程 210 → 越界（触发超程警示逻辑用）
    a4 = [[(0, 0), (210, 297)]]
    bad = check_bounds(a4, max_x=210, max_y=210)
    assert len(bad) == 1
    # 可达区域：min(纸,210) 切角 —— 只切到 Y≤210 的部分不越界
    reachable = [[(0, 0), (210, 200)]]
    assert check_bounds(reachable, max_x=210, max_y=210) == []


# --- 裁纸 --------------------------------------------------------------------

def test_cutting_closed_contour_with_depth():
    # 一个闭合矩形：应从起点下压 cut_down_z → 沿整圈 G1 → 抬回 safe_z
    square = [[(10, 10), (30, 10), (30, 30), (10, 30), (10, 10)]]
    lines = gcode_for_cutting(square, cut_down_z=12.0, safe_z=30.0)
    assert any(l.startswith("G1 Z12") for l in lines)  # 下压到 12
    assert not any(" Z0 " in l for l in lines)
    # 空移到首点
    assert any(l.startswith("G0 X10 Y10") for l in lines)
    # 闭合轮廓：回到起点的 G1 在
    assert any(l == "G1 X10 Y10 F1200" for l in lines)
    assert lines[-1] == "M400"


def test_cutting_multiple_passes():
    square = [[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]]
    lines = gcode_for_cutting(square, cut_down_z=12.0, safe_z=30.0, passes=2)
    downs = [l for l in lines if l.startswith("G1 Z12")]
    assert len(downs) == 2  # 两遍各下压一次


def test_cutting_segment_lift_is_low():
    # 两闭合轮廓：段间抬到 cut_down+lift(12+5=17)，非 safe_z(30)
    lines = gcode_for_cutting(
        [[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
         [(20, 0), (30, 0), (30, 10), (20, 10), (20, 0)]],
        cut_down_z=12.0, safe_z=30.0)
    assert any(l == "G0 Z17 F300" for l in lines)  # 段间低抬
    assert lines.count("G0 Z30 F300") == 1  # 首段前才抬到 30


def test_cutting_invalid_depth():
    with pytest.raises(ValueError):
        gcode_for_cutting([[(0, 0), (1, 0)]], cut_down_z=40.0, safe_z=30.0)


# --- 裁刀下压深度语义（P1b） -------------------------------------------------

def test_cut_z_for_depth_touch_minus_depth():
    # 触纸 Z=17，下压 0.5mm → 绝对下压 Z=16.5
    assert cut_z_for_depth(17.0, 0.5) == pytest.approx(16.5)
    assert cut_z_for_depth(20.0, 1.0) == pytest.approx(19.0)
    with pytest.raises(ValueError):
        cut_z_for_depth(17.0, -0.1)


def test_flip_y_global():
    # SVG y-down → 左下原点 CAD 约定：y' = 210 - y
    paths = [[(0.0, 10.0), (20.0, 90.0)]]
    out = flip_y(paths, bed_h=210.0)
    assert out[0] == [(0.0, 200.0), (20.0, 120.0)]
    # x 不变，y 镜像；原列表不变
    assert paths[0] == [(0.0, 10.0), (20.0, 90.0)]
    # 翻转两次 = 还原
    assert flip_y(out, bed_h=210.0) == paths


def test_cutting_depth_roundtrip():
    # touch_z=17 深 0.5 → gcode 下压到 Z16.5（不是 Z0，不是 touch 本身）
    square = [[(10, 10), (30, 10), (30, 30), (10, 30), (10, 10)]]
    lines = gcode_for_cutting(square, cut_down_z=cut_z_for_depth(17.0, 0.5),
                              safe_z=30.0)
    assert any(l.startswith("G1 Z16.5") for l in lines)
    assert not any(" Z17 " in l and l.startswith("G1") for l in lines)
