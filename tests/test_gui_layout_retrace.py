"""M5 图片二次追踪（FR-10 / T10）—— 模型字段 + 撤销命令 + 产线分派 + 重追。

算法本身（Canny+thin 骨架）由并行的 T11 分支落在 ``edge_to_svg.py`` 并自带
合成样张 golden（``tests/test_gui_edge.py``）；本文件只覆盖 **T10 接线**：
``Item.image_spec``、``RetraceImageCommand``、多模式产线分派、多折线保全、
就地重追（保 pos/scale/angle）与可撤销。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _synthetic_png(tmp_path, *, circles=((80, 150, 30), (170, 120, 34)),
                   size=(240, 288)):
    """合成样张：白底 + 若干深色实心圆（高对比边缘）。返回路径 str。"""
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


# --- Item.image_spec（D6：仿 text_spec 先例） --------------------------------

def test_item_image_spec_default_and_recorded():
    """``image_spec`` 默认 None；记录后可回读全部产线参数。"""
    from megapro.gui.layout.model import Item

    it = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]])
    assert it.image_spec is None
    spec = {"source": "a.png", "mode": "canny", "threshold": 160,
            "multi": False, "target_mm": 100.0, "low": 50, "high": 120}
    it.image_spec = spec
    assert it.image_spec["mode"] == "canny"
    assert it.image_spec["low"] == 50


def test_image_spec_defaults_survive_deepcopy():
    """与 text_spec 同：可 deepcopy（剪贴板/undo 侧依赖）。"""
    import copy

    from megapro.gui.layout.model import Item

    it = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]],
              image_spec={"source": "a.png", "mode": "canny"})
    cp = copy.deepcopy(it)
    assert cp.image_spec == it.image_spec
    cp.image_spec["mode"] = "center"
    assert it.image_spec["mode"] == "canny"   # 深拷贝，不共享


# --- 多折线保全（FR-10 坑 1） -----------------------------------------------

def test_add_image_keeps_all_polylines(tmp_path):
    """**全部折线**进模型（旧实现 ``[0]`` 只取第一条、静默丢弃其余）。"""
    import megapro.gui.layout.layout_page as L
    from megapro.gui.layout.model import Item

    lp = L.LayoutPage()
    paths = L._svg_to_paths(lp._trace_image(
        _synthetic_png(tmp_path), mode="canny", threshold=160,
        use_multi=False, target_mm=100.0, low=50, high=120))
    assert len(paths) >= 2, "Canny 对两个圆应产出多条折线"
    it = L._import_group([paths], name="图")[0]
    assert len(it.paths) == len(paths)      # 一条不丢
    assert len(it.transformed_paths()) == len(paths)
    lp.deleteLater()


def test_import_group_with_multi_paths_keeps_them():
    """``_import_group`` 长度为 1 ⇒ 一个 Item 持多条折线（不拆成多个 Item）。"""
    import megapro.gui.layout.layout_page as L

    paths = [[(0.0, 0.0), (10.0, 0.0)], [(0.0, 5.0), (10.0, 5.0)],
             [(0.0, 9.0), (10.0, 9.0)]]
    items = L._import_group([paths], name="multi")
    assert len(items) == 1
    assert len(items[0].paths) == 3


# --- 产线分派（多模式） ------------------------------------------------------

def test_image_modes_include_canny():
    """加图对话框含「照片/素描（Canny+骨架）」第三项（FR-10 接线）。"""
    import megapro.gui.layout.layout_page as L

    # _IMAGE_MODES 是 (显示名, 值) 对，值才是 mode
    values = [v for _label, v in L.LayoutPage._IMAGE_MODES]
    assert "canny" in values
    assert "center" in values and "outline" in values
    labels = [label for label, _v in L.LayoutPage._IMAGE_MODES]
    assert any("Canny" in lb for lb in labels)


def test_trace_image_dispatches_per_mode(tmp_path):
    """产线分派：Canny 模式真的走 Canny 出口（并产出可解析的折线）。"""
    import megapro.gui.layout.layout_page as L

    lp = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    svg = lp._trace_image(src, mode="canny", threshold=160, use_multi=False,
                          target_mm=100.0, low=50, high=120)
    paths = L._svg_to_paths(svg)
    assert len(paths) >= 2
    lp.deleteLater()


def test_image_spec_records_canny_thresholds(tmp_path):
    """Canny 模式把 low/high 记进 spec（重追要能取回）。"""
    import megapro.gui.layout.layout_page as L

    lp = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    spec = lp._image_spec(src, mode="canny", threshold=160, use_multi=False,
                          target_mm=100.0, low=60, high=140)
    assert spec["mode"] == "canny"
    assert spec["low"] == 60 and spec["high"] == 140
    assert spec["source"] == src
    lp.deleteLater()


# --- 就地重追（FR-10 验收①②③） --------------------------------------------

def test_retrace_changes_geometry_and_is_undoable(tmp_path):
    """重追换几何 + 可撤销；**pos/scale/angle 保持不变**（验收③）。"""
    import megapro.gui.layout.layout_page as L
    from megapro.gui.canvas.undo_cmds import RetraceImageCommand

    lp = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    paths1 = L._svg_to_paths(lp._trace_image(
        src, mode="canny", threshold=160, use_multi=False, target_mm=100.0,
        low=50, high=120))
    it = L._import_group([paths1], name="图")[0]
    it.pos = (30.0, 40.0)
    it.scale = 1.5
    it.angle_deg = 10.0
    it.image_spec = {"source": src, "mode": "canny", "low": 50, "high": 120,
                     "target_mm": 100.0, "threshold": 160, "multi": False}
    lp._add_items([it])
    before_paths = [list(p) for p in it.paths]
    before_transform = (it.pos, it.scale, it.angle_deg)

    # 换更高阈值 ⇒ 不同几何
    paths2 = L._svg_to_paths(lp._trace_image(
        src, mode="canny", threshold=160, use_multi=False, target_mm=100.0,
        low=10, high=20))
    n0 = lp._undo.count()
    lp._undo.push(RetraceImageCommand(
        lp, it, paths2, {"source": src, "mode": "canny", "low": 10,
                         "high": 20, "target_mm": 100.0, "threshold": 160,
                         "multi": False}))
    assert lp._undo.count() == n0 + 1
    assert it.paths != before_paths
    # 位置/缩放/角度不变
    assert (it.pos, it.scale, it.angle_deg) == before_transform
    # 撤销恢复
    lp._undo.undo()
    assert [list(p) for p in it.paths] == before_paths
    assert (it.pos, it.scale, it.angle_deg) == before_transform
    lp.deleteLater()


def test_retrace_rebuilds_canvas_path(tmp_path):
    """重追走显式 ``rebuild_path``（坑 2）：画布路径随重追改变，非陈旧。"""
    import megapro.gui.layout.layout_page as L
    from megapro.gui.canvas.undo_cmds import RetraceImageCommand

    lp = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    it = L._import_group(
        [L._svg_to_paths(lp._trace_image(src, mode="canny", threshold=160,
                                         use_multi=False, target_mm=100.0,
                                         low=50, high=120))], name="图")[0]
    lp._add_items([it])
    gi = lp._gi_for(it)
    before_h = gi.path().boundingRect().height()

    paths2 = L._svg_to_paths(lp._trace_image(
        src, mode="canny", threshold=160, use_multi=False, target_mm=40.0,
        low=50, high=120))
    lp._undo.push(RetraceImageCommand(lp, it, paths2, {"source": src,
                                                       "mode": "canny"}))
    after_h = gi.path().boundingRect().height()
    assert after_h != before_h   # 画布真的重画了（target_mm 改了尺寸）
    lp.deleteLater()


def test_retrace_preserves_pos_scale_angle_exactly(tmp_path):
    """重追严格保 pos/scale/angle（FR-10 验收③的显式断言）。"""
    import megapro.gui.layout.layout_page as L
    from megapro.gui.canvas.undo_cmds import RetraceImageCommand

    lp = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    it = L._import_group([L._svg_to_paths(lp._trace_image(
        src, mode="canny", threshold=160, use_multi=False, target_mm=100.0,
        low=50, high=120))], name="图")[0]
    it.pos, it.scale, it.angle_deg = (12.5, 33.5), 2.0, 42.0
    lp._add_items([it])
    keep = (it.pos, it.scale, it.angle_deg)
    lp._undo.push(RetraceImageCommand(
        lp, it, [[(0.0, 0.0), (5.0, 0.0)]], {"source": src, "mode": "canny"}))
    assert (it.pos, it.scale, it.angle_deg) == keep
    lp.deleteLater()


def test_retrace_requires_existing_source(tmp_path, monkeypatch):
    """源图缺失/未记 spec ⇒ **拒绝且不 push 命令**（不静默产出空几何）。

    该路径会弹提示框，故把 ``QMessageBox.warning`` 打成 no-op —— 断言的是
    「没产生撤销命令」这一可观察后果，不是对话框本身。
    """
    import megapro.gui.layout.layout_page as L
    from PySide6 import QtWidgets

    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    lp = L.LayoutPage()
    it = L._import_group([[[(0.0, 0.0), (5.0, 0.0)]]], name="图")[0]
    lp._add_items([it])
    n0 = lp._undo.count()
    lp.retrace_image_item(it)  # 无 image_spec ⇒ 拒绝
    assert lp._undo.count() == n0
    assert it.paths == [[(0.0, 0.0), (5.0, 0.0)]]   # 几何未被清空

    # 记了 spec 但源文件不存在 ⇒ 同样拒绝
    it.image_spec = {"source": str(tmp_path / "nope.png"), "mode": "canny"}
    lp.retrace_image_item(it)
    assert lp._undo.count() == n0
    lp.deleteLater()


def test_double_click_on_image_dispatches_to_retrace(tmp_path, monkeypatch):
    """**真实**双击 → ``PathItem.mouseDoubleClickEvent`` 真的分派到重追。

    上一版只断言 ``callable(page.retrace_image_item)``，删掉 ``items.py``
    里的 image_spec 分支也照样绿 —— 那不算接线覆盖。本用例直接调
    ``mouseDoubleClickEvent`` 并用替身记录分派目标。
    """
    import megapro.gui.layout.layout_page as L
    from megapro.gui.canvas.items import PathItem
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import Qt

    lp = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    it = L._import_group([L._svg_to_paths(lp._trace_image(
        src, mode="canny", threshold=160, use_multi=False, target_mm=100.0,
        low=50, high=120))], name="图")[0]
    it.image_spec = {"source": src, "mode": "canny", "low": 50, "high": 120,
                     "target_mm": 100.0, "threshold": 160, "multi": False}
    lp._add_items([it])
    gi: PathItem = lp._gi_for(it)

    called: list = []
    monkeypatch.setattr(lp, "retrace_image_item", lambda item: called.append(item))
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonDblClick, QPointF(0, 0),
                     Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    gi.mouseDoubleClickEvent(ev)
    assert called == [it], "双击未分派到 retrace_image_item"
    lp.deleteLater()


def test_double_click_text_item_still_goes_to_text_edit(tmp_path, monkeypatch):
    """分派顺序：文字图元双击仍走**文字重编**（不被图片分支抢走）。"""
    import megapro.gui.layout.layout_page as L
    from megapro.gui.canvas.items import PathItem
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    lp = L.LayoutPage()
    it = L._import_group([[[(0.0, 0.0), (5.0, 0.0)]]], name="文字")[0]
    it.text_spec = {"text": "hi", "mode": "outline"}
    it.image_spec = {"source": "x.png", "mode": "canny"}  # 两者都有
    lp._add_items([it])
    gi: PathItem = lp._gi_for(it)

    text_calls: list = []
    img_calls: list = []
    monkeypatch.setattr(lp, "edit_text_item", lambda item: text_calls.append(item))
    monkeypatch.setattr(lp, "retrace_image_item", lambda item: img_calls.append(item))
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonDblClick, QPointF(0, 0),
                     Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    gi.mouseDoubleClickEvent(ev)
    assert text_calls == [it] and img_calls == []
    lp.deleteLater()


def test_clipboard_roundtrip_preserves_image_spec(tmp_path):
    """复制/粘贴/副本**保留** ``image_spec``（否则副本双击不能重追）。

    ``_item_to_json`` 是显式字段白名单：漏了 ``image_spec`` 时，复制出来的
    图片图元 ``image_spec=None`` ⇒ :meth:`retrace_image_item` 直接拒绝，
    用户看不出原因。``text_spec`` 同在白名单里，本用例一并钉住两者。
    """
    import megapro.gui.layout.layout_page as L
    from megapro.gui.layout.model import Item

    it = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="图",
               text_spec={"a": 1},
               image_spec={"source": "x.png", "mode": "canny", "low": 50,
                           "high": 120, "target_mm": 100.0})
    d = L._item_to_json(it)
    assert "image_spec" in d
    back = L._item_from_json(d, dz=0.0, z=1.0)
    assert back.image_spec == it.image_spec
    assert back.text_spec == it.text_spec
    # 深拷贝，不共享
    back.image_spec["mode"] = "center"
    assert it.image_spec["mode"] == "canny"


def test_clipboard_accepts_legacy_payload_without_image_spec():
    """旧剪贴板内容（无 ``image_spec`` 键）仍可粘贴，不炸。"""
    import megapro.gui.layout.layout_page as L

    legacy = {"paths": [[(0.0, 0.0), (1.0, 0.0)]], "pos": (0.0, 0.0),
              "scale": 1.0, "angle_deg": 0.0, "name": "x", "z": 1,
              "text_spec": None}
    it = L._item_from_json(legacy, dz=0.0, z=1.0)
    assert it.image_spec is None
    assert it.name == "x" and len(it.paths) == 1


def test_cloned_image_item_still_retraceable(tmp_path):
    """「复制副本」出的图片图元仍带 spec（副本链走 _clone_item → _item_from_json）。"""
    import megapro.gui.layout.layout_page as L
    from megapro.gui.layout.model import Item

    src = _synthetic_png(tmp_path)
    it = Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="图",
               image_spec={"source": src, "mode": "canny"})
    clone = L._clone_item(it, dz=5.0, z=1.0)
    assert clone.image_spec == it.image_spec
    assert clone.image_spec["source"] == src
    assert clone.image_spec and clone.pos == (5.0, 5.0)


def test_retrace_dialog_prefills_from_spec(tmp_path, monkeypatch):
    """重追对话框**预填** ``image_spec``（只改要改的那项，其余不丢）。

    US-5「双击图片调阈值重追」的前提：不预填的话用户每次都得把模式/最长边/
    Canny 阈值/多阈值重敲一遍。``QDialog.exec`` 被替换为「自动接受并在此刻
    读控件初值」，故断言语义是**控件初值取自 spec**（而不是流程跑通）。

    回归价值：把 ``_ask_image_params`` 的 ``spec = dict(spec or {})`` 与各处
    ``spec.get(...)`` 改回占位 ``spec = {}``，本用例**必红**（其余用例照绿
    —— 它们只看模型字段，不看控件初值）。
    """
    import megapro.gui.layout.layout_page as L
    from PySide6 import QtWidgets

    seen: dict = {}

    def fake_exec(self):
        for child in self.findChildren(QtWidgets.QComboBox):
            seen["mode"] = child.currentData()
        seen["spins"] = [c.value()
                         for c in self.findChildren(QtWidgets.QSpinBox)]
        for child in self.findChildren(QtWidgets.QCheckBox):
            seen["multi"] = child.isChecked()
        for child in self.findChildren(QtWidgets.QDoubleSpinBox):
            seen["mm"] = child.value()
        for child in self.findChildren(QtWidgets.QSlider):
            seen["threshold"] = child.value()
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(QtWidgets.QDialog, "exec", fake_exec)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    lp = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    it = L._import_group([L._svg_to_paths(lp._trace_image(
        src, mode="canny", threshold=160, use_multi=False, target_mm=100.0,
        low=50, high=120))], name="图")[0]
    it.image_spec = {"source": src, "mode": "canny", "threshold": 77,
                     "multi": True, "target_mm": 88.0, "low": 33, "high": 99}
    lp._add_items([it])
    lp.retrace_image_item(it)
    assert seen["mode"] == "canny"                    # 模式
    assert 33 in seen["spins"] and 99 in seen["spins"]   # Canny low/high
    assert seen["multi"] is True                      # 多阈值
    assert seen["mm"] == pytest.approx(88.0, abs=1e-6)   # 最长边
    assert seen["threshold"] == 77                    # 暗度阈值
    lp.deleteLater()


def test_add_image_records_spec_and_keeps_all_paths(tmp_path, monkeypatch):
    """``_add_image`` 入库即记 spec，且**全部折线**都在（FR-10 坑 1 的回归）。

    文件对话框与参数对话框都被替换：只验「点确定」之后模型里剩下什么 ——
    ``image_spec`` 是否记全、Canny 产出的多条折线是否一条不丢。
    """
    import megapro.gui.layout.layout_page as L
    from PySide6 import QtWidgets

    src = _synthetic_png(tmp_path)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (src, "")))
    # 参数对话框：canny / 阈值 160 / 不多阈值 / 100mm / low 50 / high 120
    monkeypatch.setattr(
        L.LayoutPage, "_ask_image_params",
        lambda self, **kw: ("canny", 160, False, 100.0, 50, 120))
    lp = L.LayoutPage()
    lp._add_image()

    assert len(lp.doc.items) == 1
    it = lp.doc.items[0]
    # 全部 Canny 折线都在一个 Item 里
    assert len(it.paths) >= 2, "Canny 对多目标应产出多条折线，不能只留一条"
    assert len(it.transformed_paths()) == len(it.paths)
    # spec 记全：模式 + 阈值 + Canny low/high + target + 源路径
    assert it.image_spec["mode"] == "canny"
    assert it.image_spec["source"] == src
    assert it.image_spec["threshold"] == 160
    assert it.image_spec["low"] == 50 and it.image_spec["high"] == 120
    assert it.image_spec["target_mm"] == pytest.approx(100.0, abs=1e-6)
    lp.deleteLater()
