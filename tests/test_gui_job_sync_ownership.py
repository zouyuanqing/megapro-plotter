"""B2 修复回归：静默同步**不得无声顶掉**用户已载入的作业。

缺陷（本轮实测复现，非读码推断）：``MainWindow._on_layout_job_sync`` 无条件
``self._job_spec = spec``，不检查作业页当前握的是什么。实测（真 MainWindow，
作业页握 ``source_name='design.svg'`` 的文件作业，排版页仅 1 个图元，触发真实
``_on_page_tab_changed``）：

    BEFORE: source='design.svg' paths=1 lines=9
    AFTER : source='排版版面'  paths=0 lines=4
    console unchanged (silent): True
    job still runnable?: True

即用户在作业页载入的文件作业被排版页切页**无声**顶掉：无提示、无撤销，且下一
刀会真的切另一份内容。同一条 probe 在 7283a9d^ 跑，前后都是 design.svg ⇒ 本
缺陷由 B2 新增的静默通道引入。

**执行期更糟**（另一条触发路径，且是**独立**缺陷）：赋值发生在 ``_recompile``
的执行期守卫**之前**（main_window.py 的 ``_recompile`` 首行才是守卫），于是
「内存唯一源」被换而 ``_job_lines``/预览不变，``_on_job_done`` 的补编译遂编译
**切过去那一页**。实跑（文件作业 ``[[(150,150),(200,150)]]``、排版两页 0..10 /
100..140，执行中切到第 1 页）：

    A1 _job_spec.source_name = 排版版面        ← 已被顶掉
    A1 _job_lines 重编译? False                ← 但 lines 还是跑的那个
    A2 lines含X200(刚跑完的文件作业) = False    ← 跑完的作业从作业页消失
    A2 lines含X140(切过去的页1)      = True

而旧 handler 的 docstring 自称「执行中仍 no-op + 提示」—— 与实测相反，属
「契约陈述失真」，与本缺陷同一类，故本文件同时钉住行为与陈述。

修法（``main_window.py``）：``_job_from_layout`` 来源门禁 + 执行中门禁**都放在
``_job_spec`` 赋值之前**；来源判据用布尔量而**不**用 ``source_name ==
"排版版面"``（那是显示名，且载入对话框带「所有文件 (*)」，一个无扩展名、恰好
叫「排版版面」的文件就会撞名）。

覆盖面说明：``tests/test_gui_layout_job_sync.py`` 的每个用例都先
``lp._on_export()``（即**本来就打算**让作业页握排版页），「作业页握的是外部
文件作业」这个状态从未被构造过；且其执行期用例只断 ``_job_lines`` 未变 +
控制台出现「执行中」，**从不断 ``_job_spec``**。本文件补的就是这两个洞。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# --- 脚手架（与 tests/test_gui_layout_job_sync.py 同口径） -------------------

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


#: 排版第 0 页 / 第 1 页几何（页面 mm）。与文件作业的 (150,60)-(200,60) 互不重叠，
#: 三者任何混淆都能被断言抓住。
_P0 = [[(0.0, 0.0), (10.0, 0.0)]]
_P1 = [[(100.0, 100.0), (140.0, 140.0)]]
#: 文件作业载入后的**纸面**几何（SVG y-down 的 y=150 经 paper y-up 翻转 = 60）
_FILE_PATHS = [[(150.0, 60.0), (200.0, 60.0)]]

_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="210" height="210" \
viewBox="0 0 210 210">
  <polyline points="150,150 200,150" fill="none" stroke="#000000" \
stroke-width="1"/>
</svg>
"""


def _make_window():
    from megapro.gui.main_window import MainWindow

    w = MainWindow()
    w._pen_down_z, w._cut_touch_z, w._safe_z = 17.0, 17.5, 30.0
    w._worker = _StubWorker()
    w._homed = True
    w._link_ready = True
    w._refresh_gate_ui()
    return w


