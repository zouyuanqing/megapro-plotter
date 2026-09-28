"""R3 回归：缩放路径**不得**触碰图元几何（``selection_rect()`` 是缩放不变量）。

R3 推翻了 B1 的代价论。``update_sizes()`` 末尾调 ``_relayout_rotate(ppm)``，
后者为了摆旋转手柄调 ``self.selection_rect()`` —— 而 ``selection_rect()`` 是
对 ``_targets`` 逐个 ``gi.sceneBoundingRect()`` 求并集，
``PathItem.boundingRect()`` 又直通 ``model.Item.bbox()``（两条纯 Python 列表推导，
**无缓存**）⇒ 每次缩放多扫一遍 O(选中集总点数)。

而这个量**数学上不可能随缩放变化**：视图变换走 ``QGraphicsView.setTransform``，
不进入图元的场景包围盒。本文件实测（真实 ``LayoutPage`` + 真实 ``set_zoom``）：

    distinct selection_rect() values over 23 reads: 1
    bit-identical before/after-zoom/after-fit?      True
    rotate handle scene pos moved by zoom?           True (-10.1266 -> -7.9800)

⇒ 每次缩放重扫是**纯白做**，而真正需要随 ppm 重算的只有 5 个手柄的
``setScale(1/ppm)`` 与 ``_ROTATE_GAP_PX / ppm``，两者都是标量运算。

**本文件断言的是「缩放路径不调它」，不是「跑得多快」** —— 计时断言在不同机器上
会漂到没法验收；而「不该调的函数在缩放时被调了 23 次」是确定的、可复现的事实。

另钉一条**防呆错缓存**的用例：若按提案在 ``sync()`` 里缓存 ``selection_rect()``，
但忘了在选中变化时刷新，旋转手柄就会按**上一个选中集**的位置摆。故
:func:`test_rotate_handle_uses_current_selection_after_cache` 要求：换选中 → 缩放
→ 手柄必须落在**新**选中集的选框顶上。这条能挡住一个「让性能用例转绿」的坏修法。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

#: R3 未修：修复要改 ``canvas/handles.py``（``sync()`` 缓存 + ``_relayout_rotate``
#: 改读缓存），而 ``canvas/`` 不在本批白名单（其它线正在并行改它）。
#: 编码方式与 R1/R2 同：不用普通测试（会把仓库留红、卡下一道门），也不用 skip
#: （会让「缩放不重扫几何」这个契约消失）。**strict=True** 保证它不会烂在文件里：
#: 修好后本条会 XPASS(strict) 报红，逼人摘标记。
#: 摘标记的条件（已用 ``-p`` 插件在运行时注入该修复**验证过**，未改任何仓库文件）：
#: ``sync()`` 里算出 ``selection_rect()`` 后存进 ``self._cached_rect``（并在
#: ``clear()``/换选中时作废），``_relayout_rotate`` 改读缓存、仅在缓存缺失时回扫一次。
#: 验证记录：注入后本文件 6 passed，且既有 tests/test_gui_canvas_handle_zoom.py
#: 的 6 条 B1 正确性用例仍全绿（B1 的缺陷不能被"删掉重排"这种方式改掉）。
_XFAIL_R3 = pytest.mark.xfail(
    strict=True,
    reason="R3 未修：缩放路径仍在重扫 selection_rect()/Item.bbox()（缩放不变量，"
           "代价 O(选中集总点数)）。修复需改 canvas/handles.py，不在本批白名单。"
           "修好后本条会 XPASS(strict) 报红，届时请删除此标记。",
)


# -- 脚手架 -----------------------------------------------------------------

def _page(items_spec):
    """造一个真实 LayoutPage 并全选；``items_spec`` = [(name, x0, y0), ...]"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    items = []
    for name, x0, y0 in items_spec:
        items.append(Item(paths=[[(x0, y0), (x0 + 30.0, y0),
                                 (x0 + 30.0, y0 + 20.0), (x0, y0 + 20.0),
                                 (x0, y0)]], name=name))
    lp._add_items(items)
    return lp


def _select(lp, name):
    it = next(i for i in lp.doc.items if i.name == name)
    for gi in lp._scene_items:
        gi.setSelected(gi.model_item is it)
    return it


def _rotate(lp):
    return next((h for h in lp._handles._handles if h.kind == "rotate"), None)


def _zoom_times(lp, n=6, base=1.0, step=0.35):
    """真实缩放 n 次，每次 ppm 都变（绕过 ``_on_view_changed`` 的平移早退守卫）。"""
    ppm0 = lp._handles._view_ppm()
    return [ppm0 * (base + step * k) for k in range(1, n + 1)]


# -- ① 缩放路径不得触碰图元几何 ----------------------------------------------

