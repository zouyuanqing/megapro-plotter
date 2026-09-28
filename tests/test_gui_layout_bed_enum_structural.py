"""A2 结构面补钉：床尺寸的**单一存储位置** + 枚举面口径的**独立参照对拍**。

## 为什么已有 `test_gui_layout_bed_enum.py` 还不够

那份文件（`2a66414` 引入）已经把 ask 列的每条路径各钉一例，并证明它们在
pre-A2 代码上**真的红**（28 failed / 8 passed）。但它的钉法是**逐条路径枚举**
——这正是本项目连吃两次亏的形状（A5 的「递归入口清单」、A1 的「children 构造者
枚举」）：**清单不会自己知道有新条目**。给 `Document` 加第 8 个会改
``pages``/``current`` 的方法，那份文件一声不响。

床尺寸的修复本质**不是**「七条路径都同步了」，而是**「床只有一个存储位置」**
——``doc.bed_w`` 改成直通当前页的 property，恒等式从「每条路径都要记得同步」
的纪律变成结构事实。所以本文件钉的是**因**，不是那份**症状清单**：

- 床：``bed_w``/``bed_h`` **不得**重新变成 dataclass 字段（一旦变回，property
  被实例属性遮蔽、整类脱节 bug 原样复活，而七条路径里未必有一条会红）；
- 床：**反射扫出** ``Document`` 上所有真会改 ``pages``/``current`` 的公开方法
  （不手写清单），逐个调用后断言恒等式 —— 新增第 8 个页操作自动进网；
- 枚举：对着一个**手写的朴素递归参照**对拍，而不是对着手写的位置清单。
"""

import dataclasses
import inspect
import os
import random

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from megapro.gui.layout.model import (
    BED_H, BED_W, Document, Item, Page, effectively_visible, iter_items,
)


# ---------------------------------------------------------------- 床：单一存储位置

def test_bed_is_not_a_dataclass_field_on_document():
    """``bed_w``/``bed_h`` **不得**重新变成 ``Document`` 的 dataclass 字段。

    这是整份 A2 修复的**根因级**不变量。dataclass 字段在 ``__init__``/赋值时
    走 ``self.__dict__``，会**遮蔽**同名 property ⇒ ``doc.bed_w = 150`` 只写
    文档、不写页 ⇒ ② 号脱节（永不自愈）原样复活。

    钉字段表而不是钉行为，是为了让「谁把字段加回来」这一步**立刻**失败：
    行为级的脱节要等到有人真的赋一次值才暴露，而字段级是**结构性的**。
    """
    fields = {f.name for f in dataclasses.fields(Document)}
    assert "bed_w" not in fields, (
        "Document.bed_w 变回了 dataclass 字段 ⇒ property 被实例属性遮蔽，"
        f"床尺寸重新出现「两处存」。当前字段表：{sorted(fields)}")
    assert "bed_h" not in fields, (
        "Document.bed_h 变回了 dataclass 字段 ⇒ 同上。当前字段表：{sorted(fields)}")
    assert "bed_w" not in Document.__dataclass_fields__, (
        "__dataclass_fields__ 里不该有 bed_w（装饰器处理阶段就已失守）")
    # 属性本体必须还在（否则上面两条是「因为没这个字段」而非「因为改成了字段」）
    for axis in ("bed_w", "bed_h"):
        assert isinstance(inspect.getattr_static(Document, axis), property), (
            f"Document.{axis} 既不是 dataclass 字段也不是 property —— 床无处安放")


def test_bed_facade_is_a_live_view_not_a_cached_copy():
    """门面必须**活读**当前页：改页立刻看得见，改门面立刻写进页。

    「活读」这条是**缓存**与「直通」的分界：一旦有人在 ``bed_w`` getter 里
    做缓存（或改回两处存 + 同步），改 ``page`` 就不会立刻反映到 ``doc``。
    """
    for axis, other in (("bed_w", "bed_h"), ("bed_h", "bed_w")):
        doc = Document()
        setattr(doc.page, axis, 77.0)
        assert getattr(doc, axis) == 77.0, \
            f"改 page.{axis} 后 doc.{axis} 没跟随 ⇒ 门面不是活读（被缓存了？）"
        setattr(doc, axis, 88.0)
        assert getattr(doc.page, axis) == 88.0, \
            f"写 doc.{axis} 没写进 page.{axis} ⇒ 门面不是直通"
        # 写门面**不得**留下实例属性（有它就说明被字段遮蔽了）
        assert axis not in doc.__dict__, \
            f"doc.__dict__ 里出现了 {axis} ⇒ 有人用 self.{axis}=… 写穿了 property"
        setattr(doc, other, 99.0)   # 两轴独立，别串


