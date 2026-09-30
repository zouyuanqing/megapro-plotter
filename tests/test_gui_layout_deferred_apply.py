"""D3 回归：执行中的参数改动**不得**被原子包装当成「编译失败」而回滚。

缺陷（上一轮 R8 原子包装的配套缺口）：``_set_job_and_recompile`` 把
:meth:`_recompile` 的返回值当成败，False 就把刚赋的 spec 连同来源标记一起
还原。但 ``_recompile`` 返回 False 有**两种完全不同的含义**：

(a) **编译真失败**（参数非法，如切刀且 ``touch_z < depth`` ⇒ 负 Z）⇒ 回滚对；
(b) **作业正在执行**（``_recompile`` 首行的执行中守卫，main_window.py:1901）
    ⇒ 语义是仓库既有的「本次作业结束后生效」**延迟生效契约**，**不是失败**。

R8 把 (b) 一并回滚了，用户改的放置**永久蒸发**，而下拉框还停在他选的那一项
—— 界面对用户撒谎。实测（真 MainWindow + 真 combo 信号）::

    用户把 combo 切到「锚点归位」
      -> spec.placement = preserve     ← 被回滚
         combo         = 锚点归位      ← UI 停在用户选的那项
    jobDone 补编译后
      -> spec.placement = 仍是 preserve ← 补编译拿到的是回滚后的 spec
         MODEL vs UI disagree? True

**为什么不能简单地「一律不回滚」**：R8 冻住的那条要求是真的 —— 换作业
（几何变了）时若不回滚，``_job_spec`` 已是新几何而 ``_job_lines`` 停在旧几何，
机器切旧版面而作业页看着正常（半提交）。那条由
``tests/test_gui_layout_recompile_atomicity.py::test_export_while_running_does_not_half_commit``
钉住（**执行中换作业必须回滚**）。本文件不碰它。

故按**改动性质**区分（这正是 ask 说的「读 _recompile 的返回值语义，别靠猜」）：

===============  =====================  ==========================
改动              ``paths_paper``        处置
===============  =====================  ==========================
换作业            变了（换了几何）        **回滚**（R8：spec 新/旧 lines = 半提交）
参数/口径         未变（同一份几何）      **不回滚**，留给 jobDone 补编译
===============  =====================  ==========================

「放置」属第二类：它只是同一份几何的**放置方式**，几何多重集没变 ⇒ 不存在
R8 说的半提交；而它恰恰是用户最需要「结束后生效」的那类改动。

**另一条底线（ask 要求 2）**：真编译失败回滚时，若下拉框与被还原的 spec
不一致，必须让它们一致（把 combo 还原），否则用户看到的还是他选的那一项。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets                            # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.job import JobSpec, Placement            # noqa: E402
from megapro.gui.main_window import MainWindow           # noqa: E402


# -- 工具 ------------------------------------------------------------------

class _Sig:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class _StubWorker:
    """作业页只在构造期要一个 worker；本文件不发任何真实指令。"""

    def __init__(self):
        self._state = "READY"
        self.reqRunJob = _Sig()
        self.reqSendSequence = _Sig()
        self.reqSendLine = _Sig()

    def setPenState(self, *_a, **_k):
        pass


#: 版面几何 —— **左下角不在原点**（故意）。
#: 锚点归位 bl→(0,0) 会把整份内容平移到原点，而 preserve 保持原位 ⇒
#: 两种放置编出的 lines **确实不同**。若版面本来就在 (0,0)，两种模式编出
#: 逐行相同的 lines，判据「lines 是不是 anchor 版」就会**恒真**（本文件
#: 实测踩到过：方块画在 (0,0) 时 preserve/anchor 的 lines 一字不差）。
_SQUARE = [[(40.0, 30.0), (60.0, 30.0), (60.0, 50.0), (40.0, 50.0)]]


def _window():
    w = MainWindow()
    w._pen_down_z, w._cut_touch_z, w._safe_z = 17.0, 17.5, 30.0
    w._worker = _StubWorker()
    w._homed = True
    w._link_ready = True
    w._refresh_gate_ui()
    return w


def _layout_job_window():
    """作业页握一份**排版作业**（基线），静默同步关掉（避免本文件测别的通道）。"""
    w = _window()
    lp = w.layout_page
    lp._sync_job_cb.setChecked(False)
    w._set_job_and_recompile(
        JobSpec(paths_paper=[list(p) for p in _SQUARE],
                source_name="排版版面",
                placement=Placement(mode="preserve")),
        from_layout=True)
    assert w._job_from_layout is True, "前置：应是排版作业"
    assert w._job_compiled is not None and w._job_compiled.runnable
    return w, lp


def _combo_mode(w) -> str:
    return w.placement_combo.currentData()


def _model_mode(w) -> str:
    return w._job_spec.placement.mode


def _select_anchor(w) -> None:
    """真用户操作：点下拉 ⇒ 走 ``currentIndexChanged`` 信号。"""
    idx = w.placement_combo.findData("anchor")
    w.placement_combo.setCurrentIndex(idx)


def _lines_anchor(w) -> bool:
    """将发送的 lines 是否**已按 anchor 归位**（判据 = 最小 X 是否为 0）。"""
    zs = [ln for ln in w._job_lines if ln.startswith("G0 X") or ln.startswith("G1 X")]
    if not zs:
        return False
    return any(float(ln.split("X")[1].split()[0]) == pytest.approx(0.0)
               for ln in zs)


# -- ① 执行中改放置：不回滚，jobDone 接住 ---------------------------------

def test_placement_change_while_running_is_not_rolled_back(win_running):
    """执行中切「锚点归位」⇒ spec **不回滚**（留给 jobDone 补编译）。

    这是 D3 的主契约：这一项**不是**编译失败，是既有的「结束后生效」。
    """
    w, lp = win_running
    assert _model_mode(w) == "preserve", "前置：基线应为 preserve"

    _select_anchor(w)                      # 真 combo 信号 ⇒ _on_placement_changed

    assert _model_mode(w) == "anchor", (
        f"执行中改放置被回滚了：spec.placement 仍是 {_model_mode(w)!r} —— "
        f"用户改的放置永久蒸发，而这是仓库既有的「结束后生效」语义，不是编译失败")
    # lines 不该此刻变化（作业正在跑，机器收的是执行开始时的快照）
    assert not _lines_anchor(w), \
        "执行中 lines 不该变成 anchor 版（那是正在执行的那份作业）"


def test_placement_lands_after_job_done(win_running):
    """jobDone 的补编译自然接住 ⇒ spec 与 combo 都是用户选的那一项。"""
    w, lp = win_running
    _select_anchor(w)
    assert _model_mode(w) == "anchor", "前置：执行中不该回滚"

    w._job_running = False
    w._on_job_done(ok=True)                 # 既有补编译路径

    assert _model_mode(w) == "anchor", "补编译后 spec 仍不是用户选的"
    assert _combo_mode(w) == "anchor", "补编译后 combo 不应被改回去"
    assert _model_mode(w) == _combo_mode(w), (
        f"补编译后模型({_model_mode(w)})与 UI({_combo_mode(w)})不一致 —— "
        f"用户看到的和他将要执行的不是一个东西")
    assert _lines_anchor(w), "补编译后 lines 应当是 anchor 版（落点已归位）"


def test_ui_and_model_agree_immediately_while_running(win_running):
    """执行中 UI 与模型必须**当场**一致（不能一个说 anchor、另一个说 preserve）。"""
    w, lp = win_running
    _select_anchor(w)
    assert _model_mode(w) == _combo_mode(w), (
        f"执行中模型({_model_mode(w)})与 UI({_combo_mode(w)})不一致 —— "
        f"下拉框停在用户选的那项，模型却不是那一项")


# -- ② 换作业（几何变了）：仍要回滚（R8 契约不许被破坏）-------------------

def test_job_swap_while_running_still_rolls_back(win_running):
    """执行中**换作业**（几何变了）⇒ 仍回滚。

    这条是 R8 的核心，不许因为 D3 而被放松：spec 若是新几何而 lines 停在旧
    几何，机器切旧版面、作业页看着正常 —— 半提交。
    """
    w, lp = win_running
    before = [list(p) for p in w._job_spec.paths_paper]

    # 排版页加一条线 ⇒ 静默同步带来**新几何**（换作业类）
    from megapro.gui.layout.model import Item

    lp._sync_job_cb.setChecked(True)
    lp._add_items([Item(paths=[[(50.0, 50.0), (150.0, 50.0), (150.0, 150.0)]],
                        name="extra")])

    assert [list(p) for p in w._job_spec.paths_paper] == before, (
        "执行中换作业必须回滚（R8：spec 新几何 / 旧 lines = 半提交）")


# -- ③ 真编译失败：回滚 + UI 一并还原 -------------------------------------

def test_real_compile_failure_keeps_spec_and_lines_same_source(win_ok):
    """真编译失败后，spec 与 lines 仍**同一源**（R8 主契约）。"""
    w, lp = win_ok
    before_paths = [list(p) for p in w._job_spec.paths_paper]
    before_lines = list(w._job_lines)

    # 造编译失败：切刀 + 负 Z
    w.tool_combo.setCurrentIndex(w.tool_combo.findData("knife"))
    w._cut_touch_z = 2.0
    w.cut_depth_spin.setValue(5.0)

    # 此时走「换作业」入口：新几何 + 失败 ⇒ 必须整体回滚
    w._set_job_and_recompile(
        JobSpec(paths_paper=[[(60.0, 60.0), (160.0, 60.0), (160.0, 160.0)]],
                source_name="排版版面",
                placement=Placement(mode="preserve")),
        from_layout=True)

    assert "编译失败" in w.console.toPlainText(), "前置：应触发编译失败"
    assert [list(p) for p in w._job_spec.paths_paper] == before_paths, \
        "真编译失败后 spec 必须回滚到与 lines 同一源的那版"
    assert w._job_lines == before_lines, "失败路径本就应保持上一份 lines"


def test_real_compile_failure_restores_combo_to_match_spec(win_ok):
    """真编译失败回滚后，**combo 必须与被还原的 spec 一致**（ask 要求 2）。

    否则用户看到的下拉框停在他选的那一项，而模型是另一项 —— 又一次界面撒谎。

    走真实路径：先让参数必然编译失败，再把 combo 切到 anchor（信号 ⇒
    ``_on_placement_changed`` ⇒ 原子包装）⇒ 真回滚 ⇒ combo 必须被拨回 spec
    实际的 preserve。
    ⚠ **不能用** ``_set_placement_ui`` 造前置：那只动 combo、不碰模型，
    也就**不触发**回滚，测不到要求 2 的那条路（实测：那样写断言必然失败或
    恒真，钉不住任何东西）。
    """
    w, lp = win_ok
    assert _model_mode(w) == "preserve", "前置：基线应为 preserve"

    # 先把编译搞坏：切刀 + touch_z(2.0) < 切深(5.0) ⇒ 下压后 Z 为负
    w.tool_combo.setCurrentIndex(w.tool_combo.findData("knife"))
    w._cut_touch_z = 2.0
    w.cut_depth_spin.setValue(5.0)

    # 真用户操作：把 combo 切到 anchor ⇒ 触发回滚
    _select_anchor(w)

    assert "编译失败" in w.console.toPlainText(), "前置：应触发编译失败"
    assert _model_mode(w) == "preserve", "前置：回滚后 spec 应回到 preserve"
    assert _combo_mode(w) == _model_mode(w), (
        f"编译失败后模型({_model_mode(w)})与 UI({_combo_mode(w)})不一致 —— "
        f"回滚了模型却没还原下拉框")


def test_combo_and_model_agree_after_rollback(win_ok):
    """**通用底线**：任何一次回滚之后，combo 与 spec 都必须一致。"""
    w, lp = win_ok
    w.tool_combo.setCurrentIndex(w.tool_combo.findData("knife"))
    w._cut_touch_z = 2.0
    w.cut_depth_spin.setValue(5.0)

    _select_anchor(w)          # 失败 ⇒ 回滚
    assert _model_mode(w) == _combo_mode(w), (
        f"回滚后模型({_model_mode(w)})与 UI({_combo_mode(w)})说各话")


# -- fixture ---------------------------------------------------------------

@pytest.fixture
def win_ok():
    """非执行期的作业页（编译成功）。"""
    w, lp = _layout_job_window()
    return w, lp


@pytest.fixture
def win_running(win_ok):
    """**执行中**的作业页（与 D3 复现同口径）。"""
    w, lp = win_ok
    w._job_running = True
    yield w, lp
    w._job_running = False
