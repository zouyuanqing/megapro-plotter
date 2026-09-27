"""M3c 排版模型/导出 单测（纯逻辑，无 Qt）。"""

import math

import pytest

from megapro.gui.layout.export_svg import document_to_svg
from megapro.gui.layout.model import (
    BED_W, BED_H, Document, Item, MAX_TREE_DEPTH, flatten_visible, iter_items,
    iter_leaves, normalize_local,
)


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


# --- M1 模型树化（docs/PRD_layout_model_tree.md FR-01/FR-02） -----------------
#
# 既有 14 个用例（:15-155、:160+）一字不改保绿 = 「无 children 的 Item 数学
# 逐位不变」的可执行证明；下面是容器侧的新 golden。


def _approx_polyline(actual, expected, *, tol=1e-9):
    """单条折线逐点比对（``expected`` = 点列表）。"""
    assert len(actual) == len(expected)
    for (xa, ya), (xe, ye) in zip(actual, expected):
        assert xa == pytest.approx(xe, abs=tol)
        assert ya == pytest.approx(ye, abs=tol)


def test_nested_container_compose_parent_over_child():
    """FR-01 ② 嵌套 golden（防御性 pin）：父∘子，一般式。

    手算（父 pos=(50,60) s=0.5 θ=90°；子 pos=(10,20) s=2 θ=90°；叶子本地折线
    (0,0)-(4,0)-(4,3)）：

    叶子 → 子变换（先 2×、转 90°、平移 (10,20)）::

        (0,0)→(10,20)   (4,0)→(10,28)   (4,3)→(4,28)

    再 → 父变换（先 0.5×、转 90°、平移 (50,60)）::

        (10,20)→(50−10, 60+5)=(40,65)
        (10,28)→(50−14, 60+5)=(36,65)
        (4,28) →(50−14, 60+2)=(36,62)

    即整体 ≡ ``(x,y) ↦ (−x+40, −y+65)``。合成顺序写反（子∘父）会得到
    ``(−110,120)/(−114,120)/(−114,117)`` —— 本 golden 可区分两者。
    """
    leaf = Item(paths=[[(0.0, 0.0), (4.0, 0.0), (4.0, 3.0)]], name="leaf")
    child = Item(name="child", pos=(10.0, 20.0), scale=2.0, angle_deg=90.0,
                 children=[leaf])
    parent = Item(name="parent", pos=(50.0, 60.0), scale=0.5, angle_deg=90.0,
                  children=[child])

    assert parent.is_container() and child.is_container()
    assert not leaf.is_container()
    out = parent.transformed_paths()
    assert len(out) == 1
    _approx_polyline(out[0], [(40.0, 65.0), (36.0, 65.0), (36.0, 62.0)])


def test_container_transform_inherits_frozen_leaf_golden():
    """恒等子 + 父变换 = 既有叶子 golden 的同一手算值（(10,0)→(0,20)）。"""
    leaf = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="x")
    cont = Item(children=[leaf], scale=2.0, angle_deg=90.0)
    out = cont.transformed_paths()[0]
    x, y = out[1]
    assert x == pytest.approx(0.0, abs=1e-9)  # 缩放2 旋转90° → (0,20)
    assert y == pytest.approx(20.0, abs=1e-9)
    # 恒等容器逐点等于叶子自身结果（FR-07 包装等价性的数学基础）
    assert Item(children=[leaf]).transformed_paths() == leaf.transformed_paths()

    # 父 pos 非零时按父 pos 平移（同样按 父∘子）
    leaf2 = Item(paths=[[(0.0, 0.0), (1.0, 2.0)]], pos=(1.0, 2.0))
    cont2 = Item(children=[leaf2], pos=(100.0, 50.0), scale=2.0, angle_deg=90.0)
    _approx_polyline(cont2.transformed_paths()[0], [(96.0, 52.0), (92.0, 54.0)])


