"""D4 回归：置底真的沉到最底 + 组内 top/bottom 保持组内层序。

两条缺陷（终审冷读实测，本文件逐条复现）：

① **置底仍与未选中项并列**（AGENTS.md A-4 原场景：X z=1 未选、M0 z=2、
   M1 z=3、Y z=4，选 M0+M1 置底）::

       z = [('M0', 0.0), ('M1', 1.0), ('X', 1.0), ('Y', 4.0)]
                              ^^^^^ M1 与未选中的 X 并列 1.0

   X 被切在两个选中件**中间**。根因：``_zorder`` 置底 ``bottom_z() - 1``
   起手，但铺开时 ``cursor += 1`` **只增不减** ⇒ 置底的游标其实在往上爬，
   跨过 ``bottom_z`` 就撞进未选中项占住的区间。置顶安全（游标向上、必在
   全体之上），洞只在置底。

② **组内置顶/置底翻转组内切割次序**（A-11）：X(z=3) 先画、Y(z=2) 后画
   → 编组（children 文档序 ``[X, Y]``）→ 整组置顶 ⇒ z 变成
   ``[('X', 4.0), ('Y', 5.0)]``，切割序从 ``[Y, X]`` 翻成 ``[X, Y]``。
   X 是先画的（z=3 > Y 的 2），置顶后 X 反而先切。

   根因：铺开时**按 ``iter_leaves`` 的文档序**分配游标，而文档序与 z 序
   可以相反（文档序 = 入模/挂进容器的顺序，z 序 = 画上去的顺序）。于是
   「保持选中前相对层序」这条承诺在**组内**口径上被证伪。

**为什么两条都要真判据**：本文件一律拿 ``flatten_visible`` 的实际几何
次序（= 发往机器的切割次序）说话，不看 z 也不看场景选中态 —— z 断言抓不住
②（z 确实单调了，错的是**谁在前**），而这正是会动刀的那一半。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets                            # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.layout.layout_page import LayoutPage      # noqa: E402
from megapro.gui.layout.model import (                     # noqa: E402
    Item, flatten_visible, iter_ancestors, iter_leaves, unit_paths,
)


# -- 观测辅助（独立真值：拍平链 = 切割次序）--------------------------------

def _cut_order(page):
    """实际切割次序 → ``[图元名, ...]``。

    做法：先用 ``unit_paths`` 把每条几何标上**拥有它的叶子名**，再按
    ``flatten_visible`` 的输出顺序取名。判据取自拍平链（**发往机器的那份
    几何**），不是 z、也不是场景选中态。
    """
    owner: dict = {}
    for leaf, chain in iter_ancestors(page.doc.items):
        if leaf.children:
            continue
        for p in unit_paths(leaf, chain):
            if len(p) >= 2:
                key = tuple(tuple(pt) for pt in p)
                owner.setdefault(key, leaf.name)
    out = []
    for p in flatten_visible(page.doc):
        key = tuple(tuple(pt) for pt in p)
        name = owner.get(key)
        assert name is not None, f"拍平里出现了一条找不到主人的几何：{p}"
        out.append(name)
    return out


def _zs(page) -> dict:
    """``{图元名: z}``（只叶子；切割只认叶子 z）。"""
    return {it.name: it.z for it in iter_leaves(page.doc.items)}


def _page_with(items):
    lp = LayoutPage()
    lp.snap_enabled = False
    lp._add_items(items)
    lp._rebuild_scene()
    lp._after_change()
    return lp


def _tri(x, y, name):
    """一条几何唯一的三角（不同 y ⇒ 拍平里可逐条配对，不会同名混淆）。"""
    return Item(paths=[[(x, y), (x + 8.0, y), (x + 8.0, y + 4.0)]],
                pos=(0.0, 0.0), name=name)


def _select(page, names):
    for gi in page._scene_items:
        gi.setSelected(gi.model_item.name in names)
    page._on_selection_changed()


# -- ① 置底：选中项占一段连续且**严格低于**所有未选中项 --------------------

def test_bottom_puts_selection_below_every_unselected(lp_a4):
    """A-4 原场景：选 M0+M1 置底 ⇒ 两者严格低于未选中的 X 与 Y。

    修复前实测：``z = [('M0',0.0), ('M1',1.0), ('X',1.0), ('Y',4.0)]``
    —— M1 与未选中的 X **并列 1.0**，X 被切在两个选中件中间。
    """
    page = lp_a4
    page._zorder("bottom")          # 真正的操作
    z = _zs(page)
    sel = [z["M0"], z["M1"]]
    unsel = [z["X"], z["Y"]]

    assert max(sel) < min(unsel), (
        f"置底后选中项未严格低于所有未选中项：{z} —— "
        f"max(sel)={max(sel)} 应 < min(unsel)={min(unsel)}")
    assert len(set(sel)) == 2, f"选中项内部 z 交叠：{z}"
    assert max(sel) - min(sel) == 1.0, f"选中项不是连续一段：{z}"


def test_bottom_cut_order_keeps_selection_contiguous_at_front(lp_a4):
    """**机器后果**：拍平（=切割次序）里选中项必须整块在最前、不被穿插。"""
    page = lp_a4
    page._zorder("bottom")          # 真正的操作
    order = _cut_order(page)
    first_two = order[:2]
    assert sorted(first_two) == ["M0", "M1"], (
        f"切割次序最前面两项不是选中集：{order} —— 未选中的 X/Y 被切进了选中块里")
    # M0 z=2 < M1 z=3 ⇒ 置底后仍 M0 先切（相对层序保持）
    assert first_two == ["M0", "M1"], f"组内/跨项相对层序被置底打乱：{order}"


def test_bottom_does_not_tie_with_unselected(lp_a4):
    """并列 z 是本缺陷的直接症状，单独钉一条（可读的失败信息）。"""
    page = lp_a4
    page._zorder("bottom")          # 真正的操作
    z = _zs(page)
    assert z["M1"] != z["X"], (
        f"置底后 M1 与未选中的 X 仍并列 z={z['M1']}（并列 ⇒ 切割次序由 order "
        f"决胜，X 被切在两个选中件中间）")


def test_bottom_still_one_undo_command(lp_a4):
    """单命令契约不变：一次撤销把**所有**选中项一起还原。"""
    page = lp_a4
    before = dict(_zs(page))
    n0 = page._undo.count()

    page._zorder("bottom")

    assert page._undo.count() == n0 + 1, \
        f"置底应只推一条命令，实际推了 {page._undo.count() - n0} 条"
    page._undo.undo()
    assert _zs(page) == before, "一次撤销未把全部选中项还原"


# -- ② 组内 top/bottom：保持组内层序（先画的还在先切的位次）---------------

@pytest.fixture
def lp_group_inverted():
    """X(z=3) 先画、Y(z=2) 后画 → 编组（children 文档序 ``[X, Y]``）。

    **故意让文档序与 z 序相反**：X 的 z 更高（= 画得更晚、应该后切），
    却排在 children 文档序的**前面**。铺开若按文档序分配游标，X 就会拿到
    更小的 z、被切到 Y 前面 —— 这正是 A-11。
    """
    x = _tri(25.0, 5.0, "X")
    y = _tri(5.0, 25.0, "Y")
    x.z, y.z = 3.0, 2.0
    page = _page_with([x, y])
    g = page.doc.group_items([x, y])
    assert g is not None
    page._rebuild_scene()
    page._after_change()
    assert [c.name for c in g.children] == ["X", "Y"], \
        "前置：children 文档序应为 [X, Y]（与 z 序相反）"
    return page, g


def test_group_internal_cut_order_preserved_by_top(lp_group_inverted):
    """整组置顶 ⇒ **组内**切割次序与置顶前逐位一致（A-11）。

    修复前实测：置顶后 z 变 ``[('X',4.0), ('Y',5.0)]``，切割序从
    ``[Y, X]`` 翻成 ``[X, Y]`` —— X 先画却被切到前面。
    """
    page, g = lp_group_inverted
    before = _cut_order(page)
    assert before == ["Y", "X"], f"前置：置顶前切割序应为 [Y, X]（X 的 z 更高）"

    _select(page, {"X", "Y"})
    page._zorder("top")

    assert _cut_order(page) == before, (
        f"整组置顶翻转了组内切割次序：{before} -> {_cut_order(page)} —— "
        f"先画的 X 被切到了 Y 前面")
    z = _zs(page)
    assert z["Y"] < z["X"], f"组内 z 序被翻转：{z}（Y 应当仍先切）"


def test_group_internal_cut_order_preserved_by_bottom(lp_group_inverted):
    """整组置底同样不得翻转组内次序（同一根因，另一个方向）。"""
    page, g = lp_group_inverted
    before = _cut_order(page)

    _select(page, {"X", "Y"})
    page._zorder("bottom")

    assert _cut_order(page) == before, (
        f"整组置底翻转了组内切割次序：{before} -> {_cut_order(page)}")


def test_group_block_still_contiguous_after_top(lp_group_inverted):
    """组仍占**一段连续** z（FR-06 整组连续），不被未选中项插进来。"""
    page, g = lp_group_inverted
    page._add_items([_tri(80.0, 80.0, "OUT")])   # 一个未选中项，z 更高
    page._rebuild_scene()
    page._after_change()

    _select(page, {"X", "Y"})
    page._zorder("top")

    z = _zs(page)
    block = sorted([z["X"], z["Y"]])
    assert block[1] - block[0] == 1.0, f"组内 z 不再连续：{z}"
    assert min(block) > z["OUT"], f"选中组未整体高于未选中项：{z}"


def test_group_undo_restores_everything(lp_group_inverted):
    """撤销后 z 与切割次序**逐位**复原（单命令契约）。"""
    page, g = lp_group_inverted
    z_before = dict(_zs(page))
    order_before = _cut_order(page)
    n0 = page._undo.count()

    _select(page, {"X", "Y"})
    page._zorder("top")
    assert page._undo.count() == n0 + 1, "置顶应只推一条命令"

    page._undo.undo()
    assert _zs(page) == z_before, f"撤销后 z 未复原：{z_before} -> {_zs(page)}"
    assert _cut_order(page) == order_before, "撤销后切割次序未复原"


def test_top_for_ungrouped_items_still_above(lp_a4):
    """**不回归**：置顶（散件）仍严格高于未选中项并占连续一段。"""
    page = lp_a4
    _select(page, {"M0", "M1"})
    page._zorder("top")
    z = _zs(page)
    sel = [z["M0"], z["M1"]]
    assert min(sel) > max(z["X"], z["Y"]), f"置顶未整体高于未选中：{z}"
    assert max(sel) - min(sel) == 1.0, f"置顶选中项不连续：{z}"


def test_up_down_unaffected(lp_a4):
    """**不回归**：up/down 语义不变（各单元按自身 z 平移 ±1）。"""
    page = lp_a4
    before = dict(_zs(page))
    _select(page, {"M0", "M1"})
    page._zorder("up")
    z = _zs(page)
    assert z["M0"] == before["M0"] + 1.0 and z["M1"] == before["M1"] + 1.0, \
        f"up 应让每个单元各 +1：{before} -> {z}"


# -- fixture ---------------------------------------------------------------

@pytest.fixture
def lp_a4():
    """A-4 原场景：X z=1（未选）、M0 z=2、M1 z=3（选中）、Y z=4。

    几何各占不同 y，保证拍平里能逐条配对到图元名。
    """
    items = [
        _tri(5.0, 5.0, "X"),      # z=1，未选中 —— **在选中项之下**
        _tri(15.0, 15.0, "M0"),    # z=2
        _tri(25.0, 25.0, "M1"),    # z=3
        _tri(35.0, 35.0, "Y"),     # z=4
    ]
    for it, z in zip(items, (1.0, 2.0, 3.0, 4.0)):
        it.z = z
    page = _page_with(items)
    _select(page, {"M0", "M1"})
    return page
