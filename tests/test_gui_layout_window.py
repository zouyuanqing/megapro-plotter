"""M3c 排版页窗口冒烟测试（offscreen，独立 QApplication）。

含阶段 2 契约用例（§7/§8.3）：旋转一致性（gi.mapToScene ≡ model.transformed_paths）、
拖动回写 model + 手势 token undo、多选改 X/Y 不塌缩、sp_h 生效、标尺 tick=view_from_mm
位置（只断言修复后正确位置）、网格吸附（含绘制工具）。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
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


# --- 阶段 2 契约（§7/§8.3） -------------------------------------------------

def _drag_via_mouse(view, p0_mm, p1_mm):
    """QTest 真事件拖拽：mm → 视口 px 走 view_from_mm 浮点通道后取整（容差 ≥1px）。"""
    def vp(x, y):
        return view.viewportTransform().map(QPointF(float(x), float(y))).toPoint()

    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=vp(*p0_mm))
    QTest.mouseMove(view.viewport(), vp(*p1_mm))
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=vp(*p1_mm))


def test_pathitem_matches_model_transform():
    """旋转一致性：θ=30/90、scale≠1 时 gi.mapToScene ≡ model.transformed_paths。"""
    from megapro.gui.canvas.items import PathItem
    from megapro.gui.layout.model import Item

    for ang, sc in ((30.0, 1.0), (90.0, 1.0), (30.0, 2.0), (90.0, 0.5),
                    (45.0, 1.7)):
        it = Item(paths=[[(0.0, 0.0), (10.0, 0.0), (0.0, 5.0)]],
                  pos=(3.5, -2.25), scale=sc, angle_deg=ang)
        gi = PathItem(it)
        # 旋转支点 = 本地 (0,0)（= pos，与 model.transformed_paths 一致）
        assert gi.transformOriginPoint() == QPointF(0.0, 0.0)
        for poly_g, poly_m in zip(it.paths, it.transformed_paths()):
            for (x, y), (mx, my) in zip(poly_g, poly_m):
                sp = gi.mapToScene(QPointF(x, y))
                assert sp.x() == pytest.approx(mx, abs=1e-6), (ang, sc, x, y)
                assert sp.y() == pytest.approx(my, abs=1e-6), (ang, sc, x, y)


def test_drag_writes_back_model_with_gesture_token():
    """拖动 mouseRelease → MoveItemsCommand 回写 model 并进撤销栈。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    lp.snap_enabled = False
    it = Item(paths=[[(0.0, 0.0), (20.0, 10.0)]], name="x", pos=(30.0, 40.0))
    lp._add_items([it])
    gi = lp._gi_for(it)
    before = lp._undo.count()
    _drag_via_mouse(lp.view, (40.0, 45.0), (60.0, 65.0))  # 拖 +20,+20（bbox 中心起）
    # 回写 model（不是只动 gi）
    assert it.pos[0] == pytest.approx(50.0, abs=2.0)
    assert it.pos[1] == pytest.approx(60.0, abs=2.0)
    assert (gi.pos().x(), gi.pos().y()) == pytest.approx(it.pos, abs=1e-9)
    assert lp._undo.count() == before + 1  # 进撤销栈
    lp._undo.undo()
    assert it.pos == (30.0, 40.0)  # 精确还原
    lp.deleteLater()


def test_two_drags_two_independent_undo_commands():
    """两次独立拖拽 → 两条独立可撤销命令（手势 token，mergeWith 先比 token）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    lp.snap_enabled = False
    it = Item(paths=[[(0.0, 0.0), (20.0, 10.0)]], name="x", pos=(30.0, 40.0))
    lp._add_items([it])
    before = lp._undo.count()
    _drag_via_mouse(lp.view, (40.0, 45.0), (60.0, 65.0))
    mid = it.pos
    lp._gi_for(it).setSelected(True)
    _drag_via_mouse(lp.view, (mid[0] + 10.0, mid[1] + 5.0),
                    (mid[0] + 30.0, mid[1] + 5.0))
    assert lp._undo.count() == before + 2  # 两条独立命令（未误并）
    lp._undo.undo()  # 只退第二次
    assert it.pos == pytest.approx(mid, abs=1e-9)
    lp._undo.undo()  # 再退第一次
    assert it.pos == (30.0, 40.0)
    lp.deleteLater()


def test_multi_select_move_no_collapse():
    """多选改 X/Y = 按选择集 bbox 锚点整体平移（每项 own old/new，不塌缩）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it1 = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="a", pos=(10.0, 10.0))
    it2 = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="b", pos=(40.0, 40.0))
    lp._add_items([it1, it2])
    g1, g2 = lp._gi_for(it1), lp._gi_for(it2)
    g1.setSelected(True)
    g2.setSelected(True)
    old_dx = it2.pos[0] - it1.pos[0]
    old_dy = it2.pos[1] - it1.pos[1]
    lp.sp_x.setValue(5.0)
    lp.sp_y.setValue(7.0)
    # 不塌缩：相对布局保持
    assert it2.pos[0] - it1.pos[0] == pytest.approx(old_dx, abs=1e-9)
    assert it2.pos[1] - it1.pos[1] == pytest.approx(old_dy, abs=1e-9)
    assert it1.pos != it2.pos
    # 整体平移到锚点 (5,7)（选择集 bbox bl = (10,10) → delta (-5,-3)）
    assert it1.pos == pytest.approx((5.0, 7.0), abs=1e-9)
    assert it2.pos == pytest.approx((35.0, 37.0), abs=1e-9)
    lp.deleteLater()


