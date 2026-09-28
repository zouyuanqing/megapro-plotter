"""R1 回归：``Page.attach`` 不得把**已归属他处**的图元挂成第二个父。

R1 推翻了 A1 的「已一并堵上」声称。A1 守卫判的是「item 与 owner **互相关联**」
（自环 / owner ∈ 子树(item) / item ∈ 子树(owner)），三者都只按**这一对**判定，
**从不检查 item 在 owner 子树之外是否已有归属**。于是「切两遍」还剩一条门没关：

    G1.children=[a,b]  G2.children=[c]   →  p.attach(a, owner=G2)

三段判据全部落空（``a`` 无子树；``G2.descendants()=[G2,c]`` 不含 ``a``），
:attr:`Page.attach` 照常 append ⇒ **a 同时挂在 G1 与 G2 下**：

    iter_items  : ['G1','a','b','G2','c','a']        ← a 出现两次
    flatten_visible : 4 条折线，文档只有 3 条不同几何   ← ((0,0),(10,0)) 两份
    document_to_svg : 4 个 ``<polyline>``，其中一份逐字节相同

拍平序 = 切割次序（``flatten_visible`` 走 ``key=z`` 稳定排序），所以这在切纸机上
就是**同一条线切两遍**。

本文件断言的是**安全性质**（导出不得出现重复几何），不是「守卫长什么样」——
换任何一种实现（``owner_of`` 判据、或先把 item 从原 owner 摘下）都该绿。

⚠ **F2（顶层双父）不在本文件**：``Document.add`` 的同一形状是
``model.py:680-685`` docstring **刻意**写明不加守卫、且被
``tests/test_gui_layout.py::test_attach_restores_group_structure_exactly``
（:462-484）以「错误路径复现」显式钉死（断言 ``["G","a"]``）。给它加守卫会让那条
既有用例变红，而本仓库不允许为了让实现变绿而改弱既有断言 —— 故 F2 交由人拍板，
见该批次的 stuck。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


#: R1 的 F1 是**已定位、未修复**的缺陷：修复必须改 ``model.py``，而该文件不在本批
#: 的写入白名单内（其它线正在并行改它，且它直接决定切割几何）。
#:
#: 为什么不写成普通测试：普通测试会**把仓库留在红**（全量 3 failed / 621 passed），
#: 那是拿别人的门当自己的交付。
#: 为什么不 ``skip``：skip 会让这个反例从视野里消失 —— 而它正是切纸机上
#: 「同一条线切两遍」的形状。
#: 为什么是 **strict=True**：xfail 默认非 strict，一旦修好测试会静悄悄变成 XPASS
#: 继续绿着，标记就永远烂在文件里。strict=True 让它**修好即报红**（XPASS(strict)），
#: 逼 whoever 修完把标记摘掉 —— 缺陷不会被人遗忘，也不会被标记永久掩盖。
#:
#: 摘标记的条件：``Page.attach`` 的 owner 分支加上
#: ``parent = self.owner_of(item); if parent is not None and parent is not owner: return``
#: （该方案已验证：F1/孙级被拦、A1 的 ①②③ 仍被拦、无归属合法 attach 不被误拦）。
_XFAIL_R1 = pytest.mark.xfail(
    strict=True,
    reason="R1/F1 未修：Page.attach 缺「item 已有他处宿主 ⇒ 拒」判据"
           "（修复需改 model.py，不在本批白名单）。修好后本条会 XPASS(strict) 报红，"
           "届时请删除此标记。",
)


def _leaf(name, y=0.0):
    from megapro.gui.layout.model import Item

    return Item(paths=[[(0.0, y), (10.0, y)]], name=name)


def _group(name, children):
    from megapro.gui.layout.model import Item

    return Item(paths=[], name=name, children=list(children))


def _doc(*tops):
    from megapro.gui.layout.model import Document

    return Document(items=list(tops))


def _geom_keys(paths):
    """折线的身份键（逐点取整），用于「是不是同一条几何」。"""
    return [tuple(map(tuple, p)) for p in paths]


def _duplicated(paths):
    seen: dict = {}
    for k in _geom_keys(paths):
        seen[k] = seen.get(k, 0) + 1
    return {k: v for k, v in seen.items() if v > 1}


def _svg_polylines(doc) -> int:
    from megapro.gui.layout.export_svg import document_to_svg

    return document_to_svg(doc).count("<polyline")


# -- F1：attach(叶子, 兄弟容器) ---------------------------------------------

@_XFAIL_R1
def test_attach_rejects_leaf_already_owned_by_another_container():
    """R1 反例本体：``a`` 已属 G1，再 ``attach(a, owner=G2)`` 必被拒。

    判据是**归属唯一**，不是「这一对关联不关联」：a 在 G1.children 里，与 G2 毫无
    包含关系，三段旧判据全落空。
    """
    a, b, c = _leaf("a", 0.0), _leaf("b", 5.0), _leaf("c", 9.0)
    g1, g2 = _group("G1", [a, b]), _group("G2", [c])
    doc = _doc(g1, g2)

    doc.attach(a, owner=g2)

    assert not any(ch is a for ch in g2.children), \
        "已被 G1 收着的 a 又挂进了 G2（双父）"
    assert [ch.name for ch in g1.children] == ["a", "b"], \
        f"被拒的 attach 不该动原 owner：{[i.name for i in g1.children]}"


@_XFAIL_R1
def test_attach_sibling_container_does_not_duplicate_geometry():
    """安全性质：这次 attach 之后，导出里**不得**出现重复几何。

    这条比上面那条更贴近机器后果 —— 它不关心守卫怎么实现，只要求「同一条线不会
    被切两遍」。若哪天实现改成「先从原 owner 摘下再挂」，上面那条会红、这条仍绿，
    这正是我们想要的分工。
    """
    a, b, c = _leaf("a", 0.0), _leaf("b", 5.0), _leaf("c", 9.0)
    g1, g2 = _group("G1", [a, b]), _group("G2", [c])
    doc = _doc(g1, g2)

    doc.attach(a, owner=g2)

    from megapro.gui.layout.model import flatten_visible

    flat = flatten_visible(doc)
    assert not _duplicated(flat), f"导出出现重复几何（=切两遍）：{_duplicated(flat)}"
    assert len(flat) == len(set(_geom_keys(flat))), "拍平结果里有同一条线两份"
    assert _svg_polylines(doc) == len(set(_geom_keys(flat))), \
        f"SVG 折线数 { _svg_polylines(doc) } ≠ 不同几何数 { len(set(_geom_keys(flat))) }"


@_XFAIL_R1
def test_attach_rejects_item_owned_deep_in_another_subtree():
    """item 是**孙级**（不是直接子项）时同样必须被拒。

    守「有没有宿主」而不是「是不是直接子项」：只查 ``owner.children`` 的话，
    把深层成员摘到兄弟组里的形状会漏过去。
    """
    deep = _leaf("deep", 0.0)
    mid = _leaf("mid", 3.0)
    g3 = _group("G3", [mid])
    g3.children.append(deep)            # deep 是 G3 的孙子
    g2 = _group("G2", [_leaf("other", 9.0)])
    doc = _doc(g3, g2)

    doc.attach(deep, owner=g2)

    assert not any(ch is deep for ch in g2.children), \
        "别处的孙级成员被挂进了另一个容器（双父）"
    assert any(ch is deep for ch in g3.children), "被拒的 attach 不该把 item 从原处摘走"


# -- 修复不得回退 A1 已经挡住的三种形状 --------------------------------------

def test_attach_still_blocks_self_loop():
    """A1 ①：``attach(G, owner=G)`` 仍被拒。"""
    g = _group("G", [_leaf("a")])
    doc = _doc(g)

    doc.attach(g, owner=g)

    assert [ch.name for ch in g.children] == ["a"], "自环未被拒（把自己挂进自己）"


def test_attach_still_blocks_true_cycle():
    """A1 ②：真环 —— ``A.children=[B,C]`` 后 ``attach(A, owner=B)`` 仍被拒。"""
    from megapro.gui.layout.model import iter_items

    b = _group("B", [])
    c = _leaf("c")
    a = _group("A", [b, c])
    doc = _doc(a)

    doc.attach(a, owner=b)

    assert b.children == [], f"真环未被拒：B 现在是 {[i.name for i in b.children]}"
    assert [i.name for i in iter_items(doc.page.items)] == ["A", "B", "c"]


def test_attach_still_idempotent_for_same_owner():
    """A1 ③：同一 owner 的重复挂仍被身份幂等短路（无副作用）。"""
    x = _leaf("x")
    p = _group("P", [])
    doc = _doc(p)

    doc.attach(x, owner=p)
    doc.attach(x, owner=p)

    assert [ch.name for ch in p.children] == ["x"], \
        f"同一 owner 重复 attach 产生了多余子项：{[i.name for i in p.children]}"


def test_attach_legitimate_case_still_works():
    """对照组：**无归属**的散件 attach 到空容器，必须照常成功。

    守「别把修复做过头」—— 新守卫若写成「只要 owner 有子项就拒」之类，本条即红。
    """
    x = _leaf("x")
    fresh = _group("fresh", [])
    doc = _doc(fresh, _leaf("loose", 9.0))

    doc.attach(x, owner=fresh)

    assert [ch.name for ch in fresh.children] == ["x"], \
        "无归属图元 attach 到容器被误拒"
