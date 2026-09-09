"""P0 GUI controller 单测（无 Qt 依赖，纯逻辑）。

覆盖：M114 坐标解析、Jog/Home/Pen/Park 序列构造、guard 兼容、状态机 gating。
"""

import pytest

from megapro.gui.controller import (
    MachineError,
    MachineState,
    Action,
    assert_can,
    build_full_home_sequence,
    build_home_sequence,
    build_jog_sequence,
    build_park_sequence,
    build_pen_down,
    build_pen_up,
    can,
    parse_position,
    sequence_ok,
    translate_paths,
)


# --- parse_position -----------------------------------------------------------

def test_parse_position_normal():
    assert parse_position("X:0.00 Y:50.00 Z:17.00 E:0.00 Count X: 0 Y:4000 Z:6800") == (0.0, 50.0, 17.0)


def test_parse_position_with_leading_echo_and_ok():
    reply = "echo:ok\nX:20.00 Y:100.00 Z:22.00 Count X: 1600\nok"
    # 取含 X: 的坐标行；容忍 E: / Count 干扰
    assert parse_position(reply) == (20.0, 100.0, 22.0)


def test_parse_position_missing_axis():
    assert parse_position("X:5 Y:6") == (5.0, 6.0, None)


def test_parse_position_empty_garbage():
    assert parse_position("") == (None, None, None)
    assert parse_position("ok") == (None, None, None)
    assert parse_position("wait") == (None, None, None)


def test_parse_position_negative():
    assert parse_position("X:-3.50 Y:0.00 Z:-1.25") == (-3.5, 0.0, -1.25)


# --- jog sequence -------------------------------------------------------------

def test_jog_xy_lifts_only_when_pen_down():
    # 笔抬起（默认）：XY 直接走，无 Z-hop
    seq = build_jog_sequence(dx=10, dy=0)
    assert seq == ["G91", "G0 X10 Y0 F600", "G90"]
    # 笔落下：XY 先抬 Z+5 再走
    seq = build_jog_sequence(dx=10, pen_down=True)
    assert seq[1] == "G0 Z5 F300"  # 安全 Z-hop
    assert "G0 X10 F600" in seq


def test_jog_z_requires_allow_z():
    with pytest.raises(MachineError):
        build_jog_sequence(dz=-1)  # 未 allow_z
    seq = build_jog_sequence(dz=-5, allow_z=True)
    assert "G0 Z-5 F600" in seq
    # 纯 Z 移动（无论笔态）不触发 XY Z-hop
    assert "G0 Z5 F300" not in seq
    seq2 = build_jog_sequence(dz=-5, allow_z=True, pen_down=True)
    assert "G0 Z5 F300" not in seq2


def test_jog_pen_state_controls_hop():
    # 笔抬起：XY 无 Z-hop
    assert build_jog_sequence(dx=10) == ["G91", "G0 X10 F600", "G90"]
    # 笔落下：XY 先抬 Z+5
    assert build_jog_sequence(dx=10, pen_down=True)[1] == "G0 Z5 F300"
    # Y 同理
    assert "G0 Z5 F300" not in build_jog_sequence(dy=5)
    assert "G0 Z5 F300" in build_jog_sequence(dy=5, pen_down=True)


def test_jog_requires_axis():
    with pytest.raises(MachineError):
        build_jog_sequence()


def test_jog_feed_override():
    seq = build_jog_sequence(dx=10, feed_xy=1000)
    assert any("X10 F1000" in l for l in seq)


# --- home / pen / park --------------------------------------------------------

def test_home_lifts_z_first():
    seq = build_home_sequence()
    assert seq[:2] == ["G91", "G0 Z10 F300"]
    assert seq[-1] == "G28 X Y"


def test_home_z_axis_requires_allow_z():
    with pytest.raises(MachineError):
        build_home_sequence("Z")
    seq = build_home_sequence("X Y Z", allow_z=True)
    assert seq[-1] == "G28 X Y Z"


def test_full_home_sequence_ends_at_safe_point():
    seq = build_full_home_sequence(safe_y=50.0, safe_z=30.0)
    # 抬 Z30 → G28 XY → G28 Z → 抬 Z30 → Y50
    assert seq[0] == "G91"
    assert seq[1] == "G0 Z30 F300"  # 先抬离纸
    assert "G28 X Y" in seq
    assert "G28 Z" in seq  # Z 也要归零
    assert seq[-2:] == ["G90", "G0 Y50 F1200"]  # 终点 Y 回安全点
    # 终点逻辑 = X0 Y50 Z30
    with pytest.raises(MachineError):
        build_full_home_sequence(safe_z=0)


def test_pen_down_up_absolute():
    pd = build_pen_down(17.0)
    assert pd == ["G90", "G0 Z17 F300"]
    pu = build_pen_up(30.0)
    assert pu == ["G90", "G0 Z30 F300"]
    with pytest.raises(MachineError):
        build_pen_up(-1.0)


def test_park_sequence():
    seq = build_park_sequence(0, 50, 30)
    assert seq == ["G90", "G0 Z30 F300", "G0 X0 Y50 F1200"]


# --- guard compatibility ------------------------------------------------------

def test_sequences_pass_guard_when_configured():
    # jog XY（含 Z-hop）无 Z 词，guard 直接过
    assert sequence_ok(build_jog_sequence(dx=10)) == []
    # pen down 到绝对 Z17（正值，自 0 向上）→ 有 allow_z 即过 guard
    seq = build_pen_down(17.0)
    assert sequence_ok(seq, allow_z=True, lift_configured=True) == []
    assert sequence_ok(seq, allow_z=True, lift_configured=False) == []
    # guard 拦的是【负】Z：绝对目标为负（或相对负移）且未配置 pen_down_z
    bad = sequence_ok(["G0 Z-1 F300"], allow_z=True, lift_configured=False)
    assert bad == ["G0 Z-1 F300"]
    assert sequence_ok(["G0 Z-1 F300"], allow_z=True, lift_configured=True) == []


# --- state machine ------------------------------------------------------------

def test_state_gating_basics():
    assert can(MachineState.DISCONNECTED, Action.CONNECT)
    assert not can(MachineState.DISCONNECTED, Action.JOG)
    assert can(MachineState.READY, Action.JOG)
    assert not can(MachineState.BUSY, Action.JOG)  # busy 禁 Jog
    assert can(MachineState.BUSY, Action.ESTOP)
    assert can(MachineState.ESTOP, Action.DISCONNECT)
    assert not can(MachineState.ESTOP, Action.CONNECT)  # 急停后只能断开


def test_assert_can_raises():
    with pytest.raises(MachineError):
        assert_can(MachineState.DISCONNECTED, Action.JOG)


# --- 工件原点平移（P1c） -----------------------------------------------------

def test_translate_paths_offsets_xy():
    paths = [[(0.0, 0.0), (20.0, 0.0)], [(5.0, 5.0)]]
    out = translate_paths(paths, dx=10.0, dy=50.0)
    assert out[0] == [(10.0, 50.0), (30.0, 50.0)]
    assert out[1] == [(15.0, 55.0)]
    # 原列表不变
    assert paths[0][0] == (0.0, 0.0)


def test_translate_paths_zero_is_copy():
    paths = [[(1.0, 2.0)]]
    out = translate_paths(paths, 0.0, 0.0)
    assert out == paths
    assert out is not paths