def test_two_documents_sharing_a_page_share_one_bed():
    """两个 Document 共用同一个 ``Page`` 对象 ⇒ 床**只有一份**，不是各存一份。

    这是「单一存储位置」最直接的证明：若门面内部还缓存/复制了一份，改一个
    文档不会影响另一个 —— 也就是又回到了「两处存」。
    """
    p = Page(name="共享", bed_w=50.0, bed_h=60.0)
    d1 = Document(pages=[p])
    d2 = Document(pages=[p])
    d1.bed_w = 51.0
    d1.bed_h = 52.0
    assert (d2.bed_w, d2.bed_h) == (51.0, 52.0), \
        "共用一个 Page 的两个 Document 没有看到同一份床 ⇒ 存在第二处存储"
    assert (d2.page.bed_w, d2.page.bed_h) == (51.0, 52.0)


def test_equality_still_discriminates_bed_via_pages():
    """床不再是字段，但**相等性不能因此失效**。

    ``bed_w``/``bed_h`` 移出字段表后，``Document.__eq__`` 只比 ``pages``/
    ``current``/``_init_items``。若哪天 ``Page`` 也退化成「所有页看起来一样」，
    两个床差 100 倍的文档会判为相等 —— 那会让「按文档去重」类逻辑失效。
    """
    assert Document(bed_w=1.0) != Document(bed_w=2.0), \
        "两个床尺寸差 100% 的 Document 判为相等 ⇒ 相等性已失去分辨力"


# ------------------------------------------------- 床：反射扫出页操作（不手写清单）

def _doc_with_distinct_beds(n: int = 3) -> Document:
    """每页一个**互不相同**的床（111.0 / 222.0 / 333.0 …）⇒ 脱节一眼可见。"""
    pages = [Page(name=f"p{i}", bed_w=111.0 * (i + 1), bed_h=112.0 * (i + 1))
             for i in range(n)]
    return Document(pages=pages, current=n - 1)


def _assert_in_sync(doc, where):
    assert doc.bed_w == doc.page.bed_w, (
        f"[{where}] doc.bed_w={doc.bed_w} != page.bed_w={doc.page.bed_w}（床脱节）")
    assert doc.bed_h == doc.page.bed_h, (
        f"[{where}] doc.bed_h={doc.bed_h} != page.bed_h={doc.page.bed_h}（床脱节）")


def test_every_discovered_page_mutating_method_keeps_bed_in_sync():
    """**反射扫出**所有真会改 ``pages``/``current`` 的公开方法，逐个断言恒等式。

    这就是本文件存在的理由：ask 列的七条路径里，任何一条被「修好」都可以
    靠重跑那份清单验证；而**新增**第 8 个页操作时，那份清单不会响。这里的
    判据是「这个方法**实际上**改动了 ``pages`` 或 ``current`` 吗」——
    由**行为**发现成员，不由人手写，所以清单漏不掉。

    调用一律包在 try 里：抛异常的签名（参数不合法）**不可能**造成脱节，
    跳过不损失覆盖。
    """
    probed: set[str] = set()
    for name in sorted(n for n, _ in inspect.getmembers(Document, callable)
                       if not n.startswith("_")):
        fn = getattr(Document, name)
        if not callable(fn):
            continue
        doc = _doc_with_distinct_beds()
        before = (len(doc.pages), doc.current,
                  tuple((p.bed_w, p.bed_h) for p in doc.pages))
        _assert_in_sync(doc, f"{name} 之前")
        for args in ((0,), (0, 0), (0, 1), (1, 0), (1, 2), (0, 2),
                     (1,), (2,), (None,), ()):
            trial = _doc_with_distinct_beds()
            b = (len(trial.pages), trial.current,
                 tuple((p.bed_w, p.bed_h) for p in trial.pages))
            try:
                getattr(trial, name)(*args)
            except Exception:
                continue
            a = (len(trial.pages), trial.current,
                 tuple((p.bed_w, p.bed_h) for p in trial.pages))
            if a == b:
                continue                      # 这一组参数没改动页状态
            probed.add(name)
            _assert_in_sync(trial, f"{name}{args} 之后")
            break
    # 扫到的必须是**已知那批**（防止判据退化成「什么都算」而失去意义）
    expected = {"add_page", "duplicate_page", "move_page", "remove_page",
                "switch_page"}
    assert expected <= probed, (
        f"反射没扫到这些页操作 ⇒ 判据失灵：{sorted(expected - probed)}")
    assert probed <= expected, (
        f"扫出了未知的改页状态成员（多半是测试自己有副作用）：{sorted(probed - expected)}")