def test_container_bbox_is_union_in_own_local_frame():
    """容器 bbox = 子项几何在本项局部系的并集（不含本项自身变换）。

    **帧自洽性**（防「本地系 own paths 与页面系子几何混盒」）：容器自身
    paths 也在本项局部系里 —— ``bbox()`` 与 ``page_bbox()`` 的跨度必须相等，
    且 ``page_bbox()`` ≡ 自身变换作用在 ``bbox()`` 四角上。
    """
    a = Item(paths=[[(0.0, 0.0), (2.0, 1.0)]], pos=(10.0, 20.0))
    b = Item(paths=[[(0.0, 0.0), (4.0, 3.0)]], pos=(0.0, 0.0))
    cont = Item(children=[a, b], name="G")
    assert cont.bbox() == (0.0, 0.0, 12.0, 21.0)  # 并集，未含 cont 自身变换
    assert cont.page_bbox() == (0.0, 0.0, 12.0, 21.0)  # 恒等容器两者相同
    # 非恒等父变换：本地 bbox 不含父变换，页面 bbox 含
    moved = Item(children=[a, b], pos=(100.0, 0.0))
    assert moved.bbox() == (0.0, 0.0, 12.0, 21.0)
    assert moved.page_bbox() == (100.0, 0.0, 112.0, 21.0)
    # 空容器 = 叶子（退化 bbox 不炸）
    empty = Item(children=[], name="empty")
    assert not empty.is_container()
    assert empty.bbox() == (0.0, 0.0, 0.0, 0.0)
    assert empty.transformed_paths() == []

    # 容器**自身 paths**（本地系）与子项几何（子项已过自身变换 ⇒ 同为本项
    # 局部系）取并集：own (0,50)-(5,50) ∪ 子 (10,20)-(14,23) = (0,20,14,50)
    kid = Item(paths=[[(0.0, 0.0), (4.0, 0.0), (4.0, 3.0)]], pos=(10.0, 20.0))
    mixed = Item(paths=[[(0.0, 50.0), (5.0, 50.0)]], pos=(100.0, 0.0),
                 children=[kid], name="mix")
    assert mixed.bbox() == (0.0, 20.0, 14.0, 50.0)
    assert mixed.page_bbox() == (100.0, 20.0, 114.0, 50.0)
    # 帧自洽：跨度相等（若 own paths 混进页面系，跨度会不等 → 立即失败）
    span = lambda bb: (bb[2] - bb[0], bb[3] - bb[1])  # noqa: E731
    assert span(mixed.bbox()) == span(mixed.page_bbox()) == (14.0, 30.0)
    assert span(mixed.bbox()) == (14.0, 30.0)  # own y=50 与子 y=20 并到同一竖区间


def test_flatten_visible_orders_by_global_leaf_z():
    """FR-02：拍平按**叶子自身 z** 全局升序；容器 z 不参与（z=0、2 夹 1）。"""
    a = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="A", z=0)
    c = Item(paths=[[(1.0, 0.0), (2.0, 0.0)]], name="C", z=1)
    b = Item(paths=[[(2.0, 0.0), (3.0, 0.0)]], name="B", z=2)
    # 容器 z=-5（最小）但不得抢到最下层
    g = Item(name="G", z=-5, children=[a, c])
    doc = Document(items=[b, g])
    assert [pl[0][0] for pl in flatten_visible(doc)] == [0.0, 1.0, 2.0]


def test_flatten_visible_container_equals_flat_leaves():
    """恒等容器包住平铺项 = 逐点恒等（含折线顺序），嵌套同理。"""
    a = Item(paths=[[(0.0, 0.0), (2.0, 1.0)]], name="A", z=1,
             pos=(5.0, 6.0), scale=1.5, angle_deg=30.0)
    b = Item(paths=[[(0.0, 0.0), (1.0, 4.0), (2.0, 0.0)]], name="B", z=0,
             pos=(1.0, 2.0))
    flat = Document(items=[a, b])
    wrapped = Document(items=[Item(name="G", children=[a, b])])
    nested = Document(items=[Item(name="G2", children=[
        Item(name="G1", children=[a, b])])])
    assert flatten_visible(wrapped) == flatten_visible(flat)
    assert flatten_visible(nested) == flatten_visible(flat)


