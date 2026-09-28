"""R2 回归：``children`` 写入点的「产品路径不可达」枚举必须**穷尽且可核查**。

R2 推翻了上一批（要求 2）那处「陈述更正」本身。两处缺陷：

(a) **行号指向散文**。枚举里写「``model.py:801/830/832/960`` 共 8 处」，实测这四行
    分别是 docstring 的 bullet、一条 ``#`` 注释、另一条 ``#`` 注释、和
    ``Document.__init__`` docstring 里的一句话 —— **一处 attach 调用都不是**。
    真正的 attach 调用点当时是 859/888/890（+1047 门面）。

(b) **枚举漏了 3 个直接写 ``children`` 的点**。原句称「``src/`` 里能写 ``children``
    的有 ①②③」，但 ``group_items`` 里的 ``cont.children.append(it)`` /
    ``cont.children = []`` 与 ``ungroup`` 里的 ``container.children = []``
    **完全绕过** ``Page.attach`` 的守卫。它们今天安全靠的是「先 remove 再 append」
    这条纪律，不是靠守卫 —— 而「守卫 + 纪律」被写成了「只有纪律」。

**本文件刻意不校验「当前正确的行号」** —— 行号在 model.py 每被改一次就失效一次
（该批次自身就因此陈旧过一次）。那正是本项目反复吃的亏：断言会变的量。
改为钉**两件不会变的事**：

1. ``src/megapro/gui/`` 下每一个**可执行**的 ``children`` 写入点，
   要么落在 ``Page.attach`` 体内（受守卫保护），要么在两处枚举里被**点名**；
2. 枚举里出现的每一个 ``model.py:NNN`` 行号引用，必须**真的落在 attach 调用点上**
   —— 修法可以是「改成正确行号」，也可以是「删掉行号、只留函数名」，两者都绿；
   唯独「留着一个指向散文的老行号」会红。

同时用运行时 trace 钉住独立复核者查出的那条事实：``group_items`` 路径上
``Page.attach`` 的守卫**一次都没被求值**（它唯一那次 attach 走的是 ``owner is
None`` 顶层分支，而该分支按设计不加守卫）—— 这正是 ② 那句陈述的证据。
"""

import ast
import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from megapro.gui.layout import model as _model   # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src", "megapro", "gui", "layout")

#: R2 未修：两处枚举的**文本**在 ``model.py`` 里，而该文件不在本批白名单
#: （其它线正在并行改它）。理由与 R1 那三条 xfail 同源：留着普通测试会把仓库
#: 交给下一道门时留红；``skip`` 会让「枚举穷尽性」这个契约消失。
#: **strict=True** 保证它不会烂在文件里：文本一旦修好，本条会 XPASS(strict)
#: 报红，逼人摘标记。
#: 摘标记的条件（已用 ``__doc__`` 运行时替换**验证过**，未改任何仓库文件）：
#: 在 ``Item.descendants`` 那段穷尽枚举里点名 ``group_items`` 与 ``ungroup``、
#: 并注明它们绕过守卫、只靠「先 remove 再 append」纪律；把
#: ``model.py:801/830/832/960`` 改成正确行号或直接删掉行号只留函数名。
_XFAIL_R2 = pytest.mark.xfail(
    strict=True,
    reason="R2 未修：model.py 两处「产品路径不可达」枚举的文本缺陷"
           "（4 个行号指向散文 + 漏枚举 3 个绕过守卫的 children 写入点）。"
           "修复需改 model.py，不在本批白名单。修好后本条会 XPASS(strict) 报红，"
           "届时请删除此标记。",
)


def _enumeration_passage() -> str:
    """``Item.descendants`` docstring 里**那段穷尽枚举**（不是整篇 docstring）。

    为什么要抠出这一段：``group_items`` 这几个字在整篇 docstring 里本来就有，
    但出现在「成环自含拒绝内部要遍历每个成员的子树」那句与枚举**无关**的话里。
    拿整篇做「点名」检查，等于让这条测试因为无关的提及而恒绿 —— 那又是一条
    空断言。只认真正列构造者的那一段，才是对「枚举穷尽」的检查。
    """
    doc = inspect.getdoc(_model.Item.descendants) or ""
    paras = [p for p in doc.split("\n\n")]
    marker = ("构造者", "能写 ``children``", "wrap_group")
    hit = [p for p in paras if any(m in p for m in marker)]
    assert hit, (
        "找不到那段穷尽枚举（docstring 结构变了？）—— 本文件失去了检查对象，"
        "请把「能写 children 的有 ①②③」那段的锚点同步更新")
    return "\n".join(hit)


def _line_refs() -> list[int]:
    """枚举里出现的所有行号引用。

    要同时吃两种写法：``model.py:801`` 与 ``model.py:801/830/832/960``
    —— 评审点名的正是后者，只匹配单行形式会漏掉 3 个。
    """
    import re

    text = inspect.getdoc(_model.Item.descendants) or ""
    text += "\n" + (inspect.getdoc(_model.Page.attach) or "")
    refs: set[int] = set()
    for m in re.finditer(r"model\.py:([\d/]+)", text):
        refs.update(int(x) for x in re.findall(r"\d+", m.group(1)))
    return sorted(refs)


# -- children 写入点扫描（AST，不是 grep） -----------------------------------

