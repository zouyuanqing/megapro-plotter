"""A3 回归：三条静默的数据结构缺口。

## 一、``group_items`` 不校验成员归属当前页（本批**已修**，纯 model 层）

两种成员会静默把模型搞坏：

- **跨页成员**（挂在别的页）：``Page.remove`` 在当前页找不到它 ⇒ ``info is
  None`` ⇒ 它**没被摘走**却被 ``cont.children.append`` 收进新容器。实测
  ``doc.group_items([页0的i0, 页1的i1])`` 得到容器落在页1、``G.children=
  ['i1','i0']``，而页0 的 ``items`` 仍是 ``['i0']`` ⇒ **i0 同时属于两页**
  ⇒ 同一几何在两页各切一次。
- **文档外悬空成员**：同样 ``info is None`` ⇒ 同样被收进容器 ⇒
  ``contains(悬空项)`` 由 False 静默变 True、``flatten_visible`` 条数由 0 变 2
  ⇒ **一个从未入模的图元凭空进了切割序列**。

旧的「``if not placements`` 全悬空才回滚」只兜住**全部**悬空的情形，**混合**
选择照样放行。

## 二、容器自身 ``paths`` 非空时「导出切但画布不画」（**未修，跨文件**）

实测 ``c = Item(paths=[[(0,0),(300,0)]], children=[kid])``：
``flatten_visible`` 吐 2 条（含 x=300 超床的 own 折线）、``items_visible()`` 含
``C``、``iter_leaves`` 只有 ``[kid]`` ⇒ 越界预检会报、导出会切、**画布上什么
都没有**。

⚠ **ask 里「两个用例把相反契约钉死了」这个说法不准**（本文件实测）：
``test_container_is_not_a_render_unit``（tests/test_gui_layout.py:550-578）用的
是 ``G.paths == []`` 的容器，它钉的是「**无自身折线**的容器不是渲染单元」——
这条在任何修法下都成立、都是对的。真正的矛盾在**实现**（``iter_leaves`` 与
``undo_cmds.make_gi``）与 ``test_container_own_geometry_reaches_enumeration``
（:527-547）之间，不在这两个用例之间。故本批**没有**改这两个既有用例。

修法落在**本批无权修改**的文件：``canvas/undo_cmds.py:79``
（``if item.is_container(): return None``）与 ``layout_page.py`` 的同步路径。
本文件用 ``test_container_own_paths_is_cut_but_not_drawn__KNOWN_GAP`` 把这个
缺口**钉成可见的刻画测试**，谁改画布侧时它会提示同步更新。

## 三、删光整组残留僵尸顶层 Item（**未修，跨文件**）

删光整组后顶层留下 ``cont``（``children=[]``/``paths=[]``/``is_container()=False``），
``contains(cont)`` 仍为 True；场景项退化成一个 1e-6mm 的空矩形（``items.py:55``
的退化尺寸保护）⇒ 实际不可见、点不中、删不掉。

⚠ **朴素的修法（在 ``Page.remove`` 里级联删掉空容器）会打断撤销**，本文件用
``test_cascading_remove_would_break_undo`` 把这个反证钉住：级联后 undo 侧那句
``attach(child, owner=空容器)`` 会把子项挂进一个**已不在文档里**的孤儿容器
⇒ ``contains(child)`` 为 False ⇒ **撤销后子项彻底消失**（静默丢数据，比幽灵
更糟）。真正的修法要 ``DetachInfo`` 能表达「容器 + 它的最后一个子项」两份归属，
消费方 ``undo_cmds.RemoveItemsCommand._do_undo`` 也得跟着改 —— 不在本批所有权内。
"""

import os
import random

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from megapro.gui.layout.model import (
    Document, Item, Page, flatten_visible, iter_items, iter_leaves,
)


def _line(x, name, *, z=0.0, paths=None):
    return Item(
        paths=paths if paths is not None
        else [[(float(x), 0.0), (float(x) + 10.0, 0.0)]],
        name=name, z=z)


def _occurrences(doc: Document, item: Item) -> int:
    """该对象在**整个文档**的树里出现了几次（按身份）。"""
    return sum(1 for page in doc.pages for cur in iter_items(page.items)
               if cur is item)


# ───────────────────────────── 一、编组成员归属（已修）

