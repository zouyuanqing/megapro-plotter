"""R6 回归：静默同步**不得**重置用户在作业页选定的 placement。

缺陷（R6 对 C2 的对抗性复核，复现于本仓库）：:meth:`MainWindow._on_layout_job_sync`
照搬了 :meth:`_on_layout_export`（**显式**『送去作业』，重置 placement 合理）的
整条语义，其中 ``self._set_placement_ui(spec.placement.mode)`` 会把 combo
打回 spec 自带的值；而 :meth:`LayoutPage.to_job_spec` **恒**返回
``Placement(mode="preserve")``。

⇒ 用户在作业页亲手选『锚点归位 bl→(0,0)』后，回排版页按**一次**方向键
（最轻的一类编辑），placement 即被无声抹掉，整刀内容平移。实测::

    选锚点后    placement=anchor  落点=[0.0, 100.0]
    一次微调后  placement=preserve  落点=[50.0, 54.0, 150.0, 154.0]   ← 平移 (54, 50) mm
    控制台末行  '排版已送去作业页 …'（与操作前逐字相同，零提示）
    run 门禁    enabled=True，_job_compiled.runnable=True

**这是会动刀的一类**：用户亲手定的放置方式被一次按键改掉，机器照切。

**归属**：C2 把触发面从「切一次排版页签」扩到「排版页每按一次键」，放大了这个
旧口子，但不是它的根因（``main_window.py`` 那行自 0f160c3 起未改）。本文件钉的
是**同步路径不改 placement**；显式『送去作业』路径**仍应**重置它——那是用户
主动换作业，重置是合理的，一起钉住防止我把它改过头。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtCore                                # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

_app = QApplication.instance() or QApplication([])


# -- 脚手架（与 tests/test_gui_layout_job_sync.py 同口径） ------------------

class _Sig:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class _StubWorker:
    def __init__(self):
        self._state = "READY"
        self.reqRunJob = _Sig()
        self.reqSendSequence = _Sig()
        self.reqSendLine = _Sig()


def _window():
    from megapro.gui.main_window import MainWindow

    w = MainWindow()
    w._pen_down_z, w._cut_touch_z, w._safe_z = 17.0, 17.5, 30.0
    w._worker = _StubWorker()
    w._homed = True
    w._link_ready = True
    w._refresh_gate_ui()
    return w


_RECT = [[(50.0, 50.0), (150.0, 50.0), (150.0, 150.0), (50.0, 150.0),
          (50.0, 50.0)]]


def _exported_rect_window():
    """排版页放一个方框并真『送去作业』，随后作业页选上『锚点归位 bl→(0,0)』。"""
    from megapro.gui.layout.model import Item

    w = _window()
    lp = w.layout_page
    lp._sync_job_cb.setChecked(False)
    lp._add_items([Item(paths=[list(p) for p in _RECT], name="rect")])
    lp._gi_for(lp.doc.items[0]).setSelected(True)      # 微调只作用于选中集
    lp._on_export()
    assert w._job_from_layout is True, "前置：作业页应握排版作业"
    lp._sync_job_cb.setChecked(True)                   # 静默同步默认开

    w.placement_combo.setCurrentIndex(w.placement_combo.findData("anchor"))
    assert w._job_spec.placement.mode == "anchor", "前置：未能切到锚点归位"
    return w, lp


def _landing(w):
    """G-code 里的落点集合（机器真正会切到的位置），排序去重。"""
    out = set()
    for ln in w._job_lines:
        for tok in ln.split():
            head, tail = tok[0], tok[1:]
            if head in "XY" and tail.replace(".", "").replace("-", "").isdigit():
                out.add(round(float(tail), 3))
    return sorted(out)


def _expected_landing(lp, placement):
    """**独立**算一遍「当前版面几何 + 用户那个 placement」应当落到哪里。

    刻意不经过 ``_on_layout_job_sync`` / ``w._job_lines``：那正是被本文件证伪
    的通路，拿它当真值就是自指。这里从 :func:`flatten_visible` 取几何、用捕获
    下来的 placement 对象自己编译一份。

    之所以断言「与算出来的一致」而不是「与编辑前相同」：撤销会把图元删掉、
    页切换会换几何，落点本就应当变；不变的是**用户选的 placement 仍被尊重**。
    """
    import dataclasses

    from megapro.gui.job import ZMap, compile_job
    from megapro.gui.layout.model import flatten_visible

    spec = dataclasses.replace(
        lp.to_job_spec(), zmap=ZMap(30.0, 17.0), placement=placement)
    lines = compile_job(spec, dry_run=True).lines
    out = set()
    for ln in lines:
        for tok in ln.split():
            head, tail = tok[0], tok[1:]
            if head in "XY" and tail.replace(".", "").replace("-", "").isdigit():
                out.add(round(float(tail), 3))
    return sorted(out)


# 排版页四类编辑的代表路径（反例逐条实测过：置顶 / Ctrl+Z / 数值改 X / 方向键）
def _edit_nudge(lp, w):
    lp._nudge(QtCore.Qt.Key_Right, False)


def _edit_zorder(lp, w):
    lp._zorder("top")


def _edit_undo(lp, w):
    lp._undo.undo()


def _edit_numeric_x(lp, w):
    lp.sp_x.setValue(80.0)


_EDITS = {"微调(方向键)": _edit_nudge, "层序(置顶)": _edit_zorder,
          "撤销": _edit_undo, "数值改X": _edit_numeric_x}


# -- ① 主契约：编辑不得抹掉用户的 placement ---------------------------------

@pytest.mark.parametrize("edit", sorted(_EDITS))
def test_layout_edit_keeps_user_placement(edit):
    """四类编辑**逐条**：placement 仍是 anchor、combo 文案不变、落点与几何一致。"""
    w, lp = _exported_rect_window()
    place_before = w._job_spec.placement
    combo_before = w.placement_combo.currentText()

    _EDITS[edit](lp, w)

    assert w._job_spec.placement == place_before, (
        f"{edit} 之后 placement 被改回 {w._job_spec.placement}，"
        f"用户在作业页选的选择没了")
    assert w.placement_combo.currentText() == combo_before, \
        f"{edit} 之后 combo 文案被改写：{w.placement_combo.currentText()!r}"
    assert _landing(w) == _expected_landing(lp, place_before), (
        f"{edit} 之后机器落点与「当前几何 + 用户 placement」不符："
        f"{_landing(w)} ≠ {_expected_landing(lp, place_before)}")


def test_edited_job_is_still_runnable_so_the_shift_really_would_reach_the_machine():
    """机器后果：作业仍可执行（run 门禁全绿）——否则这条只是「看着吓人」。

    修复前实测正是这样：``run_btn`` enabled、``runnable=True``，配合
    placement 被抹 ⇒ 偏移后的坐标真的会被执行。
    """
    w, lp = _exported_rect_window()
    place_before = w._job_spec.placement
    landing_before = _landing(w)

    lp._nudge(QtCore.Qt.Key_Right, False)

    assert w.run_btn.isEnabled(), "前置：作业应可执行（否则本例无意义）"
    assert w._job_compiled.runnable is True, "前置：编译产物应 runnable"
    # 锚点归位把内容贴到工件原点，故版面平移**不应**改变机器落点
    assert _landing(w) == landing_before, "落点在编辑后被整体平移"
    assert w._job_spec.placement == place_before


def test_sync_leaves_placement_untouched_even_across_repeated_edits():
    """连续多次编辑也不许漂移（单次通过不代表序列安全）。"""
    w, lp = _exported_rect_window()
    place_before = w._job_spec.placement
    landing_before = _landing(w)

    for _ in range(5):
        lp._nudge(QtCore.Qt.Key_Right, False)

    assert w._job_spec.placement == place_before
    assert _landing(w) == landing_before == _expected_landing(lp, place_before)


# -- ② 不过度修复：显式『送去作业』**仍应**重置 placement -------------------

def test_explicit_export_still_resets_placement():
    """『送去作业』是用户主动换作业 ⇒ 重置 placement 是合理的，不得被我一并改掉。

    与上面的用例并置，就钉死「同步路径不改 / 显式路径照旧」这条分工，而不是
    把 placement 变成谁都动不了的全局状态。
    """
    from megapro.gui.layout.model import Item

    w = _window()
    lp = w.layout_page
    lp._sync_job_cb.setChecked(False)
    lp._add_items([Item(paths=[list(p) for p in _RECT], name="rect")])
    lp._on_export()
    w.placement_combo.setCurrentIndex(w.placement_combo.findData("anchor"))
    assert w._job_spec.placement.mode == "anchor", "前置：未能切到锚点归位"

    lp._add_items([Item(paths=[[(10.0, 10.0), (20.0, 10.0), (20.0, 10.0)]],
                        name="another")])
    lp._on_export()                       # 再点一次『送去作业』

    assert w._job_spec.placement.mode == "preserve", \
        "显式送去作业应把 placement 复位为版面自带值（本次未改这一条）"
    assert w.placement_combo.currentText() == "保持版面坐标"


# -- ③ 与既有两道门禁并存（不因修这个而放松别的） ---------------------------

def test_sync_still_keeps_source_and_running_gates():
    """来源门禁与执行中门禁**仍在**：修 placement 不得顺带放松它们。"""
    from megapro.gui.layout.model import Item

    w = _window()
    lp = w.layout_page
    lp._sync_job_cb.setChecked(True)
    lp._add_items([Item(paths=[list(p) for p in _RECT], name="rect")])
    lp._gi_for(lp.doc.items[0]).setSelected(True)     # 微调只作用于选中集

    # 门禁 1：作业页握的不是排版作业 ⇒ 整个方法早退，一个字段都不碰
    spec_before = w._job_spec
    lp._nudge(QtCore.Qt.Key_Right, False)
    assert w._job_spec is spec_before, "作业页无排版作业时不应被同步改写"

    # 门禁 2：执行中 ⇒ 只提示、且不换 _job_spec
    lp._on_export()
    assert w._job_from_layout is True
    before = w._job_spec
    console_before = w.console.toPlainText()
    w._job_running = True
    lp._nudge(QtCore.Qt.Key_Right, False)
    assert w._job_spec is before, "执行中不得换 _job_spec（补编译会拿它去编译）"
    assert "执行中" in w.console.toPlainText()[len(console_before):]