def _two_page_layout(w, *, sync_on: bool = False):
    """造「第 0 页 P0 / 第 1 页 P1」两页文档，造完停在第 0 页、已导出。

    ``_on_export`` 是**真实导出通道** ⇒ 经 ``_on_layout_export`` 把来源标记
    置为排版。本用例组里需要「作业页握排版作业」时用它。
    """
    from megapro.gui.layout.model import Item

    lp = w.layout_page
    lp._sync_job_cb.setChecked(sync_on)
    lp._add_items([Item(paths=[list(p) for p in _P0], name="p0")])
    lp._on_page_add()
    lp._add_items([Item(paths=[list(p) for p in _P1], name="p1")])
    lp._page_bar.setCurrentIndex(0)
    lp._on_export()                      # 真实导出 ⇒ 作业页握排版作业
    lp._sync_job_cb.setChecked(sync_on)
    return lp


def _load_file_job(w, tmp_path, monkeypatch):
    """走**真实** ``_on_load_svg`` 载入一个 SVG 文件作业（不经手工赋 _job_spec）。

    手工赋 ``w._job_spec = JobSpec(...)`` 不会经过任何来源标记代码，能绕过我
    正在修的门禁；故这里必须走生产入口。
    """
    from PySide6 import QtWidgets

    p = tmp_path / "design.svg"
    p.write_text(_SVG, encoding="utf-8")
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(p), "")))
    w._on_load_svg()
    assert w._job_spec is not None and w._job_lines, "文件作业未载入成功"
    return w._job_spec.source_name


def _paths(w):
    return [list(p) for p in w._job_spec.paths_paper]


def _assert_job_geometry(w, want, what: str) -> None:
    """作业页握的必须是**指定几何**：spec + lines + 预览三处一起看。

    只看 ``_job_spec`` 会被「赋了 spec 但没重编译」溜过去；只看 lines 又看不见
    内存唯一源被换掉。预览走 ``_preview_draw_lines``（预览 ≡ 将发送的 lines 的
    回读），故三处同源可比。
    """
    got = _paths(w)
    assert got == [list(p) for p in want], f"{what}: spec 几何 {got} != {want}"
    assert w._job_lines, f"{what}: 无 lines"
    assert w._job_compiled is not None and w._job_compiled.segments, \
        f"{what}: 未重编译出 segments"
    draw = w._preview_draw_lines()
    assert draw, f"{what}: 预览无落笔段"
    xs = [x for p0, p1 in draw for x in (p0[0], p1[0])]
    ys = [y for p0, p1 in draw for y in (p0[1], p1[1])]
    wx = [x for p in want for x, _y in p]
    wy = [y for p in want for _x, y in p]
    assert (round(min(xs), 6), round(max(xs), 6)) == \
        (round(min(wx), 6), round(max(wx), 6)), \
        f"{what}: 预览 x 范围 {min(xs)}-{max(xs)} != {min(wx)}-{max(wx)}"
    assert (round(min(ys), 6), round(max(ys), 6)) == \
        (round(min(wy), 6), round(max(wy), 6)), \
        f"{what}: 预览 y 范围 {min(ys)}-{max(ys)} != {min(wy)}-{max(wy)}"


# --- 情形 1（核心）：作业页握**文件作业** + 开关开 → 切页 → 文件作业不变 ----

def test_page_switch_does_not_hijack_file_job(tmp_path, monkeypatch):
    """文件作业 + 开关开 + 切页 ⇒ ``_job_spec``/lines/预览/控制台**全部不变**。

    本条缺陷的核心。旧行为下作业页被换成排版页（paths 1→0、lines 9→4）且控制
    台一声不吭 —— 用户无从察觉，下一刀就切了另一份内容。
    """
    w = _make_window()
    lp = _two_page_layout(w, sync_on=False)
    name = _load_file_job(w, tmp_path, monkeypatch)

    _assert_job_geometry(w, _FILE_PATHS, "载入后应持文件作业")
    before_lines = list(w._job_lines)
    before_console = w.console.toPlainText()

    lp._sync_job_cb.setChecked(True)      # 开关开 —— 用户明确要同步
    lp._page_bar.setCurrentIndex(1)       # ← 用户点页签 2

    assert lp.doc.current == 1, "排版页确实切了页（否则本用例是空跑）"
    # ↓ 行为断言在前：即便将来换一种来源标记实现，「顶掉文件作业」也必红。
    assert w._job_spec.source_name == name, \
        f"文件作业被顶掉：source_name={w._job_spec.source_name!r} != {name!r}"
    _assert_job_geometry(w, _FILE_PATHS, "切页后作业页仍应是文件作业")
    assert list(w._job_lines) == before_lines, "切页重编译了用户的文件作业"
    assert w.console.toPlainText() == before_console, \
        "静默通道不该为「顶掉别人作业」刷控制台"
    # 来源标记本身（机制层，放最后：行为断言已独立证明过「顶掉」这件事）
    assert w._job_from_layout is False, \
        "载入文件作业、以及静默同步之后，来源标记都必须是 False"
    w.close()