def test_cross_page_members_are_rejected_and_never_land_in_two_pages():
    """跨页成员：整单拒绝，且**同一个对象绝不会出现在两页**。

    旧行为：容器落在当前页、``G.children=['i1','i0']``、页0 的 items 仍是
    ``['i0']`` ⇒ i0 被两页共享 ⇒ 同一几何在两页各切一次。
    """
    doc = Document()
    doc.add_page()
    doc.add_page()
    doc.switch_page(0)
    i0 = _line(0, "i0")
    doc.add(i0)
    doc.switch_page(1)
    i1 = _line(20, "i1")
    doc.add(i1)
    before0 = [x.name for x in doc.pages[0].items]
    before1 = [x.name for x in doc.page.items]
    before_flat = len(flatten_visible(doc))

    cont = doc.group_items([i0, i1], name="跨页组")

    assert cont is None, "跨页成员必须整单拒绝（返回 None），不该编出容器"
    assert [x.name for x in doc.pages[0].items] == before0, "页0 被改动了"
    assert [x.name for x in doc.page.items] == before1, "页1 被改动了"
    assert _occurrences(doc, i0) == 1, \
        f"i0 出现在 {_occurrences(doc, i0)} 处 ⇒ 同一几何被切两遍"
    assert _occurrences(doc, i1) == 1
    assert len(flatten_visible(doc)) == before_flat, "拍平条数变了（几何凭空增减）"


def test_dangling_member_is_rejected_and_never_enters_the_document():
    """文档外悬空成员：整单拒绝，悬空项**不**凭空入模。

    旧行为：``contains(dangling)`` 由 False 静默变 True、``flatten_visible``
    条数由 0 变 2。
    """
    doc = Document()
    a = _line(0, "a")
    doc.add(a)
    dangling = _line(99, "dangling")
    assert not doc.contains(dangling), "样例前提错：悬空项本就不该在文档里"
    before_flat = len(flatten_visible(doc))

    cont = doc.group_items([a, dangling], name="含悬空")

    assert cont is None, "悬空成员必须整单拒绝"
    assert not doc.contains(dangling), \
        "悬空项凭空进了文档 ⇒ 一个从未入模的图元出现在切割序列里"
    assert _occurrences(doc, dangling) == 0
    assert len(flatten_visible(doc)) == before_flat, "拍平条数变了（几何凭空入模）"
    assert [x.name for x in doc.items] == ["a"], "拒绝不得有副作用"


def test_mixed_dangling_and_cross_page_selections_are_all_or_nothing():
    """**混合**选择（在/不在/跨页）也整单拒绝 —— 旧回滚只兜「全悬空」。

    这是旧实现真正漏的那一格：``if not placements`` 只在**所有**成员都摘不
    掉时才回滚，部分摘得掉就照常把摘不掉的一并塞进容器。
    """
    doc = Document()
    doc.add_page()
    in0 = _line(0, "in0")
    doc.add(in0)
    other = _line(20, "other")
    doc.add(other)
    doc.switch_page(1)
    far = _line(40, "far")
    doc.add(far)
    dangling = _line(99, "dangling")

    for members, tag in (
        ([in0, dangling], "在页 + 悬空"),
        ([in0, far], "在页 + 跨页"),
        ([in0, other, far], "两个在页 + 一个跨页"),
        ([in0, other, dangling], "两个在页 + 一个悬空"),
        ([in0, other, far, dangling], "全都有问题"),
    ):
        doc.switch_page(0)
        snapshot = ([x.name for x in doc.pages[0].items],
                    [x.name for x in doc.pages[1].items],
                    len(flatten_visible(doc)))
        cont = doc.group_items(members, name="混合")
        assert cont is None, f"[{tag}] 必须整单拒绝"
        now = ([x.name for x in doc.pages[0].items],
               [x.name for x in doc.pages[1].items],
               len(flatten_visible(doc)))
        assert now == snapshot, f"[{tag}] 拒绝有副作用：{snapshot} -> {now}"
        for m in members:
            assert _occurrences(doc, m) <= 1, f"[{tag}] {m.name} 出现在两页"


def test_legal_grouping_still_works_after_the_ownership_check():
    """加了归属校验**不得**误伤正常编组（含「成员在组内」的合法嵌套编组）。"""
    doc = Document()
    p, q = _line(0, "p"), _line(20, "q")
    doc.add(p)
    doc.add(q)
    before_flat = flatten_visible(doc)

    cont = doc.group_items([p, q], name="正常组")
    assert cont is not None, "合法编组被误拒"
    assert [c.name for c in cont.children] == ["p", "q"]
    assert flatten_visible(doc) == before_flat, "编组改变了拍平几何"
    assert cont in list(iter_items(doc.items))

    # 合法嵌套：把已有的组和另一个散件编成新组（两者都在当前页）
    r = _line(40, "r")
    doc.add(r)
    outer = doc.group_items([cont, r], name="外层")
    assert outer is not None, "合法的组套组被误拒"
    assert [c.name for c in outer.children] == ["正常组", "r"]


