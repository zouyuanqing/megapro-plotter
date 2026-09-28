"""B5 回归：图片参数对话框必须对 spec **诚实**（预填不吞 0、失效控件不装样子）。

缺陷（f2225ec 实测复现）：

1. :meth:`LayoutPage._ask_image_params` 对 Canny 低/高阈用
   ``int(spec.get("low") or 50)`` —— ``or`` 把 **0 吞成默认值**（同函数的
   threshold 用的是 ``.get(k, default)``，函数内自相矛盾）。而 0 是合法取值
   （spin 的 range 就是 0..255，产线只拦 ``low > high``）。闭环后果不是纯显示：
   用户输入 (0,30) → spec 落库 (0,30) → 重追被预填成 (50,30) → 点确定后**真的**
   用 50/30 重跑，低对比样张上直接产出 0 条触发「无线条」。即**静默换一组阈值
   重算**。
2. canny 分支只吃 low/high，但「阈值」滑块与「多阈值」勾选照常显示、照常写入
   ``image_spec``（:meth:`LayoutPage._image_spec`）⇒ spec 里躺着两个从未参与
   产线的字段（实测：spec.threshold 取 40/160/250 三次重追，折线签名逐档相同）。
3. 注释与代码矛盾：``_add_image`` 上方那行注释称 ``_import_group([paths],…)[0]``
   「只取第 [0] 条、其余静默丢弃」—— ``git show 2fb1e47`` 与现行代码逐字节相同，
   即它**从未成立**；``[0]`` 取的是「组」，Item 照持全部折线。按注释去「修」反而
   会拆散单 Item 契约、打断重追。本文件把实际契约钉住。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal_boxes(monkeypatch):
    """把 QMessageBox 打成 no-op：这些路径可能弹模态框，offscreen 下会挂死。"""
    from PySide6 import QtWidgets

    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))


def _synthetic_png(tmp_path, *, circles=((80, 150, 30), (170, 120, 34)),
                   size=(240, 288)):
    """合成样张：白底 + 深色实心圆（Canny 必出多条骨架）。"""
    import numpy as np
    from PIL import Image

    h, w = size
    arr = np.full((h, w), 255, np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    for cx, cy, r in circles:
        arr[((yy - cy) ** 2 + (xx - cx) ** 2) < r * r] = 40
    p = tmp_path / "synthetic.png"
    Image.fromarray(arr).save(str(p))
    return str(p)


# --- 对话框预填（把 QDialog.exec 换成「在此刻读控件初值」） -------------------

def _read_dialog(monkeypatch) -> dict:
    """替换 ``QDialog.exec``：接受对话框并记录各控件的**初值**。"""
    from PySide6 import QtWidgets

    seen: dict = {}

    def fake_exec(self):
        for c in self.findChildren(QtWidgets.QComboBox):
            seen["mode"] = c.currentData()
        for c in self.findChildren(QtWidgets.QCheckBox):
            seen["multi"] = c.isChecked()
            seen["multi_enabled"] = c.isEnabled()
        for c in self.findChildren(QtWidgets.QDoubleSpinBox):
            seen["mm"] = c.value()
        for c in self.findChildren(QtWidgets.QSpinBox):
            seen.setdefault("spins", []).append(c.value())
        for c in self.findChildren(QtWidgets.QSlider):
            seen["threshold"] = c.value()
            seen["threshold_enabled"] = c.isEnabled()
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(QtWidgets.QDialog, "exec", fake_exec)
    return seen


def _retrace_with_spec(page, src, spec, monkeypatch):
    """挂一个带 image_spec 的图元，跑真实重追入口。"""
    import megapro.gui.layout.layout_page as L
    from megapro.gui.layout.model import Item

    it = Item(paths=[[(0.0, 0.0), (10.0, 0.0)]], name="图", image_spec=spec)
    page._add_items([it])
    page.retrace_image_item(it)
    del L
    return it


# --- 1. 预填不得吞 0 --------------------------------------------------------

def test_retrace_prefill_keeps_zero_low_and_high(tmp_path, monkeypatch):
    """spec 里 low=0/high=0 ⇒ 预填必须是 **0/0**（不是 50/120）。

    旧行为：``or 50`` / ``or 120`` 把 0 吞掉 ⇒ 用户点确定后用 50/120 真重跑。
    """
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    seen = _read_dialog(monkeypatch)
    _retrace_with_spec(page, src, {
        "source": src, "mode": "canny", "low": 0, "high": 0,
        "target_mm": 100.0}, monkeypatch)

    assert sorted(seen["spins"]) == [0, 0], \
        f"Canny 低/高阈预填应为 0/0，实得 {seen['spins']}"
    page.deleteLater()


def test_retrace_prefill_keeps_zero_low_only(tmp_path, monkeypatch):
    """spec 里 low=0/high=30 ⇒ 预填 0/30（只吞 falsy 的那一个，不是两个都吞）。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    seen = _read_dialog(monkeypatch)
    _retrace_with_spec(page, src, {
        "source": src, "mode": "canny", "low": 0, "high": 30,
        "target_mm": 100.0}, monkeypatch)

    assert sorted(seen["spins"]) == [0, 30], \
        f"Canny 低/高阈预填应为 0/30，实得 {seen['spins']}"
    page.deleteLater()


