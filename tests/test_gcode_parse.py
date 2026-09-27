"""gcode_parse 单测（纯逻辑，零 Qt）—— 将发送 G-code 文本 → 段列表。

覆盖：段分类（SETUP/TRAVEL/PLUNGE/DRAW/RETRACT/SYNC/UNKNOWN）、line_range
0 基半开、起点未知 p0=None、零长段保留、模态机 G90/G91/G21、G20/G2/G3/G5/G92
拒绝、G28 丢位置、classify 配色（笔蓝/刀紫）。
"""

import pytest

from megapro.gui.gcode_parse import (
    COLOR_DRAW_KNIFE,
    COLOR_DRAW_PEN,
    COLOR_NONE,
    COLOR_TRAVEL,
    COLOR_Z,
    Segment,
    classify,
    parse_lines,
)

# 手写最小作业行（与 gcode_for_drawing 结构一致）
DRAW_JOB = [
    "G90",
    "G21",
    "G0 Z30 F300",
    "G0 X10 Y10 F1200",
    "G1 Z17 F300",
    "G1 X10 Y10 F1200",
    "G1 X20 Y10 F1200",
    "G0 Z22 F300",
    "M400",
]


def test_parse_setup_travel_draw():
    segs = parse_lines(DRAW_JOB, z_down=17.0, z_safe=30.0, tool="pen")
    assert [s.kind for s in segs] == [
        "SETUP", "SETUP", "SETUP", "TRAVEL", "PLUNGE", "DRAW", "DRAW",
        "RETRACT", "SYNC",
    ]
    # 起点未知 p0=None（含首段前的 G0 Z{z_safe} 归 SETUP）
    assert segs[0].p0 is None
    assert segs[2].kind == "SETUP" and segs[2].p1 == (0.0, 0.0, 30.0)
    # TRAVEL 前位置 X/Y 未知 → p0=None，不画幽灵线
    assert segs[3].p0 is None and segs[3].p1 == (10.0, 10.0, 30.0)
    # PLUNGE：纯 Z 的 G1 下行到 z_down
    assert segs[4].kind == "PLUNGE"
    assert segs[4].p0 == (10.0, 10.0, 30.0) and segs[4].p1 == (10.0, 10.0, 17.0)
    # DRAW：G1 含 X/Y 且 z==z_down
    assert segs[6].kind == "DRAW"
    assert segs[6].p0 == (10.0, 10.0, 17.0) and segs[6].p1 == (20.0, 10.0, 17.0)
    # RETRACT：段间纯 Z 上行 G0
    assert segs[7].kind == "RETRACT"
    assert segs[8].kind == "SYNC"


def test_parse_line_range_half_open_zero_based():
    segs = parse_lines(DRAW_JOB, z_down=17.0, z_safe=30.0, tool="pen")
    assert [s.line_range for s in segs] == [(i, i + 1) for i in range(len(DRAW_JOB))]


def test_parse_zero_length_draw_kept():
    # 发射器把 p[0] 重复发一次 G1 → 零长段（p0==p1）解析保留、渲染跳过
    segs = parse_lines(DRAW_JOB, z_down=17.0, z_safe=30.0, tool="pen")
    zero = segs[5]
    assert zero.kind == "DRAW" and zero.p0 == zero.p1 == (10.0, 10.0, 17.0)


def test_parse_dry_run_no_draw_plunge():
    # 空跑：down==safe，全程不落笔 → 无 DRAW/PLUNGE（§3.2）
    dry = [
        "G90", "G21", "G0 Z30 F300", "G0 X10 Y10 F1200",
        "G1 Z30 F300", "G1 X20 Y10 F1200", "G0 Z30 F300", "M400",
    ]
    segs = parse_lines(dry, z_down=30.0, z_safe=30.0, tool="pen")
    kinds = {s.kind for s in segs}
    assert "DRAW" not in kinds
    assert "PLUNGE" not in kinds
    # 走线仍可见（TRAVEL），头尾 SETUP/SYNC
    assert "TRAVEL" in kinds and "SETUP" in kinds and "SYNC" in kinds


def test_parse_modal_relative_and_comments():
    lines = [
        "; 注释行（跳过）",
        "G90",
        "G0 Z5 F300",
        "G0 X10 Y10 F1200",
        "G91",
        "G0 X5 Y0 F1200  ; 尾注释",
        "",
        "G90",
    ]
    segs = parse_lines(lines, z_down=1.0, z_safe=5.0, tool="pen")
    kinds = [s.kind for s in segs]
    assert kinds == ["SETUP", "SETUP", "TRAVEL", "UNKNOWN", "TRAVEL", "UNKNOWN"]
    rel = segs[4]  # G91 下 +5
    assert rel.p0 == (10.0, 10.0, 5.0) and rel.p1 == (15.0, 10.0, 5.0)


def test_parse_g28_loses_position():
    lines = ["G90", "G0 Z30 F300", "G0 X10 Y10 F1200", "G28 X Y", "G0 X5 Y5 F1200"]
    segs = parse_lines(lines, z_down=17.0, z_safe=30.0, tool="pen")
    home = segs[3]
    assert home.kind == "UNKNOWN"
    assert home.line_range == (3, 4)
    after = segs[4]
    assert after.p0 is None and after.p1 == (5.0, 5.0, 0.0)  # G28 后位置未知


def test_parse_rejects_unsupported_codes():
    for bad in ("G20", "G2 X0 Y0 I5", "G3 X0 Y0", "G5 X0 Y0", "G92 X0 Y0"):
        with pytest.raises(ValueError):
            parse_lines([bad], z_down=17.0, z_safe=30.0, tool="pen")


def test_parse_unknown_keeps_line_numbers():
    lines = ["M112", "M400", "G0 X1 Y1"]
    segs = parse_lines(lines, z_down=17.0, z_safe=30.0, tool="knife")
    assert [s.kind for s in segs] == ["UNKNOWN", "SYNC", "TRAVEL"]
    assert segs[0].line_range == (0, 1)


def test_classify_colors_pen_blue_knife_purple():
    segs = parse_lines(DRAW_JOB, z_down=17.0, z_safe=30.0, tool="pen")
    pen = classify(segs, "pen")
    knife = classify(segs, "knife")
    draw_idx = [i for i, s in enumerate(segs) if s.kind == "DRAW"]
    for i in draw_idx:
        assert pen[i] == COLOR_DRAW_PEN
        assert knife[i] == COLOR_DRAW_KNIFE
    for i, s in enumerate(segs):
        if s.kind == "TRAVEL":
            assert pen[i] == COLOR_TRAVEL
        elif s.kind in ("PLUNGE", "RETRACT"):
            assert pen[i] == COLOR_Z
        elif s.kind in ("SETUP", "SYNC", "UNKNOWN"):
            assert pen[i] == COLOR_NONE
    with pytest.raises(ValueError):
        classify(segs, "laser")
    with pytest.raises(ValueError):
        parse_lines([], z_down=1.0, z_safe=2.0, tool="laser")  # type: ignore[arg-type]


def test_segment_frozen_named_fields():
    s = Segment("DRAW", (0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (3, 4))
    assert s.kind == "DRAW" and s.line_range == (3, 4)
    with pytest.raises(Exception):
        s.kind = "TRAVEL"  # frozen
