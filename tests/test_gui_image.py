"""M3b 图片→线条 SVG 单测（mock potrace；若本机有 potrace 则跑真实追踪）。"""

from pathlib import Path

import pytest

pytest.importorskip("PIL")

from megapro.gui import image_to_svg as m

from megapro.gui.image_to_svg import (
    find_potrace,
    trace_image,
)


def _make_img(path: Path):
    from PIL import Image, ImageDraw

    img = Image.new("L", (400, 200), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 160, 160], fill=0)
    img.save(path)
    return path


def test_find_potrace_detects_bundled_or_none():
    # 返回 None 或路径（随附 bin/potrace.exe 存在则路径）
    found = find_potrace()
    if found:
        assert Path(found).exists()


def test_missing_potrace_raises(tmp_path):
    img = _make_img(tmp_path / "a.png")
    with pytest.raises(FileNotFoundError):
        trace_image(img, potrace="/nonexistent/potrace.exe")


def test_bitmap_polarity_and_header(tmp_path):
    # 黑方块 → PBM 里对应行含 1 位
    from PIL import Image
    img = Image.new("L", (16, 8), 255)
    from PIL import ImageDraw
    ImageDraw.Draw(img).rectangle([2, 2, 6, 6], fill=0)
    pbm = tmp_path / "t.pbm"
    m._to_bitmap(img, pbm, 160)
    data = pbm.read_bytes()
    assert data.startswith(b"P4\n16 8\n")
    # 第 2 行（y=2，黑方块行）第一字节非 0xff（有黑位）
    header_len = len(b"P4\n16 8\n")
    row2 = data[header_len + 2]
    assert row2 != 0xFF


def test_parse_potrace_svg_transform(tmp_path):
    # 模拟 potrace 输出（g transform + 相对 l 路径）→ 应得 mm polyline
    svg = ('<svg><g transform="translate(0,200) scale(0.1,-0.1)">'
           '<path d="M400 995 l0 -605 605 0 0 605 -605 0z"/></g></svg>')
    out = m._svg_polylines(svg, 0.25)
    assert "<polyline" in out
    # 首点 M400 995 → 40,100.5（potrace 坐标）→ ×0.25 = 10,25.125 mm
    assert "10,25.125" in out


def test_parse_empty_svg():
    assert "<polyline" not in m._svg_polylines("<svg/>", 0.5)


@pytest.mark.skipif(find_potrace() is None, reason="本机无 potrace")
def test_trace_image_real(tmp_path):
    img = _make_img(tmp_path / "src.png")
    svg = trace_image(img, target_mm=100.0, tmp_dir=tmp_path)
    assert svg.count("<polyline") >= 1
    # 产物可被 parse_svg 回读
    from megapro.toolchain.svg_to_gcode import parse_svg

    out = tmp_path / "o.svg"
    out.write_text(svg, encoding="utf-8")
    polys = parse_svg(str(out))
    xs = [x for p in polys for x, y in p]
    ys = [y for p in polys for x, y in p]
    assert 5 < max(xs) <= 100 and 5 < max(ys) <= 60  # 方块在 mm 范围内