def test_file_job_survives_repeated_page_switches(tmp_path, monkeypatch):
    """反复切页（含切回）也不许顶掉文件作业 —— 钉「每次切换都判一次来源」。"""
    w = _make_window()
    lp = _two_page_layout(w, sync_on=False)
    _load_file_job(w, tmp_path, monkeypatch)
    lp._sync_job_cb.setChecked(True)

    for idx in (1, 0, 1, 0):
        lp._page_bar.setCurrentIndex(idx)
        _assert_job_geometry(w, _FILE_PATHS, f"切到第 {idx} 页后")
    w.close()


def test_file_job_named_like_a_layout_job_is_still_not_hijacked(tmp_path,
                                                               monkeypatch):
    """**撞名**的用户文件作业同样不被接管 —— 钉住「为什么不用 source_name 判据」。

    这是本设计的关键一格：来源判据若写成 ``source_name == "排版版面"``，那么
    一个**无扩展名、恰好叫「排版版面」**的文件就会与排版作业撞名。而这不是假想
    —— 载入对话框带「所有文件 (*)」，该文件**能被正常解析载入**（实测：
    ``polylines_for_svg`` 对无扩展名文件照常返回路径，``_on_load_svg`` 给它
    ``source_name='排版版面'``，与 :meth:`LayoutPage.to_job_spec` 的字面量
    **完全相同**）。那种实现下，用户这个文件作业会被静默同步无声接管。

    故来源判据必须是只在「谁把它放进作业页」时置位的 :attr:`_job_from_layout`
    布尔量 —— 构造上就没有这个洞。本用例即那条设计决定的**可执行**依据：把
    门禁 1 改回 ``source_name`` 判据，本用例即红（其余用例照绿：它们的文件名
    叫 design.svg，不撞名）。
    """
    from PySide6 import QtWidgets

    w = _make_window()
    lp = _two_page_layout(w, sync_on=False)

    p = tmp_path / "排版版面"          # 无扩展名 ⇒ source_name 恰为「排版版面」
    p.write_text(_SVG, encoding="utf-8")
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(p), "")))
    w._on_load_svg()

    assert w._job_spec.source_name == "排版版面", \
        f"本用例的前提不成立：{w._job_spec.source_name!r}"
    assert w._job_from_layout is False, "撞名的**文件**作业仍不该被标成排版来源"
    _assert_job_geometry(w, _FILE_PATHS, "撞名文件作业载入后")

    lp._sync_job_cb.setChecked(True)
    lp._page_bar.setCurrentIndex(1)

    _assert_job_geometry(w, _FILE_PATHS, "撞名文件作业在切页后也不被接管")
    assert w._job_spec.source_name == "排版版面"
    w.close()


# --- 情形 2：作业页握**排版页作业** + 开关开 → 切页 → 随之更新 --------------

def test_page_switch_still_syncs_layout_job():
    """排版作业 + 开关开 + 切页 ⇒ 作业页**照旧**跟着换页（B2 的原意不能丢）。"""
    w = _make_window()
    lp = _two_page_layout(w, sync_on=False)
    _assert_job_geometry(w, _P0, "导出后应持第 0 页")
    lines_p0 = list(w._job_lines)

    lp._sync_job_cb.setChecked(True)
    lp._page_bar.setCurrentIndex(1)

    _assert_job_geometry(w, _P1, "切到第 1 页后应持第 1 页")
    assert list(w._job_lines) != lines_p0, "lines 未随页切换重编译"
    assert w._job_from_layout is True, "导出后应标为排版来源"
    w.close()