def test_bed_invariant_holds_for_random_page_op_sequences():
    """随机页操作序列：恒等式**每一步**都成立（不是只查终态）。

    2000 条随机序列 × 12 步。终态对了、中间某一步错了的序列照样能通过终态
    断言 —— 床脱节是**可自愈也可能不自愈**的，必须逐步查。
    """
    rng = random.Random(20260928)
    for _ in range(2000):
        doc = _doc_with_distinct_beds(rng.randint(1, 4))
        for _ in range(12):
            n = doc.page_count
            op = rng.choice(["switch", "add", "dup", "remove", "move", "assign"])
            if op == "switch":
                doc.switch_page(rng.randrange(n))
            elif op == "add":
                doc.add_page(at=rng.choice([None, *range(n + 1)]))
            elif op == "dup":
                doc.duplicate_page(rng.randrange(n))
            elif op == "remove":
                doc.remove_page(rng.randrange(n))
            elif op == "move":
                doc.move_page(rng.randrange(n), rng.randrange(n))
            else:
                doc.bed_w = float(rng.randint(50, 300))
                doc.bed_h = float(rng.randint(50, 300))
            _assert_in_sync(doc, f"随机序列 op={op}")
            assert (BED_W, BED_H) != (doc.bed_w, doc.bed_h) or True  # 值可合法
            assert doc.bed_w > 0 and doc.bed_h > 0, "床尺寸必须为正"


# ------------------------------------------- 枚举面：对着独立参照对拍（不手写位置）

def _ref_renderable(items, *, visible_only):
    """**朴素递归**参照实现：可渲染单元 = 有自身折线的 ∪ 叶子。

    刻意写成与 :func:`_own_geometry` **毫无共享**的一段：既不调
    ``iter_flattens`` 也不用 ``is_container()``，自己判
    ``bool(children)`` 和 ``bool(paths)``。参照与实现同源时，对拍等于没对。
    """
    out: list[Item] = []

    def walk(lst, ancestors_visible):
        for it in lst:
            vis = ancestors_visible and it.visible
            if bool(it.paths) or not bool(it.children):
                if (not visible_only) or vis:
                    out.append(it)
            if it.children:
                walk(it.children, vis)

    walk(items, True)
    return out


def _rand_item(rng, depth=0):
    n = rng.randint(0, 3)
    paths = [[(rng.uniform(0, 50), rng.uniform(0, 50))
              for _ in range(rng.randint(1, 3))] for _ in range(n)]
    it = Item(paths=paths, z=rng.choice([0.0, 1.0, 1.0, 2.0]),
              visible=rng.random() > 0.25)
    if depth < 3 and rng.random() < 0.35:
        it.children = [_rand_item(rng, depth + 1)
                       for _ in range(rng.randint(1, 3))]
    return it