def test_retrace_prefill_still_defaults_when_key_absent(tmp_path, monkeypatch):
    """键缺失 / 值为 None（旧的非 canny spec）⇒ 仍回默认 50/120（不被 0 修复牵连）。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    seen = _read_dialog(monkeypatch)
    _retrace_with_spec(page, src, {
        "source": src, "mode": "canny", "low": None, "high": None,
        "target_mm": 100.0}, monkeypatch)

    assert sorted(seen["spins"]) == [50, 120], \
        f"None 应回默认 50/120，实得 {seen['spins']}"
    page.deleteLater()


# --- 2. canny 下失效控件不得装样子、不得进 spec ----------------------------

def test_canny_spec_has_no_unused_fields(tmp_path, monkeypatch):
    """Canny 确认后 image_spec **只含真的生效的产线参数**。

    即 ``{source, mode, target_mm, low, high}``；``threshold``/``multi`` 不残留
    —— 它们在 canny 分支被 :meth:`_trace_image` 完全忽略，写进 spec 就是假信息。
    """
    import megapro.gui.layout.layout_page as L
    from PySide6 import QtWidgets

    src = _synthetic_png(tmp_path)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (src, "")))
    monkeypatch.setattr(
        L.LayoutPage, "_ask_image_params",
        lambda self, **kw: ("canny", 160, False, 100.0, 50, 120))
    page = L.LayoutPage()
    page._add_image()

    spec = page.doc.items[0].image_spec
    assert set(spec) == {"source", "mode", "target_mm", "low", "high"}, \
        f"canny spec 只该含生效参数，实得 {sorted(spec)}"
    assert spec["mode"] == "canny"
    assert spec["low"] == 50 and spec["high"] == 120
    assert "threshold" not in spec and "multi" not in spec
    page.deleteLater()


def test_spec_reproduces_trace_line(tmp_path, monkeypatch):
    """**spec 必须能如实复现产线**：只拿 spec 里的参数重跑 `_trace_image`。

    口径 = :meth:`_trace_image` 真的消费的参数（canny: low/high/target_mm），
    而不是「把 spec 里所有键都塞回去」—— 后者会把无效键也算进「产线输入」。
    """
    import megapro.gui.layout.layout_page as L
    from PySide6 import QtWidgets

    src = _synthetic_png(tmp_path)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (src, "")))
    monkeypatch.setattr(
        L.LayoutPage, "_ask_image_params",
        lambda self, **kw: ("canny", 160, False, 100.0, 50, 120))
    page = L.LayoutPage()
    page._add_image()
    spec = dict(page.doc.items[0].image_spec)

    from_spec = page._trace_image(
        spec["source"], mode=spec["mode"], threshold=160, use_multi=False,
        target_mm=spec["target_mm"], low=spec["low"], high=spec["high"])
    original = page._trace_image(
        src, mode="canny", threshold=160, use_multi=False, target_mm=100.0,
        low=50, high=120)
    assert from_spec == original, "照 spec 重跑必须与原产线逐字节一致"
    page.deleteLater()


def test_canny_dialog_disables_threshold_and_multi(tmp_path, monkeypatch):
    """canny 模式下「阈值」滑块与「多阈值」勾选被置灰（不装样子）。

    置灰即可，**不清控件里的值**（不销毁用户数据）；真正的「不诚实」由
    :meth:`_image_spec` 兜住 —— canny 的 spec 里不写这两个字段。
    """
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    seen = _read_dialog(monkeypatch)
    _retrace_with_spec(page, src, {
        "source": src, "mode": "canny", "low": 50, "high": 120,
        "target_mm": 100.0}, monkeypatch)

    assert seen["threshold_enabled"] is False, "canny 下阈值滑块应置灰"
    assert seen["multi_enabled"] is False, "canny 下多阈值勾选应置灰"
    page.deleteLater()


def test_non_canny_dialog_keeps_controls_enabled(tmp_path, monkeypatch):
    """非 canny 模式：两个控件照常可用（别把置灰做成一刀切）。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    seen = _read_dialog(monkeypatch)
    _retrace_with_spec(page, src, {
        "source": src, "mode": "center", "threshold": 160, "multi": False,
        "target_mm": 100.0}, monkeypatch)

    assert seen["threshold_enabled"] is True
    assert seen["multi_enabled"] is True
    page.deleteLater()


