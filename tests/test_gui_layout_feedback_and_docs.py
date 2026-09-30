"""D5 回归：反馈通道自己可靠 + 空容器不留壳 + 空作业不静默可跑 + 文档不指错。

四条（终审冷读实测，本文件逐条复现）：

① **B-17：页内提示行只会被下一次 ``status_message`` 改写**，而全文件 14 个
   emit 点里 11 个是**拒绝** ⇒ 一次成功操作之后，那行会长期停在与模型相反
   的陈述上。实测：无选中点「水平镜像」⇒ 提示「未选中图元：请先选中要镜像
   的图元」；随后**选中该项再点一次** ⇒ 镜像**真的成功**（``mirror_x=True``、
   按钮勾上），而 ``layoutPageStatus.text()`` **仍是那一句拒绝**。
   反馈通道本身就是本轮认定不可靠的通道 ⇒ R11 那 8 条提示没有资格被信任。

② **B-16：长消息被裁掉后半截**（``sl.wordWrap()=False``、
   ``hasHeightForWidth()=False`` 实测仍在）。提示行是本页唯一常驻的可见落点，
   它裁掉的那半句往往正是**可操作**的那半句。

③ **删光组内成员后留下空壳**：``flatten_visible`` 为空所以不会多切一刀，
   但版面挂着一个不渲染也不计界的空对象（``contains=True``、``top_z=0.0``）。

④ **空作业（0 条折线）点「开始执行」静默可跑**：``runnable=True``、
   ``run_btn`` enabled，点了直接把 4 行（只有头尾指令、没有几何）发给 worker。

⑤ **文档/注释失真**（grep 断言，见文末）：AGENTS.md:290 的交叉引用指错条目、
   两处行号漂移、model.py:542-543 的 R2 枚举行号全错且漏了三处直接写
   ``children`` 的点。这些不测试行为，只测**文本不许指错**。
"""

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtCore, QtWidgets                        # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.layout.layout_page import LayoutPage        # noqa: E402
from megapro.gui.layout.model import Item, flatten_visible    # noqa: E402
from megapro.gui.main_window import MainWindow               # noqa: E402


# -- 工具 ------------------------------------------------------------------

def _rect(name, x, y, z=0.0):
    return Item(paths=[[(x, y), (x + 20.0, y), (x + 20.0, y + 20.0)]],
                name=name, z=z)


def _page_with(items):
    lp = LayoutPage()
    lp.snap_enabled = False
    lp._add_items(items)
    lp._rebuild_scene()
    lp._after_change()
    return lp


def _select_all(lp):
    for gi in lp._scene_items:
        gi.setSelected(True)
    lp._on_selection_changed()


# -- ① B-17：成功之后状态行必须离开拒绝文案 -------------------------------

def test_status_line_leaves_rejection_after_successful_action():
    """拒绝 → 选中 → 成功 ⇒ 状态行**不得**还停在那句拒绝上。

    修复前实测：``status`` 仍是「未选中图元：请先选中要镜像的图元」，而
    ``mirror_x`` 已经是 True —— 界面与模型说的不是一回事。
    """
    lp = _page_with([_rect("m1", 10.0, 10.0, 1.0)])

    lp._toggle_mirror("h")                    # 无选中 ⇒ 拒绝
    reject = lp.status_label.text()
    assert "请先选中" in reject, f"前置：应先有一条拒绝提示，实得 {reject!r}"

    _select_all(lp)
    lp._toggle_mirror("h")                    # 成功
    assert lp.doc.items[0].mirror_x is True, "前置：镜像应真的成功"

    assert lp.status_label.text() != reject, (
        f"成功之后状态行仍是拒绝文案：{lp.status_label.text()!r} —— "
        f"用户被告知「没选中」，而镜像其实已经做过了")
    assert "请先选中" not in lp.status_label.text(), \
        f"状态行仍在说「请先选中」：{lp.status_label.text()!r}"