def test_no_item_object_is_ever_present_on_two_pages_under_random_ops():
    """**结构化不变量**：随机页操作 + 随机编组，任一时刻同一对象都只在一页。

    ask 点名要的一条。它不枚举「哪条操作会坏事」，而是随机走一遍所有页操作与
    编组（含跨页选择），每步查一遍全局不变量 —— 新增页操作会自动进网。
    """
    rng = random.Random(20260928)
    checked = 0
    for _ in range(400):
        doc = Document(pages=[Page(name=f"p{i}") for i in range(rng.randint(1, 3))],
                       current=0)
        doc.switch_page(0)
        for k in range(rng.randint(1, 4)):
            doc.add(_line(10.0 * k, f"t{len(doc.pages)}-{k}"))
        for _ in range(14):
            n = doc.page_count
            op = rng.choice(["switch", "add", "dup", "remove", "move", "group"])
            if op == "switch":
                doc.switch_page(rng.randrange(n))
            elif op == "add":
                doc.add_page()
            elif op == "dup":
                doc.duplicate_page(rng.randrange(n))
            elif op == "remove":
                doc.remove_page(rng.randrange(n))
            elif op == "move":
                doc.move_page(rng.randrange(n), rng.randrange(n))
            else:
                # ⚠ 必须**故意**混入别页成员与悬空项，否则这条不变量在旧实现上
                # 恒真（只编当前页的成员时不可能跨页）—— 那样它就不是回归测试。
                # 这里三分之二的概率走「跨页 / 含悬空」的危险选择。
                pool = list(iter_items(doc.page.items))
                other_page = rng.choice([p for p in doc.pages if p is not doc.page]
                                        or [doc.page])
                foreign = list(iter_items(other_page.items))
                dangling = _line(999.0, f"dangling{rng.randrange(10**6)}")
                pool = pool + (foreign if rng.random() < 0.5 else []) \
                    + ([dangling] if rng.random() < 0.4 else [])
                if len(pool) >= 2:
                    k = rng.randint(2, min(4, len(pool)))
                    doc.group_items(rng.sample(pool, k), name="g")
            # —— 每一步都查全局不变量 ——
            seen: dict[int, int] = {}
            for pi, page in enumerate(doc.pages):
                for cur in iter_items(page.items):
                    seen[id(cur)] = seen.get(id(cur), 0) + 1
            dupes = {k: v for k, v in seen.items() if v > 1}
            assert not dupes, \
                f"op={op} 之后有 {len(dupes)} 个对象出现在多处（同一切割次数）"
            checked += 1
    assert checked > 4000, f"检查步数太少（{checked}）"


# ─────────────────────────── 二、容器自身 paths（未修：跨文件，刻画测试）

def test_container_own_paths_is_cut_but_not_drawn__KNOWN_GAP():
    """**已知缺口**（A3 证据二）：容器 own 折线被切，但画布不画它。

    五个面的现状：``iter_leaves``（渲染面）排除 C、``items_visible`` 含 C、
    ``flatten_visible`` 切 C、越界预检（``iter_units``）报 C、``make_gi`` 因
    ``is_container()`` 返回 None 不建场景项 ⇒ **导出会切、画布上什么都没有**。

    ⚠ 本用例是**刻画测试**，钉的是「缺口仍然存在」。修法落在
    ``canvas/undo_cmds.py:79``（``make_gi``）与 ``layout_page`` 的同步路径，
    不在本批所有权内。**谁把它修好，请连同本用例一起更新**（把
    ``iter_leaves`` 那一行改成「含 C」），否则模型层与画布层会各说各话。
    """
    kid = _line(10, "kid")
    c = Item(paths=[[(0.0, 0.0), (300.0, 0.0)]], name="C", children=[kid])
    doc = Document(items=[c])

    # 拍平面：切它（x=300 超床）
    assert len(flatten_visible(doc)) == 2
    # 枚举面 / 越界预检面：看得见
    assert [i.name for i in doc.items_visible()] == ["C", "kid"]
    # 渲染面：看不见 —— **这就是缺口**
    assert list(iter_leaves(doc.items)) == [kid], \
        "iter_leaves 现在包含 C 了 ⇒ 画布侧要么已跟上（更新本用例），要么出现了新的半截状态"


