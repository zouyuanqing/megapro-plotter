"""P1 job 编译器单测（纯逻辑，无 Qt/无真机）。

覆盖：SVG→polylines、写字 Z 映射（落=pen_down_z/抬=safe_z 绝对）、
越界预检、A4 超程、裁纸闭合轮廓 + 深度、G92 无关（坐标即工件）。
"""

from pathlib import Path

import pytest

# 阶段 3：job.flip_y 转发 shim 已退役（核心红线：剥注释/字符串后 grep 零命中）；
# 阶段 5 C1：本文件引 coords 真名 paper_to_svg_ydown（不再用 flip_y 等价别名），
# 断言语义逐字等价（shim 原本就是一行转发）。
from megapro.gui.canvas.coords import paper_to_svg_ydown
from megapro.gui.job import (
    check_bounds,
    cut_z_for_depth,
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


def test_paper_to_svg_ydown_global():
    # SVG y-down → 左下原点 CAD 约定：y' = 210 - y（唯一翻转实现，语义等价）
    paths = [[(0.0, 10.0), (20.0, 90.0)]]
    out = paper_to_svg_ydown(paths, bed_h=210.0)
    assert out[0] == [(0.0, 200.0), (20.0, 120.0)]
    # x 不变，y 镜像；原列表不变
    assert paths[0] == [(0.0, 10.0), (20.0, 90.0)]
    # 翻转两次 = 还原
    assert paper_to_svg_ydown(out, bed_h=210.0) == paths


def test_cutting_depth_roundtrip():
    # touch_z=17 深 0.5 → gcode 下压到 Z16.5（不是 Z0，不是 touch 本身）
    square = [[(10, 10), (30, 10), (30, 30), (10, 30), (10, 10)]]
    lines = gcode_for_cutting(square, cut_down_z=cut_z_for_depth(17.0, 0.5),
                              safe_z=30.0)
    assert any(l.startswith("G1 Z16.5") for l in lines)
    assert not any(" Z17 " in l and l.startswith("G1") for l in lines)


# ===========================================================================
# 阶段 1 扩充（蓝图 §7/§8.2）：compile_job 流水线 + cur=None 回归 + 越界 v2
# ===========================================================================


def _mk_spec(paths, *, tool="pen", sort=False, work_origin=None, zmap=None,
             material=None, motion=None, placement=None):
    """构造最小 JobSpec（默认：源序直通、preserve 放置、ZMap(30, 17)）。"""
    from megapro.gui.job import (
        JobSpec, Material, MotionParams, OptParams, Placement, ZMap,
    )

    return JobSpec(
        paths_paper=paths,
        source_name="test",
        tool=tool,
        placement=placement or Placement(mode="preserve"),
        opt=OptParams(dedup=False, sort=sort),
        motion=motion or MotionParams(),
        zmap=zmap if zmap is not None else ZMap(30.0, 17.0),
        material=material or Material(210.0, 210.0),
        work_origin=work_origin,
    )


def _first_motion(lines):
    """第一条含 X/Y 的运动行。"""
    return next(l for l in lines if l.startswith(("G0 X", "G1 X")))


# --- 根因 #5 回归：首段必发定位 G0 -------------------------------------------

def test_first_segment_positioning_g0():
    from megapro.gui.job import gcode_for_cutting, gcode_for_drawing

    cases = [
        (gcode_for_drawing, dict(pen_down_z=17.0, safe_z=30.0)),
        (gcode_for_cutting, dict(cut_down_z=12.0, safe_z=30.0)),
    ]
    for fn, kw in cases:
        # p[0] == (0,0) 的情形：cur=None 语义下也必须发定位 G0
        lines = fn([[(0.0, 0.0), (10.0, 0.0)]], **kw)
        assert "G0 X0 Y0 F1200" in lines, "首段必发定位 G0（哪怕起点恰是 (0,0)）"
        i_pos = lines.index("G0 X0 Y0 F1200")
        i_down = next(i for i, l in enumerate(lines) if l.startswith("G1 Z"))
        assert i_pos < i_down
        # 非零起点同样先定位
        lines2 = fn([[(5.5, 6.5), (10.0, 0.0)]], **kw)
        assert lines2.index("G0 X5.5 Y6.5 F1200") < next(
            i for i, l in enumerate(lines2) if l.startswith("G1 Z"))


def test_knife_plunge_after_positioning():
    from megapro.gui.job import gcode_for_cutting

    square = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]]
    lines = gcode_for_cutting(square, cut_down_z=12.0, safe_z=30.0)
    i_pos = lines.index("G0 X0 Y0 F1200")
    i_plunge = next(i for i, l in enumerate(lines) if l.startswith("G1 Z"))
    assert i_pos < i_plunge, "裁刀下压必须发生在定位到 p[0] 之后"
    assert all(not l.startswith("G1 Z") for l in lines[:i_pos])


