"""B2 独立回归：**组套组**时每个容器都要画出组框（offscreen，独立 QApplication）。

缺陷（本批证据，可复现）：`canvas/group_overlay.py` 的 `_complete_groups`
旧实现只遍历**顶层** ``doc.items``，且判据是「容器的**直接子项**全部落在叶子
选择集里」。两层嵌套后：外层容器的直接子项是 ``[内层容器, ...]``，而容器永远
不在叶子选择集里（选择集天然是叶子集，见 `layout_page._group_selected_items`）
⇒ 外层判据恒 False；内层容器又不在 ``doc.items`` 里 ⇒ 永不被考察。实跑：3 个
图元连续编组两次 ⇒ 顶层 ``['组3']``/children ``['组3']``、3 叶子全选，而
**组框数 0**。

本文件是**独立**于并行线那份 `test_gui_canvas_group_nested.py` 的第二道回归
（断言口径不同：框的**存储矩形** vs 容器 page_bbox 的集合相等、单层语义与
旧判据逐子集等价、内层完整/外层残缺的选择性、橡皮筋框选入口）。

**状态**：该缺陷已由并行线在 commit ``2a66414`` 修掉（``_complete_groups``
改走 ``iter_units`` + 叶子传递闭包），本文件是**独立复核**——把该方法单独回退
成旧判据后，下面 6 个嵌套用例即失败（见 proof）。

选择集一律用**真实鼠标事件**建立（点空白清空 → 点第一个 → Ctrl+点其余）：
程序化 `setSelected` 会与 `_expand_group_selection` 的「点一个 = 选整组」互相
打架（取消选中一个成员时兄弟立刻被重新选中），测不出真实语义。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

#: 床上一个保证空白的位置（``view.fit()`` 保证整床在视口内 ⇒ 该点可点）
_EMPTY = (200.0, 200.0)


# --- 脚手架 -----------------------------------------------------------------

def _page():
    import megapro.gui.layout.layout_page as L

    return L.LayoutPage()


def _leaf(page, name, *, y, x=0.0, z=1.0):
    """闭合矩形图元 —— 矩形才有**非退化命中区**（``PathItem.shape`` = 本地 bbox；
    两条点的线只有 1e-6mm 高，鼠标点不中）。"""
    from megapro.gui.layout.model import Item

    it = Item(paths=[[(x, y), (x + 10.0, y), (x + 10.0, y + 5.0),
                      (x, y + 5.0), (x, y)]], name=name, z=z)
    page._add_items([it])
    return it


def _click_mm(page, x_mm, y_mm, *, ctrl=False):
    """真实单击（mm → 视口 px 浮点通道）：点空白=清空，点图元=选中/Ctrl 加选。

    PySide6 的 ``QTest.mousePress`` 不收关键字 ``modifier``（只吃位置参）。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    pos = page.view.viewportTransform().map(QPointF(float(x_mm), float(y_mm))).toPoint()
    mods = Qt.ControlModifier if ctrl else Qt.NoModifier
    QTest.mousePress(page.view.viewport(), Qt.LeftButton, mods, pos)
    QTest.mouseRelease(page.view.viewport(), Qt.LeftButton, mods, pos)


def _center_mm(page, item):
    x0, y0, x1, y1 = page._page_box(item)
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _select_by_clicks(page, expect, *targets, clear=True):
    """真实点击建选择集；断言结果 == ``expect``（名字序）。

    ``clear=True``（默认）：点空白清空 → 点 targets[0] → Ctrl+点其余。
    ``clear=False``：保留现有选择，全部 Ctrl+点（编组后组已全选，要**加选**
    散件时用 —— 不带 Ctrl 的第一次点击会把整组选择清掉）。
    """
    if clear:
        _click_mm(page, *_EMPTY)
    for i, t in enumerate(targets):
        _click_mm(page, *_center_mm(page, t), ctrl=(i > 0 or not clear))
    got = _selected_names(page)
    assert got == sorted(expect), f"前置：点击序列应选出 {sorted(expect)}，实得 {got}"


def _selected_names(page):
    return sorted(gi.model_item.name for gi in page._selected())


def _group_now(page, expect_leaves):
    """走真实 UI 入口 :meth:`LayoutPage.group_selected`（不直接调 doc.group_items）。"""
    assert _selected_names(page) == sorted(expect_leaves), "前置：叶子已全选"
    page.group_selected()
    return page._undo.undoText()      # 仅确认命令真的推了栈


