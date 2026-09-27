"""排版/制作页（Qt）—— CAD/PS 式编辑 + 工具 + 属性 + 导入/导出。

能力：
- 添加：文字（字体可选、可双击重编）、图片（中心线/轮廓）、SVG、Word/Excel、绘制图元
- 编辑：框选/Ctrl多选/Ctrl+A、删除、撤销/重做、复制粘贴、箭头微调、
        层序（置顶/置底/上移/下移）、对齐/分布、W/H 数值
- 画布：mm 网格、缩放控件、标尺、网格/对象吸附、旋转缩放手柄
- 导出：另存为 SVG / 送去执行（按 z 排序、跳过隐藏）

坐标契约（阶段 2 y-up 统一，docs/preview-layout-blueprint.md §2.1/§2.2）：
- 模型 Item 存**纸面 mm y-up**（原点左下 0..210）；**场景 ≡ 纸面**（同值），
  paint 直画无翻转。y 翻转只存在于 ``canvas/view_transform.py``（视图层
  唯一负比例尺）与 SVG 互换层（``coords.paper_from_svg_ydown`` 等）。
- 鼠标坐标一律 ``mm_from_view`` 浮点（PaperView 已保证）。
- 拖动结束经 :class:`~megapro.gui.canvas.undo_cmds.MoveItemsCommand`（手势
  token）回写 model 并进撤销栈；数值定位走九宫格锚点（默认 bl），
  多选按选择集 bbox 锚点整体平移/等比缩放（不塌缩）。
- 组导入契约 :func:`_import_group`：整组 ``paper_from_svg_ydown`` →
  **组级一次** ``place_at_anchor`` → 逐 Item ``normalize_local``；严禁逐
  Item 归位（Word/Excel 相对布局必须保持）。
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from megapro.gui.canvas.coords import (
    BED_H,
    BED_W,
    anchor_point,
    machine_from_paper,
    paper_from_svg_ydown,
    place_at_anchor,
)
from megapro.gui.canvas.handles import SelectionHandles
from megapro.gui.canvas.items import PathItem
from megapro.gui.canvas.paper_scene import PaperScene
from megapro.gui.canvas.paper_view import PaperView
from megapro.gui.canvas.rulers import RulerWidget
from megapro.gui.canvas.snap import SnapEngine
from megapro.gui.canvas.undo_cmds import (
    AddItemsCommand,
    ChangeItemPropsCommand,
    ClearCommand,
    EditTextCommand,
    MoveItemsCommand,
    RemoveItemsCommand,
    new_gesture_token,
)
from megapro.gui.layout.export_svg import document_to_svg
from megapro.gui.layout.model import Document, Item, normalize_local

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
    """把 SVG 字符串解析为 polylines（经临时文件走 toolchain parse_svg）。

    名称保留（doc_import / tests/test_gui_edge.py / test_gui_layout_window.py
    依赖，§2.2 评审 missing #6）。输出为 SVG y-down 数值 —— 入库前须走
    组导入契约（:func:`_import_group` / :func:`_group_paths_to_paper`）。
    """
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


def _group_paths_to_paper(
    paths_svg_list, *, anchor: str = "bl", target: tuple[float, float] = (0.0, 0.0),
) -> list:
    """组导入契约几何段（§2.2）：①整组 paper_from_svg_ydown ②组级一次 place_at_anchor。

    ①SVG y-down → 纸面 y-up（格式编解码，保相对布局）；
    ②对**整组拼接 bbox** 一次归位（严禁逐 Item 归位 —— 段落/表格会塌到原点）。
    返回与输入等长的逐图元纸面 y-up 折线组。
    """
    flipped = [paper_from_svg_ydown(pg) for pg in paths_svg_list]
    flat = [p for pg in flipped for p in pg]
    placed = place_at_anchor(flat, anchor, target)
    out: list = []
    i = 0
    for pg in flipped:
        out.append(placed[i:i + len(pg)])
        i += len(pg)
    return out


def _import_group(
    paths_svg_list, *, name, anchor: str = "bl",
    target: tuple[float, float] = (0.0, 0.0),
) -> list[Item]:
    """完整组导入契约：①翻转 ②组级一次归位 ③逐 Item normalize_local。

    单图元 = 组长度 1。``name`` 为 str 时单图元原样用作名称、多图元加 ``:i``
    后缀；也可传与组等长的名称序列。③只做局部重锚（页面几何不变）。
    """
    groups = _group_paths_to_paper(paths_svg_list, anchor=anchor, target=target)
    if isinstance(name, str):
        names = [name if len(groups) == 1 else f"{name}:{i + 1}"
                 for i in range(len(groups))]
    else:
        names = list(name)
    items: list[Item] = []
    for i, paths in enumerate(groups):
        it = Item(
            paths=[[(float(x), float(y)) for x, y in p] for p in paths],
            pos=(0.0, 0.0),
            name=(names[i] if i < len(names) else f"item:{i + 1}"),
            z=float(i),
        )
        normalize_local(it)
        items.append(it)
    return items


def _pos_for_anchor(
    item: Item, target: tuple[float, float], anchor: str = "bl",
) -> tuple[float, float]:
    """九宫格锚点绝对定位：``pos = target − R(θ)S(s)·anchor_offset``（§2.2）。"""
    ax, ay = anchor_point(item.bbox(), anchor)
    s = item.scale
    rad = math.radians(item.angle_deg)
    cos, sin = math.cos(rad), math.sin(rad)
    ox, oy = ax * s, ay * s
    return (target[0] - (ox * cos - oy * sin), target[1] - (ox * sin + oy * cos))


def _union_bbox(boxes) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )


class LayoutPage(QtWidgets.QWidget):
    """排版页：信号 export_requested(JobSpec) 供 MainWindow 送去作业。

    导出链（阶段 3）：:meth:`to_job_spec` = ``flatten_visible(doc)`` +
    ``Placement(mode='preserve')``（版面坐标即工件坐标，排版直传不归位）；
    「另存为 SVG」走同一 :func:`document_to_svg` 格式写盘。
    """

    export_requested = QtCore.Signal(object)  # JobSpec（纯逻辑对象）
    status_message = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.doc = Document()
        self._scene_items: list[PathItem] = []
        self._applying = False
        self._tool = TOOL_SELECT
        self._preview_item = None
        self._draw_start = None
        self._poly_pts: list[QtCore.QPointF] = []
        self.snap_pitch = 1.0
        self.snap_enabled = True
        #: 数值定位九宫格锚点（§4.2 默认 bl）
        self._pos_anchor = "bl"
        #: 数值输入手势 token（同字段连续改值合并为一条撤销，评审 low #6）
        self._prop_token: int | None = None
        self._prop_sender = None
        self._undo = QtGui.QUndoStack(self)
        self._build_ui()
        self._refresh_props()
        self._refresh_undo_actions()

    # -- 供 undo 命令调用 --------------------------------------------------

    def _gi_for(self, item: Item) -> PathItem | None:
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
        """model → 场景（场景 ≡ 纸面 y-up，**无翻转**）—— 场景同步唯一入口。"""
        gi = self._gi_for(item)
        if gi is None:
            return
        gi.apply_model_state()
        gi.setZValue(item.z)
        gi.setVisible(item.visible)
        gi.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, not item.locked)
        gi.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, not item.locked)
        gi.update()

    def _after_change(self) -> None:
        """任何编辑后：刷新属性/手柄。"""
        self._refresh_props()
        self._handles.sync(self._selected())

    def _refresh_undo_actions(self) -> None:
        if hasattr(self, "_undo_act"):
            self._undo_act.setText(f"撤销 {self._undo.undoText()}".strip())
            self._redo_act.setText(f"重做 {self._undo.redoText()}".strip())

    # -- UI ----------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(self._build_toolbar())
        self.scene = PaperScene(self)
        self.view = PaperView(self.scene, self)
        self.view.set_tool(TOOL_SELECT)
        self._handles = SelectionHandles(self.scene, self.view, self)
        self.view.viewChanged.connect(self._sync_snap_pitch)
        self.view.fit()
        self._sync_snap_pitch()
        self.scene.selectionChanged.connect(self._on_selection_changed)
        # 画布 + 标尺（上/左）
        canvas_box = QtWidgets.QGridLayout()
        self._ruler_h = RulerWidget(self.view, "h")
        self._ruler_v = RulerWidget(self.view, "v")
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
        # 吸附开关（§4.2）
        self.btn_snap = add_btn("吸附", self._toggle_snap, checkable=True)
        self.btn_snap.setChecked(True)
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
            sp.editingFinished.connect(self._end_prop_gesture)
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
        self.sp_scale.editingFinished.connect(self._end_prop_gesture)
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

    def _toggle_snap(self) -> None:
        self.snap_enabled = self.btn_snap.isChecked()

    def _sync_snap_pitch(self) -> None:
        """网格吸附 pitch 随缩放定价 = grid_steps(ppm) 的 minor（§2.2/Q4）。

        缩放变化时自动更新 ``snap_pitch``（测试/调用方可显式覆盖，下次缩放
        重算）。评审 low #3①：旧实现恒 1.0mm 不随缩放。
        """
        ppm = self.view.px_per_mm()
        if ppm > 0:
            self.snap_pitch = SnapEngine.minor_pitch(ppm)

    def _snap_engine(self) -> SnapEngine:
        return SnapEngine(self.snap_pitch, enabled=self.snap_enabled)

    def _snap_pt(self, p: QtCore.QPointF) -> QtCore.QPointF:
        """绘制工具吸附（§2.2：网格 + 对象，同样接入 SnapEngine）。"""
        engine = self._snap_engine()
        x, y = engine.snap((float(p.x()), float(p.y())), self._all_paths())
        return QtCore.QPointF(x, y)

    def _all_paths(self) -> list:
        return [p for it in self.doc.items for p in it.paths]

    # -- 选中/属性 ---------------------------------------------------------

    def _selected(self) -> list[PathItem]:
        return [gi for gi in self.scene.selectedItems()
                if isinstance(gi, PathItem)]

    def _on_selection_changed(self) -> None:
        self._end_prop_gesture()  # 换目标即新手势（禁止跨选中合并）
        self._refresh_props()
        self._handles.sync(self._selected())

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
        it = sel[0].model_item
        if len(sel) == 1:
            self.sp_x.setValue(it.pos[0])
            self.sp_y.setValue(it.pos[1])
            x0, y0, x1, y1 = it.page_bbox()
        else:
            # 多选：X/Y 显示选择集 bbox 锚点（与 _apply_pos 的整体平移一致）
            x0, y0, x1, y1 = _union_bbox([gi.model_item.page_bbox() for gi in sel])
            ax, ay = anchor_point((x0, y0, x1, y1), self._pos_anchor)
            self.sp_x.setValue(ax)
            self.sp_y.setValue(ay)
        self.sp_w.setValue(abs(x1 - x0))
        self.sp_h.setValue(abs(y1 - y0))
        self.sp_ang.setValue(it.angle_deg)
        self.sp_scale.setValue(it.scale)
        for s in spins:
            s.blockSignals(False)

    def _apply_props(self) -> None:
        """数值定位（根因 #10）：按改动字段分派，全部走可撤销命令。"""
        sel = self._selected()
        if not sel:
            return
        src = self.sender()
        if src in (self.sp_x, self.sp_y):
            self._apply_pos(self.sp_x.value(), self.sp_y.value())
        elif src is self.sp_w:
            self._apply_size(self.sp_w.value(), axis="w")
        elif src is self.sp_h:
            self._apply_size(self.sp_h.value(), axis="h")
        elif src is self.sp_ang:
            self._apply_angle(self.sp_ang.value())
        elif src is self.sp_scale:
            self._apply_scale(self.sp_scale.value())

    def _end_prop_gesture(self) -> None:
        """结束数值输入手势（editingFinished / 换选中）：下一次改值另起撤销条目。"""
        self._prop_token = None
        self._prop_sender = None

    def _push_props(self, changes, text: str) -> None:
        """推属性命令；同字段连续改值（键入 "100"=1→10→100）共用一个手势 token
        合并为一条撤销（评审 low #6）。"""
        if not changes:
            return
        src = self.sender()
        if src is not self._prop_sender or self._prop_token is None:
            self._prop_sender = src
            self._prop_token = new_gesture_token()
        self._undo.push(ChangeItemPropsCommand(self, changes, text,
                                               token=self._prop_token))

    def _apply_pos(self, tx: float, ty: float) -> None:
        """单选=锚点绝对定位；多选=按选择集 bbox 锚点整体平移（不塌缩）。"""
        sel = self._selected()
        changes = []
        if len(sel) == 1:
            it = sel[0].model_item
            changes.append((it, {"pos": it.pos},
                           {"pos": _pos_for_anchor(it, (tx, ty), self._pos_anchor)}))
        else:
            boxes = [gi.model_item.page_bbox() for gi in sel]
            ax, ay = anchor_point(_union_bbox(boxes), self._pos_anchor)
            dx, dy = tx - ax, ty - ay
            for gi in sel:
                it = gi.model_item
                changes.append((it, {"pos": it.pos},
                               {"pos": (it.pos[0] + dx, it.pos[1] + dy)}))
        self._push_props(changes, "数值定位")

    def _apply_size(self, value: float, *, axis: str) -> None:
        """宽/高等比联动（sp_h 生效：scale × target/cur）。"""
        sel = self._selected()
        if value <= 0:
            return
        if len(sel) == 1:
            it = sel[0].model_item
            x0, y0, x1, y1 = it.page_bbox()
            cur = abs(x1 - x0) if axis == "w" else abs(y1 - y0)
            if cur <= 1e-9:
                return
            k = value / cur
            self._push_props(
                [(it, {"scale": it.scale}, {"scale": it.scale * k})], "等比缩放")
            return
        boxes = [gi.model_item.page_bbox() for gi in sel]
        u = _union_bbox(boxes)
        cur = abs(u[2] - u[0]) if axis == "w" else abs(u[3] - u[1])
        if cur <= 1e-9:
            return
        k = value / cur
        ax, ay = anchor_point(u, self._pos_anchor)
        changes = []
        for gi in sel:
            it = gi.model_item
            changes.append((it,
                            {"pos": it.pos, "scale": it.scale},
                            {"pos": (ax + k * (it.pos[0] - ax),
                                     ay + k * (it.pos[1] - ay)),
                             "scale": it.scale * k}))
        self._push_props(changes, "整体等比缩放")

    def _apply_angle(self, deg: float) -> None:
        changes = []
        for gi in self._selected():
            it = gi.model_item
            changes.append((it, {"angle_deg": it.angle_deg}, {"angle_deg": deg}))
        self._push_props(changes, "旋转")

    def _apply_scale(self, value: float) -> None:
        sel = self._selected()
        if value <= 0 or not sel:
            return
        if len(sel) == 1:
            it = sel[0].model_item
            self._push_props(
                [(it, {"scale": it.scale}, {"scale": value})], "缩放")
            return
        k = value / (sel[0].model_item.scale or 1e-9)
        boxes = [gi.model_item.page_bbox() for gi in sel]
        ax, ay = anchor_point(_union_bbox(boxes), self._pos_anchor)
        changes = []
        for gi in sel:
            it = gi.model_item
            changes.append((it,
                            {"pos": it.pos, "scale": it.scale},
                            {"pos": (ax + k * (it.pos[0] - ax),
                                     ay + k * (it.pos[1] - ay)),
                             "scale": it.scale * k}))
        self._push_props(changes, "整体等比缩放")

    def _sync_models(self) -> None:
        """model → 场景推送（幂等；拖动已由 MoveItemsCommand 回写 model）。"""
        for gi in list(self._scene_items):
            self._sync_gi(gi.model_item)

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
        it = _import_group([paths], name=f"文字:{dlg.text()[:8]}")[0]
        if edit_item is not None:
            self._undo.push(EditTextCommand(self, edit_item, it.paths, dlg.spec()))
        else:
            it.z = self.doc.top_z() + 1
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
        mm.setRange(10, BED_W)
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
        it = _import_group([paths], name=f"图:{Path(path).name[:8]}")[0]
        it.z = self.doc.top_z() + 1
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
        it = _import_group([paths], name=f"SVG:{Path(path).name[:8]}")[0]
        it.z = self.doc.top_z() + 1
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

    # -- 绘制预览（供 canvas 调用；坐标 = 纸面 mm 浮点） --------------------

    def _clear_preview(self) -> None:
        if self._preview_item is not None:
            try:
                self.scene.removeItem(self._preview_item)
            except RuntimeError:
                pass
            self._preview_item = None

    def begin_draw(self, scene_pos) -> None:
        self._clear_preview()
        p = self._snap_pt(QtCore.QPointF(scene_pos))
        self._draw_start = p
        self._poly_pts = [p]

    def update_draw(self, scene_pos) -> None:
        self._clear_preview()
        s = self._draw_start
        if s is None or self._tool == TOOL_SELECT:
            return
        cur = self._snap_pt(QtCore.QPointF(scene_pos))
        # 预览统一：QGraphicsPathItem，无填充（只描边），避免出现"封闭面"
        path = QtGui.QPainterPath()
        if self._tool == TOOL_LINE:
            path.moveTo(s)
            path.lineTo(cur)
        elif self._tool == TOOL_RECT:
            path.addRect(QtCore.QRectF(s, cur).normalized())
        elif self._tool == TOOL_CIRCLE:
            path.addEllipse(QtCore.QRectF(s, cur).normalized())
        elif self._tool in (TOOL_POLY, TOOL_PENCIL):
            pts = self._poly_pts + [cur]
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
            # 场景 ≡ 纸面 y-up（§2.1）：无翻转，经 coords 恒等换算
            return machine_from_paper((float(p.x()), float(p.y())))

        cur = self._snap_pt(QtCore.QPointF(scene_pos))
        paths = []
        if self._tool == TOOL_LINE:
            paths = [[to_paper(s), to_paper(cur)]]
        elif self._tool == TOOL_RECT:
            r = QtCore.QRectF(s, cur).normalized()
            c = [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft(),
                 r.topLeft()]
            paths = [[to_paper(p) for p in c]]
        elif self._tool == TOOL_CIRCLE:
            r = QtCore.QRectF(s, cur).normalized()
            cx, cy = r.center().x(), r.center().y()
            rx, ry = r.width() / 2, r.height() / 2
            pts = []
            for k in range(49):
                a = 2 * math.pi * k / 48
                pts.append(to_paper(QtCore.QPointF(cx + rx * math.cos(a),
                                                   cy + ry * math.sin(a))))
            paths = [pts]
        elif self._tool in (TOOL_POLY, TOOL_PENCIL):
            pts = list(self._poly_pts)
            if pts and (pts[-1].x() != cur.x() or pts[-1].y() != cur.y()):
                pts.append(cur)
            paths = [[to_paper(p) for p in pts]] if len(pts) >= 2 else []
        self._draw_start = None
        self._poly_pts = []
        if paths:
            it = Item(paths=paths, pos=(0.0, 0.0),
                      name={"line": "直线", "rect": "矩形", "circle": "圆",
                            "poly": "折线", "pencil": "自由笔"}.get(self._tool, "图元"),
                      z=self.doc.top_z() + 1)
            # 绘制工具直接产纸面 y-up：跳过①②，但走③归一（§2.2）
            normalize_local(it)
            self._add_items([it], "绘制")

    def add_poly_point(self, scene_pos) -> None:
        self._poly_pts.append(self._snap_pt(QtCore.QPointF(scene_pos)))

    def cancel_draw(self) -> None:
        """取消当前绘制（切工具/右键）。"""
        self._clear_preview()
        self._draw_start = None
        self._poly_pts = []

    def _clear(self) -> None:
        if self.doc.items:
            self._undo.push(ClearCommand(self))

    def _fit(self) -> None:
        self.view.fit()

    def _zoom_in(self) -> None:
        self.view.set_zoom(self.view.px_per_mm() * 1.25)

    def _zoom_out(self) -> None:
        self.view.set_zoom(self.view.px_per_mm() / 1.25)

    def _zoom_100(self) -> None:
        self.view.set_zoom(1.0)

    # -- 双击重编文字 ------------------------------------------------------

    def edit_text_item(self, item: Item) -> None:
        if item.text_spec:
            self._add_text(edit_item=item)

    # -- 导出 --------------------------------------------------------------

    def to_job_spec(self):
        """导出当前版面为 :class:`~megapro.gui.job.JobSpec`（阶段 3 导出链）。

        ``flatten_visible``（z 升序拍平，跳 hidden/<2 点）+ ``Placement(mode=
        'preserve')`` —— 版面坐标即工件坐标，**排版直传不做 anchor 归位**
        （§2.2/§10 unclear #4）。纯数据拷贝，不消费/不清空源文档。
        """
        from megapro.gui.job import JobSpec, Placement
        from megapro.gui.layout.model import flatten_visible

        self._sync_models()
        return JobSpec(
            paths_paper=flatten_visible(self.doc),
            source_name="排版版面",
            placement=Placement(mode="preserve"),
        )

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
        self.export_requested.emit(self.to_job_spec())


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
