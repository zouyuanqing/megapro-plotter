"""R4 回归：拖动未提交时缩放，5 个手柄必须**同源**（不能 1 对 4 分裂）。

R3 推翻了 B1 的第二个后果。``update_sizes()``（缩放路径）里 ``_relayout_rotate()``
用**实时**的 ``selection_rect()`` 摆旋转手柄；而 4 个角手柄只在 ``sync()`` 里
按当时的选框摆位，``sync()`` 只在「选中变化 / 编辑后 / 拖动**结束**」可达
（``handles.end()`` → ``sync()``）。拖动进行中场景项**实时移动**（``items.py``
``ItemIsMovable``），回写 model 只在 ``mouseReleaseEvent`` → ``commit_move()``。

于是「按住拖动、还没松手时滚一下滚轮」会把 5 个手柄劈成两派：

    4 个角手柄  → 拖动**开始**时的陈旧选框（sync 没跑）
    1 个旋转手柄 → **实时**选框（``_relayout_rotate`` 每次缩放都重算）

本文件实测（真实 LayoutPage + 真实 ``QWheelEvent``，未经 release）：

    after select   : rotate 对齐 corner box? True   gap 18.00px
    mid-drag       : rotate 对齐 corner box? True   gap 18.00px   （一致地陈旧）
    mid-drag+zoom  : rotate 对齐 corner box? **False (dx=+100.0)**
                     rotate 对齐 live rect?  **True**
                     gap above corner-top = **-103.97 SCREEN px**   ← 劈裂

**关键：这是 B1 修好缩放漂移时「新造」出来的回归。** 修复前 5 个手柄同源
（都取 sync 时的陈旧框），虽然一起陈旧但**视觉自洽**；B1 之后变成 1 贴实时框、
4 贴陈旧框。

⚠ **本文件不规定该怎么修**：既接受「旋转手柄改读 ``sync()`` 的缓存框」（与角手柄
同源、一起陈旧），也接受「缩放时把 4 个角点一起重摆」（全用实时框、全新）。
断言的只是**5 个手柄必须自洽**这一条 —— 否则「全旧」和「全新」都会被误判成缺陷。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtCore, QtGui
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

#: R4 未修：修复要改 ``canvas/handles.py``（``canvas/`` 不在本批白名单）。
#: 编码与 R1/R2/R3 同：普通测试会把仓库留红、skip 会让契约消失、**strict=True**
#: 保证修好后本条 XPASS(strict) 报红逼人摘标记。
#: 摘标记的条件（已用 ``-p`` 插件在运行时注入**两种**可接受修法验证过，
#: 未改任何仓库文件，两种都 2 passed）：
#:   A. ``_relayout_rotate`` 改读 ``sync()`` 缓存的选框（= R3 的修法，一并解决）
#:   B. 缩放时把 4 个角点与旋转手柄**一起**重摆（全部用实时框）
#: ⚠ 但 B 会把 R3 的代价原样加回来（缩放路径上又调 ``selection_rect()``，
#: O(选中集总点数)）。要同时满足 R3 与 R4，应选 A，或 A 的变体。
_XFAIL_R4 = pytest.mark.xfail(
    strict=True,
    reason="R4 未修：拖动未提交时缩放，旋转手柄取实时框而 4 个角手柄停在拖动开始框，"
           "5 个手柄劈裂。修复需改 canvas/handles.py，不在本批白名单。"
           "修好后本条会 XPASS(strict) 报红，届时请删除此标记。",
)


# -- 脚手架 -----------------------------------------------------------------

def _page_with_selected():
    """真实入口造一个选中态排版页（走 selectionChanged → handles.sync）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[[(20.0, 30.0), (60.0, 30.0), (60.0, 70.0),
                      (20.0, 70.0), (20.0, 30.0)]], name="a")
    lp._add_items([it])
    lp._gi_for(it).setSelected(True)
    return lp, it


def _corner_box(lp):
    xs = [h.pos().x() for h in lp._handles._handles if h.kind == "scale"]
    ys = [h.pos().y() for h in lp._handles._handles if h.kind == "scale"]
    assert len(xs) == 4, f"角手柄不是 4 个（{len(xs)}），场景构造有问题"
    return min(xs), min(ys), max(xs), max(ys)


def _rotate(lp):
    return next(h for h in lp._handles._handles if h.kind == "rotate")


def _wheel_zoom(lp, delta=120):
    """真发一个 QWheelEvent 进 viewport（走 paper_view.wheelEvent → set_zoom）。"""
    ev = QtGui.QWheelEvent(
        QtCore.QPointF(50, 50), QtCore.QPointF(50, 50),
        QtCore.QPoint(0, 0), QtCore.QPoint(0, delta),
        QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
        QtCore.Qt.NoScrollPhase, False)
    QApplication.sendEvent(lp.view.viewport(), ev)


