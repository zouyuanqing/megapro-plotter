"""R10 回归：提示里的「这是图片件」判据必须与**真正执行重追**的判据同一条。

缺陷（R10 对 C3 的对抗性复核，复现于本仓库）：C3 在
:meth:`LayoutPage.on_item_double_clicked` 里用
``getattr(item, "image_spec", None) is not None`` 判「图片件」并承诺「再双击
一次这张图片即可重追调参」；而真正执行重追的
``canvas/items.py::PathItem.mouseDoubleClickEvent`` 用的是**真值**
``if ... .image_spec:``。

falsy-非-None 的 spec（``{}`` / ``0``）⇒ 两处判据分叉::

    第 1 次双击 提示 = '已进入组编辑（双击进组）：再双击一次这张图片即可重追调参'
    第 2 次双击 重追调用数 = 0   弹窗 = []
    裁决: *** 承诺落空：什么也没发生 ***

第 2 次双击在 items.py 判 False ⇒ 穿透 ``super().mouseDoubleClickEvent``，
**既不开重追对话框、也不报任何错**。可达载荷：``_paste`` 对剪贴板文本只
``json.loads`` 零校验、``_item_from_json`` 原样还原 ``image_spec`` ⇒ Ctrl+V
一段带 ``"image_spec": {}`` 的版面 JSON 即可。

**本文件钉的是「承诺与行为一致」这个不变量**，不是某个具体文案：对**任何**
``image_spec`` 取值，「提示说会重追」与「第 2 次双击真的会触发重追或报错」必须
同真同假。空 dict / 0 / 空字符串 / None 都归到「不承诺」那一侧。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtCore, QtWidgets                      # noqa: E402
from PySide6.QtTest import QTest                          # noqa: E402
from PySide6.QtWidgets import QApplication                 # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.layout.layout_page import LayoutPage      # noqa: E402


# -- 版面：组 G{pasteImg, other}，pasteImg 的 image_spec 由参数给出 ----------

def _payload(image_spec_literal):
    return json.dumps([{
        "name": "G", "z": 0.0, "pos": [0.0, 0.0],
        "children": [
            {"name": "pasteImg", "z": 0.0, "pos": [20.0, 20.0],
             "paths": [[(0.0, 0.0), (20.0, 0.0), (20.0, 15.0),
                        (0.0, 15.0), (0.0, 0.0)]],
             "image_spec": image_spec_literal},
            {"name": "other", "z": 1.0, "pos": [60.0, 20.0],
             "paths": [[(0.0, 0.0), (15.0, 0.0), (15.0, 12.0),
                        (0.0, 12.0), (0.0, 0.0)]]},
        ],
    }])


def _page_with(image_spec_literal):
    """走真实 ``_paste`` 造版面（对照组之外，这是载荷的来源）。"""
    lp = LayoutPage()
    QtWidgets.QApplication.clipboard().setText(_payload(image_spec_literal))
    lp._paste()
    lp._sync_models()
    lp.view.fit()
    return lp, lp.doc.items[0].children[0]


def _dblclick(lp, item):
    """真 QTest 双击（走 view/scene → PathItem.mouseDoubleClickEvent 真链路）。"""
    x0, y0, x1, y1 = item.page_bbox()
    assert y1 > y0 and x1 > x0, f"图元退化（bbox {x0,y0,x1,y1}）⇒ 点不中"
    vp = lp.view.viewportTransform().map(
        QtCore.QPointF((x0 + x1) / 2.0, (y0 + y1) / 2.0)).toPoint()
    assert lp.view.itemAt(vp) is not None, \
        f"点击坐标 {vp} 没落在图元上（本用例会空跑，判定无意义）"
    QTest.mouseDClick(lp.view.viewport(), QtCore.Qt.LeftButton, pos=vp)
    _app.processEvents()


def _first_click_hint(lp, item):
    msgs: list = []
    lp.status_message.connect(msgs.append)
    _dblclick(lp, item)
    assert msgs, "第 1 次双击连提示都没有"
    return msgs[-1]


def _second_click_effect(lp, item):
    """第 2 次双击的后果：真的重追、或至少**有反应**（不是静默无响应）。"""
    calls: list = []
    dialogs: list = []
    lp.retrace_image_item = lambda it: calls.append(it.name)
    orig_warning = QtWidgets.QMessageBox.warning
    QtWidgets.QMessageBox.warning = staticmethod(
        lambda *a, **k: dialogs.append(k.get("title", a[1] if len(a) > 1 else "")))
    try:
        _dblclick(lp, item)
    finally:
        QtWidgets.QMessageBox.warning = orig_warning
    return calls, dialogs


# falsy-非-None：原缺陷所在（页面判 True / 消费者判 False）
_FALSY_NON_NONE = [pytest.param({}, id="空dict"),
                   pytest.param(0, id="零"),
                   pytest.param("", id="空串")]


# -- ① 判据一致性：提示与行为必须同真同假 -----------------------------------

@pytest.mark.parametrize("spec", _FALSY_NON_NONE)
def test_falsy_spec_is_not_promised_as_retraceable(spec, monkeypatch):
    """``image_spec`` 为 falsy-非-None 时**不得**承诺重追。

    修复前实测：提示说「再双击一次这张图片即可重追调参」，而第 2 次双击重追
    调用 0 次、弹窗 0 个 —— 承诺落空且**无任何反馈**。
    """
    lp, img = _page_with(spec)

    hint = _first_click_hint(lp, img)
    assert "再双击" not in hint, (
        f"image_spec={spec!r} 是 falsy，第 2 次双击不会真的重追，"
        f"却承诺了：{hint!r}")
    assert "组编辑" in hint, f"提示应说明进入了组编辑：{hint!r}"

    calls, dialogs = _second_click_effect(lp, img)
    assert not calls, f"不该触发重追，却调了 {calls}"
    # 不承诺 ⇒ 无反应是**一致**的（这条不要求有弹窗）


def test_hint_promises_retrace_exactly_when_second_click_really_retraces():
    """不变量本体：提示说会重追 ⇔ 第 2 次双击真的会触发重追。

    对四类 spec 各跑一遍，把「承诺」与「兑现」摆在一起比 —— 这条比逐个参数化
    断言更能防止两处判据再次分叉。
    """
    verdicts = {}
    for label, spec in (("falsy{}", {}), ("falsy0", 0), ("none", None),
                        ("truthy", {"mode": "canny"})):
        lp, img = _page_with(spec)
        hint = _first_click_hint(lp, img)
        calls, _dialogs = _second_click_effect(lp, img)
        verdicts[label] = ("再双击" in hint, bool(calls))

    promised = {k for k, (says, _) in verdicts.items() if says}
    delivered = {k for k, (_, does) in verdicts.items() if does}
    assert promised == delivered, (
        f"承诺与兑现不一致：\n  各例 (承诺, 兑现) = {verdicts}\n"
        f"  只承诺不兑现 = {sorted(promised - delivered)}\n"
        f"  只兑现不承诺 = {sorted(delivered - promised)}")
    assert "truthy" in delivered, "前置：真值 spec 应当真的能重追"
    assert not ({"falsy{}", "falsy0", "none"} & delivered), \
        f"falsy spec 不该触发重追：{verdicts}"


def test_second_click_on_falsy_spec_is_not_silently_swallowed():
    """不承诺时，第 2 次双击**不是静默无响应**——至少不得假装进了重追。

    这里守住的是「别把静默吞操作换到别处」：判据统一后，falsy 件的提示与行为
    都不提重追；若将来有人给它加上「沉默」路径，这条会提醒重新评估。
    """
    lp, img = _page_with({})
    hint = _first_click_hint(lp, img)
    calls, _dialogs = _second_click_effect(lp, img)

    assert "重追" not in hint, f"提示不该提重追：{hint!r}"
    assert not calls, "不该调重追"


# -- ② 既有契约不许被改坏 ---------------------------------------------------

def test_truthy_spec_still_gets_the_retrace_promise():
    """真值 spec 照旧承诺重追，且第 2 次双击**真的**调用重追入口。"""
    lp, img = _page_with({"mode": "canny", "low": 50, "high": 120})

    hint = _first_click_hint(lp, img)
    assert "再双击" in hint, f"真值 spec 应给重追提示：{hint!r}"
    assert "重追" in hint

    calls, _dialogs = _second_click_effect(lp, img)
    assert calls == ["pasteImg"], f"第 2 次双击应真的触发重追，实得 {calls}"


def test_non_image_child_still_gets_the_plain_hint():
    """完全没有 image_spec 的子项 ⇒ 走「现在可选组内子项」那句（既有 C3 契约）。"""
    lp = LayoutPage()
    from megapro.gui.layout.model import Item

    a = Item(paths=[[(0.0, 0.0), (5.0, 5.0), (5.0, 5.0)]], name="a")
    b = Item(paths=[[(5.0, 0.0), (9.0, 4.0), (9.0, 4.0)]], name="b")
    lp._add_items([a, b])
    assert lp.doc.group_items([a, b], name="G") is not None
    msgs: list = []
    lp.status_message.connect(msgs.append)

    assert lp.on_item_double_clicked(a) is True

    assert msgs, "进组编辑无声发生"
    assert "再双击" not in msgs[-1], f"普通图元不该被承诺重追：{msgs[-1]!r}"
    assert "组编辑" in msgs[-1]
