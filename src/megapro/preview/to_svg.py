"""Preview: render compiled G-code segments as a standalone SVG.

阶段 5（docs/preview-layout-blueprint.md §2.2 退役表）：``toolpath_to_svg``
（polylines 入口）已退役，改为 :func:`preview_svg_from_segments` —— 预览吃
``gcode_parse.Segment``（= ``parse_lines(将发送的同一份 lines)`` 的回读，
§3.1 预览≡发送单一真源），段语义随段自带，不再从裸折线猜抬落笔。

着色：DRAW 蓝实线 / TRAVEL 红虚线 / PLUNGE·RETRACT 灰短竖标（与 GUI 预览
``canvas/gcode_items.py`` 同族；本函数**无 tool 入参、DRAW 恒蓝** —— 刀具
紫色配色属 GUI 侧 ``gcode_parse.classify(tool='knife')``/GcodePathItem，
独立 SVG 不分工具）；SETUP/SYNC/UNKNOWN 不画；零长段（``p0 == p1``）与
起点未知段（``p0 is None``）跳过。几何为机器/纸面 mm（≡ 将发送坐标）；
``work_origin``（机器坐标）只画工件原点十字标记供对准参考，**不平移几何**。
"""

from __future__ import annotations

from megapro.gui.canvas.coords import BED_H, BED_W

__all__ = ["preview_svg_from_segments"]


def _fmt(v: float) -> str:
    s = f"{float(v):.3f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "", "-") else s


def preview_svg_from_segments(segments, *, work_origin) -> str:
    """把 ``gcode_parse.Segment`` 序列渲成独立 SVG 文本（浏览器可看）。

    ``work_origin`` = 工件原点的机器坐标 ``(x, y)``（**必填** keyword-only，
    不传即 TypeError）；传 ``(0, 0)`` 时十字标记落在床左下（与纸面原点
    重合）。
    """
    ox, oy = float(work_origin[0]), float(work_origin[1])
    draw: list = []
    travel: list = []
    z_marks: list = []
    pts: list = [(0.0, 0.0), (float(BED_W), float(BED_H)), (ox, oy)]
    for s in segments:
        if s.p0 is None:
            continue  # 起点未知不成线
        p0 = (float(s.p0[0]), float(s.p0[1]))
        p1 = (float(s.p1[0]), float(s.p1[1]))
        if s.kind == "DRAW":
            if p0 != p1:
                draw.append((p0, p1))
                pts += [p0, p1]
        elif s.kind == "TRAVEL":
            if p0 != p1:
                travel.append((p0, p1))
                pts += [p0, p1]
        elif s.kind in ("PLUNGE", "RETRACT"):
            z_marks.append(p1)
            pts.append(p1)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
    sw = max(maxx - minx, maxy - miny, 1.0) / 400.0
    parts = [
        f'<rect x="0" y="0" width="{_fmt(BED_W)}" height="{_fmt(BED_H)}"'
        ' fill="white" stroke="black" stroke-width="' + _fmt(sw) + '"/>'
    ]
    for p0, p1 in travel:
        parts.append(f'<line x1="{_fmt(p0[0])}" y1="{_fmt(p0[1])}"'
                     f' x2="{_fmt(p1[0])}" y2="{_fmt(p1[1])}"'
                     ' stroke="red" stroke-width="' + _fmt(sw) + '" stroke-dasharray="'
                     + _fmt(4 * sw) + " " + _fmt(3 * sw) + '"/>')
    for p0, p1 in draw:
        parts.append(f'<line x1="{_fmt(p0[0])}" y1="{_fmt(p0[1])}"'
                     f' x2="{_fmt(p1[0])}" y2="{_fmt(p1[1])}"'
                     ' stroke="blue" stroke-width="' + _fmt(sw) + '"/>')
    for x, y in z_marks:
        parts.append(f'<line x1="{_fmt(x)}" y1="{_fmt(y - 1.5)}"'
                     f' x2="{_fmt(x)}" y2="{_fmt(y + 1.5)}"'
                     ' stroke="gray" stroke-width="' + _fmt(sw) + '"/>')
    # 工件原点十字（对准参考，不参与几何）
    cr = 4.0
    parts.append(f'<line x1="{_fmt(ox - cr)}" y1="{_fmt(oy)}"'
                 f' x2="{_fmt(ox + cr)}" y2="{_fmt(oy)}"'
                 ' stroke="#c00" stroke-width="' + _fmt(sw) + '"/>')
    parts.append(f'<line x1="{_fmt(ox)}" y1="{_fmt(oy - cr)}"'
                 f' x2="{_fmt(ox)}" y2="{_fmt(oy + cr)}"'
                 ' stroke="#c00" stroke-width="' + _fmt(sw) + '"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="{_fmt(minx)} {_fmt(miny)} {_fmt(maxx - minx)} {_fmt(maxy - miny)}">\n'
            + "\n".join(parts) + "\n</svg>\n")