def test_flatten_visible_skips_hidden_leaf_and_container():
    a = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="A", z=0)
    hid = Item(paths=[[(1.0, 0.0), (2.0, 0.0)]], name="hid", z=1, visible=False)
    g = Item(name="G", children=[a, hid])
    assert [pl[0][0] for pl in flatten_visible(Document(items=[g]))] == [0.0]
    g_off = Item(name="G", visible=False, children=[a])
    assert flatten_visible(Document(items=[g_off])) == []


def test_flatten_visible_composes_ancestor_transforms():
    """FR-01 ② 防御性 pin 的**导出侧**：拍平必须含全部祖先变换。

    夹具同 :func:`test_nested_container_compose_parent_over_child`（父
    pos=(50,60) s=0.5 θ=90 ∘ 子 pos=(10,20) s=2 θ=90），手算值
    (40,65)/(36,65)/(36,62) 见该用例。期望值是**字面量**（不是
    ``flatten_visible`` 自身输出），故把父换成 pos=(9999,8888) s=13 θ=37
    时本用例必挂 —— 父变换被丢弃的实现骗不过去。
    """
    leaf = Item(paths=[[(0.0, 0.0), (4.0, 0.0), (4.0, 3.0)]], name="leaf")
    child = Item(name="child", pos=(10.0, 20.0), scale=2.0, angle_deg=90.0,
                 children=[leaf])
    parent = Item(name="parent", pos=(50.0, 60.0), scale=0.5, angle_deg=90.0,
                  children=[child])
    out = flatten_visible(Document(items=[parent]))
    assert len(out) == 1
    _approx_polyline(out[0], [(40.0, 65.0), (36.0, 65.0), (36.0, 62.0)])
    # 逐位等于容器自身的递归合成（两条几何链同源，不各写一套）
    assert out == parent.transformed_paths()


def test_flatten_visible_keeps_container_own_paths():
    """容器**自身**折线也是几何：拍平不得只吐叶子（否则自带折线消失）。"""
    leaf = Item(paths=[[(0.0, 0.0), (4.0, 0.0), (4.0, 3.0)]], name="leaf")
    mix = Item(paths=[[(0.0, 50.0), (5.0, 50.0)]], pos=(10.0, 0.0),
               children=[leaf], name="mix")
    out = flatten_visible(Document(items=[mix]))
    assert len(out) == 2  # own paths + 子树
    _approx_polyline(out[0], [(10.0, 50.0), (15.0, 50.0)])
    _approx_polyline(out[1], [(10.0, 0.0), (14.0, 0.0), (14.0, 3.0)])
    # 与容器递归合成同序（own paths 在前，与 transformed_paths 一致）
    assert out == mix.transformed_paths()
    # 祖先变换同样作用在容器自身折线上（own 单元也要合成父链）
    outer = Item(name="outer", pos=(0.0, 100.0), scale=1.0, children=[mix])
    _approx_polyline(flatten_visible(Document(items=[outer]))[0],
                     [(10.0, 150.0), (15.0, 150.0)])


def test_export_svg_ydown_roundtrip_nested_container(tmp_path):
    """FR-02 验收：嵌套容器 → 导出 → parse_svg + paper_from_svg_ydown 回读。

    期望值是**手算字面量**（不是 ``flatten_visible(doc)`` —— 那样断言两侧
    同源、对祖先变换是同义反复）：

    - 子树：叶子 (0,0)/(4,0)/(4,3) → 子(2×,90°,(10,20)) → (10,20)/(10,28)/
      (4,28) → 父(0.5×,90°,(50,60)) → **(40,65)/(36,65)/(36,62)**（z=0 先）
    - b（z=2）：(5,15)/(20,15) 平移 (3,4) → **(8,19)/(23,19)**
    """
    from megapro.gui.canvas.coords import paper_from_svg_ydown
    from megapro.toolchain.svg_to_gcode import parse_svg

    expect = [
        [(40.0, 65.0), (36.0, 65.0), (36.0, 62.0)],
        [(8.0, 19.0), (23.0, 19.0)],
    ]
    leaf = Item(paths=[[(0.0, 0.0), (4.0, 0.0), (4.0, 3.0)]], name="leaf")
    child = Item(name="child", pos=(10.0, 20.0), scale=2.0, angle_deg=90.0,
                 children=[leaf])
    doc = Document(items=[
        Item(name="parent", pos=(50.0, 60.0), scale=0.5, angle_deg=90.0,
             children=[child]),
        Item(paths=[[(5.0, 15.0), (20.0, 15.0)]], name="b", z=2, pos=(3.0, 4.0)),
    ])
    # 模型侧：拍平本身即手算值（对父变换敏感）
    flat = flatten_visible(doc)
    assert len(flat) == 2
    for pa, pe in zip(flat, expect):
        _approx_polyline(pa, pe, tol=1e-9)
    # 导出/回读侧：y-down 编解码对合后仍是同一组手算值
    p = tmp_path / "rt_tree.svg"
    p.write_text(document_to_svg(doc), encoding="utf-8")
    back = paper_from_svg_ydown(parse_svg(str(p)))
    assert len(back) == 2
    for pa, pe in zip(back, expect):
        _approx_polyline(pa, pe, tol=1e-6)


