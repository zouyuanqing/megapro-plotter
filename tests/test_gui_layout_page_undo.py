"""A1 回归：删页后 undo/redo，同一图元挂到两页 ⇒ 同一几何切两遍。

背景（A1 实测）：页删除**不进撤销栈**（PRD 未要求），故删页后栈里残留的
编辑命令仍在。命令的页归属存的是**下标**，页一删下标整体漂移；越界时又
退回 ``doc.current``。两者叠加：

1. **下标漂移** ⇒ 撤销「在 p1 上加 B」实际改 p0 —— 成员复活到错误的页；
2. **越界退回 current** ⇒ 删首屏后 ``AddItemsCommand(p2a)`` 的 home=2 越界、
   退回 0，在 p0 上做「p2a 还在不在」判定；
3. **入模判据只看当前页**（``make_gi`` / ``AddItemsCommand._rebuild_scene``
   走 ``doc.contains`` = 当前页门面）⇒ p2a 其实在 p1、p0 上判 False
   ⇒ ``doc.add(p2a)`` 把**同一个对象**追加成 p0 的第二个顶层 Item。

终态 ``[['p1a','p2a'], ['p2a']]``：p2a 同时在两页，两页几何合起来同一条线
出现两次 —— 切纸机上就是**下两遍刀**。

本文件钉的不变量（删首屏 / 删中间页 / 删末页 三条路径都跑）：

- **无双挂**：任何时刻不存在「同一对象 ``is`` 出现在两页」；
- **条数自洽**：每页 ``flatten_visible`` 的折线条数 == 该页 items 数；
- **几何唯一**：同一几何在全文只出现一次（真·重复切割判据）；
- **删掉的内容不复活**：被删页上的图元不会跑到存活页去；
- **不靠清栈糊弄**：删页后撤销栈条目数不变（编辑历史保留）。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _line(y, *, name):
    """一条水平线（y 逐页不同 ⇒ 重复几何可被唯一定位）。"""
    from megapro.gui.layout.model import Item

    return Item(paths=[[(0.0, float(y)), (10.0, float(y))]], name=name, z=0.0)


#: 三页三图元，几何互不相同（y = 0 / 20 / 40）。
_SPEC = [(0, "p0a"), (20, "p1a"), (40, "p2a")]


def _build_three_pages():
    """LayoutPage + 3 页各 1 图元（p0 = 默认页，另两页用「＋」）。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    for y, name in _SPEC:
        if name != "p0a":
            lp._on_page_add()  # 建新页并切过去
        lp._add_items([_line(y, name=name)])
    assert lp.doc.page_count == 3
    return lp


def _page_polylines(page):
    """某页的可见拍平折线（走独立 Document 包装，不污染原页的床尺寸）。"""
    from megapro.gui.layout.model import Document, Page, flatten_visible

    return flatten_visible(Document(pages=[Page(items=list(page.items))], current=0))


def _assert_invariants(lp, tag):
    """全文不变量；任一条不成立即抛出，双挂处额外打印便于定位。"""
    from megapro.gui.layout.model import flatten_visible, iter_items

    doc = lp.doc
    owner: dict[int, tuple[int, str]] = {}
    for idx, page in enumerate(doc.pages):
        for it in iter_items(page.items):
            key = id(it)
            if key in owner:
                prev_idx, prev_name = owner[key]
                raise AssertionError(
                    f"[{tag}] 同一对象双挂：{it.name!r} 同时在 p{prev_idx} 与 p{idx}")
            owner[key] = (idx, it.name)
        # 每页折线条数 == 该页 items 数（本文件 item 均「1 顶层 + 1 折线」）
        assert len(_page_polylines(page)) == len(page.items), (
            f"[{tag}] p{idx} 折线条数 {len(_page_polylines(page))} "
            f"!= items 数 {len(page.items)}")
    # 同一几何在全文只出现一次
    allp = [tuple(p) for page in doc.pages for p in _page_polylines(page)]
    assert len(allp) == len(set(allp)), (
        f"[{tag}] 同一几何在全文出现多次（共 {len(allp)} 条、"
        f"去重后 {len(set(allp))} 条）⇒ 重复切割")
    # 每个图元都带着一条 2 点折线（「名字在但几何没了」也算一种错挂）
    for idx, page in enumerate(doc.pages):
        for it in iter_items(page.items):
            got = _page_polylines(page)
            assert any(len(p) == 2 for p in got), (
                f"[{tag}] p{idx} 的 {it.name!r} 已挂载但页内无任何折线")


def _delete_page(lp, index):
    lp._on_page_tab_changed(index)
    lp._on_page_del()
    assert lp.doc.page_count == 2


def _undo(lp, n):
    for _ in range(n):
        lp._undo.undo()


def _redo(lp, n):
    for _ in range(n):
        lp._undo.redo()


# --- 三条删除路径：任意次 undo/redo 都不破不变量 ---------------------------

