"""M3a 文字→可画路径 SVG 单测（纯逻辑；需系统中文字体 / fontTools）。"""

import pytest

from megapro.gui.text_to_svg import (
    find_cjk_font,
    load_singleline_data,
    text_outline_svg,
    text_singleline_svg,
)

pytest.importorskip("fontTools")

FONT = find_cjk_font()


def _poly_extents(polys):
    xs = [x for p in polys for x, y in p]
    ys = [y for p in polys for x, y in p]
    return (min(xs), max(xs), min(ys), max(ys)) if xs else (0, 0, 0, 0)


def test_find_cjk_font_ok():
    assert FONT, "Windows 应有 msyh/simhei 等中文字体"


def test_outline_ascii_advance():
    svg = text_outline_svg("AA", size_mm=10.0)
    assert "<svg" in svg and "<path" in svg
    from megapro.toolchain.svg_to_gcode import parse_svg

    polys = parse_svg  # noqa - 下面用文件
    import tempfile, os
    fd, p = tempfile.mkstemp(suffix=".svg")
    os.write(fd, svg.encode("utf-8")); os.close(fd)
    try:
        pl = parse_svg(p)
        x0, x1, _, _ = _poly_extents(pl)
        # 两个 'A' 宽约 ~2 个半角 → 总宽应 ≈ (advance*2) ≈ 每个 ~0.5em*10*2 ≈ 10mm
        assert x1 > 5, f"A 两字应占 >5mm，实际 {x1}"
    finally:
        os.unlink(p)


def test_outline_chinese_mm_scale_and_y():
    # 3 个全角字 @10mm：x 总宽 ≈ 30mm；y 在合理小正值（字身向下）
    svg = text_outline_svg("写字机", size_mm=10.0)
    import tempfile, os
    from megapro.toolchain.svg_to_gcode import parse_svg
    fd, p = tempfile.mkstemp(suffix=".svg")
    os.write(fd, svg.encode("utf-8")); os.close(fd)
    try:
        pl = parse_svg(p)
        x0, x1, y0, y1 = _poly_extents(pl)
        assert x1 > 25, f"3 全角字 x 应 ~30mm，实际 {x1}"
        assert y0 >= 0, f"字形应落在 y>=0（SVG y 下），实际 y0={y0}"
        assert 5 < (y1 - y0) < 15, f"字形高度 ~10mm，实际 {y1-y0}"
    finally:
        os.unlink(p)


def test_outline_multiline():
    svg = text_outline_svg("A\nB", size_mm=10.0)
    assert svg.count("<path") >= 2  # 两行各至少一字


def test_missing_glyph_placeholder():
    # 用不含的字（如私有区）→ 应出方框 path 而不崩
    svg = text_outline_svg("\ue000", size_mm=10.0)
    assert "<path" in svg


def test_singleline_data_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_singleline_data("nonexistent.json")


def test_singleline_with_fake_data_and_fallback():
    data = {"U+0041": [[[0.0, 0.0], [1.0, 0.5]]]}  # 假 'A' 一条线
    svg = text_singleline_svg("A", data=data, size_mm=10.0)
    assert "<polyline" in svg
    assert "0,10" in svg  # (0,0)->(0,10)? 实际 y=(1-0)*10=10
    # 缺字 → 方框 path
    svg2 = text_singleline_svg("写", data=data, size_mm=10.0)
    assert "<path" in svg2 or "<polyline" in svg2  # 至少不崩（方框 path）