def test_pathless_container_is_still_not_a_render_unit():
    """对照侧（**必须保持**）：``paths==[]`` 的容器仍然不是渲染单元。

    这条与上面那条**不矛盾** —— 上面那个容器**有**自身折线，是有东西可画的；
    这个没有。把两者混为一谈会得出「容器该建场景项」的错误结论，而那正是
    ``test_container_is_not_a_render_unit``（test_gui_layout.py:550-578）当初
    钉死的「隐形可点幽灵」防线。
    """
    kid = _line(10, "kid")
    g = Item(name="G", children=[kid])
    assert g.paths == []
    assert g.is_container()
    assert g.bbox() != (0.0, 0.0, 0.0, 0.0), \
        "样例前提错：路径容器 bbox 递归后非零，正是幽灵命中区的成因"
    assert list(iter_leaves([g])) == [kid]


# ─────────────────────────────── 三、僵尸空容器（未修：跨文件，刻画测试）

def test_emptying_a_group_leaves_an_invisible_undeletable_zombie__KNOWN_GAP():
    """**已知缺口**（A3 证据三）：删光整组后留下一个幽灵顶层 Item。

    危害：``contains(cont)`` 为 True（文档**声称拥有**它），但它没有几何
    ⇒ ``PathItem`` 退化成一个 1e-6mm 的空矩形（``canvas/items.py:55`` 的退化
    尺寸保护）⇒ 用户看不见、点不中、也删不掉。

    ⚠ 刻画测试：钉的是「缺口仍然存在」。修法需要 ``DetachInfo`` 能表达
    「容器 + 它的最后一个子项」两份归属，消费方
    ``undo_cmds.RemoveItemsCommand._do_undo`` 也得跟着改 —— 跨文件，不在本批
    所有权内。
    """
    doc = Document()
    x, y = _line(0, "x"), _line(20, "y")
    g = Item(name="组", children=[x, y])
    doc.add(g)
    doc.remove(x)
    doc.remove(y)

    assert [i.name for i in doc.items] == ["组"], "样例前提错：僵尸应留在顶层"
    cont = doc.items[0]
    assert cont.children == [] and cont.paths == []
    assert not cont.is_container(), "僵尸已经不算容器了"
    assert cont.bbox() == (0.0, 0.0, 0.0, 0.0), "无几何 ⇒ PathItem 退化成空矩形"
    assert doc.contains(cont), "文档仍声称拥有它"
    assert len(flatten_visible(doc)) == 0, "它没有几何，不影响切割（危害仅限模型泄漏）"


def test_cascading_remove_would_break_undo__why_the_naive_fix_is_refuted():
    """**反证**：在 :meth:`Page.remove` 里级联删空容器会让撤销**丢数据**。

    这是最容易被提出来的修法，先用测试把它的后果钉死，免得有人直接改上去：

    1. 撤销侧那句 ``doc.attach(child, owner=info.owner, index=info.index)``
       的 ``info.owner`` 是**级联时已被摘掉**的空容器；
    2. 于是 child 被挂进一个**不在文档里**的孤儿容器；
    3. ``doc.contains(child)`` 为 False ⇒ **撤销后子项彻底消失** ——
       比留下幽灵更糟（幽灵至少还在，切纸机不会少切）。

    真正的修法要让 ``DetachInfo`` 携带**两段**归属（容器自己的 + 它最后一个
    子项的），并由 ``undo_cmds`` 的 undo 侧按序复原 —— 那是跨文件改动。
    """
    doc = Document()
    x, y = _line(0, "x"), _line(20, "y")
    g = Item(name="组", children=[x, y])
    doc.add(g)

    # 手工复刻「级联」会产生的状态：子项摘走、**容器也**被摘走
    info = doc.remove(x)
    assert info.owner is g
    doc.remove(g)                                   # ← 级联的那一步
    assert not doc.contains(g), "容器已不在文档里"

    # 撤销侧会做的事：把 child 挂回那个 owner
    doc.attach(x, owner=info.owner, index=info.index)

    assert not doc.contains(x), \
        "样例前提错：级联后撤销竟然把子项救回来了（那这个反证不成立）"
    assert x not in list(iter_items(doc.items)), "子项彻底消失 ⇒ 静默丢数据"
    assert len(flatten_visible(doc)) == 0, "x 的几何在撤销后不见了 ⇒ 少切"