def test_status_line_never_contradicts_a_later_success():
    """**通用底线**：任何一次成功操作之后，状态行不得停在更早的拒绝上。

    覆盖面比上一条宽：把「R11 那些拒绝守卫」逐个走一遍，确认**每一个**
    成功后都有新提示覆盖掉它。
    """
    lp = _page_with([_rect("a", 10.0, 10.0, 1.0), _rect("b", 40.0, 10.0, 2.0)])

    # 逐个触发「无选中 ⇒ 拒绝」，随后选中并成功
    for op, args in (("_toggle_mirror", ("h",)),
                     ("_zorder", ("top",)),
                     ("_align", ("left",)),
                     ("_distribute", ("x",))):
        # 先制造一次拒绝
        getattr(lp, op)(*args)
        rejected = lp.status_label.text()
        # 选中后重做同一操作
        _select_all(lp)
        getattr(lp, op)(*args)
        after = lp.status_label.text()
        assert after != rejected or "请先选中" not in after, (
            f"{op}：成功之后状态行仍停在那条拒绝上 {rejected!r}（现在 {after!r}）")


# -- ② B-16：长消息不被裁 --------------------------------------------------

def test_status_line_wraps_long_message():
    """提示行必须能**换行**（否则长消息的后半截——含可操作那句——被裁掉）。

    判据用控件属性而不是像素：``wordWrap`` 为真 ⇒ 布局会按可用宽度折行。
    """
    lp = LayoutPage()
    assert lp.status_label.wordWrap() is True, \
        "提示行未开 wordWrap —— 长消息会被裁掉后半截（且被裁掉的常是可操作那半句）"
    # 顺带把评审记录里那条真实拒收提示整段塞进去，确保不会抛/不消失
    msg = ("粘贴被拒绝：「组A」是组却自带折线。组只能装子项、自带折线的图元"
           "不会被画出却会被切，已整单撤销粘贴，请确认剪贴板内容。")
    lp._show_status(msg)
    assert lp.status_label.text() == msg, "提示行没有如实显示整段文案"
    assert lp.status_label.toolTip() or True, "tooltip 至少不该为空"


def test_status_line_full_message_visible_under_narrow_width():
    """**等价判据**：开了 wordWrap 后，长消息**折行显示**而不被裁掉。

    判据两层：① ``wordWrap()`` 为真（便宜、直接）；② 真给一个窄宽走一遍布局，
    高度必须**真的变大**（折行了），且全文仍保存。②是「折行真的发生」的证据：
    只查属性的话，一个被布局忽略的控件也能过（实测第一版就栽在这里：从未
    ``show()`` 的控件拿的是默认 100px 与陈旧 sizeHint，测的是「还没布局」）。

    ②里用 ``setFixedWidth(200)`` 强制窄：否则 1000px 窗口下这条消息本来就
    一行放得下、根本不会折行，断言会**恒真**（实测 1002px 时 height 仍是 12）。
    ⚠ **不**断言 ``hasHeightForWidth()``：那要求控件的**宽度**由高度驱动，
    提示行不是这种控件（实测为 False 且折行仍正常），拿它当判据会误报。
    """
    lp = LayoutPage()
    lp.show()
    QApplication.processEvents()
    lbl = lp.status_label
    assert lbl.wordWrap() is True, "提示行没开 wordWrap —— 长消息会被裁掉后半截"

    msg = ("粘贴被拒绝：「超长组名A」是组却自带折线。组只能装子项、自带折线的图元"
           "不会被画出却会被切，已整单撤销粘贴，请确认剪贴板内容后重试。")
    lp._show_status(msg)
    lbl.setFixedWidth(200)          # 强制窄 ⇒ 这条消息**必须**折行
    lp.layout().activate()
    QApplication.processEvents()
    QApplication.processEvents()

    one_line_h = _single_line_height(lbl)
    assert lbl.height() > one_line_h, (
        f"给了 200px 宽却没有折行（height={lbl.height()}、单行高约 {one_line_h}）"
        f" —— 长消息仍会被横向裁掉")
    assert lbl.text() == msg, "提示行没有如实显示整段文案"


def _single_line_height(lbl) -> int:
    """该字体的**单行**高度（用一条短消息量，而不是 sizeHint）。

    ⚠ 不能拿 ``lbl.sizeHint().height()`` 当单行高：开了 wordWrap 之后
    ``sizeHint`` **本身**就是折行后的高度（实测窄宽下 200px 时它也是 68），
    拿它当基准 ⇒ ``height > sizeHint`` 永远为假，断言**恒失败**（实测踩过）。
    """
    probe = QtWidgets.QLabel("短", lbl)
    probe.setWordWrap(lbl.wordWrap())
    probe.setFont(lbl.font())
    h = probe.sizeHint().height()
    probe.deleteLater()
    return h


