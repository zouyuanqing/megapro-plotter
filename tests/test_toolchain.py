"""M1a toolchain tests: SVG parsing, travel sort, meta declarations, preview.

No hardware required.（阶段 5：emit_gcode/svg_file_to_gcode 已退役，其
test_emit_* 用例随之删除；预览入口改 preview_svg_from_segments 喂
gcode_parse.Segment。）
"""

import math
from pathlib import Path

import pytest

from megapro.toolchain.svg_to_gcode import (
    nearest_neighbor_sort,
    parse_svg,
    parse_svg_meta,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SQUARE = EXAMPLES / "square_20mm.svg"


def _write_svg(tmp_path, body):
    p = tmp_path / "case.svg"
    p.write_text('<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg">\n'
                 + body + "\n</svg>\n", encoding="utf-8")
    return str(p)


def _travel(polys):
    total, cur = 0.0, (0.0, 0.0)
    for p in polys:
        total += math.hypot(p[0][0] - cur[0], p[0][1] - cur[1])
        cur = p[-1]
    return total


def test_circle_within_tolerance(tmp_path):
    path = _write_svg(tmp_path, '<circle cx="50" cy="50" r="10"/>')
    polys = parse_svg(path)
    assert len(polys) == 1
    for x, y in polys[0]:
        assert abs(math.hypot(x - 50, y - 50) - 10.0) <= 0.1


def test_square_closed(tmp_path):
    polys = parse_svg(str(SQUARE))
    assert len(polys) == 1
    sq = polys[0]
    assert len(sq) == 5
    assert math.hypot(sq[0][0] - sq[-1][0], sq[0][1] - sq[-1][1]) <= 1e-6
    xs = [x for x, _ in sq]
    ys = [y for _, y in sq]
    assert max(xs) - min(xs) == 20.0
    assert max(ys) - min(ys) == 20.0


def test_nn_sort_reduces_travel():
    far = [(100.0, 0.0), (101.0, 0.0)]
    near = [(0.0, 1.0), (1.0, 1.0)]
    far2 = [(100.0, 5.0), (101.0, 5.0)]
    given = [far, near, far2]
    ordered = nearest_neighbor_sort(given)
    assert _travel(ordered) < _travel(given)
    key = lambda p: repr(sorted((p[0], p[-1])))
    assert sorted(map(key, ordered)) == sorted(map(key, given))
    assert ordered[0][0] == (0.0, 1.0)


def test_preview_writes_svg(tmp_path):
    """预览 SVG 从 gcode_parse.Segment 渲出（阶段 5：改喂 segments）。

    覆盖三色语义：DRAW 蓝实线、TRAVEL 红虚线（起点已知的跨段空移）、
    PLUNGE/RETRACT 灰竖标，另含工件原点十字（work_origin）。
    """
    from megapro.gui.gcode_parse import parse_lines
    from megapro.preview.to_svg import preview_svg_from_segments

    lines = [
        "G90", "G21", "G0 Z30 F300",
        "G0 X10 Y10 F1200", "G1 Z17 F300", "G1 X10 Y10 F1200",
        "G1 X20 Y10 F1200", "G0 Z22 F300",
        "G0 X40 Y40 F1200", "G1 Z17 F300", "G1 X50 Y40 F1200",
        "G0 Z22 F300", "M400",
    ]
    segs = parse_lines(lines, z_down=17.0, z_safe=30.0, tool="pen")
    svg = preview_svg_from_segments(segs, work_origin=(0.0, 0.0))
    out = tmp_path / "preview.svg"
    out.write_text(svg, encoding="utf-8")
    text = out.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<svg")
    assert "<svg" in text and "</svg>" in text
    assert 'stroke="blue"' in text  # DRAW 落笔
    assert 'stroke="red"' in text   # TRAVEL 空移（首段前 p0 未知的不画）
    assert 'stroke="gray"' in text  # PLUNGE/RETRACT 竖标
    assert 'stroke="#c00"' in text  # 工件原点十字（对准参考）


def test_rotate_skew_transforms(tmp_path):
    path = _write_svg(tmp_path, '<g transform="rotate(90)"><line x1="1" y1="0" x2="2" y2="0"/></g>')
    x0, y0 = parse_svg(path)[0][0]
    assert (x0, y0) == pytest.approx((0.0, 1.0))
    path = _write_svg(tmp_path, '<g transform="skewX(45)"><line x1="1" y1="1" x2="2" y2="1"/></g>')
    x0, y0 = parse_svg(path)[0][0]
    assert (x0, y0) == pytest.approx((2.0, 1.0))


def test_unsupported_transform_raises(tmp_path):
    path = _write_svg(tmp_path, '<g transform="perspective(10)"><line x1="0" y1="0" x2="1" y2="1"/></g>')
    with pytest.raises(ValueError):
        parse_svg(path)


# --- parse_svg_meta（阶段 3：Placement 判定用的尺寸声明，只读） ----------------


def _write_root_svg(tmp_path, attrs, body=""):
    p = tmp_path / "meta.svg"
    p.write_text(
        '<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" '
        + attrs + ">\n" + body + "\n</svg>\n", encoding="utf-8")
    return str(p)


def test_parse_svg_meta_declares_bed_size(tmp_path):
    """声明 210×210（导出格式）→ width/height/viewBox 全解析。"""
    path = _write_root_svg(
        tmp_path, 'width="210mm" height="210mm" viewBox="0 0 210 210"',
        '<polyline points="0,210 10,210" fill="none"/>')
    meta = parse_svg_meta(path)
    assert meta.width_mm == pytest.approx(210.0)
    assert meta.height_mm == pytest.approx(210.0)
    assert meta.viewBox == (0.0, 0.0, 210.0, 210.0)
    # 声明 210×210 → Placement preserve 的判据成立
    assert (meta.width_mm, meta.height_mm) == (210.0, 210.0)


def test_parse_svg_meta_missing_and_units(tmp_path):
    """缺失属性 → None；单位不做换算（数值原样，mm/无单位视为 mm）。"""
    meta = parse_svg_meta(_write_root_svg(tmp_path, ""))
    assert meta.width_mm is None
    assert meta.height_mm is None
    assert meta.viewBox is None
    meta2 = parse_svg_meta(_write_root_svg(
        tmp_path, 'width="20" height="20mm" viewBox="0 0 20 20"'))
    assert meta2.width_mm == pytest.approx(20.0)
    assert meta2.height_mm == pytest.approx(20.0)
    assert meta2.viewBox == (0.0, 0.0, 20.0, 20.0)
    # 小画板（square_20mm 一类）不声明 210×210 → 默认 anchor 归位判据成立
    assert (meta2.width_mm, meta2.height_mm) != (210.0, 210.0)
    meta3 = parse_svg_meta(str(SQUARE))
    assert meta3.width_mm is None or abs(meta3.width_mm - 210.0) > 1e-6
