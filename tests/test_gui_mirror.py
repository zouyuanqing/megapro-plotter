"""M3 镜像交付（FR-08）—— 纯逻辑 + 离屏画布。

三层验证：
- ``mirror_scalar`` 对合/中心不动/退化为 ``flip_y_scalar``（coords 侧）
- ``Item`` 几何与**手算值**一致（归一 y0=0 与非归一 y0≠0 各一对，含
  scale/angle≠1/0 组合；compose 顺序 mirror→scale→rotate→translate）
- 导出 SVG 回读对合、端到端进 JobSpec、画布路径烘镜像 + 撤销

坐标单源红线：镜像数学只落 ``coords.mirror_scalar``，本文件不含任何翻转算术。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# --- coords 侧：镜像纯函数 ---------------------------------------------------

def test_mirror_scalar_is_involution_and_fixes_center():
    """镜像是对合（点两次回原位）且区间中点不动。"""
    from megapro.gui.canvas.coords import mirror_scalar

    for lo, hi in ((0.0, 10.0), (2.0, 12.0), (-3.0, 7.0)):
        mid = (lo + hi) / 2.0
        assert mirror_scalar(mid, lo, hi) == pytest.approx(mid, abs=1e-12)
        for v in (lo, hi, lo + 0.25, hi - 0.75, mid):
            assert mirror_scalar(mirror_scalar(v, lo, hi), lo, hi) == \
                pytest.approx(v, abs=1e-12)


def test_mirror_scalar_degenerates_to_flip_when_lo_is_zero():
    """``lo == 0`` 时退化为唯一翻转实现 ``flip_y_scalar(v, hi)``（FR-08 v1.3）。

    这是 y0=0（``normalize_local`` 归一入口常态）的情形；非归一项必须仍对。
    """
    from megapro.gui.canvas.coords import flip_y_scalar, mirror_scalar

    for v in (-3.5, 0.0, 2.25, 10.0):
        assert mirror_scalar(v, 0.0, 10.0) == pytest.approx(
            flip_y_scalar(v, 10.0), abs=1e-12)


def test_mirror_scalar_equals_lo_plus_hi_minus_v():
    """展开 :func:`mirror_scalar` = ``lo + hi - v``（手算核对）。

    ``flip_y_scalar(v-lo, hi-lo) + lo = (hi-lo)-(v-lo)+lo = lo+hi-v``。
    支点 = 区间中点 ``(lo+hi)/2`` 的对称映射。
    """
    from megapro.gui.canvas.coords import mirror_scalar

    assert mirror_scalar(3.0, 2.0, 9.0) == pytest.approx(8.0, abs=1e-12)
    assert mirror_scalar(7.0, 2.0, 9.0) == pytest.approx(4.0, abs=1e-12)


# --- Item 几何：手算 golden --------------------------------------------------

def test_no_mirror_is_bit_identical_to_previous_math():
    """**未设镜像时逐位不变**（既有 transform golden 语义不漂移）。"""
    from megapro.gui.layout.model import Item

    it = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], pos=(0.0, 0.0),
              scale=2.0, angle_deg=90.0)
    x, y = it.transformed_paths()[0][1]
    assert x == pytest.approx(0.0, abs=1e-9)
    assert y == pytest.approx(20.0, abs=1e-9)


def test_mirror_normalized_y0_zero_golden():
    """归一夹具（y0=0）手算：paths y∈[0,3]，mirror_y ⇒ y' = 3 - y。

    ``[(0,0),(10,0),(10,3)]`` → ``[(0,3),(10,3),(10,0)]``；bbox 与 pos 不变。
    """
    from megapro.gui.layout.model import Item

    it = Item(paths=[[(0.0, 0.0), (10.0, 0.0), (10.0, 3.0)]],
              pos=(20.0, 30.0), mirror_y=True)
    assert it.transformed_paths() == [[(20.0, 33.0), (30.0, 33.0), (30.0, 30.0)]]
    # 镜像绕自身中心 ⇒ bbox 稳定、位置不变
    assert it.page_bbox() == (20.0, 30.0, 30.0, 33.0)


def test_mirror_unnormalized_y0_nonzero_golden():
    """**非归一**（y0≠0）手算：paths y∈[2,5] ⇒ ``y' = lo+hi-y = 7-y``。

    逐点：y=2 → 5、y=5 → 2；再加 pos_y=1 ⇒ 页面 (7,6) 与 (17,3)。
    这条钉住 FR-08 v1.3 的「支点=本地 bbox 中心（一般式）」—— 实现若按
    0 起点的 ``flip_y_scalar(y, hi)`` 写成 y=2→3、y=5→0，本用例必挂。
    """
    from megapro.gui.layout.model import Item

    it = Item(paths=[[(0.0, 2.0), (10.0, 5.0)]], pos=(7.0, 1.0),
              mirror_y=True)
    assert it.transformed_paths() == [[(7.0, 6.0), (17.0, 3.0)]]


def test_mirror_x_and_y_golden():
    """水平镜像手算：paths x∈[2,5] ⇒ ``x' = 7-x``；与垂直同式。

    双轴：x∈[2,5]、y∈[1,4] ⇒ (2,1)→(5,4)、(5,4)→(2,1)。
    """
    from megapro.gui.layout.model import Item

    it = Item(paths=[[(2.0, 0.0), (5.0, 0.0)]], mirror_x=True)
    assert it.transformed_paths() == [[(5.0, 0.0), (2.0, 0.0)]]
    both = Item(paths=[[(2.0, 1.0), (5.0, 4.0)]], mirror_x=True, mirror_y=True)
    assert both.transformed_paths() == [[(5.0, 4.0), (2.0, 1.0)]]


def test_mirror_compose_order_is_mirror_scale_rotate_translate():
    """合成顺序 mirror → scale → rotate → translate（FR-08）。

    期望值是**独立手算**（不是回读本实现）：paths y∈[0,6]、mirror_y、
    scale=2、angle=30、pos=(100,50) ⇒ 先 y'=lo+hi-y=6-y，再乘 2，再转 30°，
    再平移 (100,50)。

    夹具**必须有非零 y 跨度**：一条全 y=0 的水平线没有垂直镜像轴
    （``_mirror_span`` 返回 None ⇒ 该轴不镜像，见
    :func:`test_mirror_degenerate_span_is_noop`），那样的夹具根本不会走到
    镜像分支、测不到合成顺序。
    """
    import math

    from megapro.gui.layout.model import Item

    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0)]
    it = Item(paths=[list(pts)], mirror_y=True, scale=2.0,
              angle_deg=30.0, pos=(100.0, 50.0))
    out = it.transformed_paths()
    c, s = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
    exp = []
    for x, y in pts:
        y1 = (0.0 + 6.0 - y) * 2.0    # mirror → scale
        x1 = x * 2.0
        exp.append((x1 * c - y1 * s + 100.0, x1 * s + y1 * c + 50.0))
    assert len(out) == 1
    assert len(out[0]) == len(exp)
    for got, want in zip(out[0], exp):
        assert got[0] == pytest.approx(want[0], abs=1e-9)
        assert got[1] == pytest.approx(want[1], abs=1e-9)


def test_mirror_degenerate_span_is_noop():
    """退化跨度（宽/高为 0）**该轴**不镜像 —— 竖直线的水平镜像本就是恒等。

    若不挡，``mirror_scalar(v, v, v)`` 会退化成绕原点反射（``-v``），把竖直线
    整体搬到负半轴。
    """
    from megapro.gui.layout.model import Item

    vertical = Item(paths=[[(3.0, 0.0), (3.0, 10.0)]], mirror_x=True)
    assert vertical.transformed_paths() == [[(3.0, 0.0), (3.0, 10.0)]]
    horizontal = Item(paths=[[(0.0, 7.0), (10.0, 7.0)]], mirror_y=True)
    assert horizontal.transformed_paths() == [[(0.0, 7.0), (10.0, 7.0)]]


def test_mirror_degenerate_axis_does_not_silence_the_other_axis():
    """**双标志 + 单轴退化**：退化轴不镜像，另一轴**照常镜像**（逐轴语义）。

    回归 M3 评审阻塞项：``_mirror_span`` 曾用一个 ``None`` 门控**整项** ——
    只要任一被镜像的轴跨度退化就返回 None，而消费侧把两轴一起门控。于是
    「竖直线 + 水平+垂直两个标志」时 y 的镜像被**连带静默丢弃**，产生
    「flag=True 而几何未镜像」的分叉，并随导出/JobSpec 一路传下去。

    触发路径是常规操作：工具条是两个独立按钮，连点「水平镜像 + 垂直镜像」。

    手算：竖直线 y∈[0,10]（含 3）⇒ y'=10-y ⇒ 0→10、10→0、3→7；x 恒 5
    （无水平轴）。水平线 x∈[0,10]（含 6）⇒ x'=10-x ⇒ 0→10、10→0、6→4。
    """
    from megapro.gui.layout.model import Item

    vertical = Item(paths=[[(5.0, 0.0), (5.0, 10.0), (5.0, 3.0)]],
                    mirror_x=True, mirror_y=True)
    assert vertical.local_paths() == [[(5.0, 10.0), (5.0, 0.0), (5.0, 7.0)]]
    assert vertical.transformed_paths() == [[(5.0, 10.0), (5.0, 0.0), (5.0, 7.0)]]

    horizontal = Item(paths=[[(0.0, 0.0), (10.0, 0.0), (6.0, 0.0)]],
                      mirror_x=True, mirror_y=True)
    assert horizontal.local_paths() == [[(10.0, 0.0), (0.0, 0.0), (4.0, 0.0)]]
    assert horizontal.transformed_paths() == [[(10.0, 0.0), (0.0, 0.0), (4.0, 0.0)]]

    # 标志与几何**不得**分叉：任一轴退化时，另一轴的镜像必须真的发生
    assert vertical.mirror_x is True and vertical.mirror_y is True
    assert vertical.transformed_paths() != [list(vertical.paths[0])]


def test_mirror_degenerate_axis_reaches_job_spec():
    """同一缺陷的**导出侧**：退化轴不得连带吞掉另一轴的镜像（端到端不丢）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[[(5.0, 0.0), (5.0, 10.0), (5.0, 3.0)]], name="V")
    lp._add_items([it])
    gi = lp._gi_for(it)
    gi.setSelected(True)
    before = lp.to_job_spec().paths_paper

    lp._toggle_mirror("h")   # 竖直线无水平轴 ⇒ 不镜像
    lp._toggle_mirror("v")   # 垂直镜像**必须**生效
    after = lp.to_job_spec().paths_paper
    assert after != before
    assert after == [[(5.0, 10.0), (5.0, 0.0), (5.0, 7.0)]]
    lp.deleteLater()


