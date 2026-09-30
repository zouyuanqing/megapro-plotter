"""D2 回归：越床版面不许在作业页**显示为干净可跑**（含 anchor 洗白那条）。

两条缺陷（终审冷读 + 本文件实测复现）：

① **静默同步这条路本来就没有用户可见的越床提示**。
   ``LayoutPage._confirm_in_bed`` 只有 ``_on_save_svg`` / ``_on_export`` 两个
   调用点；``job_sync_requested``（切页、撤销、任何一次编辑）走
   ``MainWindow._on_layout_job_sync``，全程不碰它。
   ⚠ **但越床仍被拦住了** —— 拦它的是 ``compile_job`` 内部的
   ``check_bounds_v2``（``job.py:441``，在 ``place_at_anchor`` **之后**），
   产出 ``compiled.bounds.violations`` ⇒ ``runnable=False`` ⇒
   ``_update_run_btn`` 禁执行 + ``job_info`` 显示「越界N处(禁执行)」。
   换句话说：**执行安全**没问题，缺的是「作业页能一眼看出这份版面越界」。
   本文件据此把判据定在「不许干净可跑」，而不是「必须弹框」——
   同步是高频动作，弹模态框不可接受（PRD §10.1-8）。

② **anchor 放置把越床几何洗回床内，且无任何界面告知**（本条**真的**坏）。
   排版页一个床内方块 → 送去作业 → 作业页选「锚点归位 bl→(0,0)」→
   回排版页把 X 键入 -40（版面已越界 40mm）→ 静默同步：
   排版页 ``_out_of_bed()`` 红着脸报 40mm，作业页却
   ``violations=()``、``runnable=True``、lines 的 X 范围 (0,30)。
   机器切在 (0,0)-(30,30)，**没有一个界面告诉用户他要去的地方已经变了**。
   根因：``compiled.bounds`` 判的是**放置之后**的几何（正确），而
   「这份版面越界」这件事只存在于排版页的 ``_out_of_bed()``，作业页无从得知。

**为什么不能只靠 bounds**：放置后的落点合法（这正是 anchor 的语义），
「是否越界」与「是否合法落点」是两个问题。把 anchor 禁掉会砸掉一个正当功能
（大版面归位到床内是用户**主动**要的）。故本文件要求的是**告知**而非**拦截**：
作业页必须把「这份版面原本越界、落点已被 anchor 移动」显示出来。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets                        # noqa: E402
from PySide6.QtWidgets import QApplication           # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.job import Placement                # noqa: E402
from megapro.gui.layout.model import Item, flatten_visible  # noqa: E402


# -- 工具 ------------------------------------------------------------------

def _rect(name, x, y, w, h, z=0.0):
    return Item(paths=[[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]],
                pos=(0.0, 0.0), name=name, z=z)


def _win():
    from megapro.gui.main_window import MainWindow

    return MainWindow()


def _sync(win):
    """走真静默同步入口（发 job_sync_requested，不是直接调 handler）。"""
    lp = win.layout_page
    lp.job_sync_requested.emit(lp.to_job_spec())


def _lines_xy_range(win):
    """将发送的 lines 回读出的 ``(X范围, Y范围)``；未标定/空作业 ⇒ None。

    与 :func:`_lines_x_range` 同源，只把两个轴分开返回（落点告知要同时
    说清 X 和 Y，只说一个轴会让「落到哪」仍然含糊）。
    """
    from megapro.gui.gcode_parse import parse_lines

    zm = win._current_zmap()
    if zm is None or not win._job_lines:
        return None, None
    segs = parse_lines(list(win._job_lines), z_down=zm.down_z, z_safe=zm.safe_z,
                       tool=win._tool)
    xs = [c for s in segs for pt in (s.p0, s.p1) if pt for c in (pt[0],)]
    ys = [c for s in segs for pt in (s.p0, s.p1) if pt for c in (pt[1],)]
    return ((min(xs), max(xs)) if xs else None,
            (min(ys), max(ys)) if ys else None)


def _lines_x_range(win):
    """**将发送的同一份 lines** 回读出来的 X 范围（不是模型几何）。

    用 ``_job_lines`` 而不是 ``_job_spec.paths_paper``：lines 才是 worker
    实收的东西，判据必须与它一致（AGENTS.md：预览≡发送，单一真源）。
    """
    return _lines_xy_range(win)[0]


def _lines_y_range(win):
    return _lines_xy_range(win)[1]


def _set_anchor(win, anchor="bl", target=(0.0, 0.0)):
    """作业页选「锚点归位」（模拟用户在下拉里选 + 落点输入）。"""
    from dataclasses import replace

    win._job_spec = replace(win._job_spec,
                            placement=Placement(mode="anchor", anchor=anchor,
                                                target=target))
    win._set_placement_ui("anchor")
    win._recompile()


# -- ① 越床版面 + 同步开关开 → 切页 → 作业页不得显示为干净可跑 -------------

def test_out_of_bed_sync_never_shows_clean_runnable_job(win_sync_ready):
    """缺陷 ①：越床版面经静默同步后，作业页**不得**是「干净可跑」。

    验收底线（ask 原话）：「不能让一个排版页明确说越界的版面，在作业页
    显示为干净可跑」。本条只断言这条底线 —— **不断言弹框**（同步是高频动作，
    弹模态框不可接受），断言的是「要么拦住、要么作业页明说越界」。
    """
    win, lp = win_sync_ready
    # 把版面挪到床外（排版页自己会红着脸说越界）
    lp.doc.items[0].pos = (-40.0, 0.0)
    lp._rebuild_scene()
    lp._after_change()

    over = lp._out_of_bed()
    assert over is not None and over[0] == pytest.approx(40.0), \
        f"前置：排版页应报越界 40mm，实得 {over}"

    _sync(win)

    compiled = win._job_compiled
    assert compiled is not None, "同步后作业页应有编译结果"
    # 底线：要么 bounds 拦住，要么作业页把越界显示出来 —— 两者皆无才是 BUG
    shown = bool(compiled.bounds.violations) or (not compiled.runnable)
    assert shown, (
        f"排版页报越界 40mm，作业页却显示为干净可跑"
        f"（violations={compiled.bounds.violations}, runnable={compiled.runnable}）"
        f" —— 用户会以为可以直接开始执行")
    # 机器安全底线：**禁执行**（``_update_run_btn`` 绑 runnable、``_on_run_job``
    # 再查一次）。越界 G0 在这台机器上是**静默**的（开串口即复位、软限位只
    # 钳位不报错），所以「lines 里有越界坐标」本身不致命，「能不能发出去」才致命。
    win._refresh_gate_ui()
    assert not win.run_btn.isEnabled(), \
        "越界版面下『开始执行』仍可点 —— 越界 G0 静默钳位，刀会真的切到床外"
    # 越界坐标确实进了 lines（说明这条判据不是靠「几何压根没编进去」蒙混过关）
    xr = _lines_x_range(win)
    assert xr is not None and xr[0] < -1e-6, \
        f"前置：越界坐标应出现在将发送的 lines 里，实得 {xr}"


def test_out_of_bed_sync_does_not_pop_modal_dialog(win_sync_ready, monkeypatch):
    """**同步不许弹模态框**（这是本条的另一半要求，也是 ① 的原始动机）。

    切页/撤销/每次编辑都会走同步；弹框 = 把高频动作变成骚扰，且会在
    模态嵌套里卡死整条交互。判据 = ``QMessageBox.exec`` 一次都不被调。
    """
    win, lp = win_sync_ready
    calls = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec",
                        lambda self, *a, **k: calls.append(self) or 0)

    lp.doc.items[0].pos = (-40.0, 0.0)
    lp._rebuild_scene()
    lp._after_change()
    _sync(win)

    assert calls == [], f"静默同步弹了 {len(calls)} 次模态框 —— 高频动作不许弹框"


def test_in_bed_sync_stays_untouched(win_sync_ready):
    """**不回归**：床内版面同步后仍干净可跑（闸门不能变成误报骚扰）。"""
    win, lp = win_sync_ready
    assert lp._out_of_bed() is None, "前置：应是床内版面"
    _sync(win)
    compiled = win._job_compiled
    assert compiled is not None
    assert compiled.runnable and not compiled.bounds.violations, \
        "床内版面被误拦 —— 闸门成了误报骚扰"
    # 且同步本身不该改动 placement（用户自己选的归位方式不能被打回）
    assert win._job_spec.placement.mode == "preserve"


# -- ② anchor 洗白：落点合法但「版面越界」必须被告知 -----------------------

def test_anchor_wash_never_shows_clean_runnable_job(win_sync_ready):
    """缺陷 ②（**真 BUG**）：anchor 把越床版面洗到床内后，作业页仍须告知。

    修复前实测：排版页 ``_out_of_bed()=(30.0, [...])``，作业页
    ``violations=()``、``runnable=True``、lines X=(0,20) —— 机器切在
    (0,0)-(20,20)，而用户被告知「一切正常」。
    """
    win, lp = win_sync_ready
    _set_anchor(win, "bl", (0.0, 0.0))
    assert win._job_spec.placement.mode == "anchor", "前置：应是 anchor 模式"

    # 回排版页把图元挪出床（版面越界），但 anchor 会把它整体拉回 (0,0)-(20,20)
    lp.doc.items[0].pos = (-40.0, 0.0)
    lp._rebuild_scene()
    lp._after_change()
    over = lp._out_of_bed()
    assert over is not None, "前置：排版页应报越界"

    _sync(win)

    compiled = win._job_compiled
    assert compiled is not None
    # 落点确实被洗到床内（这正是 anchor 的语义，不是 bug）
    xr = _lines_x_range(win)
    assert xr is not None and 0.0 <= xr[0] and xr[1] <= 210.0, \
        f"前置：anchor 后落点应在床内，实得 {xr}"
    # ……但作业页不许对此**一声不吭**
    warned = _layout_out_of_bed_shown(win)
    assert warned, (
        f"排版页报越界 {over[0]:.0f}mm、anchor 已把落点移到 {xr}，"
        f"作业页却显示为干净可跑（violations={compiled.bounds.violations}, "
        f"runnable={compiled.runnable}）—— 用户不知道要去的地方变了")


def test_anchor_wash_shows_actual_landing_spot(win_sync_ready):
    """告知必须**含真实落点**，否则用户仍不知道刀会切在哪。

    这是 ask 的原话：「或者，在 anchor 应用时明确告诉用户『越界几何将被移到
    (x,y)』」。只说「有越界」不够 —— 用户要的是**新的落点**。

    ⚠ **判据不能用「文本里含裸数字」**（Q1）。``assert "0" in text`` 会被
    改动前就有的 ``bbox (0,0)-(20,20)`` 喂饱：把整段告知摘掉
    （``_layout_bounds_notice`` 返回 ``""``）后那两条断言照样通过 ——
    空断言钉不住任何东西。本文件实测复现过这一点，故改为：
    **把 lines 回读出的落点范围原样拼出来去匹配**，且要求匹配的是
    「落点告知那一段」而不是整行（整行里本来就有 bbox）。
    """
    win, lp = win_sync_ready
    _set_anchor(win, "bl", (0.0, 0.0))
    lp.doc.items[0].pos = (-40.0, 0.0)
    lp._rebuild_scene()
    lp._after_change()
    _sync(win)

    # 真值 = 将发送的同一份 lines 回读出的落点（与 worker 实收一致）
    xr = _lines_x_range(win)
    yr = _lines_y_range(win)
    assert xr is not None and yr is not None
    notice = _layout_notice_text(win)
    want = f"({xr[0]:.0f},{yr[0]:.0f})-({xr[1]:.0f},{yr[1]:.0f})"
    assert want in notice, (
        f"告知里没有落点 {want}（lines 实测 X={xr} Y={yr}）—— "
        f"用户仍不知道刀会切在哪。notice={notice!r}")
    # 越界这件事本身也要在作业页看得见
    assert _layout_out_of_bed_shown(win), \
        f"作业页未显示「版面越界」：{notice!r}"


def test_anchor_wash_still_allows_execution(win_sync_ready):
    """**不回归**：告知 ≠ 拦截。落点合法时用户仍应能执行（anchor 是正当功能）。

    若本条失败，说明实现把「告知」做成了「禁执行」—— 那等于砸掉 anchor
    归位这个功能（大版面归位到床内是用户主动要的）。
    """
    win, lp = win_sync_ready
    _set_anchor(win, "bl", (0.0, 0.0))
    lp.doc.items[0].pos = (-40.0, 0.0)
    lp._rebuild_scene()
    lp._after_change()
    _sync(win)

    compiled = win._job_compiled
    assert compiled is not None
    assert compiled.runnable and not compiled.bounds.violations, \
        ("落点合法却禁执行 —— 「告知」被做成了「拦截」，"
         "等于砸掉 anchor 归位这个正当功能")


# -- ③ 不回归：边界情形不该被新逻辑波及 -------------------------------------

def test_flush_to_bed_edge_after_anchor_is_not_warned(win_sync_ready):
    """**不回归**：恰好贴边（越界量 0）不该被说成「越界」。

    闸门不能变成误报骚扰（同 frozen 文件的判定对称契约）。
    """
    win, lp = win_sync_ready
    _set_anchor(win, "bl", (0.0, 0.0))
    # 挪到恰好 x=0（贴左边界，越界量 0）
    lp.doc.items[0].pos = (0.0, 0.0)
    lp._rebuild_scene()
    lp._after_change()
    assert lp._out_of_bed() is None, f"前置：贴边不该报越界，实得 {lp._out_of_bed()}"
    _sync(win)
    assert not _layout_out_of_bed_shown(win), "贴边版面被误报为越界"


def test_no_layout_job_untouched_by_sync_checks(win_sync_ready):
    """**不回归**：作业页握的不是排版作业时，同步的越界检查不得碰它。

    来源门禁（``_job_from_layout=False`` ⇒ 整个方法直接返回）是既有契约；
    越床提示绝不能绕过它去动用户自己载入的文件作业。
    """
    win, lp = win_sync_ready
    win._on_clear_job()
    assert win._job_from_layout is False
    n_before = len(win._job_lines)
    _sync(win)
    assert win._job_spec is None, "同步动了非排版作业 —— 来源门禁被破坏"
    assert len(win._job_lines) == n_before


def test_out_of_bed_notice_does_not_leak_to_non_layout_job(win_sync_ready):
    """**自查补的洞**：载入自己的文件作业后，越界提示不得跟着过来。

    越界判据是**排版页**的事实（版面几何的真源在那儿）。作业页握的不是
    排版作业时，这段告知不该出现 —— 否则用户载入自己的文件后，看到一句
    与他无关的「排版版面越界」，而他自己的文件可能完全没问题。

    ⚠ 判据刻意**不用** ``_on_clear_job``：它会把 ``job_info`` 整行改写成
    「未载入文件」，越界字样自然被抹掉 ⇒ 那条路径**结构上不可能失败**
    （实测：去掉下面两处修复它照样绿）。真正的风险在**载入文件作业**这条路
    —— 它同样把 ``_job_from_layout`` 翻成 False，但 ``job_info`` 是重算出来
    的，越界字样会**真的**留在界面上。
    """
    win, lp = win_sync_ready
    lp.doc.items[0].pos = (-40.0, 0.0)
    lp._rebuild_scene()
    lp._after_change()
    _sync(win)
    assert "越界" in _job_page_text(win), "前置：本例应先出现越界告知"

    # 用户改主意，载入自己的文件作业（来源从排版切走）
    win._set_job_and_recompile(_file_job(), from_layout=False)
    win._refresh_gate_ui()

    assert win._job_from_layout is False, "前置：应是文件作业"
    text = _job_page_text(win)
    assert "越界" not in text, \
        f"文件作业上仍显示排版越界（用户会以为自己的文件有问题）：{text!r}"


def _file_job():
    """一个**床内**的普通文件作业（与排版无关）。"""
    from megapro.gui.job import JobSpec, Placement

    return JobSpec(paths_paper=[[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]],
                   source_name="mine.svg", placement=Placement(mode="preserve"))


def test_notice_does_not_claim_moved_when_nothing_moved(win_sync_ready):
    """**自查补的洞**：落点**也**越界时，告知不得说「落点已移到 …」。

    preserve 下什么都没移（坐标原样越界），说「已移到」是**不实陈述** ——
    会让用户以为有个归位动作兜住了，越界那一段反而不看了。落点越界时
    已有「越界N处(禁执行)」在管执行，这里只补「版面本身越界」这层事实。
    """
    win, lp = win_sync_ready
    lp.doc.items[0].pos = (-40.0, 0.0)
    lp._rebuild_scene()
    lp._after_change()
    _sync(win)

    text = _job_page_text(win)
    assert "未做放置归位" in text, f"落点也越界时应说明未归位：{text!r}"
    assert "已移到" not in text, f"落点越界时不得声称「已移到」：{text!r}"


def test_notice_claims_moved_when_anchor_actually_moved(win_sync_ready):
    """与上一条互为镜像：anchor 真的把落点挪了，才可以说「已移到」。

    两条一起钉住那句文案**跟着事实走**，不靠措辞本身。
    """
    win, lp = win_sync_ready
    _set_anchor(win, "bl", (0.0, 0.0))
    lp.doc.items[0].pos = (-40.0, 0.0)
    lp._rebuild_scene()
    lp._after_change()
    _sync(win)

    text = _job_page_text(win)
    assert "已移到" in text, f"anchor 确实归位了却没说落点：{text!r}"
    assert "未做放置归位" not in text, f"归位了却声称未归位：{text!r}"


# -- Q3：归位做了、但落点**仍**超床（宽度大于床 / 笔径撑出床） ---------------
#
# 这两条是评审指出的覆盖缺口：原实现用 ``compiled.bounds.violations`` 当
# 「有没有做归位」的判据，于是「归位做了但落点仍超床」被说成「未做放置归位」
# —— 而 place_at_anchor（coords.py:145）**只要 mode==anchor 就无条件平移**。
# 实测：300mm 宽的方块（>210mm 床）放 pos=(-50,0) + anchor bl→(0,0)，
# 实际位移 (+50,0)，画布 (-50,0)-(250,20) vs 机器 (0,0)-(300,20) 差 50mm，
# 而界面写「未做放置归位」。缓解事实：该分支 runnable=False、按钮禁用、
# 不会切错；错的是**这句话**——而本批的唯一目的就是让文案跟着事实走。

def test_anchor_moved_but_landing_still_out_of_bed_says_moved(win_sync_ready):
    """归位**做了**、落点**仍**超床 ⇒ 必须说「已移到」且说清仍超床。

    正确判据是 ``spec.placement.mode``（用户选了什么就说什么），不是
    「落点还越不越界」。
    """
    win, lp = win_sync_ready
    # 300mm 宽 > 210mm 床：归位到 (0,0) 后右边缘仍在 250mm ⇒ 仍超床
    lp.doc.items[0].paths = [[(0.0, 0.0), (300.0, 0.0), (300.0, 20.0),
                              (0.0, 20.0), (0.0, 0.0)]]
    lp._rebuild_scene()
    lp._after_change()
    _set_anchor(win, "bl", (0.0, 0.0))
    lp.doc.items[0].pos = (-50.0, 0.0)
    lp._rebuild_scene()
    lp._after_change()
    _sync(win)

    assert win._job_spec.placement.mode == "anchor", "前置：应是 anchor 模式"
    # 落点确实仍超床（所以旧的 violations 判据会走进「未做放置归位」分支）
    assert win._job_compiled.bounds.violations, \
        "前置：本例的落点应仍越界（300mm > 210mm 床）"
    # 落点确实被搬了：画布 (-50,0) vs 机器 (0,0) ⇒ 差 50mm
    xr, yr = _lines_xy_range(win)
    assert (xr, yr) == ((0.0, 300.0), (0.0, 20.0)), \
        f"前置：lines 应落在 (0,0)-(300,20)，实得 X={xr} Y={yr}"

    text = _job_page_text(win)
    assert "未做放置归位" not in text, (
        f"place_at_anchor 实际搬了 (+50,0)，界面却说「未做放置归位」：{text!r}"
        f" —— 画布 (-50,0)-(250,20) 与机器 (0,0)-(300,20) 差 50mm")
    assert "已移到" in text, f"归位确实发生了却没说落点：{text!r}"
    # 落点仍超床这件事也要说清（别让用户以为归位兜住了）
    assert "仍超床" in text, f"未告知落点仍超床：{text!r}"


def test_anchor_moved_but_pen_radius_makes_landing_out_of_bed(win_sync_ready):
    """同 Q3 的第二条触发路径：笔径把**落点**撑出床（几何本身在床内）。

    评审实测的第二条：笔径 400mm 时，几何已从 (-40,0,-20,20) 移到
    (0,0,20,20)，但落点因笔半径仍越界 ⇒ 旧判据同样会谎称「未做放置归位」。
    """
    win, lp = win_sync_ready
    _set_anchor(win, "bl", (0.0, 0.0))
    win._pen_diameter_mm = 400.0        # 笔半径 200mm ⇒ 落点必然出床
    win._recompile()
    lp.doc.items[0].pos = (-40.0, 0.0)
    lp._rebuild_scene()
    lp._after_change()
    _sync(win)

    assert win._job_compiled.bounds.violations, "前置：笔径应导致落点越界"
    xr, yr = _lines_xy_range(win)
    assert xr is not None and abs(min(xr)) < 1e-6, \
        f"前置：几何应已被归位到 x=0，实得 {xr}"
    text = _job_page_text(win)
    assert "未做放置归位" not in text, \
        f"几何已归位到 x=0，界面却说「未做放置归位」：{text!r}"
    assert "已移到" in text, f"未告知真实落点：{text!r}"



# -- 观测辅助：作业页「有没有把版面越界说出来」 ------------------------------

def _job_page_text(win) -> str:
    """作业页上用户能看到越界信息的全部文本（作业信息行 + 状态栏）。"""
    parts = [win.job_info.text(), getattr(win, "status_label").text()]
    return "\n".join(p for p in parts if p)


def _layout_notice_text(win) -> str:
    """**越界告知那一段**（``⚠排版版面越界…`` 起的那截），不在则空串。

    刻意只取告知段、不取整行：整行里本来就有改动前就存在的 ``bbox (…)``，
    拿整行去匹配落点会**必然命中** ⇒ 空断言（Q1 实测：摘掉整段告知后
    ``"0" in text`` / ``"20" in text`` 仍为 True）。
    """
    for line in _job_page_text(win).splitlines():
        if "⚠排版版面越界" in line:
            _, _, tail = line.partition("⚠排版版面越界")
            return "⚠排版版面越界" + tail
    return ""


#: 作业页用来表示「这份排版版面原本越界」的**机器可读**标记。
#: 实现必须设置它（属性名固定），测试据此断言「说没说」——不靠猜中文文案，
#: 也不靠「文本里恰好含某个词」（那种断言会被措辞改动误伤/漏伤）。
LAYOUT_OOB_ATTR = "_layout_out_of_bed"


def _layout_out_of_bed_shown(win) -> bool:
    """作业页是否已把「排版版面越界」告诉用户。

    判据 = :data:`LAYOUT_OOB_ATTR` 属性（实现写入）+ 作业信息行里**真的**
    出现了落点坐标（防止只置属性不显示）。
    """
    info = getattr(win, LAYOUT_OOB_ATTR, None)
    if not info:
        return False
    text = _job_page_text(win)
    return "越界" in text or "床" in text


# -- fixture ---------------------------------------------------------------

@pytest.fixture
def win_sync_ready(monkeypatch):
    """真 MainWindow + 同步开关开（默认开）+ 排版页已有一个床内方块并已「送去作业」。

    ``_confirm_in_bed`` 被替身挡掉（床内版面本不该弹框；真弹框会让测试挂死）。
    """
    from megapro.gui.layout import layout_page as lp_mod

    _real = QtWidgets.QMessageBox.exec
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec",
                        lambda self, *a, **k: 0)
    win = _win()
    lp = win.layout_page
    assert lp._sync_job_cb.isChecked(), "前置：同步开关默认应开"
    # 方块画在 (0,0)-(20,20) ⇒ ``pos`` 就是它的左下角，键入负值即越出左边界，
    # 越界量 = |pos.x|，好核对（画在 (10,10) 的话越界量会比键入值小 10）。
    lp.doc.add(_rect("blk", 0.0, 0.0, 20.0, 20.0, z=1.0))
    lp._rebuild_scene()
    lp._after_change()
    # 显式「送去执行」建立 _job_from_layout=True（静默同步的来源门禁要求）
    win._on_layout_export(lp.to_job_spec())
    assert win._job_from_layout is True
    assert win._job_compiled is not None and win._job_compiled.runnable
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", _real)
    yield win, lp
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", _real)
