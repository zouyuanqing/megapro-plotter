"""R8 回归：编译失败时**不得留下半提交的作业**（``_job_spec`` 与 lines 不同源）。

缺陷（R8 对 C2 的对抗性复核，复现于本仓库）：四条「换作业」路径都是「先
``self._job_spec = spec``、再 :meth:`MainWindow._recompile`」，而
:meth:`_recompile` 在参数非法时只打一行控制台就 return —— 切刀且
``touch_z < depth`` ⇒ :func:`cut_z_for_depth` 抛 ValueError（下压后 Z 为负）。
于是**内存唯一源已经是新几何，``_job_lines`` / 预览 / run 门禁却停在旧几何**。

实测（真「送去作业」基线 + 切刀非法切深 + 排版页加一条线）::

    控制台末行  '编译失败（参数）：下压后 Z 为负（touch_z=2.0, depth=5.0）：Z 永不为负'
    spec        2 段（含新增的 (50,50)-(150,50)）        ← 新
    lines       10 行旧几何（只有 X0/X10，无 X150）      ← 旧
    run 门禁    enabled=True，_job_compiled.runnable=True
    worker 实收  那 10 行旧版面                            ← 机器切旧版面

归属：赋值顺序是旧代码（``main_window.py`` 自 0f160c3 起零 diff），但 C2 把这条
通道从「切一次页签」扩到「每按一次键」，把潜伏窗口变成了常开窗口。

**修法**：新增 :meth:`MainWindow._set_job_and_recompile`，把「赋 spec +
重编译」做成原子操作——编译不成（含执行中 no-op）就把 spec **与来源标记一起**
还原；:meth:`_recompile` 多返回一个「是否真出了新 lines」的布尔值，**触发集与
成功路径一字未改**。

⚠ 触发条件确实苛刻（``cut_depth_spin`` range 上限 5.0，真机 ``touch_z``≈17.5，
正常标定打不出来），控制台也**不是**全静默（有那一行提示）。但「作业页看着
正常、机器收到旧版面」仍是静默地做错的那一类，故按契约钉住。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets                            # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.layout.model import Item                 # noqa: E402
from megapro.gui.main_window import MainWindow            # noqa: E402


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


# 一条在 (150,60)-(200,60) 的文件作业（坐标系与作业页测试同口径）
_FILE_PATHS = [[(150.0, 60.0), (200.0, 60.0)]]
_FILE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="210" height="210" \
viewBox="0 0 210 210">
  <polyline points="150,150 200,150" fill="none" stroke="#000000" \
stroke-width="1"/>
</svg>
"""


def _window():
    w = MainWindow()
    w._pen_down_z, w._cut_touch_z, w._safe_z = 17.0, 17.5, 30.0
    w._worker = _StubWorker()
    w._homed = True
    w._link_ready = True
    w._refresh_gate_ui()
    return w


def _rect(x0, y0, w_, h_, name):
    return Item(paths=[[(x0, y0), (x0 + w_, y0), (x0 + w_, y0 + h_),
                        (x0, y0 + h_), (x0, y0)]], name=name)


def _layout_job_window():
    """作业页握一份**编译成功**的排版作业（基线），静默同步开着。"""
    w = _window()
    lp = w.layout_page
    lp._sync_job_cb.setChecked(False)
    lp._add_items([_rect(0, 0, 10, 10, "a")])
    lp._on_export()
    assert w._job_from_layout is True, "前置：作业页应握排版作业"
    assert w._job_compiled.runnable is True, "前置：基线应可执行"
    lp._sync_job_cb.setChecked(True)
    return w, lp


def _break_compilation(w):
    """把参数改成**编译期必然失败**：切刀 + touch_z(2.0) < 切深(5.0) ⇒ 负 Z。"""
    w.tool_combo.setCurrentIndex(w.tool_combo.findData("knife"))
    w._cut_touch_z = 2.0
    w.cut_depth_spin.setValue(5.0)


def _x_coords(lines):
    return {t for ln in lines for t in ln.split() if t.startswith("X")}


# -- ① 主契约：编译失败不得留下半提交的作业 ---------------------------------