def test_mirror_reaches_flatten_and_page_bbox():
    """镜像几何端到端：``flatten_visible`` / ``page_bbox`` / ``unit_paths`` 同源。"""
    from megapro.gui.layout.model import (
        Document, Item, flatten_visible, unit_paths,
    )

    it = Item(paths=[[(0.0, 0.0), (10.0, 0.0), (10.0, 3.0)]], pos=(20.0, 30.0),
              mirror_y=True)
    doc = Document(items=[it])
    assert flatten_visible(doc) == it.transformed_paths()
    assert it.page_bbox() == (20.0, 30.0, 30.0, 33.0)
    assert unit_paths(it) == it.transformed_paths()


def test_mirror_inside_container_composes_with_ancestors():
    """组内叶子镜像 + 祖先链合成（一般式仍成立，非恒等容器亦对）。"""
    from megapro.gui.layout.model import Document, Item, flatten_visible

    leaf = Item(paths=[[(0.0, 0.0), (10.0, 0.0), (10.0, 3.0)]],
                pos=(10.0, 20.0), mirror_y=True)
    cont = Item(name="C", pos=(100.0, 0.0), children=[leaf])
    doc = Document(items=[cont])
    flat = flatten_visible(doc)
    # 叶子先自身镜像：y 0..3 → 3..0（pos 20）⇒ (10,23)(20,23)(20,20)
    # 再父平移 +100 ⇒ (110,23)(120,23)(120,20)
    assert flat == [[(110.0, 23.0), (120.0, 23.0), (120.0, 20.0)]]


