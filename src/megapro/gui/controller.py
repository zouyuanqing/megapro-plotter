"""MachineController —— 纯逻辑层（无 Qt 依赖，可单测）。

职责：
- 机器状态机（MachineState）与操作 gating（can()）。
- G-code 命令序列构造（jog/home/pen/归位/安全点停靠），语义对齐 CLI：
    jog   G91 →[XY 且非 draw：先抬 Z+5]→ G0 <axes> F600 → G90
    home  先抬 Z+10 → G28 <axes>   （Z 归位需 allow_z）
    pen   PenDown = 绝对 Z pen_down_z；PenUp = 绝对 Z safe_z（慢速）
    停靠  抬 Z 到 safe_z → 回安全点 X safe_x / Y safe_y
- 所有待发指令经 guard.check 校验（由调用方逐条调用，这里提供
  sequence_ok() 便于在发前整体预检）。

坐标解析（parse_position）与 gating 规则与 CLI/guard 完全一致；
关于「动作完成」的可信度见 REPORT.md §6（人眼 > 逻辑坐标 > Count），
本层不把 M114 的 Count 当判据。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

__all__ = [
    "MachineState",
    "Action",
    "MachineError",
    "parse_position",
    "build_jog_sequence",
    "build_home_sequence",
    "build_full_home_sequence",
    "build_pen_down",
    "build_pen_up",
    "build_park_sequence",
    "sequence_ok",
    "translate_paths",
]

# 与 CLI 常量保持一致（仅作默认；实际由 profile/调用方决定）
_DEFAULT_FEED_XY = 600.0
_DEFAULT_FEED_Z = 300.0
_SAFE_HOP_MM = 5.0
_HOME_LIFT_MM = 10.0


class MachineState(Enum):
    DISCONNECTED = auto()  # 未连接
    CONNECTING = auto()  # 连接中：dtr/settle/启动引导
    READY = auto()  # 已连接并归位完成，可 Jog/发命令
    BUSY = auto()  # 执行中（归位/长移动/序列）
    FAULT = auto()  # 链路丢失/固件错误，需处理
    ESTOP = auto()  # 已急停（M112），需断电重启


class Action(Enum):
    CONNECT = auto()
    DISCONNECT = auto()
    JOG = auto()
    HOME = auto()
    PEN_DOWN = auto()
    PEN_UP = auto()
    SEND_LINE = auto()
    ESTOP = auto()
    ABORT = auto()
    POLL = auto()


class MachineError(Exception):
    """不合法操作（未连接/状态不允许/参数错）。"""


# 状态转移表：state -> (allowed_actions)
_GATES: dict[MachineState, set[Action]] = {
    MachineState.DISCONNECTED: {Action.CONNECT},
    MachineState.CONNECTING: {Action.DISCONNECT, Action.ESTOP},
    MachineState.READY: {
        Action.DISCONNECT, Action.JOG, Action.HOME, Action.PEN_DOWN,
        Action.PEN_UP, Action.SEND_LINE, Action.ESTOP, Action.ABORT,
        Action.POLL,
    },
    MachineState.BUSY: {
        Action.DISCONNECT, Action.ESTOP, Action.ABORT, Action.POLL,
    },
    MachineState.FAULT: {Action.DISCONNECT, Action.ESTOP},
    MachineState.ESTOP: {Action.DISCONNECT},  # 只能断开，提示断电重启
}


def can(state: MachineState, action: Action) -> bool:
    return action in _GATES[state]


def assert_can(state: MachineState, action: Action) -> None:
    if not can(state, action):
        raise MachineError(f"当前状态 {state.name} 不允许 {action.name}")


def _fmt(v: float) -> str:
    return f"{v:g}"


def _fmt3(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def parse_position(reply: str) -> tuple[float | None, float | None, float | None]:
    """从 M114 回显解析逻辑坐标。

    接受形如 ``X:0.00 Y:50.00 Z:17.00 E:0.00 Count X: 0 ...`` 的行，
    容忍缺轴/多余 token。解析的是**逻辑坐标**（固件计划位置），
    不是物理真值（见 REPORT §6）。解析失败返回 (None, None, None)。
    """
    if not reply:
        return (None, None, None)
    # 取第一个含 X: 的坐标行；整段可能多行。
    text = reply
    if "\n" in text:
        for line in text.splitlines():
            if "X:" in line and ("Y:" in line or "Z:" in line):
                text = line
                break
    vals = {"X": None, "Y": None, "Z": None}
    # Count X: 0 会干扰，先切掉 Count 段。
    head = text.split(" Count", 1)[0]
    for token in head.split():
        for axis in ("X", "Y", "Z"):
            if token.startswith(f"{axis}:") and token[2:]:
                try:
                    vals[axis] = float(token[2:])
                except ValueError:
                    pass
    return (vals["X"], vals["Y"], vals["Z"])


def build_jog_sequence(
    dx: float | None = None,
    dy: float | None = None,
    dz: float | None = None,
    *,
    allow_z: bool = False,
    pen_down: bool = False,
    feed_xy: float = _DEFAULT_FEED_XY,
    feed_z: float = _DEFAULT_FEED_Z,
) -> list[str]:
    """构造一次相对 Jog 的完整指令序列（含模式切换与安全 Z-hop）。

    Z 移动需 allow_z。XY 平移时：仅当笔当前处于「落下」（pen_down=True，
    刚画完一段）才先抬 Z+5 再走，避免拖笔；笔抬起（普通 Jog/对刀）直接走 XY，
    不产生额外 Z 动作。与 CLI jog 的固定 Z-hop 不同——GUI 按笔态决定，
    更利于对刀/微调（用户决策 2026-09-07）。
    """
    if dx is None and dy is None and dz is None:
        raise MachineError("jog 至少需要一个轴")
    if dz is not None and not allow_z:
        raise MachineError("jog 含 Z 需先开启『允许 Z』")
    parts: list[str] = []
    if dx is not None:
        parts.append(f"X{_fmt(dx)}")
    if dy is not None:
        parts.append(f"Y{_fmt(dy)}")
    if dz is not None:
        parts.append(f"Z{_fmt(dz)}")
    move = f"G0 {' '.join(parts)} F{_fmt(feed_xy)}"
    lines = ["G91"]
    if (dx is not None or dy is not None) and pen_down:
        lines.append(f"G0 Z{_fmt(_SAFE_HOP_MM)} F{_fmt(feed_z)}")
    lines.append(move)
    lines.append("G90")
    return lines


def build_home_sequence(axes: str = "X Y", *, allow_z: bool = False) -> list[str]:
    """构造归位序列：先抬 Z+10（防拖笔），再 G28。

    G28 含 Z 需 allow_z（与 CLI/guard 一致）。
    """
    axis_set = axes.upper().split()
    if not axis_set:
        raise MachineError("home 至少需要一个轴")
    if "Z" in axis_set and not allow_z:
        raise MachineError("归位 Z 需先开启『允许 Z』")
    lines = ["G91", f"G0 Z{_fmt(_HOME_LIFT_MM)} F300", "G90", f"G28 {' '.join(axis_set)}"]
    return lines


def build_full_home_sequence(
    *,
    safe_y: float = 50.0,
    safe_z: float = 30.0,
    feed_xy: float = 1200.0,
    feed_z: float = _DEFAULT_FEED_Z,
) -> list[str]:
    """一键寻零并回到安全点：抬 Z+safe_z → G28 XY → G28 Z → 抬 Z+safe_z → Y 回 safe_y。

    终点 = (X0, safe_y, safe_z)，即 profile 的安全点（X0 Y50 Z30）。
    各步：先抬 Z 防 XY 归位拖笔；G28 XY；G28 Z（工具降到下限位/触床）；
    再从 Z0 抬起到 safe_z；最后 Y 到 safe_y（X 已在 0）。
    G28 Z 会使笔/刀短暂触床 —— 需能承受该位。
    """
    if safe_z <= 0:
        raise MachineError("safe_z 需 > 0")
    return [
        "G91",
        f"G0 Z{_fmt3(safe_z)} F{_fmt(feed_z)}",  # 抬离纸（防 XY 归位拖笔）
        "G90",
        "G28 X Y",
        "G28 Z",
        "G91",
        f"G0 Z{_fmt3(safe_z)} F{_fmt(feed_z)}",  # 从 Z0 抬起到安全高度
        "G90",
        f"G0 Y{_fmt3(safe_y)} F{_fmt(feed_xy)}",  # X 已 0，Y 回 safe_y
    ]


def build_pen_down(pen_down_z: float, *, feed_z: float = _DEFAULT_FEED_Z) -> list[str]:
    """落笔：绝对 Z 到 pen_down_z（profile 标定的触纸高度）。"""
    return ["G90", f"G0 Z{_fmt3(pen_down_z)} F{_fmt(feed_z)}"]


def build_pen_up(
    safe_z: float, *, allow_z: bool = False, feed_z: float = _DEFAULT_FEED_Z
) -> list[str]:
    """抬笔到安全高度 safe_z（绝对）。safe_z 需 >0 且非负。"""
    if safe_z < 0:
        raise MachineError("safe_z 不能为负")
    return ["G90", f"G0 Z{_fmt3(safe_z)} F{_fmt(feed_z)}"]


def build_park_sequence(
    safe_x: float, safe_y: float, safe_z: float, *, feed_xy: float = 1200.0,
    feed_z: float = _DEFAULT_FEED_Z,
) -> list[str]:
    """常规停靠：抬 Z 到 safe_z → 回安全点 (safe_x, safe_y)。

    供『中止/完成』使用；绝对模式，Z 高于 0 不触发 guard 负 Z 规则。
    """
    return [
        "G90",
        f"G0 Z{_fmt3(safe_z)} F{_fmt(feed_z)}",
        f"G0 X{_fmt3(safe_x)} Y{_fmt3(safe_y)} F{_fmt(feed_xy)}",
    ]


def sequence_ok(lines: list[str], *, allow_z: bool = False,
                lift_configured: bool = False) -> list[str]:
    """返回序列中会被 guard 拦下的行（空 = 全部通过）。不修改原序列。"""
    from megapro.safety.guard import check, GuardReject

    bad: list[str] = []
    for line in lines:
        try:
            check(line, allow_z=allow_z, lift_configured=lift_configured)
        except GuardReject:
            bad.append(line)
    return bad


def translate_paths(
    paths: list[list[tuple[float, float]]],
    dx: float,
    dy: float,
) -> list[list[tuple[float, float]]]:
    """把多段线整体平移 (dx, dy)。用于把工件坐标路径平移到机器坐标。

    P1c 工件原点采用 GUI 维护偏移（不发 G92 给固件）：SVG 路径是工件坐标，
    发机器前加偏移 = 机器坐标。返回新列表，不改入参。
    """
    if dx == 0.0 and dy == 0.0:
        return [list(p) for p in paths]
    return [
        [(x + dx, y + dy) for x, y in p]
        for p in paths
    ]