def test_failed_recompile_keeps_spec_and_lines_same_source():
    """编译失败 ⇒ ``_job_spec`` 必须**回滚**到与 lines 同一份的那版。

    修复前实测：spec 变 2 段（含新增线）而 lines 仍是旧的 10 行。
    """
    w, lp = _layout_job_window()
    spec_before = [list(p) for p in w._job_spec.paths_paper]
    lines_before = list(w._job_lines)

    _break_compilation(w)
    lp._add_items([_rect(50, 50, 100, 100, "b")])   # 编辑 → 撤销栈 → 静默同步

    assert "编译失败" in w.console.toPlainText(), \
        "前置：本用例依赖编译失败，控制台应有提示"
    assert [list(p) for p in w._job_spec.paths_paper] == spec_before, (
        f"编译失败后 spec 仍被换成了新版面："
        f"{[list(p) for p in w._job_spec.paths_paper]}")
    assert w._job_lines == lines_before, "失败路径本就应保持上一份 lines"


def test_machine_never_receives_geometry_the_job_page_no_longer_describes():
    """**机器后果**：编译失败后 worker 收到的内容必须与作业页此刻描述的一致。

    这条直接对「静默切旧版面」取证：``_on_run_job`` 发送的是
    ``_job_compiled.lines``，所以作业页的 spec 与它必须同源。
    """
    w, lp = _layout_job_window()
    _break_compilation(w)
    lp._add_items([_rect(50, 50, 100, 100, "b")])

    emitted = list(w._job_compiled.lines)
    assert emitted == w._job_lines, "compiled.lines 与 _job_lines 应同源"
    assert not any("X150" in ln for ln in emitted), \
        f"发往机器的 G-code 里出现了作业页已回滚掉的几何：{emitted}"
    # 作业页此刻描述的几何必须真的能在 lines 里找到
    xs = {round(x, 6) for p in w._job_spec.paths_paper for x, _ in p}
    assert xs <= {float(t[1:]) for t in _x_coords(emitted)}, \
        f"spec 里的 x={sorted(xs)} 没在 lines 里出现（不同源）"


def test_run_gate_state_matches_the_committed_job():
    """run 门禁必须与**当前提交的作业**一致，不能停在别的一版上。

    修复前：lines 是旧的、runnable 却是旧作业的真值，run 仍 enabled ⇒ 窗口内
    照样能点执行（只是发旧版面）。本例盯住「门禁与 spec 同源」这一条。
    """
    w, lp = _layout_job_window()
    _break_compilation(w)
    lp._add_items([_rect(50, 50, 100, 100, "b")])

    assert w._job_compiled.runnable is True, "基线作业本身是可执行的"
    assert w.run_btn.isEnabled() is True
    # 门禁为真而 spec 已回滚 ⇒ 允许执行的是**回滚后那一份**，不是新版面
    xs = {round(x, 6) for p in w._job_spec.paths_paper for x, _ in p}
    assert 150.0 not in xs, "回滚后 spec 不该含新图元的坐标"


# -- ② 窗口可自愈：修好参数后仍能同步到新几何 -------------------------------