def _containers(page):
    from megapro.gui.layout.model import iter_items

    return [it for it in iter_items(page.doc.items) if it.is_container()]


def _frames(page):
    return page._group_overlay._frames


def _frame_boxes(page):
    """组框画出的矩形（x0, y0, w, h），排序后返回（与容器顺序无关）。"""
    return sorted((f._rect.x(), f._rect.y(), f._rect.width(), f._rect.height())
                  for f in _frames(page))


def _container_boxes(page):
    """容器 page_bbox（x0, y0, w, h），走页面系真相通道 ``_page_box``。"""
    out = []
    for c in _containers(page):
        x0, y0, x1, y1 = page._page_box(c)
        out.append((x0, y0, x1 - x0, y1 - y0))
    return sorted(out)


def _box_of(page, item):
    x0, y0, x1, y1 = page._page_box(item)
    return (x0, y0, x1 - x0, y1 - y0)


# --- 核心：嵌套组每个容器一个框 ---------------------------------------------

def test_nested_group_draws_one_frame_per_container():
    """本批缺陷的复现路径：3 图元 → 编组 → 整组已全选再编组 ⇒ 2 层 2 个框。"""
    page = _page()
    a = _leaf(page, "a", y=0.0)
    b = _leaf(page, "b", y=20.0)
    c = _leaf(page, "c", y=40.0)

    _select_by_clicks(page, ["a", "b", "c"], a, b, c)
    _group_now(page, ["a", "b", "c"])
    assert len(_containers(page)) == 1, "前置：一次编组后 1 个容器"
    assert len(_frames(page)) == 1, "前置：单层组框 = 1"

    _group_now(page, ["a", "b", "c"])        # 「整组选中 → 再点编组」= 组套组
    conts = _containers(page)
    assert len(conts) == 2, f"前置：应为两层嵌套，实得 {len(conts)}"
    outer = [c_ for c_ in conts
             if any(ch.is_container() for ch in c_.children)][0]
    assert len(outer.children) == 1 and outer.children[0].is_container(), \
        "前置：外层的直接子项就是内层容器（旧判据正是在这里恒 False）"

    assert len(_frames(page)) == len(conts) == 2, \
        f"每个容器都应有组框，实得 {len(_frames(page))}"
    page.deleteLater()


def test_nested_group_frame_rects_match_container_page_bbox():
    """每个组框的矩形与**对应容器**的 page_bbox 吻合（集合相等，不依赖顺序）。"""
    page = _page()
    a, b, c, d = (_leaf(page, n, y=20.0 * i) for i, n in enumerate("abcd"))
    _select_by_clicks(page, ["a", "b", "c"], a, b, c)
    _group_now(page, ["a", "b", "c"])                       # 内层
    _select_by_clicks(page, ["a", "b", "c", "d"], d, clear=False)  # 加选散件 d
    _group_now(page, ["a", "b", "c", "d"])                  # 外层（组套组）

    assert len(_containers(page)) == 2
    assert len(_frames(page)) == 2
    assert _frame_boxes(page) == pytest.approx(_container_boxes(page)), \
        "组框矩形必须与容器页面系 bbox 逐位吻合"
    page.deleteLater()


def test_three_level_nesting_draws_all_frames():
    """三层嵌套 ⇒ 3 个容器 ⇒ 3 个框（传递闭包判据对任意深度成立）。"""
    page = _page()
    leaves = {n: _leaf(page, n, y=20.0 * i) for i, n in enumerate("abcdef")}
    _select_by_clicks(page, ["a", "b"], leaves["a"], leaves["b"])
    _group_now(page, ["a", "b"])                             # L3
    _select_by_clicks(page, ["a", "b", "c", "d"],
                      leaves["c"], leaves["d"], clear=False)
    _group_now(page, ["a", "b", "c", "d"])                   # L2
    _select_by_clicks(page, ["a", "b", "c", "d", "e", "f"],
                      leaves["e"], leaves["f"], clear=False)
    _group_now(page, list("abcdef"))                         # L1

    assert len(_containers(page)) == 3
    assert len(_frames(page)) == 3, \
        f"三层嵌套应有 3 个框，实得 {len(_frames(page))}"
    assert _frame_boxes(page) == pytest.approx(_container_boxes(page))
    page.deleteLater()


# --- 判据：内层完整即画内层，外层残缺就不画外层 -----------------------------

