"""B4 回归：切页必须复位组编辑态；复制一个组时整棵子树要落在最上层之上。

缺陷一（组编辑态跨页泄漏，f2225ec 实测复现）：``_on_page_tab_changed`` /
``_on_page_add`` / ``_on_page_del`` / ``_on_page_dup`` / ``_move_page`` 五个都
只调 :meth:`LayoutPage._rebuild_scene`，而 ``_rebuild_scene`` **不复位**
``GroupOverlay.editing`` ⇒ 换页后 ``editing`` 仍指着**上一页**的容器。受害面
远不止「选中」：``_expand_group_selection``/``on_item_double_clicked``/
``_selected_units`` 全把它当哨兵，于是新页里整组编辑语义静默失效（点组内子项
选不中整组、双击进不去组、组框不画、拖动/缩放/对齐/分布/层序/复制全按叶子走）。
editing 只当哨兵、容器从不被解引用 ⇒ 不崩，只会一直错下去；Esc 能救，导出走
``iter_units`` 不受影响。修法：在共同汇流点 :meth:`_rebuild_scene` 复位。

缺陷二（复制组只抬容器 z）：``_item_from_json`` 给子项保留原 z，而
``flatten_visible`` **只按叶子 z 排序、容器 z 不参与** ⇒ 副本容器 z=top+1 看着
在最上，成员 z 仍是 1/2，拍平序被压到原有图元之下。修法：在**调用点**
（:meth:`_paste` / :meth:`_duplicate`）用 :func:`_restack_above` 把整棵子树抬上
去 —— 不去改 ``_item_from_json`` 的子项 z 契约（那是既有冻结断言）。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# --- 脚手架 -----------------------------------------------------------------

def _leaf(page, name, *, y, x=0.0, z=1.0):
    from megapro.gui.layout.model import Item

    it = Item(paths=[[(x, y), (x + 10.0, y)]], name=name, z=z)
    page._add_items([it])
    return it


def _group(page, *items, name):
    assert page.doc.group_items(list(items), name=name) is not None
    return [c for c in page.doc.items if c.is_container()][-1]


def _enter_group_edit(page, leaf):
    """双击组内子项进组编辑态（走真实入口 :meth:`on_item_double_clicked`）。"""
    return page.on_item_double_clicked(leaf)


def _selected_names(page) -> list:
    return sorted(gi.model_item.name for gi in page._selected())


def _make_group_on_p0_then_switch(page, switch):
    """p0 编组 G0 → 进组编辑态 → 切页（``switch(page)``）。

    返回新页上建好的 G1 的 (a1, b1)。
    """
    a0, b0 = _leaf(page, "a0", y=0.0), _leaf(page, "b0", y=20.0)
    _group(page, a0, b0, name="G0")
    page._gi_for(a0).setSelected(True)
    page._gi_for(b0).setSelected(True)
    assert _enter_group_edit(page, a0) is True
    assert page._group_overlay.editing is not None, "前置：p0 应已进组编辑态"

    switch(page)

    a1, b1 = _leaf(page, "a1", y=40.0), _leaf(page, "b1", y=60.0)
    _group(page, a1, b1, name="G1")
    return a1, b1


# --- 缺陷一：组编辑态跨页泄漏 ------------------------------------------------

def test_group_edit_state_resets_on_page_add():
    """加页后组编辑态复位 + 整组选中语义恢复（点 a1 → 选中 a1,b1）。

    旧行为：editing 仍指向上页的 G0 ⇒ 点 a1 只选中 ``['a1']``、组框 0 个、
    双击进不去 G1（``on_item_double_clicked`` 直接 False）。
    """
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    a1, _b1 = _make_group_on_p0_then_switch(page, L.LayoutPage._on_page_add)

    assert page._group_overlay.editing is None, "加页后组编辑态必须复位"
    page._gi_for(a1).setSelected(True)      # 只点一个成员，让 selectionChanged 自己跑
    assert _selected_names(page) == ["a1", "b1"], \
        f"新页应按整组选中，实得 {_selected_names(page)}"
    page.deleteLater()


def test_group_edit_state_resets_on_tab_click():
    """点页签切页同样复位（五个页操作的共同汇流点 = _rebuild_scene）。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    a0, b0 = _leaf(page, "a0", y=0.0), _leaf(page, "b0", y=20.0)
    _group(page, a0, b0, name="G0")
    page._gi_for(a0).setSelected(True)
    assert _enter_group_edit(page, a0) is True
    page._on_page_add()                      # p1（空）
    a1, b1 = _leaf(page, "a1", y=40.0), _leaf(page, "b1", y=60.0)
    _group(page, a1, b1, name="G1")
    page._page_bar.setCurrentIndex(0)       # ← 用户点页签回 p0
    assert page.doc.current == 0
    assert page._group_overlay.editing is None, "点页签切页后必须复位"
    page._page_bar.setCurrentIndex(1)       # 再切回 p1
    page._gi_for(a1).setSelected(True)
    assert _selected_names(page) == ["a1", "b1"]
    page.deleteLater()


