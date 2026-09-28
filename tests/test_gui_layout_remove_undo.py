"""A3 回归：一次删除 ≥2 项后撤销，文档顺序被反转。

背景（A3 实测）：:meth:`Page.remove` 每次删除**当场**记
:class:`DetachInfo(owner, index)`，而 ``index`` 是「本次删除前、已删项塌陷
之后」的位置。于是 ``RemoveItemsCommand._do_undo`` / ``ClearCommand._do_undo``
里那句按 ``zip(items, infos)`` **正序** ``attach(it, owner, index=info.index)``
会按一条已经失效的下标日程插入 —— 结果是原顺序的一个置换：

- 三个顶层散件 ``p,q,r`` 一次删除后撤销 → ``['r','q','p']``（完全反转，
  而「多选 → Ctrl+D → Ctrl+Z」是最常规的操作）；
- 非相邻的 2、4 → ``['1','2','4','3']``（一般性 off-by-k）；
- 跨组：组内 ``a,b`` 加顶层 ``c`` → ``G.children`` 从 ``['a','b']`` 变成
  ``['b','a']``；
- 清空对照：顶层 ``['组','z']`` → ``['z','组']``；
- 一次只删 1 项时正确 ⇒ 触发条件是「一次删 ≥2 项」。

影响是**静默**的：几何集合、条数都没变，画布上看不出来，但
``document_to_svg`` 与删除前**逐字节**不同 ⇒ 送作业的 G-code 行多重集相同
而有序不同（实测首个切割点从 ``G0 X0 Y10`` 变成 ``G0 X40 Y10``）—— 切纸机
按另一条顺序走刀。

修法：撤销必须**逆删除序**回插。``DetachInfo.index`` 记的是"r_t 被摘下那一刻
它所在的位置"，而那一刻的列表恰好是「原列表 − 已摘的 r_1..r_{t-1}」；逆序回插
到 r_t 时，列表已是「原列表 − {r_1..r_{t-1}}」（r_t..r_k 都已就位），两者相同
⇒ 该下标仍是 r_t 的正确落点。逐 owner 独立成立，故跨组也覆盖。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _line(i, name):
    from megapro.gui.layout.model import Item

    return Item(paths=[[(0.0, float(i * 10)), (10.0, float(i * 10))]],
                name=name, z=0.0)


def _tops(doc):
    return [it.name for it in doc.items]


def _kids(container):
    return [c.name for c in container.children]


def _svg(doc):
    from megapro.gui.layout.export_svg import document_to_svg

    return document_to_svg(doc)


def _assert_restored(lp, before_svg, before_tops, before_kids, tag):
    """撤销后：顶层序、children 序、SVG 三者都必须与删除前逐点相同。"""
    assert _tops(lp.doc) == before_tops, \
        f"[{tag}] 顶层顺序未复原：{_tops(lp.doc)} != {before_tops}"
    if before_kids is not None:
        assert _kids(before_kids[0]) == before_kids[1], \
            f"[{tag}] children 顺序未复原：{_kids(before_kids[0])} != {before_kids[1]}"
    assert _svg(lp.doc) == before_svg, \
        f"[{tag}] document_to_svg 与删除前**逐字节**不同（切割次序变了）"


# --- 四种情形 ---------------------------------------------------------------

def test_undo_after_removing_two_adjacent_top_level_items():
    """相邻删除：两个相邻顶层图元一次删，撤销后顺序逐项复原。"""
    from megapro.gui.canvas.undo_cmds import RemoveItemsCommand
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    made = {n: _line(i, n) for i, n in enumerate(["p", "q", "r"])}
    lp._add_items([made["p"], made["q"], made["r"]])
    before_svg, before_tops = _svg(lp.doc), _tops(lp.doc)

    lp._undo.push(RemoveItemsCommand(lp, [made["p"], made["q"]]))
    assert _tops(lp.doc) == ["r"]
    lp._undo.undo()
    _assert_restored(lp, before_svg, before_tops, None, "相邻")
    lp.deleteLater()


def test_undo_after_removing_non_adjacent_top_level_items():
    """非相邻删除：删 2、4，撤销后不得变成 ``['1','2','4','3']``（off-by-k）。"""
    from megapro.gui.canvas.undo_cmds import RemoveItemsCommand
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    made = {n: _line(i, n) for i, n in enumerate(["1", "2", "3", "4"])}
    lp._add_items([made[n] for n in ["1", "2", "3", "4"]])
    before_svg, before_tops = _svg(lp.doc), _tops(lp.doc)

    lp._undo.push(RemoveItemsCommand(lp, [made["2"], made["4"]]))
    assert _tops(lp.doc) == ["1", "3"]
    lp._undo.undo()
    _assert_restored(lp, before_svg, before_tops, None, "非相邻")
    lp.deleteLater()


def test_undo_after_removing_items_across_a_group():
    """跨组删除：组内 a,b + 顶层 c ⇒ children 顺序与顶层序都逐项复原。"""
    from megapro.gui.canvas.undo_cmds import RemoveItemsCommand
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a, b, c = _line(0, "a"), _line(1, "b"), _line(2, "c")
    lp._add_items([a, b, c])
    g = lp.doc.group_items([a, b], name="G")
    before_svg = _svg(lp.doc)
    before_tops, before_kids = _tops(lp.doc), (g, _kids(g))
    assert before_tops == ["G", "c"] and before_kids[1] == ["a", "b"]

    lp._undo.push(RemoveItemsCommand(lp, [a, b, c]))
    assert _tops(lp.doc) == ["G"] and g.children == []
    lp._undo.undo()
    _assert_restored(lp, before_svg, before_tops, before_kids, "跨组")
    assert _kids(g) == ["a", "b"], "组内顺序被反转"
    lp.deleteLater()


def test_clear_undo_restores_top_level_order():
    """清空对照：顶层 ``['组','z']`` 清空后撤销，不得变成 ``['z','组']``。"""
    from megapro.gui.canvas.undo_cmds import ClearCommand
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    members = [_line(0, "g1"), _line(1, "g2")]
    lp._add_items(members)
    lp.doc.group_items(members, name="组")
    lp._add_items([_line(2, "z")])
    before_svg, before_tops = _svg(lp.doc), _tops(lp.doc)
    assert before_tops == ["组", "z"]

    lp._undo.push(ClearCommand(lp))
    assert _tops(lp.doc) == []
    lp._undo.undo()
    _assert_restored(lp, before_svg, before_tops, None, "清空")
    lp.deleteLater()


# --- 顺序无关性：victims 不必按文档序 --------------------------------------

def test_undo_order_independent_of_victim_list_order():
    """victims 顺序 = **场景选择顺序**，不必等于文档顺序（`_delete_selected`）。

    逆删除序回插的正确性不依赖入参有序，故这里钉住它。
    """
    from megapro.gui.canvas.undo_cmds import RemoveItemsCommand
    from megapro.gui.layout.layout_page import LayoutPage

    for victims in (["r", "p", "q"], ["q", "r", "p"]):
        lp = LayoutPage()
        made = {n: _line(i, n) for i, n in enumerate(["p", "q", "r"])}
        lp._add_items([made["p"], made["q"], made["r"]])
        before_svg, before_tops = _svg(lp.doc), _tops(lp.doc)
        lp._undo.push(RemoveItemsCommand(lp, [made[n] for n in victims]))
        assert _tops(lp.doc) == []
        lp._undo.undo()
        _assert_restored(lp, before_svg, before_tops, None, f"victims={victims}")
        lp.deleteLater()


# --- 不累积 -----------------------------------------------------------------

def test_remove_undo_redo_cycles_do_not_accumulate_drift():
    """要求 3：undo/redo 三个来回，终态恒等于删除前（不累积错位）。"""
    from megapro.gui.canvas.undo_cmds import RemoveItemsCommand
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    made = {n: _line(i, n) for i, n in enumerate(["1", "2", "3", "4"])}
    lp._add_items([made[n] for n in ["1", "2", "3", "4"]])
    a, b, c = made["1"], made["2"], made["3"]
    g = lp.doc.group_items([a, b], name="G")
    before_svg, before_tops = _svg(lp.doc), _tops(lp.doc)

    lp._undo.push(RemoveItemsCommand(lp, [a, b, c, made["4"]]))
    # 删掉 G 的两个成员 + 顶层 3、4 ⇒ 只剩空组 G（它没被列为 victims）
    assert _tops(lp.doc) == ["G"]
    for _ in range(3):
        lp._undo.undo()
        _assert_restored(lp, before_svg, before_tops, (g, ["1", "2"]), "来回-撤销")
        lp._undo.redo()
        assert _tops(lp.doc) == ["G"], "重做后应回到只剩空组 G"
    # 循环以 redo 收尾，栈停在「已删除」；再撤一次验证终态仍精确复原
    lp._undo.undo()
    _assert_restored(lp, before_svg, before_tops, (g, ["1", "2"]), "来回终态")
    lp.deleteLater()


# --- 对照守卫 ---------------------------------------------------------------

def test_single_item_removal_still_restores_exactly():
    """对照组：一次只删 1 项本来就正确，修法不得把它弄坏。"""
    from megapro.gui.canvas.undo_cmds import RemoveItemsCommand
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    made = {n: _line(i, n) for i, n in enumerate(["p", "q", "r"])}
    lp._add_items([made["p"], made["q"], made["r"]])
    before_svg, before_tops = _svg(lp.doc), _tops(lp.doc)
    lp._undo.push(RemoveItemsCommand(lp, [made["q"]]))
    assert _tops(lp.doc) == ["p", "r"]
    lp._undo.undo()
    _assert_restored(lp, before_svg, before_tops, None, "单删")
    lp.deleteLater()


def test_remove_items_spanning_two_groups_and_top_level():
    """跨**两个**组 + 顶层：一条命令里三个不同 owner，逆序回插各自复原。

    特意让 GA 的两个 victims **在组内非相邻**（a1、a3 夹着 a2）：只取每个
    列表的首元素时正序回插恰好也对，钉不住回归。
    """
    from megapro.gui.canvas.undo_cmds import RemoveItemsCommand
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a1, a2, a3, b1, b2, z = (_line(0, "a1"), _line(1, "a2"), _line(2, "a3"),
                             _line(3, "b1"), _line(4, "b2"), _line(5, "z"))
    lp._add_items([a1, a2, a3, b1, b2, z])
    ga = lp.doc.group_items([a1, a2, a3], name="GA")
    gb = lp.doc.group_items([b1, b2], name="GB")
    before_svg = _svg(lp.doc)
    before_tops = _tops(lp.doc)
    assert before_tops == ["GA", "GB", "z"]
    assert _kids(ga) == ["a1", "a2", "a3"] and _kids(gb) == ["b1", "b2"]

    lp._undo.push(RemoveItemsCommand(lp, [a1, a3, b2, z]))
    assert _tops(lp.doc) == ["GA", "GB"]
    assert _kids(ga) == ["a2"] and _kids(gb) == ["b1"]

    lp._undo.undo()
    assert _tops(lp.doc) == before_tops
    assert _kids(ga) == ["a1", "a2", "a3"], "组内非相邻成员撤销后顺序错了"
    assert _kids(gb) == ["b1", "b2"]
    assert _svg(lp.doc) == before_svg, "跨两个组时 SVG 必须逐字节复原"
    assert all(lp._gi_for(it) is not None for it in lp.doc.items_visible()), \
        "逆序回插后场景项未重建"
    lp.deleteLater()