# --- 身份语义（Item eq=False：结构相等的图元/容器是两个图元） ----------------

def test_item_equality_is_identity_not_structural():
    a = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="x")
    b = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="x")
    assert a == a
    assert not (a == b)  # 结构相等但身份不同
    # 容器同理：children 相同也是两个容器
    c1 = Item(name="G", children=[a])
    c2 = Item(name="G", children=[a])
    assert not (c1 == c2)
    assert c1 == c1


def test_document_remove_uses_identity_not_structure():
    """``Document.remove`` 按身份删；``item in doc.items`` 同口径
    （make_gi 的 ``item not in page.doc.items`` 依赖它）。"""
    a = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="x")
    b = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="x")
    doc = Document()
    doc.add(a)
    doc.add(b)
    doc.remove(b)
    assert len(doc.items) == 1
    assert a in doc.items and b not in doc.items
    doc.remove(b)  # 再删不崩
    assert len(doc.items) == 1
    # 容器：删容器不动结构相同的另一个
    kid = Item(name="c", paths=[[(0.0, 0.0), (1.0, 0.0)]])
    g1 = Item(name="G", children=[kid])
    g2 = Item(name="G", children=[g1.children[0]])
    doc2 = Document(items=[g1, g2])
    doc2.remove(g2)
    assert [it is g1 for it in doc2.items] == [True]


def test_document_remove_is_group_aware_and_carries_ownership():
    """组内叶子**不再抛异常**：按身份定位 owning container 并摘除，返回归属回执。

    原方案（抛 ``ValueError``）已推翻（M1 评审裁决）：实跑
    ``QUndoStack.push(RemoveItemsCommand(lp,[kid]))`` 时 PySide6 报
    ``Error calling Python override of QUndoCommand::redo()``，但**命令已入栈**
    （``undo.count()==1``）而模型未改（几何仍在、切纸机照样下刀）；随后 Ctrl+Z
    得 ``doc.items=['G','kid']`` 且 flatten 吐**两条完全相同的折线** —— 几何翻倍。
    抛异常把「静默错」换成「不可撤销的错」，非净改善。
    """
    kid = Item(paths=[[(0.0, 0.0), (4.0, 0.0)]], pos=(10.0, 20.0), name="kid")
    sib = Item(paths=[[(0.0, 5.0), (4.0, 5.0)]], name="sib")
    g = Item(name="G", children=[kid, sib])
    doc = Document(items=[g])

    info = doc.remove(kid)  # 不抛
    assert info is not None and info.owner is g and info.index == 0
    assert g.children == [sib]              # 按身份摘除，不是整组清空
    assert doc.items == [g]                 # 容器本身不受影响
    assert kid not in g.children
    assert flatten_visible(doc) == [
        [(0.0, 5.0), (4.0, 5.0)]]          # sib 几何在，kid 几何已走

    # 文档里根本没有的对象仍静默忽略（既有契约：重复删不崩）
    assert doc.remove(kid) is None
    # 顶层删除仍按身份、返回 owner=None
    top = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="top")
    d2 = Document(items=[top])
    t = d2.remove(top)
    assert t is not None and t.owner is None
    assert d2.remove(top) is None
    # 删容器本身（顶层）正常
    doc.remove(g)
    assert doc.items == [] and flatten_visible(doc) == []
    doc.remove(g)  # 再删不崩


