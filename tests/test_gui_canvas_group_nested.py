"""B2 回归：**组套组**时每个容器都要画出自己的组框（含嵌套任意深度）。

缺陷（本批证据，修复前可复现）：``GroupOverlay._complete_groups`` 只遍历顶层
``doc.items``，且判据是「容器的**直接子项**全部落在叶子选择集里」。组套组时
外层容器的直接子项是**内层容器**（容器不建 PathItem、不进选择集 ⇒ 永远不在
叶子集里）⇒ 判据恒不成立；而内层容器不在 ``doc.items`` 里 ⇒ 永不被考察。
实跑：3 个图元 → ``group_selected()``（组框 1，正常）→ 再 ``group_selected()``
（用户「整组选中再点编组」，``layout_page.py:483`` 是设计内行为）⇒
顶层 ``['组3']``、其 children ``['组3']``、3 个叶子全选，而 **组框数 = 0**。

修法（``group_overlay.py`` 内，不动画布语义）：
- 枚举面走 :func:`iter_units`（任意深度，带 ``MAX_TREE_DEPTH`` 守卫），不再
  只看 ``doc.items`` 一层；
- 判据换成对嵌套成立的等价形式：**子树叶子的传递闭包 ⊆ 当前叶子选择集**
  （深度 1 时与旧判据逐位等价）；
- 组框矩形仍走 ``page_box``（= ``layout_page._page_box`` → ``iter_units`` 的
  祖先链），嵌套下天然带祖先变换。

另修同文件里让「框的矩形 == 容器 page_bbox」这条不变量当场失效的那处：
``update_sizes`` 的 ``setScale(1/ppm)`` 是**几何**缩放（注释「不改变几何」是
错的），而组框的 pen 已是 cosmetic（``set_width(0)``）本就随缩放恒 1px。

断言走**用户可见的量**：框在场景系里真正画出来的矩形 vs 模型 page_bbox 的
逐位吻合，以及选中/编组/点选这些真实 UI 入口的结果。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# --- 脚手架 -----------------------------------------------------------------

def _page():
    import megapro.gui.layout.layout_page as L

    return L.LayoutPage()


def _leaf(page, name, *, x=10.0, y=10.0, w=30.0, h=8.0, z=1.0):
    """加一个**闭合矩形**图元（矩形才有非退化命中区，``PathItem.shape``
    = 本地 bbox 矩形，见 ``canvas/items.py:65``）。"""
    from megapro.gui.layout.model import Item

    it = Item(paths=[[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]],
              name=name, z=z)
    page._add_items([it])
    return it


def _add_three(page):
    """3 个图元并**全选**（复现步骤的前置：先选中才能编组）。"""
    s = [_leaf(page, "s1", y=10.0, z=1.0),
         _leaf(page, "s2", y=30.0, z=2.0),
         _leaf(page, "s3", y=50.0, z=3.0)]
    for it in s:
        page._gi_for(it).setSelected(True)
    assert _selected_names(page) == ["s1", "s2", "s3"]
    return s


def _containers(page):
    """整棵树里的全部容器，DFS 前序（外层在前）—— 不依赖 overlay 内部结构。"""
    from megapro.gui.layout.model import iter_units

    return [it for it, _chain in iter_units(page.doc.items, visible_only=False)
            if it.is_container()]


def _frames(page):
    return list(page._group_overlay._frames)


def _drawn_rect(page, frame):
    """框在**场景系**里真正画出来的矩形（含框自身变换）—— 用户看到的那个框。"""
    return frame.mapRectToScene(frame._rect)


def _selected_names(page):
    return sorted(gi.model_item.name for gi in page._selected())


def _click_mm(page, x, y):
    """真实鼠标点击（视口 px 浮点通道，同 test_gui_layout_group.py 的做法）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    vp = page.view.viewportTransform().map(QPointF(float(x), float(y))).toPoint()
    QTest.mouseClick(page.view.viewport(), Qt.LeftButton, pos=vp)