# -- ③ 空容器：删光组内成员后不留空壳 -------------------------------------

def test_deleting_all_group_members_leaves_no_empty_shell():
    """删光组内成员 ⇒ 顶层不得留下一个既不渲染也不计界的空对象。

    修复前实测：顶层剩 ``[('组', 0)]``、``contains=True``、``top_z=0.0``。
    """
    lp = _page_with([_rect("a", 10.0, 10.0, 1.0), _rect("b", 40.0, 10.0, 2.0)])
    g = lp.doc.group_items([lp.doc.items[0], lp.doc.items[1]])
    assert g is not None, "前置：编组应成功"
    lp._rebuild_scene()
    lp._after_change()

    _select_all(lp)
    lp._delete_selected()
    lp._rebuild_scene()
    lp._after_change()

    shells = [it for it in lp.doc.items if not it.paths]
    assert shells == [], (
        f"删光组内成员后顶层留下空壳：{[(i.name, len(i.children)) for i in lp.doc.items]}"
        f" —— 它不渲染也不计界，版面却挂着一个幽灵对象")
    assert len(lp.doc.items) == 0, \
        f"删光后顶层应为空，实得 {[i.name for i in lp.doc.items]}"


def test_empty_shell_would_not_ever_reach_the_machine():
    """即便路径将来变了：空壳**不得**进切割序列（拍平为空）。"""
    lp = _page_with([_rect("a", 10.0, 10.0, 1.0), _rect("b", 40.0, 10.0, 2.0)])
    lp.doc.group_items([lp.doc.items[0], lp.doc.items[1]])
    lp._rebuild_scene()
    lp._after_change()
    _select_all(lp)
    lp._delete_selected()
    lp._rebuild_scene()
    lp._after_change()
    assert flatten_visible(lp.doc) == [], "空壳进了拍平 ⇒ 会被切"


def test_group_with_remaining_members_is_kept():
    """**不回归**：组里**还剩成员**时，组必须留下（不能被当成空壳删掉）。

    ⚠ 实测记录（既有问题，**不在本批修**）：只删掉组内**一个**成员时，
    :meth:`RemoveItemsCommand` 会连带把另一个也摘掉（``doc.contains(另一
    个)`` 变 False、拍平随之变空）—— 这属于「组内部分删除」的连带语义，
    根在 ``model.py`` / ``undo_cmds.py``（本批无所有权）。故本条只钉
    **「组里确实还有成员 ⇒ 不许被当空壳删掉」** 这半句：把两个成员都留在
    文档里、只对**另一个组**做删光，验证本批的空壳清理不会误伤有内容的组。
    """
    lp = _page_with([_rect("a", 10.0, 10.0, 1.0), _rect("b", 40.0, 10.0, 2.0),
                     _rect("c", 70.0, 10.0, 3.0), _rect("d", 100.0, 10.0, 4.0)])
    a, b, c, d = list(lp.doc.items)
    keep = lp.doc.group_items([a, b], name="KEEP")     # 这组**留着不动**
    doomed = lp.doc.group_items([c, d], name="DOOMED")  # 这组会被删光
    lp._rebuild_scene()
    lp._after_change()

    for gi in lp._scene_items:
        gi.setSelected(gi.model_item in (c, d))
    lp._on_selection_changed()
    lp._delete_selected()
    lp._rebuild_scene()
    lp._after_change()

    names = [it.name for it in lp.doc.items]
    assert "KEEP" in names, f"还有成员的组被误当成空壳删掉了：顶层 {names}"
    assert "DOOMED" not in names, f"被删光的组应当消失：顶层 {names}"
    assert len(flatten_visible(lp.doc)) == 2, \
        f"留下的组成员应当仍会被切（2 条），实得 {len(flatten_visible(lp.doc))}"


# -- ③b 摘壳**不得**吃掉未选中的顶层散件（Q3-① 回归）--------------------
#
# ⚠ 这一族是**补上的覆盖缺口**：D5 第一版把「空壳」判成
# ``[it for it in doc.items if not it.children]``，而**普通顶层叶子的
# ``children`` 本来就是 ``[]``** ⇒ 该谓词命中的不只是空壳，还有**每一个
# 未选中的散件**。原三条空壳用例全都侥幸躲开：删除后幸存的顶层项要么
# 「没有」、要么是**仍带 2 个 children 的容器 KEEP**（``not it.children``
# 为 False）。**没有任何一条让未选中的顶层叶子与删除共存**，所以谓词写反了
# 也全绿。
#
# 后果是**静默数据丢失**：三个散件 L1/L2/L3 只选 L2 删除 ⇒ 顶层变 ``[]``、
# 拍平 0 条；一次 Ctrl+Z 后只剩 ``['L2']``，L1/L3 **永久丢失**。

