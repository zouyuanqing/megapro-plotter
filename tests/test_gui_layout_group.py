"""M2 编组可用（offscreen，独立 QApplication）—— FR-03/04/05/06/07 的 T7b。

``tests/test_gui_layout_window.py`` 的 21 个既有用例**一字不改**保绿（另文件
冻结），本文件只放 M2 新增用例：编组/解组模型、组拖动一条命令、组语义对齐/
分布/层序、递归剪贴板、FR-06 吸附域修复、T7b 包装接线。

模型侧纯逻辑（编组恒等重组）不依赖 Qt，但同文件共享一个 QApplication。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _drag_via_mouse(view, p0_mm, p1_mm):
    """QTest 真事件拖拽（与冻结窗口用例同款：mm → 视口 px 浮点通道）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    def vp(x, y):
        return view.viewportTransform().map(QPointF(float(x), float(y))).toPoint()

    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=vp(*p0_mm))
    QTest.mouseMove(view.viewport(), vp(*p1_mm))
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=vp(*p1_mm))


# --- FR-03① 编组/解组前后 flatten_visible 逐点恒等（任意选择） -----------------

def test_group_ungroup_preserves_flatten_pointwise_and_order():
    """FR-03①：编组前后拍平含折线顺序逐点恒等（z 不连续：0、2 夹 1）。"""
    from megapro.gui.layout.model import Document, Item, flatten_visible

    a = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="a", z=0)
    c = Item(paths=[[(5.0, 5.0), (15.0, 5.0)]], name="c", z=1)
    b = Item(paths=[[(20.0, 0.0), (30.0, 0.0)]], name="b", z=2)
    doc = Document(items=[a, c, b])
    before = flatten_visible(doc)
    cont = doc.group_items([a, b], name="G")  # z 不连续的选择
    assert cont is not None
    after = flatten_visible(doc)
    assert after == before                       # 逐点恒等（含顺序）
    # 容器恒等变换、z 不参与排序
    assert cont.paths == [] and cont.pos == (0.0, 0.0)
    assert cont.scale == 1.0 and cont.angle_deg == 0.0
    # 解组：拍平仍逐点恒等（FR-03① 的真正契约 —— z 序决定切割次序）
    assert doc.ungroup(cont) is not None
    assert flatten_visible(doc) == before


def test_group_ungroup_restores_top_level_order_when_adjacent():
    """**相邻**成员编组再解组 ⇒ 顶层列表序精确复原（undo 的强契约）。

    非相邻成员（如 a、b 中间夹着 c）解组后成员**并到组槽位**成连续一段
    （编组本身就把它们拉到了一起，顶层序不可复原）；但拍平（= 实际切割次序）
    始终逐点恒等，见上一个用例。
    """
    from megapro.gui.layout.model import Document, Item, flatten_visible

    a = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="a", z=0)
    c = Item(paths=[[(5.0, 5.0), (15.0, 5.0)]], name="c", z=1)
    b = Item(paths=[[(20.0, 0.0), (30.0, 0.0)]], name="b", z=2)
    doc = Document(items=[a, b, c])  # a、b 相邻
    before = flatten_visible(doc)
    cont = doc.group_items([a, b], name="G")
    assert [it.name for it in doc.items] == ["G", "c"]
    assert doc.ungroup(cont) is not None
    assert [it.name for it in doc.items] == ["a", "b", "c"]
    assert flatten_visible(doc) == before


def test_group_rejects_single_and_cyclic_selection():
    """成员不足 2 个 / 自含（组选自己或其祖先）→ None，且不产生副作用。"""
    from megapro.gui.layout.model import Document, Item

    a = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="a")
    b = Item(paths=[[(2.0, 0.0), (3.0, 0.0)]], name="b")
    doc = Document(items=[a, b])
    assert doc.group_items([a]) is None                    # 单个
    assert [it.name for it in doc.items] == ["a", "b"]
    cont = Item(name="H", children=[a, b])
    doc2 = Document(items=[cont])
    assert doc2.group_items([cont, a]) is None              # 成环
    assert [it.name for it in doc2.items] == ["H"]


# --- FR-04 拖组 = 一条可撤销命令 --------------------------------------------