# --- 情形 3：文件作业 + 开关**关** → 切页 → 不变 ----------------------------

def test_file_job_untouched_when_sync_switch_off(tmp_path, monkeypatch):
    """文件作业 + 开关关 + 切页 ⇒ 不变（老行为，且不得因开关关而误伤）。"""
    w = _make_window()
    lp = _two_page_layout(w, sync_on=False)
    _load_file_job(w, tmp_path, monkeypatch)
    before_lines = list(w._job_lines)
    before_console = w.console.toPlainText()

    lp._sync_job_cb.setChecked(False)
    lp._page_bar.setCurrentIndex(1)

    _assert_job_geometry(w, _FILE_PATHS, "开关关时切页不应动作业页")
    assert list(w._job_lines) == before_lines
    assert w.console.toPlainText() == before_console
    w.close()


# --- 情形 4：执行中切页 → _job_spec 不变，且 jobDone 补编译不得用错 spec ----

def test_switch_during_run_keeps_spec_and_jobdone_recompiles_same():
    """执行中切页：``_job_spec`` **不得**被换，且补编译编译的仍是**本次跑的那份**。

    这是**独立于来源门禁**的一条：作业页握的就是排版作业（来源门禁放行），旧
    行为下 ``_job_spec = spec`` 发生在 ``_recompile`` 执行期守卫**之前** ⇒ 内
    存唯一源被换而 lines 不变，``_on_job_done`` 的补编译遂编译切过去那一页，刚
    跑完的作业从作业页彻底消失（实测：跑完后 lines 里 X200 消失、X140 出现）。
    """
    w = _make_window()
    lp = _two_page_layout(w, sync_on=False)
    _assert_job_geometry(w, _P0, "执行前应持第 0 页")
    spec_before = _paths(w)
    lines_before = list(w._job_lines)
    console_before = w.console.toPlainText()

    lp._sync_job_cb.setChecked(True)
    w._job_running = True                  # 执行中
    lp._page_bar.setCurrentIndex(1)        # ← 执行中切页

    assert _paths(w) == spec_before, \
        f"执行中 _job_spec 被换：{_paths(w)} != {spec_before}"
    assert list(w._job_lines) == lines_before, "执行中不得重编译"

    # 跑完：补编译必须编译**同一份** spec，而不是切过去那一页
    w._job_running = False
    w._on_job_done(True)

    _assert_job_geometry(w, _P0, "jobDone 补编译后应仍是第 0 页")
    assert not any("X100" in line for line in w._job_lines), \
        f"补编译把作业换成了切过去的第 1 页：{w._job_lines}"
    assert "执行中" in w.console.toPlainText()[len(console_before):], \
        "执行中切页应沿用参数改动那条路的提示（不另发明措辞）"
    w.close()


def test_switch_during_run_with_file_job_is_silent_and_inert(tmp_path, monkeypatch):
    """文件作业 + 执行中切页 ⇒ 不动 ``_job_spec``，且**不**误报「执行中」。

    两道门禁的次序：来源门禁在前。文件作业根本不归排版同步管，故走的是**静默
    早退**（保持本通道「不刷控制台」的契约），而不是执行中提示 —— 否则每次切页
    都会给用户一条与本次作业毫无关系的提示。
    """
    w = _make_window()
    lp = _two_page_layout(w, sync_on=False)
    _load_file_job(w, tmp_path, monkeypatch)
    _assert_job_geometry(w, _FILE_PATHS, "执行前应持文件作业")
    console_before = w.console.toPlainText()

    lp._sync_job_cb.setChecked(True)
    w._job_running = True
    lp._page_bar.setCurrentIndex(1)

    assert _paths(w) == [list(p) for p in _FILE_PATHS], "执行中文件作业被顶掉"
    assert w.console.toPlainText() == console_before, \
        "非排版作业的切页不该发执行中提示（静默早退）"

    w._job_running = False
    w._on_job_done(True)
    _assert_job_geometry(w, _FILE_PATHS, "jobDone 后应仍是文件作业")
    w.close()
