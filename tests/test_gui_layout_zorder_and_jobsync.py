"""C2 回归：混选层序穿组 + 排版页编辑同步作业预览。

两个独立缺陷（均实测复现，非读码推断）：

**缺陷一 · 混选置顶/置底时 z 穿组。** ``_zorder("top"/"bottom")`` 的基准
``self.doc.top_z() + 1`` 写在**每个操作单元的循环体内**，而变更统一到循环
**之后**才一次 ``push``。于是整个循环里 ``top_z()`` 读到的是同一个值：单元
**内部**递推、单元**之间**不递推 ⇒ 多个操作单元被摆到同一段 z 上，散件插进
组的两个成员之间。实测（X 散件 z=50 未选中；组 G{M0(z=1), M1(z=2)}；散件 M2
(z=3)；全选 G+M2 置顶）：

    修复前  M0=51, M1=52, M2=51   ← M2 与 M0 同 z，穿进组里
    修复后  M0=51, M1=52, M2=53   ← 选中集 {51,52,53} 连续一段，与 X(50) 不交叠

拍平序 = 切割次序（:func:`flatten_visible` 走 ``key=z`` 稳定排序），所以这不是
画布上的显示问题，是**发往机器的 G-code 次序变了**。

**缺陷二 · 排版页编辑不同步作业预览。** 镜像/移动/新增/删除四类编辑一律不发
``job_sync_requested``（上一轮只接了页切换这一个触发点）：用户在排版页改完、切
到作业页看到的是**陈旧几何**，而且可以照着直接执行 —— 下一刀切的是另一份内容。
作业页改参数（feed）会正常走 ``_recompile``，故这是「排版页→作业页」这条通道的
系统性缺口。

触发点挂在**撤销栈**（``QUndoStack.indexChanged``）而不是逐个编辑入口：拖动的
``MoveItemsCommand`` 是从 ``canvas/items.py`` 直接 push 到 ``page._undo`` 的，
而 ``canvas/`` 不在本线文件所有权内，逐点挂钩**抓不到最常见的「移动」**。
``indexChanged`` 覆盖四类必需编辑（镜像=ChangeItemProps / 移动=MoveItems（微调
与拖动）/ 新增=AddItems / 删除=RemoveItems），顺带覆盖粘贴复制、撤销重做、层序、
对齐缩放旋转 —— 它们的共同点是**都改变了 ``flatten_visible(doc)``**，即作业预览
显示的那份东西；层序尤其：它改的是切割次序，比镜像更该同步。作业页那侧的两道
门禁（来源 + 执行中）已由 :meth:`MainWindow._on_layout_job_sync` 既有实现承担，
本文件不另发明。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# ============================ 缺陷一：混选层序 ==============================


def _L(x, z, name):
    """一条水平线（x 决定几何位置与身份，z 决定层序）。"""
    from megapro.gui.layout.model import Item

    return Item(paths=[[(float(x), 0.0), (float(x) + 5.0, 0.0)]],
                name=name, z=float(z))


def _zs(doc):
    """全树叶子的 name → z 映射。"""
    from megapro.gui.layout.model import iter_leaves

    return {it.name: it.z for it in iter_leaves(doc.items)}


def _select_all_but(lp, keep: set, doc_items):
    """场景里勾选除 ``keep`` 外的全部图元（组编辑态外的普通态选择）。"""
    for it in doc_items:
        gi = lp._gi_for(it)
        if gi is not None:
            gi.setSelected(it not in keep)


def _mixed_page():
    """X(散件 z=50，未选中) + 组 G{M0(z=1), M1(z=2)} + 散件 M2(z=3)。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    x = _L(0, 50, "X")
    m0, m1 = _L(10, 1, "M0"), _L(20, 2, "M1")
    m2 = _L(30, 3, "M2")
    lp._add_items([x, m0, m1, m2])
    g = lp.doc.group_items([m0, m1], name="G")
    assert g is not None
    return lp, {"X": x, "M0": m0, "M1": m1, "M2": m2, "G": g}


