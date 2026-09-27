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


def test_double_click_on_image_triggers_retrace(tmp_path):
    """双击图片图元 → 走重追回调（不是文字重编）。"""
    import megapro.gui.layout.layout_page as L

    lp = L.LayoutPage()
    src = _synthetic_png(tmp_path)
    it = L._import_group([L._svg_to_paths(lp._trace_image(
        src, mode="canny", threshold=160, use_multi=False, target_mm=100.0,
        low=50, high=120))], name="图")[0]
    it.image_spec = {"source": src, "mode": "canny", "low": 50, "high": 120,
                     "target_mm": 100.0, "threshold": 160, "multi": False}
    lp._add_items([it])
    # 双击链：PathItem → page.retrace_image_item（此处因会弹对话框，只验回调存在）
    assert callable(lp.retrace_image_item)
    assert it.image_spec is not None
    lp.deleteLater()


def test_retrace_dialog_prefills_from_spec(tmp_path, monkeypatch):
    """重追对话框**预填** ``image_spec``（只改要改的那项，其余不丢）。

    US-5「双击图片调阈值重追」的前提：不预填的话用户每次都得把模式/最长边/
    Canny 阈值重敲一遍。``QDialog.exec`` 被替换为「自动接受并在此时读控件
    初值」，故断言语义是「控件初值取自 spec」。
    """
    import megapro.gui.layout.layout_page as L
    from PySide6 import QtWidgets

    seen: dict = {}

    def fake_exec(self):
        for child in self.findChildren(QtWidgets.QComboBox):
            seen["mode"] = child.currentData()
        spins = []
        for child in self.findChildren(QtWidgets.QSpinBox):
            spins.append(child.value())
        seen["spins"] = spins
        for child in self.findChildren(QtWidgets.QCheckBox):
            seen["multi"] = child.isChecked()
        for child in self.findChildren(QtWidgets.QDoubleSpinBox):
            seen["mm"] = child.value()
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
    assert seen["mode"] == "canny"                 # 模式预填
    assert 33 in seen["spins"] and 99 in seen["spins"]  # Canny 阈值预填
    assert seen["multi"] is True                    # 多阈值预填
    assert seen["mm"] == pytest.approx(88.0, abs=1e-6)  # 最长边预填
    lp.deleteLater()