def test_sp_h_scales_item():
    """『高』输入生效：scale × h_target/h_cur（等比联动）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[[(0.0, 0.0), (0.0, 10.0)]], name="x", pos=(0.0, 0.0))
    lp._add_items([it])
    lp._gi_for(it).setSelected(True)
    assert lp.sp_h.value() == pytest.approx(10.0, abs=1e-6)
    lp.sp_h.setValue(20.0)
    assert it.scale == pytest.approx(2.0, abs=1e-9)
    x0, y0, x1, y1 = it.page_bbox()
    assert abs(y1 - y0) == pytest.approx(20.0, abs=1e-6)
    lp.deleteLater()


def test_ruler_ticks_at_paper_positions():
    """标尺 tick = view_from_mm 浮点位置；0 刻度对齐 paper y=0（只断言修复后位置）。"""
    from megapro.gui.canvas.view_transform import mm_from_view, view_from_mm
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    view = lp.view
    rv, rh = lp._ruler_v, lp._ruler_h
    for mmv in (0.0, 50.0, 137.5, -25.0):
        y = rv.tick_pos(mmv)
        assert y == pytest.approx(
            view_from_mm(view, QPointF(0.0, float(mmv))).y(), abs=1e-9)
        # 反查：该刻度对应的纸面 y == mmv（修旧实现恒偏一个床高的 bug）
        assert mm_from_view(view, QPointF(0.0, y)).y() == pytest.approx(mmv, abs=1e-6)
    for mmv in (0.0, 80.0, 210.0):
        x = rh.tick_pos(mmv)
        assert x == pytest.approx(
            view_from_mm(view, QPointF(float(mmv), 0.0)).x(), abs=1e-9)
        assert mm_from_view(view, QPointF(x, 0.0)).x() == pytest.approx(mmv, abs=1e-6)
    # 0 刻度对齐纸面原点（非床高那侧）
    assert mm_from_view(view, QPointF(0.0, rv.tick_pos(0.0))).y() == pytest.approx(0.0, abs=1e-6)
    lp.deleteLater()


def test_grid_snap_draw_tool_and_item():
    """网格吸附：SnapEngine 纯函数 + 绘制工具接入 + 拖动 itemChange 吸附。"""
    from PySide6.QtWidgets import QApplication  # noqa: F401
    from megapro.gui.canvas.snap import SnapEngine, snap_point
    from megapro.gui.layout.layout_page import LayoutPage, TOOL_LINE
    from megapro.gui.layout.model import Item

    # 纯函数
    assert snap_point((2.3, 4.7), 5.0) == (0.0, 5.0)
    assert snap_point((7.6, 2.4), 5.0) == (10.0, 0.0)
    engine = SnapEngine(5.0, enabled=True, obj_tol_mm=2.0)
    # 对象吸附优先（边投影 (1,0) 比端点更近）
    assert engine.snap((1.0, 1.0), [[(0.0, 0.0), (4.0, 0.0)]]) == (1.0, 0.0)
    assert engine.snap((9.0, 9.0), [[(0.0, 0.0), (4.0, 0.0)]]) == (10.0, 10.0)
    assert SnapEngine(5.0, enabled=False).snap((2.3, 4.7)) == (2.3, 4.7)

    # 绘制工具吸附（含对象吸附用网格验证）
    lp = LayoutPage()
    lp.snap_enabled = True
    lp.snap_pitch = 5.0
    lp._set_tool(TOOL_LINE)
    lp.begin_draw(QPointF(2.3, 4.7))
    lp.finish_draw(QPointF(61.2, 38.9))
    poly = lp.doc.items[0].transformed_paths()[0]  # 页面几何 = 吸附后坐标
    assert poly == [(0.0, 5.0), (60.0, 40.0)]

    # itemChange 拖动网格吸附（空文档 = 无对象候选，纯网格；对象吸附见
    # test_drag_snaps_to_object_key_points）
    lp2 = LayoutPage()
    lp2.snap_enabled = True
    lp2.snap_pitch = 5.0
    it = Item(paths=[[(0.0, 0.0), (1.0, 1.0)]], name="s")
    lp2._add_items([it])
    gi = lp2._gi_for(it)
    gi.setPos(2.3, 4.7)
    assert (gi.pos().x(), gi.pos().y()) == (0.0, 5.0)
    lp.deleteLater()
    lp2.deleteLater()


def test_paper_scene_grid_renders_both_axes():
    """drawBackground 网格两轴 + minor/major 两级都真实落像素（评审 medium #1 回归）。

    旧实现 _hgrid 从 QRectF.bottom()（数值大端）起步、while <= top()（小端）
    永不执行 → 水平线一条不画。此处渲到 QImage 按行/列统计网格色像素。
    """
    from PySide6.QtGui import QImage, QPainter, QTransform
    from megapro.gui.canvas.paper_scene import PaperScene

    sc = PaperScene()
    img = QImage(200, 200, QImage.Format_RGB32)
    img.fill(Qt.white)
    painter = QPainter(img)
    painter.setTransform(QTransform(2.0, 0, 0, -2.0, 0.0, 400.0))  # ppm=2
    from PySide6.QtCore import QRectF

    sc.drawBackground(painter, QRectF(0.0, 0.0, 100.0, 200.0))
    painter.end()
    grid_colors = {0xFFD8DDE2, 0xFFB8C0C8}  # minor #d8dde2 / major #b8c0c8
    rows = [0] * 200
    cols = [0] * 200
    seen = set()
    for y in range(200):
        for x in range(200):
            pix = img.pixel(x, y)
            if pix in grid_colors:
                rows[y] += 1
                cols[x] += 1
                seen.add(pix)
    # 水平线：一行应横贯（≥100 px）；旧 bug 下 max row = 交点数（<30）
    assert sum(1 for r in rows if r >= 100) >= 3, f"水平网格线缺失: max={max(rows)}"
    # 竖直线：一列应贯穿
    assert sum(1 for c in cols if c >= 100) >= 3, f"垂直网格线缺失: max={max(cols)}"
    # minor/major 两级
    assert seen == grid_colors, f"网格应 minor/major 两级: {sorted(hex(c) for c in seen)}"


def test_set_zoom_anchor_invariance():
    """§8.3 具名补测：view_from_mm ∘ mm_from_view 恒等 + set_zoom 锚点不变。

    与 test_set_zoom_anchor_and_roundtrip 同一把尺（该用例为阶段 2 既有），
    本用例按 §8.3 契约名钉住两条不变量本身。
    """
    from megapro.gui.canvas.view_transform import mm_from_view, view_from_mm
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    view = lp.view
    # ① 恒等：view→mm→view 往返浮点精确
    for vp in (QPointF(0.0, 0.0), QPointF(100.0, 100.0), QPointF(321.5, -7.25)):
        back = view_from_mm(view, mm_from_view(view, vp))
        assert back.x() == pytest.approx(vp.x(), abs=1e-9)
        assert back.y() == pytest.approx(vp.y(), abs=1e-9)
    # ② 锚点不变：set_zoom 后锚点视口像素下的 mm 点不动
    anchor_vp = QPointF(77.0, 143.0)
    anchor_mm = mm_from_view(view, anchor_vp)
    for ppm in (0.8, 2.0, 9.5, 60.0):
        view.set_zoom(ppm, anchor_vp)
        got = mm_from_view(view, anchor_vp)
        assert got.x() == pytest.approx(anchor_mm.x(), abs=1e-6), ppm
        assert got.y() == pytest.approx(anchor_mm.y(), abs=1e-6), ppm
    lp.deleteLater()


def test_set_zoom_anchor_and_roundtrip():
    """Q6+ 点名项：set_zoom 显式锚点不变 + view_from_mm∘mm_from_view 恒等。"""
    from megapro.gui.canvas.view_transform import mm_from_view, view_from_mm
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    view = lp.view
    anchor_vp = QPointF(123.0, 88.0)
    anchor_mm = mm_from_view(view, anchor_vp)
    for ppm in (3.0, 17.5, 80.0, 2.2):
        view.set_zoom(ppm, anchor_vp)
        got = mm_from_view(view, anchor_vp)
        assert got.x() == pytest.approx(anchor_mm.x(), abs=1e-6), ppm
        assert got.y() == pytest.approx(anchor_mm.y(), abs=1e-6), ppm
        assert view.px_per_mm() == pytest.approx(view._clamp_ppm(ppm), abs=1e-9)
    for vp in (QPointF(0.0, 0.0), QPointF(123.5, 88.25), QPointF(399.0, 200.0)):
        back = view_from_mm(view, mm_from_view(view, vp))
        assert back.x() == pytest.approx(vp.x(), abs=1e-9)
        assert back.y() == pytest.approx(vp.y(), abs=1e-9)
    lp.deleteLater()


def test_multi_drag_writes_back_whole_group():
    """多选拖动：整组漂移收集进一条 MoveItemsCommand，回写并可一次撤销。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    lp.snap_enabled = False
    it1 = Item(paths=[[(0.0, 0.0), (20.0, 10.0)]], name="a", pos=(30.0, 40.0))
    it2 = Item(paths=[[(0.0, 0.0), (20.0, 10.0)]], name="b", pos=(30.0, 80.0))
    lp._add_items([it1, it2])
    lp._gi_for(it1).setSelected(True)
    lp._gi_for(it2).setSelected(True)
    before = lp._undo.count()
    _drag_via_mouse(lp.view, (40.0, 45.0), (60.0, 65.0))
    assert lp._undo.count() == before + 1  # 一条命令（整组）
    assert it1.pos[0] == pytest.approx(50.0, abs=2.0)
    assert it1.pos[1] == pytest.approx(60.0, abs=2.0)
    # 整组同移：相对布局保持
    assert it2.pos[0] - it1.pos[0] == pytest.approx(0.0, abs=0.5)
    assert it2.pos[1] - it1.pos[1] == pytest.approx(40.0, abs=0.5)
    lp._undo.undo()
    assert it1.pos == (30.0, 40.0) and it2.pos == (30.0, 80.0)
    lp.deleteLater()


