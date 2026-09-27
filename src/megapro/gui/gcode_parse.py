"""将发送 G-code 文本 → 段列表（纯逻辑，零 Qt）。

预览≡实际机制（蓝图 §3.1）：预览渲染器吃 ``parse_lines(将发送的同一份
lines)``，发送器吃同一份 ``lines`` —— 生成器 bug 会原样出现在预览并被
golden 拦下。依赖方向：job → gcode_parse 单向；本模块不 import job。

段分类（``parse_lines(..., z_down, z_safe, tool)``，§3.1 段分类契约）：
- SETUP   首段前的 ``G0 Z{z_safe}``（及 G90/G21）
- TRAVEL  G0 含 X/Y；以及「未落笔」的 G1 含 X/Y（如空跑 dry_run 全程）
- DRAW    G1 含 X/Y 且 |z−z_down| ≤ 1e-6 且存在真实落笔档（z_down < z_safe）。
  注：§3.2 契约要求空跑（down==safe）解析后「无 DRAW/PLUNGE」，故在
  DRAW/PLUNGE 判据上补「z_down < z_safe」这一「存在落笔档」条件；
  空跑的 G1 走线按 TRAVEL 显示（笔未落下）。
- PLUNGE  纯 Z 的 G1 下行到 z_down
- RETRACT 段间/收尾的纯 Z 上行 G0
- SYNC    M400
- UNKNOWN 其他（M112 等）；G28 记 HOME 并置后续位置未知

模态机：G90/G91、G21（G20 → ValueError）；G2/G3/G5/G92 → ValueError。
起点含未定轴 → ``p0=None``（渲染端只画 p0 已知的段，起点未知不成线）。
**未定轴 0.0 占位只发生在运动段的 p1**（未指定且仍未知的轴填 0.0，如
G28 之后的 TRAVEL，p1 的 z 是占位值）：p1 含占位 ⟹ p0 为 None，反之
不然（该行可一次补齐全部未知轴，p1 全为真值——如 G28 后先 ``G0 Z30`` 再
``G0 X10 Y10``，p0=None 而 p1=(10,10,30) 无占位）。非运动段
（SETUP/SYNC/UNKNOWN）：起点已知时
p1 = p0；起点未知时 p1 记 ``(0.0, 0.0, 0.0)`` 全零元组（已知轴也归零，
不是逐轴占位语义）；G28 段恒记全零元组（占位，**不代表机头在原点**）。
占位一律不是真实坐标，消费端勿当真值。
零长段（``p0 == p1``，如发射器把 p[0] 重复发一次 G1）解析保留、渲染跳过、
roundtrip 比对时过滤。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence

__all__ = [
    "Kind",
    "Tool",
    "Segment",
    "parse_lines",
    "classify",
    "COLOR_NONE",
    "COLOR_TRAVEL",
    "COLOR_DRAW_PEN",
    "COLOR_DRAW_KNIFE",
    "COLOR_Z",
]

Kind = Literal["SETUP", "TRAVEL", "PLUNGE", "DRAW", "RETRACT", "SYNC", "UNKNOWN"]
Tool = Literal["pen", "knife"]

#: 配色 token（画布侧映射到具体 QPen）。
COLOR_NONE = "none"  # SETUP/SYNC/UNKNOWN：不画
COLOR_TRAVEL = "red"  # 空行程：红虚线
COLOR_DRAW_PEN = "blue"  # 写字落笔：蓝实线
COLOR_DRAW_KNIFE = "purple"  # 裁刀切削：紫实线
COLOR_Z = "gray"  # PLUNGE/RETRACT：灰短竖标

Point3 = tuple[float, float, float]
_EPS_Z = 1e-6
_WORD = re.compile(r"([A-Za-z])([+-]?\d*\.?\d*)")
_FORBIDDEN_G = {
    2.0: "G2 圆弧",
    3.0: "G3 圆弧",
    5.0: "G5（激光/样条，guard 亦拦）",
    92.0: "G92（工件原点是 GUI 侧纯平移，不发固件）",
    20.0: "G20 英制（只支持公制 G21）",
}


@dataclass(frozen=True)
class Segment:
    """一段语义/运动。

    ``line_range`` 为 0 基半开 ``[i0, i1)`` 行号（对 ``parse_lines`` 入参
    下标）；worker 的 ``done`` 是 1 基已发送行数，段全亮 iff
    ``line_range[1] <= done``。``p0=None`` 表示起点含未定轴（不成线）；
    未定轴 0.0 占位只发生在**运动段的 p1**（p1 含占位 ⟹ p0 为 None，反之
    不然）；非运动段起点未知时 p1 记全零元组、G28 段恒记全零元组——占位轴
    都不是真实坐标。
    """

    kind: Kind
    p0: Point3 | None
    p1: Point3
    line_range: tuple[int, int]


def parse_lines(
    lines: Sequence[str],
    *,
    z_down: float,
    z_safe: float,
    tool: Tool,
) -> list[Segment]:
    """把将发送的 G-code 文本解析成 :class:`Segment` 列表。

    ``z_down``/``z_safe`` 是 Z 参照（来自 ZMap）；``tool`` 决定配色约定
    （笔蓝/刀紫，由 :func:`classify` 消费），判据本身只用 Z 参照。
    空行/纯注释行跳过（不下段）；其余行行号保留。
    """
    if tool not in ("pen", "knife"):
        raise ValueError(f"未知工具: {tool!r}（可用: 'pen', 'knife')")
    engaged = (z_safe - z_down) > _EPS_Z  # 存在真实落笔/下压档（空跑时 False）
    segments: list[Segment] = []
    pos: list[float | None] = [None, None, None]  # 逐轴 None = 未知
    absolute = True
    seen_motion = False

    for i, raw in enumerate(lines):
        code = raw.split(";", 1)[0].strip()
        if not code:
            continue
        words = [(m.group(1).upper(), m.group(2)) for m in _WORD.finditer(code)]
        if not words:
            continue
        g_vals: list[float] = []
        m_vals: list[float] = []
        axes: dict[str, str] = {}
        for letter, num in words:
            if letter == "G":
                if num == "":
                    raise ValueError(f"第{i}行 G 词无数值: {code!r}")
                g_vals.append(float(num))
            elif letter == "M":
                if num == "":
                    raise ValueError(f"第{i}行 M 词无数值: {code!r}")
                m_vals.append(float(num))
            elif letter in ("X", "Y", "Z"):
                axes[letter] = num
        for gv in g_vals:
            if gv in _FORBIDDEN_G:
                raise ValueError(f"第{i}行不支持 {_FORBIDDEN_G[gv]}: {code!r}")

        if 90.0 in g_vals:
            absolute = True
        if 91.0 in g_vals:
            absolute = False

        p0 = _point(pos)
        motion = "G0" if 0.0 in g_vals else ("G1" if 1.0 in g_vals else None)

        if 28.0 in g_vals:
            # HOME：位置归未知（ok≠已移动；Count 不可信，见 REPORT §6）
            segments.append(Segment("UNKNOWN", p0, (0.0, 0.0, 0.0), (i, i + 1)))
            pos[:] = [None, None, None]
            seen_motion = True
            continue

        if motion is not None and axes:
            new = list(pos)
            for letter, num in axes.items():
                idx = "XYZ".index(letter)
                if num == "":
                    raise ValueError(f"第{i}行轴词无数值: {code!r}")
                val = float(num)
                if absolute:
                    new[idx] = val
                else:
                    if pos[idx] is None:
                        raise ValueError(f"第{i}行相对模式起点未知: {code!r}")
                    new[idx] = pos[idx] + val
            p1: Point3 = tuple(v if v is not None else 0.0 for v in new)  # type: ignore[assignment]
            has_xy = ("X" in axes) or ("Y" in axes)
            kind: Kind
            if has_xy:
                if motion == "G0":
                    kind = "TRAVEL"
                else:
                    z = new[2]
                    if engaged and z is not None and abs(z - z_down) <= _EPS_Z:
                        kind = "DRAW"
                    else:
                        kind = "TRAVEL"  # 空跑/未落笔的 G1 走线
            else:
                z = new[2]
                z0 = pos[2]
                if motion == "G1":
                    down_ok = engaged and z is not None and abs(z - z_down) <= _EPS_Z
                    if down_ok and (z0 is None or z < z0):
                        kind = "PLUNGE"
                    else:
                        kind = "UNKNOWN"
                else:
                    if (not seen_motion) and z is not None and abs(z - z_safe) <= _EPS_Z:
                        kind = "SETUP"
                    elif z0 is not None and z is not None and z > z0:
                        kind = "RETRACT"
                    else:
                        kind = "UNKNOWN"
            segments.append(Segment(kind, p0, p1, (i, i + 1)))
            pos[:] = new
            if kind in ("TRAVEL", "DRAW", "PLUNGE", "RETRACT"):
                seen_motion = True
            continue

        if 400.0 in m_vals:
            kind = "SYNC"
        elif m_vals:
            kind = "UNKNOWN"  # M112 等：保留行号
        elif g_vals and 91.0 not in g_vals and set(g_vals) <= {90.0, 21.0} \
                and not seen_motion:
            kind = "SETUP"  # G90/G21 头
        else:
            kind = "UNKNOWN"
        p1 = p0 if p0 is not None else (0.0, 0.0, 0.0)
        segments.append(Segment(kind, p0, p1, (i, i + 1)))

    return segments


def classify(segments: Sequence[Segment], tool: Tool) -> list[str]:
    """供配色（笔蓝/刀紫）：按段顺序返回颜色 token（见 COLOR_* 常量）。

    DRAW 的颜色由 ``tool`` 决定（pen→蓝、knife→紫）；TRAVEL 红、
    PLUNGE/RETRACT 灰、SETUP/SYNC/UNKNOWN 不画。
    """
    if tool not in ("pen", "knife"):
        raise ValueError(f"未知工具: {tool!r}（可用: 'pen', 'knife')")
    draw = COLOR_DRAW_PEN if tool == "pen" else COLOR_DRAW_KNIFE
    table = {
        "SETUP": COLOR_NONE,
        "TRAVEL": COLOR_TRAVEL,
        "PLUNGE": COLOR_Z,
        "DRAW": draw,
        "RETRACT": COLOR_Z,
        "SYNC": COLOR_NONE,
        "UNKNOWN": COLOR_NONE,
    }
    return [table[s.kind] for s in segments]


def _point(vals: Sequence[float | None]) -> Point3 | None:
    """逐轴位置 → (x, y, z)；任一轴未知返回 None（起点未知）。"""
    if any(v is None for v in vals):
        return None
    return (vals[0], vals[1], vals[2])  # type: ignore[return-value]