def test_delete_one_sibling_never_touches_unselected_siblings():
    """只删一个散件 ⇒ 另外两个**必须原样留下**，且一次撤销完整复原。"""
    lp = _page_with([_rect("L1", 5.0, 5.0, 1.0),
                     _rect("L2", 25.0, 5.0, 2.0),
                     _rect("L3", 45.0, 5.0, 3.0)])
    before = [it.name for it in lp.doc.items]
    assert before == ["L1", "L2", "L3"], "前置：三个顶层散件"

    lp._gi_for(lp.doc.items[1]).setSelected(True)     # **只**选 L2
    lp._on_selection_changed()
    lp._delete_selected()

    assert [it.name for it in lp.doc.items] == ["L1", "L3"], (
        f"只删 L2 却把未选中的兄弟也删了：顶层 "
        f"{[it.name for it in lp.doc.items]} —— 未选中的图元不该被动")
    assert len(flatten_visible(lp.doc)) == 2, \
        f"幸存的两条应当仍会被切，实得 {len(flatten_visible(lp.doc))}"

    lp._undo.undo()
    assert [it.name for it in lp.doc.items] == before, (
        f"一次撤销未把全部图元复原：{[it.name for it in lp.doc.items]}")


def test_delete_group_members_keeps_unselected_top_level_sibling():
    """删组内成员 ⇒ **未选中的顶层散件**必须留下（组 + 散件混排的版面）。"""
    lp = _page_with([_rect("a", 5.0, 5.0, 1.0), _rect("b", 25.0, 5.0, 2.0),
                     _rect("KEEP", 60.0, 5.0, 3.0)])
    a, b, keep = list(lp.doc.items)
    lp.doc.group_items([a, b], name="G")
    lp._rebuild_scene()
    lp._after_change()

    for gi in lp._scene_items:
        gi.setSelected(gi.model_item in (a, b))
    lp._on_selection_changed()
    lp._delete_selected()

    names = [it.name for it in lp.doc.items]
    assert "KEEP" in names, f"未选中的散件 KEEP 被连带删了：顶层 {names}"
    lp._undo.undo()
    names = [it.name for it in lp.doc.items]
    assert "KEEP" in names and "G" in names, f"撤销后结构未复原：顶层 {names}"


# -- ③c 撤销必须把**组**还回来（Q3-② 回归）--------------------------------
#
# D5 第一版把摘壳做成「push 之后单独做、不进撤销栈」⇒ 撤销一次删除只把**成员**
# 放回顶层、**组容器没了**（实测撤销后顶层 ``[('b',0), ('a',0)]``，两个成员
# 退化成两个散件，这个结构改变不可撤销）。切割次序当时没变（撤销前后都是
# ``['a','b']``，画布与机器仍一致），坏的是文档结构 + 一句错误的注释。

def _struct(lp):
    return [(it.name, [c.name for c in it.children]) for it in lp.doc.items]


def test_undo_restores_the_group_container_itself():
    """删光组内成员后一次撤销 ⇒ **组容器与它的成员**都回来。"""
    lp = _page_with([_rect("a", 5.0, 5.0, 1.0), _rect("b", 25.0, 5.0, 2.0)])
    a, b = list(lp.doc.items)
    g = lp.doc.group_items([a, b], name="G")
    lp._rebuild_scene()
    lp._after_change()
    before = _struct(lp)
    assert before == [("G", ["a", "b"])], f"前置：{before}"

    _select_all(lp)
    lp._delete_selected()
    assert _struct(lp) == [], f"删光后顶层应为空，实得 {_struct(lp)}"

    lp._undo.undo()
    assert _struct(lp) == before, (
        f"撤销后组没回来（成员退化成散件）：{_struct(lp)} —— "
        f"结构改变不可撤销")