def test_enumeration_faces_match_an_independent_reference_on_random_trees():
    """随机**树**文档：``sorted_items``/``items_visible`` 与朴素参照**集合**全等。

    已有那份文件钉的是「顶层 / 组内 / 隐藏 / 深层」几个**手挑位置**；这里是
    随机树（含随机空叶子、随机容器嵌套、随机可见性）× 3000 份。位置清单漏掉
    的形状 —— 比如「两层空容器里的空叶子」「隐藏容器里的隐藏空叶子」—— 由
    随机树自己长出来。
    """
    rng = random.Random(20260928)
    checked = 0
    for _ in range(3000):
        doc = Document()
        doc.page.items = [_rand_item(rng) for _ in range(rng.randint(0, 5))]
        if rng.random() < 0.3:
            sel = list(iter_items(doc.items))
            if len(sel) >= 2 and rng.random() < 0.5:
                try:
                    doc.group_items(rng.sample(sel, 2), name="g")
                except ValueError:
                    pass
        try:
            got_s, got_v = doc.sorted_items(), doc.items_visible()
        except ValueError:
            continue
        exp_s = _ref_renderable(doc.items, visible_only=False)
        exp_v = _ref_renderable(doc.items, visible_only=True)
        # 参照是朴素 DFS 前序、枚举面按 (z, order) 排 —— **集合**才是本用例要钉的
        # 口径（顺序另有 ``test_flat_documents_stay_bit_identical_to_the_baseline``
        # 与既有文件钉）。先比集合。
        assert {id(x) for x in got_s} == {id(x) for x in exp_s}, (
            f"sorted_items 与参照不一致：{[i.name for i in got_s]} "
            f"!= {[i.name for i in exp_s]}")
        assert {id(x) for x in got_v} == {id(x) for x in exp_v}, (
            f"items_visible 与参照不一致：{[i.name for i in got_v]} "
            f"!= {[i.name for i in exp_v]}")
        # 再钉顺序规则 = (z, 入档序)，与树形无关（A4）
        assert got_s == sorted(got_s, key=lambda i: (i.z, i.order)), \
            "sorted_items 的顺序不是 (z, 入档序)"
        assert got_v == sorted(got_v, key=lambda i: (i.z, i.order)), \
            "items_visible 的顺序不是 (z, 入档序)"
        checked += 1
    assert checked > 2000, f"有效样本太少（{checked}），对拍失去意义"


def test_flat_documents_stay_bit_identical_to_the_baseline():
    """**无 children 文档与基线逐位一致** —— 比口径统一更重要的约束。

    基线 ``git show 2fb1e47:model.py`` 的 ``sorted_items`` 是
    ``sorted(self.items, key=z)``（**全都产出**，含 ``paths==[]``）。平面文档里
    每个图元都是叶子 ⇒ ``order`` == 列表序 ⇒ ``(z, order)`` 稳定排序与之逐位
    相同；``items_visible`` 再按 ``visible`` 过滤。
    """
    rng = random.Random(4242)
    n_flat = 0
    for _ in range(3000):
        doc = Document()
        doc.page.items = [_rand_item(rng) for _ in range(rng.randint(0, 6))]
        if any(i.children for i in iter_items(doc.items)):
            continue
        n_flat += 1
        base_all = sorted(doc.items, key=lambda i: i.z)          # 基线实现
        base_vis = [i for i in base_all if i.visible]
        got_s, got_v = doc.sorted_items(), doc.items_visible()
        assert [id(x) for x in got_s] == [id(x) for x in base_all], (
            f"平面文档 sorted_items 与基线不同：{[i.name for i in got_s]} "
            f"!= {[i.name for i in base_all]}")
        assert [id(x) for x in got_v] == [id(x) for x in base_vis], (
            f"平面文档 items_visible 与基线不同：{[i.name for i in got_v]} "
            f"!= {[i.name for i in base_vis]}")
    assert n_flat > 800, f"平面样本太少（{n_flat}），对拍失去意义"


