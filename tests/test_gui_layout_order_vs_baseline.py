"""R2 回归：「与基线逐位一致」是**有前置条件**的，不是全局不变量。

**被收窄的声称**（R2 对 A2 的对抗性复核）——
原文按字面读是假的：

    「无 children 文档与基线（2fb1e47 的 sorted(self.items, key=z)）逐位一致」

它只对 **order 恰好等于列表位置**的文档成立，而那正是现有三份套件构造文档的
方式（全部现造 ``Item``，``_stamp_order`` 使 ``order`` == 列表序）。实测反例：
``a(z=0) b(z=1) c(z=1)``，对 ``b`` 做 ``remove`` + ``doc.add``（b 回到列表末尾，
``order`` 粘住不动）⇒::

    sorted_items()  ['a','b','c']   vs 基线 ['a','c','b']      *** MISMATCH ***
    切割 x 序        [0, 10, 20]     vs 基线 [0, 20, 10]

**收窄后的正确声称**：*现造且未重排的平面文档*（order == 列表序）与基线逐位一致。
本文件把这个前置条件**变成可执行的断言**，并把「order 脱钩后会发生什么」钉死，
以免后来者把「与基线一致」当成全局不变量、或反过来把 (z, order) 误当成 bug
去「修回」列表序。

**机制**（model.py）：
- ``_stamp_order``（:200-225）只在 ``order < 0`` 时赋值 ⇒ **幂等**，已编号者
  重新入模不重新编号；
- ``Document.add``（:1064-1066）无条件 ``_stamp_order`` + ``append``；
- ``Page.attach(owner=None, index=…)`` 按 index 插回原位 ⇒ undo 侧不脱钩；
- 排序键 ``(it.z, it.order)``（:741-742）。

**两条边界，如实标出**（都是「声称 / 覆盖」层面的，不是现网 bug）：
① 这是 A4（order 作 tie-break）的**既定设计后果**，不是 A2 引入的回归 ——
   A2 只改了 ``_own_geometry`` 的判据，没碰排序键；
② **今天没有 UI 可达路径**：生产里 undo 侧一律走 ``attach(owner, index)``
   （原位复原、不脱钩）。本文件用 ``remove + doc.add`` 刻意造出脱钩，是**契约
   用例**而非缺陷复现。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

#: 越床闸那条用例会造真 LayoutPage（QWidget）——本文件须自备一个 QApplication，
#: 否则同进程混用 QCoreApplication/QWidget 会挂死（AGENTS.md 记的 Qt 测试约定）。
_app = QApplication.instance() or QApplication([])

from megapro.gui.layout.model import (Document, Item, flatten_visible,  # noqa: E402
                                      iter_leaves)


# -- 工具 ------------------------------------------------------------------

def _line(name, x0, z):
    """一条水平线：x 决定几何位置与身份，z 决定层序。"""
    return Item(paths=[[(float(x0), 0.0), (float(x0) + 10.0, 0.0)]], name=name, z=z)


def _baseline_sorted(items):
    """基线 ``2fb1e47`` 的 ``sorted_items`` = ``sorted(self.items, key=z)``。

    稳定排序 ⇒ 并列 z 的 tie-break 是**列表位置**。故意独立实现（同
    tests/test_gui_layout_bed_enum.py:361-372 的口径），这样「与基线一致」才是
    一条会被违反的断言，而不是同义反复。
    """
    return sorted(items, key=lambda it: it.z)


def _names(seq):
    return [i.name for i in seq]


def _cut_x(doc):
    """拍平后的切割 x 序（= 将发送的几何次序）。"""
    return [round(p[0][0], 6) for p in flatten_visible(doc)]


def _fresh(*specs):
    """按给定顺序现造平面文档（order 与列表序同向）。

    ⚠ ``order`` 取自 :mod:`megapro.gui.layout.model` 的**模块级**计数器
    ``_ORDER_SEQ``，同一进程里造第二份文档会从 7、8、9 继续（不是 1、2、3）。
    所以本文件一律断言**相对**关系（谁大谁小、同不同向），绝不写死具体数字
    —— 写死就是「断言会变的量」。
    """
    doc = Document()
    for name, x, z in specs:
        doc.add(_line(name, x, z))
    return doc


def _orders(doc):
    return {i.name: i.order for i in doc.items}


_ABC = (("a", 0, 0.0), ("b", 10, 1.0), ("c", 20, 1.0))


def _decoupled():
    """a(z=0) b(z=1) c(z=1) 经 remove(b) + doc.add(b) ⇒ order 与列表位置脱钩。"""
    doc = _fresh(*_ABC)
    b = doc.items[1]
    doc.remove(b)
    doc.add(b)
    return doc


# -- ① 收窄后的声称：现造且未重排的平面文档与基线逐位一致 --------------------

def test_fresh_flat_document_matches_baseline_bitwise():
    """**收窄后的声称**（本文件要守的主干）：现造、未重排 ⇒ 与基线逐位一致。

    这条不是「新鲜事」——它是 A2 真正成立的保证，钉它是为了防止有人为了
    「让所有文档都与基线一致」而去删掉 ``order`` tie-break（那会毁掉 A4）。
    """
    doc = _fresh(*_ABC)

    assert _names(doc.sorted_items()) == _names(_baseline_sorted(doc.items))
    o = _orders(doc)
    assert o["a"] < o["b"] < o["c"], f"现造时编号应与列表序同向：{o}"


def test_baseline_equivalence_holds_only_while_order_equals_list_position():
    """把前置条件本身钉成断言：一旦 order ≠ 列表位置，基线一致性即失效。

    这是本文件的**核心**：它不假设「order 永远等于列表序」，而是把「两者同向
    ⇒ 等价于基线」这个条件陈述出来，并证明去掉条件就不等价。
    """
    doc = _fresh(*_ABC)
    assert [i.name for i in doc.items] == ["a", "b", "c"]
    o = _orders(doc)
    assert o["a"] < o["b"] < o["c"], "前置：现造文档的编号与列表序同向"

    decoupled = _decoupled()
    assert [i.name for i in decoupled.items] == ["a", "c", "b"], \
        "反例构造失效：remove+add 后列表顺序本应改变"
    assert _names(decoupled.sorted_items()) != \
        _names(_baseline_sorted(decoupled.items)), \
        "order 脱钩后仍与基线一致 ⇒ (z, order) tie-break 疑似已退化成列表序"


# -- ② 反例本身：脱钩后由 (z, order) 决胜，且分叉只改**次序** ----------------

def test_decoupled_document_is_ordered_by_stamped_order_not_list_position():
    """脱钩后 ``sorted_items`` 按**入档序**排，不是列表位置。

    ``order`` = {a:1, c:3, b:2} ⇒ 并列 z=1 的两项里 b 在 c 之前 ⇒ 结果
    ['a','b','c']，而列表序会给 ['a','c','b']。这正是与基线分叉的机制。
    """
    doc = _decoupled()

    assert [i.name for i in doc.items] == ["a", "c", "b"], "前置：b 已回到列表末尾"
    o = _orders(doc)
    assert o["b"] < o["c"], f"前置：b 的入档序仍早于 c（order 粘住不动）——{o}"
    assert _names(doc.sorted_items()) == ["a", "b", "c"]
    assert _cut_x(doc) == [0.0, 10.0, 20.0], "切割次序按入档序"


def test_decoupling_changes_order_but_never_the_geometry_multiset():
    """**安全侧**：分叉只改次序 —— 几何多重集守恒，不少切、不重切、不越床。

    为什么这条重要：切割次序变了不等于「切错东西」。对会动刀的软件，必须把
    「次序变了」与「切的东西变了」分开断言；后者才是事故。
    """
    doc = _decoupled()
    base = _baseline_sorted(doc.items)
    now_x, base_x = _cut_x(doc), [round(it.paths[0][0][0], 6) for it in base]

    assert now_x != base_x, "前置：本用例就是要展示次序分叉"
    assert sorted(now_x) == sorted(base_x), \
        f"几何多重集被改变（会少切/多切）：{now_x} vs {base_x}"


def test_decoupling_never_pushes_geometry_outside_the_bed():
    """次序变化不得连带把几何挪到床外（越床判定只看坐标，与次序无关）。

    对会动刀的软件，这条是「次序变了」与「切到床外了」之间的防火墙：脱钩前后
    走**同一个真闸门** :meth:`LayoutPage._out_of_bed`，判定必须一致。
    """
    from megapro.gui.layout.layout_page import LayoutPage

    def gate(doc):
        lp = LayoutPage()
        lp.doc = doc
        lp._sync_models()
        return lp._out_of_bed()

    fresh = _fresh(*_ABC)
    decoupled = _decoupled()
    assert gate(fresh) == gate(decoupled) is None, \
        "床内几何换了个次序就不该改变越床判定"

    # 越界的也一样：次序变、判定不变（x0=205 ⇒ x1=215，超出物理床 210 共 5mm）
    out_doc = _fresh(("a", 0, 0.0), ("wide", 205, 1.0))
    out_before = gate(out_doc)
    wide = out_doc.items[1]
    out_doc.remove(wide)
    out_doc.add(wide)
    assert out_before is not None, "前置：这条确实有个越界件（x1=215 > 210）"
    assert gate(out_doc) == out_before, \
        "越界判定必须与次序无关（次序变 ⇒ 拦截被绕过才是事故）"


# -- ③ 分叉确实进 SVG 与发往机器的 G-code ------------------------------------

def _spec_and_lines(doc):
    from megapro.gui.job import JobSpec, Placement, ZMap, compile_job

    spec = JobSpec(
        paths_paper=flatten_visible(doc),
        source_name="排版版面",
        placement=Placement(mode="preserve"),
        zmap=ZMap(safe_z=30.0, down_z=17.0),
    )
    return compile_job(spec).lines


def test_decoupling_reaches_the_gcode_sent_to_the_machine():
    """发往机器的 G-code 落笔**段多重集守恒、但次序不同**。

    用「全新对象按基线序入档」的等价文档当基线输出（不能靠重排现文档的列表 ——
    ``flatten_visible`` 仍按 ``order`` 重排，那样造不出分叉）。
    """
    import collections

    decoupled = _decoupled()
    baseline_doc = _fresh(("a", 0, 0.0), ("c", 20, 1.0), ("b", 10, 1.0))

    g1_now = [ln for ln in _spec_and_lines(decoupled) if ln.startswith("G1")]
    g1_base = [ln for ln in _spec_and_lines(baseline_doc) if ln.startswith("G1")]

    assert collections.Counter(g1_now) == collections.Counter(g1_base), \
        "落笔段多重集变了 —— 这就不是次序问题而是切错东西了"
    assert g1_now != g1_base, "前置：G-code 次序应当不同（否则本用例空跑）"


def test_decoupling_reaches_the_exported_svg():
    """另存为 SVG 的 polyline 顺序随 ``order`` 走（逐字节不同）。"""
    from megapro.gui.layout.export_svg import document_to_svg

    decoupled = _decoupled()
    baseline_doc = _fresh(("a", 0, 0.0), ("c", 20, 1.0), ("b", 10, 1.0))

    def points(doc):
        return [ln.strip() for ln in document_to_svg(doc).splitlines()
                if "polyline" in ln]

    assert points(decoupled) != points(baseline_doc)
    assert sorted(points(decoupled)) == sorted(points(baseline_doc)), \
        "SVG 里出现的几何必须一致（只是画下去的先后变了）"


# -- ④ A4 的本意：编组/解组**不**改次序（与上面的脱钩是两种东西） -------------

def test_group_ungroup_does_not_decouple_order():
    """编组/解组只改树形、不改 ``order`` ⇒ 切割次序恒定（A4 的目的）。

    与 :func:`test_decoupling_changes_order_but_never_the_geometry_multiset`
    并置，区分「**设计要的**次序稳定（编组）」与「**入档序**造成的次序变化
    （remove+add）」—— 前者是契约，后者才是本条声称收窄的原因。
    """
    doc = _fresh(*_ABC)
    before = _cut_x(doc)
    orders_before = {i.name: i.order for i in iter_leaves(doc.items)}

    a, c = doc.items[0], doc.items[1]
    grp = doc.group_items([a, c], name="G")
    assert grp is not None
    after_group = _cut_x(doc)

    doc.ungroup(grp)
    after_ungroup = _cut_x(doc)

    assert after_group == before == after_ungroup, \
        f"编组/解组改了切割次序：{before} → {after_group} → {after_ungroup}"
    assert {i.name: i.order for i in iter_leaves(doc.items)} == orders_before, \
        "编组/解组不得改写入档编号"