def _select_mixed(lp, by_name):
    """全选 G（= 勾它两个叶子）**和** 散件 M2 —— 就是「混选」。"""
    for n in ("M0", "M1", "M2"):
        lp._gi_for(by_name[n]).setSelected(True)


def test_mixed_top_keeps_selection_in_one_block_above_unselected():
    """置顶：所有选中叶子严格高于所有未选中，且占**连续一段**。"""
    lp, by = _mixed_page()
    _select_mixed(lp, by)

    lp._zorder("top")

    z = _zs(lp.doc)
    sel = [z["M0"], z["M1"], z["M2"]]
    assert min(sel) > z["X"], \
        f"选中集未整体高于未选中：{z}（散件穿到了组成员之下）"
    assert len(set(sel)) == 3, f"选中集内部 z 交叠（未占连续段）：{z}"
    # 连续段：max - min == 选中叶子数 - 1
    assert max(sel) - min(sel) == 2.0, f"选中集不是连续 z 段：{z}"


def test_mixed_top_preserves_group_internal_order():
    """置顶后组内相对次序不变（M1 仍在 M0 之上）。"""
    lp, by = _mixed_page()
    _select_mixed(lp, by)
    before = _zs(lp.doc)

    lp._zorder("top")

    z = _zs(lp.doc)
    assert z["M1"] > z["M0"], f"组内相对次序被破坏：{z}"
    assert (z["M1"] - z["M0"]) == pytest.approx(
        before["M1"] - before["M0"]), "组内间距被改变"


def test_mixed_bottom_keeps_selection_in_one_block_below_unselected():
    """置底：所有选中叶子严格低于所有未选中，且占连续一段。"""
    lp, by = _mixed_page()
    _select_mixed(lp, by)

    lp._zorder("bottom")

    z = _zs(lp.doc)
    sel = [z["M0"], z["M1"], z["M2"]]
    assert max(sel) < z["X"], f"选中集未整体低于未选中：{z}"
    assert len(set(sel)) == 3, f"选中集内部 z 交叠：{z}"
    assert max(sel) - min(sel) == 2.0, f"选中集不是连续 z 段：{z}"
    assert z["M1"] > z["M0"], f"置底破坏了组内相对次序：{z}"


def test_mixed_top_flatten_order_puts_selection_last():
    """拍平（=切割次序）：未选中在前，选中整块在后，且**组内相邻不被拆散**。

    ⚠ 这里**刻意不断言「M0 一定排在 M2 前面」**：``_selected()`` 走
    ``scene.selectedItems()``，Qt 不保证返回顺序（实测同一用例逐次运行会在
    ``[X,M0,M1,M2]`` 与 ``[X,M2,M0,M1]`` 间跳变）。独立操作单元之间的相对次序
    属**会变的量**，不是会出错的事；本条只钉真正会出错的三件：未选中在前、
    选中整块连续、组内两成员相邻。

    修复前的缺陷（[X, M0, M2, M1]）照样被「组内相邻」抓住。
    """
    from megapro.gui.layout.model import flatten_visible

    lp, by = _mixed_page()
    _select_mixed(lp, by)
    lp._zorder("top")

    order = [round(p[0][0], 6) for p in flatten_visible(lp.doc)]
    x_of = {n: by[n].paths[0][0][0] for n in ("X", "M0", "M1", "M2")}

    assert order[0] == pytest.approx(x_of["X"]), f"未选中项未排在最前：{order}"
    block = order[1:]
    assert sorted(block) == sorted(x_of[n] for n in ("M0", "M1", "M2")), \
        f"选中块不是那三项：{order}"
    assert abs(block.index(x_of["M1"]) - block.index(x_of["M0"])) == 1, \
        f"组内被打散（散件插在 M0 与 M1 之间）：{order}"


def test_mixed_top_is_still_a_single_undo_command():
    """单命令契约不变：一次撤销把**所有**选中项一起还原。"""
    lp, by = _mixed_page()
    _select_mixed(lp, by)
    before_z = dict(_zs(lp.doc))
    before_n = lp._undo.count()

    lp._zorder("top")
    assert lp._undo.count() == before_n + 1, "层序没有且仅有一条撤销命令"
    assert _zs(lp.doc) != before_z, "置顶后 z 没变（本用例空跑）"

    lp._undo.undo()
    assert _zs(lp.doc) == before_z, "一次撤销未还原全部选中项"


