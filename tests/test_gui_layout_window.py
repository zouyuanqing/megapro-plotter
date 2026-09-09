"""M3c 排版页窗口冒烟测试（offscreen，独立 QApplication）。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def test_layout_page_add_export_roundtrip(tmp_path):
    """排版页加文字图元 → 导出 → 临时 SVG 可被 parse_svg 回读（mm 坐标）。"""
    from megapro.gui.layout.export_svg import document_to_svg
    from megapro.gui.layout.layout_page import LayoutPage, _svg_to_paths
    from megapro.gui.layout.model import Item
    from megapro.gui.text_to_svg import text_outline_svg

    lp = LayoutPage()
    svg = text_outline_svg("A", size_mm=10.0)
    paths = _svg_to_paths(svg)
    it = Item(paths=paths, pos=(15.0, 20.0), name="A")
    lp._add_items([it])
    assert len(lp.doc.items) == 1
    lp._sync_models()

    out = document_to_svg(lp.doc)
    p = tmp_path / "o.svg"
    p.write_text(out, encoding="utf-8")
    from megapro.toolchain.svg_to_gcode import parse_svg

    polys = parse_svg(str(p))
    xs = [x for pl in polys for x, _ in pl]
    ys = [y for pl in polys for _, y in pl]
    assert min(xs) >= 15.0 - 0.5
    assert min(ys) >= 20.0 - 1.0
    lp.deleteLater()


def test_layout_page_clear_undoable():
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    lp._add_items([Item(paths=[[(0, 0), (1, 1)]], name="x")])
    assert len(lp.doc.items) == 1
    lp._clear()
    assert len(lp.doc.items) == 0
    lp._undo.undo()  # 清空可撤销
    assert len(lp.doc.items) == 1
    lp.deleteLater()


def test_layout_delete_and_undo():
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[[(0, 0), (1, 1)]], name="x")
    lp._add_items([it])
    gi = lp._gi_for(it)
    gi.setSelected(True)
    lp._delete_selected()
    assert len(lp.doc.items) == 0
    lp._undo.undo()
    assert len(lp.doc.items) == 1
    lp.deleteLater()


def test_layout_duplicate_and_zorder():
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[[(0, 0), (1, 1)]], name="x", z=1)
    lp._add_items([it])
    lp._gi_for(it).setSelected(True)
    lp._duplicate()
    assert len(lp.doc.items) == 2
    assert lp.doc.top_z() >= 2
    lp.deleteLater()


def test_layout_draw_rect():
    from megapro.gui.layout.layout_page import LayoutPage, TOOL_RECT
    from PySide6 import QtCore

    lp = LayoutPage()
    lp._set_tool(TOOL_RECT)
    lp.begin_draw(QtCore.QPointF(10, 10))
    lp.finish_draw(QtCore.QPointF(60, 40))
    assert len(lp.doc.items) == 1
    # 矩形 = 5 点闭合折线
    assert len(lp.doc.items[0].paths[0]) == 5
    lp.deleteLater()


def test_layout_draw_line_and_poly_open_not_closed():
    """直线/折线是开放路径（不闭合）；预览用 NoBrush 不显示成实心面。"""
    from megapro.gui.layout.layout_page import LayoutPage, TOOL_LINE, TOOL_POLY
    from PySide6 import QtCore

    # 直线
    lp = LayoutPage()
    lp._set_tool(TOOL_LINE)
    lp.begin_draw(QtCore.QPointF(10, 10))
    lp.finish_draw(QtCore.QPointF(60, 40))
    line = lp.doc.items[0].paths[0]
    assert len(line) == 2
    assert line[0] != line[-1]  # 开放
    lp.deleteLater()

    # 折线：多点，不闭合
    lp = LayoutPage()
    lp._set_tool(TOOL_POLY)
    lp.begin_draw(QtCore.QPointF(10, 10))
    lp.add_poly_point(QtCore.QPointF(30, 20))
    lp.finish_draw(QtCore.QPointF(50, 30))
    poly = lp.doc.items[0].paths[0]
    assert len(poly) == 3
    assert poly[0] != poly[-1]  # 折线开放，不闭合
    lp.deleteLater()


def test_layout_preview_no_brush():
    """预览项必须无填充（否则矩形/圆预览显示成实心面）。"""
    from megapro.gui.layout.layout_page import LayoutPage, TOOL_RECT
    from PySide6 import QtCore

    lp = LayoutPage()
    lp._set_tool(TOOL_RECT)
    lp.begin_draw(QtCore.QPointF(10, 10))
    lp.update_draw(QtCore.QPointF(60, 40))
    assert lp._preview_item is not None
    assert lp._preview_item.brush().style() == QtCore.Qt.NoBrush
    lp.cancel_draw()
    assert lp._preview_item is None
    lp.deleteLater()
