"""R1–R4 回归：四条被对抗性复核推翻的修复，逐条钉住。

四条都在**纯 UI 路径**上可达、且都能让界面说出与机器要切的不一致的话：

**R1**（`canvas/handles.py` 的 ``pivot()`` 与 D1 的祖先变换不同框）
    D1 之后 ``sceneBoundingRect()`` 是页面系、``gi.pos()`` 仍是父系，而
    ``pivot()`` 取后者、被拿去和页面系的 ``scene_pos`` 一起算 ``r0/r1``
    与旋转角。实测组 pos=(100,30) 时支点误差 **104.40mm**、抓可见角点拖到
    两倍得 ``k=1.1497`` 而非 2.0。
    ⚠ **本批修不了**（要改 ``handles.py``，无所有权）⇒ 下面用
    ``xfail(strict=True)`` 钉住契约：将来有人改了 handles.py，它会 XPASS
    逼人摘标记；在那之前它如实记录「仍坏着」。

**R2**（``_layout_out_of_bed`` 只在静默同步那条路上被填）
    那条路被「同步作业预览」复选框把守 ⇒ 用户**关掉**它（真控件、tooltip
    明写受支持）时整场会话停在 ``None`` ⇒ 作业页对「版面越界」零提示，
    而 anchor 已把落点搬走。修法：判据在**显示时现取**。

**R3**（``job_info`` 标签不换行，越界告知的可操作后半截被硬裁）
    D2-② 的告知只经这一个 QLabel 出去，1000px 窗口下整串需要 1188px、
    标签只分到 420px ⇒ 99 字里只有 35 字落像素，被切掉的恰是「超出多少」
    与「已挪到哪、照此执行」。

**R4**（``_align`` / ``_distribute`` 无条件 append 恒等命令）
    「本来就对齐」时几何零变化却仍 push（undoText「对齐」）⇒ 下一次
    Ctrl+Z 被它吃掉；本批新增的 ``_show_ok`` 还会把空转**播报成成功**。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtCore, QtWidgets                        # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.job import JobSpec, Placement               # noqa: E402
from megapro.gui.layout.layout_page import LayoutPage         # noqa: E402
from megapro.gui.layout.model import Item, iter_ancestors, unit_paths  # noqa: E402
from megapro.gui.main_window import MainWindow               # noqa: E402


# -- 工具 ------------------------------------------------------------------

def _rect(name, x, y, w, h, z=0.0):
    return Item(paths=[[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]],
                pos=(0.0, 0.0), name=name, z=z)


def _page(*items):
    lp = LayoutPage()
    lp.snap_enabled = False
    lp._add_items(list(items))
    lp._rebuild_scene()
    lp._after_change()
    return lp


def _select_all(lp):
    for gi in lp._scene_items:
        gi.setSelected(True)
    lp._on_selection_changed()


def _zmap(lp):
    return {it.name: it.z for it in
            __import__("megapro.gui.layout.model", fromlist=["iter_leaves"]).iter_leaves(lp.doc.items)}


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

    def setPenState(self, *_a, **_k):
        pass


@pytest.fixture
def job_window(monkeypatch):
    """真 MainWindow（作业页已握一份排版作业），ZMessageBox 打桩防挂死。"""
    real_exec = QtWidgets.QMessageBox.exec
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self, *a, **k: 0)
    w = MainWindow()
    w._pen_down_z, w._cut_touch_z, w._safe_z = 17.0, 17.5, 30.0
    w._worker = _StubWorker()
    w._homed = True
    w._link_ready = True
    w._refresh_gate_ui()
    w.layout_page._sync_job_cb.setChecked(False)
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", real_exec)
    yield w
    w.close()


def _export_block(w):
    """在作业页放一个床内方块并「送去作业」（建立 _job_from_layout）。"""
    lp = w.layout_page
    lp._add_items([_rect("BLK", 0.0, 0.0, 20.0, 20.0, 1.0)])
    lp._rebuild_scene()
    lp._after_change()
    w._on_layout_export(lp.to_job_spec())
    return lp.doc.items[0]


def _move_out_of_bed(lp, blk, pos):
    """真 UI 路径改 pos（走撤销 push ⇒ indexChanged ⇒ 静默同步），不直接改字段。"""
    from megapro.gui.canvas.undo_cmds import MoveItemsCommand

    lp._undo.push(MoveItemsCommand(lp, [(blk, blk.pos, pos)]))
    lp._after_change()


# ============================================================================
# R1：handles 支点与 D1 祖先变换不同框（本批修不了，xfail 钉契约）
# ============================================================================

def _grouped_page():
    """组 pos=(100,30)，成员在组编辑态下成为操作单元（单选 ⇒ 走 pos() 分支）。"""
    lp = _page(_rect("leaf", 0.0, 0.0, 10.0, 20.0, 1.0),
               _rect("sib", 0.0, 60.0, 10.0, 20.0, 2.0))
    g = lp.doc.group_items([lp.doc.items[0], lp.doc.items[1]])
    g.pos = (100.0, 30.0)
    lp._rebuild_scene()
    lp._after_change()
    leaf = lp.doc.items[0].children[0]
    lp._enter_group_edit(leaf)
    gi = lp._gi_for(leaf)
    gi.setSelected(True)
    lp._on_selection_changed()
    return lp, leaf, gi


@pytest.mark.xfail(strict=True, reason="R1 未修：需改 canvas/handles.py（本批无所有权）")
def test_r1_handles_pivot_is_in_page_space():
    """单选支点必须落在**画出来的**本地原点上（页面系），不是 ``pos()``。

    修复前实测：pivot()=(0,0) 而本地图元画在 (100,30) ⇒ 误差 104.40mm。
    """
    lp, _leaf, gi = _grouped_page()
    drawn_origin = gi.mapToParent(QtCore.QPointF(0.0, 0.0))
    pivot = lp._handles.pivot()
    assert abs(pivot.x() - drawn_origin.x()) < 1e-6 and \
        abs(pivot.y() - drawn_origin.y()) < 1e-6, (
            f"支点 {pivot.x():.2f},{pivot.y():.2f} 与画出来的原点 "
            f"{drawn_origin.x():.2f},{drawn_origin.y():.2f} 脱节 —— "
            f"缩放/旋转的 r0、角度全错")


@pytest.mark.xfail(strict=True, reason="R1 未修：需改 canvas/handles.py（本批无所有权）")
def test_r1_drag_visible_corner_to_double_yields_k_two():
    """抓**可见角点**拖到两倍大小 ⇒ k 必须是 2.0。

    修复前实测 k=1.1497（支点在父系 (0,0)，与可见角点 (110,50) 的距离比
    被祖先偏移扭曲）。正确支点是画出来的本地原点 (100,30)。
    """
    import math

    lp, _leaf, gi = _grouped_page()
    lp._handles.sync([gi])
    r = gi.sceneBoundingRect()
    near = (r.right(), r.bottom())
    far = (near[0] + r.width(), near[1] + r.height())
    pivot = lp._handles.pivot()
    r0 = math.hypot(near[0] - pivot.x(), near[1] - pivot.y())
    r1 = math.hypot(far[0] - pivot.x(), far[1] - pivot.y())
    k = r1 / r0
    assert abs(k - 2.0) < 1e-6, f"拖到两倍应得 k=2.0，实得 {k}"


@pytest.mark.xfail(strict=True, reason="R1 未修：需改 canvas/handles.py（本批无所有权）")
def test_r1_multi_rotate_does_not_mix_frames():
    """多选旋转：写回的父系 pos 不得混进页面系支点。

    修复前实测：祖先 pos=(30,20)、旋转 90° 后两版结果 x 差 50mm。
    """
    lp = _page(_rect("a", 0.0, 0.0, 20.0, 10.0, 1.0),
               _rect("b", 0.0, 30.0, 20.0, 10.0, 2.0))
    g = lp.doc.group_items([lp.doc.items[0], lp.doc.items[1]])
    g.pos = (30.0, 20.0)
    lp._rebuild_scene()
    lp._after_change()
    a, b = lp.doc.items[0].children
    lp._enter_group_edit(a)
    gis = [lp._gi_for(a), lp._gi_for(b)]
    for x in gis:
        x.setSelected(True)
    lp._on_selection_changed()
    before = {it.name: it.pos for it in iter_ancestors(lp.doc.items) if not it.children
              for it in [it]}
    pivot = lp._handles.pivot()
    origin = gis[0].mapToParent(QtCore.QPointF(0.0, 0.0))
    assert abs(pivot.x() - origin.x()) < 1e-6 and \
        abs(pivot.y() - origin.y()) < 1e-6, (
            f"多选支点 {pivot.x():.2f},{pivot.y():.2f} 应在页面系 "
            f"{origin.x():.2f},{origin.y():.2f}，否则旋转会把父系 pos 写歪")
    del before


def test_r1_path_item_exposes_page_space_accessors():
    """本批已交付的**使能半边**：PathItem 提供页面系原点与逆变换。

    ``page_origin()`` / ``page_to_parent()`` 是 handles 修正需要的两个入口
    （把支点换到页面系、再把结果换回父系）。本条不钉行为正确性，只钉它们
    存在且**与 pos() 的父系语义自洽**：空祖先链时 page_origin()==pos()、
    page_to_parent 是逆运算。
    """
    lp, _leaf, gi = _grouped_page()
    # 有祖先时 page_origin != pos（这正是 R1 缺陷的量化）
    drawn = gi.mapToParent(QtCore.QPointF(0.0, 0.0))
    assert abs(gi.page_origin().x() - drawn.x()) < 1e-9, \
        "page_origin() 应等于本地图元的页面原点"
    # 逆运算：page_to_parent(page_origin) == pos
    back = gi.page_to_parent(gi.page_origin())
    assert abs(back.x() - gi.pos().x()) < 1e-6 and \
        abs(back.y() - gi.pos().y()) < 1e-6, \
        "page_to_parent 应是 page_origin 的逆运算"

    # 顶层图元（空祖先链）两者相等
    flat = _page(_rect("top", 5.0, 7.0, 3.0, 3.0, 1.0))
    top_gi = flat._scene_items[0]
    assert abs(top_gi.page_origin().x() - top_gi.pos().x()) < 1e-9, \
        "空祖先链时 page_origin() 应逐位等于 pos()"


# ============================================================================
# R2：同步开关关掉时，作业页仍须拿到版面越床判据
# ============================================================================

def test_r2_notice_present_with_sync_switch_off(job_window):
    """复选框**关掉**时，anchor 洗白仍必须在作业页被说出来。

    修复前实测：``_layout_out_of_bed`` 停在 ``None``、job_info 无
    「排版版面越界」、``runnable=True violations=()``，机器切 (0,0)-(20,20)
    而用户排的是 (-40,10) —— **零提示**。
    """
    w = job_window
    lp = w.layout_page
    assert not lp._sync_job_cb.isChecked(), "前置：本用例专测开关**关掉**"

    blk = _export_block(w)
    _move_out_of_bed(lp, blk, (-40.0, 10.0))

    assert lp._out_of_bed() is not None, "前置：排版页应报越界"

    w._job_spec = JobSpec(
        paths_paper=list(w._job_spec.paths_paper),
        source_name=w._job_spec.source_name,
        placement=Placement(mode="anchor", anchor="bl", target=(0.0, 0.0)))
    w._set_placement_ui("anchor")
    w._recompile()

    assert "排版版面越界" in w.job_info.text(), (
        f"开关关掉时作业页对「版面越界」零提示：{w.job_info.text()!r} —— "
        f"排版页明明报了 {lp._out_of_bed()[0]:.0f}mm")
    assert "按当前放置执行" in w.job_info.text(), \
        f"没告知「照此放置执行」：{w.job_info.text()!r}"


def test_r2_notice_is_not_stale_after_later_edit(job_window):
    """更糟的变体：判据**陈旧**比没有更误导（自信地报错数字）。

    修复前实测：开关先开（判据取到 40.0mm）→ 关掉 → 再拖到 -100mm，
    作业页仍报「最大超出 40.0mm」（少报 60mm）。
    """
    w = job_window
    lp = w.layout_page
    lp._sync_job_cb.setChecked(True)          # 先开，取一次判据
    blk = _export_block(w)
    _move_out_of_bed(lp, blk, (-40.0, 0.0))
    w._recompile()
    assert "40.0mm" in w.job_info.text(), "前置：此时应报 40.0mm"

    lp._sync_job_cb.setChecked(False)         # 关掉，后续编辑不再同步
    _move_out_of_bed(lp, blk, (-100.0, 0.0))
    w._recompile()
    truth = lp._out_of_bed()
    assert truth is not None and abs(truth[0] - 100.0) < 1e-6, \
        f"前置：真实越界量应为 100.0mm，实得 {truth}"
    assert "100.0mm" in w.job_info.text(), (
        f"作业页仍在报陈旧的 40.0mm：{w.job_info.text()!r} —— "
        f"真实越界量已是 {truth[0]:.0f}mm")


def test_r2_export_path_also_populates_verdict(job_window):
    """「送去作业」这条排版来源**也要**填判据（不只静默同步那条路）。"""
    w = job_window
    lp = w.layout_page
    lp._sync_job_cb.setChecked(False)
    blk = _export_block(w)
    _move_out_of_bed(lp, blk, (-40.0, 0.0))
    w._recompile()
    assert "排版版面越界" in w.job_info.text(), (
        f"export 路径没填判据：{w.job_info.text()!r}")


# ============================================================================
# R3：越界告知的可操作后半截不得被硬裁
# ============================================================================

def test_r3_job_info_wraps_the_notice(job_window):
    """告知串必须**能完整显示**（折行 + tooltip 兜底）。

    修复前实测：wordWrap=False、整串要 1188px 而标签只分到 420px ⇒ 99 字里
    只有 35 字落像素，「最大超出 40.0mm」「已移到」「按当前放置执行」全部被切。
    """
    w = job_window
    lp = w.layout_page
    blk = _export_block(w)
    _move_out_of_bed(lp, blk, (-40.0, 0.0))
    w._recompile()
    lbl = w.job_info
    txt = lbl.text()
    assert "排版版面越界" in txt, f"前置：本例应产生越界告知：{txt!r}"
    assert lbl.wordWrap() is True, "job_info 未开 wordWrap —— 长告知会被硬裁"
    # tooltip 兜底整串：任何窗口宽度下都能读到可操作那半句
    assert lbl.toolTip() == txt, "job_info 的 tooltip 未兜住整串告知"


def test_r3_notice_fits_after_wrap(job_window):
    """折行后控件**实际高度**必须够放下整串（渲染事实，不是字符数估算）。

    ⚠ 判据用 ``height()`` 与 ``heightForWidth()`` 的比较，**不用**「可见字数」
    估算：字符数与像素列不是线性关系，那样的估算在修复前也会给出误导性的
    「看起来没被裁」。这里比的是「控件实际拿到的�� vs 放满整串所需的高」。

    修复前实测：wordWrap=False 时整串要 1188px、标签只分到 420px ⇒ 硬裁，
    「最大超出 40.0mm」「已移到」「按当前放置执行」三样全不落像素。
    """
    w = job_window
    lp = w.layout_page
    blk = _export_block(w)
    _move_out_of_bed(lp, blk, (-40.0, 0.0))
    w._recompile()
    lbl = w.job_info
    assert lbl.wordWrap() is True, "前置：job_info 必须开 wordWrap"

    real_exec = QtWidgets.QMessageBox.exec
    try:
        QtWidgets.QMessageBox.exec = lambda self, *a, **k: 0
        w.resize(800, 800)
        w.show()
        QApplication.processEvents()
        QApplication.processEvents()
        for width in (200, 348, 640):
            lbl.setFixedWidth(width)
            QApplication.processEvents()
            QApplication.processEvents()
            need = lbl.heightForWidth(width)
            assert lbl.height() >= need, (
                f"宽 {width}px 时控件高 {lbl.height()} < 放满整串所需 {need}"
                f" —— 告知仍被裁掉（尾句是「已挪到哪、照此执行」那条可操作事实）")
    finally:
        QtWidgets.QMessageBox.exec = real_exec
        w.hide()


# ============================================================================
# R4：_align / _distribute 空转不得推恒等命令、不得播报假成功
# ============================================================================

def test_r4_align_noop_pushes_nothing_and_tells_truth():
    """本来就左对齐 ⇒ 几何零变化、**不推命令**、提示行说实话。"""
    lp = _page(_rect("a", 0.0, 0.0, 10.0, 10.0, 1.0),
               _rect("b", 0.0, 60.0, 5.0, 5.0, 2.0))
    _select_all(lp)
    from megapro.gui.layout.model import iter_leaves

    before = {it.name: it.pos for it in iter_leaves(lp.doc.items)}
    idx0 = lp._undo.index()

    lp._align("left")

    after = {it.name: it.pos for it in iter_leaves(lp.doc.items)}
    assert before == after, "左对齐不该动几何"
    assert lp._undo.index() == idx0, (
        f"空转仍推了一条命令（index {idx0}->{lp._undo.index()}、"
        f"undoText={lp._undo.undoText()!r}）—— 它会吃掉用户下一次 Ctrl+Z")
    msg = lp.status_label.text()
    assert "已对齐" not in msg, f"空转被播报成成功：{msg!r}"


def test_r4_noop_align_does_not_swallow_next_undo():
    """恒等命令吃 Ctrl+Z 的后果：置顶后空转左对齐，一次撤销必须撤掉置顶。

    修复前实测：一次 Ctrl+Z 后 z 纹丝不动（被空转命令吃掉），需两次。
    """
    lp = _page(_rect("a", 0.0, 0.0, 10.0, 10.0, 1.0),
               _rect("b", 0.0, 60.0, 5.0, 5.0, 2.0))
    _select_all(lp)
    lp._zorder("top")
    z_top = _zmap(lp)
    lp._align("left")            # 空转
    lp._undo.undo()              # 这一次必须撤掉置顶
    assert _zmap(lp) != z_top, "一次 Ctrl+Z 没有撤掉置顶（被空转命令吃掉）"


def test_r4_distribute_degenerate_pushes_nothing():
    """退化分布（三个零宽图元 ⇒ gaps=0）⇒ 几何零变化、不推命令、不报成功。"""
    lp = _page(_rect("d0", 0.0, 0.0, 0.0, 0.0, 0.0),
               _rect("d1", 0.0, 0.0, 0.0, 0.0, 1.0),
               _rect("d2", 0.0, 0.0, 0.0, 0.0, 2.0))
    _select_all(lp)
    from megapro.gui.layout.model import iter_leaves

    before = {it.name: it.pos for it in iter_leaves(lp.doc.items)}
    idx0 = lp._undo.index()

    lp._distribute("h")

    assert {it.name: it.pos for it in iter_leaves(lp.doc.items)} == before, \
        "退化分布不该动几何"
    assert lp._undo.index() == idx0, "退化分布仍推了恒等命令"
    assert "已分布" not in lp.status_label.text(), \
        f"退化分布被播报成成功：{lp.status_label.text()!r}"


def test_r4_real_align_still_works_and_reports_success():
    """**不回归**：真会动的对齐照旧推命令并播报成功。"""
    lp = _page(_rect("a", 0.0, 0.0, 10.0, 10.0, 1.0),
               _rect("b", 50.0, 60.0, 5.0, 5.0, 2.0))
    _select_all(lp)
    idx0 = lp._undo.index()

    lp._align("left")

    assert lp._undo.index() == idx0 + 1, "真移动必须推命令"
    assert "已对齐" in lp.status_label.text(), \
        f"真移动应播报成功：{lp.status_label.text()!r}"
    # 判据用**页面系** bbox（pos 是父系局部量，对齐改的是它，局部坐标不可比）
    xs = sorted(min(unit_paths(leaf, chain)[0][0]
                    for leaf, chain in iter_ancestors(lp.doc.items)
                    if leaf.name in ("a", "b")))
    assert abs(xs[0] - xs[1]) < 1e-6, f"左对齐后两个图元的页面 min-x 应相等，实得 {xs}"