def test_group_edit_state_resets_on_page_dup():
    """复制页后复位。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    _make_group_on_p0_then_switch(page, L.LayoutPage._on_page_dup)
    assert page._group_overlay.editing is None, "复制页后必须复位"
    page.deleteLater()


def test_group_edit_state_resets_on_page_move():
    """页重排（左移）后复位。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    a0, b0 = _leaf(page, "a0", y=0.0), _leaf(page, "b0", y=20.0)
    _group(page, a0, b0, name="G0")
    page._gi_for(a0).setSelected(True)
    assert _enter_group_edit(page, a0) is True
    page._on_page_add()                      # 现在在 p1，可左移
    a1, b1 = _leaf(page, "a1", y=40.0), _leaf(page, "b1", y=60.0)
    _group(page, a1, b1, name="G1")
    page._gi_for(a1).setSelected(True)
    assert _enter_group_edit(page, a1) is True

    page._on_page_left()                     # ← 页重排

    assert page.doc.current == 0
    assert page._group_overlay.editing is None, "页重排后必须复位"
    page.deleteLater()


def test_group_edit_state_resets_on_page_del():
    """删页后复位（删的是当前页 ⇒ 当前页换了人，编辑态必须跟着作废）。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    _leaf(page, "keep", y=0.0)               # p0 有内容，保证可删 p1
    page._on_page_add()                      # p1
    a1, b1 = _leaf(page, "a1", y=40.0), _leaf(page, "b1", y=60.0)
    _group(page, a1, b1, name="G1")
    page._gi_for(a1).setSelected(True)
    assert _enter_group_edit(page, a1) is True

    page._on_page_del()                      # ← 删当前页 p1

    assert page.doc.current == 0
    assert page._group_overlay.editing is None, "删页后必须复位"
    page.deleteLater()


def test_double_click_enters_new_pages_group_after_switch():
    """复位后双击能进**新页的**组（editing 指向 G1，而不是残留的 G0）。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    a1, _b1 = _make_group_on_p0_then_switch(page, L.LayoutPage._on_page_add)

    assert _enter_group_edit(page, a1) is True, "双击应被组编辑入口消费"
    editing = page._group_overlay.editing
    assert editing is not None and editing.name == "G1", \
        f"editing 应指向 G1，实得 {getattr(editing, 'name', None)}"
    # 编辑态内再双击成员 = 不消费（叶子语义，不重复进组）
    assert _enter_group_edit(page, a1) is False
    page.deleteLater()


def test_group_frames_come_back_after_page_switch():
    """组框（组的唯一可视标识）在复位后重新画出：完整选中 → 1 个框。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    a1, b1 = _make_group_on_p0_then_switch(page, L.LayoutPage._on_page_add)

    page._gi_for(a1).setSelected(True)      # 普通态：点成员自动选整组
    assert _selected_names(page) == ["a1", "b1"]
    assert len(page._group_overlay._frames) == 1, "整组选中后应有 1 个组框"
    # 进组编辑态 ⇒ 组框消失（这是既有语义，不是缺陷）
    assert _enter_group_edit(page, a1) is True
    assert len(page._group_overlay._frames) == 0
    page.deleteLater()


def test_selection_units_back_to_group_level_after_switch():
    """``_selected_units`` 复位后回到「组按容器」口径（拖动/缩放/对齐的语义基础）。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    a1, _b1 = _make_group_on_p0_then_switch(page, L.LayoutPage._on_page_add)
    page._gi_for(a1).setSelected(True)

    units = page._selected_units()
    assert [u.name for u, _c in units] == ["G1"], \
        f"普通态操作单元应是容器 G1，实得 {[u.name for u, _ in units]}"
    page.deleteLater()


# --- 缺陷二：复制一个组，子树要落在最上层之上 ------------------------------

def test_duplicate_group_puts_whole_subtree_on_top():
    """复制一个组 ⇒ 副本的**每个成员**都排在所有原有图元之上，且组内次序保持。"""
    import megapro.gui.layout.layout_page as L
    from megapro.gui.layout.model import Item, iter_leaves

    page = L.LayoutPage()
    p = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="p", z=100.0)
    q = Item(paths=[[(0.0, 10.0), (5.0, 10.0)]], name="q", z=101.0)
    g = Item(name="G", children=[
        Item(paths=[[(0.0, 30.0), (5.0, 30.0)]], name="a1", z=1.0),
        Item(paths=[[(0.0, 40.0), (5.0, 40.0)]], name="b1", z=2.0)])
    top = Item(paths=[[(0.0, 60.0), (5.0, 60.0)]], name="TOP", z=100.5)
    for it in (p, q, g, top):
        page._add_items([it])
    before = {id(x) for x in iter_leaves(page.doc.items)}
    kids = {id(g.children[0]), id(g.children[1])}
    for gi in page._scene_items:
        if id(gi.model_item) in kids:
            gi.setSelected(True)

    page._duplicate()

    new_leaves = [it for it in iter_leaves(page.doc.items)
                  if id(it) not in before]
    assert len(new_leaves) == 2, "副本应是组内的两个成员"
    order = sorted(iter_leaves(page.doc.items), key=lambda i: i.z)
    pos = {id(it): k for k, it in enumerate(order)}
    old_pos = [pos[id(it)] for it in iter_leaves(page.doc.items)
               if id(it) in before]
    # 判别点：旧行为下副本成员 z 仍是 1/2，会被压到 p/TOP/q 之下
    assert min(pos[id(n)] for n in new_leaves) > max(old_pos), \
        f"副本成员未落到最上：序 {[(it.name, it.z) for it in order]}"
    # 组内相对次序保持（a1 在 b1 之上）
    assert [it.name for it in sorted(new_leaves, key=lambda i: i.z)] == ["a1", "b1"]
    page.deleteLater()