def test_canny_spec_drops_multi_even_if_checked(tmp_path, monkeypatch):
    """center 模式勾了「多阈值」再切到 canny ⇒ 产出的 spec 里**没有** multi。

    这才是「spec 必须能如实复现产线」的真判据：控件仍带着勾选（置灰但值保留），
    但它从未参与 canny 产线，就不能进 spec。直接调对话框（不跑产线），免得
    依赖某张样张在 center 模式下能否出线条。
    """
    from PySide6 import QtWidgets

    import megapro.gui.layout.layout_page as L

    got: dict = {}

    def fake_exec(self):
        combo = self.findChildren(QtWidgets.QComboBox)[0]
        cb = self.findChildren(QtWidgets.QCheckBox)[0]
        slider = self.findChildren(QtWidgets.QSlider)[0]
        cb.setChecked(True)                     # 用户在 center 下勾了多阈值
        got["before"] = (combo.currentData(), cb.isChecked(), cb.isEnabled(),
                         slider.isEnabled())
        combo.setCurrentIndex([v for _l, v in L.LayoutPage._IMAGE_MODES]
                              .index("canny"))   # ← 切到 canny
        got["after"] = (combo.currentData(), cb.isChecked(), cb.isEnabled(),
                        slider.isEnabled())
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(QtWidgets.QDialog, "exec", fake_exec)
    page = L.LayoutPage()
    params = page._ask_image_params(
        spec={"source": "x.png", "mode": "center", "threshold": 160,
              "multi": False, "target_mm": 100.0})

    assert got["before"] == ("center", True, True, True), got["before"]
    assert got["after"] == ("canny", True, False, False), got["after"]
    mode, th, multi, mm, lo, hi = params
    spec = page._image_spec("x.png", mode=mode, threshold=th, use_multi=multi,
                            target_mm=mm, low=lo, high=hi)
    assert set(spec) == {"source", "mode", "target_mm", "low", "high"}, \
        f"canny spec 不该带 multi/threshold，实得 {sorted(spec)}"
    page.deleteLater()


def test_non_canny_spec_still_records_threshold_and_multi():
    """center/outline 照旧记 threshold/multi（别把「诚实」做成「一刀切少字段」）。

    这里直接断 spec 构造器（产线分派与本条无关，且不该依赖某张样张出不出线）。
    """
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    spec = page._image_spec("x.png", mode="center", threshold=77,
                            use_multi=True, target_mm=90.0, low=50, high=120)
    assert spec["mode"] == "center"
    assert spec["threshold"] == 77 and spec["multi"] is True
    assert "low" not in spec and "high" not in spec, \
        f"非 canny 不该记 Canny 阈值，实得 {sorted(spec)}"
    assert spec["source"] == "x.png" and spec["target_mm"] == 90.0
    page.deleteLater()


# --- 3. 注释说的那件事：单 Item 持全部折线 --------------------------------

