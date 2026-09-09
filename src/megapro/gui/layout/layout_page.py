"""排版/制作页（Qt）—— CAD/PS 式编辑 + 工具 + 属性 + 导入/导出。

能力：
- 添加：文字（字体可选、可双击重编）、图片（中心线/轮廓）、SVG、Word/Excel、绘制图元
- 编辑：框选/Ctrl多选/Ctrl+A、删除、撤销/重做、复制粘贴、箭头微调、
        层序（置顶/置底/上移/下移）、对齐/分布、W/H 数值
- 画布：mm 网格、原点左下、缩放控件、标尺、网格吸附
- 导出：另存为 SVG / 送去执行（按 z 排序、跳过隐藏）

模型 Item 存 paper 坐标（y-up，原点左下 0..210）；显示 space y-down，
映射 disp_y = BED_H − paper_y。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from megapro.gui.layout.canvas import CanvasView, GridScene
from megapro.gui.layout.export_svg import document_to_svg
from megapro.gui.layout.items import PolylineItem
from megapro.gui.layout.model import BED_H, BED_W, Document, Item
from megapro.gui.layout.undo import (
    AddItemsCommand, ChangeItemPropsCommand, ClearCommand, EditTextCommand,
    MoveItemsCommand, RemoveItemsCommand,
)

from megapro.gui.text_to_svg import (
    find_cjk_font, list_fonts, text_outline_svg, text_singleline_svg,
)

_DATA_DIR = Path(__file__).resolve().parents[4] / "data"

#: 绘制工具
TOOL_SELECT = "select"
TOOL_LINE = "line"
TOOL_RECT = "rect"
TOOL_CIRCLE = "circle"
TOOL_POLY = "poly"
TOOL_PENCIL = "pencil"


def _svg_to_paths(svg: str) -> list:
    """把 SVG 字符串解析为 polylines（经临时文件走 toolchain parse_svg）。"""
    from megapro.toolchain.svg_to_gcode import parse_svg

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False,
                                     mode="w", encoding="utf-8") as fh:
        fh.write(svg)
        p = fh.name
    try:
        return parse_svg(p)
    finally:
        Path(p).unlink(missing_ok=True)


class LayoutPage(QtWidgets.QWidget):
    """排版页：信号 export_requested(svg_str) 供 MainWindow 送去作业。"""

    export_requested = QtCore.Signal(str)
    status_message = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.doc = Document()
        self._scene_items: list[PolylineItem] = []
        self._applying = False
        self._tool = TOOL_SELECT
        self._preview_item = None
        self._draw_start = None
        self._poly_pts: list[tuple[float, float]] = []
        self.snap_pitch = 1.0
        self._bed_h = BED_H
        self._undo = QtGui.QUndoStack(self)
        self._build_ui()
        self._refresh_props()
        self._refresh_undo_actions()

    # -- 供 undo 命令调用 --------------------------------------------------

    def _gi_for(self, item: Item) -> PolylineItem | None:
        for gi in self._scene_items:
            if gi.model_item is item:
                return gi
        return None

    def _remove_item_obj(self, item: Item) -> None:
        gi = self._gi_for(item)
        if gi is not None:
            self.scene.removeItem(gi)
            if gi in self._scene_items:
                self._scene_items.remove(gi)
        self.doc.remove(item)

    def _sync_gi(self, item: Item) -> None:
        gi = self._gi_for(item)
        if gi is None:
            return
        gi.setPos(item.pos[0], self._bed_h - item.pos[1])
        gi.setScale(item.scale)
        gi.setRotation(item.angle_deg)
        gi.setZValue(item.z)
        gi.setVisible(item.visible)
        gi.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, not item.locked)
        gi.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, not item.locked)
        gi.update()

    def _after_change(self) -> None:
        """任何编辑后：刷新属性/场景。"""
        self._refresh_props()

    def _refresh_undo_actions(self) -> None:
        if hasattr(self, "_undo_act"):
            self._undo_act.setText(f"撤销 {self._undo.undoText()}".strip())
            self._redo_act.setText(f"重做 {self._undo.redoText()}".strip())

    # -- UI ----------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(self._build_toolbar())
        self.scene = GridScene(self)
        self.view = CanvasView(self.scene, self)
        self.view.set_tool(TOOL_SELECT)
        self.view.fit_bed()
        self.scene.selectionChanged.connect(self._on_selection_changed)
        # 画布 + 标尺（上/左）
        from megapro.gui.layout.canvas import Ruler
        canvas_box = QtWidgets.QGridLayout()
        self._ruler_h = Ruler(self.view, "h")
        self._ruler_v = Ruler(self.view, "v")
        canvas_box.addWidget(self._ruler_h, 0, 1)
        canvas_box.addWidget(self._ruler_v, 1, 0)
        canvas_box.addWidget(self.view, 1, 1)
        canvas_box.setRowStretch(1, 1)
        canvas_box.setColumnStretch(1, 1)
        root.addLayout(canvas_box, 1)
        root.addWidget(self._build_props())

    def _build_toolbar(self) -> QtWidgets.QWidget:
        """工具条：QToolBar（自带溢出「»」），避免按钮过多撑宽窗口。"""
        tb = QtWidgets.QToolBar()
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(16, 16))

        def add_btn(text, fn, checkable=False):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.setCheckable(checkable)
            b.setAutoRaise(True)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            b.clicked.connect(fn)
            tb.addWidget(b)
            return b

        # 工具模式
        self.tool_group = QtWidgets.QButtonGroup(self)
        self.tool_group.setExclusive(True)
        for label, tool in (("选择", TOOL_SELECT), ("直线", TOOL_LINE),
                            ("矩形", TOOL_RECT), ("圆", TOOL_CIRCLE),
                            ("折线", TOOL_POLY), ("自由笔", TOOL_PENCIL)):
            b = add_btn(label, lambda _c=False, t=tool: self._set_tool(t),
                        checkable=True)
            b.setChecked(tool == TOOL_SELECT)
            self.tool_group.addButton(b)
        tb.addSeparator()
        # 添加
        for label, fn in (("加文字…", self._add_text), ("加图片…", self._add_image),
                          ("导入SVG…", self._add_svg),
                          ("导入Word/Excel…", self._add_doc)):
            add_btn(label, fn)
        tb.addSeparator()
        # 撤销/重做
        self._undo_act = self._undo.createUndoAction(self, "撤销")
        self._undo_act.setShortcut(QtGui.QKeySequence.Undo)
        self._redo_act = self._undo.createRedoAction(self, "重做")
        self._redo_act.setShortcut(QtGui.QKeySequence.Redo)
        self.addAction(self._undo_act)
        self.addAction(self._redo_act)
        for a in (self._undo_act, self._redo_act):
            b = QtWidgets.QToolButton()
            b.setDefaultAction(a)
            b.setAutoRaise(True)
            tb.addWidget(b)
        tb.addSeparator()
        # 层序/对齐/删除
        for label, fn in (("置顶", lambda: self._zorder("top")),
                          ("置底", lambda: self._zorder("bottom")),
                          ("上移", lambda: self._zorder("up")),
                          ("下移", lambda: self._zorder("down")),
                          ("左对齐", lambda: self._align("left")),
                          ("水平居中", lambda: self._align("hcenter")),
                          ("垂直分布", lambda: self._distribute("v")),
                          ("删除", self._delete_selected)):
            add_btn(label, fn)
        tb.addSeparator()
        # 缩放
        for label, fn in (("－", self._zoom_out), ("＋", self._zoom_in),
                          ("适合窗口", self._fit), ("100%", self._zoom_100)):
            add_btn(label, fn)
        return tb

    def _build_props(self) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        props = QtWidgets.QHBoxLayout(box)
        props.setContentsMargins(0, 0, 0, 0)
        for name in ("X", "Y", "宽", "高", "角度°"):
            props.addWidget(QtWidgets.QLabel(name + ":"))
            sp = QtWidgets.QDoubleSpinBox()
            sp.setRange(-1000, 1000)
            sp.setDecimals(2)
            sp.setFixedWidth(72)  # 紧凑，避免撑宽
            sp.valueChanged.connect(self._apply_props)
            setattr(self, {"X": "sp_x", "Y": "sp_y", "宽": "sp_w", "高": "sp_h",
                           "角度°": "sp_ang"}[name], sp)
            props.addWidget(sp)
        props.addWidget(QtWidgets.QLabel("缩放:"))
        self.sp_scale = QtWidgets.QDoubleSpinBox()
        self.sp_scale.setRange(0.05, 20.0)
        self.sp_scale.setValue(1.0)
        self.sp_scale.setSingleStep(0.1)
        self.sp_scale.setFixedWidth(72)
        self.sp_scale.valueChanged.connect(self._apply_props)
        props.addWidget(self.sp_scale)
        props.addStretch(1)
        self.btn_save = QtWidgets.QPushButton("另存为 SVG…")
        self.btn_save.clicked.connect(self._on_save_svg)
        props.addWidget(self.btn_save)
        self.btn_export = QtWidgets.QPushButton("导出并送去执行")
        self.btn_export.clicked.connect(self._on_export)
        props.addWidget(self.btn_export)
        return box

    # -- 工具模式 ----------------------------------------------------------

    def _set_tool(self, tool: str) -> None:
        self.cancel_draw()
        self._tool = tool
        self.view.set_tool(tool)

    # -- 选中/属性 ---------------------------------------------------------

    def _selected(self) -> list[PolylineItem]:
        return [gi for gi in self.scene.selectedItems()
                if isinstance(gi, PolylineItem)]

    def _on_selection_changed(self) -> None:
        self._refresh_props()

    def _refresh_props(self) -> None:
        sel = self._selected()
        spins = (self.sp_x, self.sp_y, self.sp_w, self.sp_h, self.sp_ang,
                 self.sp_scale)
        if not sel:
            for s in spins:
                s.blockSignals(True)
                s.setValue(0 if s is not self.sp_scale else 1.0)
                s.blockSignals(False)
                s.setEnabled(False)
            return
        for s in spins:
            s.setEnabled(True)
            s.blockSignals(True)
        gi = sel[0]
        it = gi.model_item
        self.sp_x.setValue(it.pos[0])
        self.sp_y.setValue(it.pos[1])
        x0, y0, x1, y1 = it.page_bbox()
        self.sp_w.setValue(abs(x1 - x0))
        self.sp_h.setValue(abs(y1 - y0))
        self.sp_ang.setValue(it.angle_deg)
        self.sp_scale.setValue(it.scale)
        for s in spins:
            s.blockSignals(False)

    def _apply_props(self) -> None:
        sel = self._selected()
        if not sel:
            return
        self._applying = True
        try:
            changes = []
            for gi in sel:
                it = gi.model_item
                old = {"pos": it.pos, "scale": it.scale, "angle_deg": it.angle_deg}
                new = dict(old)
                if sel:
                    new["pos"] = (self.sp_x.value(), self.sp_y.value())
                    new["angle_deg"] = self.sp_ang.value()
                    # 宽/高 → 按比例改 scale
                    x0, y0, x1, y1 = it.page_bbox()
                    w = abs(x1 - x0) or 1e-6
                    if self.sp_w.value() > 0:
                        new["scale"] = it.scale * (self.sp_w.value() / w)
                changes.append((it, old, new))
            if changes:
                self._undo.push(ChangeItemPropsCommand(self, changes))
        finally:
            self._applying = False
        self._sync_models()

    def _sync_models(self) -> None:
        for gi in self._scene_items:
            gi.sync_to_model()

    # -- 键盘：删除/复制/箭头 ----------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self.view.hasFocus() or self.hasFocus():
            k = event.key()
            mods = event.modifiers()
            if k in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
                self._delete_selected()
                return
            if k == QtCore.Qt.Key_A and mods & QtCore.Qt.ControlModifier:
                for gi in self._scene_items:
                    if not gi.model_item.locked:
                        gi.setSelected(True)
                return
            if k == QtCore.Qt.Key_C and mods & QtCore.Qt.ControlModifier:
                self._copy_selected()
                return
            if k == QtCore.Qt.Key_V and mods & QtCore.Qt.ControlModifier:
                self._paste()
                return
            if k == QtCore.Qt.Key_D and mods & QtCore.Qt.ControlModifier:
                self._duplicate()
                return
            if k in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right,
                     QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
                self._nudge(k, bool(mods & QtCore.Qt.ShiftModifier))
                return
        super().keyPressEvent(event)

    def _nudge(self, key, big: bool) -> None:
        step = self.snap_pitch * (10 if big else 1)
        dx = -step if key == QtCore.Qt.Key_Left else step if key == QtCore.Qt.Key_Right else 0
        dy = -step if key == QtCore.Qt.Key_Down else step if key == QtCore.Qt.Key_Up else 0
        moves = []
        for gi in self._selected():
            it = gi.model_item
            old = it.pos
            moves.append((it, old, (old[0] + dx, old[1] + dy)))
        if moves:
            self._undo.push(MoveItemsCommand(self, moves, "微调"))

    def _delete_selected(self) -> None:
        items = [gi.model_item for gi in self._selected()]
        if items:
            self._undo.push(RemoveItemsCommand(self, items))

    def _copy_selected(self) -> None:
        data = []
        for gi in self._selected():
            it = gi.model_item
            data.append({"paths": it.paths, "pos": it.pos, "scale": it.scale,
                         "angle_deg": it.angle_deg, "name": it.name, "z": it.z,
                         "text_spec": it.text_spec})
        QtWidgets.QApplication.clipboard().setText(json.dumps(data))

    def _paste(self) -> None:
        try:
            data = json.loads(QtWidgets.QApplication.clipboard().text())
        except Exception:
            return
        items = []
        for d in data:
            it = Item(paths=[list(map(tuple, p)) for p in d["paths"]],
                      pos=(d["pos"][0] + 5, d["pos"][1] + 5),
                      scale=d.get("scale", 1.0), angle_deg=d.get("angle_deg", 0.0),
                      name=d.get("name", "item"), z=self.doc.top_z() + 1,
                      text_spec=d.get("text_spec"))
            items.append(it)
        if items:
            self._undo.push(AddItemsCommand(self, items, "粘贴"))

    def _duplicate(self) -> None:
        items = []
        for gi in self._selected():
            it = gi.model_item
            items.append(Item(paths=copy.deepcopy(it.paths),
                              pos=(it.pos[0] + 5, it.pos[1] + 5),
                              scale=it.scale, angle_deg=it.angle_deg,
                              name=it.name, z=self.doc.top_z() + 1,
                              text_spec=copy.deepcopy(it.text_spec)))
        if items:
            self._undo.push(AddItemsCommand(self, items, "复制副本"))

    # -- 层序 / 对齐 / 分布 -------------------------------------------------

    def _zorder(self, mode: str) -> None:
        sel = self._selected()
        if not sel:
            return
        changes = []
        for gi in sel:
            it = gi.model_item
            old = {"z": it.z}
            if mode == "top":
                new_z = self.doc.top_z() + 1
            elif mode == "bottom":
                new_z = self.doc.bottom_z() - 1
            elif mode == "up":
                new_z = it.z + 1
            else:
                new_z = it.z - 1
            changes.append((it, old, {"z": new_z}))
        self._undo.push(ChangeItemPropsCommand(self, changes, "层序"))

    def _align(self, mode: str) -> None:
        sel = self._selected()
        if len(sel) < 2:
            return
        boxes = [(gi.model_item, gi.model_item.page_bbox()) for gi in sel]
        xs0 = [b[0] for _, b in boxes]
        xs1 = [b[2] for _, b in boxes]
        ys0 = [b[1] for _, b in boxes]
        ys1 = [b[3] for _, b in boxes]
        changes = []
        for it, (x0, y0, x1, y1) in boxes:
            old = {"pos": it.pos}
            px, py = it.pos
            if mode == "left":
                px += min(xs0) - x0
            elif mode == "right":
                px += max(xs1) - x1
            elif mode == "hcenter":
                px += (min(xs0) + max(xs1)) / 2 - (x0 + x1) / 2
            elif mode == "top":
                py += max(ys1) - y1
            elif mode == "bottom":
                py += min(ys0) - y0
            changes.append((it, old, {"pos": (px, py)}))
        self._undo.push(ChangeItemPropsCommand(self, changes, "对齐"))

    def _distribute(self, axis: str) -> None:
        sel = self._selected()
        if len(sel) < 3:
            return
        boxes = [(gi.model_item, gi.model_item.page_bbox()) for gi in sel]
        if axis == "v":
            boxes.sort(key=lambda t: t[1][1])
            lo = boxes[0][1][1]
            hi = boxes[-1][1][3]
            gaps = (hi - lo) / (len(boxes) - 1)
            changes = []
            for i, (it, (x0, y0, x1, y1)) in enumerate(boxes):
                old = {"pos": it.pos}
                target = lo + i * gaps
                changes.append((it, old, {"pos": (it.pos[0], it.pos[1] + target - y0)}))
            self._undo.push(ChangeItemPropsCommand(self, changes, "分布"))
        else:
            boxes.sort(key=lambda t: t[1][0])
            lo = boxes[0][1][0]
            hi = boxes[-1][1][2]
            gaps = (hi - lo) / (len(boxes) - 1)
            changes = []
            for i, (it, (x0, y0, x1, y1)) in enumerate(boxes):
                old = {"pos": it.pos}
                target = lo + i * gaps
                changes.append((it, old, {"pos": (it.pos[0] + target - x0, it.pos[1])}))
            self._undo.push(ChangeItemPropsCommand(self, changes, "分布"))

    # -- 添加 --------------------------------------------------------------

    def _add_items(self, items: list[Item], text: str = "添加") -> None:
        if items:
            self._undo.push(AddItemsCommand(self, items, text))

    def _add_text(self, edit_item: Item | None = None) -> None:
        dlg = _TextDialog(self, edit_item)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            paths = dlg.generate_paths()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "文字生成失败", str(exc))
            return
        if not paths:
            QtWidgets.QMessageBox.warning(self, "无路径", "文字未能生成可画路径")
            return
        paths = self._to_paper(paths)
        if edit_item is not None:
            self._undo.push(EditTextCommand(self, edit_item, paths, dlg.spec()))
        else:
            it = Item(paths=paths, pos=(0.0, 0.0), name=f"文字:{dlg.text()[:8]}",
                      z=self.doc.top_z() + 1, text_spec=dlg.spec())
            self._add_items([it], "加文字")

    def _add_image(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*)")
        if not path:
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("图片→线条参数")
        form = QtWidgets.QFormLayout(dlg)
        mode = QtWidgets.QComboBox()
        mode.addItem("中心线（骨架，消双线）", "center")
        mode.addItem("区域轮廓（potrace，双线）", "outline")
        form.addRow("模式:", mode)
        multi = QtWidgets.QCheckBox("多阈值合并（一次提全淡→浓所有线条）")
        form.addRow(multi)
        th = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        th.setRange(0, 255)
        th.setValue(160)
        form.addRow("阈值(暗→线，未勾多阈值时用):", th)
        mm = QtWidgets.QDoubleSpinBox()
        mm.setRange(10, 210)
        mm.setValue(100.0)
        form.addRow("最长边(mm):", mm)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok
                                        | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        md = mode.currentData()
        threshold = th.value()
        use_multi = multi.isChecked()
        try:
            if md == "center":
                if use_multi:
                    from megapro.gui.edge_to_svg import image_to_centerline_svg_multi
                    svg = image_to_centerline_svg_multi(path, target_mm=mm.value())
                else:
                    from megapro.gui.edge_to_svg import image_to_centerline_svg
                    svg = image_to_centerline_svg(path, target_mm=mm.value(),
                                                  threshold=threshold)
            else:
                if use_multi:
                    from megapro.gui.image_to_svg import trace_image_multi
                    svg = trace_image_multi(path, target_mm=mm.value())
                else:
                    from megapro.gui.image_to_svg import trace_image
                    svg = trace_image(path, target_mm=mm.value(),
                                      threshold=threshold)
            paths = _svg_to_paths(svg)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self, "图片转线条失败",
                f"{exc}\n（中心线需 scikit-image；potrace 需 bin/potrace.exe）")
            return
        if not paths:
            QtWidgets.QMessageBox.warning(self, "无线条", "阈值后没有可追踪的线条")
            return
        paths = self._to_paper(paths)
        it = Item(paths=paths, pos=(0.0, 0.0), name=f"图:{Path(path).name[:8]}",
                  z=self.doc.top_z() + 1)
        self._add_items([it], "加图片")

    def _add_svg(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "导入 SVG", "", "SVG (*.svg);;所有文件 (*)")
        if not path:
            return
        try:
            from megapro.toolchain.svg_to_gcode import parse_svg
            paths = parse_svg(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "导入失败", str(exc))
            return
        if not paths:
            QtWidgets.QMessageBox.warning(self, "空", "SVG 无几何")
            return
        paths = self._to_paper(paths)
        it = Item(paths=paths, pos=(0.0, 0.0),
                  name=f"SVG:{Path(path).name[:8]}", z=self.doc.top_z() + 1)
        self._add_items([it], "导入SVG")

    def _add_doc(self) -> None:
        from megapro.gui.layout.doc_import import DocImportDialog

        dlg = DocImportDialog(self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        items = dlg.items()
        if items:
            for it in items:
                it.z = self.doc.top_z() + 1
            self._add_items(items, "导入Word/Excel")

    # -- 绘制预览（供 canvas 调用） ----------------------------------------

    def _clear_preview(self) -> None:
        if self._preview_item is not None:
            try:
                self.scene.removeItem(self._preview_item)
            except RuntimeError:
                pass
            self._preview_item = None

    def begin_draw(self, scene_pos) -> None:
        self._clear_preview()
        self._draw_start = scene_pos
        self._poly_pts = [scene_pos]

    def update_draw(self, scene_pos) -> None:
        self._clear_preview()
        s = self._draw_start
        if s is None or self._tool == TOOL_SELECT:
            return
        # 预览统一：QGraphicsPathItem，无填充（只描边），避免出现"封闭面"
        path = QtGui.QPainterPath()
        if self._tool == TOOL_LINE:
            path.moveTo(s)
            path.lineTo(scene_pos)
        elif self._tool == TOOL_RECT:
            path.addRect(QtCore.QRectF(s, scene_pos).normalized())
        elif self._tool == TOOL_CIRCLE:
            path.addEllipse(QtCore.QRectF(s, scene_pos).normalized())
        elif self._tool in (TOOL_POLY, TOOL_PENCIL):
            pts = self._poly_pts + [scene_pos]
            if len(pts) >= 2:
                path.moveTo(pts[0])
                for p in pts[1:]:
                    path.lineTo(p)
        pen = QtGui.QPen(QtGui.QColor("#e60"))
        pen.setWidthF(0)
        item = QtWidgets.QGraphicsPathItem(path)
        item.setPen(pen)
        item.setBrush(QtCore.Qt.NoBrush)  # 关键：不填充
        self._preview_item = item
        self.scene.addItem(item)

    def finish_draw(self, scene_pos) -> None:
        self._clear_preview()
        s = self._draw_start
        if s is None:
            return

        def to_paper(p):
            return (p.x(), self._bed_h - p.y())

        paths = []
        if self._tool == TOOL_LINE:
            paths = [[to_paper(s), to_paper(scene_pos)]]
        elif self._tool == TOOL_RECT:
            r = QtCore.QRectF(s, scene_pos).normalized()
            c = [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft(),
                 r.topLeft()]
            paths = [[to_paper(p) for p in c]]
        elif self._tool == TOOL_CIRCLE:
            r = QtCore.QRectF(s, scene_pos).normalized()
            cx, cy = r.center().x(), r.center().y()
            rx, ry = r.width() / 2, r.height() / 2
            import math
            pts = []
            for k in range(49):
                a = 2 * math.pi * k / 48
                pts.append(to_paper(QtCore.QPointF(cx + rx * math.cos(a),
                                                   cy + ry * math.sin(a))))
            paths = [pts]
        elif self._tool in (TOOL_POLY, TOOL_PENCIL):
            pts = list(self._poly_pts)
            if pts and (pts[-1].x() != scene_pos.x() or pts[-1].y() != scene_pos.y()):
                pts.append(scene_pos)
            paths = [[to_paper(p) for p in pts]] if len(pts) >= 2 else []
        self._draw_start = None
        self._poly_pts = []
        if paths:
            it = Item(paths=paths, pos=(0.0, 0.0),
                      name={"line": "直线", "rect": "矩形", "circle": "圆",
                            "poly": "折线", "pencil": "自由笔"}.get(self._tool, "图元"),
                      z=self.doc.top_z() + 1)
            self._add_items([it], "绘制")

    def add_poly_point(self, scene_pos) -> None:
        self._poly_pts.append(scene_pos)

    def cancel_draw(self) -> None:
        """取消当前绘制（切工具/右键）。"""
        self._clear_preview()
        self._draw_start = None
        self._poly_pts = []

    def _to_paper(self, paths):
        ys = [y for p in paths for _, y in p]
        if not ys:
            return [[(float(x), float(y)) for x, y in p] for p in paths]
        ysum = max(ys) + min(ys)
        return [[(x, ysum - y) for x, y in p] for p in paths]

    def _clear(self) -> None:
        if self.doc.items:
            self._undo.push(ClearCommand(self))

    def _fit(self) -> None:
        self.view.fit_bed()

    def _zoom_in(self) -> None:
        self.view.scale(1.25, 1.25)

    def _zoom_out(self) -> None:
        self.view.scale(0.8, 0.8)

    def _zoom_100(self) -> None:
        self.view.reset_transform()

    # -- 双击重编文字 ------------------------------------------------------

    def edit_text_item(self, item: Item) -> None:
        if item.text_spec:
            self._add_text(edit_item=item)

    # -- 导出 --------------------------------------------------------------

    def _on_save_svg(self) -> None:
        self._sync_models()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "另存为 SVG", "layout.svg", "SVG (*.svg);;所有文件 (*)")
        if not path:
            return
        if not path.lower().endswith(".svg"):
            path += ".svg"
        svg = document_to_svg(self.doc)
        try:
            Path(path).write_text(svg, encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.status_message.emit(f"已另存为 {path}")

    def _on_export(self) -> None:
        self._sync_models()
        for it in self.doc.items_visible():
            x0, y0, x1, y1 = it.page_bbox()
            if x0 < 0 or y0 < 0 or x1 > BED_W or y1 > BED_H:
                QtWidgets.QMessageBox.warning(
                    self, "越界",
                    f"图元「{it.name}」超出 210×210 床面 "
                    f"(bbox {x0:.0f},{y0:.0f}-{x1:.0f},{y1:.0f})。仍导出？",
                )
                break
        svg = document_to_svg(self.doc)
        self.export_requested.emit(svg)


class _TextDialog(QtWidgets.QDialog):
    """文字编辑对话框（新增/重编共用）。"""

    def __init__(self, parent, edit_item: Item | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑文字" if edit_item else "添加文字")
        spec = (edit_item.text_spec or {}) if edit_item else {}
        form = QtWidgets.QFormLayout(self)
        self._text = QtWidgets.QPlainTextEdit(spec.get("text", "写字机测试"))
        self._text.setFixedHeight(80)
        form.addRow("文字:", self._text)
        self._mode = QtWidgets.QComboBox()
        self._mode.addItem("轮廓空心字（大字号清晰）", "outline")
        self._mode.addItem("单线手写体（可小字）", "single")
        if spec.get("mode") == "single":
            self._mode.setCurrentIndex(1)
        form.addRow("模式:", self._mode)
        self._font = QtWidgets.QComboBox()
        self._font_paths = list_fonts()
        for name, _path in self._font_paths:
            self._font.addItem(name)
        # 选中已用字体
        cur = spec.get("font_path")
        for i, (_n, p) in enumerate(self._font_paths):
            if p == cur:
                self._font.setCurrentIndex(i)
                break
        form.addRow("字体:", self._font)
        self._size = QtWidgets.QDoubleSpinBox()
        self._size.setRange(2, 100)
        self._size.setValue(float(spec.get("size_mm", 15.0)))
        form.addRow("字号(mm):", self._size)
        self._cgap = QtWidgets.QDoubleSpinBox()
        self._cgap.setRange(-5, 20)
        self._cgap.setValue(float(spec.get("char_gap_mm", 0.0)))
        form.addRow("字距(mm):", self._cgap)
        self._lgap = QtWidgets.QDoubleSpinBox()
        self._lgap.setRange(0, 50)
        self._lgap.setValue(float(spec.get("line_gap_mm", 2.0)))
        form.addRow("行距(mm):", self._lgap)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok
                                        | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def text(self) -> str:
        return self._text.toPlainText()

    def spec(self) -> dict:
        idx = self._font.currentIndex()
        path = self._font_paths[idx][1] if 0 <= idx < len(self._font_paths) else None
        return {"text": self.text(), "mode": self._mode.currentData(),
                "font_path": path, "size_mm": self._size.value(),
                "char_gap_mm": self._cgap.value(),
                "line_gap_mm": self._lgap.value()}

    def generate_paths(self):
        s = self.spec()
        if s["mode"] == "single":
            data = _DATA_DIR / "chinese_hershey_heiti.json"
            svg = text_singleline_svg(s["text"], data_path=data,
                                      size_mm=s["size_mm"],
                                      char_gap_mm=s["char_gap_mm"],
                                      fallback_font=s["font_path"] or find_cjk_font())
        else:
            svg = text_outline_svg(s["text"], font_path=s["font_path"],
                                   size_mm=s["size_mm"],
                                   line_gap_mm=s["line_gap_mm"],
                                   char_gap_mm=s["char_gap_mm"])
        return _svg_to_paths(svg)