def test_group_drag_is_one_undoable_command():
    """编组后拖动整组 = **一条** MoveItemsCommand，撤销整组复原。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    lp.snap_enabled = False
    a = Item(paths=[[(0.0, 0.0), (20.0, 10.0)]], name="a", pos=(30.0, 40.0))
    b = Item(paths=[[(0.0, 0.0), (20.0, 10.0)]], name="b", pos=(30.0, 80.0))
    lp._add_items([a, b])
    lp._gi_for(a).setSelected(True)
    lp._gi_for(b).setSelected(True)
    lp.group_selected()  # 编组
    # 组选中 = 叶子集（两个成员都选中）
    assert lp._gi_for(a).isSelected() and lp._gi_for(b).isSelected()
    before = lp._undo.count()
    _drag_via_mouse(lp.view, (40.0, 45.0), (60.0, 65.0))
    assert lp._undo.count() == before + 1  # 一条命令（整组）
    # 整组同移，组内相对布局保持
    assert b.pos[1] - a.pos[1] == pytest.approx(40.0, abs=0.5)
    lp._undo.undo()
    assert a.pos == (30.0, 40.0) and b.pos == (30.0, 80.0)
    lp.deleteLater()


def test_group_undo_restores_top_level_structure():
    """编组撤销 = 解组复原（顶层序与几何都回来）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="a", pos=(10.0, 10.0))
    b = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="b", pos=(20.0, 10.0))
    lp._add_items([a, b])
    lp._gi_for(a).setSelected(True)
    lp._gi_for(b).setSelected(True)
    lp.group_selected()
    assert len(lp.doc.items) == 1  # 编成一个容器
    lp._undo.undo()  # 撤销编组
    assert len(lp.doc.items) == 2  # 恢复两个顶层
    lp.deleteLater()


# --- FR-03 选择语义：点组内子项选中整组 / 双击进组 ---------------------------

def test_click_group_child_selects_whole_group():
    """点组内一个子项 → 选中整组（FR-03 选择语义）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="a", pos=(10.0, 10.0))
    b = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="b", pos=(20.0, 10.0))
    lp._add_items([a, b])
    lp.group_selected()  # 未选中任何 → 不足两个，no-op；改为直接编组
    # 直接编组两个再验证
    cont = lp.doc.group_items([a, b], name="G")
    assert cont is not None
    # 模拟只点其中一个（无 Ctrl）→ _on_selection_changed 扩成整组
    lp._gi_for(a).setSelected(True)
    lp._expand_group_selection()
    assert lp._gi_for(a).isSelected()
    assert lp._gi_for(b).isSelected()  # 整组被选中
    lp.deleteLater()


def test_double_click_enters_group_edit_mode():
    """双击组内子项 → 进组编辑态（再点子项不自动扩成整组）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="a", pos=(10.0, 10.0))
    b = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="b", pos=(20.0, 10.0))
    lp._add_items([a, b])
    cont = lp.doc.group_items([a, b], name="G")
    assert lp.on_item_double_clicked(a) is True     # 进组
    assert lp._group_overlay.editing is cont
    # 组编辑态：只选一个子项时**不**扩成整组（操作单元 = 叶子）
    lp._gi_for(a).setSelected(True)
    lp._gi_for(b).setSelected(False)
    lp._expand_group_selection()
    assert lp._gi_for(a).isSelected()
    assert not lp._gi_for(b).isSelected()
    lp.deleteLater()


# --- FR-06 吸附域修复（前置修复验收①） -------------------------------------

def test_draw_tool_object_snap_hits_in_page_domain():
    """FR-06 验收①：pos≠(0,0) 图元，页面查询点 (51,61) → 吸附到 (51,60)。

    旧实现候选是**局部**坐标（it.paths），对页面点零命中；现切到页面域
    （transformed_paths / unit_paths）。
    """
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    lp.snap_enabled = True
    lp.snap_pitch = 5.0
    it = Item(paths=[[(0.0, 0.0), (20.0, 0.0)]], name="a", pos=(50.0, 60.0))
    lp._add_items([it])
    # 页面点 (51,61) 靠近页面边 (50,60)-(70,60)，在对象容差内 → 吸附
    got = lp._snap_pt(QPointF(51.0, 61.0))
    assert (got.x(), got.y()) == pytest.approx((51.0, 60.0))
    lp.deleteLater()