# --- 导出 SVG 回读对合 --------------------------------------------------------

def test_export_svg_mirror_roundtrip(tmp_path):
    """导出 y-down ↔ 回读：镜像几何经 SVG 往返**逐点恒等**（roundtrip 对合）。

    容差 1e-3：``export_svg._fmt`` 按 3 位小数写坐标（既有精度，冻结用例
    ``test_export_svg_ydown_roundtrip`` 同款），故含旋转的路径往返误差上限
    约 5e-4 —— 用 1e-6 卡会因**导出精度**而非镜像数学失败。
    """
    from megapro.gui.canvas.coords import paper_from_svg_ydown
    from megapro.gui.layout.export_svg import document_to_svg
    from megapro.gui.layout.model import Document, Item, flatten_visible
    from megapro.toolchain.svg_to_gcode import parse_svg

    doc = Document(items=[
        Item(paths=[[(0.0, 0.0), (10.0, 0.0), (10.0, 3.0)]], name="a", z=1,
             pos=(20.0, 30.0), mirror_y=True),
        Item(paths=[[(0.0, 0.0), (4.0, 2.0)]], name="b", z=2, pos=(5.0, 5.0),
             mirror_x=True, scale=1.5, angle_deg=20.0),
    ])
    flat = flatten_visible(doc)
    p = tmp_path / "mirror.svg"
    p.write_text(document_to_svg(doc), encoding="utf-8")
    back = paper_from_svg_ydown(parse_svg(str(p)))
    assert len(back) == len(flat)
    for pa, pb in zip(back, flat):
        assert len(pa) == len(pb)
        for (xa, ya), (xb, yb) in zip(pa, pb):
            assert xa == pytest.approx(xb, abs=1e-3)
            assert ya == pytest.approx(yb, abs=1e-3)