def test_single_item_keeps_every_canny_polyline(tmp_path, monkeypatch):
    """Canny 的多条折线必须全在**一个** Item 里（重追的承重契约）。

    钉的是「实际行为」，不是那条写反了的注释：``_import_group([paths],…)[0]``
    的 ``[0]`` 取的是**组**，Item 照持全部折线。谁按注释去改成「每条折线一项」，
    本用例即红。
    """
    import megapro.gui.layout.layout_page as L
    from PySide6 import QtWidgets

    src = _synthetic_png(tmp_path)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (src, "")))
    monkeypatch.setattr(
        L.LayoutPage, "_ask_image_params",
        lambda self, **kw: ("canny", 160, False, 100.0, 50, 120))
    page = L.LayoutPage()
    page._add_image()

    assert len(page.doc.items) == 1, "一个物体组 = 一个 Item"
    it = page.doc.items[0]
    assert len(it.paths) >= 2, "Canny 对两个圆应产出多条折线，一条不丢"
    assert len(it.transformed_paths()) == len(it.paths)
    page.deleteLater()


# --- 4. 「诚实」成立的前提本身也必须被钉住 --------------------------------

def _gradient_png(tmp_path):
    """低对比渐变图：二值化阈值在这里**真的起作用**（硬边圆的 Canny 不吃阈值）。

    用它做对照，才能在**同一张图、同一组参数**下只让 mode 成为变量。
    """
    import numpy as np
    from PIL import Image

    h, w = 288, 240
    yy, xx = np.mgrid[0:h, 0:w]
    arr = np.clip(120 + 60 * np.sin(xx / 25.0) * np.cos(yy / 30.0),
                  0, 255).astype(np.uint8)
    p = tmp_path / "gradient.png"
    Image.fromarray(arr).save(str(p))
    return str(p)


def test_canny_really_ignores_threshold_and_multi():
    """**B5 的前提**：canny 分支真的不读 ``threshold``/``multi``。

    「canny 的 spec 不写这两个字段」这条诚实性契约是**单向**依赖本用例的：
    只要 ``_trace_image`` 的 canny 分支哪天开始读 ``threshold``，spec 就从
    「不写无效字段」悄悄翻成「**漏记生效参数**」—— 比原来更坏，而本文件里
    现有的 spec 用例**一条都不会红**（它们只断 spec 的键集合，不断
    ``_trace_image`` 到底吃不吃）。故此处把前提本身钉住。

    ⚠ **两个方向都断**，否则「canny 不吃阈值」会退化成「谁都不吃阈值」：
    同一张渐变图、同一组参数下，``center``/``outline`` **必须**随 threshold
    变化（实测折线数 42 / 406 / 107 互不相同），canny 则**逐字节恒等**。
    """
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    src = _gradient_png(tmp_path)

    def traced(mode, *, threshold, use_multi=False):
        return page._trace_image(
            src, mode=mode, threshold=threshold, use_multi=use_multi,
            target_mm=100.0, low=50, high=120)

    canny = {th: traced("canny", threshold=th) for th in (60, 120, 200)}
    baseline = canny[120]
    assert baseline, "基准本身应产出折线，否则本用例空过"
    for th, out in canny.items():
        assert out == baseline, (
            f"canny 模式在 threshold={th} 上产出了不同结果 —— canny 分支"
            "竟然开始读 threshold 了。此时 canny 的 spec 必须**补回**"
            f" threshold（否则是漏记生效参数，比 B5 修的还糟）。实得折线数 "
            f"{len(out)} vs 基准 {len(baseline)}")
    assert traced("canny", threshold=120, use_multi=True) == baseline, \
        "canny 模式竟然开始读 use_multi 了"

    # 反向对照：非 canny 模式**必须**对 threshold 敏感，否则上面等于空过
    for mode in ("center", "outline"):
        outs = [traced(mode, threshold=th) for th in (60, 120, 200)]
        assert len({len(o) for o in outs}) > 1, (
            f"{mode} 模式对 threshold 毫无反应 —— 说明「canny 不吃阈值」"
            "只是因为这张图/这条路径对谁都不吃，对照失效，换张图再判")
    page.deleteLater()
