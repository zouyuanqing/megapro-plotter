"""B1 回归：缩放后手柄的**屏幕尺寸/屏幕间距**必须恒定（不再只靠 ``sync()``）。

缺陷（本批证据，修复前可复现）：:meth:`SelectionHandles.update_sizes` 用
``setScale(1/ppm)`` 把手柄钉成固定**屏幕**尺寸，但 ``update_sizes()`` 只从
``sync()`` 可达，而 ``sync()`` 只在「选中变化 / 编辑后」被调
（``layout_page.py:343`` ``_after_change``、``:879`` ``_on_selection_changed``）。
``PaperView.viewChanged``（``paper_view.py:34``，在 ``_apply_transform`` :79 发）
只接了 ``_sync_snap_pitch``（``layout_page.py:553``），手柄没接 ⇒ 缩放/适应/
滚轮之后手柄屏幕尺寸一路漂，直到下一次选中变化才对上。同理 ``sync()`` 里
旋转手柄的「18.0 场景单位」间距（``handles.py:97``）是场景系常数 ⇒ 缩小视图
时旋转手柄会陷进选区里。

断言的是**可换算的几何量**（手柄本地宽 × 自身缩放 × 视图 px/mm = 屏幕 px；
再走一条独立通道 ``sceneBoundingRect().width() × ppm``），不是像素渲染断言。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# --- 脚手架 -----------------------------------------------------------------

def _page_with_one_selected():
    """真实入口造一个只选中单图元的排版页（走 selectionChanged → handles.sync）。"""
    import megapro.gui.layout.layout_page as L
    from megapro.gui.layout.model import Item

    page = L.LayoutPage()
    it = Item(paths=[[(20.0, 30.0), (60.0, 30.0), (60.0, 70.0)]], name="a", z=1)
    page._add_items([it])
    page._gi_for(it).setSelected(True)          # → _on_selection_changed → sync
    assert page._handles._handles, "前置：应摆出 4 缩放 + 1 旋转手柄"
    return page


def _handles(page):
    return page._handles._handles


def _screen_px(page, gi) -> float:
    """手柄的**屏幕宽度**（px）：本地宽 × 自身缩放 × 视图 px/mm（可换算几何量）。"""
    ppm = abs(page.view.transform().m11())
    return gi.boundingRect().width() * gi.scale() * ppm


def _screen_px_via_scene(page, gi) -> float:
    """同一几何量的**独立**通道：场景系宽 × px/mm。"""
    ppm = abs(page.view.transform().m11())
    return gi.sceneBoundingRect().width() * ppm


def _rotate(page):
    return [h for h in _handles(page) if h.kind == "rotate"][0]


def _rotate_gap_screen_px(page) -> float:
    """选择框顶边 → 旋转手柄中心的**屏幕**间距（px）。"""
    ppm = abs(page.view.transform().m11())
    rect = page._handles.selection_rect()
    gap_scene = rect.top() - _rotate(page).pos().y()
    return gap_scene * ppm


# --- 核心：手柄屏幕尺寸不随缩放漂移 -----------------------------------------

def test_handle_screen_size_constant_across_zoom():
    """选中 → ``set_zoom`` 放大/缩小 → 手柄屏幕宽度（两条独立通道）不变。

    旧行为：``update_sizes`` 不可达 ⇒ 屏幕宽度 = 本地宽 × 旧 1/ppm × 新 ppm，
    随缩放同比例涨落。
    """
    page = _page_with_one_selected()
    before = [_screen_px(page, h) for h in _handles(page)]
    before_scene = [_screen_px_via_scene(page, h) for h in _handles(page)]
    assert before == pytest.approx([10.0] * len(before)), "前置：4+1 手柄各 10px"

    ppm0 = page.view.px_per_mm()          # 初始即 fit_ppm（缩放下限，paper_view.py:99）
    page.view.set_zoom(ppm0 * 3.0, QPointF(50.0, 50.0))
    assert page.view.px_per_mm() == pytest.approx(ppm0 * 3.0), "前置：确实放大了"

    assert [_screen_px(page, h) for h in _handles(page)] == pytest.approx(before)
    assert ([_screen_px_via_scene(page, h) for h in _handles(page)]
            == pytest.approx(before_scene))

    # 从放大态缩回来（仍高于 fit 下限，否则会被 _clamp_ppm 钳回原 ppm）
    page.view.set_zoom(ppm0 * 1.5, QPointF(50.0, 50.0))
    assert page.view.px_per_mm() == pytest.approx(ppm0 * 1.5), "前置：确实缩小了"
    assert [_screen_px(page, h) for h in _handles(page)] == pytest.approx(before)
    assert ([_screen_px_via_scene(page, h) for h in _handles(page)]
            == pytest.approx(before_scene))
    page.deleteLater()


def test_handle_scale_inverts_ppm_after_zoom():
    """自身缩放必须真的跟上：``h.scale() == 1/ppm``（缩放的充要量）。"""
    page = _page_with_one_selected()
    page.view.set_zoom(page.view.px_per_mm() * 2.5, QPointF(10.0, 10.0))
    ppm = page.view.px_per_mm()
    for h in _handles(page):
        assert h.scale() == pytest.approx(1.0 / ppm, rel=1e-9)
    page.deleteLater()


def test_handle_screen_size_constant_across_fit():
    """``fit()``（换页/初始布局的公共入口）同样不能让手柄漂。

    ⚠ **R5 更正**：原版是「``set_zoom(ppm*2)`` 然后 ``fit()``」，而视口尺寸没变，
    ``fit()``（``paper_view.py:112-123``）按 ``min(vr.width()/210, vr.height()/210)``
    算 ppm ⇒ 必然回到**与 before 完全相同**的 ppm。那是一次**恒等比较**：手柄逻辑
    怎么坏它都过（实测：修复前代码上中途漂到 20px 仍过；把 ``update_sizes`` 打成
    完全 no-op 也过）。

    真正能验 ``fit()`` 的场景是「视口尺寸变了 → fit 到**新** ppm」。故先 resize
    视口再 fit，并前置断言结束 ppm 确实 ≠ 起始 ppm —— 没有这条前置，本用例又会
    退回成恒等比较。
    """
    page = _page_with_one_selected()
    before = [_screen_px(page, h) for h in _handles(page)]
    assert before == pytest.approx([10.0] * len(before)), \
        "前置：4+1 手柄各 10px（缺这条，就像 R5 指出的那样可能带着活 bug 通过）"

    ppm0 = page.view.px_per_mm()
    page.view.set_zoom(ppm0 * 2.0, QPointF(0.0, 0.0))
    assert page.view.px_per_mm() == pytest.approx(ppm0 * 2.0), "前置：确实放大了"

    # 改变视口尺寸 ⇒ fit() 会落到**不同**的 ppm
    vp = page.view.viewport()
    old_w, old_h = vp.width(), vp.height()
    vp.resize(int(old_w * 1.35), int(old_h * 1.25))
    assert (vp.width(), vp.height()) != (old_w, old_h), "前置：视口确实被改尺寸了"

    ppm_after = page.view.fit()

    assert abs(ppm_after - ppm0) > 1e-6, (
        f"前置失败：fit() 后 ppm={ppm_after} 与起始 {ppm0} 相同 ⇒ "
        "本用例退化成恒等比较，验不到任何东西")
    assert [_screen_px(page, h) for h in _handles(page)] == pytest.approx(before), \
        f"fit() 到新 ppm={ppm_after} 后手柄屏幕尺寸漂了"
    page.deleteLater()


def test_rotate_handle_gap_constant_across_zoom():
    """旋转手柄与选框顶边的**屏幕**间距恒定（``sync()`` 里的 18.0 是场景系常数）。"""
    page = _page_with_one_selected()
    before = _rotate_gap_screen_px(page)
    assert before == pytest.approx(18.0, abs=1e-6), "前置：18 屏幕 px"

    ppm0 = page.view.px_per_mm()          # 初始即 fit_ppm（缩放下限）
    page.view.set_zoom(ppm0 * 4.0, QPointF(50.0, 50.0))
    assert page.view.px_per_mm() == pytest.approx(ppm0 * 4.0), "前置：确实放大了"
    assert _rotate_gap_screen_px(page) == pytest.approx(before, abs=1e-6)

    page.view.set_zoom(ppm0 * 1.5, QPointF(50.0, 50.0))   # 再缩回来
    assert page.view.px_per_mm() == pytest.approx(ppm0 * 1.5), "前置：确实缩小了"
    assert _rotate_gap_screen_px(page) == pytest.approx(before, abs=1e-6)
    page.deleteLater()


# --- 不许把重算接到拖动/平移的每帧路径上 -------------------------------------

def test_pan_does_not_recompute_handle_sizes():
    """平移（``viewChanged`` 每次鼠标移动都发，但 ppm 不变）不得触发重算。

    ``update_sizes`` 在实例属性上被探针替换，故 slot 内部必须走
    ``self.update_sizes()`` 的动态属性查找，而不是在连接时就绑死方法对象。
    """
    page = _page_with_one_selected()
    sh = page._handles
    calls = []
    original = sh.update_sizes

    def spy():
        calls.append(1)
        original()

    sh.update_sizes = spy
    for i in range(5):
        page.view.pan_by(7.0, 3.0)              # 纯平移：ppm 不变
    assert calls == [], f"平移不应触发手柄重算，实得 {len(calls)} 次"

    page.view.set_zoom(page.view.px_per_mm() * 2.0, QPointF(50.0, 50.0))
    assert calls, "缩放必须触发一次重算"
    del sh.update_sizes
    page.deleteLater()


def test_no_selection_zoom_is_noop_and_safe():
    """无选中时缩放：不得崩、不得凭空长出手柄，且**缩放流水线必须仍然走到**。

    ⚠ **R5 更正**：原版只断言「前后手柄数都是 0」，而**修复前也成立**（B1 前
    ``viewChanged`` 压根没接手柄），故钉不住任何东西。

    补的前置断言是「缩放确实走到了 ``update_sizes``」。这**不是**在测实现细节，
    而是在守一条契约：空选中集时 ``update_sizes`` 走的是一个**空循环**（对任何
    手柄都不操作 = 「无谓工作」仍然是 0），但**整条路径不能被短路**。否则将来谁
    在 ``_on_view_changed`` 开头加一句 ``if not self._handles: return`` 当
    「优化」，缩放就会静默回到 B1 之前的状态 —— 而所有其它用例当时仍然绿。
    """
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    sh = page._handles
    assert sh._handles == [], "前置：初始无选中 ⇒ 无手柄"

    calls = []
    original = sh.update_sizes

    def spy():
        calls.append(1)
        original()

    sh.update_sizes = spy
    page.view.set_zoom(page.view.px_per_mm() * 3.0, QPointF(1.0, 1.0))
    del sh.update_sizes

    assert sh._handles == [], "无选中时缩放不该凭空长出手柄"
    assert calls, ("缩放没有走到 update_sizes —— 若将来给空选中集加「早退优化」，"
                   "缩放路径会静默失效，而本文件其它用例那时仍会全绿")
    page.deleteLater()


# --- 事件级：真实 wheelEvent 才是用户实际触发的那条路 -------------------------

def test_real_wheel_event_resizes_handles():
    """真发 ``QWheelEvent`` 进 viewport ⇒ 手柄屏幕尺寸仍恒定。

    B1 的整个论证是「``_apply_transform`` 是缩放的**唯一**汇流点，``viewChanged``
    在那里发」——而那 6 条用例全都直接调 ``set_zoom()``/``fit()``/``pan_by()``，
    **没有一条走真实滚轮**，等于这条前提本身没有回归保护。本条补上。

    复现要点：``wheelEvent``（``paper_view.py:140-144``）算 ``factor`` 后调
    ``set_zoom``，所以必须真的把事件投进 viewport，绕不过去。
    """
    from PySide6 import QtCore, QtGui

    page = _page_with_one_selected()
    before = [_screen_px(page, h) for h in _handles(page)]
    assert before == pytest.approx([10.0] * len(before)), "前置：4+1 手柄各 10px"
    ppm0 = page.view.px_per_mm()

    vp = page.view.viewport()
    for _ in range(2):
        ev = QtGui.QWheelEvent(
            QtCore.QPointF(40.0, 40.0), QtCore.QPointF(40.0, 40.0),
            QtCore.QPoint(0, 0), QtCore.QPoint(0, 120),
            QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
            QtCore.Qt.NoScrollPhase, False)
        QApplication.sendEvent(vp, ev)

    assert page.view.px_per_mm() != pytest.approx(ppm0), \
        "前置失败：真实滚轮没改变 ppm ⇒ 本用例什么都没验"
    assert [_screen_px(page, h) for h in _handles(page)] == pytest.approx(before), \
        "真实滚轮缩放后手柄屏幕尺寸漂了"
    assert _rotate_gap_screen_px(page) == pytest.approx(18.0, abs=1e-6), \
        "真实滚轮缩放后旋转手柄的屏幕间距漂了"
    page.deleteLater()