def test_mixed_top_three_units_stay_contiguous():
    """三个操作单元（2 组 + 1 散件）也必须占连续一段。"""
    lp, by = _mixed_page()
    n0, n1 = _L(40, 4, "N0"), _L(50, 5, "N1")
    lp._add_items([n0, n1])
    lp.doc.group_items([n0, n1], name="H")
    by.update(N0=n0, N1=n1)
    for n in ("M0", "M1", "M2", "N0", "N1"):
        lp._gi_for(by[n]).setSelected(True)

    lp._zorder("top")

    z = _zs(lp.doc)
    sel = [z[n] for n in ("M0", "M1", "M2", "N0", "N1")]
    assert min(sel) > z["X"], f"选中集未整体高于未选中：{z}"
    assert len(set(sel)) == 5, f"选中集内部 z 交叠：{z}"
    assert max(sel) - min(sel) == 4.0, f"选中集不是连续 z 段：{z}"
    assert z["M1"] > z["M0"] and z["N1"] > z["N0"], f"组内相对次序被破坏：{z}"


def test_top_unaffected_for_single_group_selection():
    """只选整组（无散件混选）⇒ 与基线同口径：压在全树最上、组内保序。"""
    lp, by = _mixed_page()
    for n in ("M0", "M1"):
        lp._gi_for(by[n]).setSelected(True)

    lp._zorder("top")

    z = _zs(lp.doc)
    assert min(z["M0"], z["M1"]) > z["X"] and min(z["M0"], z["M1"]) > z["M2"]
    assert z["M1"] > z["M0"], "组内相对次序被破坏"


def test_up_down_still_unchanged_by_the_zorder_fix():
    """上移/下移走的是各单元自身 z ±1，本次修复不得动它。"""
    lp, by = _mixed_page()
    for n in ("M0", "M1", "M2"):
        lp._gi_for(by[n]).setSelected(True)
    before = _zs(lp.doc)

    lp._zorder("up")

    z = _zs(lp.doc)
    for n in ("M0", "M1", "M2"):
        assert z[n] == pytest.approx(before[n] + 1.0), \
            f"上移语义被改：{n} {before[n]} -> {z[n]}"


# ============================ 缺陷二：编辑同步 ==============================

#: 作业页持有的排版作业几何（页面 mm，全部在 0..210 内 ⇒ 过 C1 越床闸）。
_BASE_PATHS = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]]
#: 镜像后的同一条路径（绕本地 bbox 中心 x=5 翻）⇒ 几何可观测地变化。
_MIRRORED = [[(10.0, 0.0), (0.0, 0.0), (0.0, 10.0)]]

#: 文件作业几何（与 _BASE_PATHS 互不重叠 ⇒ 任何混淆都能被断言抓住）。
_FILE_PATHS = [[(150.0, 60.0), (200.0, 60.0)]]
_FILE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="210" height="210" \
viewBox="0 0 210 210">
  <polyline points="150,150 200,150" fill="none" stroke="#000000" \
stroke-width="1"/>
</svg>
"""


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


def _drag_via_mouse(view, p0_mm, p1_mm):
    """QTest 真事件拖拽（与既有用例同口径：mm → 视口 px 走浮点变换后取整）。"""
    def vp(x, y):
        return view.viewportTransform().map(QPointF(float(x), float(y))).toPoint()

    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=vp(*p0_mm))
    QTest.mouseMove(view.viewport(), vp(*p1_mm))
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=vp(*p1_mm))


def _new_page_with_one_item():
    """一个排版页 + 一个选中图元 p（开快照捕获器）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[list(q) for q in _BASE_PATHS], name="p")
    lp._add_items([it])
    lp._gi_for(it).setSelected(True)
    got: list = []
    lp.job_sync_requested.connect(got.append)
    return lp, it, got


def _paths(spec):
    return [list(p) for p in spec.paths_paper]


def _mirror(lp, it):
    lp._toggle_mirror("h")