# --- §8.2 编译 golden / 回读一致性 -------------------------------------------

def test_compile_golden_text():
    from megapro.gui.job import compile_job

    cj = compile_job(_mk_spec([[(0.0, 0.0), (10.0, 0.0)]]))
    assert list(cj.lines) == [
        "G90",
        "G21",
        "G0 Z30 F300",
        "G0 X0 Y0 F1200",
        "G1 Z17 F300",
        "G1 X0 Y0 F1200",
        "G1 X10 Y0 F1200",
        "G0 Z22 F300",
        "M400",
    ]


def test_compile_parse_roundtrip():
    """滤零长段后端点序列 ≡ 平移后输入（placement preserve + work_origin）。"""
    from megapro.gui.job import compile_job, translate_paths

    paths = [[(1.0, 2.0), (11.0, 2.0), (11.0, 12.0)], [(50.0, 50.0), (60.0, 55.0)]]
    origin = (10.0, 5.0)
    cj = compile_job(_mk_spec(paths, work_origin=origin))
    runs: list[list] = []
    cur: list = []
    for s in cj.segments:
        if s.kind == "DRAW":
            if s.p0 != s.p1:  # 滤零长段（发射器把 p[0] 重复发一次 G1）
                cur.append(s)
        else:
            if cur:
                runs.append(cur)
                cur = []
    if cur:
        runs.append(cur)
    rebuilt = [
        [(r[0].p0[0], r[0].p0[1])] + [(s.p1[0], s.p1[1]) for s in r]
        for r in runs
    ]
    assert rebuilt == translate_paths(paths, origin[0], origin[1])


def test_work_origin_translation_appears_in_lines():
    from megapro.gui.job import compile_job

    paths = [[(0.0, 0.0), (10.0, 0.0)]]
    with_origin = compile_job(_mk_spec(paths, work_origin=(10.0, 5.0)))
    text = "\n".join(with_origin.lines)
    assert "G0 X10 Y5 F1200" in text
    assert "G1 X20 Y5 F1200" in text
    assert "X0 Y0" not in text  # 偏移已并入，不再有工件 (0,0)
    plain = compile_job(_mk_spec(paths))
    assert "G0 X0 Y0 F1200" in plain.lines
    # meta 记录的机器坐标路径同源
    assert with_origin.meta["paths"] == [[(10.0, 5.0), (20.0, 5.0)]]


def test_z_never_negative():
    """合成参数（不依赖 profile 当前值）：负 Z 的算术面一律 ValueError。"""
    from megapro.gui.job import compile_job, cut_z_for_depth, gcode_for_cutting

    with pytest.raises(ValueError):
        cut_z_for_depth(2.0, 5.0)  # touch_z < depth → 下压 Z 为负
    with pytest.raises(ValueError):
        cut_z_for_depth(17.0, -0.5)
    with pytest.raises(ValueError):
        gcode_for_cutting([[(0.0, 0.0), (1.0, 0.0)]],
                          cut_down_z=-1.0, safe_z=30.0)
    # 编译期断言：real 模式 safe_z > down_z ≥ 0
    from megapro.gui.job import ZMap

    with pytest.raises(ValueError):
        compile_job(_mk_spec([[(0.0, 0.0), (1.0, 0.0)]], zmap=ZMap(30.0, -1.0)))
    with pytest.raises(ValueError):
        compile_job(_mk_spec([[(0.0, 0.0), (1.0, 0.0)]], zmap=ZMap(17.0, 30.0)))
    # 合法裁刀下压输出全 Z ≥ 0
    cj = compile_job(_mk_spec(
        [[(10.0, 10.0), (30.0, 10.0), (30.0, 30.0), (10.0, 10.0)]],
        tool="knife",
        zmap=ZMap(30.0, cut_z_for_depth(2.0, 1.0))))
    zvals = [s.p1[2] for s in cj.segments]
    assert zvals and min(zvals) >= 0.0