# --- 缺陷：二次编组后一个组框都不画 -----------------------------------------

def test_nested_group_draws_one_frame_per_container():
    """真实 UI 复现路径：3 图元 → 编组 → 整组已全选再编组 ⇒ 2 层 2 个框。

    旧行为：``_complete_groups`` 只看顶层 + 判据用直接子项 ⇒ ``rects=[]``。
    """
    page = _page()
    _add_three(page)
    page.group_selected()                       # 第 1 次编组
    assert len(_frames(page)) == 1, "前置：平铺一组 = 1 个框"

    page.group_selected()                       # 第 2 次编组（整组已全选）
    conts = _containers(page)
    assert len(conts) == 2, f"前置：应有两层容器，实得 {len(conts)}"
    assert conts[0].is_container() and conts[0].children[0].is_container(), \
        "前置：某层容器的直接子项是一个**容器**（这正是旧判据失效的形状）"
    assert _selected_names(page) == ["s1", "s2", "s3"], "前置：三个叶子全选"

    assert len(_frames(page)) == 2, \
        f"嵌套后每层容器都要有框，实得 {len(_frames(page))}"
    page.deleteLater()


def test_each_frame_rect_matches_its_container_page_bbox():
    """每个框画在**场景系**的位置必须与对应容器的 page_bbox 逐位吻合。

    这里同时钉住「框 ↔ 容器」的对应关系（``GroupFrameItem.container``），
    以及「不得有几何缩放」——旧 ``update_sizes`` 的 ``setScale(1/ppm)`` 会把框
    缩到 1/ppm 并朝页原点平移，与 page_bbox 完全不吻合。
    """
    page = _page()
    _add_three(page)
    page.group_selected()
    page.group_selected()

    by_container = {id(f.container): f for f in _frames(page)}
    assert len(by_container) == 2, "每个框都要能对应回它的容器"
    for cont in _containers(page):
        frame = by_container.get(id(cont))
        assert frame is not None, f"容器 {cont.name!r} 没有组框"
        want = page._page_box(cont)             # 模型真值（含祖先链）
        got = _drawn_rect(page, frame)
        assert (got.x(), got.y()) == pytest.approx((want[0], want[1]), abs=1e-9)
        assert (got.width(), got.height()) == pytest.approx(
            (want[2] - want[0], want[3] - want[1]), abs=1e-9)
    page.deleteLater()


def test_closure_predicate_separates_inner_from_outer():
    """判据必须是**传递闭包**而不是「直接子项」：outer=[inner(a,b), c] 全选。

    选集 = {a,b,c}。内层（叶子闭包 {a,b}）与外层（叶子闭包 {a,b,c}）都完整
    ⇒ 两个框。旧判据下外层的直接子项是 [inner, c]，inner 不在叶子集里 ⇒
    **0 个框**（内层 container 也不在 ``doc.items`` 里，同样考察不到）。

    「外层 = [容器, 散件]」这一形状走 :meth:`Page.group_items` 构造 ——
    ``group_selected()`` 的成员恒为**叶子集**（``layout_page.py:507``），所以
    二次编组得到的是「新容器套进旧容器」的另一形状（见上一个用例），走不到
    混成员这条路；``model.py:782`` 明确支持成员是 mix，这里直接用模型入口。
    """
    page = _page()
    a = _leaf(page, "a", y=10.0, z=1.0)
    b = _leaf(page, "b", y=20.0, z=2.0)
    c = _leaf(page, "c", y=90.0, z=3.0)
    page._gi_for(a).setSelected(True)
    page._gi_for(b).setSelected(True)
    page.group_selected()                       # inner = [a, b]
    inner = _containers(page)[-1]
    assert [x.name for x in inner.children] == ["a", "b"], "前置：inner = [a,b]"

    outer = page.doc.group_items([inner, c], name="外层")
    assert outer is not None, "前置：模型应接受「容器 + 散件」的混成员编组"
    page._rebuild_scene()
    for it in (a, b, c):
        page._gi_for(it).setSelected(True)
    assert _selected_names(page) == ["a", "b", "c"], "前置：三个叶子全选"
    assert inner in outer.children and c in outer.children, \
        "前置：outer 的直接子项 = [容器 inner, 散件 c]"

    assert len(_frames(page)) == 2, \
        f"inner 与 outer 的叶子闭包都完整 ⇒ 2 个框，实得 {len(_frames(page))}"
    assert {id(f.container) for f in _frames(page)} == {id(inner), id(outer)}
    page.deleteLater()


