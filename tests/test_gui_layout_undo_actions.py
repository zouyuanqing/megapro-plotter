"""C4：撤销/重做**按钮文案与可用态**随栈实时变化。

⚠ **本文件是「钉住现状」，不是「钉住缺陷」** —— 这是本文件与仓库里其它回归
文件最大的不同，请下一个人先读完这段再动手。

C4 的缺陷报告称：``_refresh_undo_actions`` 只被调用一次（构造时）、
``QUndoStack.indexChanged`` 没接上，故按钮文案**永远**停在初始的「撤销/重做」，
用户按之前不知道会撤什么。

**该现象在本仓库复现不了。** 实测（PySide6 6.11.2 / offscreen，逐条见 result
的 proof）::

    初始      撤销(禁用)          重做(禁用)
    添加后    撤销 添加(可用)      重做(禁用)
    镜像后    撤销 水平镜像(可用)   重做(禁用)
    层序后    撤销 层序(可用)       重做(禁用)
    撤销一次  撤销 水平镜像(可用)   重做 层序(可用)
    撤销两次  撤销 添加(可用)       重做 水平镜像(可用)
    撤到底    撤销(禁用)          重做 添加(可用)
    重做回去  撤销 添加(可用)      重做(禁用)
    clear()   撤销(禁用)          重做(禁用)

原因：按钮上的 action 是 ``QUndoStack.createUndoAction(self, "撤销")`` 建的
**``QUndoAction``**，Qt 自己就会在每次 push/undo/redo/clear 时把文案重写成
「前缀 + ``undoText()``」、把可用态重算成 ``canUndo()``/``canRedo()``；工具条上
那个 ``QToolButton`` 是 ``setDefaultAction(a)`` 装的，跟着 action 走。
所以 ``_refresh_undo_actions`` 是一次性的**空转**（栈空时 ``undoText()`` 为
``''``，``f"撤销 ".strip()`` 还是「撤销」），**接不接 indexChanged 都一样**。

**因此本文件断言的全是「不许退回去」**：哪天有人把 ``createUndoAction`` 换成
自己 ``QAction``、或把按钮的 ``setDefaultAction`` 去掉、或在两处各写一套文案，
本文件即红。不要为了「让它更像一个已修的缺陷」而去给 ``indexChanged`` 加一个
重写文案的槽 —— 那是在栈每次变动时做一遍**完全多余**的工作（拖动/绘制等高频
手势会白跑），且有可能与 Qt 的文案规则分叉。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

#: 三个**名字各不相同**的命令 —— 名字相同的话「文案有没有更新」根本测不出来
#: （两次「添加」都显示「撤销 添加」，不更新也长得一样）。
_ADD = "添加"
_MIRROR = "水平镜像"
_ZORDER = "层序"
_UNDO_DEFAULT = "撤销"
_REDO_DEFAULT = "重做"


def _page():
    from megapro.gui.layout.layout_page import LayoutPage

    return LayoutPage()


def _buttons(lp):
    """工具条上真正挂着的两个 QToolButton（用户看见的就是它们，不是 action）。

    按 ``defaultAction()`` 认领，不按顺序/索引 —— 工具条按钮增删不该让本文件失效。
    """
    undo = [b for b in lp.findChildren(QtWidgets.QToolButton)
            if b.defaultAction() is lp._undo_act]
    redo = [b for b in lp.findChildren(QtWidgets.QToolButton)
            if b.defaultAction() is lp._redo_act]
    assert len(undo) == 1 and len(redo) == 1, \
        f"工具条上的撤销/重做按钮各应恰好一个，实得 {len(undo)}/{len(redo)}"
    return undo[0], redo[0]


def _seed(lp):
    from megapro.gui.layout.model import Item

    lp._add_items([Item(paths=[[(0.0, 0.0), (10.0, 0.0),
                               (10.0, 10.0), (0.0, 0.0)]], name="a")])


def _mirror_it(lp):
    it = lp.doc.items[0]
    lp._gi_for(it).setSelected(True)
    lp._toggle_mirror("h")


# -- ① 文案随栈变化 ---------------------------------------------------------

def test_undo_label_tracks_top_of_stack():
    """做一次操作 ⇒ 按钮含该操作名；再做**另一种**操作 ⇒ 文案跟着换。"""
    lp = _page()
    undo_btn, _ = _buttons(lp)

    _seed(lp)
    assert _ADD in undo_btn.text(), \
        f"添加后撤销按钮未含「{_ADD}」：{undo_btn.text()!r}"
    assert undo_btn.text().startswith(_UNDO_DEFAULT), \
        f"撤销按钮丢了前缀「{_UNDO_DEFAULT}」：{undo_btn.text()!r}"

    _mirror_it(lp)
    assert _MIRROR in undo_btn.text(), \
        f"镜像后撤销按钮未更新为「{_MIRROR}」：{undo_btn.text()!r}"
    assert _ADD not in undo_btn.text(), \
        f"撤销按钮仍停在上一条命令：{undo_btn.text()!r}"

    lp._zorder("top")
    assert _ZORDER in undo_btn.text(), \
        f"层序后撤销按钮未更新为「{_ZORDER}」：{undo_btn.text()!r}"
    # 栈顶变了，action 与按钮必须一致（不是两个各写一套的东西）
    assert undo_btn.text() == lp._undo_act.text()


def test_undo_label_without_our_own_refresh_call():
    """**不调用** ``_refresh_undo_actions``，文案照样实时变。

    这条是本文件存在的理由：它证明「实时更新」是 Qt ``QUndoAction`` 自带的，
    我们**没有**也**不需要**在 ``indexChanged`` 上挂一个重写文案的槽。若哪天
    它变红了而代码里也没人加过那个槽，说明 Qt 行为或用法被换掉了。
    """
    lp = _page()
    undo_btn, _ = _buttons(lp)

    _seed(lp)

    assert undo_btn.text() != _UNDO_DEFAULT, "文案没被更新（本该由 Qt 自动更新）"
    assert _ADD in undo_btn.text()


# -- ② 撤到底 ⇒ 文案回落默认值 + 按钮变灰 ----------------------------------

def test_undo_to_empty_falls_back_to_default_and_disables():
    """撤销到栈空 ⇒ 按钮**禁用**且文案回落到「撤销」默认值。"""
    lp = _page()
    undo_btn, redo_btn = _buttons(lp)

    _seed(lp)
    _mirror_it(lp)
    assert undo_btn.isEnabled(), "有东西可撤时按钮应可用"

    while lp._undo.canUndo():
        lp._undo.undo()

    assert not undo_btn.isEnabled(), "栈空后撤销按钮仍可点"
    assert undo_btn.text() == _UNDO_DEFAULT, \
        f"栈空后文案未回落默认值：{undo_btn.text()!r} != {_UNDO_DEFAULT!r}"
    # 撤到底 = 重做栈满，重做必须**可用**且指回第一条
    assert redo_btn.isEnabled(), "栈空（刚撤完）时重做按钮应可用"
    assert _ADD in redo_btn.text(), f"重做文案未指回第一条：{redo_btn.text()!r}"


def test_redo_back_to_top_disables_redo():
    """一路重做回栈顶 ⇒ 重做按钮**禁用**且文案回落到「重做」默认值。"""
    lp = _page()
    _seed(lp)
    _mirror_it(lp)
    _, redo_btn = _buttons(lp)

    while lp._undo.canUndo():
        lp._undo.undo()
    while lp._undo.canRedo():
        lp._undo.redo()

    assert not redo_btn.isEnabled(), "无东西可重做时重做按钮仍可点"
    assert redo_btn.text() == _REDO_DEFAULT, \
        f"无可重做时文案未回落默认值：{redo_btn.text()!r} != {_REDO_DEFAULT!r}"


def test_clear_resets_both_labels_and_states():
    """``clear()`` ⇒ 两个按钮都回默认值且都禁用。"""
    lp = _page()
    undo_btn, redo_btn = _buttons(lp)
    _seed(lp)
    _mirror_it(lp)

    lp._undo.clear()

    assert undo_btn.text() == _UNDO_DEFAULT and not undo_btn.isEnabled()
    assert redo_btn.text() == _REDO_DEFAULT and not redo_btn.isEnabled()


# -- ③ 每一次栈变动都要刷新（不是「某些路径」） ------------------------------

def test_label_updates_on_every_stack_mutation():
    """逐次撤销：每一步的文案都要跟着换，不是只在某一步更新。

    这条钉的是「实时」二字：若某条路径（比如撤销到中间态）漏刷，用户在栈的
    中间位置就看不到下一步会撤什么。
    """
    lp = _page()
    undo_btn, redo_btn = _buttons(lp)
    _seed(lp)          # 添加
    _mirror_it(lp)     # 水平镜像
    lp._zorder("top")  # 层序

    seen_undo = [undo_btn.text()]
    seen_redo = [redo_btn.text()]
    states = [(undo_btn.isEnabled(), redo_btn.isEnabled())]
    while lp._undo.canUndo():
        lp._undo.undo()
        seen_undo.append(undo_btn.text())
        seen_redo.append(redo_btn.text())
        states.append((undo_btn.isEnabled(), redo_btn.isEnabled()))

    # 栈深 3 ⇒ 撤销侧逐次应是 层序 → 水平镜像 → 添加 →（空栈）默认值。
    # 逐项比对「这一撤销步会撤掉谁」，而不是只断言「某一处出现过某词」。
    assert seen_undo == [f"{_UNDO_DEFAULT} {_ZORDER}",
                         f"{_UNDO_DEFAULT} {_MIRROR}",
                         f"{_UNDO_DEFAULT} {_ADD}",
                         _UNDO_DEFAULT], f"撤销侧逐次文案不对：{seen_undo}"
    # 重做侧指的是**下一个会被重做的命令** = 刚刚撤掉的那一条，所以它与撤销侧
    # 同序推进（层序 → 水平镜像 → 添加），不是倒着来。
    assert seen_redo == [_REDO_DEFAULT,
                         f"{_REDO_DEFAULT} {_ZORDER}",
                         f"{_REDO_DEFAULT} {_MIRROR}",
                         f"{_REDO_DEFAULT} {_ADD}"], f"重做侧逐次文案不对：{seen_redo}"
    # 可用态在**每一拍**都要对：撤销侧一路可用到最后一拍才禁用；重做侧第一拍
    # （还没撤过任何东西）禁用，从第一次撤销起一路可用。
    assert [u for u, _ in states] == [True, True, True, False], \
        f"撤销按钮可用态逐拍不对：{states}"
    assert [r for _, r in states] == [False, True, True, True], \
        f"重做按钮可用态逐拍不对：{states}"
