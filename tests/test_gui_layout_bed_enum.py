"""A2 回归：床尺寸同步有漏路径；枚举面对 ``paths==[]`` 图元口径不一致。

## 缺陷一：床尺寸（两条存储 + 手工同步 = 五条漏同步路径，全部**静默**）

M4 的设计是「``Document`` 与 ``Page`` 各存一份 + ``_sync_page_bed`` 手工同步」，
实测五条路径不同步：

1. ``Document(pages=[Page(bed_w=100.0, bed_h=120.0)])`` ⇒ 调用方给的 100/120
   被构造时的同步**顶成 210**（静默销毁）。
2. ``d = Document(); d.bed_w = 150.0`` ⇒ 文档改了、页没改；且
   ``switch_page`` 指向当前页时提前 return 不 pull ⇒ **脱节永不自愈**。
3. 两页 bed_w = 111/222、``current=1``，``remove_page(1)`` ⇒ ``current`` 落到 0
   而门面值仍是 222。
4. ``duplicate_page`` 完全不碰 ``current``，复制当前页**之前**的页时
   ``current`` 静默落到克隆体上，门面值没跟上。
5. 复核补测：``move_page`` 改 ``current`` 同理（复核时未复现出独立症状，
   但它和 ③④ 走的是同一个「改了 current 却不同步」的缺口）。

**修法（收敛，不补齐）**：把 :class:`Page` 提成床尺寸的**唯一真源**，
``Document.bed_w/bed_h`` 改成**读写直通**当前页的 property。于是
「``doc.bed_w`` 恒等于 ``doc.page.bed_w``」不再是**每条路径都要记得同步**的纪律，
而是「只有一个存储位置」的结构事实 —— 补齐五条一样容易漏第六条。
``_sync_page_bed`` 随之删除（两处存储的同步机器，正是那五条漏路径的来源）。

## 缺陷二：枚举面对 ``paths==[]`` 的口径

原 ``_own_geometry`` 带 ``if paths:`` 过滤，把 ``paths==[]`` 的图元**一律**丢掉。
于是同一份文档里 ``contains(empty)=True``、``top_z()`` 算进它的 z、
``remove(empty)`` 摘得掉，``sorted_items()``/``items_visible()`` 却**看不见**
它 —— 枚举面看不见、身份面看得见。

对拍（基线 ``git show 2fb1e47:src/megapro/gui/layout/model.py`` 当第二实现，
4000 份随机**平面**文档，每图元随机 0-3 条折线、z 有意制造并列）：
修复前 ``sorted_items`` 2016/4000 不一致、``items_visible`` 1801/4000 不一致；
把生成器改成每图元至少一条折线后两者都 0 不一致 ⇒ 差异唯一成因就是
``paths==[]``。修复后两种配置**全部 0/4000**。

**判据改成「有折线」或「不是容器」**：空的**叶子**照常产出，**空容器**仍不产出。
空容器那条是刻意保留的，不是遗漏 —— 四条既有断言把它钉死了：
``test_gui_layout.py:516/517``（``sorted_items()`` 不含 ``组``）、
``test_gui_layout.py:544``（自带折线的容器 ``C`` **在**结果里）、
``test_gui_layout_remove_undo.py:246``（``items_visible()`` 每一项都必须
``_gi_for(...) is not None``，而**容器不建 PathItem**）。
即本方法的含义是「**可渲染单元**」，与 ``contains`` 的「对象在不在文档里」
是两个不同的问法。详见 ``model.py`` 里 ``_own_geometry`` 的 docstring。

本文件钉的断言全部是**不变量**（恒等式），不是某个具体数值或调用序：
「任何一条路径之后，``doc.bed_*`` 恒等于 ``doc.page.bed_*``」。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import random

import pytest

from megapro.gui.layout.model import (
    Document, Item, Page, flatten_visible, iter_units,
)

BED_A, BED_B = 111.0, 222.0


# --- 断言工具 ----------------------------------------------------------------

def assert_bed_in_sync(doc, tag):
    """**核心不变量**：门面值恒等于当前页的值（两个轴都要）。

    钉的是恒等式而不是某个数值 —— 换实现、换默认值都假不了红；而旧实现的
    五条漏路径**全部**会让它变红。
    """
    assert doc.bed_w == doc.page.bed_w, (
        f"[{tag}] doc.bed_w={doc.bed_w} != 当前页 bed_w={doc.page.bed_w}"
        f"（current={doc.current}/{doc.page_count}）")
    assert doc.bed_h == doc.page.bed_h, (
        f"[{tag}] doc.bed_h={doc.bed_h} != 当前页 bed_h={doc.page.bed_h}"
        f"（current={doc.current}/{doc.page_count}）")


def _line(x0, name, z=0.0):
    return Item(paths=[[(float(x0), 0.0), (float(x0) + 10.0, 0.0)]],
                name=name, z=z)


# --- 缺陷一：床尺寸，每条列出的路径各一例 -------------------------------------

def test_constructor_keeps_the_bed_the_caller_declared_on_the_page():
    """路径①：``Document(pages=[Page(bed_w=100, bed_h=120)])`` 不得被顶成 210。

    旧实现：``__post_init__`` 无条件把 Document 的默认值写进页 ⇒ 调用方声明的
    100/120 被**静默销毁**。本条钉的是**语义**（页声明的值是权威），不只是恒等式。
    """
    d = Document(pages=[Page(name="custom", bed_w=100.0, bed_h=120.0)])
    assert (d.pages[0].bed_w, d.pages[0].bed_h) == (100.0, 120.0), \
        "构造把调用方声明的床尺寸销毁了"
    assert_bed_in_sync(d, "构造 pages=[Page(100,120)]")


def test_explicit_bed_kwarg_wins_over_the_page_default():
    """显式传 ``bed_w=`` 时以关键字为准（构造语义的另一半，不能含糊）。"""
    d = Document(pages=[Page(bed_w=100.0)], bed_w=300.0)
    assert d.bed_w == 300.0 and d.page.bed_w == 300.0
    assert_bed_in_sync(d, "构造 pages+bed_w=300")


def test_assignment_writes_through_to_the_current_page():
    """路径②：``d.bed_w = 150.0`` 必须**同时**改页（旧实现只改文档）。"""
    d = Document()
    d.bed_w = 150.0
    d.bed_h = 160.0
    assert (d.page.bed_w, d.page.bed_h) == (150.0, 160.0), \
        "赋值没有写进当前页 —— 门面与页脱节"
    assert_bed_in_sync(d, "赋值后")


def test_desync_never_heals_itself_is_now_impossible():
    """路径②的后半：脱节**永不自愈**（切回当前页时提前 return 不 pull）。

    新设计下没有「两处存」，故「自愈」这个概念不存在 —— 断言的是切页来回后
    仍然恒等，且**值没被莫名改掉**。
    """
    d = Document()
    d.bed_w = 150.0
    d.add_page()
    d.switch_page(1)
    assert_bed_in_sync(d, "切到新页")
    d.switch_page(0)
    assert d.bed_w == 150.0, "来回切页把调用方设的床尺寸弄丢了（旧实现的脱节回拉）"
    assert_bed_in_sync(d, "切回原页")
    d.switch_page(0)                     # 指向当前页：旧实现提前 return
    assert_bed_in_sync(d, "switch_page(当前页)")
    assert d.bed_w == 150.0


def test_switch_page_follows_the_page():
    """``switch_page`` 后门面字段跟随目标页（这是门面**本该**有的行为）。"""
    d = Document()
    d.pages.append(Page(name="p1", bed_w=BED_B, bed_h=BED_B))
    d.current = 1
    assert d.bed_w == BED_B
    assert d.switch_page(0)
    assert d.bed_w == 210.0, "切回第 0 页没有跟随该页的床尺寸"
    assert_bed_in_sync(d, "switch_page 后")


def test_add_page_does_not_desync():
    """``add_page`` 之后恒等（新增页不是当前页，不该动门面值）。"""
    d = Document()
    d.bed_w = 55.0
    d.add_page()
    assert_bed_in_sync(d, "add_page 后")
    d.add_page(at=0)
    assert_bed_in_sync(d, "add_page(at=0) 后")
    assert d.current == 0, "add_page 不该切页"


def test_remove_current_page_does_not_desync():
    """路径③：两页 111/222、``current=1``，``remove_page(1)`` 后恒等。

    旧实现：``current`` 落到 0 而门面值仍是 222。
    """
    d = Document()
    d.pages[0].bed_w = BED_A
    d.pages.append(Page(name="p1", bed_w=BED_B, bed_h=BED_B))
    d.current = 1
    assert_bed_in_sync(d, "删前")
    assert d.remove_page(1)
    assert d.current == 0
    assert_bed_in_sync(d, "remove_page(当前页) 后")
    # current 落到第 0 页 ⇒ 门面必须报第 0 页的床（111），不是已删页的 222
    assert d.bed_w == BED_A, \
        f"删掉当前页后门面应跟随新当前页（{BED_A}），实际 {d.bed_w}"


def test_remove_non_current_page_does_not_desync():
    """对照：删**非**当前页（修复前就正确的一侧，仍钉住不许回归）。"""
    d = Document()
    d.pages[0].bed_w = BED_A
    d.pages.append(Page(name="p1", bed_w=BED_B))
    d.current = 1
    assert d.remove_page(0)
    assert d.current == 0
    assert_bed_in_sync(d, "remove_page(非当前) 后")
    assert d.bed_w == BED_B


def test_duplicate_page_before_current_does_not_desync():
    """路径④：``duplicate_page`` 复制当前页**之前**的页时 ``current`` 落到克隆体。

    旧实现：``current`` 静默指向克隆体（111），而门面值还是原第 1 页的 222。
    """
    d = Document()
    d.pages[0].bed_w = BED_A
    d.pages.append(Page(name="p1", bed_w=BED_B, bed_h=BED_B))
    d.current = 1
    assert_bed_in_sync(d, "复制前")
    new = d.duplicate_page(0)
    assert new == 1
    assert_bed_in_sync(d, "duplicate_page(0) 后")
    assert d.bed_w == BED_A, "current 落到克隆体，门面却没跟着克隆体的床尺寸走"


def test_duplicate_current_page_does_not_desync():
    """对照：复制**当前**页（``current`` 不变，修复前也正确，仍钉住）。"""
    d = Document()
    d.pages[0].bed_w = BED_A
    d.current = 0
    d.duplicate_page(0)
    assert_bed_in_sync(d, "duplicate_page(当前) 后")
    assert d.bed_w == BED_A


def test_move_page_does_not_desync():
    """``move_page`` 改 ``current``（路径⑤）后恒等。"""
    d = Document()
    d.pages.append(Page(name="p1", bed_w=BED_B, bed_h=BED_B))
    d.current = 1
    assert_bed_in_sync(d, "move 前")
    assert d.move_page(1, 0)
    assert d.current == 0
    assert_bed_in_sync(d, "move_page(1->0) 后")
    assert d.bed_w == BED_B


@pytest.mark.parametrize("op", [
    lambda d: d.add_page(),
    lambda d: d.add_page(at=0),
    lambda d: d.duplicate_page(0),
    lambda d: d.duplicate_page(1),
    lambda d: d.move_page(0, 1),
    lambda d: d.remove_page(1),
    lambda d: d.switch_page(1),
])
def test_bed_stays_in_sync_after_every_page_op(op):
    """组合压力：随机前缀的一串页操作后，恒等式**每一步**都成立。

    逐路径各一例已经钉住；这条再钉「**任意顺序**的组合」也不会漏 —— 旧实现
    只要碰上那四条中的任意一条就红。
    """
    rnd = random.Random(20260928)
    d = Document()
    d.bed_w, d.bed_h = 30.0, 40.0
    assert_bed_in_sync(d, "起点")
    for step in range(60):
        d.bed_w = rnd.choice([30.0, 40.0, 50.0])   # 中途改门面
        d.bed_h = rnd.choice([60.0, 70.0])
        if d.page_count < 3:
            d.add_page()
        op(d)
        assert_bed_in_sync(d, f"第 {step} 步")


def test_pages_can_have_independent_beds():
    """设计意图本身：床是**每页**的，两页不同床各页自守（不是全局只有一个值）。"""
    d = Document()
    d.pages.append(Page(name="p1", bed_w=BED_B, bed_h=BED_B))
    d.current = 1
    assert d.bed_w == BED_B
    d.switch_page(0)
    assert d.bed_w == 210.0
    assert d.pages[1].bed_w == BED_B, "切页把**别的页**的床尺寸改了"
    assert_bed_in_sync(d, "两页独立床")


# --- 缺陷二：枚举面对 paths==[] 的口径 ---------------------------------------

def test_empty_leaf_is_visible_to_all_four_interfaces():
    """空叶子 ``paths==[]``：四个接口**同一口径**都得看得见它。

    旧行为：``contains=True``、``top_z/bottom_z`` 算进它的 z、``remove`` 摘得掉，
    但 ``sorted_items()``/``items_visible()`` 看不见它（枚举面/身份面分裂）。
    """
    a = _line(0.0, "a", z=0.0)
    empty = Item(paths=[], z=1.0, name="empty")
    b = _line(20.0, "b", z=2.0)
    d = Document(items=[a, empty, b])

    assert d.contains(empty) is True
    assert d.top_z() == 2.0 and d.bottom_z() == 0.0
    assert [i.name for i in d.sorted_items()] == ["a", "empty", "b"]
    assert [i.name for i in d.items_visible()] == ["a", "empty", "b"]
    # 身份判定（不是名字判定）：枚举面给出的就是**同一个对象**
    assert any(i is empty for i in d.sorted_items())
    assert any(i is empty for i in d.items_visible())


def test_empty_leaf_does_not_change_what_gets_cut():
    """空叶子是**枚举面**的事，**不是**拍平面的事（别顺手把它也放进切割）。

    钉的是「枚举面修好了，导出/送作业的几何逐点不变」—— 否则一个纯枚举修复
    就会改掉送进切纸机的 G-code。
    """
    a = _line(0.0, "a", z=0.0)
    empty = Item(paths=[], z=1.0, name="empty")
    b = _line(20.0, "b", z=2.0)
    d = Document(items=[a, empty, b])
    assert flatten_visible(d) == [[(0.0, 0.0), (10.0, 0.0)],
                                  [(20.0, 0.0), (30.0, 0.0)]]
    # ⚠ ``iter_units`` **会**产出空叶子（实测：它逐项产出，docstring 写的
    # 「叶子 + 带自身折线的容器」与实现不符）。这是 A2 范围外的**另一处**口径
    # 分裂，本批不修（见 stuck）—— 但它**不影响切割**：
    # ``flatten_visible`` 对没有折线的项天然不产出任何折线。
    assert [u[0].name for u in iter_units(d.items)] == ["a", "empty", "b"]
    assert flatten_visible(d) == [[(0.0, 0.0), (10.0, 0.0)],
                                  [(20.0, 0.0), (30.0, 0.0)]], \
        "空叶子不该给拍平贡献折线（否则就是白切一刀）"


def test_hidden_empty_leaf_is_still_filtered_by_visibility():
    """``visible=False`` 的空叶子：枚举面按可见性过滤（与其它图元同口径）。"""
    a = _line(0.0, "a", z=0.0)
    hidden = Item(paths=[], z=1.0, name="hidden", visible=False)
    shown = Item(paths=[], z=2.0, name="shown")
    d = Document(items=[a, hidden, shown])
    assert [i.name for i in d.sorted_items()] == ["a", "hidden", "shown"]
    assert [i.name for i in d.items_visible()] == ["a", "shown"]


def test_empty_leaf_inside_a_group_is_visible():
    """组内的空叶子同样要看得见（守卫不能只对顶层生效）。"""
    empty = Item(paths=[], z=1.0, name="empty")
    shown = _line(0.0, "shown", z=2.0)
    g = Item(name="G", children=[empty, shown])
    d = Document(items=[g])
    assert d.contains(empty) is True
    assert sorted(i.name for i in d.sorted_items()) == ["empty", "shown"]
    assert sorted(i.name for i in d.items_visible()) == ["empty", "shown"]


def test_empty_container_stays_out_of_the_enumeration_surface():
    """空容器**仍然不产出**（可渲染单元面）—— 与四条既有断言一致。

    刻意钉住这条：容器不建 ``PathItem``（模块 docstring「画布侧硬约束」），
    ``test_gui_layout_remove_undo.py:246`` 要求 ``items_visible()`` 每一项都有
    场景项。本条防的是「把过滤整个删掉」那种改法。
    """
    kid = _line(0.0, "kid", z=1.0)
    g = Item(name="G", children=[kid])            # 空容器
    d = Document(items=[g])
    assert [i.name for i in d.sorted_items()] == ["kid"]
    assert [i.name for i in d.items_visible()] == ["kid"]
    # 但它**在文档里**，身份面照样看得见（这是本方法与 contains 的分工）
    assert d.contains(g) is True
    assert d.top_z() == 1.0 and d.bottom_z() == 0.0


def test_container_with_own_paths_is_still_an_enumeration_unit():
    """自带折线的容器**是**枚举单元（既有契约 :544 的那一侧，别被改掉）。"""
    c = Item(name="C", paths=[[(0.0, 0.0), (300.0, 20.0)]], z=0.0)
    kid = _line(0.0, "kid", z=0.0)
    c.children.append(kid)
    d = Document(items=[c])
    assert [i.name for i in d.sorted_items()] == ["C", "kid"]
    assert [i.name for i in d.items_visible()] == ["C", "kid"]


# --- 更重要的约束：无 children 文档与基线逐位一致 ----------------------------

def _baseline_sorted_items(items):
    """基线 ``2fb1e47`` 的 ``sorted_items`` = ``sorted(self.items, key=z)``。

    稳定排序 ⇒ 并列 z 的 tie-break = 列表序。**故意写成独立实现**而不是 import
    现行代码：这样「与基线一致」才是一条会被违反的断言，而不是同义反复。
    """
    return sorted(items, key=lambda it: it.z)


def _baseline_items_visible(items):
    """基线 ``items_visible`` = 上面那个结果里 ``visible`` 的那些。"""
    return [it for it in _baseline_sorted_items(items) if it.visible]


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
def test_flat_document_matches_baseline_pointwise(seed):
    """无 children 文档：``sorted_items``/``items_visible`` 与基线**逐点相同**。

    这是本批**更重要**的约束：枚举面修 ``paths==[]`` 不得把「与基线逐位不变」
    搭进去 —— 基线 ``sorted(self.items, key=z)`` 把空图元也产出。
    旧实现在这个用例上红（空图元被丢）。
    """
    rnd = random.Random(seed)
    items = []
    for i in range(rnd.randint(0, 6)):
        paths = [[(round(rnd.uniform(0, 50), 3), round(rnd.uniform(0, 50), 3))
                  for _ in range(rnd.randint(2, 4))]
                 for _ in range(rnd.randint(0, 3))]        # 含 0 条 ⇒ paths==[]
        items.append(Item(
            paths=paths,
            pos=(round(rnd.uniform(0, 9), 3), round(rnd.uniform(0, 9), 3)),
            scale=round(rnd.uniform(0.5, 2.0), 3),
            angle_deg=round(rnd.uniform(0, 360), 3),
            z=rnd.choice([0.0, 0.0, 1.0, 2.0]),             # 制造并列 z
            name="i%d" % i,
            visible=rnd.random() > 0.25))
    d = Document(items=items)

    assert [i.name for i in d.sorted_items()] == \
        [i.name for i in _baseline_sorted_items(d.items)]
    assert [i.name for i in d.items_visible()] == \
        [i.name for i in _baseline_items_visible(d.items)]
    assert d.top_z() == max((i.z for i in d.items), default=0.0)
    assert d.bottom_z() == min((i.z for i in d.items), default=0.0)


def test_flat_document_random_differential_is_bit_identical():
    """300 份随机平面文档 vs 基线独立实现：必须**零**不一致。

    单例容易被「刚好撞对」放过；这里把搜索空间铺开，任何口径分叉都会冒出来。
    """
    bad = 0
    first = None
    for seed in range(300):
        rnd = random.Random(10_000 + seed)
        items = []
        for i in range(rnd.randint(0, 8)):
            paths = [[(round(rnd.uniform(0, 50), 3), round(rnd.uniform(0, 50), 3))
                      for _ in range(rnd.randint(2, 4))]
                     for _ in range(rnd.randint(0, 3))]
            items.append(Item(paths=paths,
                              pos=(round(rnd.uniform(0, 9), 3),
                                   round(rnd.uniform(0, 9), 3)),
                              scale=round(rnd.uniform(0.5, 2.0), 3),
                              angle_deg=round(rnd.uniform(0, 360), 3),
                              z=rnd.choice([0.0, 0.0, 1.0, 2.0]),
                              name="i%d" % i,
                              visible=rnd.random() > 0.25))
        d = Document(items=items)
        got = ([i.name for i in d.sorted_items()],
               [i.name for i in d.items_visible()])
        want = ([i.name for i in _baseline_sorted_items(d.items)],
                [i.name for i in _baseline_items_visible(d.items)])
        if got != want:
            bad += 1
            first = first or (seed, got, want)
    assert bad == 0, (
        f"{bad}/300 份随机平面文档与基线不一致；首个：seed={first[0]} "
        f"现行={first[1]} 基线={first[2]}")
