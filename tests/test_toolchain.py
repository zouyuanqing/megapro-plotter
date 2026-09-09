"""M1a toolchain tests: SVG parsing, travel sort, G-code emit, preview.

No hardware required.
"""

import math
from pathlib import Path

import pytest

from megapro.preview.to_svg import toolpath_to_svg
from megapro.safety.guard import check
from megapro.toolchain.svg_to_gcode import (
    emit_gcode,
    nearest_neighbor_sort,
    parse_svg,
    svg_file_to_gcode,
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


def test_emit_allowed_codes_and_guard():
    gcode = svg_file_to_gcode(str(SQUARE))
    allowed = {"G0", "G1", "G21", "G28", "G90", "G91", "M400"}
    assert "G90" in gcode.splitlines()
    assert "G21" in gcode.splitlines()
    assert "M400" in gcode.splitlines()
    codes = [ln.split()[0].upper() for ln in gcode.splitlines() if ln.strip()]
    assert not ({c.split()[0] for c in codes} - allowed)
    assert not any(c in ("G2", "G3") for c in codes)
    for line in gcode.splitlines():
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        assert s.split()[0].upper() in allowed
        check(line, allow_z=True, lift_configured=True)


def test_emit_pen_cycle():
    gcode = emit_gcode([[(0.0, 0.0), (20.0, 0.0)]])
    assert "G0 X0 Y0" in gcode
    assert "G1 Z0 F300" in gcode
    assert "G1 X20 Y0 F1200" in gcode
    assert "G1 Z1 F300" in gcode


def test_preview_writes_svg(tmp_path):
    out = str(tmp_path / "preview.svg")
    polys = parse_svg(str(SQUARE)) + [[(50.0, 50.0), (60.0, 50.0)]]
    toolpath_to_svg(polys, out)
    text = Path(out).read_text(encoding="utf-8")
    assert text.lstrip().startswith("<svg")
    assert "<svg" in text and "</svg>" in text
    assert 'stroke="blue"' in text
    assert 'stroke="red"' in text


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
