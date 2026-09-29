"""R11 回归：工具条上「选择不足」必须**出声**，不许裸 return。

缺陷（R11 对 C3 的对抗性复核，复现于本仓库）：C3 把「界面撒谎 / 静默吞操作」
这一类在排版页收口，①（无选中点镜像）是样板；但同一条工具条上紧邻的另外七
个按钮仍是纯静默 return，零 ``status_message``：

    ``_zorder``     ``if not sel: return``            （置顶/置底/上移/下移）
    ``_align``      ``if len(units) < 2: return``     （左对齐/水平居中/垂直居中）
    ``_distribute`` ``if len(units) < 3: return``     （水平分布/垂直分布）

这不是「静默是既定设计」——**同文件**的 ``group_selected``「编组需选中至少两个
图元」、``ungroup_selected``「请选中组内图元后解组」、``_on_page_del``「至少保留
一页」在**完全相同**的「选择不足」条件下都发中文提示；唯独这几个不接。

而它们**既不禁用、也没有 tooltip**（实测 ``isEnabled() == True``、``toolTip() ==
''``）⇒ 排除「禁用即自证」这条最强反驳。叠加 R9（提示即便发了也不在眼前），
用户在排版页按「置顶」/「左对齐」得到的是**纯粹的零反馈**。

**本文件钉的是「整条工具条不再有静默按钮」这个类**，而不是逐条点名：新增按钮若
忘了接提示，扫一遍就红。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets                            # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.layout.layout_page import LayoutPage     # noqa: E402
from megapro.gui.layout.model import Item                 # noqa: E402


def _rect(x0, y0, name):
    return Item(paths=[[(x0, y0), (x0 + 5, y0), (x0 + 5, y0 + 5),
                        (x0, y0 + 5), (x0, y0)]], name=name)


def _page(n_selected=0, n_items=4):
    lp = LayoutPage()
    items = [_rect(5 + 12 * i, 5, f"i{i}") for i in range(n_items)]
    for it in items:
        lp._add_items([it])
    lp._sync_models()
    for it in items[:n_selected]:
        lp._gi_for(it).setSelected(True)
    msgs: list = []
    lp.status_message.connect(msgs.append)
    return lp, items, msgs


# 工具条上「选择不足就静默 return」的全部入口（**按钮文案** → 调用）。
# 刻意与 layout_page.py 构造工具条的那个元组一一对应：新增按钮若忘了接提示，
# 扫一遍就红。工具条上对齐只有「左对齐 / 水平居中」两个、分布只有「垂直分布」
# 一个（``_align``/``_distribute`` 的其余 mode 仍被直接调用测到）。
_TOOLBAR_GUARDS = {
    "置顶": lambda lp: lp._zorder("top"),
    "置底": lambda lp: lp._zorder("bottom"),
    "上移": lambda lp: lp._zorder("up"),
    "下移": lambda lp: lp._zorder("down"),
    "左对齐": lambda lp: lp._align("left"),
    "水平居中": lambda lp: lp._align("hcenter"),
    "垂直分布": lambda lp: lp._distribute("v"),
    "删除": lambda lp: lp._delete_selected(),
}

#: 同三个守卫的**全部 mode**（含工具条上没露出的），直接调函数覆盖。
_ALL_GUARDS = dict(_TOOLBAR_GUARDS, **{
    "_align/垂直居中": lambda lp: lp._align("vcenter"),
    "_distribute/水平": lambda lp: lp._distribute("h"),
})


# -- ① 无选中：十个入口全部必须出声 ------------------------------------------

@pytest.mark.parametrize("button", sorted(_ALL_GUARDS))
def test_toolbar_button_announces_when_nothing_selected(button):
    """无选中时点任一工具条按钮 ⇒ 必须有中文提示（不許裸静默）。"""
    lp, _items, msgs = _page(n_selected=0)

    _ALL_GUARDS[button](lp)

    assert msgs, f"「{button}」在无选中时零反馈 —— 用户不知道该先选中"
    assert any("选中" in m or "单元" in m for m in msgs), \
        f"「{button}」的提示未说明原因/门槛：{msgs}"


@pytest.mark.parametrize("button", sorted(_ALL_GUARDS))
def test_toolbar_button_announces_when_one_selected(button):
    """只选 1 个时：对齐/分布的门槛（2/3 个）不满足 ⇒ 同样要出声。

    与无选中那一档分开跑：这两档走的是**不同**的守卫（``not sel`` vs
    ``len(units) < N``），修复其中一处不代表另一处也被接上。
    """
    lp, _items, msgs = _page(n_selected=1)

    _ALL_GUARDS[button](lp)

    if button not in ("左对齐", "水平居中", "垂直分布", "_align/垂直居中",
                      "_distribute/水平"):
        pytest.skip("层序/删除单选是**合法**输入，不该有提示（另有用例钉）")
    assert msgs, f"「{button}」在只选 1 个时零反馈（门槛 2/3 未满足）"
    assert any("单元" in m for m in msgs), f"提示未说明门槛：{msgs}"


# -- ② 提示内容要对得上各自的门槛 -------------------------------------------

def test_zorder_message_says_select_something():
    lp, _items, msgs = _page(n_selected=0)
    lp._zorder("top")
    assert "未选中" in msgs[-1] and "层序" in msgs[-1], msgs[-1]


def test_align_message_reports_the_threshold_and_current_count():
    lp, _items, msgs = _page(n_selected=1)
    lp._align("left")
    assert "2" in msgs[-1] and "1" in msgs[-1], \
        f"对齐提示应同时给出门槛(2)与当前数量(1)：{msgs[-1]}"


def test_distribute_message_reports_the_threshold_and_current_count():
    lp, _items, msgs = _page(n_selected=2)
    lp._distribute("h")
    assert "3" in msgs[-1] and "2" in msgs[-1], \
        f"分布提示应同时给出门槛(3)与当前数量(2)：{msgs[-1]}"


# -- ③ 合法输入不许被误伤（提示只在门槛不满足时出现） ------------------------

def test_valid_zorder_input_says_nothing():
    """层序单选是合法输入 ⇒ 不得误报「未选中」。"""
    lp, items, msgs = _page(n_selected=1)
    lp._zorder("top")
    assert msgs == [], f"单选层序是合法的，却报了提示：{msgs}"


def test_valid_align_and_distribute_input_say_nothing():
    """2 个可对齐、3 个可分布 ⇒ 不得误报。"""
    lp, _items, msgs = _page(n_selected=2)
    lp._align("left")
    assert msgs == [], f"2 个对齐是合法的，却报了提示：{msgs}"

    lp2, _items2, msgs2 = _page(n_selected=3)
    lp2._distribute("h")
    assert msgs2 == [], f"3 个分布是合法的，却报了提示：{msgs2}"


def test_valid_delete_says_nothing():
    lp, _items, msgs = _page(n_selected=1)
    lp._delete_selected()
    assert msgs == [], f"有选中时删除不该报提示：{msgs}"


# -- ④ 「禁用即自证」这条路被排除 -------------------------------------------

@pytest.mark.parametrize("button", sorted(_TOOLBAR_GUARDS))
def test_toolbar_buttons_are_not_disabled_so_announcing_is_the_only_channel(button):
    """这些按钮**不禁用** ⇒ 提示是唯一反馈通道，不能指望「灰按钮」自证。

    若将来有人把它们改成按选择态 setEnabled(False)，本文件会提醒重新评估
    （那时「不出声」就不再是唯一反馈了，但文案仍应保留）。
    """
    lp, _items, _msgs = _page(n_selected=0)
    btn = _find_button(lp, button)
    assert btn is not None, f"工具条上找不到按钮「{button}」（文案变了？）"
    assert btn.isEnabled(), f"「{button}」被禁用了，与本批前提不符"


def _find_button(lp, text):
    for b in lp.findChildren(QtWidgets.QToolButton):
        if b.text() == text:
            return b
    for b in lp.findChildren(QtWidgets.QPushButton):
        if b.text() == text:
            return b
    return None
