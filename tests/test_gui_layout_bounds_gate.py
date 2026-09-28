"""C1 回归：排版页越界导出**真正拦截**（默认拒绝 + 显式「忽略并继续」出口）。

背景（C1 实测）：``layout_page._on_export`` 弹完 QMessageBox 后**无条件**
``export_requested.emit`` —— 问「仍导出？」却从不读答案。切纸机上这就是
「静默地切到床外」：越界几何走 make/JobSpec → 绝对 G0 → 切纸刀落在床面外
（夹具上 / 纸外），且 ok≠已移动，软件侧无任何二次提示。

本文件钉的不变量（**会出错的事**，不是会变的量）：

- **默认拒绝**：越界时不点任何按钮（直接关窗）⇒ 绝不 emit；
- **尊重答案**：点「取消」⇒ 绝不 emit；点「忽略并继续」⇒ 才 emit；
- **判定对称**：越界量为 0（严格在床内 / 恰好贴边）⇒ **根本不弹框**直接通过
  —— 拦截不能变成误报骚扰；
- **两出口同判**：「另存为 SVG」与「导出并送去执行」共用同一判据；
- **口径含祖先链**：非恒等组内的叶子按**页面系**判越界（局部坐标会漏报）；
- **床面取常量**：文案里的床面尺寸来自 ``BED_W``/``BED_H``，不是硬编码 210。

QMessageBox 被整类替身替换，测试里不会弹出真实模态框。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# -- QMessageBox 替身 ------------------------------------------------------
# 必须整类替换：layout_page 走 ``QtWidgets.QMessageBox``，只替静态方法无法
# 表达「用户点了哪个按钮」。替身保留按钮身份，使实现只能用
# ``clickedButton()`` 判定 —— 关窗（X）时 clickedButton() 为 None ⇒ 拒绝。

_RealMsgBox = QtWidgets.QMessageBox


class _FakeButton:
    def __init__(self, text, role):
        self._text = text
        self.role = role
        self.is_default = False

    def text(self):
        return self._text

    def setDefault(self, on):
        self.is_default = bool(on)


class _FakeMsgBox:
    """记录文案与按钮；:attr:`click` 指定「用户点了哪个按钮文案」。

    ``click is None`` 模拟用户直接关窗（X）—— 这是**默认拒绝**的判据。
    """

    instances: list = []
    warned: list = []
    click: "str | None" = None

    def __init__(self, parent=None, *_a, **_kw):
        self.title_ = ""
        self.text_ = ""
        self.info_ = ""
        self.buttons: list = []
        self._clicked = None
        _FakeMsgBox.instances.append(self)

    # 角色/图标常量照抄真实 Qt 值，替身被换上后实现仍能取到
    AcceptRole = _RealMsgBox.AcceptRole
    RejectRole = _RealMsgBox.RejectRole
    Critical = _RealMsgBox.Critical
    Warning = _RealMsgBox.Warning

    def setIcon(self, *_a):
        pass

    def setWindowTitle(self, t):
        self.title_ = t

    def setText(self, t):
        self.text_ = t

    def setInformativeText(self, t):
        self.info_ = t

    def addButton(self, text, role=None, *_a):
        b = _FakeButton(text, role)
        self.buttons.append(b)
        return b

    def setDefaultButton(self, b):
        for x in self.buttons:
            x.is_default = False
        if b is not None:
            b.is_default = True

    def defaultButton(self):
        return next((b for b in self.buttons if b.is_default), None)

    def exec(self):
        # ⚠ 必须与**真实 Qt** 一致：``QMessageBox.exec()`` 返回的是被点按钮的
        # **StandardButton 角色值**（实测 忽略并继续→2、取消→3），**不是**
        # ``QDialog.Accepted``(1)/``Rejected``(0)。故这里回角色值而非 1/0 ——
        # 否则「拿 exec() 和 QDialog.Accepted 比」这种错误实现会蒙混过关：
        # 那个比较在真实 Qt 下对**两个按钮都是 False**（出口成了死代码）。
        self._clicked = None
        for b in self.buttons:
            if _FakeMsgBox.click is not None and b.text() == _FakeMsgBox.click:
                self._clicked = b
        if self._clicked is None:
            return 0  # 关窗 = QDialog.Rejected
        return self._clicked.role

    def clickedButton(self):
        return self._clicked

    def button(self, text):
        return next((b for b in self.buttons if b.text() == text), None)

    @property
    def body(self):
        return self.title_ + "\n" + self.text_ + "\n" + self.info_

    @staticmethod
    def warning(parent, title, text, *_a, **_kw):
        """旧实现走这里（无答案可读 ⇒ 静默放行）。记录下来供断言。"""
        _FakeMsgBox.warned.append((title, text))
        return _RealMsgBox.StandardButton.Ok

    @staticmethod
    def information(parent, title, text, *_a, **_kw):
        _FakeMsgBox.warned.append((title, text))
        return _RealMsgBox.StandardButton.Ok


@pytest.fixture
def msgbox(monkeypatch):
    """装上 QMessageBox 替身；用 :attr:`click` 模拟用户选择。"""
    _FakeMsgBox.instances = []
    _FakeMsgBox.warned = []
    _FakeMsgBox.click = None
    monkeypatch.setattr(QtWidgets, "QMessageBox", _FakeMsgBox)
    return _FakeMsgBox


# -- 版面构造 --------------------------------------------------------------


def _page():
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    return lp


def _rect(x0, y0, x1, y1, *, name="r"):
    """左上角定位的矩形图元（pos=(x0,y0)，本地尺寸 x1-x0 / y1-y0）。"""
    from megapro.gui.layout.model import Item

    return Item(paths=[[(float(x0), float(y0)), (float(x1), float(y0)),
                        (float(x1), float(y1)), (float(x0), float(y1)),
                        (float(x0), float(y0))]],
                name=name)


def _out_of_bed_page():
    """一页一个越界图元：页面系 x 20..230 ⇒ 右侧超出 210 共 20mm。"""
    lp = _page()
    lp._add_items([_rect(20, 20, 230, 40, name="big")])
    return lp


def _in_bed_page():
    lp = _page()
    lp._add_items([_rect(10, 10, 50, 50, name="ok")])
    return lp


def _edge_page():
    """恰好贴边（bbox 恰为 0..210）—— 越界量 0，不应弹框。"""
    lp = _page()
    lp._add_items([_rect(0, 0, 210, 210, name="flush")])
    return lp


def _collect(lp):
    """接住 export_requested 的全部发射。"""
    got: list = []
    lp.export_requested.connect(got.append)
    return got


# -- ① 取消 ⇒ 不导出 --------------------------------------------------------


def test_out_of_bed_cancel_does_not_emit(msgbox):
    """点「取消」⇒ 既不 emit，也不留任何作业规格。"""
    lp = _out_of_bed_page()
    got = _collect(lp)

    msgbox.click = "取消"
    lp._on_export()

    assert got == [], "取消后仍 emit export_requested —— 会把越界几何送去动刀"
    # 旧实现走 QMessageBox.warning（无答案可读）⇒ 这里必须为空
    assert msgbox.warned == [], (
        "越界判定仍用无返回值的 QMessageBox.warning，"
        "用户的选择无处可读（静默放行的根因）")


def test_out_of_bed_dialog_close_is_deny(msgbox):
    """直接关窗（X）⇒ 等价取消，**默认拒绝**。"""
    lp = _out_of_bed_page()
    got = _collect(lp)

    msgbox.click = None  # 没点任何按钮
    lp._on_export()

    assert got == [], "关窗等同取消；越界几何不得默认放行"


# -- ② 忽略并继续 ⇒ 出口可用且规格正确 ---------------------------------------


def test_out_of_bed_ignore_emits_correct_spec(msgbox):
    """「忽略并继续」⇒ emit，且 JobSpec 携带的正是当前页可见几何。"""
    from megapro.gui.layout.model import flatten_visible

    lp = _out_of_bed_page()
    got = _collect(lp)

    msgbox.click = "忽略并继续"
    lp._on_export()

    assert len(msgbox.instances) == 1, "越界未走可确认对话框"
    assert len(got) == 1, "「忽略并继续」后未导出 —— 出口被堵死"
    spec = got[0]
    assert spec.source_name == "排版版面"
    assert spec.placement.mode == "preserve"
    assert spec.paths_paper == flatten_visible(lp.doc)


def test_out_of_bed_dialog_default_is_cancel(msgbox):
    """「取消」是默认按钮（回车/焦点在取消上），「忽略并继续」不是。"""
    lp = _out_of_bed_page()
    msgbox.click = "取消"
    lp._on_export()

    box = msgbox.instances[0]
    assert box.defaultButton() is box.button("取消")
    assert box.defaultButton() is not box.button("忽略并继续")


def test_out_of_bed_dialog_reports_amount_and_not_hardcoded(msgbox):
    """文案回显具体越界量，且床面尺寸取 BED_W/BED_H 常量而非硬编码 210。"""
    from megapro.gui.canvas.coords import BED_H, BED_W

    lp = _out_of_bed_page()  # x1 = 230 ⇒ 超出 20.0mm
    msgbox.click = "取消"
    lp._on_export()

    box = msgbox.instances[0]
    body = box.body
    assert "20.0" in body, f"未回显具体越界量：{body!r}"
    assert f"{BED_W:g}" in body and f"{BED_H:g}" in body
    assert box.button("忽略并继续") is not None
    assert box.button("取消") is not None
    # 越界清单要指名道姓，用户才知道自己在绕过什么
    assert "big" in body


def test_exec_return_is_role_value_not_dialog_code(msgbox):
    """钉住「判据只能用 ``clickedButton()``」这个前提。

    真实 Qt 实测：``QMessageBox.exec()`` 返回被点按钮的 **StandardButton 角色值**
    （忽略并继续→2、取消→3），而 ``QDialog.Accepted`` 是 1。所以
    ``box.exec() == QDialog.Accepted`` 对**两个按钮都是 False** ——
    「忽略并继续」会被当成取消，出口成了死代码。本例同时守护替身不漂移。
    """
    lp = _out_of_bed_page()
    got = _collect(lp)
    msgbox.click = "忽略并继续"
    lp._on_export()

    box = msgbox.instances[0]
    assert box.exec() != QtWidgets.QDialog.Accepted, (
        "替身不再模拟真实 Qt 的 exec() 语义 —— 本文件的判据前提已失效")
    assert len(got) == 1, "「忽略并继续」未放行"


# -- ③ 无越界 ⇒ 不打扰 ------------------------------------------------------


def test_in_bed_no_dialog_and_emits(msgbox):
    """在床内 ⇒ 根本不弹框，直接导出。"""
    lp = _in_bed_page()
    got = _collect(lp)

    lp._on_export()

    assert msgbox.instances == [], "床内几何不应弹框（拦截变成误报骚扰）"
    assert msgbox.warned == []
    assert len(got) == 1


def test_flush_to_bed_edge_is_not_out_of_bed(msgbox):
    """恰好贴到床沿（bbox == 0..210）⇒ 越界量 0，不弹框。"""
    lp = _edge_page()
    got = _collect(lp)

    lp._on_export()

    assert msgbox.instances == []
    assert len(got) == 1


# -- ④ 两个出口同判 ---------------------------------------------------------


def test_save_svg_out_of_bed_cancel_writes_nothing(msgbox, tmp_path, monkeypatch):
    """另存为 SVG：越界 + 取消 ⇒ 连文件对话框都不问，磁盘上不落文件。"""
    lp = _out_of_bed_page()
    status: list = []
    lp.status_message.connect(status.append)

    asked: list = []

    def _fake_save(*_a, **_kw):
        asked.append(True)
        return str(tmp_path / "o.svg"), ""

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", _fake_save)

    msgbox.click = "取消"
    lp._on_save_svg()

    assert asked == [], "取消后仍弹出保存对话框"
    assert not (tmp_path / "o.svg").exists(), "取消后仍写出了越界 SVG"
    assert status == []
    assert len(msgbox.instances) == 1, "另存为 SVG 未走同一越界判据"


def test_save_svg_in_bed_writes_file(msgbox, tmp_path, monkeypatch):
    """床内另存为 SVG 正常落盘（拦截不能误伤正常路径）。"""
    lp = _in_bed_page()
    target = tmp_path / "o.svg"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(target), ""))
    msgbox.click = "取消"

    lp._on_save_svg()

    assert msgbox.instances == []
    assert target.exists() and target.read_text(encoding="utf-8").strip()


def test_save_svg_ignore_writes_file(msgbox, tmp_path, monkeypatch):
    """「忽略并继续」⇒ 另存为 SVG 正常落盘。"""
    lp = _out_of_bed_page()
    target = tmp_path / "o.svg"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(target), ""))
    msgbox.click = "忽略并继续"

    lp._on_save_svg()

    assert len(msgbox.instances) == 1
    assert target.exists()


# -- ⑤ 口径：祖先链 ---------------------------------------------------------


def test_bounds_check_uses_page_coords_through_group(msgbox):
    """组内叶子局部坐标在床内、页面系在床外 ⇒ 必须报越界。

    裸 ``item.page_bbox()`` 走不到祖先 → 编组后漏报（切纸 = 切到床外）。
    """
    from megapro.gui.layout.model import Item

    lp = _page()
    a = Item(paths=[[(0.0, 0.0), (20.0, 20.0)]], name="leafA", z=0.0)
    b = Item(paths=[[(0.0, 30.0), (20.0, 50.0)]], name="leafB", z=1.0)
    lp._add_items([a, b])
    cont = lp.doc.group_items([a, b], name="G")  # 成员不足 2 → None
    assert cont is not None
    # 局部 x 0..20 在床内；容器平移到 x=200 后页面系 200..220 ⇒ 越界 10mm
    cont.pos = (200.0, 0.0)
    lp._sync_models()

    got = _collect(lp)
    msgbox.click = "取消"
    lp._on_export()

    assert len(msgbox.instances) == 1, "编组后越界漏报（组内叶子未按页面系判定）"
    body = msgbox.instances[0].body
    assert "10.0" in body, f"未回显 10.0mm 越界量：{body!r}"
    assert got == []


def test_hidden_out_of_bed_item_does_not_block(msgbox):
    """隐藏图元不进导出 ⇒ 也不应参与越界判定（与可见几何同口径）。"""
    lp = _out_of_bed_page()
    lp._add_items([_rect(200, 200, 400, 400, name="ghost")])
    lp.doc.items[-1].visible = False

    got = _collect(lp)
    lp._on_export()

    assert len(msgbox.instances) == 1, "隐藏图元的越界不应放行可见图元的导出"
    assert got == []


def test_multiple_out_of_bed_units_all_reported(msgbox):
    """多处越界 ⇒ 文案里都能找到（只报第一个会让人漏改）。"""
    lp = _page()
    lp._add_items([_rect(20, 20, 230, 40, name="wide")])
    lp._add_items([_rect(20, 20, 40, 260, name="tall")])

    msgbox.click = "取消"
    lp._on_export()

    body = msgbox.instances[0].body
    assert "wide" in body and "tall" in body