def test_paste_group_puts_whole_subtree_on_top():
    """粘贴一个组 ⇒ 与复制同口径（整棵子树在最上）。"""
    import megapro.gui.layout.layout_page as L
    from megapro.gui.layout.model import Item, iter_leaves

    page = L.LayoutPage()
    top = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="TOP", z=100.0)
    page._add_items([top])
    g = Item(name="G", children=[
        Item(paths=[[(0.0, 30.0), (5.0, 30.0)]], name="a1", z=1.0),
        Item(paths=[[(0.0, 40.0), (5.0, 40.0)]], name="b1", z=2.0)])
    page._add_items([g])
    kids = {id(g.children[0]), id(g.children[1])}
    for gi in page._scene_items:
        if id(gi.model_item) in kids:
            gi.setSelected(True)
    before = {id(x) for x in iter_leaves(page.doc.items)}

    page._copy_selected()
    page._paste()

    new_leaves = [it for it in iter_leaves(page.doc.items)
                  if id(it) not in before]
    assert len(new_leaves) == 2
    order = sorted(iter_leaves(page.doc.items), key=lambda i: i.z)
    pos = {id(it): k for k, it in enumerate(order)}
    old_pos = [pos[id(it)] for it in iter_leaves(page.doc.items)
               if id(it) in before]
    assert min(pos[id(n)] for n in new_leaves) > max(old_pos)
    page.deleteLater()


def test_duplicate_scattered_item_still_lands_on_top():
    """粘贴/复制**散件**的既有行为不变（别把这条改坏）。"""
    import megapro.gui.layout.layout_page as L
    from megapro.gui.layout.model import Item, iter_leaves

    page = L.LayoutPage()
    a = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="a", z=3.0)
    b = Item(paths=[[(0.0, 10.0), (5.0, 10.0)]], name="b", z=7.0)
    page._add_items([a, b])
    page._gi_for(a).setSelected(True)
    before = {id(x) for x in iter_leaves(page.doc.items)}

    page._duplicate()

    new_leaves = [it for it in iter_leaves(page.doc.items)
                  if id(it) not in before]
    assert len(new_leaves) == 1
    order = sorted(iter_leaves(page.doc.items), key=lambda i: i.z)
    assert order[-1] is new_leaves[0], "散件副本仍应落在最上"
    assert new_leaves[0].z == pytest.approx(8.0)  # top_z(7)+1，与旧实现逐位一致
    page.deleteLater()


def test_restack_preserves_nested_group_relative_order():
    """组**套组**：操作单元是内层组（既有语义），其叶子照样整棵抬到最上。

    这里内层容器 z=0、外层 z=0，成员 z=1/2 —— 正是「容器 z 不参与拍平排序」
    的坑：只看容器 z 会以为副本已经在最上，实际成员会掉回 TOP 之下。
    """
    import megapro.gui.layout.layout_page as L
    from megapro.gui.layout.model import Item, iter_leaves

    page = L.LayoutPage()
    top = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="TOP", z=50.0)
    page._add_items([top])
    inner = Item(name="IN", children=[
        Item(paths=[[(0.0, 10.0), (5.0, 10.0)]], name="i1", z=1.0),
        Item(paths=[[(0.0, 20.0), (5.0, 20.0)]], name="i2", z=2.0)])
    outer = Item(name="OUT", children=[
        inner,
        Item(paths=[[(0.0, 30.0), (5.0, 30.0)]], name="o1", z=3.0)])
    page._add_items([outer])
    page._gi_for(inner.children[0]).setSelected(True)   # 普通态点成员 → 选内层整组
    before = {id(x) for x in iter_leaves(page.doc.items)}

    page._duplicate()

    new_leaves = [it for it in iter_leaves(page.doc.items)
                  if id(it) not in before]
    assert [it.name for it in new_leaves] == ["i1", "i2"], \
        "普通态的操作单元是内层容器，副本即内层组的两个成员"
    order = sorted(iter_leaves(page.doc.items), key=lambda i: i.z)
    pos = {id(it): k for k, it in enumerate(order)}
    old_pos = [pos[id(it)] for it in iter_leaves(page.doc.items)
               if id(it) in before]
    assert min(pos[id(n)] for n in new_leaves) > max(old_pos), \
        f"副本成员未落到最上：{[(it.name, it.z) for it in order]}"
    assert [it.name for it in sorted(new_leaves, key=lambda i: i.z)] == \
        ["i1", "i2"], "组内相对次序必须保持"
    page.deleteLater()