def test_hidden_container_hides_whole_subtree_in_scene():
    """隐藏容器 ⇒ 整棵子树在画布上不可见（M1 连带契约的兑现）。

    可见性**不能**在建场景项时过滤（那会让隐藏→显示切换永远画不出来），
    只能在**显示**时按**整条祖先链**求值。实测踩过的坑：判据若从
    ``iter_ancestors([item])`` 查（只遍历 item 自己的子树），容器的
    ``visible`` 永远不被访问 ⇒ 隐藏容器下的叶子照样被画出来。
    """
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item, flatten_visible

    lp = LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="a", pos=(10.0, 10.0))
    b = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="b", pos=(20.0, 10.0))
    lp._add_items([a, b])
    lp.doc.group_items([a, b], name="G")
    g = lp.doc.items[0]
    before = flatten_visible(lp.doc)

    g.visible = False
    for it in (a, b):
        lp._sync_gi(it)
    assert not lp._gi_for(a).isVisible()   # 祖先隐藏 ⇒ 叶子不可见
    assert not lp._gi_for(b).isVisible()
    assert flatten_visible(lp.doc) == []   # 模型侧也跳过整棵子树

    g.visible = True
    for it in (a, b):
        lp._sync_gi(it)
    assert lp._gi_for(a).isVisible() and lp._gi_for(b).isVisible()
    assert flatten_visible(lp.doc) == before  # 隐藏→显示可切回
    lp.deleteLater()


def test_snap_candidates_exclude_hidden_and_include_group_leaves():
    """吸附候选 = 可见页面域；隐藏图元排除、组内叶子纳入。"""
    from megapro.gui.canvas.snap import snap_candidate_paths
    from megapro.gui.layout.model import Document, Item

    a = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="a", pos=(10.0, 10.0))
    hid = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="hid", pos=(50.0, 10.0),
               visible=False)
    g = Item(name="G", children=[a, hid])
    doc = Document(items=[g])
    cands = snap_candidate_paths(doc)
    # 只含可见的 a（页面域 (10,10)-(20,10)），隐藏的 hid 不在内
    assert any(abs(x - 10.0) < 1e-6 and abs(y - 10.0) < 1e-6
               for p in cands for x, y in p)
    assert not any(abs(x - 50.0) < 1e-6 for p in cands for x, _ in p)


# --- FR-06 组语义：对齐/分布按组 bbox ----------------------------------------

