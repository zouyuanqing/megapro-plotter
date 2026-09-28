"""B2 回归：排版页 → 作业页的**静默同步**通道 + 页切换同步开关。

缺陷（f2225ec 实测复现）：排版页与作业页之间只有
:attr:`LayoutPage.export_requested` 一条通道，且其 MainWindow 侧 handler
``_on_layout_export`` 会 ``setCurrentIndex(0)`` + 刷控制台 —— 于是「页切换」
（PRD §10.1-8 承诺同步作业预览）**完全没有**触发点：切到第 1 页后
``layout_page.to_job_spec()`` 已是第 1 页几何，而 ``MainWindow._job_spec``
仍握着第 0 页的旧几何、``_job_lines`` 与预览纹丝不动。

修法（``layout_page._maybe_emit_job_sync`` + ``MainWindow._on_layout_job_sync``）：
新增一条**独立于导出按钮**的信号，只做 ``_job_spec`` 赋值 + ``_recompile``，
不切标签页、不打控制台；页切换按一个**默认开**的中文开关决定是否走它。

范围守卫（**C2 已改**）：本文件原先还钉了一条「镜像/移动/增/删**故意不接**这条
通道（产品未拍板）」，由 ``test_layout_edits_do_not_emit_job_sync`` 承担。用户
已拍板**接上**（复用同一个开关、默认开），该用例与文件头注释一并按新契约重写
为 ``test_layout_edits_emit_job_sync_each``（+ 开关关的对照）—— 断言方向反转，
且由「一律不发」**收紧**为「每次编辑各发一次」，少发一次照样红。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# --- 测试脚手架（与 tests/test_gui_window.py 同口径：假 worker + 显式标定） --

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


def _make_window():
    from megapro.gui.main_window import MainWindow

    w = MainWindow()
    w._pen_down_z, w._cut_touch_z, w._safe_z = 17.0, 17.5, 30.0
    w._worker = _StubWorker()
    w._homed = True
    w._link_ready = True
    w._refresh_gate_ui()
    return w


#: 两页内容（页面坐标 mm）：p0 一条贴左下，p1 一条贴右上
_P0 = [[(0.0, 0.0), (10.0, 0.0)]]
_P1 = [[(100.0, 100.0), (140.0, 140.0)]]


def _two_page_layout(w, *, sync_on: bool):
    """造「第 0 页 P0 / 第 1 页 P1」两页文档；造完停在第 0 页、已导出。

    ``sync_on`` 只用于**造页阶段**（避免 ＋ 页/切页顺手同步干扰断言）；真正
    被测的那次切页由用例自己设置开关。
    """
    from megapro.gui.layout.model import Item

    lp = w.layout_page
    lp._sync_job_cb.setChecked(sync_on)
    lp._add_items([Item(paths=[list(p) for p in _P0], name="p0")])
    lp._on_page_add()
    lp._add_items([Item(paths=[list(p) for p in _P1], name="p1")])
    lp._page_bar.setCurrentIndex(0)      # 切回第 0 页
    lp._on_export()                      # 「第 0 页导出」
    lp._sync_job_cb.setChecked(sync_on)
    return lp


def _assert_job_is_page(w, want, what):
    """作业页握的必须是**指定页**的几何：spec / lines / 预览三处一起看。

    只看 ``_job_spec`` 会被「赋了 spec 但没重编译」溜过去，故这里同时断言
    ``_job_lines`` 非空、``_job_compiled`` 已换、且**预览线段的坐标范围**就是
    该页几何的范围（预览 ≡ 将发送的 lines 的回读，构造性单一真源）。
    """
    got = [list(p) for p in w._job_spec.paths_paper]
    assert got == [list(p) for p in want], f"{what}: {got} != {want}"
    assert w._job_lines, f"{what}: 未重编译出 lines"
    assert w._job_compiled is not None and w._job_compiled.segments, \
        f"{what}: 未重编译出 segments"
    draw = w._preview_draw_lines()
    assert draw, f"{what}: 预览无落笔段"
    xs = [x for p0, p1 in draw for x in (p0[0], p1[0])]
    ys = [y for p0, p1 in draw for y in (p0[1], p1[1])]
    wx = [x for p in want for x, _y in p]
    wy = [y for p in want for _x, y in p]
    assert (round(min(xs), 6), round(max(xs), 6)) == (round(min(wx), 6), round(max(wx), 6)), \
        f"{what}: 预览 x 范围 {min(xs)}-{max(xs)} != 该页 {min(wx)}-{max(wx)}"
    assert (round(min(ys), 6), round(max(ys), 6)) == (round(min(wy), 6), round(max(wy), 6)), \
        f"{what}: 预览 y 范围 {min(ys)}-{max(ys)} != 该页 {min(wy)}-{max(wy)}"


# --- 核心回归：开关开/关 ----------------------------------------------------

def test_page_switch_sync_on_updates_job_page():
    """开关开：切到第 1 页 ⇒ 作业页 ``_job_spec``/lines/预览**跟着换页**。

    旧行为下切页无任何触发点：作业页仍握第 0 页几何（见 proof）。
    """
    w = _make_window()
    lp = _two_page_layout(w, sync_on=False)
    _assert_job_is_page(w, _P0, "导出后应持第 0 页")
    lines_p0 = list(w._job_lines)
    lp._sync_job_cb.setChecked(True)

    lp._page_bar.setCurrentIndex(1)     # ← 用户点页签 2

    assert lp.doc.current == 1
    _assert_job_is_page(w, _P1, "切到第 1 页后作业页应持第 1 页")
    assert w._job_lines != lines_p0, "lines 未随页切换重编译"
    assert any("X100 Y100" in line for line in w._job_lines), \
        f"lines 里没有第 1 页的坐标：{w._job_lines}"
    w.close()


def test_page_switch_sync_off_keeps_job_page():
    """开关关：切页**不**动作业页（退回「只有点送去作业才刷新」的老行为）。"""
    w = _make_window()
    lp = _two_page_layout(w, sync_on=False)
    _assert_job_is_page(w, _P0, "导出后应持第 0 页")
    lines_p0 = list(w._job_lines)
    lp._sync_job_cb.setChecked(False)

    lp._page_bar.setCurrentIndex(1)

    assert lp.doc.current == 1, "画布仍应切到第 1 页（只是不同步作业页）"
    _assert_job_is_page(w, _P0, "开关关时作业页应保持第 0 页")
    assert w._job_lines == lines_p0, "开关关时不该重编译"
    w.close()


# --- 新通道必须「安静」且独立于导出按钮 -------------------------------------

def test_sync_channel_does_not_switch_tab_or_log():
    """新通道**不切标签页、不打控制台**（与 ``_on_layout_export`` 的唯一差别）。

    页切换是高频动作；复用 export handler 会把用户踢回控制页并每切一页刷
    一行。这里断言「作业页确实更新了，但标签页没动、控制台一行没多」。
    """
    w = _make_window()
    lp = _two_page_layout(w, sync_on=False)
    tab_before = w.tabs.currentIndex()
    console_before = w.console.toPlainText()
    lp._sync_job_cb.setChecked(True)

    lp._page_bar.setCurrentIndex(1)

    _assert_job_is_page(w, _P1, "作业页应已更新")
    assert w.tabs.currentIndex() == tab_before, "同步通道不得切标签页"
    assert w.console.toPlainText() == console_before, "同步通道不得刷控制台"
    w.close()


def test_export_handler_still_switches_tab_and_logs():
    """对照组：导出通道的既有行为**未变**（仍切页 + 仍打控制台）。

    与上一条并置，就钉死了「新通道是新增，不是把 export handler 改了」。
    """
    w = _make_window()
    lp = w.layout_page
    from megapro.gui.layout.model import Item

    lp._add_items([Item(paths=[list(p) for p in _P0], name="p0")])
    w.tabs.setCurrentIndex(1)            # 先离开控制页
    console_before = w.console.toPlainText()

    lp._on_export()

    assert w.tabs.currentIndex() == 0, "导出仍应切回控制/作业页"
    assert "排版已送去作业页" in w.console.toPlainText()[len(console_before):]
    w.close()


# --- 开关的形态（位置 / 默认 / 中文文案） -----------------------------------

def test_sync_switch_is_in_page_bar_checked_by_default():
    """开关在**页签行**里、默认**勾选**、中文文案（PRD 本就要同步）。"""
    from PySide6 import QtWidgets

    w = _make_window()
    lp = w.layout_page
    cb = lp._sync_job_cb
    assert cb is not None, "页签行应有同步开关"
    assert cb.isChecked(), "默认应开（PRD §10.1-8；做成可选是让步）"
    assert cb.text() == "同步作业预览"
    # 与页签 / ＋ － 复制 ◀ ▶ 同一行（同一父控件 = 同一行）
    row = cb.parentWidget()
    assert row is lp._page_bar.parentWidget(), "开关须与页签同属一行"
    texts = {b.text() for b in row.findChildren(QtWidgets.QPushButton)}
    assert {"＋", "－", "复制", "◀", "▶"} <= texts, f"页签行缺按钮：{texts}"
    w.close()


# --- 新通道复用既有 _recompile 语义（触发点语义未改） -----------------------

def test_sync_channel_goes_through_existing_recompile(monkeypatch):
    """新通道走**既有** ``_recompile``（不是另开的编译路径），且不打控制台。

    两条可观察后果：
    ① 执行中 ``_job_running=True`` ⇒ ``_recompile`` 的既有 no-op 语义对它
       一样生效（只提示、不改 lines）；
    ② 非执行期同步后的 lines，与「手工赋同一份 spec + 调 ``_recompile``」
       逐行相同 —— 证明它没有绕过参数投影/单一真源。
    """
    w = _make_window()
    lp = _two_page_layout(w, sync_on=False)
    lp._sync_job_cb.setChecked(True)

    # ① 执行中：既有 no-op 语义
    lp._page_bar.setCurrentIndex(1)
    _assert_job_is_page(w, _P1, "先正常同步到第 1 页")
    lines_p1 = list(w._job_lines)
    console_before = w.console.toPlainText()
    w._job_running = True
    lp._page_bar.setCurrentIndex(0)      # 执行中切页
    assert w._job_lines == lines_p1, "执行中不得重编译（既有 no-op 语义）"
    assert "执行中" in w.console.toPlainText()[len(console_before):]
    w._job_running = False

    # ② 与手工路径逐行相同
    lp._page_bar.setCurrentIndex(1)
    synced = list(w._job_lines)
    w._job_spec = lp.to_job_spec()
    w._recompile()
    assert w._job_lines == synced, "新通道的编译结果应与既有 _recompile 路径一致"
    w.close()


def test_param_change_still_recompiles_after_sync():
    """同步之后，既有触发点（作业页改 feed）照常重编译 —— 语义未被新通道顶掉。"""
    w = _make_window()
    lp = _two_page_layout(w, sync_on=False)
    lp._sync_job_cb.setChecked(True)
    lp._page_bar.setCurrentIndex(1)
    _assert_job_is_page(w, _P1, "先同步到第 1 页")

    w.feed_xy_spin.setValue(600.0)       # 既有触发点：改速度

    assert any("F600" in line for line in w._job_lines)
    assert [list(p) for p in w._job_spec.paths_paper] == _P1, "几何不因改参数而变"
    w.close()


# --- C2：编辑类动作**接上**这条通道（产品已拍板，替代原范围守卫） ------------

def _emit_counter(lp):
    seen: list = []
    lp.job_sync_requested.connect(lambda spec: seen.append(spec))
    return seen


def test_layout_edits_emit_job_sync_each():
    """镜像/移动/增/删**各自**发一次同步（逐次断言，不是「总数 > 0」）。

    替代原 ``test_layout_edits_do_not_emit_job_sync``（范围守卫「故意不接」）。
    那条守卫编码的是「产品未拍板」，而用户已拍板**接上**：用户在排版页改完、切到
    作业页会看到陈旧几何并可能直接执行。断言方向随之反转，且由「一律不发」**收紧**
    为「每次编辑各发一次」—— 少发一次照样红。

    另钉两条：每次发的 spec 是**当时**的版面几何（不是首帧的旧 spec），以及页切换
    仍照发（对照，证明信号本身是通的）。
    """
    from megapro.gui.canvas.undo_cmds import MoveItemsCommand
    from megapro.gui.layout.model import Item, flatten_visible

    w = _make_window()
    lp = w.layout_page
    seen = _emit_counter(lp)

    def step(fn, what):
        n = len(seen)
        fn()
        assert len(seen) == n + 1, \
            f"{what} 应各发一次同步，实际发了 {len(seen) - n} 次"
        # 带上的是**这次编辑之后**的版面几何
        assert seen[-1].paths_paper == flatten_visible(lp.doc), \
            f"{what} 同步的 JobSpec 与当时版面不一致"

    step(lambda: lp._add_items([Item(paths=[list(p) for p in _P0], name="p0")]),
         "新增图元")
    it = lp.doc.items[0]
    lp._gi_for(it).setSelected(True)

    step(lambda: lp._toggle_mirror("h"), "镜像")
    step(lambda: lp._undo.push(MoveItemsCommand(lp, [(it, it.pos, (5.0, 7.0))])),
         "移动")
    step(lambda: lp._add_items([Item(paths=[[(1.0, 1.0), (2.0, 2.0)]], name="x")]),
         "再新增")

    lp._gi_for(it).setSelected(True)          # 删除目标显式勾上，不靠上一个动作
    step(lp._delete_selected, "删除")

    # 对照：页切换仍要发
    n = len(seen)
    lp._on_page_add()
    assert len(seen) == n + 1, "页切换仍应发一次同步"
    w.close()


def test_layout_edits_do_not_emit_job_sync_when_switch_off():
    """开关**关** ⇒ 同样四类编辑一律不发（开关语义覆盖编辑，不只管页切换）。

    与 :meth:`test_layout_edits_emit_job_sync_each` 成对：开关开时四类编辑都发，
    关时都不发 —— 若只把开关接到页切换那条路上，编辑会无视开关继续发，本条即红。
    """
    from megapro.gui.canvas.undo_cmds import MoveItemsCommand
    from megapro.gui.layout.model import Item

    w = _make_window()
    lp = w.layout_page
    lp._sync_job_cb.setChecked(False)
    seen = _emit_counter(lp)

    lp._add_items([Item(paths=[list(p) for p in _P0], name="p0")])
    it = lp.doc.items[0]
    lp._gi_for(it).setSelected(True)
    lp._toggle_mirror("h")                                   # 镜像
    lp._undo.push(MoveItemsCommand(lp, [(it, it.pos, (5.0, 7.0))]))  # 移动
    lp._add_items([Item(paths=[[(1.0, 1.0), (2.0, 2.0)]], name="x")])
    lp._gi_for(it).setSelected(True)
    lp._delete_selected()
    assert seen == [], f"开关关时编辑仍发了同步，实际 {len(seen)} 次"

    # 开关关时页切换同样不发（同一口径）
    lp._on_page_add()
    assert seen == [], f"开关关时页切换仍发了同步，实际 {len(seen)} 次"
    w.close()