# --- 画布（离屏）：路径烘镜像 + 撤销 ------------------------------------------

def test_canvas_bakes_mirror_into_drawn_path():
    """画笔路径烘镜像：``rebuild_path`` 用 ``local_paths()``（坑 1 的正解）。

    判据用**点的位置**而非 ``boundingRect`` —— 镜像绕本地 bbox 中心 ⇒
    boundingRect 本就稳定（那是 FR-08 的保证，不是没生效）。
    """
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[[(0.0, 0.0), (10.0, 0.0), (10.0, 3.0)]], name="L",
              pos=(20.0, 30.0))
    lp._add_items([it])
    gi = lp._gi_for(it)
    assert it.local_paths() == [[(0.0, 0.0), (10.0, 0.0), (10.0, 3.0)]]

    it.mirror_y = True
    gi.rebuild_path()  # 场景同步唯一入口要求显式重建
    # 场景点 == 模型页面点（镜像已烘、scale/rot/pos 交给 Qt）
    for (lx, ly), (mx, my) in zip(it.local_paths()[0],
                                  it.transformed_paths()[0]):
        sp = gi.mapToScene(QPointF(lx, ly))
        assert sp.x() == pytest.approx(mx, abs=1e-6)
        assert sp.y() == pytest.approx(my, abs=1e-6)
    lp.deleteLater()


def test_mirror_toggle_is_one_undoable_command():
    """工具条镜像 = 一条 ``ChangeItemPropsCommand``，撤销精确还原。

    这条同时钉住坑 1：若 setattr 通道不 ``rebuild_path``，模型会翻转但画布
    纹丝不动（撤销栈计数与模型断言照过，必须另有一处能观察到路径没变 ——
    故这里同时断言 ``local_paths`` 与页面几何都已改变）。
    """
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[[(0.0, 0.0), (10.0, 0.0), (10.0, 3.0)]], name="L",
              pos=(20.0, 30.0))
    lp._add_items([it])
    lp._gi_for(it).setSelected(True)
    before = it.transformed_paths()
    n0 = lp._undo.count()

    lp._toggle_mirror("v")
    assert lp._undo.count() == n0 + 1  # 一条
    assert it.mirror_y is True
    assert it.transformed_paths() != before  # 几何真的翻了
    assert it.page_bbox() == (20.0, 30.0, 30.0, 33.0)  # 但 bbox 稳定

    lp._undo.undo()
    assert it.mirror_y is False
    assert it.transformed_paths() == before  # 精确还原
    lp.deleteLater()