def test_group_align_uses_group_bbox():
    """组参与对齐按**容器 bbox**（组内布局保持，整体移动）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="a", pos=(0.0, 50.0))
    b = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="b", pos=(10.0, 50.0))
    c = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="c", pos=(80.0, 50.0))
    lp._add_items([a, b, c])
    lp.doc.group_items([a, b], name="G")  # 组的 bbox = (0..20)
    # 选中整组 + 组外散件 c，按左对齐：组与 c 的左边界都到 0
    for it in (a, b, c):
        lp._gi_for(it).setSelected(True)
    lp._align("left")
    # c 已对齐（x=80 → 移到 0），组的两个成员整体左移保持相对间距
    assert c.pos[0] == pytest.approx(0.0, abs=1e-6)
    assert b.pos[0] - a.pos[0] == pytest.approx(10.0, abs=1e-6)  # 组内保持
    lp.deleteLater()


def test_group_distribute_uses_group_bbox_once_not_per_leaf():
    """组参与分布按**容器 bbox 算一个单元**（FR-06 验收的分布部分）。

    回归 M2 评审阻塞项：``_selected_units`` 曾对每个选中**叶子**各产一条
    同容器单元（N 个成员 ⇒ N 条重复 G）。``_distribute`` 的除数
    ``len(boxes) - 1`` 被重复项灌大 ⇒ gaps 偏小，且每个重复项按不同 target
    各写一次绝对 pos（``ChangeItemPropsCommand`` 是 setattr 绝对赋值、
    最后一条生效）⇒ **整组被平移一个非零量**。

    本夹具是「应当零位移」的三单元分布：A(y=0)、组 G{y=50,60}、B(y=100)：
    lo=0、hi=100、gaps=50，target 0/50/100 与现 y0 完全吻合 ⇒ 正确结果是
    **一个都不动**。去重前的错误实现把 C 50→66.67、D 60→76.67。
    """
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="A", pos=(0.0, 0.0))
    b = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="B", pos=(0.0, 100.0))
    c = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="C", pos=(0.0, 50.0))
    d = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="D", pos=(0.0, 60.0))
    lp._add_items([a, b, c, d])
    lp.doc.group_items([c, d], name="G")
    for it in (a, b, c, d):
        lp._gi_for(it).setSelected(True)

    # 三个操作单元：两个散件 + 一个组（组只算一次）
    units = lp._selected_units()
    assert len(units) == 3
    assert sorted(u.name for u, _ in units) == ["A", "B", "G"]

    lp._distribute("v")
    # 零位移（组内相对间距亦保持）
    assert a.pos[1] == pytest.approx(0.0, abs=1e-6)
    assert c.pos[1] == pytest.approx(50.0, abs=1e-6)
    assert d.pos[1] == pytest.approx(60.0, abs=1e-6)
    assert b.pos[1] == pytest.approx(100.0, abs=1e-6)
    lp.deleteLater()


def test_group_distribute_actually_spaces_units():
    """分布**真要移动**时也按组 bbox 算一个单元（手算对照值）。

    夹具 A(y=0)、B(y=20)、组 G{y=50,60}。按 y0 稳定排序 A(0) / B(20) / G(50)，
    lo=0、**hi = 组的远沿 60**（不是最远单元的 y0）、gaps=60/2=30
    ⇒ target 0/30/60 ⇒ A 不动、B +10、组 +10（C 50→60、D 60→70，
    组内间距 10 保持）。

    夹具里 A/B **刻意取不同 y**（0 与 20）：y 相等时它们在 ``boxes.sort``
    的稳定序里先后不定，「谁拿 target 0、谁拿 30」随机 —— 那不是实现缺陷
    而是夹具歧义，会让本用例时绿时红。
    """
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="A", pos=(0.0, 0.0))
    b = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="B", pos=(0.0, 20.0))
    c = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="C", pos=(0.0, 50.0))
    d = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="D", pos=(0.0, 60.0))
    lp._add_items([a, b, c, d])
    lp.doc.group_items([c, d], name="G")
    for it in (a, b, c, d):
        lp._gi_for(it).setSelected(True)

    lp._distribute("v")
    assert a.pos[1] == pytest.approx(0.0, abs=1e-6)     # 已到位，不动
    assert b.pos[1] == pytest.approx(30.0, abs=1e-6)    # 20 → 30
    assert c.pos[1] == pytest.approx(60.0, abs=1e-6)    # 50 → 60
    assert d.pos[1] == pytest.approx(70.0, abs=1e-6)    # 60 → 70
    assert d.pos[1] - c.pos[1] == pytest.approx(10.0, abs=1e-6)  # 组内保持
    lp.deleteLater()


def test_distribute_without_groups_is_unchanged():
    """**无组**平铺文档的分布行为不受去重影响（防修组语义时误伤老路径）。

    手算：A(0) B(10) C(50) D(70) 按 y0 排序、lo=0、hi=70、gaps=70/3
    ⇒ target 0 / 23.33 / 46.67 / 70。
    """
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    its = [Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name=n, pos=(0.0, y))
           for n, y in (("A", 0.0), ("B", 10.0), ("C", 50.0), ("D", 70.0))]
    lp._add_items(its)
    for it in its:
        lp._gi_for(it).setSelected(True)
    assert len(lp._selected_units()) == 4  # 散件不折叠
    lp._distribute("v")
    got = {i.name: i.pos[1] for i in its}
    assert got["A"] == pytest.approx(0.0, abs=1e-6)
    assert got["B"] == pytest.approx(70.0 / 3.0, abs=1e-6)
    assert got["C"] == pytest.approx(2 * 70.0 / 3.0, abs=1e-6)
    assert got["D"] == pytest.approx(70.0, abs=1e-6)
    lp.deleteLater()


def test_group_distribute_horizontal_axis_dedupes_too():
    """横向分布同样按组 bbox 算一个单元（修的是 ``_selected_units``，两轴共用）。

    手算（图元宽 10 ⇒ 组 bbox x=50..70，散件 A 0..10、B 100..110）：
    按 x0 排序 A(0) / G(50) / B(100)，lo=0、hi=**B 的右沿** 110、gaps=110/2=55
    ⇒ target 0/55/110 ⇒ A 位移 0、组 +5（C 50→55、D 60→65）、B +10。
    """
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="A", pos=(0.0, 0.0))
    b = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="B", pos=(100.0, 0.0))
    c = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="C", pos=(50.0, 0.0))
    d = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="D", pos=(60.0, 0.0))
    lp._add_items([a, b, c, d])
    lp.doc.group_items([c, d], name="G")
    for it in (a, b, c, d):
        lp._gi_for(it).setSelected(True)
    assert len(lp._selected_units()) == 3
    # 组只算一个单元：bbox = 两个成员的并集 (50..70)
    gb = [lp._unit_bbox(u) for u, _ in lp._selected_units() if u.name == "G"][0]
    assert gb == (50.0, 0.0, 70.0, 0.0)

    lp._distribute("h")
    assert a.pos[0] == pytest.approx(0.0, abs=1e-6)
    assert c.pos[0] == pytest.approx(55.0, abs=1e-6)
    assert d.pos[0] == pytest.approx(65.0, abs=1e-6)
    assert b.pos[0] == pytest.approx(110.0, abs=1e-6)
    lp.deleteLater()


def test_selected_units_in_group_edit_mode_are_leaves():
    """组编辑态：操作单元 = 叶子（FR-05 选择态二分），故**不**按容器折叠。

    这是 ``_selected_units`` 去重的另一面 —— 普通态折叠、编辑态不折叠，
    两个态各自的语义都被钉住。
    """
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="A", pos=(0.0, 0.0))
    b = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="B", pos=(100.0, 0.0))
    c = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="C", pos=(50.0, 0.0))
    d = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="D", pos=(60.0, 0.0))
    lp._add_items([a, b, c, d])
    cont = lp.doc.group_items([c, d], name="G")
    for it in (a, b, c, d):
        lp._gi_for(it).setSelected(True)
    assert len(lp._selected_units()) == 3  # 普通态：组折叠成一个

    assert lp.on_item_double_clicked(c) is True
    assert lp._group_overlay.editing is cont
    assert len(lp._selected_units()) == 4  # 编辑态：4 个叶子
    assert sorted(u.name for u, _ in lp._selected_units()) == ["A", "B", "C", "D"]
    lp.deleteLater()


def test_group_zorder_moves_whole_group_together():
    """组层序 = 整组叶子同步上移、保持组内相对次序（FR-06）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="a", z=1)
    b = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="b", z=3)
    lp._add_items([a, b])
    lp.doc.group_items([a, b], name="G")
    for it in (a, b):
        lp._gi_for(it).setSelected(True)
    lp._zorder("up")
    assert a.z == pytest.approx(2.0) and b.z == pytest.approx(4.0)
    assert b.z - a.z == pytest.approx(2.0)  # 组内相对次序保持
    lp.deleteLater()