def test_only_complete_inner_group_drawn_when_outer_partial():
    """内层完整 / 外层缺一个成员 ⇒ **只**画内层框（部分选中绝不画半个框）。

    形状 ``外层 = [内层容器, 散件 c]``（混合容器）：先 UI 编组得到
    ``{a,b,c}``，再用 :meth:`Page.group_items` 对**子集** ``{a,b}`` 再编一层
    —— 新容器落在首成员原位，于是外层留下 ``[内层, c]``。

    **为什么不用鼠标走完这条路径**：真实 UI 造不出「组内只选一个」的选择 ——
    ``_expand_group_selection`` 在每次 ``selectionChanged`` 时都把同容器兄弟
    补齐，Ctrl 取消一个成员会立刻被加回。故这里直接驱动
    :meth:`GroupOverlay.sync` 传显式叶子集（叶子集 = 选择集的等价物）。
    """
    page = _page()
    a = _leaf(page, "a", y=0.0)
    b = _leaf(page, "b", y=20.0)
    c = _leaf(page, "c", y=40.0)
    _select_by_clicks(page, ["a", "b", "c"], a, b, c)
    _group_now(page, ["a", "b", "c"])                  # 外层 = {a, b, c}
    outer = _containers(page)[0]
    inner = page.doc.group_items([a, b], name="内层")
    assert inner is not None
    assert outer.children == [inner, c], \
        f"前置：外层 = [内层容器, 散件 c]，实得 {[k.name for k in outer.children]}"

    # 只选内层两个叶子 ⇒ 内层完整、外层残缺 ⇒ 1 个框，且是**内层**那个
    page._group_overlay.sync([a, b], page_box=page._page_box)
    assert len(_frames(page)) == 1, \
        f"外层残缺时只该画内层一个框，实得 {len(_frames(page))}"
    assert [f.container for f in _frames(page)] == [inner]
    assert _frame_boxes(page) == pytest.approx([_box_of(page, inner)])

    # 三个叶子全选 ⇒ 两层都完整 ⇒ 2 个框
    page._group_overlay.sync([a, b, c], page_box=page._page_box)
    assert len(_frames(page)) == 2
    assert _frame_boxes(page) == pytest.approx(_container_boxes(page))

    # 只选 c（内层与外层都残缺）⇒ 一个框都不画
    page._group_overlay.sync([c], page_box=page._page_box)
    assert _frames(page) == [], "只选 c 时两层都不完整，不该有任何框"
    page.deleteLater()


# --- 点选语义不受影响（要求 4） ---------------------------------------------

def test_click_inner_group_member_selects_whole_inner_group():
    """真单击内层组成员 ⇒ 仍选中整组（组选中 = 叶子集），且两层框都画出。"""
    page = _page()
    a = _leaf(page, "a", y=0.0)
    b = _leaf(page, "b", y=20.0)
    c = _leaf(page, "c", y=40.0)
    _select_by_clicks(page, ["a", "b", "c"], a, b, c)
    _group_now(page, ["a", "b", "c"])
    _group_now(page, ["a", "b", "c"])        # 两层嵌套

    _click_mm(page, *_EMPTY)
    assert _selected_names(page) == [], "前置：点空白清空"
    assert _frames(page) == [], "前置：无选择无组框"

    _click_mm(page, *_center_mm(page, a))   # 点内层组成员 a
    assert _selected_names(page) == ["a", "b", "c"], \
        f"点内层组成员应选中整组，实得 {_selected_names(page)}"
    assert len(_frames(page)) == 2
    assert _frame_boxes(page) == pytest.approx(_container_boxes(page))
    page.deleteLater()


