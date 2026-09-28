"""A4 回归：z 并列时编组会改变 flatten 顺序，违反 FR-03① 契约。

背景（A4 实测）：:func:`flatten_visible` 走
``sorted(iter_flattens(doc.items), key=lambda u: u[0].z)`` —— **稳定**排序，
并列 z 的 tie-break 实际是 :func:`iter_flattens` 的 **DFS 先序**。而 DFS 先序
**依赖树形**：:meth:`Page.group_items` 把非相邻成员拉到第一个成员的槽位、连成
一段（``top=['G','b']``, ``G.children=['a','c']``），DFS 于是给出 a、c、b
而不是原来的 a、b、c。z 一并列，稳定排序就把这个变化原样放出去 ⇒ 拍平顺序
变了。

实跑数字：

- 最小例 a(z=0,x=0)、b(z=1,x=10)、c(z=1,x=20)，``group_items([a,c])`` 后
  顶层 ``['G','b']``，flatten 的 x 序 ``[0,10,20]`` → ``[0,20,10]``，逐点恒等
  False、多重集相同 True；**再 ungroup 仍是 ``[0,20,10]``，不可逆**；
- docx 夹具同型（z=[0,1,0,1,1,1,1]、非相邻取 [items[0], items[3]]）：恒等
  False；
- 随机压力 1000 次单层编组：z 互异 0/1000 失败，z 并列 143/1000 失败；
- 可达性：真 UI 调 ``_zorder('down')`` 造出 z=[0,1,1] 并列后 push 真
  ``GroupCommand`` 即触发。

**几何集合守恒、不少切不重复**，但顺序进了导出的 SVG 和发往机器的 G-code ⇒
切割次序变了。画布上看不出来（``gi.setZValue`` 用 item.z，编组不重建场景项）。

**为什么 wrap_group 免疫**（同仓 doc_import.py 的「可行方向」线索）：它把
**整个** items 列表一次性包进容器，成员的相对次序原封不动 ⇒ DFS 先序不变。
那是特例而非通用修法；通用修法必须让 tie-break 与树形无关。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _L(x, z, name):
    """一条水平线，x/z 可控（x 决定几何位置，z 决定层序）。"""
    from megapro.gui.layout.model import Item

    return Item(paths=[[(float(x), 0.0), (float(x) + 5.0, 0.0)]],
                name=name, z=float(z))


def _xs(doc):
    """拍平结果的 x 序（每个单元一条折线 ⇒ 一一对应，可精确比较顺序）。"""
    from megapro.gui.layout.model import flatten_visible

    return [p[0][0] for p in flatten_visible(doc)]


# --- 最小例 -----------------------------------------------------------------

def test_grouping_tied_z_preserves_flatten_order_minimal_case():
    """a(z=0)、b(z=1)、c(z=1)，编组 [a,c] ⇒ 拍平 x 序必须仍是 [0,10,20]。"""
    from megapro.gui.layout.model import Document

    a, b, c = _L(0, 0, "a"), _L(10, 1, "b"), _L(20, 1, "c")
    doc = Document(items=[a, b, c])
    before = _xs(doc)
    assert before == [0.0, 10.0, 20.0]

    g = doc.group_items([a, c], name="G")
    assert g is not None
    assert [i.name for i in doc.items] == ["G", "b"]     # 非相邻成员被拉成一段
    assert _xs(doc) == before, "z 并列时编组改变了拍平顺序（FR-03① 被违反）"
    assert sorted(_xs(doc)) == sorted(before)            # 多重集守恒（不少切）


def test_ungroup_after_tied_z_grouping_restores_original_order():
    """编组再解组必须回到原顺序（修复前是**不可逆**的）。"""
    from megapro.gui.layout.model import Document

    a, b, c = _L(0, 0, "a"), _L(10, 1, "b"), _L(20, 1, "c")
    doc = Document(items=[a, b, c])
    before = _xs(doc)
    g = doc.group_items([a, c], name="G")
    doc.ungroup(g)
    assert _xs(doc) == before, "解组后拍平顺序不可逆"


# --- docx 夹具同型（z=[0,1,0,1,1,1,1]、5 个并列 z=1）----------------------

def test_grouping_tied_z_preserves_flatten_order_docx_shaped():
    """z=[0,1,0,1,1,1,1] 的 7 元件、非相邻取 [0,3] ⇒ 拍平逐点恒等。"""
    from megapro.gui.layout.model import Document

    zs = [0, 1, 0, 1, 1, 1, 1]
    items = [_L(10 * i, z, f"i{i}") for i, z in enumerate(zs)]
    doc = Document(items=items)
    before = _xs(doc)
    assert sorted(i.z for i in items if i.z == 1).count(1.0) == 5, "确有 5 个并列 z=1"

    g = doc.group_items([items[0], items[3]], name="G")
    assert g is not None
    assert _xs(doc) == before, "并列 z 下非相邻编组改变了拍平顺序"


# --- 要求 2：无 children 的文档必须与基线逐位一致 -------------------------

def test_leaf_document_with_tied_z_keeps_list_order():
    """并列 z 的**叶子文档**必须保持列表序，哪怕它与几何序不同。

    这是「无 children 行为与基线逐位一致」的钉子：并列项 z 相同，原实现是
    稳定排序 ⇒ tie-break = 列表序 ⇒ x 序 ``[20, 10]``。任何「按几何坐标做
    tie-break」的改法都会把它变成 ``[10, 20]`` —— 那是基线可见的回归。
    """
    from megapro.gui.layout.model import Document

    c, b = _L(20, 1, "c"), _L(10, 1, "b")      # 故意让列表序与几何序相反
    doc = Document(items=[c, b])
    assert _xs(doc) == [20.0, 10.0], "叶子文档的并列 z tie-break 被改了"


# --- 要求 3：group_items 与 wrap_group 行为一致 ---------------------------

def test_group_items_and_wrap_group_are_consistent():
    """同一批图元走两条编组路径 ⇒ 拍平含顺序必须逐点相同。

    修复前 ``wrap_group`` 免疫、``group_items`` 不免疫，是明确的不一致。
    """
    from megapro.gui.layout.doc_import import wrap_group
    from megapro.gui.layout.model import Document

    zs = [0, 1, 0, 1, 1, 1, 1]

    def build():
        return [_L(10 * i, z, f"i{i}") for i, z in enumerate(zs)]

    # (a) group_items：非相邻选择（会拉成一段）
    items_a = build()
    doc_a = Document(items=items_a)
    before = _xs(doc_a)
    doc_a.group_items([items_a[0], items_a[3]], name="G")
    via_group_items = _xs(doc_a)

    # (b) wrap_group：整批一次性包起来（修复前的免疫路径）
    items_b = build()
    doc_b = Document(items=items_b)
    doc_b.items = [wrap_group(list(items_b), name="G")]
    via_wrap = _xs(doc_b)

    assert via_group_items == before, "group_items 路径不保序"
    assert via_group_items == via_wrap, \
        f"两条编组路径行为不一致：{via_group_items} vs {via_wrap}"


# --- 随机编组压力 -----------------------------------------------------------

@pytest.mark.parametrize("zmode", ["distinct", "tied"])
def test_random_grouping_preserves_flatten_order(zmode):
    """随机单层编组：z 互异与 z 并列两种分布下，恒等都必须成立。"""
    import random

    from megapro.gui.layout.model import Document

    rng = random.Random(20260928)
    n_trials = 300
    for t in range(n_trials):
        n = rng.randint(3, 6)
        if zmode == "distinct":
            zs = list(range(n))
            rng.shuffle(zs)                     # z 互异但与列表序无关
        else:
            zs = [rng.randint(0, 3) for _ in range(n)]
        items = [_L(10 * i, z, f"i{i}") for i, z in enumerate(zs)]
        doc = Document(items=items)
        before = _xs(doc)
        g = doc.group_items(rng.sample(items, rng.randint(2, n)), name="G")
        if g is None:
            continue
        after = _xs(doc)
        assert after == before, \
            f"第 {t} 次编组（zmode={zmode}, z={zs}）破坏 FR-03①：{before} -> {after}"
        assert sorted(after) == sorted(before), "多重集变了（几何丢失或重复）"


# --- 反复编组/解组不漂移 ----------------------------------------------------

def test_repeated_group_ungroup_cycles_keep_flatten_order():
    """反复编组/解组，拍平顺序恒等于初始（不漂移）。"""
    from megapro.gui.layout.model import Document

    zs = [0, 1, 0, 1, 1, 1, 1]
    items = [_L(10 * i, z, f"i{i}") for i, z in enumerate(zs)]
    doc = Document(items=items)
    before = _xs(doc)
    for _ in range(4):
        g = doc.group_items([items[0], items[3]], name="G")
        assert _xs(doc) == before, "编组后拍平漂移"
        doc.ungroup(g)
        assert _xs(doc) == before, "解组后拍平漂移"