def test_dry_run_compiles_without_draw():
    from megapro.gui.job import compile_job

    # spec zmap 带真实落笔档（17），空跑应改为 down==safe 并全程不下压
    cj = compile_job(_mk_spec([[(0.0, 0.0), (10.0, 0.0)]]), dry_run=True)
    kinds = {s.kind for s in cj.segments}
    assert cj.segments
    assert "DRAW" not in kinds and "PLUNGE" not in kinds
    assert cj.meta["dry_run"] is True
    assert not any("Z17" in l for l in cj.lines)  # 空跑不触纸
    assert cj.runnable  # 空跑也可下发（物理空跑）


def test_guard_passes_compiled_job():
    from megapro.gui.controller import sequence_ok
    from megapro.gui.job import compile_job

    cj = compile_job(_mk_spec(
        [[(10.0, 10.0), (30.0, 10.0), (30.0, 30.0), (10.0, 10.0)]],
        tool="knife"))
    assert cj.lines
    assert sequence_ok(list(cj.lines), allow_z=True) == []


def test_opt_sort_flag_respected():
    from megapro.gui.job import compile_job

    far = [(100.0, 100.0), (110.0, 100.0)]
    near = [(1.0, 1.0), (2.0, 1.0)]
    keep = compile_job(_mk_spec([far, near], sort=False))
    assert _first_motion(keep.lines) == "G0 X100 Y100 F1200"  # 文件序直通
    sorted_job = compile_job(_mk_spec([far, near], sort=True))
    assert _first_motion(sorted_job.lines) == "G0 X1 Y1 F1200"  # NN 选近的


# --- 越界预检 v2（行程 ∩ 可用区；pen_radius 只内缩远端） ----------------------

def test_check_bounds_v2_frames_and_pen_radius():
    from megapro.gui.job import check_bounds_v2

    # 行程 ∩ 可用区：A4 210×297、margin=2 → 可用区远端 = min(210,297)−2 = 208
    rep = check_bounds_v2([[(10.0, 209.0), (20.0, 209.0)]],
                          travel=(210.0, 210.0), material=(210.0, 297.0),
                          margin=2.0, pen_radius=0.0)
    assert not rep.ok
    assert rep.judge == (0.0, 0.0, 208.0, 208.0)
    assert rep.violations[0].frame_name == "可用区"
    assert rep.violations[0].point == (10.0, 209.0)
    assert rep.violations[0].path_idx == 0
    # 同点在 margin=0 时全过（A4 横向 210 全宽可用）
    assert check_bounds_v2([[(10.0, 209.0)]], travel=(210.0, 210.0),
                           material=(210.0, 297.0), margin=0.0,
                           pen_radius=0.0).ok
    # 超行程
    rep2 = check_bounds_v2([[(10.0, 211.0)]], travel=(210.0, 210.0),
                           material=(210.0, 297.0), margin=2.0, pen_radius=0.0)
    assert rep2.violations[0].frame_name == "行程"
    # pen_radius 只内缩远端
    assert check_bounds_v2([[(209.8, 5.0)]], travel=(210.0, 210.0),
                           material=(210.0, 210.0), margin=0.0,
                           pen_radius=0.0).ok
    rep3 = check_bounds_v2([[(209.8, 5.0)]], travel=(210.0, 210.0),
                           material=(210.0, 210.0), margin=0.0,
                           pen_radius=0.5)
    assert rep3.violations[0].frame_name == "笔径"
    # 靠原点 (0,5)：笔径不拦（0 是物理归位端，纸从 0 铺起）
    assert check_bounds_v2([[(0.0, 5.0)]], travel=(210.0, 210.0),
                           material=(210.0, 210.0), margin=0.0,
                           pen_radius=0.5).ok