def test_rubber_band_over_nested_group_draws_both_frames():
    """要求 1 的另一半「能框选」：橡皮筋框过整个嵌套组 ⇒ 全选 + 两层框都画出。

    框选走 ``RubberBandDrag``（``PaperView.set_tool("select")``），与单击是
    不同的选中入口，但落到同一个叶子选择集 ⇒ 组框判据必须同样成立。
    起点取床上空白处（否则按下即变成拖动图元，不进橡皮筋模式）。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    page = _page()
    a = _leaf(page, "a", y=0.0)
    b = _leaf(page, "b", y=20.0)
    c = _leaf(page, "c", y=40.0)
    _select_by_clicks(page, ["a", "b", "c"], a, b, c)
    _group_now(page, ["a", "b", "c"])
    _group_now(page, ["a", "b", "c"])        # 两层嵌套
    _click_mm(page, *_EMPTY)
    assert _selected_names(page) == []

    def vp(x, y):
        return page.view.viewportTransform().map(QPointF(float(x), float(y))).toPoint()

    QTest.mousePress(page.view.viewport(), Qt.LeftButton, Qt.NoModifier, vp(150.0, 100.0))
    QTest.mouseMove(page.view.viewport(), vp(2.0, 2.0))
    QTest.mouseRelease(page.view.viewport(), Qt.LeftButton, Qt.NoModifier, vp(2.0, 2.0))

    assert _selected_names(page) == ["a", "b", "c"], \
        f"框选应选满三个叶子，实得 {_selected_names(page)}"
    assert len(_frames(page)) == 2
    assert _frame_boxes(page) == pytest.approx(_container_boxes(page))
    page.deleteLater()


def test_click_top_level_scatter_selects_only_it():
    """组外散件单点 ⇒ 只选它，且不画任何框（既有语义未被放宽）。"""
    page = _page()
    a = _leaf(page, "a", y=0.0)
    b = _leaf(page, "b", y=20.0)
    _select_by_clicks(page, ["a", "b"], a, b)
    _group_now(page, ["a", "b"])
    far = _leaf(page, "far", y=120.0)

    _click_mm(page, *_EMPTY)
    assert _frames(page) == [], "前置：清空后无框"
    _click_mm(page, *_center_mm(page, far))
    assert _selected_names(page) == ["far"]
    assert _frames(page) == []
    page.deleteLater()


# --- 单层组语义逐位不变（换判据不得放宽/收紧） -------------------------------

def test_single_level_group_semantics_unchanged():
    """单层组：**全部叶子**被选中 ⇒ 1 框；少一个 ⇒ 0 框。

    契约是「该组的全部叶子都在选择集里」，不是「选择集恰好等于该组」——
    混入组外散件**不**让组框消失（旧判据同样如此，闭包判据不得收紧它）。
    真实点击无法造出「组内只选一个」的选择（``_expand_group_selection`` 每次
    变化都会把兄弟补齐），故这里直接驱动 :meth:`GroupOverlay.sync` 逐个子集验。
    """
    page = _page()
    a = _leaf(page, "a", y=0.0)
    b = _leaf(page, "b", y=20.0)
    c = _leaf(page, "c", y=40.0)
    _select_by_clicks(page, ["a", "b"], a, b)
    _group_now(page, ["a", "b"])             # 组 = {a, b}，c 是散件
    grp = _containers(page)[0]

    for mask in range(1 << 3):
        subset = [it for i, it in enumerate((a, b, c)) if (mask >> i) & 1]
        page._group_overlay.sync(subset, page_box=page._page_box)
        complete = {a, b} <= set(subset)     # 按**身份**比，子集里是同一批对象
        expect = [grp] if complete else []
        got = [f.container for f in _frames(page)]
        assert got == expect, \
            f"选择集={[it.name for it in subset]} 时应画 {len(expect)} 个框，实得 {len(got)}"
    page.deleteLater()


def test_single_level_predicate_equals_old_direct_children_rule():
    """单层组上，新判据（叶子闭包）与**旧**判据（直接子项）逐子集等价。

    旧判据（group_overlay.py 修复前）= 「只看 ``doc.items`` 顶层 + 直接子项
    全在叶子集里」。这里把它作为参照模型，在**单层**树上穷举 2^3 个选择子集：
    换判据只应改变嵌套场景，单层必须逐位不变（不放松也不收紧）。
    """
    page = _page()
    a = _leaf(page, "a", y=0.0)
    b = _leaf(page, "b", y=20.0)
    c = _leaf(page, "c", y=40.0)
    _select_by_clicks(page, ["a", "b"], a, b)
    _group_now(page, ["a", "b"])
    doc = page.doc

    def old_rule(sel_ids):
        out = []
        for cont in doc.items:                       # ← 旧：只看顶层
            if not cont.is_container():
                continue
            kids = list(cont.children)               # ← 旧：判直接子项
            if kids and all(id(k) in sel_ids for k in kids):
                out.append(cont)
        return out

    for mask in range(1 << 3):
        subset = [it for i, it in enumerate((a, b, c)) if (mask >> i) & 1]
        sel_ids = {id(it) for it in subset}
        page._group_overlay.sync(subset, page_box=page._page_box)
        assert [f.container for f in _frames(page)] == old_rule(sel_ids), \
            f"子集 {[it.name for it in subset]} 上新旧判据分叉了"
    page.deleteLater()
