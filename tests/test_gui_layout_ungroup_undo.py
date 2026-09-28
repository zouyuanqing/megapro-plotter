"""A2 回归：解组之后按 Ctrl+Z 是静默空操作，组回不来。

背景（A2 实测）：``Page.ungroup`` 是**破坏性**的 —— 摘容器、把 children
原地提升，然后 ``container.children = []``（"它不再是组"）。这是对的：调用
方拿回 kids 自己处理。问题在 ``UngroupCommand._do_undo``：它开头就是

    if not self.container.children:
        return

而 ``redo`` 刚把 children 清空 ⇒ **每次 undo 都在这里 return**。类 docstring
却写着"undo 据此原位挂回"—— 声明了却不实现。实测证据：

- 直接构造 ``UngroupCommand``：``redo()`` 后 children=[]、``undo()`` 后
  children 仍=[]、容器不在文档内；
- 真实窗口路径 ``group_selected() → ungroup_selected() → undo()``：按一次
  撤销 index 3→2、undoText「解组」→「编组」，但顶层仍是 ``['a','b']``、组
  没恢复；
- monkeypatch 数 ``Page.attach`` 调用次数，跨过一次解组撤销 3→3 **零增长**
  ⇒ undo 里那行 ``attach`` 是死代码；
- **级联**：第二次 Ctrl+Z 撤 ``GroupCommand`` 时操作的还是那个已被清空的
  孤儿容器，同样空转；用户要按 3 次才见到第一次真实效果。

⚠ **不能只删掉那个守卫**。实测：ungroup 之后直接 ``doc.attach(g, index=0)``
得到 ``items=['G','a','b']`` 而 ``G.children=[]`` —— **空组 + 孩子全被提为
顶层**的错误结构（顶层凭空多出一个无子项的幽灵容器 G）。正确修法必须**先
把孩子重新 attach 回 ``container.children``、再挂容器**。

本文件钉的契约（要求 1/2/3）：
- undo 后顶层顺序、children 顺序、每条折线几何与**解组前**逐点相同；
- 连按 undo 每一步都有真实效果，无静默空转；
- 解组再 undo 不产生「孩子同时在容器和顶层」的**重复切割**结构。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _line(y, *, name, z=0.0):
    from megapro.gui.layout.model import Item

    return Item(paths=[[(0.0, float(y)), (10.0, float(y))]], name=name, z=z)


def _snapshot(doc):
    """文档结构的可比较快照：每页顶层序 + 容器 children 序 + 拍平几何。"""
    from megapro.gui.layout.model import flatten_visible

    pages = []
    for page in doc.pages:
        pages.append([
            (it.name, [c.name for c in it.children]) for it in page.items
        ])
    return pages, [[(round(x, 6), round(y, 6)) for x, y in p]
                   for p in flatten_visible(doc)]


def _select_all(lp):
    for gi in lp._scene_items:
        gi.setSelected(True)


def _assert_no_double_host(lp, tag):
    """任一图元不得同时出现在两处（容器 children 里 + 顶层）——那是重复切割。"""
    from megapro.gui.layout.model import iter_items

    for page in lp.doc.pages:
        top = {id(it) for it in iter_items(page.items)}
        for it in page.items:
            for ch in it.descendants():
                assert id(ch) in top or any(ch is o for o in it.children), (
                    f"[{tag}] {ch.name!r} 挂载位置异常")
        # 顶层每个条目只出现一次（同一对象不在顶层列表里重复）
        seen = []
        for it in iter_items(page.items):
            seen.append(id(it))
        assert len(seen) == len(set(seen)), f"[{tag}] 同一对象在树里出现多次"


# --- 要求 1：undo 之后结构与解组前逐点相同 ---------------------------------

def test_ungroup_undo_restores_group_structure_pointwise():
    """编组 → 解组 → undo ⇒ 组恢复、children 顺序与几何逐点一致 → 再 undo 散开。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a, b = _line(0, name="a"), _line(5, name="b")
    lp._add_items([a, b])
    _select_all(lp)
    lp.group_selected()
    grouped = _snapshot(lp.doc)
    assert [it.name for it in lp.doc.items] == ["组2"]

    _select_all(lp)
    lp.ungroup_selected()
    assert [it.name for it in lp.doc.items] == ["a", "b"]
    assert _snapshot(lp.doc)[1] == grouped[1], "解组本身不得改几何"

    lp._undo.undo()                                   # 撤解组
    assert _snapshot(lp.doc) == grouped, "undo 未把组恢复成解组前的结构"
    _assert_no_double_host(lp, "撤解组后")

    lp._undo.undo()                                   # 撤编组
    assert [it.name for it in lp.doc.items] == ["a", "b"]
    assert _snapshot(lp.doc)[1] == grouped[1], "撤编组后几何必须逐点复原"
    _assert_no_double_host(lp, "撤编组后")
    lp.deleteLater()