def _start_uncommitted_drag(lp, dx=100.0, dy=50.0):
    """造出「拖动进行中、尚未回写」的状态，并**证明**它确实是这个状态。

    用 ``gi.setPos()`` 而不是合成一串鼠标事件：这样「场景动了、model 没动、
    sync 没跑」是**可断言**的事实，而不是碰运气碰出来的。实测二者等价
    （QTest 真 press+move 不 release 得到同样的 live rect / model pos）。
    """
    gi = lp._scene_items[0]
    before_model = tuple(gi.model_item.pos)
    gi.setPos(gi.pos() + QtCore.QPointF(dx, dy))
    return before_model


# -- 核心：5 个手柄必须同源 -------------------------------------------------

@_XFAIL_R4
def test_handles_stay_consistent_when_zooming_mid_drag():
    """拖动未提交 + 真滚轮缩放 ⇒ 旋转手柄必须与 4 个角手柄**同源**。

    「同源」= 它要么对齐角手柄围出的框、要么对齐实时框，**二选一都行**，
    但不能一个跟这个、另一个跟那个。
    """
    from megapro.gui.canvas.handles import _ROTATE_GAP_PX

    lp, it = _page_with_selected()
    model_pos_before = _start_uncommitted_drag(lp)

    # 前置状态必须真的是「拖动中」：场景动了、model 没回写
    assert tuple(it.pos) == model_pos_before, "前置不对：model 已被回写（不是拖动中）"
    live = lp._handles.selection_rect()
    cb0 = _corner_box(lp)
    assert abs(live.left() - cb0[0]) > 1e-6, \
        "前置不对：实时框与角手柄框重合（拖动没生效）"

    _wheel_zoom(lp)

    cb = _corner_box(lp)
    live = lp._handles.selection_rect()
    rot = _rotate(lp)
    ppm = lp._handles._view_ppm()

    d_corners = rot.pos().x() - (cb[0] + cb[2]) / 2.0
    d_live = rot.pos().x() - live.center().x()
    corners_are_live = (abs(cb[0] - live.left()) <= 1e-6
                        and abs(cb[2] - live.right()) <= 1e-6)

    # ⚠ 契约是「旋转手柄与**它看见的那 4 个角**同源」，**不是**「对齐实时框就行」。
    # 我第一版写成了「对齐角框 or 对齐实时框」—— 那恰恰放过了缺陷状态：角手柄
    # 陈旧时旋转手柄贴实时框正是要禁的那一格，它会让本条在坏代码上**恒绿**。
    # 正确的不变量：要么角手柄也被一起重摆了（角框==实时框），那跟着实时框没问题；
    # 要么角手柄没动（陈旧），那旋转手柄必须一起陈旧。
    ok = (abs(d_corners) <= 1e-6) or (corners_are_live and abs(d_live) <= 1e-6)

    assert ok, (
        "旋转手柄与 4 个角手柄**劈裂**了：\n"
        f"  角手柄框  = ({cb[0]:.1f},{cb[1]:.1f})-({cb[2]:.1f},{cb[3]:.1f})\n"
        f"  实时框    = ({live.left():.1f},{live.top():.1f})"
        f"-({live.right():.1f},{live.bottom():.1f})\n"
        f"  旋转手柄  = ({rot.pos().x():.1f},{rot.pos().y():.1f})\n"
        f"  离角手柄框中心 dx={d_corners:+.3f}，离实时框中心 dx={d_live:+.3f}，"
        f"角手柄是否已跟上实时框={corners_are_live}\n"
        "⇒ 5 个手柄既不都旧、也不都新。这是 B1 在缩放路径上只重排旋转手柄造成的。")

    # 跟随哪一边，都必须保住 B1 的屏幕间距契约
    top = live.top() if corners_are_live else cb[1]
    gap_px = (top - rot.pos().y()) * ppm
    assert abs(gap_px - _ROTATE_GAP_PX) < 1e-6, \
        f"旋转手柄屏幕间距漂了：{gap_px:.3f}px（应恒为 {_ROTATE_GAP_PX}）"


def test_after_drag_commit_all_handles_realign_to_live_rect():
    """拖动**结束**（commit + sync）后，5 个手柄都要对齐实时框（瞬态必须收敛）。

    R4 的现象是**瞬态**的：mouseRelease → ``commit_move()`` → ``MoveItemsCommand.redo()``
    → ``_after_change()`` → ``handles.sync()`` 会一次性对齐。本条钉住「瞬态真的
    收敛了」—— 否则一个让劈裂**持续**存在的坏状态也能让上一条转绿。
    """
    lp, it = _page_with_selected()
    _start_uncommitted_drag(lp)
    _wheel_zoom(lp)

    # 走真实 mouseRelease 路径：commit_move() → MoveItemsCommand.redo() →
    # page._after_change() → handles.sync()（commit_move 里 push 即触发 redo）
    lp._scene_items[0].commit_move()

    cb = _corner_box(lp)
    live = lp._handles.selection_rect()
    assert abs(cb[0] - live.left()) < 1e-6 and abs(cb[2] - live.right()) < 1e-6, \
        f"拖动结束后角手柄仍未对齐实时框：corners={cb} live={live}"
    rot = _rotate(lp)
    assert abs(rot.pos().x() - live.center().x()) < 1e-6, \
        "拖动结束后旋转手柄仍未对齐实时框"
