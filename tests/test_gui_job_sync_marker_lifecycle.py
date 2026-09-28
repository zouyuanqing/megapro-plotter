"""B2 修复回归（补洞）：**来源标记的作用域与生命周期**必须被钉住。

``tests/test_gui_job_sync_ownership.py`` 已经把「作业页握文件作业 + 切页 ⇒
不被顶掉」这个**可观察行为**钉死了（4 种情形齐全）。本文件补的是它**没有**
覆盖的那一层：**门禁是靠什么判据实现的、以及那个判据在作业页的整个生命周期里
是否始终正确**。

为什么必须单独钉（变异体验证，非推断）：把 ``_on_layout_job_sync`` 的来源门禁
从布尔量 :attr:`MainWindow._job_from_layout` 换成
``_job_spec.source_name == "排版版面"``，``test_gui_layout_job_sync.py`` +
``test_gui_job_sync_ownership.py`` **14 个用例全绿**。原因很直白：那些用例的
「文件作业」都叫 ``design.svg``，永远不会撞上排版作业的硬编码名。

而撞名不是理论问题 —— 载入对话框的过滤器是
「SVG 文件 (*.svg);;**所有文件 (*)**」，用户载入一个**无扩展名、恰好叫
「排版版面」**的文件完全合法（本次实跑确认：``Path(path).name`` 就是
「排版版面」，与 ``layout_page.to_job_spec`` 的常量逐字相同）。用显示名当归属
令牌，等于把这个洞留在了下一任维护者面前：谁为了「少加一个字段」把它换回去，
套件一声不响，而缺陷原样回来。

⚠ **撞名那一格已由** ``test_gui_job_sync_ownership.py::
test_file_job_named_like_a_layout_job_is_still_not_hijacked`` 覆盖，本文件
**不重复**它（重复的回归只会让两处各自漂移）。本文件只补该文件**没有**的两层：

1. **作用域**：门禁只管**静默**通道，显式「送去作业」仍必须能覆盖文件作业
   （否则用户载入文件后就再也送不进排版作业了 —— 而这条若被误加，无任何症状：
   门禁的其余用例全绿，因为它们都停在「不该覆盖」的那一侧）。
2. **生命周期**：来源标记在「显式导出 / 清空 / 改 placement / 改参数」各点上的
   取值，以及它**不该**被改的那两处（``replace()`` 参数投影）。这些全是
   「标记归错值 ⇒ 排版作业静默失去同步能力」或「标记漏归位 ⇒ 用户清空后
   作业凭空回来」，症状都与本条缺陷无关，容易整批漏网。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# --- 脚手架（与 test_gui_job_sync_ownership.py 同口径：假 worker + 显式标定）--

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


#: 排版第 0 / 第 1 页几何。与文件作业几何互不重叠 ⇒ 任何混淆都能被断言抓住。
_P0 = [[(0.0, 0.0), (10.0, 0.0)]]
_P1 = [[(100.0, 100.0), (140.0, 140.0)]]
#: 文件作业的**纸面**几何（SVG y-down y=150 经 paper y-up 翻转 = 60）
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


def _two_page_layout(w):
    """造「第 0 页 P0 / 第 1 页 P1」两页文档，停在第 0 页；**不导出**。"""
    from megapro.gui.layout.model import Item

    lp = w.layout_page
    lp._sync_job_cb.setChecked(False)
    lp._add_items([Item(paths=[list(p) for p in _P0], name="p0")])
    lp._on_page_add()
    lp._add_items([Item(paths=[list(p) for p in _P1], name="p1")])
    lp._page_bar.setCurrentIndex(0)
    return lp


def _load_file_job(w, tmp_path, monkeypatch, filename="design.svg"):
    """走**真实** ``_on_load_svg`` 载入文件作业（不经手工赋 _job_spec）。

    手工赋 ``w._job_spec = JobSpec(...)`` 绕开所有来源标记代码，能越过正在修的
    门禁 —— 那样的用例会假绿。故必须走生产入口。
    """
    from PySide6 import QtWidgets

    p = tmp_path / filename
    p.write_text(_SVG, encoding="utf-8")
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(p), "")))
    w._on_load_svg()
    assert w._job_spec is not None and w._job_lines, "文件作业未载入成功"
    return w._job_spec.source_name


def _paths(w):
    return [list(p) for p in w._job_spec.paths_paper]


# --- 1. 作用域：门禁只管静默通道，显式导出仍必须能覆盖 -----------------------

def test_explicit_export_still_overrides_file_job(tmp_path, monkeypatch):
    """载入文件作业后，点『送去作业』**仍必须**能把排版作业送进去。

    门禁若被误加到 :meth:`_on_layout_export` 上，用户就再也送不进排版作业了
    （先载文件 → 排版导出被拒）。而 ``_on_layout_export`` 本来就是**显式**动作：
    会切标签页、会刷控制台、用户明确知道自己在换作业 —— 它与「静默」的语义
    前提完全不同，本来就不该受静默门禁约束。

    同时钉住来源标记在此**必须**翻成 True（否则导出后切页就再也不同步了）。
    """
    w = _make_window()
    lp = _two_page_layout(w)
    _load_file_job(w, tmp_path, monkeypatch)
    assert _paths(w) == [list(p) for p in _FILE_PATHS], "前置：作业页握文件作业"

    lp._on_export()                          # ← 显式「送去作业」

    assert _paths(w) == [list(p) for p in _P0], (
        f"显式导出没能覆盖文件作业：paths={_paths(w)} —— 门禁被误加到导出通道")
    assert w._job_from_layout is True, "显式导出后来源标记必须翻成 True"
    w.close()


def test_export_then_page_switch_resumes_syncing(tmp_path, monkeypatch):
    """文件作业 → 显式导出（转为排版作业）→ 切页 ⇒ 同步恢复。

    把上一条和「排版作业 + 切页会同步」串起来，钉住标记的**翻转**而非只有
    其终态：只断导出后的 True，断不出「标记有没有在导出那一刻被正确翻过来、
    之后又能不能正常用」。
    """
    w = _make_window()
    lp = _two_page_layout(w)
    _load_file_job(w, tmp_path, monkeypatch)
    lp._on_export()                          # 文件作业 → 排版作业
    assert _paths(w) == [list(p) for p in _P0]

    lp._sync_job_cb.setChecked(True)
    lp._page_bar.setCurrentIndex(1)

    assert _paths(w) == [list(p) for p in _P1], (
        f"显式导出后切页不再同步：paths={_paths(w)}")
    w.close()


# --- 2. 生命周期：各翻转点 + 两处「不该被改」 -------------------------------

def test_clear_job_resets_marker_and_stays_empty_after_page_switch():
    """清空作业 ⇒ 标记归 False；此后切页**不得**把作业凭空塞回来。

    用户「清除作业」是明确表示「我不要作业了」。若 :meth:`_on_clear_job` 忘了
    归位标记，切页就会把排版页内容**凭空**送进作业页 —— 违反用户刚刚表达的
    意图，且没有任何提示。
    """
    w = _make_window()
    lp = _two_page_layout(w)
    lp._on_export()                          # 作业页握排版作业
    assert w._job_from_layout is True

    w._on_clear_job()
    assert w._job_from_layout is False, "清空作业后来源标记必须归位"
    assert w._job_spec is None

    lp._sync_job_cb.setChecked(True)
    lp._page_bar.setCurrentIndex(1)

    assert w._job_spec is None, \
        f"用户清空后，切页把作业塞回来了：{_paths(w)}"
    w.close()


def test_placement_change_preserves_layout_ownership():
    """改 Placement（走 ``_on_placement_changed`` 的 ``replace()``）**不**改归属。

    归属是「这份作业从哪来」，而 ``_on_placement_changed`` /
    ``_recompile`` 的参数投影只是**同一份作业换字段**（``dataclasses.replace``
    造新对象，但来源没变）。若在这两处把标记顺手归位，用户改一下放置模式就
    会让排版作业**静默失去**页切换同步能力 —— 且没有任何症状，直到某天发现
    切页不刷新了。
    """
    w = _make_window()
    lp = _two_page_layout(w)
    lp._on_export()
    assert w._job_from_layout is True

    # 走真实入口：切一次放置模式（handler 内部是 replace()）
    w.placement_combo.setCurrentIndex(1)
    assert w._job_from_layout is True, \
        "改放置模式后来源标记被归位了 —— 排版作业会静默失去页切换同步"

    lp._sync_job_cb.setChecked(True)
    lp._page_bar.setCurrentIndex(1)
    assert _paths(w) == [list(p) for p in _P1], (
        f"改过放置模式后切页不再同步：paths={_paths(w)}")
    w.close()


def test_param_recompile_preserves_layout_ownership():
    """改作业页参数（走 ``_recompile`` 的参数投影）同样**不**改归属。"""
    w = _make_window()
    lp = _two_page_layout(w)
    lp._on_export()
    assert w._job_from_layout is True

    w._feed_xy = 900.0                       # 既有参数触发点
    w._recompile()                           # 内部 _spec_with_current_params

    assert w._job_from_layout is True, \
        "参数投影后来源标记被归位了 —— 排版作业会静默失去页切换同步"
    w.close()


# --- 3. 标记本身的存在性与初值 --------------------------------------------

def test_marker_starts_false_and_never_leaks_across_windows():
    """新建窗口标记必为 False；关掉再建一个不得继承上一个的归属。

    归属是**逐窗口**的状态（作业页握着什么因窗口而异）。若被误提到类属性或
    模块级变量上，第二个窗口就会继承第一个窗口的归属 —— 表现为「新建一个
    窗口还没载入任何作业，切一下页签就冒出作业」。
    """
    w = _make_window()
    assert w._job_from_layout is False, "新窗口的来源标记必须是 False"
    lp = _two_page_layout(w)
    lp._on_export()
    assert w._job_from_layout is True
    w.close()

    w2 = _make_window()
    assert w2._job_from_layout is False, \
        "新窗口继承了上一个窗口的归属（标记被放到了类属性/模块级）"
    w2.close()