def test_ungroup_undo_restores_container_to_original_slot():
    """组在顶层有兄弟项时，undo 必须回**原下标**（不是追加到末尾）。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a, b, tail = _line(0, name="a"), _line(5, name="b"), _line(9, name="tail")
    lp._add_items([a, b, tail])
    _select_all(lp)
    for gi in lp._scene_items:
        gi.setSelected(gi.model_item is a or gi.model_item is b)
    lp.group_selected()
    assert [it.name for it in lp.doc.items] == ["组2", "tail"]

    _select_all(lp)
    lp.ungroup_selected()
    assert [it.name for it in lp.doc.items] == ["a", "b", "tail"]

    lp._undo.undo()
    assert [it.name for it in lp.doc.items] == ["组2", "tail"], \
        "组必须回到原槽位 0，而不是被追加到末尾"
    lp.deleteLater()


def test_ungroup_undo_restores_nested_container_owner_and_index():
    """嵌套：解组一个**在别的容器里**的组，undo 后回原 owner 的原下标。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a, b, c, d = (_line(0, name="a"), _line(5, name="b"),
                  _line(9, name="c"), _line(13, name="d"))
    lp._add_items([a, b, c, d])
    g1 = lp.doc.group_items([a, b], name="G1")
    g2 = lp.doc.group_items([c, d], name="G2")
    outer = lp.doc.group_items([g1, g2], name="OUT")
    assert [it.name for it in lp.doc.items] == ["OUT"]
    before = _snapshot(lp.doc)

    from megapro.gui.canvas.undo_cmds import UngroupCommand
    lp._undo.push(UngroupCommand(lp, g1))            # 解组嵌在 OUT 里的 G1
    assert [c.name for c in outer.children] == ["a", "b", "G2"]

    lp._undo.undo()
    assert _snapshot(lp.doc) == before, "嵌套组未按原 owner/下标复原"
    assert [c.name for c in outer.children] == ["G1", "G2"]
    assert [c.name for c in g1.children] == ["a", "b"]
    _assert_no_double_host(lp, "嵌套撤销后")
    lp.deleteLater()


# --- 要求 2：连按 undo 每一步都有真实效果 ---------------------------------

def test_consecutive_undo_after_ungroup_has_no_silent_noop():
    """级联：解组后连按两次撤销，每一步结构都要真的变（不静默空转）。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a, b = _line(0, name="a"), _line(5, name="b")
    lp._add_items([a, b])
    _select_all(lp)
    lp.group_selected()
    _select_all(lp)
    lp.ungroup_selected()

    lp._undo.undo()                                   # 撤解组 ⇒ 组回来
    assert [it.name for it in lp.doc.items] == ["组2"], "第 1 次撤销是空转"
    lp._undo.undo()                                   # 撤编组 ⇒ 散开
    assert [it.name for it in lp.doc.items] == ["a", "b"], "第 2 次撤销是空转"
    lp._undo.undo()                                   # 撤添加 ⇒ 空页
    assert [it.name for it in lp.doc.items] == [], "第 3 次撤销是空转"
    lp.deleteLater()


def test_ungroup_undo_redo_roundtrip_is_stable():
    """undo/redo 多个来回结果恒定（不因记录被就地改写而漂移）。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a, b = _line(0, name="a"), _line(5, name="b")
    lp._add_items([a, b])
    _select_all(lp)
    lp.group_selected()
    grouped = _snapshot(lp.doc)
    _select_all(lp)
    lp.ungroup_selected()
    flat = _snapshot(lp.doc)

    for _ in range(3):
        lp._undo.undo()
        assert _snapshot(lp.doc) == grouped, "来回中组结构漂移"
        _assert_no_double_host(lp, "来回-组态")
        lp._undo.redo()
        assert _snapshot(lp.doc) == flat, "来回中散态漂移"
        _assert_no_double_host(lp, "来回-散态")
    lp.deleteLater()


# --- 解组命令本身（不经窗口路径） -------------------------------------------

def test_ungroup_command_undo_restores_children_then_container():
    """直接构造 UngroupCommand：undo 必须先补 children、再挂容器（不是空挂）。"""
    from megapro.gui.canvas.undo_cmds import UngroupCommand
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a, b = _line(0, name="a"), _line(5, name="b")
    lp._add_items([a, b])
    g = lp.doc.group_items([a, b], name="G")
    before = _snapshot(lp.doc)

    uc = UngroupCommand(lp, g)
    uc.redo()
    assert [it.name for it in lp.doc.items] == ["a", "b"]
    assert g.children == [], "redo 前提：ungroup 已清空容器（破坏性）"

    uc.undo()
    assert [c.name for c in g.children] == ["a", "b"], \
        "undo 未把孩子按原顺序 attach 回容器"
    assert [it.name for it in lp.doc.items] == ["G"], \
        "孩子仍留在顶层 = 同一几何切两遍（只挂空容器是错的）"
    assert _snapshot(lp.doc) == before
    _assert_no_double_host(lp, "命令级撤销后")
    lp.deleteLater()


def test_ungroup_undo_does_not_leave_phantom_empty_container():
    """撤销后不得出现顶层多一个**无子项**的幽灵容器（空组 + 孩子在顶层）。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a, b = _line(0, name="a"), _line(5, name="b")
    lp._add_items([a, b])
    _select_all(lp)
    lp.group_selected()
    _select_all(lp)
    lp.ungroup_selected()
    lp._undo.undo()

    tops = lp.doc.items
    assert len(tops) == 1 and tops[0].is_container(), \
        f"顶层应恰有一个容器，实得 {[it.name for it in tops]}"
    assert [c.name for c in tops[0].children] == ["a", "b"]
    lp.deleteLater()