def test_mirror_toggle_twice_is_identity():
    """镜像是对合：连点两次回到原状。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[[(0.0, 0.0), (10.0, 0.0), (10.0, 3.0)]], name="L")
    lp._add_items([it])
    lp._gi_for(it).setSelected(True)
    before = it.transformed_paths()
    lp._toggle_mirror("h")
    lp._toggle_mirror("h")
    assert it.mirror_x is False
    assert it.transformed_paths() == before
    lp.deleteLater()


def test_mirror_reaches_job_spec_end_to_end():
    """镜像几何端到端进 JobSpec（贴纸转印的数据前提）。

    用 L 形（竖+横）而非直线：直线绕自身中心镜像等于自身、观察不到变化。
    """
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[[(0.0, 0.0), (0.0, 10.0), (10.0, 10.0)]], name="L",
              pos=(50.0, 50.0))
    lp._add_items([it])
    lp._gi_for(it).setSelected(True)
    plain = lp.to_job_spec().paths_paper
    assert plain == [[(50.0, 50.0), (50.0, 60.0), (60.0, 60.0)]]

    lp._toggle_mirror("h")
    mirrored = lp.to_job_spec().paths_paper
    assert mirrored == [[(60.0, 50.0), (60.0, 60.0), (50.0, 60.0)]]
    assert mirrored != plain
    lp.deleteLater()


def test_mirror_preserves_pos_scale_angle():
    """原位镜像：``pos``/``scale``/``angle_deg`` **不补偿**（仅镜像标志变更）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[[(0.0, 0.0), (10.0, 0.0), (10.0, 3.0)]], name="L",
              pos=(33.0, 44.0), scale=2.0, angle_deg=15.0)
    lp._add_items([it])
    lp._gi_for(it).setSelected(True)
    before = (it.pos, it.scale, it.angle_deg)
    lp._toggle_mirror("v")
    assert (it.pos, it.scale, it.angle_deg) == before
    lp.deleteLater()


def test_group_mirror_is_per_member_about_own_center():
    """组的镜像 = **逐成员**绕各自 bbox 中心翻，不是整组刚性反射。

    这是 FR-08 v1.3 字面契约的直接后果，不是实现取巧：
    - 支点钉死为「**本地** bbox 中心」且「**仅镜像标志变更、不补偿**」；
    - 容器恒等、组变换走叶子集合（FR-04/FR-05），没有「组中心」这条通路。

    故 A(宽 10、x 50..60) 与 B(宽 6、x 70..76) 各自翻后仍是原位，组的
    成员间距**不**等于「整组绕组中心翻」的结果。

    ⚠ **PRD 未定义组的镜像语义**（FR-08 只定义 Item 级）。若产品要「整组刚性
    镜像」，需另立契约（组级镜像字段或成员 paths 绕组中心重写），两者都会
    突破 v1.3 的「不补偿/仅标志」裁决 —— 留 M6 定稿，本用例钉住当前实际行为。
    """
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (0.0, 10.0), (10.0, 10.0)]], name="A",
             pos=(50.0, 50.0))
    b = Item(paths=[[(0.0, 0.0), (0.0, 10.0), (6.0, 10.0)]], name="B",
             pos=(70.0, 50.0))
    lp._add_items([a, b])
    lp.doc.group_items([a, b], name="G")
    for it in (a, b):
        lp._gi_for(it).setSelected(True)
    before = lp.to_job_spec().paths_paper
    assert before[0][0] == (50.0, 50.0)   # A 首点
    assert before[1][0] == (70.0, 50.0)   # B 首点

    lp._toggle_mirror("h")
    after = lp.to_job_spec().paths_paper
    # 各成员绕**自身** bbox 中心翻：宽 10 → 首点 50→60；宽 6 → 70→76
    assert after[0][0] == (60.0, 50.0)
    assert after[1][0] == (76.0, 50.0)
    # 位置不动（pos 不补偿），仅内容翻转
    assert a.pos == (50.0, 50.0) and b.pos == (70.0, 50.0)
    # 整组刚性镜像会是 50→70、70→50（绕组中心 x=63）；当前行为**不是**它
    assert after[0][0] != (70.0, 50.0)
    lp._undo.undo()
    assert lp.to_job_spec().paths_paper == before
    lp.deleteLater()