def test_compile_strict_bounds_semantics():
    from megapro.gui.job import JobBoundsError, compile_job

    over = _mk_spec([[(0.0, 0.0), (220.0, 0.0)]])
    with pytest.raises(JobBoundsError) as ei:
        compile_job(over)
    assert ei.value.report.violations
    # strict=False 返回同一个 CompiledJob：report 在 .bounds、执行绑 runnable
    cj = compile_job(over, strict=False)
    assert not cj.bounds.ok
    assert not cj.runnable
    assert cj.segments  # 预览仍可画（违规红点同源）


def test_z_never_negative_lift_channel():
    """派生 lift_z 同受「输出所有 Z ≥ 0」约束（评审 medium #1：负 travel_lift_mm）。"""
    from megapro.gui.job import (
        MotionParams, ZMap, compile_job, gcode_for_cutting, gcode_for_drawing,
    )

    paths = [[(0.0, 0.0), (10.0, 0.0)]]
    with pytest.raises(ValueError):
        gcode_for_drawing(paths, pen_down_z=17.0, safe_z=30.0,
                          travel_lift_mm=-25.0)
    with pytest.raises(ValueError):
        gcode_for_cutting(paths, cut_down_z=12.0, safe_z=30.0,
                          travel_lift_mm=-25.0)
    with pytest.raises(ValueError):
        compile_job(_mk_spec(paths, zmap=ZMap(30.0, 17.0),
                             motion=MotionParams(travel_lift_mm=-25.0)))
    # 合法编译输出无任何负 Z 字面（含 G0 Z{lift_z}）
    good = compile_job(_mk_spec(paths))
    assert all("Z-" not in l for l in good.lines)
    assert good.meta["lift_z"] == pytest.approx(22.0)  # min(30, 17+5)


def test_controller_alignment_builders():
    """阶段 1 新增对准构造器/门禁的最小回归保护（独立评审 low #5）。

    golden 全量按 §7 阶段 4 落在 tests/test_gui_controller.py；但本阶段红线
    只允许改 tests/test_coords.py / test_gcode_parse.py / test_gui_job.py，
    故在此先钉行为（bbox 校验、rounds、work_origin=None、状态文案）。
    """
    from megapro.gui.controller import (
        MachineError,
        build_frame_sequence,
        build_goto_origin_sequence,
        build_move_to_sequence,
        can_start_job,
    )

    assert build_move_to_sequence(10.0, 5.0, 30.0) == [
        "G90", "G0 Z30 F300", "G0 X10 Y5 F1200"]
    frame = build_frame_sequence((0.0, 0.0, 20.0, 30.0), 30.0)
    assert frame[0] == "G90" and frame[-1] == "M400"
    assert "G0 X20 Y0 F1200" in frame and "G0 X0 Y30 F1200" in frame
    assert len(build_frame_sequence((0.0, 0.0, 1.0, 1.0), 30.0, rounds=2)) == 13
    with pytest.raises(MachineError):
        build_frame_sequence((5.0, 5.0, 1.0, 1.0), 30.0)  # bbox 顺序非法
    with pytest.raises(MachineError):
        build_frame_sequence((0.0, 0.0, 1.0, 1.0), 30.0, rounds=0)
    with pytest.raises(MachineError):
        build_move_to_sequence(1.0, 1.0, -1.0)  # safe_z 不能为负
    with pytest.raises(MachineError):
        build_goto_origin_sequence(None, 30.0)  # 未设工件原点
    assert build_goto_origin_sequence((10.0, 5.0), 30.0) == \
        build_move_to_sequence(10.0, 5.0, 30.0)
    assert can_start_job("READY", True) is None
    assert can_start_job("READY", False)  # 未归位
    assert can_start_job("DISCONNECTED", True)  # 未连接
    assert can_start_job("ESTOP", True)  # 急停
    assert can_start_job("FAULT", True)
    assert can_start_job("BUSY", True)
