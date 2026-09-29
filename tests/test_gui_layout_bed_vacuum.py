"""R1 回归：A2「床尺寸单一真源」是**真空**性质 —— 门面接得上，产出链接不上。

⚠ **本文件钉的是「页级床当前没有生产消费者，且不得放宽物理床闸门」**，
不是「床尺寸应该跟着页走」。两者的区别就是安全与否。

**反例（R1 对 A2 的对抗性复核，复现于本仓库）**：
A2 把 ``Document.bed_w/bed_h`` 收成读写直通当前页的 property，「
``doc.bed_*`` 恒等于 ``doc.page.bed_*``」成了结构事实。这条**攻不倒**。
但它报了一个**真空**性质：被修好的那个值，生产代码里**没有任何消费者**::

    git grep -E "sorted_items|items_visible|\\.bed_w|\\.bed_h|bed_w=|bed_h=" -- src/
    → 30 处命中，**全部**在 layout/model.py 一个文件里；model.py 之外 = 0

生产链三处都绕开它、各读 ``BED_W``/``BED_H`` 常量：导出
(:func:`document_to_svg` 宽度**写死**常量、只有高度是参数)、越床闸
(:meth:`LayoutPage._out_of_bed`)、画布床框与行程校验。

探针实测（offscreen，见 result 的 proof）::

    doc.bed_w = 100.0 / doc.page.bed_w = 100.0   ← 门面自洽
    document_to_svg(doc) -> width="210mm" height="210mm" viewBox="0 0 210 210"
    _out_of_bed() = None                          ← 按 210 判不越界

**为什么不能顺手把它接上**（这才是本文件真正在守的东西）：
``BED_W``/``BED_H`` = 210mm 是**本机真实床**（AGENTS.md 机器事实）。页级
``bed_w/bed_h`` 是任何外来载荷（剪贴板 / 版面 JSON）都能写的一份**声明**。
一份声明 ``bed_w=300`` 的文档接进来，若拿它当闸门，x=250 的图元就**不再被拦**
⇒ 刀落在 210 床面之外。今天锚在常量上，恰恰是「不会切错、不撞床外」的原因。
所以本文件钉死两条：

- 页级床是**惰性**的（门面对得上，产出链对不上）—— 这是**现状记录**，改它
  需要连带改 export_svg（不在本线所有权内），且它只有高度是参数，接上去只会
  得到长短轴错配的 SVG；
- 页级床**不得放宽**越床闸 —— 这是**安全不变量**，把闸门接到
  ``doc.page.bed_*`` 的改法必须让本文件报红。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# -- 工具 ------------------------------------------------------------------

def _page():
    from megapro.gui.layout.layout_page import LayoutPage

    return LayoutPage()


def _line(x0, x1, y=0.0, name="line"):
    from megapro.gui.layout.model import Item

    return Item(paths=[[(x0, y), (x1, y)]], name=name)


def _svg_head(svg: str) -> str:
    """带 width/height/viewBox 的那行（``<svg ...>``），不是紧随其后的 polyline。"""
    return svg.splitlines()[0]


def _bed_at(w: float, h: float, *, x1: float = 150.0):
    """一份页级床 = w×h 的文档 + 一条 x∈[0, x1] 的线。

    默认 x1=150：对 100/120 越界、对 210 不越界（反例原样）。
    ``x1=250`` 用于「页床 300 / 物理床 210」那组：对 300 在床内、对 210 越界
    40mm —— 正是「拿声明的床当闸门会漏放行」的形状。

    页级床只能经 ``Page(bed_w=…, bed_h=…)`` 构造：今天全 UI 无控件能改它
    （无生产消费者，见文件头），所以外来载荷是唯一来源。
    """
    from megapro.gui.layout.model import Document, Page

    doc = Document(pages=[Page(bed_w=w, bed_h=h)])
    doc.add(_line(0.0, x1))
    return doc


def _gate(doc):
    """越床闸对这份文档的判定（走真 :meth:`LayoutPage._out_of_bed`）。"""
    lp = _page()
    lp.doc = doc
    lp._sync_models()
    return lp._out_of_bed()


# -- ① 门面自洽：这是 A2 真正修好的部分，不许退回去 -------------------------

def test_facade_bed_still_reads_through_to_current_page():
    """A2 的保证仍在：``doc.bed_*`` 恒等于 ``doc.page.bed_*``。

    本条**不是** R1 要推翻的东西 —— 反例自己也写着「这条我攻不倒」。钉它是为了
    防止有人为了「让产出链跟着页走」而把门面的 property 改回 dataclass 字段。
    """
    doc = _bed_at(100.0, 120.0)
    assert doc.bed_w == 100.0 and doc.page.bed_w == 100.0
    assert doc.bed_h == 120.0 and doc.page.bed_h == 120.0
    assert doc.bed_w == doc.page.bed_w and doc.bed_h == doc.page.bed_h


# -- ② 反例本身：页级床是**惰性**的，产出链仍锚物理床 -----------------------

def test_exported_svg_ignores_page_declared_bed():
    """页声明 100×120，导出仍是 210×210 —— 页级床对产出**零影响**。

    这是反例的正面复现，也是「床尺寸已收口」这句话的真实边界。
    """
    from megapro.gui.canvas.coords import BED_H, BED_W
    from megapro.gui.layout.export_svg import document_to_svg

    doc = _bed_at(100.0, 120.0)
    head = _svg_head(document_to_svg(doc))

    assert f'width="{BED_W:g}mm"' in head, f"导出宽度应恒为物理床宽：{head}"
    assert f'height="{BED_H:g}mm"' in head, f"导出高度应恒为物理床高：{head}"


def test_export_chain_has_no_width_parameter():
    """导出链**只有高度是参数**、宽度写死常量。

    为什么钉这条：后来者若「按 A2 精神」把 ``doc.page.bed_h`` 接进
    ``bed_h=`` 参数，会得到 ``width="210mm" height="100mm"`` 这种**长短轴错配**
    的 SVG —— 比两边都锚常量更难排查。本条让那条错配在实现发生前就被看见。
    """
    from megapro.gui.canvas.coords import BED_W
    from megapro.gui.layout.export_svg import document_to_svg

    doc = _bed_at(100.0, 120.0)
    head = _svg_head(document_to_svg(doc, bed_h=120.0))

    assert 'height="120mm"' in head, "高度参数应当生效"
    assert f'width="{BED_W:g}mm"' in head, \
        f"宽度不接受参数（接页床只会长短轴错配），实得：{head}"


def test_page_declared_bed_does_not_tighten_or_loosen_gate_for_in_bed_geometry():
    """页声明 100×120 + x∈[0,150] ⇒ 闸门判**不越界**（它看的是物理床 210）。

    这条记的是现状（反例的观察），不是期望。期望见下一组：安全不变量。
    """
    assert _gate(_bed_at(100.0, 120.0)) is None, \
        "越床闸按物理床 210 判：x∈[0,150] 不越界，页级 100 不参与判定"


# -- ③ 安全不变量：页级床**不得放宽**物理床闸门 -----------------------------

def test_oversized_page_bed_cannot_widen_the_gate():
    """外来文档声明 ``bed_w=300`` ⇒ x=250 的图元**仍判越界**，照旧被拦。

    这是本文件最硬的一条。若有人把 :meth:`LayoutPage._out_of_bed` 改成读
    ``self.doc.page.bed_w/bed_h``，声明 300 的文档会把 x=250 放行 ⇒
    刀落在 210 床面之外 ⇒ 本条立即报红。

    ⚠ 与 :func:`test_page_declared_bed_does_not_tighten_or_loosen_gate_for_in_bed_geometry`
    不矛盾：那条钉「更小的页床不收紧」（现状），这条钉「更大的页床不放宽」
    （**安全底线**）。把闸门接到页床会同时违反本条 —— 那才是必须挡住的方向。
    """
    info = _gate(_bed_at(300.0, 300.0, x1=250.0))
    assert info is not None, \
        "页级床 300 竟把 x=250 放行了 —— 物理床只有 210，这会切到床外"
    amount, names = info
    assert amount == pytest.approx(40.0), f"越界量应按物理床 210 算 250-210=40：{amount}"
    assert names, "越界清单不得为空"


def test_gate_always_measures_against_physical_bed_constants():
    """闸门的判据恒为 ``BED_W``/``BED_H``：四种页床声明下判定逐位相同。

    把「哪个数在当权威」钉成不变量，而不是钉某一次的输出 —— 页床写成 100、
    120、210、300 都得同一个答案（None），这样任何把页床接进闸门的改法
    都会在这里露出来。
    """
    from megapro.gui.canvas.coords import BED_W

    results = [_gate(_bed_at(w, w)) for w in (100.0, 120.0, 210.0, 300.0)]
    assert results == [None, None, None, None], f"页床竟然影响了判定：{results}"
    # 佐证：150 < BED_W(210) ⇒ 物理床下确实不越界
    assert 150.0 < BED_W


def test_gate_code_never_reads_page_level_bed():
    """越床闸的**可执行语句**里不得出现页级床（docstring 里的文字不算）。

    钉住「把闸门接到 ``doc.page.bed_*``」这个改法必须被挡住。行为层已经由
    :func:`test_oversized_page_bed_cannot_widen_the_gate` 兜住；本条再加一道
    **静态**关口，让它在跑出坏结果之前就红。

    为什么用 AST 而非 ``inspect.getsource`` + 子串：源码里散布着解释这件事的
    中文 docstring（里面必然出现 ``page.bed_w`` 字样），按子串查会把**说明**
    误判成**代码**。这里只遍历非 docstring 的语句节点。
    """
    import ast
    import inspect
    import textwrap

    from megapro.gui.layout.layout_page import LayoutPage

    tree = ast.parse(textwrap.dedent(inspect.getsource(LayoutPage._out_of_bed)))
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)

    # ① ``doc.page.bed_w`` 之类的属性访问
    hits = [
        ast.unparse(n)
        for n in ast.walk(fn)
        if isinstance(n, ast.Attribute) and n.attr in ("bed_w", "bed_h")
    ]
    # ② ``getattr(doc, "bed_w")`` / ``doc["bed_w"]`` 这类绕开属性节点的写法
    #    （反证时正是用它溜过去的：属性检查全绿，行为却已放行）
    hits += [
        ast.unparse(n)
        for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and n.value in ("bed_w", "bed_h")
    ]
    assert not hits, f"越床闸的可执行代码读了页级床：{hits}"


def test_svg_export_exit_goes_through_the_same_gate():
    """「另存为 SVG」是纯写文件，但**同样过** :meth:`LayoutPage._confirm_in_bed`。

    钉住 C1 的两出口同判，不被「顺手让页床说话」改掉。同样只看可执行调用，
    不用子串匹配（docstring 里提到闸门名是正常的）。
    """
    import ast
    import inspect
    import textwrap

    from megapro.gui.layout.layout_page import LayoutPage

    tree = ast.parse(textwrap.dedent(inspect.getsource(LayoutPage._on_save_svg)))
    fn = tree.body[0]
    called = {
        n.func.attr for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "_confirm_in_bed" in called, \
        f"另存为 SVG 未过同一道越床闸（实际调用：{sorted(called)}）"