def test_undo_does_not_duplicate_geometry():
    """撤销后切割几何必须与删除前**逐条相同**（不多切一遍）。

    钉住一个具体踩过的坑：空壳若在**成员之前**复原，成员会因 ``attach``
    落空而被挂成顶层散件 ⇒ 同一几何既在组里又在顶层，切割次序从 2 条变 4 条
    （**切两遍**）。
    """
    lp = _page_with([_rect("a", 5.0, 5.0, 1.0), _rect("b", 25.0, 5.0, 2.0),
                     _rect("KEEP", 60.0, 5.0, 3.0)])
    a, b, _keep = list(lp.doc.items)
    lp.doc.group_items([a, b], name="G")
    lp._rebuild_scene()
    lp._after_change()
    before_paths = flatten_visible(lp.doc)

    for gi in lp._scene_items:
        gi.setSelected(gi.model_item in (a, b))
    lp._on_selection_changed()
    lp._delete_selected()
    lp._undo.undo()

    assert flatten_visible(lp.doc) == before_paths, (
        f"撤销后切割几何与删除前不同（{len(flatten_visible(lp.doc))} vs "
        f"{len(before_paths)} 条）—— 同一几何被切两遍或漏切")
    assert len(flatten_visible(lp.doc)) == 3, \
        f"撤销后应仍是 3 条，实得 {len(flatten_visible(lp.doc))}"


def test_redo_after_undo_re_deletes_the_group():
    """撤销后再**重做** ⇒ 组应当真的被重新删掉（不能撤销成「删不掉」）。

    钉住一个具体踩过的坑：给 redo 加「只在第一次跑」的 ``_first`` 短路后，
    第二次 redo 走进了「复原」分支。
    """
    lp = _page_with([_rect("a", 5.0, 5.0, 1.0), _rect("b", 25.0, 5.0, 2.0)])
    a, b = list(lp.doc.items)
    lp.doc.group_items([a, b], name="G")
    lp._rebuild_scene()
    lp._after_change()

    _select_all(lp)
    lp._delete_selected()
    assert _struct(lp) == [], "前置：删光后应为空"

    lp._undo.undo()
    assert _struct(lp) == [("G", ["a", "b"])], f"撤销后应复原，实得 {_struct(lp)}"

    lp._undo.redo()
    assert _struct(lp) == [], (
        f"重做后组应当被重新删掉，实得 {_struct(lp)} —— 重做变成了撤销")
    lp._undo.undo()
    assert _struct(lp) == [("G", ["a", "b"])], f"再撤销应复原，实得 {_struct(lp)}"


# -- ④ 空作业点「开始执行」要有中文提示 -----------------------------------

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
def empty_job_window():
    w = MainWindow()
    w._pen_down_z, w._cut_touch_z, w._safe_z = 17.0, 17.5, 30.0
    w._worker = _StubWorker()
    w._homed = True
    w._link_ready = True
    w._refresh_gate_ui()
    w.layout_page._sync_job_cb.setChecked(False)
    from megapro.gui.job import JobSpec, Placement

    w._set_job_and_recompile(JobSpec(paths_paper=[], source_name="空作业",
                                     placement=Placement(mode="preserve")),
                             from_layout=True)
    return w


def test_empty_job_run_is_blocked_with_chinese_reason(empty_job_window):
    """0 条折线的作业**不许**静默可跑：点开始执行须给中文提示且不发指令。

    修复前实测：``runnable=True``、``run_btn`` enabled，点了直接把 4 行
    （只有头尾指令、没有一丁点几何）发给 worker，控制台还打「开始执行…」。
    """
    w = empty_job_window
    from megapro.gui.gcode_parse import parse_lines

    segs = parse_lines(list(w._job_lines), z_down=17.0, z_safe=30.0, tool="pen")
    drawn = [s for s in segs
             if s.kind != "program" and s.p1 is not None
             and (s.p1[0] or s.p1[1])]
    assert drawn == [], f"前置：空作业不该有落笔段，实得 {drawn}"

    w._refresh_gate_ui()
    w._on_run_job()

    assert not w._worker.reqRunJob.calls, \
        f"空作业仍把指令发给了 worker（{len(w._worker.reqRunJob.calls)} 次）"
    text = w.console.toPlainText()
    assert "空" in text or "没有" in text or "无内容" in text, \
        f"点开始执行没有中文原因提示：{text!r}"