def test_attach_restores_group_structure_exactly():
    """**归属回执必须被 undo 侧使用**：单独组感知仍会让组被静默解散。

    M1 评审复现：摘除 a 后执行 ``RemoveItemsCommand.undo`` 实际调用的
    ``make_gi(lp, a)``，``undo_cmds.py:54`` 的 ``if item not in
    page.doc.items`` 对刚摘除的 a 判为「不在」→ ``page.doc.add(a)`` →
    ``doc.items=['G','a']``、``G.children=['b']``：**叶子变顶层项、组静默解散**，
    全程无异常。故 remove 返回 :class:`DetachInfo`，undo 侧必须走
    :meth:`Document.attach` 回原位。

    本用例以 make_gi 的判据原样复现「错误路径」，再断言正确路径。
    """
    a = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="a")
    b = Item(paths=[[(0.0, 5.0), (10.0, 5.0)]], name="b")
    g = Item(name="G", children=[a, b])
    doc = Document(items=[g])
    info = doc.remove(a)

    # 错误路径：按 undo_cmds.py:54 的判据入模 → 组被解散（本模型不提供此行为）
    wrong = Document(items=[g])
    if a not in wrong.items:
        wrong.add(a)
    assert [i.name for i in wrong.items] == ["G", "a"]   # 组已解散（复现）
    assert [c.name for c in g.children] == ["b"]

    # 正确路径：按回执 re-attach → 组原样复原，a 仍是组内成员
    doc.attach(a, owner=info.owner, index=info.index)
    assert [c.name for c in g.children] == ["a", "b"]
    assert [i.name for i in doc.items] == ["G"]         # 没变成第二个顶层项
    assert a in g.children
    # 下标复原：原位插入而非追加
    a2 = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="a2")
    b2 = Item(paths=[[(0.0, 1.0), (1.0, 1.0)]], name="b2")
    c2 = Item(paths=[[(0.0, 2.0), (1.0, 2.0)]], name="c2")
    g2 = Item(name="G2", children=[a2, b2, c2])
    d3 = Document(items=[g2])
    i1 = d3.remove(b2)          # 中间项
    d3.attach(b2, owner=i1.owner, index=i1.index)
    assert [c.name for c in g2.children] == ["a2", "b2", "c2"]
    # owner=None → 回顶层；重复 attach 幂等
    d3.attach(b2)
    assert b2 in d3.items
    d3.attach(b2)
    assert [i.name for i in d3.items].count("b2") == 1


def test_document_z_queries_are_tree_aware():
    """z 查询/列举走全树：M2 把导入组包成单个 z=0 容器后，
    ``top_z()+1`` 仍须落在组内叶子之上（US-2）。"""
    leaves = [Item(paths=[[(float(i), 0.0), (float(i) + 1.0, 0.0)]],
                   name=f"l{i}", z=float(i)) for i in range(3)]
    doc = Document(items=[Item(name="组", children=leaves)])
    assert doc.top_z() == 2.0  # 不是容器 z=0.0
    assert doc.bottom_z() == 0.0
    assert [it.name for it in doc.sorted_items()] == ["l0", "l1", "l2"]
    assert [it.name for it in doc.items_visible()] == ["l0", "l1", "l2"]
    # 隐藏的组内成员不出现在 items_visible，但仍计入 z 范围
    leaves[1].visible = False
    assert [it.name for it in doc.items_visible()] == ["l0", "l2"]
    assert doc.top_z() == 2.0
    # 隐藏容器跳过整棵子树
    doc.items[0].visible = False
    assert doc.items_visible() == []