def _children_writers():
    """``layout/`` 下每个可执行的 ``children`` 写入点 → (文件, 所在函数名, 行号)。

    用 AST 而不是文本 grep：grep 会被 docstring/注释里的散文命中（本次评审正是
    被散文坑的），必须看真正会执行的赋值与调用。
    """
    found = []
    for fn in sorted(os.listdir(_SRC)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(_SRC, fn)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=fn)
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def enclosing_func(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node.name
            return "<module>"

        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for tgt in targets:
                # ``cont.children = []`` 的 target.value 是 Name(cont)，
                # 只认 Attribute 会把这类**清空**写入整片漏掉（评审点名的 883/919
                # 正是这种）—— 扫描器自己漏点比没扫描更危险。
                if isinstance(tgt, ast.Attribute) and tgt.attr == "children":
                    found.append((fn, enclosing_func(node), node.lineno))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("append", "insert", "extend") \
                    and isinstance(node.func.value, ast.Attribute) \
                    and node.func.value.attr == "children":
                found.append((fn, enclosing_func(node), node.lineno))
    return found


def test_attach_is_actually_a_children_writer():
    """自检：扫描器看得见 ``Page.attach`` 体内的写入点（否则下面的守卫是空的）。"""
    writers = _children_writers()
    inside_attach = [w for w in writers if w[0] == "model.py" and w[1] == "attach"]
    assert inside_attach, (
        f"扫描器没认出 Page.attach 体内的 children 写入点（扫描器坏了）：{writers}")
    assert any(w[0] == "model.py" and w[1] in ("group_items", "ungroup")
               for w in writers), "扫描器没认出 group_items/ungroup 的写入点（扫描器坏了）"


@_XFAIL_R2
def test_every_children_writer_is_enumerated():
    """每个 ``children`` 写入点，要么受 ``Page.attach`` 守卫保护，要么被枚举点名。

    这条是 (b) 的直接编码。评审点名的三个点（``group_items`` 的两处、
    ``ungroup`` 的一处）今天**没有**被任何一处枚举提到，故本条当前为红。
    """
    doc = _enumeration_passage()
    unprotected = []
    for fn, func, line in _children_writers():
        if fn == "model.py" and func == "attach":
            continue                                  # 守卫体内，受保护
        if fn == "doc_import.py" and func == "wrap_group":
            continue                                  # 枚举里的 ①
        if func not in doc:
            unprotected.append(f"{fn}:{line}（{func}）")
    assert not unprotected, (
        "这些 children 写入点**绕过** Page.attach 的守卫，却没有在 Item.descendants"
        "那段穷尽枚举里被点名：\n  "
        + "\n  ".join(unprotected)
        + "\n⇒ 原句「src/ 里能写 children 的有 ①②③」不成立。"
          "它们今天安全靠「先 remove 再 append」的纪律，不靠守卫；"
          "陈述必须把这一点写出来。")


@_XFAIL_R2
def test_line_number_references_point_at_real_attach_calls():
    """枚举里每个 ``model.py:NNN`` 引用必须真的落在 attach 调用点上（R2 的 (a)）。

    修法二选一：改成当前正确行号，或干脆删掉行号只留函数名 —— 都绿。
    唯独「留着一个指向 docstring/注释的老行号」会红。
    """
    with open(os.path.join(_SRC, "model.py"), encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    refs = _line_refs()
    # ⚠ 这里**不能**再加「至少得有一个行号引用」的自检：合法修法之一就是
    # 「把会漂移的行号全删掉、只留函数名」，那样 refs 为空、本条应当绿。
    # （我第一版加了这条自检，结果把「删行号」这个正确修法判成了红 ——
    #  测试自己的约束和它声称的契约打架。扫描器本身由
    #  test_attach_is_actually_a_children_writer 守住，不靠这里。）

    bad = []
    for n in refs:
        if not (1 <= n <= len(lines)):
            bad.append(f"model.py:{n} → 越界（文件只有 {len(lines)} 行）")
            continue
        text = lines[n - 1]
        if "attach(" not in text:
            bad.append(f"model.py:{n} → {text.strip()[:60]!r}（不是 attach 调用）")
    assert not bad, "枚举里的行号引用指向了散文/注释：\n  " + "\n  ".join(bad)


# -- 独立复核者查出的事实：group_items 路径上守卫从未生效 --------------------

def test_group_items_never_reaches_attach_guard():
    """钉住事实：``group_items`` 唯一那次 attach 走的是 **owner is None** 分支。

    守卫体在 ``Page.attach`` 的 owner 给定分支里；编组路径上 owner 恒为 None，
    故守卫**一次都没被求值**。编组的成环安全完全来自 ``group_items`` 自己的
    自含检查 + 「先 remove 再 append」纪律 —— 陈述里必须照实这么写，
    不能写成「A1 的守卫也管着编组路径」。

    这是运行时 trace（sys.settrace）实测出来的，不是从代码读出来的。
    """
    import sys

    from megapro.gui.layout.model import Document, Item

    def leaf(name):
        return Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name=name)

    doc = Document()
    page = doc.page
    page.attach(leaf("a"))
    page.attach(leaf("b"))

    attach_src, first_line = inspect.getsourcelines(_model.Page.attach)
    guard_start = first_line + next(
        i for i, ln in enumerate(attach_src) if "if any(ch is item" in ln)
    guard_end = first_line + next(
        i for i, ln in enumerate(attach_src) if "_stamp_order(item)" in ln)

    hits: list[int] = []

    def trace(frame, event, arg):
        if event == "line" and frame.f_code.co_filename.endswith("model.py") \
                and frame.f_code.co_name == "attach":
            hits.append(frame.f_lineno)
        return trace

    sys.settrace(trace)
    try:
        page.group_items(page.items[:])
    finally:
        sys.settrace(None)

    assert hits, "没抓到 attach 的执行轨迹（trace 失效）"
    reached_guard = [ln for ln in hits if guard_start < ln < guard_end]
    assert not reached_guard, (
        f"attach 守卫体竟然在编组路径上被求值了：{reached_guard}。"
        "若实现改了（给顶层分支也加守卫），请同步改这条与那两处枚举的措辞。")