def test_incomplete_leaf_set_draws_no_frame():
    """叶子集不完整 ⇒ 涉及的容器都不画框（不能画半个框）。

    ⚠ 「组内只选一部分」在普通选择态**不可达**（FR-03：点组内任一成员 ⇒
    ``_expand_group_selection`` 立刻补齐整组），所以这里屏蔽 selectionChanged
    直接摆出「选了 2/3 个叶子」的叶子集，再走 overlay 自己的入口 ``refresh``
    —— 钉的是 :meth:`GroupOverlay.sync` 的判据，不是 UI 可达性。
    """
    page = _page()
    s = _add_three(page)
    page.group_selected()
    page.group_selected()                       # 两层

    page.scene.blockSignals(True)
    try:
        page._gi_for(s[2]).setSelected(False)
    finally:
        page.scene.blockSignals(False)
    assert _selected_names(page) == ["s1", "s2"]
    page._group_overlay.refresh(page._page_box)
    assert _frames(page) == [], "叶子集不完整时不得画框"
    page.deleteLater()


# --- 任意深度 ---------------------------------------------------------------

def test_three_level_nesting_draws_every_container():
    """连编三次 ⇒ 3 层 3 个框（不是只画最外层，也不是 0 个）。"""
    page = _page()
    _add_three(page)
    for _ in range(3):
        page.group_selected()
    assert len(_containers(page)) == 3, f"前置：三层容器，实得 {len(_containers(page))}"
    assert len(_frames(page)) == 3, f"三层容器都要有框，实得 {len(_frames(page))}"
    page.deleteLater()


# --- 命中/选中语义未被改动（要求 4） -----------------------------------------

def test_clicking_inner_group_member_still_selects_whole_group():
    """点内层组的成员仍选中整组（真实鼠标点击 → ``_expand_group_selection``）。"""
    page = _page()
    _add_three(page)
    page.group_selected()
    page.group_selected()                       # 两层嵌套
    assert _selected_names(page) == ["s1", "s2", "s3"]

    # 清空选择（模拟「先点空白」）。⚠ 必须屏蔽 selectionChanged：否则
    # `_expand_group_selection`（FR-03）会立刻把整组补选回来，清不掉。
    page.scene.blockSignals(True)
    try:
        for gi in page._scene_items:
            gi.setSelected(False)
    finally:
        page.scene.blockSignals(False)
    assert _selected_names(page) == []

    _click_mm(page, 25.0, 14.0)                 # 点 s1 矩形中心内一点
    assert _selected_names(page) == ["s1", "s2", "s3"], \
        f"点内层组成员应选中整组，实得 {_selected_names(page)}"
    assert len(_frames(page)) == 2, "选中整组后两层框都要在"
    page.deleteLater()


def test_group_edit_state_still_hides_frames_in_nested_tree():
    """组编辑态（双击进组）下不画框 —— 嵌套后仍成立（不得回归）。"""
    page = _page()
    s = _add_three(page)
    page.group_selected()
    page.group_selected()
    assert page.on_item_double_clicked(s[0]) is True, "前置：应进组编辑态"
    assert _frames(page) == [], "组编辑态下操作单元 = 叶子，不画框"
    page.deleteLater()