# --- FR-06 递归剪贴板（组复制粘贴后几何与层序恢复） ---------------------------

def test_group_copy_paste_preserves_geometry_and_order():
    """复制一个组 → 粘贴得到同构副本（几何+组内层序保持，根落在新最上）。"""
    from megapro.gui.layout.layout_page import (
        LayoutPage, _clone_item, _item_from_json, _item_to_json,
    )
    from megapro.gui.layout.model import Document, Item, flatten_visible

    a = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="a", z=1, pos=(5.0, 5.0))
    b = Item(paths=[[(0.0, 0.0), (6.0, 3.0)]], name="b", z=2, pos=(5.0, 20.0))
    g = Item(name="G", children=[a, b])

    clone = _clone_item(g, dz=5.0, z=9.0)
    assert clone.is_container()
    assert [ch.name for ch in clone.children] == ["a", "b"]
    # 根 z = 9（新最上），子项保留原相对 z
    assert clone.z == 9.0
    assert [ch.z for ch in clone.children] == [1.0, 2.0]
    # 偏移只加根，组内相对几何不变
    assert clone.pos == pytest.approx((5.0, 5.0))
    # 与原组拍平逐点可比：每个点整体平移 (5,5)，组内相对布局不变
    orig_paths = flatten_visible(Document(items=[g]))
    clone_paths = flatten_visible(Document(items=[clone]))
    assert len(orig_paths) == len(clone_paths)
    for po, pc in zip(orig_paths, clone_paths):
        for (ox, oy), (cx, cy) in zip(po, pc):
            assert (cx - ox, cy - oy) == pytest.approx((5.0, 5.0), abs=1e-9)


# --- T7b：_add_doc 包装接线（容器整组可见可拖） -------------------------------

def test_doc_import_wrapped_container_scene_has_no_ghost_item():
    """T7b：包装后组内叶子**不**被 make_gi 重复入模（同一几何不切两遍）。

    M1 实测的「双重切割」：`make_gi` 若用 `item not in doc.items` 判归属，
    每个组内叶子会被 doc.add 成第二个顶层项。
    """
    from megapro.gui.canvas.undo_cmds import make_gi
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="a", pos=(10.0, 10.0))
    b = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="b", pos=(20.0, 10.0))
    lp._add_items([a, b])
    cont = lp.doc.group_items([a, b], name="G")
    n_top = len(lp.doc.items)
    # 重建组内叶子场景项（Add/Remove undo 的重建路径）
    for leaf in (a, b):
        if lp._gi_for(leaf) is None:
            make_gi(lp, leaf)
    # 仍是 1 个顶层（组），没有多出 a/b 顶层项
    assert len(lp.doc.items) == n_top == 1
    assert lp.doc.items[0] is cont
    # 容器本身不建 PathItem（不建不可见的隐形可点矩形）
    assert lp._gi_for(cont) is None
    lp.deleteLater()