def test_pending_layout_edit_lands_after_params_become_valid():
    """参数改回合法后作业页恢复自洽，且**下一次排版页编辑**把待同步几何带进来。

    ⚠ 这里钉的是一个**有取舍**的行为，写明白免得下一个人以为是漏网：

    回滚方案把「编译失败时的新几何」一起撤掉了，所以修好参数后的那次重编译
    吃的是**回滚后**那份 spec —— 待同步的那次编辑不会自己冒出来，要等下一次
    排版页交互。这是刻意的：宁可让作业页多落后一次交互，也**不**让作业页
    处于「spec 是新的、lines 是旧的」那种能发旧版面的状态。画布始终保留用户的
    编辑（没丢），下一次编辑即重新同步。

    （另一种设计是失败时禁用 run 门禁、保留待同步 spec，自愈更早；但那会让
    作业页的 spec 与 lines 继续不同源，与本文件的主契约冲突。）
    """
    w, lp = _layout_job_window()
    _break_compilation(w)
    lp._add_items([_rect(50, 50, 100, 100, "b")])
    assert not any("X150" in ln for ln in w._job_lines), "前置：此刻应仍是旧几何"

    # ① 参数修回合法：作业页恢复自洽且可执行（不卡死在失败态）
    w._cut_touch_z = 17.5
    w.feed_xy_spin.setValue(600.0)
    assert w._job_compiled.runnable is True, "参数合法后应恢复可执行"
    spec_xs = {round(x, 6) for p in w._job_spec.paths_paper for x, _ in p}
    line_xs = {float(t[1:]) for t in _x_coords(w._job_lines)}
    assert spec_xs <= line_xs, "恢复后 spec 与 lines 必须同源"

    # ② 下一次排版页编辑把画布上的新几何带进来（画布从未丢失用户的编辑）
    assert 150.0 in {round(x, 6) for p in lp.to_job_spec().paths_paper
                     for x, _ in p}, "前置：画布上应当仍看得到那条新线"
    lp._add_items([_rect(120, 20, 10, 10, "c")])
    assert any("X150" in ln for ln in w._job_lines), \
        f"下一次排版页编辑后新几何仍未进 lines：{sorted(_x_coords(w._job_lines))}"


# -- ③ 来源标记必须与 spec 一起回滚（否则会顶掉用户的文件作业） ---------------

def test_file_job_is_not_hijacked_when_a_layout_export_fails_to_compile(tmp_path,
                                                                       monkeypatch):
    """用户自己的**文件作业**遇到编译失败的排版导出 ⇒ 作业与来源标记都得保住。

    这条防的是修复里的连带风险：若只回滚 ``_job_spec`` 而留下
    ``_job_from_layout=True``，那么下一次静默同步就会把用户的文件作业顶掉
    （来源门禁就靠这个标记）。故来源标记必须与 spec **一起**还原。
    """
    w = _window()
    lp = w.layout_page

    p = tmp_path / "design.svg"
    p.write_text(_FILE_SVG, encoding="utf-8")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(p), "")))
    w._on_load_svg()
    assert w._job_from_layout is False, "前置：文件作业的来源标记应为 False"
    file_lines = list(w._job_lines)

    _break_compilation(w)
    lp._sync_job_cb.setChecked(False)
    lp._add_items([_rect(0, 0, 10, 10, "a")])
    lp._on_export()                       # 编译失败 ⇒ 整体回滚

    assert w._job_from_layout is False, \
        "来源标记没跟着回滚 ⇒ 下一次静默同步会顶掉用户的文件作业"
    assert w._job_lines == file_lines, "文件作业的 lines 被动了"
    assert [list(p) for p in w._job_spec.paths_paper] == \
        [list(q) for q in _FILE_PATHS], "作业页的 spec 换成了别的东西"


# -- ④ 不过度修复：正常路径照常提交 -----------------------------------------

def test_successful_recompile_still_commits_the_new_geometry():
    """参数合法时一切照旧：新几何照进 spec 与 lines（别把回滚做成常态）。"""
    w, lp = _layout_job_window()
    before = len(w._job_spec.paths_paper)

    lp._add_items([_rect(50, 50, 100, 100, "b")])

    assert len(w._job_spec.paths_paper) == before + 1, "正常同步未提交新几何"
    assert any("X150" in ln for ln in w._job_lines), "lines 未含新几何"
    assert w._job_compiled.runnable is True


def test_export_while_running_does_not_half_commit():
    """执行中点『送去作业』⇒ 不半提交（执行中门禁早退，也算编译未成）。

    这是同一条原子性要求的另一个早退分支：``_recompile`` 因执行中 no-op 返回
    False ⇒ spec 与来源标记一并回滚，作业页继续显示上一份。
    """
    w, lp = _layout_job_window()
    spec_before = [list(p) for p in w._job_spec.paths_paper]

    w._job_running = True
    lp._add_items([_rect(50, 50, 100, 100, "b")])

    assert [list(p) for p in w._job_spec.paths_paper] == spec_before, \
        "执行中不得留下半提交的新作业"
    assert "执行中" in w.console.toPlainText()
    w._job_running = False