def _move_nudge(lp, it):
    lp._nudge(Qt.Key_Right, False)


def _move_drag(lp, it):
    """拖动（真事件）。``MoveItemsCommand`` 由 canvas/items.py 直接 push 到
    ``page._undo`` —— 逐点挂钩抓不到这条最常见的移动路径。"""
    lp.snap_enabled = False
    before = it.pos
    _drag_via_mouse(lp.view, (before[0] + 5.0, before[1] + 5.0),
                    (before[0] + 25.0, before[1] + 5.0))
    assert it.pos[0] > before[0], "拖动未生效（本用例空跑）"


def _add(lp, it):
    from megapro.gui.layout.model import Item

    lp._add_items([Item(paths=[[(30.0, 30.0), (40.0, 30.0)]], name="q")])


def _delete(lp, it):
    lp._delete_selected()


#: 四类必需编辑（＋拖动这一条最常见的移动路径）。
_EDITS = {
    "镜像": _mirror,
    "移动(微调)": _move_nudge,
    "移动(拖动)": _move_drag,
    "新增": _add,
    "删除": _delete,
}


# --- 触发层：裸 LayoutPage，发没发信号 -------------------------------------

@pytest.mark.parametrize("name", sorted(_EDITS))
def test_edit_emits_job_sync_when_switch_on(name):
    """开关开 ⇒ 四类编辑各发一次静默同步，且发的是**当前**版面几何。"""
    from megapro.gui.layout.model import flatten_visible

    lp, it, got = _new_page_with_one_item()
    lp._sync_job_cb.setChecked(True)
    before = len(got)

    _EDITS[name](lp, it)

    assert len(got) == before + 1, f"{name} 未触发作业预览同步（陈旧几何）"
    assert _paths(got[-1]) == flatten_visible(lp.doc), \
        f"{name} 同步的 JobSpec 与当前版面不一致"


@pytest.mark.parametrize("name", sorted(_EDITS))
def test_edit_does_not_emit_when_switch_off(name):
    """开关关 ⇒ 四类编辑一律不发（开关语义与页切换那条一致）。"""
    lp, it, got = _new_page_with_one_item()
    lp._sync_job_cb.setChecked(False)
    before = len(got)

    _EDITS[name](lp, it)

    assert len(got) == before, f"开关关时 {name} 仍发了同步信号"


def test_undo_redo_also_resyncs():
    """撤销/重做同样改变版面 ⇒ 也得同步（否则一次 Ctrl+Z 就留下陈旧预览）。"""
    lp, it, got = _new_page_with_one_item()
    lp._sync_job_cb.setChecked(True)
    _add(lp, it)
    lp._undo.undo()
    n = len(got)
    assert len(got) == n, "撤销后未同步"
    assert _paths(got[-1]) == _BASE_PATHS, "撤销后同步的几何不是撤销后的版面"


# --- 门禁层：真 MainWindow -------------------------------------------------

def _make_window():
    from megapro.gui.main_window import MainWindow

    w = MainWindow()
    w._pen_down_z, w._cut_touch_z, w._safe_z = 17.0, 17.5, 30.0
    w._worker = _StubWorker()
    w._homed = True
    w._link_ready = True
    w._refresh_gate_ui()
    return w


def _layout_job_window(sync_on=True):
    """真导出通道 ⇒ 作业页握**排版**作业（来源标记置位）。"""
    w = _make_window()
    lp = w.layout_page
    from megapro.gui.layout.model import Item

    lp._sync_job_cb.setChecked(False)
    lp._add_items([Item(paths=[list(q) for q in _BASE_PATHS], name="p")])
    lp._gi_for(lp.doc.items[0]).setSelected(True)
    lp._on_export()                       # 真实导出（经 C1 越床闸，在床内直接过）
    assert w._job_from_layout is True, "本用例前提不成立：作业页握的不是排版作业"
    lp._sync_job_cb.setChecked(sync_on)
    return w, lp, lp.doc.items[0]