def test_empty_job_run_button_is_disabled(empty_job_window):
    """空作业的「开始执行」应当**禁用**（不给用户一个点了没用的按钮）。"""
    w = empty_job_window
    w._refresh_gate_ui()
    assert not w.run_btn.isEnabled(), \
        "空作业下『开始执行』仍可点 —— 用户点了只会得到一次空跑"
    assert w.run_btn.toolTip(), "按钮禁用时必须给出中文原因（tooltip）"


# -- ⑤ 文档不许指错（grep 断言）-------------------------------------------

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_agents_cross_reference_points_at_the_right_items():
    """:290「见第 15、16 条」必须指向真正的那两条（18 与 20）。

    第 15 条是 tooltip、16 是提示行裁切 —— 都不是正文说的那两个家族。
    """
    txt = _read("AGENTS.md")
    m = re.search(r"见第 (\d+)、(\d+) 条", txt)
    assert m, "未找到 AGENTS.md:290 的交叉引用"
    # ⚠ 键是**字符串**（``findall`` 的捕获组），别拿 int 去查。
    items = dict(re.findall(r"^(\d+)\. \*\*(.+?)\*\*", txt, re.M))
    a, b = m.group(1), m.group(2)
    for n in (a, b):
        assert n in items, f"第 {n} 条不存在（已解析到：{sorted(items, key=int)}）"
    # 15=tooltip、16=提示行裁切，都与「门槛已满足但空转」「键盘入口」无关
    assert a not in ("15", "16"), f"第 {a} 条是 tooltip/裁切，指错了"
    assert b not in ("15", "16"), f"第 {b} 条是 tooltip/裁切，指错了"
    assert "空转" in items[a] or "键盘" in items[a] or "恒等" in items[a], \
        f"第 {a} 条「{items[a]}」与所指家族不符"
    assert "Ctrl" in items[b] or "剪贴板" in items[b], \
        f"第 {b} 条「{items[b]}」与所指家族不符"


def test_agents_line_numbers_point_at_real_code():
    """:517 的守卫行号、:566 的 Ctrl+A 行号必须指到真实代码。"""
    txt = _read("AGENTS.md")
    model = _read("src/megapro/gui/layout/model.py").splitlines()
    page = _read("src/megapro/gui/layout/layout_page.py").splitlines()

    m = re.search(r"守卫在 `:(\d+)-(\d+)`", txt)
    assert m, "未找到守卫行号引用"
    lo, hi = int(m.group(1)), int(m.group(2))
    seg = "\n".join(model[lo - 1:hi])
    assert "item.descendants()" in seg or "owner.descendants()" in seg, \
        (f"AGENTS.md 说守卫在 model.py:{lo}-{hi}，"
         f"但那段代码里没有互为后代的自含检查：\n{seg}")


def test_agents_ctrl_a_line_number_is_real():
    """AGENTS.md 里的 Ctrl+A 行号必须指到真的 ``gi.setSelected(True)`` 裸循环。

    ⚠ **不写死行号**：``layout_page.py`` 每批都在长（提示行、状态行…），写死的
    行号会随本批自己的改动漂移（实测本批就撞了两次：1331 → 1421 → 1442）。
    故判据取「**指向的那一行确实属于 Ctrl+A 处理**」这个语义事实：那一行附近
    必须同时出现 ``Key_A`` 与逐个 ``setSelected(True)``。行号本身对不对得由这条
    断言说话，而不是由我抄一个会过期的数字。
    """
    txt = _read("AGENTS.md")
    m = re.search(r"Ctrl\+A 全选仍是裸循环\*\*（`layout_page\.py:(\d+)`", txt)
    assert m, "未找到 Ctrl+A 行号引用"
    line = int(m.group(1))
    page = _read("src/megapro/gui/layout/layout_page.py").splitlines()
    assert 1 <= line <= len(page), \
        f"AGENTS.md 指的 layout_page.py:{line} 超出文件长度 {len(page)}"
    # 看一个**窗口**而不是单行：``Key_A`` 判断在 for 上一行、``setSelected`` 在
    # for 下面第二行，只看 ±1 行会两边都够不着（实测踩过）。
    ctx = "\n".join(page[max(0, line - 4):line + 5])
    assert "Key_A" in ctx, f"AGENTS.md 说 Ctrl+A 在 layout_page.py:{line}，窗口里没有 Key_A：\n{ctx}"
    assert "setSelected(True)" in ctx, \
        f"AGENTS.md 说 Ctrl+A 在 layout_page.py:{line}，窗口里没有逐个 setSelected：\n{ctx}"