def test_snap_pitch_follows_zoom():
    """网格吸附 pitch 随缩放 = grid_steps(ppm) 的 minor（评审 low #3①）。"""
    from megapro.gui.canvas.snap import SnapEngine
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    lp.view.set_zoom(7.0)
    assert lp.snap_pitch == pytest.approx(
        SnapEngine.minor_pitch(lp.view.px_per_mm()))
    lp.snap_pitch = 5.0  # 显式覆盖仍可用
    lp.view.set_zoom(3.0)  # 下次缩放重算
    assert lp.snap_pitch == pytest.approx(
        SnapEngine.minor_pitch(lp.view.px_per_mm()))
    lp.deleteLater()


def test_drag_snaps_to_object_key_points():
    """拖动吸附含对象吸附（端点/中点/边，评审 low #3②）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it1 = Item(paths=[[(0.0, 0.0), (20.0, 0.0)]], name="a", pos=(30.0, 40.0))
    lp._add_items([it1])
    it2 = Item(paths=[[(0.0, 0.0), (20.0, 0.0)]], name="b", pos=(30.0, 80.0))
    lp._add_items([it2])
    gi2 = lp._gi_for(it2)
    # it1 页面端点 (50,40)；落点 (50.4,40.3) 在对象容差内 → 吸到端点（非网格）
    gi2.setPos(50.4, 40.3)
    assert (gi2.pos().x(), gi2.pos().y()) == (50.0, 40.0)
    lp.deleteLater()


def test_numeric_edit_merges_undo_per_gesture():
    """数值输入同字段碎击键合并为一条撤销；换字段另起（评审 low #6）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[[(0.0, 0.0), (10.0, 10.0)]], name="x", pos=(10.0, 10.0))
    lp._add_items([it])
    lp._gi_for(it).setSelected(True)
    before = lp._undo.count()
    lp.sp_x.setValue(51.0)   # 模拟键入 "100" 的 1→10→100 碎击键
    lp.sp_x.setValue(102.0)
    lp.sp_x.setValue(103.0)
    assert lp._undo.count() == before + 1  # 同字段手势合并
    lp.sp_y.setValue(88.0)  # 换字段 = 新手势
    assert lp._undo.count() == before + 2
    lp._undo.undo()  # 只退 Y
    assert it.pos == pytest.approx((103.0, 10.0), abs=1e-9)
    lp._undo.undo()  # 再退 X（一次回到手势前）
    assert it.pos == (10.0, 10.0)
    lp.deleteLater()