def _file_job_window(tmp_path, monkeypatch, sync_on=True):
    """走**真实** ``_on_load_svg`` 载入文件作业（来源标记必须为 False）。"""
    from PySide6 import QtWidgets

    w = _make_window()
    lp = w.layout_page
    from megapro.gui.layout.model import Item

    lp._sync_job_cb.setChecked(False)
    lp._add_items([Item(paths=[list(q) for q in _BASE_PATHS], name="p")])
    lp._gi_for(lp.doc.items[0]).setSelected(True)
    lp._on_export()

    p = tmp_path / "design.svg"
    p.write_text(_FILE_SVG, encoding="utf-8")
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(p), "")))
    w._on_load_svg()
    assert w._job_from_layout is False, "本用例前提不成立：文件作业被标成排版来源"
    assert _paths(w._job_spec) == [list(q) for q in _FILE_PATHS]
    lp._sync_job_cb.setChecked(sync_on)
    return w, lp, lp.doc.items[0]


def _assert_file_job_intact(w, what):
    assert w._job_from_layout is False, f"{what}: 来源标记被翻了"
    assert _paths(w._job_spec) == [list(q) for q in _FILE_PATHS], \
        f"{what}: 文件作业被顶掉了"
    assert w._job_lines, f"{what}: 无 lines"


@pytest.mark.parametrize("name", sorted(_EDITS))
def test_layout_job_resyncs_on_edit(name):
    """作业页握排版作业 + 开关开 ⇒ 编辑后作业页几何跟着变。"""
    w, lp, it = _layout_job_window(sync_on=True)
    before = _paths(w._job_spec)
    lines_before = list(w._job_lines)

    _EDITS[name](lp, it)

    assert _paths(w._job_spec) != before, \
        f"{name} 后作业页几何未更新（用户会切过去执行到陈旧内容）"
    assert list(w._job_lines) != lines_before, f"{name} 后 lines 未重编译"
    w.close()


@pytest.mark.parametrize("name", sorted(_EDITS))
def test_layout_job_untouched_when_switch_off(name):
    """作业页握排版作业 + 开关**关** ⇒ 编辑后作业页保持原样。"""
    w, lp, it = _layout_job_window(sync_on=False)
    before = _paths(w._job_spec)
    lines_before = list(w._job_lines)

    _EDITS[name](lp, it)

    assert _paths(w._job_spec) == before, f"开关关时 {name} 仍同步了作业页"
    assert list(w._job_lines) == lines_before
    w.close()


@pytest.mark.parametrize("name", sorted(_EDITS))
def test_file_job_never_hijacked_by_any_edit(name, tmp_path, monkeypatch):
    """作业页握**文件作业** ⇒ 四类编辑一律不碰它（来源门禁，不得绕过）。"""
    w, lp, it = _file_job_window(tmp_path, monkeypatch, sync_on=True)
    lines_before = list(w._job_lines)
    console_before = w.console.toPlainText()

    _EDITS[name](lp, it)

    _assert_file_job_intact(w, name)
    assert list(w._job_lines) == lines_before, f"{name} 重编译了用户的文件作业"
    assert w.console.toPlainText() == console_before, \
        f"{name} 为「顶掉别人作业」刷了控制台"
    w.close()


def test_edit_during_run_keeps_spec_and_reuses_existing_message():
    """执行中编辑：沿用既有「执行中，参数改动本次作业结束后生效」语义。

    两条硬要求：``_job_spec`` **不得**被换（补编译会拿它去编译刚跑完的作业），
    提示文案与作业页参数改动那条路**同一口径**（不另发明措辞）。
    """
    w, lp, it = _layout_job_window(sync_on=True)
    before = _paths(w._job_spec)
    lines_before = list(w._job_lines)
    console_before = w.console.toPlainText()

    w._job_running = True
    _add(lp, it)

    assert _paths(w._job_spec) == before, "执行中 _job_spec 被换"
    assert list(w._job_lines) == lines_before, "执行中不得重编译"
    assert "执行中" in w.console.toPlainText()[len(console_before):], \
        "执行中编辑未沿用既有的执行中提示"

    w._job_running = False
    w._on_job_done(True)
    assert _paths(w._job_spec) == before, \
        f"补编译把作业换成了编辑后的版面：{_paths(w._job_spec)}"
    w.close()