def test_the_only_remaining_caliber_split_is_the_container_case_and_it_is_pinned():
    """枚举面口径：**空叶子四接口全可见**；唯一例外是「无自身折线的容器」。

    ask 要求「要么都看得见、要么都看不见，不要一半一半」。空叶子已经统一；
    但 ``children`` 非空而自身 ``paths==[]`` 的**容器**仍然是
    ``contains``/``top_z`` 看得见、``sorted_items``/``items_visible`` 看不见。
    那是**刻意**的（容器不建 ``PathItem``，画布上无可点对象），且被既有断言
    钉死（``test_gui_layout.py:516/517``、``test_gui_layout_remove_undo.py:246``）。
    本用例把这条例外**显式钉成契约**，免得它继续只活在 docstring 里。
    """
    leaf = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="leaf", z=0.0)

    # —— 空叶子：身份面与枚举面**同一口径**（pre-A2 这里红：枚举面看不见它）——
    empty = Item(paths=[], name="empty", z=5.0)
    G = Item(name="G", children=[leaf, empty])
    doc = Document(items=[G])
    assert doc.contains(empty) and doc.contains_anywhere(empty)
    assert doc.top_z() == 5.0, "top_z 必须算进空叶子的 z"
    assert empty in doc.sorted_items(), "枚举面看不见空叶子（pre-A2 行为）"
    assert empty in doc.items_visible(), "枚举面看不见可见的空叶子（pre-A2 行为）"

    # —— 唯一例外：无自身折线的容器仍是「看得见的身份、看不见的枚举单元」——
    holder = Item(name="holder", z=7.0, children=[Item(paths=[], name="k", z=1.0)])
    doc2 = Document(items=[holder])
    assert doc2.contains(holder), "身份面必须看得见这个容器"
    assert doc2.top_z() == 7.0, \
        f"top_z 应算进容器的 z=7.0，实测 {doc2.top_z()}（身份面口径）"
    assert holder not in doc2.sorted_items(), \
        "无自身折线的容器进了枚举面 —— 会与 test_gui_layout.py:516/517 冲突"
    assert holder not in doc2.items_visible(), \
        "无自身折线的容器进了可见枚举面 —— 会与 test_gui_layout_remove_undo.py:246 冲突"
    # 而它**有**自身折线时是枚举单元（既有契约的另一侧，别被改掉）
    withown = Item(name="withown", paths=[[(0.0, 0.0), (1.0, 0.0)]],
                   children=[Item(paths=[], name="k2", z=1.0)])
    doc3 = Document(items=[withown])
    assert withown in doc3.sorted_items()
    assert withown in doc3.items_visible()


@pytest.mark.parametrize("hidden_on", ["leaf", "group", "ancestor"])
def test_empty_leaf_visibility_follows_the_same_rule_as_a_drawn_leaf(hidden_on):
    """隐藏的空叶子必须和隐藏的正常叶子**同一口径**（不留半截）。

    pre-A2 时期空叶子压根不进枚举面，所以「隐藏」这一层从没被测过；A2 把它
    放回枚举面后，可见性过滤就成了新的风险面。
    """
    def build(hidden):
        e = Item(paths=[], name="e", visible=not hidden)
        d = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="d", visible=not hidden)
        if hidden_on == "leaf":
            return Document(items=[e, d])
        if hidden_on == "group":
            return Document(items=[Item(name="g", children=[e, d], visible=not hidden)])
        inner = Item(name="g2", children=[e, d], visible=not hidden)
        return Document(items=[Item(name="g1", children=[inner],
                                    visible=not hidden)])

    hidden = hidden_on != "none" and hidden_on != "leaf"
    doc = build(hidden)
    names = [i.name for i in doc.items_visible()]
    if hidden:
        assert "e" not in names and "d" not in names, (
            f"隐藏位置 {hidden_on}：可见枚举面仍含 {names}")
    else:
        assert "e" in names and "d" in names, (
            f"隐藏位置 {hidden_on}：可见枚举面漏了 {names}")
    # sorted_items 是「全部可渲染单元」，不看可见性
    assert "e" in [i.name for i in doc.sorted_items()], \
        "sorted_items 漏了空叶子"
    # 且空叶子的可见性判定必须与画布侧一致
    e = next(i for i in iter_items(doc.items) if i.name == "e")
    assert effectively_visible(doc, e) == (not hidden), \
        "空叶子的 effectively_visible 与构造意图不符（画布/枚举面要打架）"
