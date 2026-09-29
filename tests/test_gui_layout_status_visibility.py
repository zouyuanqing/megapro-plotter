"""R9 回归：排版页的提示必须落在**用户此刻看得见**的地方。

缺陷（R9 对 C3 的对抗性复核，复现于本仓库）：C3 让「无选中点镜像」与「组内图片
第 1 次双击」都发出了中文提示，代码行确凿——但 ``status_message`` 在生产代码里
**只有一条连接**（``MainWindow`` 把它接到 ``_append_console``），而 console 在
**「控制 / 作业」**页签里。用户点镜像/双击图元时人在**「排版 / 制作」**页签，
于是提示被写进一个 ``isVisible() == False`` 的控件::

    当前 tab = 1 排版 / 制作
    console.isVisible() = False
    点『无选中镜像』后 console 新增 1 行 = '未选中图元：请先选中要镜像的图元'
    >>> 提示在不在眼前： False

即 C3「要出声的三处」在**信号层面**成立、在**用户可见层面**没变——用户唯一能
看到的仍是「按钮没亮」。

**为什么既有 C3 回归全绿**（13 passed）：它们把 ``status_message`` 接到一个本地
Python list，从头到尾**不构造 MainWindow**，所以「提示落到哪个页签的哪个控件」
这个维度完全没被覆盖。本文件补的正是这一维：**真 MainWindow + show() + 切到
tab1**，断言提示在**排版页自己身上**且当场可见。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtCore                                  # noqa: E402
from PySide6.QtWidgets import QApplication                 # noqa: E402

_app = QApplication.instance() or QApplication([])


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


def _window_on_layout_tab():
    """真 MainWindow，show() 后切到「排版 / 制作」——用户操作排版页时的真实位置。"""
    from megapro.gui.main_window import MainWindow

    w = MainWindow()
    w._pen_down_z, w._cut_touch_z, w._safe_z = 17.0, 17.5, 30.0
    w._worker = _StubWorker()
    w._homed = True
    w._link_ready = True
    w._refresh_gate_ui()
    w.show()
    w.tabs.setCurrentIndex(1)
    _app.processEvents()
    assert w.tabs.tabText(1) == "排版 / 制作", "页签顺序变了：本文件按 tab1 是排版页写"
    return w


def _grouped_image(lp):
    """在排版页里造「组 G{img, other}」，img 带 image_spec（图片件）。"""
    from megapro.gui.layout.model import Item

    img = Item(paths=[[(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0),
                      (10.0, 10.0)]], name="img",
               image_spec={"mode": "canny", "low": 50, "high": 120})
    other = Item(paths=[[(30.0, 10.0), (40.0, 10.0), (40.0, 20.0),
                         (30.0, 10.0)]], name="other")
    lp._add_items([img, other])
    cont = lp.doc.group_items([img, other], name="G")
    assert cont is not None, "编组失败"
    return img


# -- ① 无选中点镜像：提示必须当场在眼前 -------------------------------------

def test_mirror_without_selection_shows_hint_on_the_layout_page():
    """用户人在排版页点镜像 ⇒ 解释就显示在**排版页上**，不是另一个页签的 console。"""
    from megapro.gui.layout.model import Item

    w = _window_on_layout_tab()
    lp = w.layout_page
    lp._add_items([Item(paths=[[(0.0, 0.0), (5.0, 5.0), (5.0, 5.0)]], name="a")])
    # 前置：页面**有**图元，只是没选中（否则就不是「无选中」这一档）

    assert not w.console.isVisible(), \
        "前置：用户此刻在 tab1，console 不该可见（否则本用例测不到问题）"

    lp.btn_mirror["h"].click()
    _app.processEvents()

    lbl = getattr(lp, "status_label", None)
    assert lbl is not None, \
        "排版页自身没有提示控件 ⇒ 提示只有 console 一个落点，而它在另一个页签"
    assert "未选中" in lbl.text(), f"排版页没显示提示：{lbl.text()!r}"
    assert lbl.isVisible(), "提示行当前不可见 —— 用户依然看不到（沉默未破）"
    assert lp.btn_mirror["h"].isChecked() is False, "勾选态应回滚（既有 C3 契约）"


# -- ② 组内图片第 1 次双击：同理 --------------------------------------------

def test_group_edit_hint_shows_on_the_layout_page():
    """组内图片第 1 次双击的「再双击一次」提示，必须显示在排版页上。"""
    w = _window_on_layout_tab()
    lp = w.layout_page
    img = _grouped_image(lp)
    _app.processEvents()

    consumed = lp.on_item_double_clicked(img)
    _app.processEvents()

    assert consumed is True, "第 1 次双击应被进组消费"
    lbl = lp.status_label
    assert "再双击" in lbl.text(), f"排版页没显示重追提示：{lbl.text()!r}"
    assert lbl.isVisible(), "提示行不可见 ⇒ 用户仍看不到"


# -- ③ 落点归属与「不拆掉已有通道」 -----------------------------------------

def test_status_line_lives_inside_the_layout_page_not_the_job_tab():
    """提示行必须是**排版页自己的子控件**（否则修的还是同一个洞）。"""
    from PySide6.QtWidgets import QTabWidget

    w = _window_on_layout_tab()
    lp = w.layout_page

    lbl = lp.status_label
    assert lbl is not None
    assert lbl in lp.findChildren(type(lbl)), "提示行不在排版页的子控件里"
    # 祖先链必须经由排版页本尊（而不是控制页那一格）
    anc, node = [], lbl
    while node is not None:
        anc.append(node)
        node = node.parentWidget()
    assert lp in anc, f"提示行的祖先链里没有排版页：{[type(a).__name__ for a in anc]}"
    assert any(isinstance(a, QTabWidget) for a in anc), \
        "提示行不在 QTabWidget 的页面栈里"
    assert lp.isVisible(), "前置：排版页当前应当可见"


def test_console_landing_is_kept():
    """console 那条连接**保留**（作业页/复盘仍需要），只是不再是唯一落点。"""
    w = _window_on_layout_tab()
    lp = w.layout_page

    lp.status_message.emit("试一条提示")
    _app.processEvents()

    assert "试一条提示" in w.console.toPlainText(), "console 落点被拆掉了"
    assert lp.status_label.text() == "试一条提示", "排版页落点没生效"


def test_status_line_starts_empty_and_does_not_fake_success():
    """初始为空；不因为「什么都没做」就显示任何字（别把提示行变成装饰）。"""
    w = _window_on_layout_tab()

    assert w.layout_page.status_label.text() == "", "提示行初始应为空"