def test_container_own_geometry_reaches_enumeration():
    """容器自身折线是**一等几何**，枚举面必须与拍平面同口径。

    否则它能进 ``flatten_visible``（被切）却进不了 ``layout_page._on_export``
    的越界预检（后者遍历 ``items_visible()`` 取 ``page_bbox``）—— 超床几何
    静默出刀。
    """
    kid = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], pos=(10.0, 20.0),
               name="kid", z=1)
    c = Item(paths=[[(0.0, 0.0), (300.0, 0.0)]], name="C", children=[kid])
    doc = Document(items=[c])
    # 拍平面看得见 own 折线（x=300 超床）
    assert flatten_visible(doc)[0] == [(0.0, 0.0), (300.0, 0.0)]
    # 枚举面同样看得见（容器自己是几何拥有者）
    names = [it.name for it in doc.items_visible()]
    assert names == ["C", "kid"]  # C z=0 先于 kid z=1
    assert doc.items_visible()[0].page_bbox() == (0.0, 0.0, 300.0, 20.0)
    assert [it.name for it in doc.sorted_items()] == ["C", "kid"]
    # 容器被隐藏 → own 折线一并隐藏
    c.visible = False
    assert doc.items_visible() == [] and flatten_visible(doc) == []


def test_container_is_not_a_render_unit():
    """画布侧硬契约：容器**不是渲染单元**（M2 在 make_gi 兑现）。

    钉住「隐形可点幽灵」的确切成因：容器自身无折线可画（``paths`` 空），
    但 :meth:`bbox` 递归后非零 —— ``PathItem`` 用 bbox 当本地矩形
    （``canvas/items.py:53``）⇒ 会出现「画不出、点得到」的覆盖整组的矩形，
    而本轮修好后拖它会真的按 ``container.pos`` 移动导出几何。
    场景项列表只能是 :func:`iter_leaves`。
    """
    kid = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], pos=(10.0, 20.0))
    g = Item(name="G", children=[kid])
    assert g.is_container()
    assert g.paths == []                      # 无自身折线 ⇒ 无 PathItem 可画
    assert g.bbox() == (10.0, 20.0, 20.0, 20.0)  # 但 bbox 非空 ⇒ 幽灵命中区
    assert g.page_bbox() == (10.0, 20.0, 20.0, 20.0)
    # 渲染/选中单元 = 叶子（容器被排除）
    assert [it.name for it in iter_leaves([g])] == ["item"]  # 叶子未命名
    assert list(iter_leaves([g])) == [kid]
    # 容器在 doc 里，但它不是渲染单元
    doc = Document(items=[g])
    assert list(doc.items) == [g]
    assert list(iter_leaves(doc.items)) == [kid]
    # 组变换只写叶子集合（FR-04/FR-05）：容器 pos 变化必须带动叶子几何
    before = flatten_visible(doc)
    g.pos = (0.0, 40.0)
    after = flatten_visible(doc)
    assert before == [[(10.0, 20.0), (20.0, 20.0)]]
    assert after == [[(10.0, 60.0), (20.0, 60.0)]]
    assert after == g.transformed_paths()  # 父∘子一般式仍然成立


def test_document_contains_is_tree_aware():
    """``Document.contains`` = 全树身份查找（画布删除前置判定用）。"""
    kid = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="kid")
    g = Item(name="G", children=[Item(name="G2", children=[kid])])
    doc = Document(items=[g])
    assert doc.contains(kid) and doc.contains(g)
    assert not doc.contains(Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="kid"))
    # 结构相等的另一个对象不算
    twin = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="kid")
    assert not doc.contains(twin)


def test_normalize_local_is_noop_for_container():
    a = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], pos=(5.0, 6.0))
    g = Item(name="G", children=[a])
    before = (g.pos, g.scale, g.angle_deg, g.paths, [c.pos for c in g.children])
    assert normalize_local(g) == (0.0, 0.0)
    assert (g.pos, g.scale, g.angle_deg, g.paths,
            [c.pos for c in g.children]) == before
    assert a.transformed_paths()[0][0] == (5.0, 6.0)


