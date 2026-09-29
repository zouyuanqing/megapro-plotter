"""R3 回归：一次选择手势不得把组框判据重算 N 次（24 万点文档上的点击放大）。

缺陷（R3 对 B2 的对抗性复核，复现于本仓库）：B2 把「哪些组算完整选中」的判据
从 O(#顶层容器) 换成 O(Σ子树点数)——:meth:`GroupOverlay._complete_groups` 对**每个**
容器调 :func:`iter_leaves`，而后者搭在 :func:`iter_flattens` 上，会对子树里每条
折线**急切地**算完 ``_apply_transform`` 再丢弃。判据本身贵，这一条只放大它。

真正的放大在**调用次数**：:meth:`LayoutPage._expand_group_selection` 逐个
``gi.setSelected(want)`` 补选兄弟，**每一次**都发一次 ``selectionChanged``
⇒ 递归回 :meth:`_on_selection_changed` ⇒ 属性面板 + 手柄 + **判据**整条重跑。
点一个 10 叶子组的成员，实测::

    修复前  _on_selection_changed 进入 10 次 / _complete_groups 重算 10 次
    修复后  1 次 / 1 次      （最终选择集不变：仍是整组 10 个叶子）

**为什么用调用计数而不是计时**：计时随机器与负载抖动，CI 上必然变成随机红绿；
而「一次手势重算几次」是确定性的事实，且**正是会出错的那件事**（大文档上
它就是卡顿的来源）。修复方向若换成「让判据本身更便宜」，本文件仍绿——那是对的，
本文件守的是「不要在没有必要时重算 N 次」。

⚠ 本文件**不**守判据的单次成本（那在 ``canvas/group_overlay.py`` 的
``iter_leaves`` 上，不属本线），只守调用次数。两者相乘才是用户感受到的卡顿。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtCore, QtWidgets                      # noqa: E402
from PySide6.QtTest import QTest                          # noqa: E402
from PySide6.QtWidgets import QApplication                 # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.layout.layout_page import LayoutPage      # noqa: E402
from megapro.gui.layout.model import Item                  # noqa: E402

#: 一次手势 = 判据必须被算 **1** 次。修复前是「组内叶子数」（实测 10）。
_CRITERION_CALLS_PER_GESTURE = 1


# -- 版面构造 --------------------------------------------------------------

def _rect(x0, y0, w, h, name):
    """小矩形图元（闭合、多边形）—— 真实点击能可靠命中。

    细折线做不了这件事：线宽不到 1px 时 ``itemAt`` 常常返回 None，测试会变成
    「什么都没发生」的绿灯（实测踩过）。仓库其余 GUI 用例也一律用矩形。
    """
    return Item(paths=[[(x0, y0), (x0 + w, y0), (x0 + w, y0 + h),
                        (x0, y0 + h), (x0, y0)]], name=name)


def _grouped_page(n_groups=3, n_leaves=10):
    """若干独立组，每组 n_leaves 个成员。

    点数刻意压低：点数只影响判据**单次**成本，不影响调用次数——本文件守的是
    调用次数，所以用小文档即可保持测试快且确定。
    """
    lp = LayoutPage()
    containers = []
    for g in range(n_groups):
        leaves = []
        for k in range(n_leaves):
            it = _rect(5.0 + 12.0 * k, 5.0 + 20.0 * g, 6.0, 8.0, f"g{g}l{k}")
            lp._add_items([it])
            leaves.append(it)
        cont = lp.doc.group_items(leaves, name=f"G{g}")
        assert cont is not None, f"编组失败：G{g}"
        containers.append(cont)
    lp._sync_models()
    return lp, containers


class _Counter:
    """装上判据调用计数器（monkeypatch 用）。

    ⚠ 包装函数的**第一个参数**是 LayoutPage/GroupOverlay 实例，与外层
    ``self``（本计数器）同名就会**遮蔽**它 —— 那样 ``self.n += 1`` 会写到
    LayoutPage 上并抛 AttributeError，异常发生在信号槽里，级联整条断掉
    （实测 selected 停在 1、计数器停在 0，看着像「修复无效」）。故用
    ``counter`` 显式捕获外层引用。
    """

    def __init__(self, monkeypatch, lp):
        counter = self
        self.n = 0
        self.selection_changed = 0
        cls = type(lp._group_overlay)
        orig_cg = cls._complete_groups
        orig_sel = LayoutPage._on_selection_changed

        def counting_cg(self, doc, sel):
            counter.n += 1
            return orig_cg(self, doc, sel)

        def counting_sel(self):
            counter.selection_changed += 1
            return orig_sel(self)

        monkeypatch.setattr(cls, "_complete_groups", counting_cg)
        monkeypatch.setattr(LayoutPage, "_on_selection_changed", counting_sel)

    @property
    def both(self):
        return self.n, self.selection_changed


# -- ① 一次选择手势 ⇒ 判据只算一次 -------------------------------------------

def test_selecting_one_member_recomputes_criterion_once(monkeypatch):
    """点一个组成员 ⇒ 判据**恰好算一次**（修复前 = 组成员数，实测 10）。"""
    lp, containers = _grouped_page()
    c = _Counter(monkeypatch, lp)
    lp.scene.clearSelection()

    lp._gi_for(containers[0].children[0]).setSelected(True)

    assert c.n == _CRITERION_CALLS_PER_GESTURE, \
        (f"一次手势把判据算了 {c.n} 次 —— 大文档上就是卡顿"
         f"（修复前 = 补选的兄弟数）")
    assert c.selection_changed == _CRITERION_CALLS_PER_GESTURE, \
        f"_on_selection_changed 进入 {c.selection_changed} 次，应只 1 次"


def test_selection_expansion_still_selects_whole_group(monkeypatch):
    """**防倒退**：压掉重算不等于不补选 —— 点一个成员仍要选中整组。

    与上一条并置，就钉死「提速」不是靠「少做该做的事」换来的。
    """
    lp, containers = _grouped_page(n_groups=1, n_leaves=10)
    cont = containers[0]
    lp.scene.clearSelection()

    lp._gi_for(cont.children[0]).setSelected(True)

    got = {id(gi.model_item) for gi in lp._selected()}
    assert got == {id(lf) for lf in cont.children}, \
        f"补选整组失效：选中 {len(got)} 个，应为 {len(cont.children)} 个"


def test_signals_are_restored_after_batched_expansion(monkeypatch):
    """屏蔽只包住补选那一段：手势结束后场景信号必须照常（try/finally 守卫）。

    没有这条，一个「忘了放回去」的 blockSignals 会让**后续所有**选择变更静默
    失联（组框不更新、属性面板不刷新），而前两条仍然全绿 —— 那是最难查的坏法。
    """
    lp, containers = _grouped_page(n_groups=2, n_leaves=4)
    lp.scene.clearSelection()
    lp._gi_for(containers[0].children[0]).setSelected(True)
    assert not lp.scene.blockSignals(False), "场景信号被永久屏蔽了"

    # 换一个组再点：判据必须**再次**被算出（若信号仍被屏蔽，这里会是 0）
    c = _Counter(monkeypatch, lp)
    lp.scene.clearSelection()
    lp._gi_for(containers[1].children[0]).setSelected(True)

    assert c.n == _CRITERION_CALLS_PER_GESTURE, \
        "第二次手势后判据没被算 —— 上一次的屏蔽多半没放回去"


# -- ② 真实 QTest 点击手势 ---------------------------------------------------

def _click_member(lp, item):
    """真 QTest 鼠标点击（mm → 视口 px 走浮点变换后取整，与既有 GUI 用例同口径）。

    点**图元包围盒中心**（图元是矩形，必落在图元上）；页面上没有别的图元遮挡
    这一点，故点谁就是谁。
    """
    x0, y0, x1, y1 = item.page_bbox()
    vp = lp.view.viewportTransform().map(
        QtCore.QPointF((x0 + x1) / 2.0, (y0 + y1) / 2.0)).toPoint()
    assert lp.view.itemAt(vp) is not None, \
        f"点击坐标 {vp} 没落在图元上（本用例会空跑，判定无意义）"
    QTest.mouseClick(lp.view.viewport(), QtCore.Qt.LeftButton, pos=vp)


def test_real_click_gesture_cost_does_not_scale_with_group_size(monkeypatch):
    """真点击：判据次数**不随组内叶子数增长**（缺陷的真身）。

    为什么不写死「恰好 N 次」：真 ``QTest.mouseClick`` 会发 press/release，
    Qt 在两侧各发一次 ``selectionChanged`` ⇒ 单击固定 2 次（实测）。那是被 Qt
    决定、我们无权也不该钉死的实现细节；**会出错**的是它随组大小线性放大
    （10 叶子组：20 次；30 叶子组会更糟）。所以这里钉**不变量**：3 叶组与
    12 叶组的判据调用次数**相同**，且都很小 —— 这样无论 Qt 发几次信号都成立，
    而「补选兄弟逐个发信号」的实现必红。
    """
    counts = {}
    for n_leaves in (3, 12):
        lp, containers = _grouped_page(n_groups=1, n_leaves=n_leaves)
        lp.view.fit()
        lp.scene.clearSelection()

        c = _Counter(monkeypatch, lp)
        _click_member(lp, containers[0].children[0])
        assert len(lp._selected()) == n_leaves, \
            f"前置：{n_leaves} 叶组应被整组选中，实得 {len(lp._selected())}"
        counts[n_leaves] = c.n
        monkeypatch.undo()

    small, big = counts[3], counts[12]
    assert small == big, \
        f"判据次数随组大小变化：3 叶 {small} 次 / 12 叶 {big} 次 —— 补选兄弟在逐个发信号"
    assert big <= 2, f"单击手势的判据次数 {big}，应 ≤2（一次 press + 一次 release）"


# -- ③ 判据结果不受影响（提速不得改变画框） ---------------------------------

def test_group_frames_still_drawn_after_batched_expansion(monkeypatch):
    """提速后组框**照画**：点一个成员 ⇒ 该组仍是一个完整组框。

    从「渲染出来的框」看结果，不看内部调用 —— 内部调用由上面几条管。
    """
    lp, containers = _grouped_page(n_groups=2, n_leaves=5)
    lp.scene.clearSelection()
    lp._gi_for(containers[0].children[0]).setSelected(True)

    frames = lp._group_overlay._frames if hasattr(lp._group_overlay, "_frames") \
        else getattr(lp._group_overlay, "frames", None)
    assert frames is not None, "取不到组框集合（overlay 结构变了？）"
    assert list(frames), "点选整组后一个组框都没画"
