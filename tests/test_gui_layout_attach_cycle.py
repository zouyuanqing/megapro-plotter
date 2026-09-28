"""A1 回归：``Page.attach`` 能造出**真环**/双父，且「产品路径不可达」这句陈述本身不准。

## 缺陷本体（实测复现，修复前）

上一轮 A5 给 :meth:`Item.descendants` 补了深度守卫，成环时确实抛可诊断的
``ValueError``。但**守卫只是探测器 —— 真正造环的那扇门没关**::

    G.children = [leaf, mid]
    d.attach(G, owner=leaf)   ->  正常返回，无异常无告警
    leaf.children == ['G']     ->  G -> leaf -> G 成真环
    leaf.descendants()         ->  ValueError: 模型树深度超过 64

根因：:meth:`Page.attach` 修复前只挡「已是 owner 的**直接**子项」，随后
无条件 ``owner.children.append(item)`` —— 与
:meth:`Page.group_items` 的显式自含拒绝（返回 ``None``）不对称。

只查直接子项会漏掉**三个**坏形状里的另外两个：

=========================  =======================================  =========
坏形状                      触发调用                                  后果
=========================  =======================================  =========
① ``item is owner``         ``attach(G, owner=G)``                   自环
② ``owner ∈ subtree(item)``  ``attach(G, owner=其中叶子)``            **真环**
③ ``item ∈ subtree(owner)``  ``attach(low, owner=deep)``（low 是     **双父**
                            deep 的曾孙）                             **DAG**
=========================  =======================================  =========

② 会被深度守卫**事后**发现（抛 ``ValueError``）；③ **永远不会被任何守卫
发现** —— :func:`iter_items` / :func:`iter_units` / :func:`flatten_visible`
照常遍历，把该几何**产出两次** ⇒ 对切纸机即**同一几何切两遍**。两者都
**静默**：无异常、无告警、树看起来仍然「正常」（只是父子关系反常）。

## 「产品路径不可达」这句陈述本身不准（A1 的第二半）

原 ``model.py`` 的 :meth:`Item.descendants` 与 :meth:`Page.group_items`
docstring 都写着「产品路径不可达（src/ 里**只有** ``wrap_group`` 与 JSON
反序列化构造 children，JSON 语法表达不出环）」。
``git grep -n "\\.attach(" -- src/`` 显示 attach **也是** children 的构造者：
``undo_cmds.py:287/510/638/640`` 4 处 + ``model.py:801/830/832/960`` 4 处，
共 8 处调用。**枚举不完整** —— 这正是 A5 要治的那类病（钉了会变的枚举、没钉
会出错的事），A5 自己又犯了一处。枚举已补进 model.py 的那两处 docstring
（含 ①②③、全部 8 个调用点，以及「现有调用点为何安全」的论证）；本文件钉
**行为**。

## 本文件钉的断言（全部是「会出错的事」，不是「会变的量」）

- 拒绝必须是**返回值**而不是异常：不许出现 ``ValueError``（更不许
  ``RecursionError``）——「被拒」和「抛错」在切纸机上是两种不同的失败。
- 拒绝后**树形逐节点不变**，且**没有重复几何**。
- 拍平的几何单元**序列**逐点恒等、``flatten_visible`` 折线逐点恒等、
  ``document_to_svg`` 逐字节相同（顺序进了导出的 SVG 与发往机器的 G-code）。
- 合法回插（undo 侧 ``remove`` → ``attach(DetachInfo)``、真 ``QUndoStack``、
  ``group_items`` 末尾那次 ``attach(新容器, owner=first.owner)``、``ungroup``
  的提升）**不被新守卫误伤**。

未跟踪的 ``tests/test_gui_layout_tree_guard.py:19-23`` 里有一份同样不完整的
枚举（只数 ``wrap_group`` 与 JSON）；该文件不属本批所有权，故此处只记录、
不改它。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

from megapro.gui.layout.export_svg import document_to_svg
from megapro.gui.layout.model import (
    Document, Item, flatten_visible, iter_items, iter_units,
)

try:                                     # Qt 只被最后一条用例用到
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    _HAVE_QT = True
except Exception:                        # pragma: no cover - 无 Qt 时的退化
    _HAVE_QT = False


# --- 断言工具：全部绕开深度守卫，独立判定「树变了 / 成环了 / 几何重复了」 ----

def _tree_shape(items, _path=frozenset()):
    """树形快照 ``[(name, children_shape), ...]`` —— **只表达「谁是谁的孩子」**。

    刻意不比 ``pos``/``z``/``paths`` 等**会变的字段值**（换布局就假红），只比
    包含关系这一个结构事实。前序 + 路径集：成环时**不挂死也不抛异常**，而是把环
    记成 ``("<环>", name, [...])`` 留在快照里 —— 于是「树被改了」与「成环了」
    能被**同一次比较**抓到，不必把 :data:`MAX_TREE_DEPTH` 守卫「抛不抛」当
    前提（守卫是探测器，测试不该依赖探测器）。
    """
    out = []
    for it in items:
        if id(it) in _path:
            out.append(("<环>", it.name, [c.name for c in it.children]))
            continue
        out.append((it.name, _tree_shape(it.children, _path | {id(it)})))
    return out


def _find_cycle(items, _path=()):
    """返回第一条环的路径（名字序列），无环返回 ``None``。

    ``_path`` = 根在前的 ``((name, id), ...)``。与 :data:`MAX_TREE_DEPTH` 无关：
    这是**结构性**判定，成环时照样返回，**绝不抛异常、绝不挂死** ——
    （本判据自己不能被环带进死循环，否则测试就变成在测自己的 bug。）
    """
    for it in items:
        if any(pid == id(it) for _, pid in _path):
            return tuple(n for n, _ in _path) + (it.name, it.name)
        got = _find_cycle(it.children, _path + ((it.name, id(it)),))
        if got is not None:
            return got
    return None


def _dup_polylines(polylines):
    """拍平结果里**逐点相同**的重复折线（多重集里计数 >1 的）。

    比 :func:`_dup_units` 更贴近「切纸机实际会走什么」：它不看图元身份，只看
    最终落纸的坐标 —— 同一坐标出现两次就是**下两遍刀**，哪怕是不同名的两个
    图元（双父 DAG 就是这种：同名同路径）。
    """
    keys = [tuple(tuple(pt) for pt in pl) for pl in polylines]
    return sorted(k for k in set(keys) if keys.count(k) > 1)


def _unit_names(doc):
    """拍平面上的几何单元名（:func:`iter_units` 的单元拥有者），含重复。"""
    try:
        return [u[0].name for u in iter_units(doc.items)]
    except ValueError:
        return ["<抛 ValueError：已损坏/成环>"]


def _dup_units(doc):
    """被产出**多于一次**的几何单元名（多重集里计数 >1 的）。

    成环时会被深度守卫抛异常；双父 DAG 则不会 —— 它安静地吐两条一模一样的
    折线，对切纸机就是下两遍刀。
    """
    names = _unit_names(doc)
    return sorted(n for n in set(names) if names.count(n) > 1)


def _must_not_raise(fn, what):
    """拒绝路径必须**返回**，不能抛 —— 尤其不许 ``ValueError``。"""
    try:
        return fn()
    except Exception as e:               # noqa: BLE001 - 就是要一网打尽
        pytest.fail(f"[{what}] 应被「拒绝（返回 None）」而不是抛异常，"
                    f"实际 {type(e).__name__}: {e}")


def _line(x0, name):
    return Item(paths=[[(float(x0), 0.0), (float(x0) + 10.0, 0.0)]], name=name)


def _d():
    """样例文档：``deep(mid2(top(low)))`` 与 ``G(leaf, mid)``，共 5 条几何。

    同时含「容器套容器」与「组套组」，让三个坏形状都触发得到。
    """
    d = SimpleNamespace()
    d.leaf, d.mid = _line(0.0, "leaf"), _line(20.0, "mid")
    d.G = Item(name="G", children=[d.leaf, d.mid])
    d.low = _line(40.0, "low")
    d.top = Item(name="top", children=[d.low])
    d.mid2 = Item(name="mid2", children=[d.top])
    d.deep = Item(name="deep", children=[d.mid2])
    d.doc = Document(items=[d.deep, d.G])
    # 样例本身必须干净，否则下面所有「被拒后仍干净」的断言会自证失败
    assert _find_cycle(d.doc.items) is None
    assert _dup_units(d.doc) == []
    return d


# --- (a) attach(容器, owner=其中叶子)：真环，必须被拒 -------------------------

def test_attach_container_under_its_own_leaf_is_rejected_not_raised():
    """``attach(G, owner=leaf)``（leaf ∈ G 子树）：修复前无异常无告警地成真环。

    旧行为：``leaf.children == ['G']``，G→leaf→G。断言同时成立：
    ①**没有**抛异常（是「拒绝」不是「报错」）；②树形逐节点不变；③无重复几何；
    ④拍平序列与导出 SVG 逐点不变（切割次序没被改）。
    """
    d = _d()
    before_shape = _tree_shape(d.doc.items)
    before_units = _unit_names(d.doc)
    before_flat = flatten_visible(d.doc)
    before_svg = document_to_svg(d.doc)

    _must_not_raise(lambda: d.doc.attach(d.G, owner=d.leaf),
                    "attach(容器, owner=其中叶子)")

    assert _find_cycle(d.doc.items) is None, \
        f"attach 造出了真环：{_find_cycle(d.doc.items)}"
    assert d.leaf.children == [], \
        f"被拒的 attach 仍改了 leaf.children: {d.leaf.children}"
    assert _tree_shape(d.doc.items) == before_shape, \
        f"被拒的 attach 改了树：{_tree_shape(d.doc.items)} != {before_shape}"
    assert _dup_units(d.doc) == [], \
        f"被拒的 attach 造成重复几何：{_dup_units(d.doc)}"
    assert _unit_names(d.doc) == before_units, "拍平的几何单元序列变了 —— 切割次序被改"
    assert flatten_visible(d.doc) == before_flat
    assert document_to_svg(d.doc) == before_svg, \
        "导出的 SVG 变了 —— 送作业的切割次序被改"


def test_attach_ancestor_under_its_own_descendant_is_rejected():
    """``attach(top, owner=low)``（top 是 low 的祖先）：另一侧的真环。

    「只查直接子项」的守卫查不出这条 —— ``low`` 不是 ``top`` 的直接子项
    （中间隔着 ``mid2``），但把 ``top`` 挂到 ``low`` 下依然成环
    （low→top→mid2→low）。
    """
    d = _d()
    before_shape = _tree_shape(d.doc.items)

    _must_not_raise(lambda: d.doc.attach(d.top, owner=d.low),
                    "attach(祖先, owner=其中后代)")

    assert _find_cycle(d.doc.items) is None, \
        f"attach 造出了真环：{_find_cycle(d.doc.items)}"
    assert d.low.children == [], "被拒的 attach 仍改了 low.children"
    assert _tree_shape(d.doc.items) == before_shape
    assert _dup_units(d.doc) == []


# --- (b) attach 到自身 -------------------------------------------------------

def test_attach_item_into_itself_is_rejected():
    """``attach(G, owner=G)``：自环，必须被拒且不动模型。"""
    d = _d()
    before_shape = _tree_shape(d.doc.items)

    _must_not_raise(lambda: d.doc.attach(d.G, owner=d.G), "attach(自己, owner=自己)")

    assert _find_cycle(d.doc.items) is None, f"自环：{_find_cycle(d.doc.items)}"
    assert _tree_shape(d.doc.items) == before_shape
    assert _dup_units(d.doc) == []


# --- ③ 双父 DAG：非环，但**同一几何切两遍**，没有任何守卫会发现 ---------------

def test_attach_deep_descendant_creating_second_parent_is_rejected():
    """``attach(low, owner=deep)``（low 已是 deep 的曾孙）⇒ 双父 DAG ⇒ 切两遍。

    这是**比成环更隐蔽**的一种：树无环、所有递归遍历都正常返回，可
    :func:`iter_units` / :func:`flatten_visible` 会把 ``low`` 的折线**产出两次**。
    切纸机不报错、画布看不出异常，就是**下两遍刀**。旧实现只挡「已是 owner 的
    **直接**子项」，这一格直接漏过。
    """
    d = _d()
    before_shape = _tree_shape(d.doc.items)
    before_flat = flatten_visible(d.doc)
    before_svg = document_to_svg(d.doc)

    _must_not_raise(lambda: d.doc.attach(d.low, owner=d.deep),
                    "attach(深层后代, owner=其祖先)")

    # 先钉**最要命**的那条：同一几何被切两遍。旧行为实测 flatten 从 1 条变 2 条、
    # 单元名出现两个 'low'，且全程无异常无告警。
    assert _dup_units(d.doc) == [], \
        f"同一几何被切两遍：重复单元 {_dup_units(d.doc)}"
    assert not _dup_polylines(flatten_visible(d.doc)), \
        "拍平结果里有逐点相同的重复折线"
    assert len(flatten_visible(d.doc)) == len(before_flat), \
        "折线条数变了"
    # 再钉结构面
    assert _find_cycle(d.doc.items) is None
    assert [c.name for c in d.deep.children] == ["mid2"], \
        f"双父已建：deep.children={[c.name for c in d.deep.children]}"
    assert _tree_shape(d.doc.items) == before_shape
    assert document_to_svg(d.doc) == before_svg


# --- (c) group_items 选到自己：仍返回 None，且**不抛** -------------------------

@pytest.mark.parametrize("a_name,b_name", [
    ("G", "leaf"),      # 组 + 它的直接子项
    ("G", "mid"),       # 组 + 它的另一个直接子项
    ("deep", "mid2"),   # 外层容器 + 它的直接子项
    ("mid2", "top"),    # 容器套容器
    ("deep", "low"),    # 祖先 + 曾孙（不是直接子项）—— 与 ② 同一形状
])
def test_group_items_self_containing_selection_returns_none_without_raising(
        a_name, b_name):
    """编组把「自己人」选进子集：返回 ``None``，全程不得抛异常。

    这是与 attach **对称**的那一半契约：两条入口对同一个错误选择必须给出**同样
    的失败形状**（静默拒绝），不能一个返回 None、另一个抛错。参数化覆盖
    「直接子项 / 深层后代」两侧。
    """
    d = _d()
    objs = {it.name: it for it in iter_items(d.doc.items)}
    before_shape = _tree_shape(d.doc.items)

    out = _must_not_raise(
        lambda: d.doc.group_items([objs[a_name], objs[b_name]], name="新组"),
        f"group_items({a_name}, {b_name})")
    assert out is None, f"自含选择必须被拒（返回 None），实际 {out!r}"
    assert _tree_shape(d.doc.items) == before_shape, "拒绝不得有副作用"
    assert not any(it.name == "新组" for it in iter_items(d.doc.items)), \
        "被拒的编组留下了空容器"


# --- 合法回插不得被新守卫误伤（纯模型：undo 侧实际走的那条路） ----------------

def test_remove_then_attach_roundtrip_is_not_broken_by_the_new_guard():
    """undo 侧真实路径 ``remove`` → ``attach(DetachInfo)`` 必须**逐位**复原。

    新守卫查的是「互为后代」，而 undo 回插的是「摘出 → 挂回原 owner 原下标」：
    ``item`` 摘出后**不在** ``owner`` 子树里、``owner`` 也不在 ``item`` 子树里
    ⇒ 三个坏形状一个都不该命中。若误伤，撤销就静默失效 = 删掉的东西回不来。
    """
    d = _d()
    before_shape = _tree_shape(d.doc.items)
    before_units = _unit_names(d.doc)
    before_svg = document_to_svg(d.doc)
    before_order = {it.name: it.order for it in iter_items(d.doc.items)}

    info = d.doc.remove(d.leaf)
    assert info is not None and info.owner is d.G, "remove 未返回组感知回执"
    _must_not_raise(lambda: d.doc.attach(d.leaf, owner=info.owner,
                                         index=info.index), "undo 回插")

    assert _tree_shape(d.doc.items) == before_shape, "回插没有复原树形"
    assert _unit_names(d.doc) == before_units
    assert document_to_svg(d.doc) == before_svg
    after_order = {it.name: it.order for it in iter_items(d.doc.items)}
    assert after_order == before_order, "入档序编号变了 ⇒ 并列 z 的切割次序会变"


def test_multi_item_remove_and_reverse_attach_still_restores_exactly():
    """一次删多项、逆序回插（A3 契约）—— 逐项都要过新守卫且顺序精确复原。"""
    d = _d()
    extra = _line(60.0, "extra")
    d.doc.add(extra)
    before_shape = _tree_shape(d.doc.items)
    before_svg = document_to_svg(d.doc)

    targets = [d.leaf, d.mid, extra]
    infos = [d.doc.remove(it) for it in targets]
    for it, info in zip(reversed(targets), reversed(infos)):
        _must_not_raise(
            lambda it=it, info=info: d.doc.attach(it, owner=info.owner,
                                                  index=info.index),
            f"逆序回插 {it.name}")

    assert _tree_shape(d.doc.items) == before_shape
    assert document_to_svg(d.doc) == before_svg, "回插后导出与删除前不同（切割次序变了）"


def test_group_then_attach_of_new_container_is_not_rejected():
    """``group_items`` 末尾那次 ``attach(cont, owner=first.owner)`` 不得被自拒。

    这是 attach 守卫与编组入口的**交互点**：新容器 ``cont`` 的子树 = 全部成员，
    而 ``first.owner`` 是第一个成员的**原父容器**。若守卫过宽（误判 owner 在
    cont 子树里、或 cont 在 owner 子树里），编组会静默退化成「成员被摘掉但没挂
    回去」= 几何凭空消失。
    """
    d = _d()
    before_flat = flatten_visible(d.doc)
    other = _line(80.0, "other")
    d.doc.add(other)

    cont = d.doc.group_items([d.leaf, d.mid, other], name="新组")
    assert cont is not None, "合法编组被新守卫误拒 —— 几何会凭空消失"
    assert [c.name for c in cont.children] == ["leaf", "mid", "other"]
    # 容器落在**第一个成员的原位置**（这里是 G 的 children[0]，非顶层）
    assert cont in d.G.children, "新容器没挂到第一个成员的原位（G.children[0]）"
    assert cont in list(iter_items(d.doc.items)), "新容器没进文档树"
    assert _find_cycle(d.doc.items) is None
    assert _dup_units(d.doc) == []
    # 几何多重集守恒：不少切（成员被摘掉却没挂回）、不重复切
    after_flat = flatten_visible(d.doc)
    assert len(after_flat) == len(before_flat) + 1, \
        f"编组后折线条数变了（几何凭空消失/多出）：{len(after_flat)} " \
        f"!= {len(before_flat)} + 1"
    assert not _dup_polylines(after_flat), "编组造成了重复折线"


def test_ungroup_promotes_kids_without_being_rejected():
    """``ungroup`` 末尾那些 ``attach(ch, owner=info.owner)`` 同样不得被误拒。

    与上一条对称：解组把容器摘出后把子项提升回**容器原 owner**。若守卫把
    ``info.owner``（= 容器的父）误判成 ``ch`` 的后代，解组就会中途「拒绝」
    —— 结果是容器没了、孩子也回不去，几何凭空消失且无异常。
    """
    d = _d()
    g2 = Item(name="G2", children=[_line(0.0, "a"), _line(20.0, "b")])
    d.doc.add(g2)
    before_units = sorted(_unit_names(d.doc))

    cont = d.doc.group_items([d.top, g2], name="X")
    assert cont is not None, "前置编组被拒"
    kids = d.doc.ungroup(cont)
    assert sorted(c.name for c in kids) == ["G2", "top"]
    assert cont.children == [], "解组后容器应被清空"
    assert _find_cycle(d.doc.items) is None
    assert sorted(_unit_names(d.doc)) == before_units, "解组改变了拍平的几何集合"
    assert d.mid2 in list(iter_items(d.doc.items)), "解组把 mid2 弄丢了"


# --- 真撤销栈：QUndoStack 走 attach 的那条路 ----------------------------------

@pytest.mark.skipif(not _HAVE_QT, reason="需要 PySide6 + offscreen")
def test_real_undo_stack_reinsert_is_not_broken_by_the_new_guard():
    """真 ``QUndoStack`` 撤销（组内删除 + 清空）后逐字节复原。

    与 ``tests/test_gui_layout_remove_undo.py``（A3）同款但断言点不同：那边钉
    「逆序回插的**下标日程**」，这里钉「新守卫没有把**合法回插**一起拒掉」——
    一旦误伤，撤销静默失效（组里少东西 / 顺序错位），画面上很难察觉。
    """
    from megapro.gui.canvas.undo_cmds import ClearCommand, RemoveItemsCommand
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a, b, c = _line(0.0, "a"), _line(20.0, "b"), _line(40.0, "c")
    G = Item(name="G", children=[a, b])
    lp._add_items([G, c])
    before_svg = document_to_svg(lp.doc)
    before_tops = [it.name for it in lp.doc.items]

    lp._undo.push(RemoveItemsCommand(lp, [a, b]))
    assert [x.name for x in G.children] == [], "删除没生效"
    lp._undo.undo()
    assert [x.name for x in G.children] == ["a", "b"], \
        "撤销没把组内叶子挂回原容器（被新守卫误拒？）"
    assert [it.name for it in lp.doc.items] == before_tops
    assert document_to_svg(lp.doc) == before_svg

    lp._undo.push(ClearCommand(lp))
    assert lp.doc.items == []
    lp._undo.undo()
    assert [it.name for it in lp.doc.items] == before_tops
    assert [x.name for x in G.children] == ["a", "b"]
    assert document_to_svg(lp.doc) == before_svg
    lp.deleteLater()