def test_iter_leaves_yields_depth_first_visible_only():
    a = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="A")
    b = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="B", visible=False)
    c = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="C")
    g = Item(name="G", children=[a, Item(name="G2", children=[b, c])])
    # 默认 visible_only=False = 全部渲染单元（M1 评审裁决，见下用例）
    assert [it.name for it in iter_leaves([g])] == ["A", "B", "C"]
    assert [it.name for it in iter_leaves([g], visible_only=True)] == ["A", "C"]
    # 隐藏容器：默认模式**仍产出**其叶子（渲染单元已建，可见性由祖先链决定，
    # 见 iter_leaves docstring 的「连带契约」）；过滤模式整棵子树跳过
    g.visible = False
    assert [it.name for it in iter_leaves([g])] == ["A", "B", "C"]
    assert list(iter_leaves([g], visible_only=True)) == []


# --- M1 评审阻塞项回归（B1–B5） ------------------------------------------------

def test_iter_leaves_default_includes_hidden_leaves():
    """B4：默认必须**包含**隐藏叶子，否则隐藏→显示永远画不出来。

    ``make_gi`` 有 ``gi.setVisible(item.visible)``（``undo_cmds.py:51``）—— 可见
    性由场景项自己表达，渲染面要的是「全部渲染单元」。若枚举面按默认
    ``visible_only=True`` 建场景项，隐藏叶子的 ``PathItem`` 永不创建，此后
    ``_gi_for(hidden)`` 恒 ``None``、``_sync_gi`` 恒空操作。实跑：旧默认下
    ``iter_leaves([G])`` 对唯一 ``visible=False`` 的叶子返回 ``[]``。
    """
    hid = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="hid", visible=False)
    g = Item(name="G", children=[hid])
    assert [it.name for it in iter_leaves([g])] == ["hid"]
    # 显式开启过滤才跳过 —— 隐藏项连同整棵子树
    assert list(iter_leaves([g], visible_only=True)) == []
    g.visible = False
    assert list(iter_leaves([g], visible_only=True)) == []
    # 默认模式下隐藏容器/隐藏叶子的场景项**仍建** —— 有效可见性由祖先链决定
    # （M2 契约：make_gi 的 setVisible(item.visible) 单独用不够）
    assert [it.name for it in iter_leaves([g])] == ["hid"]
    # 可视性过滤的职责在 flatten_visible（拍平面），不在渲染面
    assert flatten_visible(Document(items=[g])) == []


def test_leaf_in_container_must_not_be_added_as_second_top_level():
    """B1：只跳过容器**不够** —— 渲染按 iter_leaves 会让 make_gi 重复入模。

    根因（``canvas/undo_cmds.py:54-55``）：
    ``if item not in page.doc.items: page.doc.add(item)``。叶子在容器里时
    **不在**顶层列表（身份判定）→ 被追加成第二个顶层 Item。

    实跑（按 model.py 旧处方的 iter_leaves + 跳过容器逐字执行）：
    ``doc.items = ['G','a','b']``、``flatten_visible`` 吐 **4** 条折线而真实
    几何单元只有 **2** 条 ⇒ **同一几何被切两遍**（切纸机上即重复下刀）。

    正确判据是「是否已有归属」，用 :meth:`Document.contains`（全树身份查找），
    不是「是否在顶层列表」。本用例钉住正确判据。
    """
    a = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="a")
    b = Item(paths=[[(0.0, 5.0), (10.0, 5.0)]], name="b")
    g = Item(name="G", children=[a, b])
    doc = Document(items=[g])

    # 旧判据（「在顶层列表?」）在组内叶子上判错 —— 复现双重切割
    wrong = Document(items=[g])
    for it in iter_leaves(wrong.items):
        if it not in wrong.items:          # undo_cmds.py:54 原样
            wrong.add(it)
    assert [i.name for i in wrong.items] == ["G", "a", "b"]
    assert len(flatten_visible(wrong)) == 4 > 2   # 几何翻倍 = 重复下刀

    # 正确判据（是否已有归属）：组内叶子一律不重复入模
    right = Document(items=[g])
    for it in iter_leaves(right.items):
        if not right.contains(it):        # contains = 全树身份查找
            right.add(it)
    assert [i.name for i in right.items] == ["G"]
    assert flatten_visible(right) == flatten_visible(doc)
    assert len(flatten_visible(right)) == 2
    # 真正的新图元（无归属）仍要入模
    new = Item(paths=[[(0.0, 20.0), (10.0, 20.0)]], name="new")
    if not right.contains(new):
        right.add(new)
    assert [i.name for i in right.items] == ["G", "new"]