@pytest.mark.parametrize("del_index,survivors", [
    (0, ["p1a", "p2a"]),
    (1, ["p0a", "p2a"]),
    (2, ["p0a", "p1a"]),
])
def test_page_delete_then_undo_redo_never_double_hosts(del_index, survivors):
    """删首屏 / 删中间页 / 删末页：undo×N + redo×N 全程无双挂、无重复几何。"""
    lp = _build_three_pages()
    try:
        _delete_page(lp, del_index)
        _assert_invariants(lp, "删页后")
        for n in (1, 2, 3):
            _undo(lp, n)
            _assert_invariants(lp, f"undo x{n}")
            _redo(lp, n)
            _assert_invariants(lp, f"redo x{n}")
        # 终态 = 存活页的图元各归其页，被删页的图元不复活
        names = [[it.name for it in pg.items] for pg in lp.doc.pages]
        assert sorted(n for row in names for n in row) == sorted(survivors)
    finally:
        lp.deleteLater()


def test_page_delete_keeps_undo_history_intact():
    """不靠「删页清空撤销栈」糊弄：删页后栈条目数不变，撤销仍逐级生效。"""
    lp = _build_three_pages()
    try:
        assert lp._undo.count() == 3
        _delete_page(lp, 0)
        assert lp._undo.count() == 3, "删页不得清空撤销栈（用户编辑历史应保留）"
        assert lp._undo.index() == 3
        _undo(lp, 1)
        assert lp._undo.index() == 2
        _redo(lp, 1)
        assert lp._undo.index() == 3
    finally:
        lp.deleteLater()


def test_delete_first_page_redo_does_not_duplicate_geometry():
    """A1 原复现：删首屏 + undo 一次 + redo 一次 ⇒ 不得双挂、不得切两遍。"""
    lp = _build_three_pages()
    try:
        _delete_page(lp, 0)
        _undo(lp, 1)
        _redo(lp, 1)
        assert [it.name for it in lp.doc.pages[0].items] == ["p1a"]
        assert [it.name for it in lp.doc.pages[1].items] == ["p2a"]
        _assert_invariants(lp, "删首屏 undo1 redo1")
    finally:
        lp.deleteLater()


@pytest.mark.parametrize("del_index,expected", [
    (0, [["p1a"], ["p2a"]]),
    (1, [["p0a"], ["p2a"]]),
    (2, [["p0a"], ["p1a"]]),
])
def test_repeated_redo_cycles_do_not_accumulate_drift(del_index, expected):
    """要求 2：多个 undo/redo 来回不累积错位，终态恒等于期望终态。

    修复前每按一次 undo 都会把命令的 ``_home_page`` 改写成当时的
    ``doc.current``，页数变化时逐次漂移；修复后归属由页对象身份解析，
    幂等 —— 故这里是「来回多少遍结果都一样」的防累积守卫。
    """
    lp = _build_three_pages()
    try:
        _delete_page(lp, del_index)
        for _ in range(4):
            _undo(lp, 3)
            _redo(lp, 3)
            names = [[it.name for it in pg.items] for pg in lp.doc.pages]
            assert names == expected, f"redo 来回出现累积错位：{names}"
            _assert_invariants(lp, f"删 p{del_index} 来回")
    finally:
        lp.deleteLater()


# --- 纯逻辑：入模判据必须跨页 ----------------------------------------------

def test_contains_anywhere_is_document_wide():
    """``contains`` 是当前页门面；「是否已在文档里」需 ``contains_anywhere``。"""
    from megapro.gui.layout.model import Document

    a = _line(0, name="a")
    b = _line(20, name="b")
    d = Document(items=[a])
    d.add_page()
    d.switch_page(1)
    d.add(b)
    assert d.contains(b) and not d.contains(a)     # 门面只看当前页
    assert d.contains_anywhere(a) and d.contains_anywhere(b)
    assert not d.contains_anywhere(_line(99, name="ghost"))


def test_page_anchor_survives_page_reorder():
    """页锚 = 页对象身份：重排后归属页自动跟随（下标漂移不再致错页）。

    与删页同根因（下标漂移），但触面不同：重排不减少页数，所以旧实现既不
    越界也不退回 current，只是**静默打到相邻的页**上 —— 撤销「p1 上加 p1a」
    去删了 p0 的内容。页锚钉死后重排对 undo 完全透明。
    """
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    lp._add_items([_line(0, name="p0a")])       # 命令 1 归属「页0 对象」
    lp._on_page_add()
    lp._add_items([_line(20, name="p1a")])      # 命令 2 归属「页1 对象」
    assert lp.doc.page_count == 2
    lp._on_page_tab_changed(0)                  # 视图停在 p0
    lp._on_page_right()                         # p0 移到末尾 → [页1, 页0]
    assert [[it.name for it in pg.items] for pg in lp.doc.pages] == \
        [["p1a"], ["p0a"]]

    _undo(lp, 1)                                # 撤命令 2（页1 对象 → 新下标 0）
    assert [[it.name for it in pg.items] for pg in lp.doc.pages] == \
        [[], ["p0a"]]
    _undo(lp, 1)                                # 撤命令 1（页0 对象 → 新下标 1）
    assert [[it.name for it in pg.items] for pg in lp.doc.pages] == [[], []]

    _redo(lp, 1)
    assert [[it.name for it in pg.items] for pg in lp.doc.pages] == \
        [[], ["p0a"]]
    _redo(lp, 1)
    assert [[it.name for it in pg.items] for pg in lp.doc.pages] == \
        [["p1a"], ["p0a"]]
    lp.deleteLater()