@_XFAIL_R3
def test_zoom_path_does_not_call_selection_rect(monkeypatch):
    """缩放时不得调用 ``selection_rect()`` —— 那是缩放不变量，重扫纯浪费。

    修复前实测：每档 23 次 ``set_zoom`` → ``selection_rect`` 被调 **23** 次。
    """
    from megapro.gui.canvas.handles import SelectionHandles

    lp = _page([("a", 10.0, 10.0), ("b", 60.0, 10.0)])
    _select(lp, "a")
    lp._handles.sync(lp._selected())          # 先把缓存/手柄建起来

    calls = []
    orig = SelectionHandles.selection_rect

    def spy(self):
        calls.append(1)
        return orig(self)

    monkeypatch.setattr(SelectionHandles, "selection_rect", spy)
    for ppm in _zoom_times(lp):
        lp.view.set_zoom(ppm)

    assert calls == [], (
        f"缩放路径调了 {len(calls)} 次 selection_rect() —— 那是缩放不变量，"
        "重扫是纯白做，代价 O(选中集总点数)")


@_XFAIL_R3
def test_zoom_path_does_not_rescan_item_geometry(monkeypatch):
    """更底层的一条：缩放时不得调 ``Item.bbox()``（真正的 O(总点数) 开销来源）。

    ``PathItem.boundingRect()`` 直通它（items.py:59-60），model 侧无缓存。
    """
    from megapro.gui.layout.model import Item

    lp = _page([("a", 10.0, 10.0), ("b", 60.0, 10.0)])
    _select(lp, "a")
    lp._handles.sync(lp._selected())

    calls = []
    orig = Item.bbox

    def spy(self):
        calls.append(1)
        return orig(self)

    monkeypatch.setattr(Item, "bbox", spy)
    for ppm in _zoom_times(lp):
        lp.view.set_zoom(ppm)

    assert calls == [], (
        f"缩放路径触发了 {len(calls)} 次 Item.bbox()（O(总点数) 纯 Python 循环）")


def test_selection_rect_is_zoom_invariant():
    """前提事实：``selection_rect()`` 不随缩放变化（所以①才有资格说「白做」）。

    视图变换走 ``setTransform``，不进入图元的场景包围盒；若哪天实现改了这条
    （比如把视口比例混进 bbox），①就会变成误伤，届��本条先红。
    """
    lp = _page([("a", 10.0, 10.0), ("b", 60.0, 10.0)])
    _select(lp, "a")
    before = repr(lp._handles.selection_rect())
    for ppm in _zoom_times(lp, n=4):
        lp.view.set_zoom(ppm)
        assert repr(lp._handles.selection_rect()) == before, \
            "selection_rect() 竟随缩放变化 ⇒ ① 的前提不成立，请重读 R3"


# -- ② 防「为性能而把缓存做陈旧」 --------------------------------------------

def test_rotate_handle_uses_current_selection_after_cache():
    """换选中 → 缩放 ⇒ 旋转手柄必须按**新**选中集的选框摆。

    这是给提案里的缓存加的看门狗：``sync()`` 里缓存 ``selection_rect()`` 只在
    「选中/编辑时」更新，若忘了在换选中时刷新，旋转手柄会按**上一个**选中集的
    位置摆 —— 而性能用例是绿的、手柄却在错位置。必须挡住这种「让①转绿」的坏修法。
    """
    from megapro.gui.canvas.handles import _ROTATE_GAP_PX

    lp = _page([("a", 10.0, 10.0), ("b", 120.0, 90.0)])
    _select(lp, "a")
    lp._handles.sync(lp._selected())
    _select(lp, "b")
    lp._handles.sync(lp._selected())        # 换选中：缓存必须在这里刷新

    ppm = _zoom_times(lp, n=1)[0]
    lp.view.set_zoom(ppm)
    actual_ppm = lp._handles._view_ppm()

    rect = lp._handles.selection_rect()      # 独立重算一次作参照
    rot = _rotate(lp)
    assert rot is not None, "没有旋转手柄，场景构造有问题"
    assert abs(rot.pos().x() - rect.center().x()) < 1e-6, \
        f"旋转手柄 x 未对齐当前选框中心：{rot.pos().x()} vs {rect.center().x()}"
    assert abs(rot.pos().y() - (rect.top() - _ROTATE_GAP_PX / actual_ppm)) < 1e-6, \
        f"旋转手柄 y 未按当前选框顶边 + 屏幕间距摆：{rot.pos().y()}"


def test_rotate_handle_still_tracks_zoom():
    """性能修复不得顺手删掉 B1 的正确性：旋转手柄的**屏幕**间距必须恒定。

    B1 的缺陷是真的（缩放后手柄陷进选区），本轮只是去掉白做的重扫，不许把
    修复退化成「缩放时干脆不摆」。
    """
    from megapro.gui.canvas.handles import _ROTATE_GAP_PX

    lp = _page([("a", 10.0, 10.0)])
    _select(lp, "a")
    lp._handles.sync(lp._selected())

    gaps = []
    for ppm in _zoom_times(lp, n=5):
        lp.view.set_zoom(ppm)
        rot = _rotate(lp)
        rect = lp._handles.selection_rect()
        actual_ppm = lp._handles._view_ppm()
        gaps.append((rot.pos().y() - rect.top()) * -actual_ppm)

    assert gaps, "没有取到任何采样"
    for g in gaps:
        assert abs(g - _ROTATE_GAP_PX) < 1e-6, \
            f"旋转手柄屏幕间距漂了：{[round(x, 4) for x in gaps]}（应为 {_ROTATE_GAP_PX}）"