def test_page_bbox_omits_ancestor_chain_known_gap():
    """B3 **已知缺口**（M1 不修，M2 必须处理）：``page_bbox()`` 不走祖先链。

    实跑：``leaf(pos=(10,20))`` 在 ``container(pos=(100,0))`` 下，
    ``flatten_visible`` 给 ``(110,20)``，``leaf.page_bbox()`` 给 ``(10,20)``、
    ``cont.page_bbox()`` 给 ``(110,20)`` —— 同一叶子两条「页面」坐标。

    后果：``layout_page.py:981-982`` 的越界预检正是
    ``for it in doc.items_visible(): it.page_bbox()``，枚举面拿不到祖先链
    （``Item`` 无 parent 反向指针）⇒ 实跑一个真实 x 范围 250..300（超 210 床）
    的图元时**所有上报 bbox 都在床内、越界警告永不触发** ⇒ 「导出切了 /
    预检没报」分叉。

    **M1 改不了**：修法要么给 Item 加 parent 指针（新字段，须重想构造与剪贴板
    白名单语义），要么改 layout_page.py:981-982 消费单元 —— 都越出本里程碑边界。
    本用例把现状钉成显式契约（而非静默分叉），M2 修好后应改写。
    """
    leaf = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], pos=(10.0, 20.0), name="leaf")
    cont = Item(name="G", pos=(100.0, 0.0), children=[leaf])
    doc = Document(items=[cont])
    # 拍平面走祖先链
    assert flatten_visible(doc) == [[(110.0, 20.0), (120.0, 20.0)]]
    # 枚举面不走 —— 两个不同的数
    assert leaf.page_bbox() == (10.0, 20.0, 20.0, 20.0)
    assert cont.page_bbox() == (110.0, 20.0, 120.0, 20.0)
    assert leaf.page_bbox() != cont.page_bbox()
    # 恒等容器（产品路径）两者一致 ⇒ 现在不炸的原因
    ident = Item(name="G2", children=[leaf])
    assert ident.page_bbox() == leaf.page_bbox()
    # 越界漏报的具体形态：真实几何超床，但叶子自报在床内
    far = Item(paths=[[(200.0, 0.0), (300.0, 0.0)]], pos=(0.0, 0.0), name="far")
    d2 = Document(items=[Item(name="G3", pos=(0.0, 0.0), children=[far])])
    real_x = [x for pl in flatten_visible(d2) for x, _ in pl]
    assert max(real_x) > BED_W                       # 几何真的超床
    assert far.page_bbox()[2] > BED_W                # 叶子自报也超床（恒等容器时）


def test_cyclic_tree_raises_value_error_not_recursion_error():
    """B5：成环必须是可诊断的 ``ValueError``，不是 ``RecursionError``。

    FR-03① 的验收是「对**任意选择**编组/解组前后恒等」，任意选择包含「组把
    自身选进子集」；无保护时 ``flatten_visible`` 直接 ``RecursionError``。
    四条递归遍历共享 :data:`MAX_TREE_DEPTH` 上限。
    """
    a = Item(name="a")
    b = Item(name="b")
    a.children = [b]
    b.children = [a]                 # 环
    doc = Document(items=[a])
    for fn, label in (
        (lambda: flatten_visible(doc), "flatten_visible"),
        (lambda: list(iter_items([a])), "iter_items"),
        (lambda: a.bbox(), "Item.bbox"),
        (lambda: a.transformed_paths(), "Item.transformed_paths"),
    ):
        with pytest.raises(ValueError, match="深度超过"):
            fn()
    # 导出链同样受保护（走 flatten_visible）
    from megapro.gui.layout.export_svg import document_to_svg
    with pytest.raises(ValueError, match="深度超过"):
        document_to_svg(doc)

    # 合法深树不受影响（未误伤正常嵌套）
    deep = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="leaf")
    for i in range(10):
        deep = Item(name=f"c{i}", children=[deep])
    assert len(flatten_visible(Document(items=[deep]))) == 1
