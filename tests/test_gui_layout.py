"""M3c 排版模型/导出 单测（纯逻辑，无 Qt）。"""

import math

import pytest

from megapro.gui.layout.export_svg import document_to_svg
from megapro.gui.layout.model import BED_W, BED_H, Document, Item


def _line(x0, y0, x1, y1):
    return Item(paths=[[(x0, y0), (x1, y1)]], name="line")


def test_item_bbox_local():
    it = _line(0, 0, 20, 5)
    assert it.bbox() == (0, 0, 20, 5)


def test_item_transformed_translate():
    it = _line(0, 0, 10, 0)
    it.pos = (50, 60)
    out = it.transformed_paths()
    assert out[0][0] == (50, 60)
    assert out[0][1] == (60, 60)


def test_item_transform_scale_and_rotate():
    it = _line(0, 0, 10, 0)
    it.pos = (0, 0)
    it.scale = 2.0
    it.angle_deg = 90.0
    out = it.transformed_paths()
    # (10,0) 缩放→(20,0) 旋转90°→(0,20)
    x, y = out[0][1]
    assert math.isclose(x, 0, abs_tol=1e-6)
    assert math.isclose(y, 20, abs_tol=1e-6)


def test_document_export_parseable(tmp_path):
    """导出 → parse_svg 回读 → paper_from_svg_ydown 对合还原纸面坐标（阶段 3）。

    导出为 SVG y-down（y_svg = 210 − y_paper），回读经 ``paper_from_svg_ydown``
    比对纸面值（第一条线 (10,40)-(30,40)；文件字面 y 为 170）。
    """
    from megapro.gui.canvas.coords import paper_from_svg_ydown
    from megapro.toolchain.svg_to_gcode import parse_svg

    doc = Document()
    it = _line(0, 0, 20, 0)
    it.pos = (10, 40)
    doc.add(it)
    doc.add(_line(0, 0, 5, 5))  # 原点线
    svg = document_to_svg(doc)
    p = tmp_path / "doc.svg"
    p.write_text(svg, encoding="utf-8")
    polys = parse_svg(str(p))
    assert len(polys) == 2
    # 第一条线应在 (10,40)-(30,40)（纸面值；对合回读比对）
    a, b = paper_from_svg_ydown(polys)[0]
    assert a == pytest.approx((10.0, 40.0))
    assert b == pytest.approx((30.0, 40.0))


def test_document_export_no_text_no_style():
    doc = Document()
    doc.add(_line(0, 0, 1, 1))
    svg = document_to_svg(doc)
    assert "<text" not in svg
    assert "<image" not in svg
    assert "stroke" not in svg or "fill=\"none\"" in svg


def test_bed_constants():
    assert (BED_W, BED_H) == (210.0, 210.0)


def test_item_new_fields_defaults():
    it = Item(paths=[[(0, 0), (1, 1)]])
    assert it.z == 0.0
    assert it.locked is False
    assert it.visible is True
    assert it.text_spec is None


def test_document_remove_and_sorted():
    doc = Document()
    a = Item(paths=[[(0, 0), (1, 0)]], name="a", z=5)
    b = Item(paths=[[(0, 0), (2, 0)]], name="b", z=1)
    doc.add(a)
    doc.add(b)
    # sorted_items 按 z 升序（b z=1 在前）
    assert [it.name for it in doc.sorted_items()] == ["b", "a"]
    assert doc.top_z() == 5 and doc.bottom_z() == 1
    doc.remove(a)
    assert len(doc.items) == 1
    doc.remove(a)  # 再删不崩
    assert len(doc.items) == 1


def test_document_items_visible_skips_hidden():
    doc = Document()
    doc.add(Item(paths=[[(0, 0), (1, 0)]], name="vis", z=1))
    doc.add(Item(paths=[[(0, 0), (1, 0)]], name="hid", z=2, visible=False))
    assert [it.name for it in doc.items_visible()] == ["vis"]


def test_export_skips_hidden_and_respects_z_order():
    doc = Document()
    doc.add(Item(paths=[[(0, 0), (10, 0)]], name="top", z=9))
    doc.add(Item(paths=[[(0, 5), (10, 5)]], name="hid", z=5, visible=False))
    doc.add(Item(paths=[[(0, 1), (10, 1)]], name="bot", z=1))
    svg = document_to_svg(doc)
    # bot(z1) 在 top(z9) 之前；hidden 不出现（阶段 3：y-down 字面 209/210/205）
    assert svg.index("0,209") < svg.index("0,210")
    assert "0,205" not in svg


def test_export_empty_doc():
    assert "<svg" in document_to_svg(Document())


def test_export_closes_contour():
    # 闭合方块 → 导出应含回到起点的点（阶段 3：y-down 字面 0,210）
    it = Item(paths=[[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]])
    doc = Document()
    doc.add(it)
    svg = document_to_svg(doc)
    assert svg.count("0,210") >= 2  # 起点出现两次（闭合回到）


def test_export_svg_ydown_roundtrip(tmp_path):
    """§8.2 具名 golden：导出 y-down ↔ 纸面 y-up 对合回读（roundtrip 恒等）。

    document_to_svg → parse_svg → paper_from_svg_ydown 应逐点还原
    flatten_visible(doc)（含 z 序拍平与变换后页面坐标）。
    """
    from megapro.gui.canvas.coords import paper_from_svg_ydown
    from megapro.gui.layout.model import flatten_visible
    from megapro.toolchain.svg_to_gcode import parse_svg

    doc = Document()
    doc.add(Item(paths=[[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]], name="a", z=1))
    doc.add(Item(paths=[[(5.0, 15.0), (20.0, 15.0)]], name="b", z=2,
                 pos=(3.0, 4.0), scale=2.0, angle_deg=90.0))
    svg = document_to_svg(doc)
    p = tmp_path / "rt.svg"
    p.write_text(svg, encoding="utf-8")
    back = paper_from_svg_ydown(parse_svg(str(p)))
    flat = flatten_visible(doc)
    assert len(back) == len(flat)
    for pa, pb in zip(back, flat):
        assert len(pa) == len(pb)
        for (xa, ya), (xb, yb) in zip(pa, pb):
            assert (xa, ya) == pytest.approx((xb, yb), abs=1e-6)


# --- CAD 网格定价 ------------------------------------------------------------

def test_grid_pitch_mm_ladder():
    from megapro.gui.canvas.snap import grid_pitch_mm

    # 低缩放 → 大格；高缩放 → 小格；都落在 1/2/5×10^k
    for ppm, lo, hi in ((1.0, 24, 80), (5.0, 24, 80), (40.0, 24, 80),
                        (0.3, 24, 80)):
        p = grid_pitch_mm(ppm)
        assert lo <= p * ppm <= hi, f"ppm={ppm} pitch={p} 屏幕格距 {p*ppm:.0f}px"
        # pitch 是 1/2/5×10^k
        m = p
        while m >= 10:
            m /= 10
        while m < 1:
            m *= 10
        assert any(abs(m - x) < 1e-9 for x in (1.0, 2.0, 5.0)), f"pitch={p}"
