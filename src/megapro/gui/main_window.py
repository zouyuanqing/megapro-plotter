"""MainWindow —— 上位机主窗口（中文界面，薄 Qt 绑定层）。

布局（自上而下）：
  连接条（端口下拉 / 波特率 / 连接 / 断开 / 打开日志目录）
  DRO + 状态栏（X/Y/Z 读数 + 状态标签 + 最后 ok）
  Jog 面板（XY±/Z↑↓ / 步长 / 连续按住 / 键盘方向键）+ 笔控（落笔/抬笔 + 笔态）
  回显台（原文回显 + 状态行）+ 命令输入行
  E-stop（常驻大按钮）+ 中止（停靠）

线程/所有权：SerialWorker 常驻于 QThread；本窗口只经 signal/slot。
DRO 用 QTimer 在 READY 空闲态轮询 M114。
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from megapro.gui.canvas.coords import (
    BED_H,
    BED_W,
    anchor_point,
    bbox_of,
    paper_from_svg_ydown,
)
from megapro.gui.canvas.gcode_items import GcodePathItem, LiveMarker
from megapro.gui.canvas.paper_scene import PaperScene
from megapro.gui.canvas.paper_view import PaperView
from megapro.gui.canvas.rulers import RulerWidget
from megapro.gui.controller import (
    MachineError,
    MachineState,
    build_frame_sequence,
    build_full_home_sequence,
    build_goto_origin_sequence,
    build_home_sequence,
    build_jog_sequence,
    build_move_to_sequence,
    build_park_sequence,
    build_pen_down,
    build_pen_up,
    can_start_job,
    sequence_ok,
)
from megapro.gui.job import (
    JobSpec,
    Material,
    MotionParams,
    OptParams,
    Placement,
    ZMap,
    compile_job,
    cut_z_for_depth,
)

from megapro.gui.worker import SerialWorker

try:
    import serial.tools.list_ports as _list_ports
except Exception:  # pragma: no cover - pyserial 缺失时端口列表为空
    _list_ports = None

STATE_CN = {
    "DISCONNECTED": "未连接",
    "CONNECTING": "连接中…",
    "READY": "就绪",
    "BUSY": "执行中",
    "FAULT": "故障",
    "ESTOP": "急停（需断电重启）",
}
STATE_COLOR = {
    "DISCONNECTED": "#888",
    "CONNECTING": "#c90",
    "READY": "#080",
    "BUSY": "#06c",
    "FAULT": "#c00",
    "ESTOP": "#c00",
}

DEFAULT_BAUD = 250000
BAUD_CHOICES = ["250000", "115200"]


class _JobNotCalibrated(Exception):
    """作业需要但未标定（pen_down_z / cut_touch_z 缺失）。"""


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, profile_path: Path | None = None) -> None:
        super().__init__()
        self.profile_path = profile_path or (Path("profiles") / "mega-pro-marlin.yaml")
        self._profile = self._load_profile()
        self._port_default = self._profile.get("port") or "COM7"
        # 注：guard 的 Z 判据真值 = UI 勾选框（allow_z_cb）+ _pen_down_z 是否标定，
        # 经 _sync_worker_guard_params 同时供 _send 预检与 worker._guard 两处消费
        # （同源，评审 issue#1）；此处不再保留只写不读的 _allow_z 死字段。
        self._pen_down_z = self._as_float(self._profile.get("pen_down_z"))
        self._safe_z = self._as_float(self._profile.get("safe_z")) or 30.0
        self._safe_x = self._as_float(self._profile.get("safe_x")) or 0.0
        self._safe_y = self._as_float(self._profile.get("safe_y")) or 50.0
        self._pen_state: str | None = None  # None=未知/抬起，'down'=落笔
        self._last_status = ""
        # P1 job 状态
        self._tool = "pen"  # "pen" 写字 / "knife" 裁刀
        self._cut_touch_z = self._as_float(self._profile.get("cut_touch_z"))
        self._cut_depth = 0.5  # 裁刀下压深度（相对纸面，mm）；每 job 由材料预设/输入
        self._material = "custom"  # A4/A5/A6/custom
        self._material_w = BED_W
        self._material_h = BED_H
        self._material_margin = 5.0
        # P3 作业源（§3.1 单一真源）：_job_spec（内存几何/参数）→ _recompile
        # → _job_compiled（compile 产物，lines/segments 同源）→ 预览/发送。
        self._job_spec: JobSpec | None = None
        self._job_compiled = None  # CompiledJob | None
        self._job_lines: list = []  # 将发送的 lines（= _job_compiled.lines 拷贝）
        #: 作业页当前握的作业**是否来自排版页**（B2 静默同步的来源门禁）。
        #: 只在「谁把它放进作业页」的那一处翻转：排版两条路
        #: （:meth:`_on_layout_export` / :meth:`_on_layout_job_sync`）置 True，
        #: 文件载入（:meth:`_on_load_svg`）与清空（:meth:`_on_clear_job`）置
        #: False；``_on_placement_changed`` 的 ``replace()`` 与
        #: :meth:`_recompile` 的参数投影**不**改它（同一次作业，只换字段）。
        #:
        #: ⚠ **不用 ``source_name == "排版版面"`` 当来源标记**（B2 已实测顶掉
        #: 用户作业）。那个字符串是**给用户看的显示名**
        #: （:meth:`_update_job_info` 直接拼进作业信息行），不是契约字段；且
        #: 文件作业的 ``source_name`` 取 ``Path(path).name``，而载入对话框带
        #: 「所有文件 (*)」（main_window.py:1465）⇒ 一个**无扩展名、恰好叫
        #: 「排版版面」**的文件会与排版作业撞名，静默同步随即把它接管。布尔
        #: 门禁在构造上就没有这个洞。
        self._job_from_layout = False
        #: **排版面版的越床判据**（D2-②）：``LayoutPage._out_of_bed()`` 的
        #: 快照，由 :meth:`_on_layout_job_sync` 在每次静默同步时向排版页取。
        #: 形状与其返回值相同（``(最大超出 mm, 清单)`` / ``None``）——**不在
        #: 作业页重算**：版面几何的真源在排版页，而作业页自己那套
        #: （``compiled.bounds``）判的是放置**之后**的几何，anchor 归位会把
        #: 越界件拉回床内使它为空。此字段**只用于显示告知**，
        #: 绝不参与拦截（拦截仍由 ``compiled.runnable`` 负责）。
        self._layout_out_of_bed = None
        self._job_running = False
        self._running_compiled = None  # 执行开始时的不可变快照（进度高亮/发送同源）
        self._feed_xy = 1200.0  # 作业 XY 进给率 mm/min
        self._feed_z = 300.0  # 作业 Z 进给率 mm/min
        self._travel_lift_mm = 5.0  # 段间跳段抬离量（纸面上方）
        self._pen_diameter_mm = 0.5  # 笔尖直径（去重容差 + 边界补偿）
        self._opt_dedup = False  # 重叠去重
        self._opt_sort = True  # 轨迹顺序优化
        # P1c 工件原点（GUI 维护 XY 偏移，机器坐标原点；Z 恒为机器绝对）
        self._work_origin: tuple[float, float] | None = None
        self._machine_pos: tuple[float | None, float | None, float | None] = (None, None, None)
        # 阶段 4 homed 门禁：仅在「一键寻零（home 序列）」sequenceDone(ok=True)
        # 后置位；连接打开/FAULT/ESTOP/断开一律清零（见 _spawn_worker/_on_state/
        # _teardown_thread）。can_start_job 据此拦 执行/走边框/设工件原点。
        self._homed = False
        self._pending_home = False  # 已发出、等 sequenceDone 的一键寻零序列
        self._busy = False  # 连接中/归位/序列执行（_set_busy_ui 维护）
        self._link_ready = False  # 链路已建立（READY/BUSY）；未连接时动轴类按钮禁用
        self._dry_run_compiled = None  # 空跑校验（compile_job dry_run）产物

        self._thread = QtCore.QThread(self)
        self._worker: SerialWorker | None = None
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(500)  # READY 空闲 500ms
        self._poll_timer.timeout.connect(self._on_poll_tick)
        self._jog_keys_enabled = True

        self._build_ui()
        self._refresh_port_list()
        self._load_last_preset()

    def _load_last_preset(self) -> None:
        """启动时若 presets/last.json 有记录则自动加载该预设。

        安全校验：预设中 pen_down_z/cut_touch_z 若 < 5（笔尖几近床面/撞床危险值，
        真机实测 ~17）则拒绝自动加载并警告，避免坏标定让落笔撞床。
        """
        try:
            from megapro.gui.presets import load_last, load_preset

            name = load_last()
            if not name:
                return
            cfg = load_preset(name)
            # 安全校验（复用 presets.preset_z_problems）
            from megapro.gui.presets import preset_z_problems

            bad = preset_z_problems(cfg)
            if bad:
                print(f"preset {name} 标定可疑（{'、'.join(bad)}），跳过自动加载")
                self._append_console(
                    f"预设「{name}」标定可疑（{'、'.join(bad)}）——已跳过自动加载；"
                    "请重做 Z 标定或删除该预设。")
                return
            self._apply_preset_cfg(cfg)
        except Exception as exc:  # noqa: BLE001 - 启动不因预设崩
            print(f"preset load skipped: {exc}")

    # -- profile -----------------------------------------------------------

    def _load_profile(self) -> dict:
        try:
            from megapro.cli.main import _load_profile

            return _load_profile(self.profile_path) or {}
        except Exception:
            return {}

    @staticmethod
    def _as_float(v) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _fit_window_to_screen(self) -> None:
        """把窗口初始尺寸限制在屏幕可用区内（防超出桌面）。

        取主屏 availableGeometry 的 92%，与期望尺寸取小；并设一个较小的
        最小尺寸，保证用户可继续缩小。屏幕很小时以屏幕为准。
        """
        want_w, want_h = 1080, 760
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            max_w = int(g.width() * 0.92)
            max_h = int(g.height() * 0.92)
            w = min(want_w, max_w)
            h = min(want_h, max_h)
            self.setMinimumSize(min(640, max_w), min(480, max_h))
            self.resize(max(w, 320), max(h, 240))
            # 居中到屏幕
            self.move(g.center().x() - self.width() // 2,
                      g.center().y() - self.height() // 2)
        else:
            self.resize(want_w, want_h)

    # -- UI 构建 -----------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("Mega Pro 写字/裁纸上位机")
        # 初始尺寸适配屏幕可用区（不超出桌面）：默认 1080×760，但不超过屏幕 92%
        self._fit_window_to_screen()
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        # 常驻：连接条 / DRO（两个 tab 共享）
        root.addLayout(self._build_connect_bar())
        root.addLayout(self._build_dro_bar())

        # Tab：Tab0 控制/作业；Tab1 排版/制作
        self.tabs = QtWidgets.QTabWidget()
        control_tab = QtWidgets.QWidget()
        ct = QtWidgets.QVBoxLayout(control_tab)
        ct.setContentsMargins(0, 0, 0, 0)
        ct.addLayout(self._build_align_bar())
        ct.addLayout(self._build_jog_pen_row())
        # 中部：左侧 控制/命令台，右侧 文件作业（载入/预览/执行）
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self._build_console())
        split.addWidget(left)
        split.addWidget(self._build_job_panel())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        ct.addWidget(split, 1)
        self.tabs.addTab(control_tab, "控制 / 作业")

        # 排版页
        from megapro.gui.layout.layout_page import LayoutPage

        self.layout_page = LayoutPage()
        self.layout_page.export_requested.connect(self._on_layout_export)
        self.layout_page.job_sync_requested.connect(self._on_layout_job_sync)
        self.layout_page.status_message.connect(self._append_console)
        self.tabs.addTab(self.layout_page, "排版 / 制作")
        root.addWidget(self.tabs, 1)

        root.addLayout(self._build_estop_row())

        # E-stop 快捷键：Space 或 Esc（当命令行未聚焦时）
        for key in (QtGui.QKeySequence("Space"), QtGui.QKeySequence("Esc")):
            sc = QtGui.QShortcut(key, self)
            sc.activated.connect(self._on_estop_clicked)

        # 全部控件就绪后按 homed/允许 Z/忙态统一刷新使能（阶段 4 门禁接线）
        self._refresh_gate_ui()

    def _build_connect_bar(self) -> QtWidgets.QLayout:
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("端口："))
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setMinimumWidth(140)
        bar.addWidget(self.port_combo)
        bar.addWidget(QtWidgets.QLabel("波特率："))
        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(BAUD_CHOICES)
        baud = str(self._profile.get("baud") or DEFAULT_BAUD)
        if baud in BAUD_CHOICES:
            self.baud_combo.setCurrentText(baud)
        bar.addWidget(self.baud_combo)
        self.connect_btn = QtWidgets.QPushButton("连接")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        bar.addWidget(self.connect_btn)
        self.disconnect_btn = QtWidgets.QPushButton("断开")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        bar.addWidget(self.disconnect_btn)
        self.log_btn = QtWidgets.QPushButton("打开日志目录")
        self.log_btn.clicked.connect(self._open_log_dir)
        bar.addWidget(self.log_btn)
        # 配置预设
        bar.addSpacing(12)
        bar.addWidget(QtWidgets.QLabel("预设："))
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.setMinimumWidth(120)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        bar.addWidget(self.preset_combo)
        self.preset_save_btn = QtWidgets.QPushButton("存为预设")
        self.preset_save_btn.setToolTip("把当前全部设置存成命名预设并同步 profile")
        self.preset_save_btn.clicked.connect(self._on_preset_save)
        bar.addWidget(self.preset_save_btn)
        self.preset_load_btn = QtWidgets.QPushButton("加载")
        self.preset_load_btn.clicked.connect(self._on_preset_load)
        bar.addWidget(self.preset_load_btn)
        self.preset_del_btn = QtWidgets.QPushButton("删除")
        self.preset_del_btn.clicked.connect(self._on_preset_delete)
        bar.addWidget(self.preset_del_btn)
        self._refresh_preset_list()
        bar.addStretch(1)
        return bar

    def _refresh_preset_list(self) -> None:
        from megapro.gui.presets import list_presets

        cur = self.preset_combo.currentText()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for name in list_presets():
            self.preset_combo.addItem(name)
        if cur:
            i = self.preset_combo.findText(cur)
            if i >= 0:
                self.preset_combo.setCurrentIndex(i)
        self.preset_combo.blockSignals(False)

    def _build_dro_bar(self) -> QtWidgets.QLayout:
        bar = QtWidgets.QHBoxLayout()
        self.pos_x = self._dro_label("X")
        self.pos_y = self._dro_label("Y")
        self.pos_z = self._dro_label("Z")
        for lbl in (self.pos_x, self.pos_y, self.pos_z):
            bar.addWidget(lbl)
        bar.addSpacing(8)
        # 工件原点（P1c，GUI 维护 XY 偏移，不发 G92）
        self.set_origin_btn = QtWidgets.QPushButton("设工件原点")
        self.set_origin_btn.setToolTip("把当前笔尖位置设为工件 XY 原点（DRO 归零）")
        self.set_origin_btn.clicked.connect(self._on_set_origin)
        bar.addWidget(self.set_origin_btn)
        self.clear_origin_btn = QtWidgets.QPushButton("清原点")
        self.clear_origin_btn.setToolTip("清除工件偏移，DRO 回到机器坐标")
        self.clear_origin_btn.clicked.connect(self._on_clear_origin)
        bar.addWidget(self.clear_origin_btn)
        self.origin_label = QtWidgets.QLabel("工件原点：机器(0,0)")
        self.origin_label.setStyleSheet("color:#666")
        bar.addWidget(self.origin_label)
        bar.addSpacing(8)
        self.state_label = QtWidgets.QLabel(STATE_CN["DISCONNECTED"])
        self.state_label.setStyleSheet(f"font-weight:bold; color:{STATE_COLOR['DISCONNECTED']}")
        bar.addWidget(self.state_label)
        bar.addSpacing(8)
        self.status_label = QtWidgets.QLabel("未连接")
        self.status_label.setStyleSheet("color:#666")
        bar.addWidget(self.status_label, 1)
        return bar

    def _dro_label(self, axis: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(f"{axis}: ---")
        lbl.setFont(QtGui.QFont("Consolas", 16, QtGui.QFont.Bold))
        return lbl

    def _build_align_bar(self) -> QtWidgets.QLayout:
        """对准工具条（§4.1-3 / §5-④）：走边框 Frame、四角/中心点检、回原点、空跑校验。

        全部为 G0 空移对准（先抬到安全 Z、不下压）；homed 门禁：未归位一律
        禁用并给中文原因（can_start_job）。空跑校验 = compile_job(dry_run=True)
        的 lines（Z 恒 safe_z、全程无落笔/下压），可选发送（物理空跑）。
        """
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("对准："))
        self.frame_btn = QtWidgets.QPushButton("走边框 Frame")
        self.frame_btn.setToolTip(
            "描内容 bbox 的 G0 矩形环绕（先抬到安全 Z，不下压）——挪纸/夹具有时间对准")
        self.frame_btn.clicked.connect(self._on_frame)
        bar.addWidget(self.frame_btn)
        bar.addWidget(QtWidgets.QLabel("轮数："))
        self.frame_rounds_combo = QtWidgets.QComboBox()
        self.frame_rounds_combo.addItem("1 轮", 1)
        self.frame_rounds_combo.addItem("3 轮", 3)
        self.frame_rounds_combo.setToolTip("环绕轮数（1/3 可选）；<1 一律归 1")
        bar.addWidget(self.frame_rounds_combo)
        self.check_bl_btn = self._align_btn("左下点检", "bl")
        self.check_br_btn = self._align_btn("右下点检", "br")
        self.check_tr_btn = self._align_btn("右上点检", "tr")
        self.check_tl_btn = self._align_btn("左上点检", "tl")
        self.check_center_btn = self._align_btn("中心点检", "c")
        self.goto_origin_btn = QtWidgets.QPushButton("回原点")
        self.goto_origin_btn.setToolTip("G0 回到工件原点（设原点后复核落点）")
        self.goto_origin_btn.clicked.connect(self._on_goto_origin)
        bar.addWidget(self.goto_origin_btn)
        bar.addSpacing(12)
        self.dry_run_btn = QtWidgets.QPushButton("空跑校验")
        self.dry_run_btn.setToolTip(
            "compile_job(dry_run=True)：Z 恒=安全 Z、全程不落笔/不下压，"
            "段全部按 TRAVEL/SETUP 对待；可选把这份空跑 G-code 发机器走一遍")
        self.dry_run_btn.clicked.connect(self._on_dry_run)
        bar.addWidget(self.dry_run_btn)
        self.dry_run_send_cb = QtWidgets.QCheckBox("空跑发送")
        self.dry_run_send_cb.setToolTip("勾选后『空跑校验』编译完成即把空跑 G-code 发给机器物理走一遍")
        bar.addWidget(self.dry_run_send_cb)
        bar.addStretch(1)
        return bar

    def _align_btn(self, text: str, corner: str) -> QtWidgets.QPushButton:
        """四角/中心点检按钮：点击后 G0 点检内容 bbox 的对应点（build_move_to_sequence）。"""
        btn = QtWidgets.QPushButton(text)
        btn.setToolTip("抬到安全 Z 后 G0 空移点检内容 bbox 对应点（不下压）")
        btn.clicked.connect(lambda _=False, c=corner: self._on_check_point(c))
        return btn

    def _build_jog_pen_row(self) -> QtWidgets.QLayout:
        row = QtWidgets.QHBoxLayout()
        row.addLayout(self._build_jog_panel())
        row.addLayout(self._build_pen_panel())
        return row

    def _build_jog_panel(self) -> QtWidgets.QLayout:
        panel = QtWidgets.QGroupBox("手动轴控制（Jog）")
        lay = QtWidgets.QVBoxLayout(panel)
        # 步长
        steps = QtWidgets.QHBoxLayout()
        steps.addWidget(QtWidgets.QLabel("步长："))
        self.step_group = QtWidgets.QButtonGroup(self)
        self._step_buttons: list[QtWidgets.QRadioButton] = []
        for val in ("0.1", "1", "10"):
            rb = QtWidgets.QRadioButton(val)
            self.step_group.addButton(rb)
            steps.addWidget(rb)
            self._step_buttons.append(rb)
        self._step_buttons[1].setChecked(True)  # 默认 1mm
        self.custom_step = QtWidgets.QLineEdit()
        self.custom_step.setFixedWidth(56)
        self.custom_step.setPlaceholderText("自定义")
        steps.addWidget(self.custom_step)
        steps.addStretch(1)
        lay.addLayout(steps)
        # 方向键网格：Z+ 上 / Z- 下 布局
        grid = QtWidgets.QGridLayout()
        self.btn_x_neg = self._jog_btn("X−")
        self.btn_y_pos = self._jog_btn("Y+")
        self.btn_x_pos = self._jog_btn("X+")
        self.btn_y_neg = self._jog_btn("Y−")
        self.btn_z_pos = self._jog_btn("Z↑")
        self.btn_z_neg = self._jog_btn("Z↓")
        grid.addWidget(self.btn_x_neg, 1, 0)
        grid.addWidget(self.btn_y_pos, 0, 1)
        grid.addWidget(self.btn_x_pos, 1, 2)
        grid.addWidget(self.btn_y_neg, 2, 1)
        grid.addWidget(self.btn_z_pos, 0, 3)
        grid.addWidget(self.btn_z_neg, 2, 3)
        self._z_jog_btns = [self.btn_z_pos, self.btn_z_neg]  # Z Jog 另需『允许 Z』
        lay.addLayout(grid)
        # 寻零
        home_row = QtWidgets.QHBoxLayout()
        self.home_full_btn = QtWidgets.QPushButton("一键寻零（抬Z→XY归零→Z归零→回安全点）")
        self.home_full_btn.setStyleSheet("font-weight:bold")
        self.home_full_btn.clicked.connect(self._on_home_full_clicked)
        home_row.addWidget(self.home_full_btn)
        self.home_btn = QtWidgets.QPushButton("仅 Home XY")
        self.home_btn.clicked.connect(self._on_home_clicked)
        home_row.addWidget(self.home_btn)
        home_row.addStretch(1)
        lay.addLayout(home_row)
        # 选项
        opts = QtWidgets.QHBoxLayout()
        self.allow_z_cb = QtWidgets.QCheckBox("允许 Z")
        self.allow_z_cb.setChecked(True)
        self.allow_z_cb.setToolTip("Z 移动/G28 Z 的安全门开关；取消勾选会禁用『一键寻零』（内含 G28 Z）")
        self.allow_z_cb.toggled.connect(lambda _checked: self._refresh_gate_ui())
        opts.addWidget(self.allow_z_cb)
        hint = QtWidgets.QLabel("XY 平移：笔落下时自动先抬 Z+5")
        hint.setStyleSheet("color:#888")
        opts.addWidget(hint)
        opts.addStretch(1)
        lay.addLayout(opts)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(panel)
        return row

    def _jog_btn(self, text: str) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(text)
        btn.setFixedSize(52, 40)
        # 按下 = 走一步（固定步长）；连续走由键盘按住 + autoRepeat 提供
        btn.pressed.connect(lambda: self._on_jog(text))
        if not hasattr(self, "_jog_btns"):
            self._jog_btns: list[QtWidgets.QPushButton] = []
        self._jog_btns.append(btn)
        return btn

    def _build_pen_panel(self) -> QtWidgets.QLayout:
        panel = QtWidgets.QGroupBox("笔/刀控制")
        lay = QtWidgets.QVBoxLayout(panel)
        self.pen_down_btn = QtWidgets.QPushButton("落笔（到触纸 Z）")
        self.pen_down_btn.clicked.connect(self._on_pen_down)
        lay.addWidget(self.pen_down_btn)
        self.pen_up_btn = QtWidgets.QPushButton("抬笔（到安全 Z）")
        self.pen_up_btn.clicked.connect(self._on_pen_up)
        lay.addWidget(self.pen_up_btn)
        self.calib_z_btn = QtWidgets.QPushButton("Z 触纸标定（非标笔必做）")
        self.calib_z_btn.setStyleSheet("font-weight:bold")
        self.calib_z_btn.setToolTip("装笔/换笔后先做：慢速下探测出笔尖触纸的绝对 Z，回写 profile")
        self.calib_z_btn.clicked.connect(self._on_calibrate_z)
        lay.addWidget(self.calib_z_btn)
        self.pen_led = QtWidgets.QLabel("笔态：未知")
        self.pen_led.setStyleSheet("color:#888; font-weight:bold")
        lay.addWidget(self.pen_led)
        self.info_label = QtWidgets.QLabel()
        lay.addWidget(self.info_label)
        lay.addStretch(1)
        self._refresh_tool_info()
        row = QtWidgets.QHBoxLayout()
        row.addWidget(panel)
        return row

    def _refresh_tool_info(self) -> None:
        """刷新工具信息标签（当前工具的触纸 Z / 切深状态）。"""
        if self._tool == "knife":
            z = self._cut_touch_z
            tip = "裁刀"
            extra = f" | 切深 {self._cut_depth_mm():g}mm" if z is not None else ""
        else:
            z = self._pen_down_z
            tip = "笔"
            extra = ""
        zs = "未标定" if z is None else f"{z:g}"
        self.info_label.setText(
            f"工具：{tip} | 触纸 Z {zs} / 安全 {self._safe_z:g}{extra} | "
            f"触纸标定：{'✓' if z is not None else '✗ 先做 Z 标定'}"
        )
        self.info_label.setStyleSheet("color:#666")

    def _build_console(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("回显 / 命令")
        lay = QtWidgets.QVBoxLayout(box)
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(2000)
        lay.addWidget(self.console, 1)
        cmd_row = QtWidgets.QHBoxLayout()
        self.cmd_input = QtWidgets.QLineEdit()
        self.cmd_input.setPlaceholderText("输入 G-code 行（经安全门校验），Enter 发送")
        self.cmd_input.returnPressed.connect(self._on_cmd_entered)
        cmd_row.addWidget(self.cmd_input, 1)
        self.send_btn = QtWidgets.QPushButton("发送")
        self.send_btn.clicked.connect(self._on_cmd_entered)
        cmd_row.addWidget(self.send_btn)
        lay.addLayout(cmd_row)
        return box

    def _build_job_panel(self) -> QtWidgets.QWidget:
        """右侧作业面板：工具/载入/预览/执行（写字/裁纸共用）。"""
        panel = QtWidgets.QGroupBox("文件作业（写字/裁纸）")
        lay = QtWidgets.QVBoxLayout(panel)

        # 工具选择 + 载入
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("工具："))
        self.tool_combo = QtWidgets.QComboBox()
        self.tool_combo.addItem("笔（写字）", "pen")
        self.tool_combo.addItem("刀（裁纸）", "knife")
        self.tool_combo.currentIndexChanged.connect(self._on_tool_changed)
        row.addWidget(self.tool_combo)
        self.load_btn = QtWidgets.QPushButton("载入 SVG…")
        self.load_btn.clicked.connect(self._on_load_svg)
        row.addWidget(self.load_btn)
        self.clear_btn = QtWidgets.QPushButton("清空")
        self.clear_btn.clicked.connect(self._on_clear_job)
        row.addWidget(self.clear_btn)
        row.addWidget(QtWidgets.QLabel("放置："))
        self.placement_combo = QtWidgets.QComboBox()
        self.placement_combo.addItem("保持版面坐标", "preserve")
        self.placement_combo.addItem("锚点归位 bl→(0,0)", "anchor")
        self.placement_combo.setToolTip(
            "preserve=版面坐标即工件坐标（排版直传/整床画板）；"
            "anchor=内容 bbox 左下对到工件原点（小画板默认）")
        self.placement_combo.currentIndexChanged.connect(self._on_placement_changed)
        row.addWidget(self.placement_combo)
        row.addStretch(1)
        lay.addLayout(row)

        # 材料（裁纸用：纸张尺寸 / 下压深度 / 安全边距；A4 超程警示）
        mat = QtWidgets.QHBoxLayout()
        mat.addWidget(QtWidgets.QLabel("材料："))
        self.material_combo = QtWidgets.QComboBox()
        # 纸张尺寸（mm）：A 系纸宽 210 恰等于床宽 → 用 BED_W/BED_H 常量表达
        # （§8.4 常量收敛：数值字面只留 coords.py 的 BED_W/BED_H 定义处）
        for label, key, w_, h_ in (
            ("A4 (210×297，超行程!)", "A4", BED_W, 297.0),
            ("A5 (148×210)", "A5", 148.0, BED_H),
            ("A6 (105×148)", "A6", 105.0, 148.0),
            ("自定义 ≤210", "custom", BED_W, BED_H),
        ):
            self.material_combo.addItem(label, (key, w_, h_))
        self.material_combo.currentIndexChanged.connect(self._on_material_changed)
        mat.addWidget(self.material_combo)
        # 初始状态与默认选中(A4)一致，并提示超程
        self._apply_material(self.material_combo.currentData())
        self._append_console(
            "默认材料 A4(210×297)：Y 超出机器行程 210，仅切可达区域（≤210×210−边距）。"
        )
        mat.addWidget(QtWidgets.QLabel("切深："))
        self.cut_depth_spin = QtWidgets.QDoubleSpinBox()
        self.cut_depth_spin.setRange(0.05, 5.0)
        self.cut_depth_spin.setSingleStep(0.1)
        self.cut_depth_spin.setValue(self._cut_depth)
        self.cut_depth_spin.setSuffix(" mm")
        self.cut_depth_spin.valueChanged.connect(self._on_material_params_changed)
        mat.addWidget(self.cut_depth_spin)
        mat.addWidget(QtWidgets.QLabel("边距："))
        self.margin_spin = QtWidgets.QDoubleSpinBox()
        self.margin_spin.setRange(0.0, 20.0)
        self.margin_spin.setValue(self._material_margin)
        self.margin_spin.setSuffix(" mm")
        self.margin_spin.valueChanged.connect(self._on_material_params_changed)
        mat.addWidget(self.margin_spin)
        mat.addStretch(1)
        self.material_row = mat
        lay.addLayout(mat)

        # 材料（裁纸）/ 信息行
        info = QtWidgets.QHBoxLayout()
        self.job_info = QtWidgets.QLabel("未载入文件")
        self.job_info.setStyleSheet("color:#666")
        # ⚠ **必须开 wordWrap**（对抗性复核 R3 实测）。D2-② 的告知串
        # 「⚠排版版面越界N处(最大超出 Xmm)，落点已移到 bbox …，按当前放置执行」
        # 只经这一个 QLabel 出去，而它默认不换行 ⇒ 窗口 1000px 时标签只分到
        # 420px、整串需要 1188px，**99 字里只有前 35 字落像素**，被切掉的
        # 恰好是「超出多少」与「已经挪到哪、照此执行」这两条**可操作事实**
        # （悬停也无 tooltip，整串无处可看）。排版页的提示行本批已按 B-16 开了
        # wordWrap，这里这条**兄弟标签**当时漏了。tooltip 再兜一层（换行前也能看全）。
        self.job_info.setWordWrap(True)
        info.addWidget(self.job_info, 1)
        # 实机十字诚实化标注（阶段 4-③）：与 LiveMarker 同语义的常驻文字说明
        self.live_hint = QtWidgets.QLabel(
            "实机十字 = M114 逻辑值（ok≠已移动；Count 不可信）")
        self.live_hint.setStyleSheet("color:#888")
        self.live_hint.setToolTip(
            "ok≠已移动：固件回 ok 只代表命令被接受，不代表电机真的走了；"
            "M114 的 Count 不可信（本机 Marlin 1.1.0-RC8 的 Count 字段已实测失真，"
            "REPORT §6）。\n"
            "执行期不轮询 M114（避免与 run_job 发送/暂停/中止流控竞争，明确不做）："
            "十字冻结置灰，标注『执行中不轮询位置』；脱节对照请停机读 M114/人工复核。")
        info.addWidget(self.live_hint)
        lay.addLayout(info)

        # 预览（PaperView ≡ 纸面 mm y-up）：床框/原点十字/网格/材料三框
        # （PaperScene）+ 标尺（RulerWidget）+ 走线三色/bbox+锚点/违规红点
        # （吃 parse_lines 同一份 lines）+ LiveMarker（M114 logical 十字）。
        self._scene = PaperScene(self)
        self._scene.set_material(self._material_w, self._material_h,
                                self._material_margin)
        self.preview = PaperView(self._scene, parent=panel)
        self.preview.setMinimumHeight(120)
        self._gcode_item: GcodePathItem | None = None
        self._overlay_items: list = []
        self._live_marker = LiveMarker()
        self._scene.addItem(self._live_marker)
        self._ruler_h = RulerWidget(self.preview, "h")
        self._ruler_v = RulerWidget(self.preview, "v")
        pv = QtWidgets.QGridLayout()
        pv.addWidget(self._ruler_h, 0, 1)
        pv.addWidget(self._ruler_v, 1, 0)
        pv.addWidget(self.preview, 1, 1)
        pv.setRowStretch(1, 1)
        pv.setColumnStretch(1, 1)
        lay.addLayout(pv, 1)

        # 执行控制
        ctl = QtWidgets.QHBoxLayout()
        self.run_btn = QtWidgets.QPushButton("开始执行")
        self.run_btn.clicked.connect(self._on_run_job)
        ctl.addWidget(self.run_btn)
        self.pause_btn = QtWidgets.QPushButton("暂停")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._on_pause_job)
        ctl.addWidget(self.pause_btn)
        self.resume_btn = QtWidgets.QPushButton("继续")
        self.resume_btn.setEnabled(False)
        self.resume_btn.clicked.connect(self._on_resume_job)
        ctl.addWidget(self.resume_btn)
        self.job_abort_btn = QtWidgets.QPushButton("中止作业")
        self.job_abort_btn.setEnabled(False)
        self.job_abort_btn.clicked.connect(self._on_job_abort)
        ctl.addWidget(self.job_abort_btn)
        lay.addLayout(ctl)

        # 速度（载入后/改变时重新生成作业 G-code）
        spd = QtWidgets.QHBoxLayout()
        spd.addWidget(QtWidgets.QLabel("XY 速度："))
        self.feed_xy_spin = QtWidgets.QDoubleSpinBox()
        self.feed_xy_spin.setRange(60, 3000)
        self.feed_xy_spin.setValue(self._feed_xy)
        self.feed_xy_spin.setSuffix(" mm/min")
        self.feed_xy_spin.setSingleStep(60)
        self.feed_xy_spin.valueChanged.connect(self._on_feed_changed)
        spd.addWidget(self.feed_xy_spin)
        spd.addWidget(QtWidgets.QLabel("Z 速度："))
        self.feed_z_spin = QtWidgets.QDoubleSpinBox()
        self.feed_z_spin.setRange(30, 600)
        self.feed_z_spin.setValue(self._feed_z)
        self.feed_z_spin.setSuffix(" mm/min")
        self.feed_z_spin.setSingleStep(30)
        self.feed_z_spin.valueChanged.connect(self._on_feed_changed)
        spd.addWidget(self.feed_z_spin)
        spd.addWidget(QtWidgets.QLabel("跳段抬升："))
        self.lift_spin = QtWidgets.QDoubleSpinBox()
        self.lift_spin.setRange(1.0, 30.0)
        self.lift_spin.setValue(self._travel_lift_mm)
        self.lift_spin.setSuffix(" mm")
        self.lift_spin.setSingleStep(1.0)
        self.lift_spin.setToolTip("段间空移时笔/刀抬离纸面的高度（3–5mm 通常够）")
        self.lift_spin.valueChanged.connect(self._on_feed_changed)
        spd.addWidget(self.lift_spin)
        spd.addStretch(1)
        lay.addLayout(spd)

        # 优化：重叠去重 / 顺序 / 笔尖直径
        opt = QtWidgets.QHBoxLayout()
        self.dedup_cb = QtWidgets.QCheckBox("重叠去重")
        self.dedup_cb.setToolTip("去除完全重叠的线段/重复线（共享边只画一次）")
        self.dedup_cb.stateChanged.connect(self._on_opt_changed)
        opt.addWidget(self.dedup_cb)
        self.sort_cb = QtWidgets.QCheckBox("优化顺序")
        self.sort_cb.setChecked(True)
        self.sort_cb.setToolTip("最近邻排序，减少空移")
        self.sort_cb.stateChanged.connect(self._on_opt_changed)
        opt.addWidget(self.sort_cb)
        opt.addWidget(QtWidgets.QLabel("笔尖直径："))
        self.pen_diameter_spin = QtWidgets.QDoubleSpinBox()
        self.pen_diameter_spin.setRange(0.1, 3.0)
        self.pen_diameter_spin.setSingleStep(0.1)
        self.pen_diameter_spin.setValue(self._pen_diameter_mm)
        self.pen_diameter_spin.setSuffix(" mm")
        self.pen_diameter_spin.setToolTip("用于重叠去重容差(半径)与越界边界补偿")
        self.pen_diameter_spin.valueChanged.connect(self._on_opt_changed)
        opt.addWidget(self.pen_diameter_spin)
        opt.addStretch(1)
        lay.addLayout(opt)

        # 进度
        prog = QtWidgets.QHBoxLayout()
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        prog.addWidget(self.progress_bar, 1)
        self.progress_label = QtWidgets.QLabel("")
        prog.addWidget(self.progress_label)
        lay.addLayout(prog)
        return panel

    def _build_estop_row(self) -> QtWidgets.QLayout:
        row = QtWidgets.QHBoxLayout()
        self.abort_btn = QtWidgets.QPushButton("中止/停靠（抬 Z 回安全点）")
        self.abort_btn.clicked.connect(self._on_abort)
        row.addWidget(self.abort_btn)
        row.addStretch(1)
        self.estop_btn = QtWidgets.QPushButton("急停 E-STOP")
        self.estop_btn.setStyleSheet(
            "background:#c00; color:white; font-weight:bold; font-size:16px; padding:8px 20px;"
        )
        self.estop_btn.setFixedHeight(44)
        self.estop_btn.clicked.connect(self._on_estop_clicked)
        row.addWidget(self.estop_btn)
        return row

    # -- 端口 --------------------------------------------------------------

    def _refresh_port_list(self) -> None:
        self.port_combo.clear()
        ports: list[tuple[str, str]] = []
        if _list_ports is not None:
            try:
                for p in _list_ports.comports():
                    desc = p.description or ""
                    ports.append((p.device, f"{p.device}  {desc}"))
            except Exception:
                ports = []
        if not ports:
            ports = [(self._port_default, f"{self._port_default}（未枚举到，请确认连接）")]
        for dev, label in ports:
            self.port_combo.addItem(label, dev)
        # 选中上次端口
        idx = self.port_combo.findData(self._port_default)
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)

    # -- 连接生命周期 -------------------------------------------------------

    def _spawn_worker(self) -> None:
        self._homed = False  # 连接打开 = 主板复位、坐标基准丢失：一律清 homed
        self._pending_home = False
        self._link_ready = False
        self._thread = QtCore.QThread(self)
        port = self.port_combo.currentData() or self.port_combo.currentText()
        try:
            baud = int(self.baud_combo.currentText())
        except ValueError:
            baud = None
        self._worker = SerialWorker(
            port,
            baud,
            allow_z=self.allow_z_cb.isChecked(),
            lift_configured=self._pen_down_z is not None,
        )
        self._worker.moveToThread(self._thread)
        self._worker.stateChanged.connect(self._on_state)
        self._worker.status.connect(self._on_status)
        self._worker.echo.connect(self._on_echo)
        self._worker.position.connect(self._on_position)
        self._worker.sequenceDone.connect(self._on_sequence_done)
        self._worker.log.connect(self._on_log_line)
        self._worker.progress.connect(self._on_job_progress)
        self._worker.jobDone.connect(self._on_job_done)
        self._open_session_log()
        self._thread.started.connect(self._worker.reqOpen)
        self._thread.start()

    def _open_session_log(self) -> None:
        """开启会话日志：logs/gui_<ts>.log（复用 transport RawLog 的格式）。"""
        try:
            logs = Path("logs")
            logs.mkdir(exist_ok=True)
            stamp = QtCore.QDateTime.currentDateTime().toString("yyyyMMdd_hhmmss")
            self._log_file = (logs / f"gui_{stamp}.log").open(
                "a", encoding="ascii", errors="replace"
            )
        except Exception:
            self._log_file = None

    def _on_log_line(self, text: str) -> None:
        if self._log_file is not None:
            try:
                self._log_file.write(text + "\n")
                self._log_file.flush()
            except Exception:
                pass

    def _on_connect_clicked(self) -> None:
        if self._worker is not None:
            return
        self._set_busy_ui(True)
        self._append_console("正在连接…（主板会复位，等待启动 ~16s）")
        self._spawn_worker()

    def _on_disconnect_clicked(self) -> None:
        if self._worker is not None:
            self._worker.reqClose.emit()
        self._teardown_thread()

    def _teardown_thread(self) -> None:
        self._homed = False  # 断开清 homed（阶段 4 门禁规则）
        self._pending_home = False
        self._link_ready = False
        if self._worker is not None:
            try:
                self._worker.reqClose.emit()
            except Exception:
                pass
            self._thread.quit()
            self._thread.wait(2000)
        self._worker = None
        if getattr(self, "_log_file", None) is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
        self._set_busy_ui(False)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._teardown_thread()
        super().closeEvent(event)

    # -- worker 信号处理 ----------------------------------------------------

    def _on_state(self, name: str) -> None:
        # 链路就绪标志：READY/BUSY = 已连接（按钮使能用）；其余 = 未连接/故障
        self._link_ready = name in ("READY", "BUSY")
        if name in ("CONNECTING", "FAULT", "ESTOP", "DISCONNECTED"):
            # homed 门禁规则（阶段 4）：连接打开（主板复位）/FAULT/ESTOP/断开
            # 一律清 homed 与在途归位标记；READY 不清（序列结束回 READY 之后
            # 才由 _on_sequence_done 按 ok 决定是否置位）。
            self._homed = False
            self._pending_home = False
        cn = STATE_CN.get(name, name)
        self.state_label.setText(cn)
        self.state_label.setStyleSheet(
            f"font-weight:bold; color:{STATE_COLOR.get(name, '#000')}"
        )
        if name == "BUSY":
            self._set_busy_ui(True)  # 执行中锁定控制；E-stop 仍可用
        elif name == "READY":
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self._set_busy_ui(False)  # 恢复 Jog/笔/命令等控制（连接时被禁用）
            self._poll_timer.start()
            self._append_console("就绪：请先归位（Home），再用绝对坐标移动。")
        elif name == "ESTOP":
            self._poll_timer.stop()
            self._set_busy_ui(False)
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            QtWidgets.QMessageBox.warning(
                self,
                "急停",
                "已发送 M112，机器已 halt。\n\n请给打印机断电重启，然后点『断开』再重新连接。",
            )
        elif name == "FAULT":
            self._poll_timer.stop()
            self._set_busy_ui(False)
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
        elif name == "DISCONNECTED":
            self._poll_timer.stop()
            self._set_busy_ui(False)
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            self._set_pen_state(None)

    def _on_sequence_done(self, ok: bool) -> None:
        """长序列发送完成：恢复控制、笔态置抬起，并按结果更新 homed（阶段 4）。

        homed **仅在**「一键寻零（home 序列，含 G28 Z）」且 sequenceDone(ok=True)
        后置位；ok=False = 序列中途被 guard 拦/超时/链路异常/急停 → 保持未归位
        并提示被拦（评审 break #6：guard 拦一半不得当成功）。清零时机见
        _on_state/_spawn_worker/_teardown_thread。
        """
        self._set_busy_ui(False)
        self._set_pen_state(None)  # 寻零结束在安全点，笔抬起
        if self._pending_home:
            self._pending_home = False
            if ok:
                self._homed = True
                self._append_console(
                    "归位完成（homed）：坐标基准已建立。ok≠已移动，"
                    "M114 的 Count 不可信——重要对准请人眼复核。")
            else:
                self._homed = False
                self._append_console(
                    "归位序列未完整执行（被安全门拦截/中断，见上方状态行）——"
                    "仍视为未归位，请排查后重新『一键寻零』。")
        elif self._current_state() == "READY":
            self._append_console("序列完成。" if ok else "序列中断/被拦截（见上方状态行）。")
        if not ok:
            # 半截序列可能停在 G91（相对）模态中间（如 Z↓ jog 的收尾 G90 未发出，
            # 评审 issue#1 后果）：补发一行 G90 复位绝对模态，防后续裸 G0 被当
            # 相对移动。G90 无运动语义，guard 必过；会话已断/急停时 send_line
            # 自行拒绝，无副作用。
            self._worker.reqSendLine.emit("G90", None)
        self._refresh_gate_ui()

    def _on_status(self, text: str) -> None:
        self._last_status = text
        self.status_label.setText(text)
        if text.lower().startswith("ok"):
            self._append_console("ok")

    def _on_echo(self, text: str) -> None:
        self._append_console(text)

    def _on_position(self, x, y, z) -> None:
        self._machine_pos = (x, y, z)
        # DRO 显示工件坐标：XY = 机器 − 原点偏移；Z 恒为机器绝对（触纸标定用机器 Z）
        ox = self._work_origin[0] if self._work_origin else 0.0
        oy = self._work_origin[1] if self._work_origin else 0.0
        wx = None if x is None else x - ox
        wy = None if y is None else y - oy
        self.pos_x.setText(f"X: {'' if wx is None else f'{wx:.2f}'}")
        self.pos_y.setText(f"Y: {'' if wy is None else f'{wy:.2f}'}")
        self.pos_z.setText(f"Z: {'' if z is None else f'{z:.2f}'}")
        # LiveMarker：M114 logical 十字（≡ 纸面 mm）；ok≠已移动、Count 不可信。
        # 执行期迟到回报（reqPoll 与 reqRunJob 的排队竞态）不点亮十字：
        # LiveMarker.set_machine 在冻结态直接丢弃回报（阶段 5 竞态修复），
        # 这里再按 _job_running 只更新 DRO/缓存、不动十字（双保险，保持灰虚
        # 冻结）；执行结束后的回报恢复正常更新（评审 issue#6，仅显示层）。
        if self._job_running:
            return
        if x is not None and y is not None:
            self._live_marker.set_machine(x, y)
        else:
            self._live_marker.hide()

    # -- 工件原点（P1c，GUI 维护 XY 偏移） ---------------------------------

    def _on_set_origin(self) -> None:
        """把当前机器 XY 设为工件原点：记录偏移，DRO XY 归零。

        homed 门禁（阶段 4）：未连接/未归位拒绝并给中文原因（can_start_job）。
        """
        reason = can_start_job(self._current_state(), self._homed)
        if reason:
            self.status_label.setText(reason)
            self._append_console(f"设工件原点被拒：{reason}")
            return
        x, y, _ = self._machine_pos
        if x is None or y is None:
            self.status_label.setText("暂无坐标，先连接并归位")
            return
        self._work_origin = (x, y)
        self.origin_label.setText(f"工件原点：机器({x:.2f},{y:.2f})")
        self._append_console(f"已设工件原点：机器 ({x:.2f},{y:.2f}) = 工件 (0,0)")
        self._on_position(*self._machine_pos)  # 刷新 DRO 为工件坐标
        self._recompile()  # 原点是编译期纯平移：立即重编译，预览随偏移平移

    def _on_clear_origin(self) -> None:
        self._work_origin = None
        self.origin_label.setText("工件原点：机器(0,0)")
        self._on_position(*self._machine_pos)
        self._recompile()

    def _job_origin_xy(self) -> tuple[float, float]:
        """SVG(工件坐标) → 机器坐标的平移量 = 工件原点的机器坐标；无原点 = (0,0)。"""
        if self._work_origin is None:
            return (0.0, 0.0)
        return (self._work_origin[0], self._work_origin[1])

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """方向键 Jog（Shift=10×，长按 autoRepeat 连续）。"""
        if not getattr(self, "_jog_enabled", False) or self.cmd_input.hasFocus():
            return super().keyPressEvent(event)
        key = event.key()
        mult = 10.0 if event.modifiers() & QtCore.Qt.ShiftModifier else 1.0
        step = self._jog_step()
        if step is None:
            return super().keyPressEvent(event)
        d = step * mult
        map2 = {
            QtCore.Qt.Key_Left: ("x", -d),
            QtCore.Qt.Key_Right: ("x", d),
            QtCore.Qt.Key_Up: ("y", d),
            QtCore.Qt.Key_Down: ("y", -d),
            QtCore.Qt.Key_PageUp: ("z", d),
            QtCore.Qt.Key_PageDown: ("z", -d),
        }
        if key not in map2:
            return super().keyPressEvent(event)
        self._jog_axis(*map2[key])
        event.accept()

    def _jog_axis(self, axis: str, delta: float) -> None:
        allow_z = self.allow_z_cb.isChecked()
        if axis == "z" and not allow_z:
            self.status_label.setText("Z 移动需勾选『允许 Z』")
            return
        kwargs = {f"d{axis}": delta}  # x→dx, y→dy, z→dz
        try:
            seq = build_jog_sequence(
                **kwargs,
                allow_z=allow_z,
                pen_down=(self._pen_state == "down"),
                feed_xy=600.0,
                feed_z=300.0,
            )
        except MachineError as exc:
            self.status_label.setText(str(exc))
            return
        self._send(seq)

    # -- 动作 --------------------------------------------------------------

    def _current_state(self) -> str:
        return self._worker._state if self._worker is not None else "DISCONNECTED"

    def _require_ready(self) -> bool:
        if self._worker is None or self._current_state() != "READY":
            self.status_label.setText("未连接或未就绪")
            return False
        return True

    def _send(self, lines: list[str], *, home: bool = False) -> bool:
        """预检（sequence_ok）后把序列投给 worker。返回 True=已发出。

        ``allow_z``/``lift_configured`` 取 UI 真值，与 worker._guard 同源
        （_sync_worker_guard_params 在 _refresh_gate_ui 推平，评审 issue#1）——
        曾硬编码 allow_z=True 导致预检永过、worker 中途拦（break #6 假阳性）。
        """
        if not self._require_ready():
            return False
        if not lines:
            return False
        bad = sequence_ok(
            lines,
            allow_z=self.allow_z_cb.isChecked(),
            lift_configured=self._pen_down_z is not None,
        )
        if bad:
            self._append_console(f"已拦截（安全门）：{bad}")
            return False
        self._worker.reqSendSequence.emit(list(lines), home)
        return True

    def _jog_step(self) -> float | None:
        checked = [b for b in self._step_buttons if b.isChecked()]
        if checked:
            try:
                return float(checked[0].text())
            except ValueError:
                return None
        try:
            return float(self.custom_step.text())
        except ValueError:
            return None

    def _on_jog(self, btn_text: str) -> None:
        step = self._jog_step()
        if step is None:
            self.status_label.setText("步长无效")
            return
        axis_map = {
            "X−": ("x", -step), "X+": ("x", step),
            "Y+": ("y", step), "Y−": ("y", -step),
            "Z↑": ("z", step), "Z↓": ("z", -step),
        }
        if btn_text not in axis_map:
            return
        axis, delta = axis_map[btn_text]
        kwargs: dict = {}
        if axis == "x":
            kwargs["dx"] = delta
        elif axis == "y":
            kwargs["dy"] = delta
        else:
            kwargs["dz"] = delta
        # Z 移动统一需『允许 Z』（对齐 CLI：任何 --z 都要 allow-z）
        allow_z = self.allow_z_cb.isChecked()
        if axis == "z" and not allow_z:
            self.status_label.setText("Z 移动需勾选『允许 Z』")
            return
        try:
            seq = build_jog_sequence(
                **kwargs,
                allow_z=allow_z,
                pen_down=(self._pen_state == "down"),
                feed_xy=600.0,
                feed_z=300.0,
            )
        except MachineError as exc:
            self.status_label.setText(str(exc))
            return
        self._send(seq)

    def _on_home_clicked(self) -> None:
        seq = build_home_sequence("X Y", allow_z=True)
        self._append_console("归位 XY（先抬 Z10，G28 需 ~60s）…")
        self._send(seq, home=True)

    def _on_home_full_clicked(self) -> None:
        """一键寻零：抬 Z+safe_z → G28 XY → G28 Z → 抬 Z+safe_z → Y 回 safe_y。

        内含 ``G28 Z``（guard 必须 allow_z 才放行）—— 未勾『允许 Z』时按钮已禁用，
        这里再兜一层。序列发出即记 ``_pending_home``：只有它 sequenceDone(ok=True)
        才置 homed（ok=False 提示被拦并保持未归位）。
        """
        if not self.allow_z_cb.isChecked():
            self.status_label.setText("一键寻零内含 G28 Z：需勾选『允许 Z』")
            return
        seq = build_full_home_sequence(safe_y=self._safe_y, safe_z=self._safe_z)
        self._append_console(
            "一键寻零：抬 Z → XY 归零 → Z 归零 → 抬 Z → Y 回安全点（约 1–2min）…"
        )
        self._set_busy_ui(True)  # 长序列期间锁定控制
        self._pending_home = True  # 先记后发：同线程直连时 sequenceDone 在 emit 内到达
        if not self._send(seq, home=True):
            self._pending_home = False
            self._set_busy_ui(False)  # 未发出：恢复控制，不记在途归位

    def _on_pen_down(self) -> None:
        if self._pen_down_z is None:
            self.status_label.setText("profile 未标定 pen_down_z，无法落笔")
            return
        self._send(build_pen_down(self._pen_down_z))
        self._set_pen_state("down")

    def _on_pen_up(self) -> None:
        self._send(build_pen_up(self._safe_z))
        self._set_pen_state(None)

    def _on_calibrate_z(self) -> None:
        """打开 Z 触纸标定向导（按当前工具：笔→pen_down_z / 刀→cut_touch_z）。"""
        if self._worker is None or self._current_state() != "READY":
            self.status_label.setText("先连接并归位，再做 Z 标定")
            return
        from megapro.gui.calibration import CalibrateZDialog

        if self._tool == "knife":
            key, name = "cut_touch_z", "裁刀"
        else:
            key, name = "pen_down_z", "笔"
        dlg = CalibrateZDialog(
            self._worker, tool_key=key, tool_name=name,
            safe_z=self._safe_z, profile_path=self.profile_path, parent=self,
        )
        dlg.exec()
        # 回写后重读 profile 缓存
        self._reload_profile_z()
        new_z = self._cut_touch_z if key == "cut_touch_z" else self._pen_down_z
        self._append_console(f"Z 标定结束：{key} = {new_z}")

    def _reload_profile_z(self) -> None:
        self._profile = self._load_profile()
        self._pen_down_z = self._as_float(self._profile.get("pen_down_z"))
        self._cut_touch_z = self._as_float(self._profile.get("cut_touch_z"))
        self._refresh_tool_info()
        self._refresh_gate_ui()  # 标定回写改 lift_configured 真值 → 同步 worker
        self._recompile()  # Z 标定回写 → 换 ZMap 重编译（触发集，§2.2）

    def _set_pen_state(self, state: str | None) -> None:
        self._pen_state = state
        if state == "down":
            self.pen_led.setText("笔态：落笔")
            self.pen_led.setStyleSheet("color:#c00; font-weight:bold")
        else:
            self.pen_led.setText("笔态：抬起/未知")
            self.pen_led.setStyleSheet("color:#080; font-weight:bold")

    def _on_abort(self) -> None:
        # 常规停靠：抬 Z 到 safe_z → 回安全点（绝对）
        seq = build_park_sequence(self._safe_x, self._safe_y, self._safe_z)
        self._append_console("中止/停靠：抬 Z → 回安全点")
        self._send(seq)
        self._set_pen_state(None)

    def _on_preset_selected(self) -> None:
        pass  # 纯选择不动作；加载走按钮

    def _on_preset_save(self) -> None:
        from megapro.gui.presets import collect_config, save_last, save_preset

        name, ok = QtWidgets.QInputDialog.getText(
            self, "保存预设", "预设名：")
        if not ok or not name.strip():
            return
        name = name.strip()
        cfg = collect_config(self)
        try:
            save_preset(name, cfg, profile_path=self.profile_path)
            save_last(name)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._refresh_preset_list()
        self._append_console(f"预设已保存：{name}（含 Z 标定已同步 profile）")

    def _on_preset_load(self) -> None:
        from megapro.gui.presets import load_preset, save_last

        name = self.preset_combo.currentText()
        if not name:
            QtWidgets.QMessageBox.information(self, "加载预设", "先选一个预设")
            return
        try:
            cfg = load_preset(name)
        except FileNotFoundError as exc:
            QtWidgets.QMessageBox.warning(self, "加载失败", str(exc))
            return
        self._apply_preset_cfg(cfg)
        save_last(name)

    def _on_preset_delete(self) -> None:
        from megapro.gui.presets import delete_preset

        name = self.preset_combo.currentText()
        if not name:
            return
        ret = QtWidgets.QMessageBox.question(
            self, "删除预设", f"删除预设「{name}」？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if ret == QtWidgets.QMessageBox.Yes:
            delete_preset(name)
            self._refresh_preset_list()
            self._append_console(f"已删除预设：{name}")

    def _apply_preset_cfg(self, cfg: dict) -> None:
        """应用预设 cfg 到状态/控件并刷新。"""
        from megapro.gui.presets import apply_config

        apply_config(self, cfg)
        self._refresh_preset_list()
        idx = self.preset_combo.findText(cfg.get("_name", ""))
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)

    def _on_estop_clicked(self) -> None:
        if self._worker is None:
            return
        ret = QtWidgets.QMessageBox.question(
            self,
            "急停确认",
            "发送 M112 紧急停止？\n\n机器会立即 halt，需断电重启才能恢复。",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ret == QtWidgets.QMessageBox.Yes:
            self._worker.reqEstop.emit()

    def _on_poll_tick(self) -> None:
        # 仅 READY 轮询 M114。**执行期 M114 轮询明确不做**（阶段 4 契约）：
        # run_job 期间 worker 工作线程被发送循环占住、job 行响应只有 'ok'
        # （不触发 position 信号），此时插 M114 轮询会与 job 行抢 send_line/
        # 串口、打乱行序与 progress 语义 —— 要轮询就得改 run_job 流控，而
        # run_job 流控（pause/abort/进度）是零改动红线。故执行期无任何位置
        # 回报，LiveMarker 如实冻结置灰 + live_hint 标注「执行中不轮询位置」；
        # ok≠已移动、M114 的 Count 不可信（REPORT §6），脱节对照靠停机复核。
        if self._worker is not None and self._current_state() == "READY":
            self._worker.reqPoll.emit()

    def _on_cmd_entered(self) -> None:
        text = self.cmd_input.text().strip()
        if not text:
            return
        self.cmd_input.clear()
        self._send([text])

    # -- 杂项 --------------------------------------------------------------

    def _append_console(self, text: str) -> None:
        self.console.appendPlainText(text)
        sb = self.console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_busy_ui(self, busy: bool) -> None:
        """Busy（连接中/归位/执行）时禁用动轴类控件；E-stop/断开保持可用。"""
        self._busy = bool(busy)
        self._jog_enabled = not busy
        self._refresh_gate_ui()

    #: homed 门禁按钮的基础 tooltip（禁用时拼上中文原因）
    _GATE_TOOLTIPS = {
        "frame": "描内容 bbox 的 G0 矩形环绕（先抬到安全 Z，不下压）——挪纸/夹具有时间对准",
        "check": "抬到安全 Z 后 G0 空移点检内容 bbox 对应点（不下压）",
        "goto": "G0 回到工件原点（设原点后复核落点）",
        "origin": "把当前笔尖位置设为工件 XY 原点（DRO 归零）",
    }

    def _sync_worker_guard_params(self) -> None:
        """把 UI 真值推给 worker 的逐行 guard —— 与 ``_send`` 预检**同源**。

        worker 的 ``_guard`` 读自身 ``allow_z``/``lift_configured`` 字段（连接时
        快照，worker.py 不改其实现），而预检读实时 UI 值；若不同步，连接后切
        『允许 Z』/加载预设/Z 标定回写即两处分叉 → 预检放行而 worker 逐行中途
        拦（评审 break #6 假阳性路径），半截序列还会遗留 G91 模态（jog 收尾
        G90 不再发出）。所有真值变更点（勾选 toggled、_reload_profile_z、
        presets.apply_config→_on_preset_applied）都汇到 _refresh_gate_ui，故
        在此一次性推平（评审 issue#1：两处必须同源）。
        """
        if self._worker is None:
            return
        self._worker.allow_z = self.allow_z_cb.isChecked()
        self._worker.lift_configured = self._pen_down_z is not None

    def _refresh_gate_ui(self) -> None:
        """阶段 4 门禁统一接线（链路 / busy / 允许 Z / homed）：

        - 未连接（_link_ready=False）或忙态（连接中/归位/序列/执行）禁动轴类
          控件并给中文原因 tooltip（E-stop/断开仍可用）；
        - 『一键寻零』内含 ``G28 Z``（guard.py G28-Z 规则必拦无 allow_z 者）
          → 未勾『允许 Z』直接禁用；Z Jog 同理；
        - ``can_start_job`` 拦 未连接/未归位的 执行/走边框/设工件原点（点检/
          回原点同为绝对定位动作，§5 工作流在归位之后，一并门禁），tooltip 带
          中文原因；
        - 末尾把 guard 判据真值同步给 worker（_sync_worker_guard_params，同源）。
        """
        busy = self._busy or self._job_running
        allow_z = self.allow_z_cb.isChecked()
        ready_ok = (not busy) and self._link_ready
        self._jog_enabled = ready_ok
        block_why = "执行/连接中，稍候" if busy else "未连接或未就绪：先连接"
        basic = [self.home_btn, self.pen_down_btn, self.pen_up_btn,
                 self.send_btn, self.cmd_input, self.abort_btn]
        basic += getattr(self, "_jog_btns", [])
        for w in basic:
            w.setEnabled(ready_ok)
            w.setToolTip("" if ready_ok else block_why)
        # Z Jog 另需『允许 Z』（Z 词安全门）
        for w in getattr(self, "_z_jog_btns", []):
            z_ok = ready_ok and allow_z
            w.setEnabled(z_ok)
            w.setToolTip("" if z_ok else
                         (block_why if not ready_ok else "Z 移动需勾选『允许 Z』"))
        # 一键寻零（内含 G28 Z）
        home_ok = ready_ok and allow_z
        self.home_full_btn.setEnabled(home_ok)
        self.home_full_btn.setToolTip(
            "" if home_ok else
            ("一键寻零内含 G28 Z：需勾选『允许 Z』（否则安全门必拦）"
             if self._link_ready and not busy else block_why))
        # homed 门禁：执行/走边框/设工件原点（+点检/回原点）
        reason = can_start_job(self._current_state(), self._homed)
        gate_ok = (not busy) and reason is None
        gated = (
            (self.frame_btn, "frame"), (self.check_bl_btn, "check"),
            (self.check_br_btn, "check"), (self.check_tr_btn, "check"),
            (self.check_tl_btn, "check"), (self.check_center_btn, "check"),
            (self.goto_origin_btn, "goto"), (self.set_origin_btn, "origin"),
        )
        for w, key in gated:
            w.setEnabled(gate_ok)
            base = self._GATE_TOOLTIPS[key]
            w.setToolTip(base if reason is None else f"{base}｜当前不可用：{reason}")
        self.dry_run_btn.setEnabled(not busy and self._job_spec is not None)
        self.dry_run_send_cb.setEnabled(not busy)
        self._update_run_btn()
        self._sync_worker_guard_params()  # 两处判据同源（评审 issue#1）

    # -- P1/P3 文件作业（JobSpec 内存唯一源 + _recompile 唯一汇流） ----------

    def _on_tool_changed(self) -> None:
        """工具切换 = 换 ZMap/emitter 后重编译（**保留几何**，§2.2 触发集）。"""
        self._tool = self.tool_combo.currentData()
        tip = "裁刀" if self._tool == "knife" else "笔"
        self._append_console(f"工具：{tip}")
        self._refresh_tool_info()
        self._recompile()

    def _on_material_changed(self) -> None:
        data = self.material_combo.currentData()
        self._apply_material(data)
        self._recompile()

    def _on_material_params_changed(self) -> None:
        """切深/边距改变（材料参数触发集，§2.2）。"""
        self._material_margin = float(self.margin_spin.value())
        self._recompile()

    def _apply_material(self, data) -> None:
        key, w, h = data
        self._material = key
        self._material_w = w
        self._material_h = h
        if key == "A4":
            self._append_console(
                "A4(210×297)：Y=297 超出机器行程 210 —— 只能切当前装纸可达区域 "
                "（≤210×210−边距）；整张 A4 分区域切不支持。"
            )

    def _on_feed_changed(self) -> None:
        """feed/Z 速度/跳段抬升改变 → 重编译（触发集，§2.2）。"""
        self._feed_xy = float(self.feed_xy_spin.value())
        self._feed_z = float(self.feed_z_spin.value())
        self._travel_lift_mm = float(self.lift_spin.value())
        self._recompile()

    def _on_opt_changed(self) -> None:
        """去重/顺序/笔径改变 → 重编译（**不重读文件**，JobSpec 在内存）。"""
        self._opt_dedup = self.dedup_cb.isChecked()
        self._opt_sort = self.sort_cb.isChecked()
        self._pen_diameter_mm = float(self.pen_diameter_spin.value())
        self._refresh_tool_info()
        self._append_console(
            f"优化：去重{'开' if self._opt_dedup else '关'} / "
            f"顺序{'开' if self._opt_sort else '关'} / "
            f"笔径 {self._pen_diameter_mm:g}mm")
        self._recompile()

    def _on_placement_changed(self) -> None:
        """放置模式切换（preserve/anchor）→ 换 Placement 重编译。"""
        if self._job_spec is None:
            return
        mode = self.placement_combo.currentData()
        if mode == "preserve":
            placement = Placement(mode="preserve")
        else:
            placement = Placement(mode="anchor", anchor="bl", target=(0.0, 0.0))
        self._set_job_and_recompile(
            replace(self._job_spec, placement=placement),
            from_layout=self._job_from_layout)

    def _cut_depth_mm(self) -> float:
        return float(self.cut_depth_spin.value())

    def _on_load_svg(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "载入 SVG", "", "SVG 文件 (*.svg);;所有文件 (*)"
        )
        if path:
            self._load_svg_path(path)

    def _load_svg_path(self, path: str) -> bool:
        """载入 SVG → :class:`JobSpec`（内存唯一源，§2.2/§3.1）。成功 True。

        - 几何走 ``parse_svg`` **原序**（NN 排序只在 compile 内按 opt.sort）；
        - SVG y-down → 纸面 y-up 经 ``coords.paper_from_svg_ydown``（格式
          解码，对合）；纸面/文件双入口已删（根因 #2）。
        - Placement：``parse_svg_meta`` 声明 210×210 → preserve（版面坐标即
          工件坐标）；否则默认 anchor@bl→(0,0)（小画板不再跑床尾，根因 #4）
          —— UI 可切换后重编译。
        - 越界不在此拦截：compile strict=False → 违规红点 + 禁执行（§3.1）。
        """
        from megapro.gui.job import polylines_for_svg
        from megapro.toolchain.svg_to_gcode import parse_svg_meta

        try:
            paths_svg = polylines_for_svg(path, sort=False)
            meta = parse_svg_meta(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "载入失败", f"无法解析 SVG：{exc}")
            return False
        if not paths_svg:
            QtWidgets.QMessageBox.warning(self, "载入失败", "SVG 中没有可绘制的图形。")
            return False
        placement = self._placement_for_meta(meta)
        self._set_placement_ui(placement.mode)
        # 用户自己的文件作业 —— 不是排版的（from_layout=False）
        self._layout_out_of_bed = None  # 文件作业不继承排版页的越界判据（D2-②）
        self._set_job_and_recompile(JobSpec(
            paths_paper=paper_from_svg_ydown(paths_svg),
            source_name=Path(path).name,
            placement=placement,
        ), from_layout=False)
        self._append_console(f"已载入 {path}")
        return True

    @staticmethod
    def _placement_for_meta(meta) -> Placement:
        """Placement 判定：width/height **均**声明 210×210 才 preserve。

        两维都非 None 且各等于 BED_W/BED_H（容差 1e-6）→ 版面坐标即工件
        坐标（preserve）；缺任一维或尺寸不符 → anchor@bl→(0,0)（小画板
        内容归到工件原点，不跑床尾）。
        """
        if (meta.width_mm is not None and meta.height_mm is not None
                and abs(meta.width_mm - BED_W) < 1e-6
                and abs(meta.height_mm - BED_H) < 1e-6):
            return Placement(mode="preserve")
        return Placement(mode="anchor", anchor="bl", target=(0.0, 0.0))

    def _set_placement_ui(self, mode: str) -> None:
        idx = self.placement_combo.findData(mode)
        if idx >= 0 and self.placement_combo.currentIndex() != idx:
            self.placement_combo.blockSignals(True)
            self.placement_combo.setCurrentIndex(idx)
            self.placement_combo.blockSignals(False)

    def _on_preset_applied(self) -> None:
        """预设写入参数字段后（presets.apply_config 尾部调用）：保留几何重编译。

        预设会写 ``_pen_down_z``/``_cut_touch_z``（presets.apply_config）→ 先刷
        门禁把新真值同步给 worker（_sync_worker_guard_params），再重编译；
        即使无作业（_recompile 提前返回）同步也已生效（评审 issue#1）。
        """
        self._refresh_gate_ui()
        self._recompile()

    def _on_layout_export(self, spec) -> None:
        """排版页 JobSpec 直传（阶段 3：无临时文件往返，源版面保持可用）。"""
        if not isinstance(spec, JobSpec):
            self._append_console("排版导出参数无效（非 JobSpec），忽略")
            return
        self._set_placement_ui(spec.placement.mode)
        # ⚠ 排版来源的越床判据**这里也必须填**（对抗性复核 R2 实测）。
        # 原来只有 :meth:`_on_layout_job_sync` 填，而它被「同步作业预览」复选框
        # 把守 —— 用户**关掉**该开关（真控件、tooltip 明写这是受支持的操作）时，
        # 静默同步信号永不发 ⇒ :attr:`_layout_out_of_bed` 整场会话停在 ``None``
        # ⇒ 作业页那句「排版版面越界…」**永不出现**。
        # 实测：开关 OFF → 排版页 ``_out_of_bed()=(40.0, …)``，作业页
        # ``runnable=True violations=()``、job_info 无「排版版面越界」，
        # 机器切 (0,0)-(20,20) 而用户排的是 (-40,10) —— **零提示**。
        # 「送去作业」是同一条排版来源，判据该在**这一条路**上也取一次。
        self._layout_out_of_bed = self._layout_out_of_bed_snapshot()
        self._set_job_and_recompile(spec, from_layout=True)
        self.tabs.setCurrentIndex(0)
        self._append_console("排版已送去作业页 —— 检查预览后点『开始执行』")

    def _set_job_and_recompile(self, spec: JobSpec, *, from_layout: bool) -> bool:
        """「换作业」= 赋 spec + 重编译，**原子**执行；真编译失败则整体回滚。

        缺陷（R8）：四条换作业路径原先都是「先 ``self._job_spec = spec``、
        再 :meth:`_recompile`」，而 :meth:`_recompile` 在参数非法时只打一行控制台
        就 return（切刀且 ``touch_z < depth`` ⇒ :func:`cut_z_for_depth` 抛
        ValueError ⇒ 下压后 Z 为负）。于是**内存唯一源已经是新几何，
        ``_job_lines`` / 预览 / run 门禁却停在旧几何**，按『开始执行』发出去的是
        旧版面——而作业页看上去一切正常。实测::

            spec   = 2 段（含新增的 (50,50)-(150,50)）
            lines  = 10 行旧几何（只有 X0/X10，无 X150），run 仍 enabled、runnable=True
            worker 实收 = 那 10 行旧版面

        两条赋值必须**一起**回滚：只还原 spec 而留下 ``_job_from_layout=True``，
        会让后续一次静默同步把用户自己的文件作业顶掉（R6 的来源门禁靠它）。
        ``_job_lines``/``_job_compiled``/预览在失败路径上根本没被碰过，无需还原。

        **回滚只针对「真编译失败」（D3）**：``_recompile`` 返回四态串，其中
        ``"deferred"``（执行中 no-op）**不是失败** —— 它是仓库既有的
        「本次作业结束后生效」延迟生效契约，:meth:`_on_job_done` 的补编译会
        自然接住。旧实现把它一视同仁地回滚了 ⇒ 用户改的放置**永久蒸发**，
        而下拉框还停在他选的那一项（界面对用户撒谎）。实测：执行中把 combo
        切到「锚点归位」⇒ spec 仍是 preserve，jobDone 补编译后**仍是 preserve**。

        处置按**改动性质**分（ask：读 ``_recompile`` 的返回值语义，别靠猜）：

        ==============  ==================  ==============================
        改动             ``paths_paper``    处置
        ==============  ==================  ==============================
        换作业           变了                **回滚**（spec 新 / 旧 lines = 半提交）
        参数/口径        未变（同一份几何）  **不回滚**，留给 jobDone 补编译
        ==============  ==================  ==============================

        「放置」属第二类：它只是同一份几何的**放置方式**，几何多重集没变 ⇒
        不存在 R8 说的半提交，而它恰是用户最需要「结束后生效」的那类改动。

        **回滚时顺带还原下拉框**（ask 要求 2）：:meth:`_on_placement_changed`
        是在 combo 变动后调本方法的，故 combo 已被用户改成新值；回滚 spec 而
        不动 combo ⇒ 模型与 UI 再次分叉（同一处撒谎，只是换了个方向）。故回滚
        后把 combo 拨回 spec 的实际放置方式（``_set_placement_ui`` 内部已
        blockSignals，不会反过来再触发一次本方法）。

        返回 True = 真的重编译出了新 lines。
        """
        prev_spec, prev_from_layout = self._job_spec, self._job_from_layout
        prev_placement = prev_spec.placement.mode if prev_spec is not None else None
        self._job_spec = spec
        self._job_from_layout = from_layout
        outcome = self._recompile()
        if outcome == "compiled":
            return True
        if outcome == "deferred" and not self._job_geometry_changed(prev_spec, spec):
            # 执行中 + 只是改放置/口径：几何没变 ⇒ 保留新 spec，jobDone 接住。
            return False
        self._job_spec, self._job_from_layout = prev_spec, prev_from_layout
        if prev_placement is not None and self._job_spec is not None:
            # 回滚了模型就得让 UI 跟上，否则下拉框停在被回滚的那一项上。
            self._set_placement_ui(self._job_spec.placement.mode)
        return False

    @staticmethod
    def _job_geometry_changed(prev: JobSpec | None, new: JobSpec) -> bool:
        """换作业了吗（``paths_paper`` 有没有被换掉）。

        判据用**几何多重集**（与切割口径一致）：长度不同、或任一段坐标不同
        ⇒ 换作业。D3 处置分流的依据 —— 几何换了却不回滚就是 R8 的半提交。
        """
        if prev is None:
            return True
        a = [[tuple(pt) for pt in p] for p in prev.paths_paper]
        b = [[tuple(pt) for pt in p] for p in new.paths_paper]
        return a != b

    def _on_layout_job_sync(self, spec) -> None:
        """排版页**静默**同步作业预览（页切换等轻量动作，PRD §10.1-8）。

        与 :meth:`_on_layout_export` 逐行同源（赋值 → placement 投影 →
        ``_recompile``），差别只有两处：**不切标签页、不打控制台** —— 页切换
        是高频动作，把用户踢回控制页并刷屏不可接受。

        ``_recompile`` 的触发集只是**新增**这一个点，既有触发点与语义一律
        未改：这里没有另开编译路径（无作业仍提前返回、参数仍由
        ``_spec_with_current_params`` 投影）。

        **两道前置门禁（B2 修的就是这个 —— 此前本方法无条件
        ``self._job_spec = spec``）**：

        1. **来源门禁**：作业页当前握的**不是**排版作业（:attr:`_job_from_layout`
           为 False，即用户自己载入的 SVG 文件作业或空作业）⇒ **整个方法
           直接返回，一个字段都不碰**。
           缺陷实测：作业页握 ``design.svg``（paths=1、lines=9）时切一下排版页
           页签，作业页即被换成空排版版（paths=0、lines=4），**控制台一声不
           吭**、按钮仍可执行 —— 用户刚载入的文件作业被无声顶掉，且无从察觉。
           排版同步是**给排版自己的**增量刷新，不该抢别人的作业；要刷排版作业
           请点『送去作业』（那条路本来就显式、会切页并刷控制台）。
        2. **执行中门禁**：``_job_running`` 为真 ⇒ 与作业页参数改动**同一模式**
           —— 打既有那句提示后返回，且**先于** ``_job_spec`` 赋值。
           缺陷实测（另一条独立触发路径）：赋值发生在 :meth:`_recompile` 的
           执行期守卫**之前**，于是「内存唯一源」被换掉而 lines/预览不变，
           :meth:`_on_job_done` 的补编译遂编译**切过去那一页** —— 刚跑完的
           作业从作业页彻底消失，下一刀悄悄换成了另一份内容。故这里必须**在
           赋值前**就返回：``_job_spec`` 一旦被换，补编译就会拿它去编译。
           ⚠ 沿用既有提示原文是为了与参数改动那条路**同一口径**（不另发明一套
           措辞）；就本路径而言「结束后生效」的准确含义是：跑完后作业页保留
           **本次实际执行的那份**作业（这正是用户要的预览），用户下次切页即重新
           同步 —— 不会自动跟到切过去的那一页。
        """
        if not isinstance(spec, JobSpec):
            return
        # 门禁 1：作业页握的不是排版作业 ⇒ 完全不动（含控制台，保持静默）
        if not self._job_from_layout:
            return
        # 门禁 2：执行中 ⇒ 照参数改动那条路的模式，先提示、后返回
        if self._job_running:
            self._append_console("执行中，参数改动本次作业结束后生效")
            return
        self._job_from_layout = True
        # ⚠ **placement 归用户所有，静默同步不得重置**（R6）。
        # :meth:`LayoutPage.to_job_spec` 恒返回 ``Placement(mode="preserve")``，
        # 而本方法原先照搬 :meth:`_on_layout_export`（那是**显式**『送去作业』，
        # 重置 placement 合理）的整条语义、含 ``_set_placement_ui`` ⇒ 用户在
        # 作业页亲手选的『锚点归位 bl→(0,0)』会被排版页的**任何一次**编辑
        # （微调/层序/撤销…）无声打回『保持版面坐标』，整刀内容沿 X 平移
        # 54mm、Y 平移 50mm。实测：控制台零提示、run 门禁全绿、
        # ``runnable=True``，机器真会收到偏移后的坐标。
        #
        # 故：把用户当前的 placement **投影回**新 spec（与
        # :meth:`_spec_with_current_params` 对其余参数的做法一致），combo
        # 一律不碰。只有来源切换（送去作业 / 载入文件）才重置 placement 下拉。
        spec = replace(spec, placement=self._job_spec.placement)
        # **D2-②：先记下排版页的越床判据，再编译**（必须早于
        # ``_set_job_and_recompile``：它会走 :meth:`_update_job_info`，
        # 而告知文案要读这个字段）。判据本身在排版页算好（几何真源在那儿），
        # 这里只取快照 —— 见 :attr:`_layout_out_of_bed`。
        self._layout_out_of_bed = self._layout_out_of_bed_snapshot()
        self._set_job_and_recompile(spec, from_layout=True)

    def _layout_out_of_bed_snapshot(self):
        """取排版页当前的越床判据（``_out_of_bed()`` 的值或 ``None``）。

        排版页可能已被销毁（析构期同步，见
        :meth:`LayoutPage._on_undo_index_changed` 的存活守卫），故整段
        ``getattr`` 兜住；取不到就当「没有越界信息」——**宁可少显示，
        不可凭空报错**（这只是一段告知文案，不参与任何拦截）。
        """
        page = getattr(self, "layout_page", None)
        getter = getattr(page, "_out_of_bed", None)
        if getter is None:
            return None
        try:
            return getter()
        except (RuntimeError, AttributeError):
            return None

    def _on_clear_job(self) -> None:
        self._job_from_layout = False   # 作业没了 ⇒ 无来源可言
        self._layout_out_of_bed = None  # 同理：排版越界判据不再适用（D2-②）
        self._job_spec = None
        self._job_lines = []
        self._job_compiled = None
        self._render_preview(None)
        self.job_info.setText("未载入文件")
        self._refresh_gate_ui()
        self.progress_bar.setValue(0)
        self.progress_label.setText("")

    # -- 对准工具（§4.1-3 / §5-④：走边框/四角·中心点检/回原点/空跑校验） -------

    @staticmethod
    def _clamp_rounds(rounds) -> int:
        """Frame 轮数归一：**<1 一律归 1**（UI 边界收口）。

        注：``controller.build_frame_sequence`` 对 rounds<1 仍抛 MachineError
        （行为被 tests/test_gui_job.py:464-465 钉死，该文件在本阶段不动清单），
        故归一放在轮数进入构造器之前的 UI 边界；1/3 轮可选（§4.1）。
        """
        try:
            r = int(rounds)
        except (TypeError, ValueError):
            return 1
        return 1 if r < 1 else r

    def _content_bbox(self) -> tuple[float, float, float, float] | None:
        """当前作业内容 bbox（机器坐标；已含放置/工件原点平移）。无内容 → None。"""
        if self._job_compiled is None:
            return None
        paths = self._job_compiled.meta.get("paths") or []
        return bbox_of(paths)

    def _gate_alignment(self, action: str) -> bool:
        """对准动作的 homed 门禁：未连接/未归位拒绝并给中文原因。"""
        reason = can_start_job(self._current_state(), self._homed)
        if reason:
            self.status_label.setText(reason)
            self._append_console(f"{action}被拒：{reason}")
            return False
        return True

    def _travel_problem(self, bb) -> str | None:
        """bbox 行程域校验：超出机器 (0,0)-(BED_W,BED_H) → 中文原因（评审 issue#3）。

        超行程的绝对 G0 会被固件软限位**静默钳位**（AGENTS.md 硬规则），描出的
        框/点检位与真实 bbox 不符 → 后续设工件原点/铺纸对准错位，故直接拒绝
        而不是发出去换一个假框。
        """
        x0, y0, x1, y1 = bb
        if x0 < -1e-9 or y0 < -1e-9 or x1 > BED_W + 1e-9 or y1 > BED_H + 1e-9:
            return (f"bbox ({x0:.1f},{y0:.1f})-({x1:.1f},{y1:.1f}) 超出机器行程 "
                    f"0–{BED_W:g}×0–{BED_H:g}：绝对移动会被软限位静默钳位，"
                    "描框/点检结果不可信")
        return None

    def _on_frame(self) -> None:
        """走边框 Frame：描内容 bbox 的 G0 矩形环绕（1/3 轮可选，空移不下压）。"""
        if not self._gate_alignment("走边框"):
            return
        bb = self._content_bbox()
        if bb is None:
            self.status_label.setText("未载入内容，无法走边框")
            self._append_console("走边框：未载入内容（先载入 SVG/排版导出）。")
            return
        problem = self._travel_problem(bb)
        if problem:
            self.status_label.setText("bbox 超出机器行程，禁走边框")
            self._append_console(f"走边框被拒：{problem}")
            return
        self._warn_alignment_violations("走边框")
        rounds = self._clamp_rounds(self.frame_rounds_combo.currentData())
        try:
            seq = build_frame_sequence(bb, self._safe_z, self._feed_xy, rounds=rounds)
        except MachineError as exc:
            self.status_label.setText(str(exc))
            return
        self._append_console(
            f"走边框：bbox ({bb[0]:.1f},{bb[1]:.1f})-({bb[2]:.1f},{bb[3]:.1f}) "
            f"×{rounds} 轮（G0 空移环绕，不下压）…")
        self._send(seq)

    def _warn_alignment_violations(self, action: str) -> None:
        """内容越判定区（材料/边距/笔径）时提示，但不拦描框/点检（评审 issue#3）。

        理由：走边框/点检正是「内容放不放得下」的诊断手段，若因 violations 直接
        禁用就失去了发现工具；真正的静默损坏面是**超行程**（软限位钳位 → 框失真），
        已由 _travel_problem 硬拦。violations 仅在行程内时提示照走（执行仍绑
        runnable 禁）。
        """
        compiled = self._job_compiled
        if compiled is not None and compiled.bounds.violations:
            self._append_console(
                f"{action}提示：内容越判定区 {len(compiled.bounds.violations)} 处"
                f"（{'；'.join(compiled.bounds.messages[:3])}…）——描框照走供核对，"
                "但该作业不可执行（执行按钮已禁）。")

    def _on_check_point(self, corner: str) -> None:
        """四角/中心点检：G0 点检内容 bbox 的角点/中心（build_move_to_sequence）。"""
        name = {"bl": "左下", "br": "右下", "tr": "右上", "tl": "左上", "c": "中心"}.get(corner, corner)
        if not self._gate_alignment(f"{name}点检"):
            return
        bb = self._content_bbox()
        if bb is None:
            self.status_label.setText("未载入内容，无法点检")
            return
        problem = self._travel_problem(bb)
        if problem:
            self.status_label.setText("bbox 超出机器行程，禁点检")
            self._append_console(f"{name}点检被拒：{problem}")
            return
        self._warn_alignment_violations(f"{name}点检")
        x0, y0, x1, y1 = bb
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        pt = {
            "bl": (x0, y0), "br": (x1, y0), "tr": (x1, y1), "tl": (x0, y1), "c": (cx, cy),
        }[corner]
        try:
            seq = build_move_to_sequence(pt[0], pt[1], self._safe_z, self._feed_xy)
        except MachineError as exc:  # 如 profile safe_z<0（评审 low #5：兜住不抛穿）
            self.status_label.setText(str(exc))
            self._append_console(f"{name}点检被拒：{exc}")
            return
        self._append_console(f"{name}点检：G0 到 ({pt[0]:.1f},{pt[1]:.1f})（不下压）…")
        self._send(seq)

    def _on_goto_origin(self) -> None:
        """回原点：G0 回工件原点复核（build_goto_origin_sequence）。"""
        if not self._gate_alignment("回原点"):
            return
        try:
            seq = build_goto_origin_sequence(self._work_origin, self._safe_z)
        except MachineError as exc:
            self.status_label.setText(str(exc))
            self._append_console(f"回原点被拒：{exc}")
            return
        self._append_console("回原点：G0 到工件原点（不下压）…")
        self._send(seq)

    def _on_dry_run(self) -> None:
        """空跑校验：``compile_job(dry_run=True)``（Z 恒=安全 Z、全程不落笔/不下压）。

        空跑 ZMap 为 (safe_z, down_z=safe_z) → ``parse_lines`` 的 engaged=False，
        段全部按 TRAVEL/SETUP 对待（无 DRAW/PLUNGE）。可选发送（『空跑发送』
        勾选）：把这份 lines 发机器物理走一遍；发送同执行的危险面，先过 homed
        门禁 + bad_z/越界拦截（评审 issue#2/#3），再逐行 guard。
        """
        if self._job_spec is None:
            self.status_label.setText("未载入内容，无法空跑")
            return
        try:
            compiled = compile_job(self._spec_with_current_params(),
                                   strict=False, dry_run=True)
        except ValueError as exc:
            self._append_console(f"空跑编译失败（参数）：{exc}")
            return
        self._dry_run_compiled = compiled
        if not compiled.lines:
            self._append_console("空跑校验：Z 未标定，无可空跑的 G-code（先做 Z 标定）。")
            return
        kinds = sorted({s.kind for s in compiled.segments})
        bad_z = [
            l for l in compiled.lines
            if self._z_value(l) is not None and self._z_value(l) < self._safe_z - 1e-6
        ]
        self._append_console(
            f"空跑校验：{len(compiled.lines)} 行；Z 恒 {self._safe_z:g}（=安全 Z）；"
            f"无落笔/下压（段={'+'.join(kinds)}，全部按 TRAVEL/SETUP 对待）；"
            f"低于安全 Z 的行：{bad_z or '无'}")
        if not self.dry_run_send_cb.isChecked():
            # 只编译=检查工具本体，报告即可（bad_z/violations 照报不拦，供诊断）
            self._append_console("空跑仅编译未发送（勾选『空跑发送』可发机器走一遍）。")
            return
        # 物理空跑 = 真实的绝对坐标走位（串口打开即复位、坐标清零、软限位静默
        # 钳位），与执行同危险面 → 发送前逐项拦截（评审 issue#2/#3）：
        # ① homed 门禁（can_start_job）；② bad_z 非空拒发；③ 越界（violations）
        # 拒发（strict=False 编译保留了越界几何，发出去就是越界 G0）。
        if bad_z:
            self.status_label.setText("空跑存在低于安全 Z 的行，禁发送")
            self._append_console(f"空跑发送被拒：{len(bad_z)} 行低于安全 Z（{bad_z[:3]}）")
            return
        if compiled.bounds.violations:
            self.status_label.setText("空跑内容越界，禁发送")
            self._append_console(
                "空跑发送被拒：内容越界 "
                + "；".join(compiled.bounds.messages[:3]))
            return
        if not self._gate_alignment("空跑发送"):
            return
        self._append_console("空跑发送：把空跑 G-code 发给机器物理走一遍（全程不落笔）…")
        self._send(list(compiled.lines))

    @staticmethod
    def _z_value(line: str) -> float | None:
        """取一行 G-code 的 Z 词数值（无 Z 词 → None）。仅空跑断言用。"""
        code = line.split(";", 1)[0]
        m = re.search(r"Z([+-]?\d*\.?\d*)", code)
        if not m or m.group(1) in ("",):
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None



    def _recompile(self) -> str:
        """唯一重编译汇流：JobSpec(内存唯一源) → compile_job → lines/预览。

        触发集：设/清工件原点、feed/Z 速度/跳段、去重/顺序/笔径、材料/切深/
        边距、工具切换（保留几何、换 ZMap）、Z 标定回写、预设应用、载入/排版
        导出。**执行中 no-op + 提示**，jobDone 后补编译（§2.2）。
        预览吃 compile 内 ``parse_lines(将发送的同一份 lines)`` 的 segments，
        worker 发同一份 lines —— 单一真源，无第二数据源。

        **返回四态字符串**（R8 补 D3）：早退/失败的原因**不同，处置也不同**，
        混成一个 ``False`` 会让调用方把「延迟生效」误当「编译失败」而回滚。

        - ``"compiled"``：真出了新 lines。
        - ``"deferred"``：**执行中** no-op（本次作业结束后生效，:meth:`_on_job_done`
          补编译接住）。**不是失败**——旧实现回滚它，用户改的放置永久蒸发
          而下拉框还停在他选的那一项（D3）。
        - ``"no-job"``：压根没有作业可编（``_job_spec is None``）。
        - ``"failed"``：参数非法（Z 断言/域错误）⇒ 上一份 lines/预览保持不变。

        ⚠ **触发集与各路径的行为一字未改**（执行中仍 no-op + 同一句提示、
        失败仍保留上一份 lines）。只是把返回值从 ``bool`` 换成能区分原因的
        四态串，让 :meth:`_set_job_and_recompile` 能正确决定回滚与否。
        """
        if self._job_running:
            self._append_console("执行中，参数改动本次作业结束后生效")
            return "deferred"
        if self._job_spec is None:
            return "no-job"
        try:
            # _spec_with_current_params 在 try 内：_current_zmap 的算术面
            # （cut_z_for_depth：touch_z < depth → 负 Z，§6 编译期拒绝）抛
            # ValueError 时同样走「编译失败（参数）」受控行为，不得抛穿 Qt 槽。
            spec = self._spec_with_current_params()
            compiled = compile_job(spec, strict=False)
        except ValueError as exc:  # Z 断言/参数域错误：保持上一份 lines+预览
            self._append_console(f"编译失败（参数）：{exc}")
            return "failed"
        self._job_compiled = compiled
        self._job_spec = spec  # 参数投影同步回 spec（内存唯一源）
        self._job_lines = list(compiled.lines)
        self._render_preview(compiled)
        self._update_job_info(spec, compiled)
        self._refresh_gate_ui()  # 含 _update_run_btn + 对准/空跑按钮使能
        if compiled.bounds.violations:
            self._append_console("越界：" + "；".join(compiled.bounds.messages))
        return "compiled"

    def _spec_with_current_params(self) -> JobSpec:
        """字段（= JobSpec 参数投影，presets 依赖这些属性名）→ 当前参数的 spec。"""
        return replace(
            self._job_spec,
            tool=self._tool,
            opt=OptParams(dedup=self._opt_dedup, sort=self._opt_sort,
                          pen_diameter_mm=self._pen_diameter_mm),
            motion=MotionParams(feed_xy=self._feed_xy, feed_z=self._feed_z,
                                travel_lift_mm=self._travel_lift_mm),
            zmap=self._current_zmap(),
            material=Material(self._material_w, self._material_h,
                              self._material_margin),
            work_origin=self._work_origin,
        )

    def _current_zmap(self) -> ZMap | None:
        """按当前工具/标定给 ZMap；未标定 → None（编译后 runnable=False 禁执行）。"""
        if self._tool == "knife":
            if self._cut_touch_z is None:
                return None
            return ZMap(self._safe_z,
                        cut_z_for_depth(self._cut_touch_z, self._cut_depth_mm()))
        if self._pen_down_z is None:
            return None
        return ZMap(self._safe_z, self._pen_down_z)

    def _update_job_info(self, spec: JobSpec, compiled) -> None:
        paths = compiled.meta.get("paths") or []
        bb = bbox_of(paths)
        if spec.placement.mode == "preserve":
            mode = "保持版面坐标"
        else:
            tx, ty = spec.placement.target
            mode = f"锚点{spec.placement.anchor}→({tx:g},{ty:g})"
        bb_txt = (f"bbox ({bb[0]:.0f},{bb[1]:.0f})-({bb[2]:.0f},{bb[3]:.0f})"
                  if bb else "bbox 空")
        flags = ""
        if compiled.bounds.violations:
            flags += f"｜越界{len(compiled.bounds.violations)}处(禁执行)"
        flags += self._layout_bounds_notice(bb, compiled, spec)
        if not compiled.meta.get("calibrated"):
            flags += "｜Z 未标定(禁执行)"
        text = (f"{spec.source_name or '未载入'}：{len(paths)} 段，"
                f"{len(compiled.lines)} 行｜{mode}｜{bb_txt}{flags}")
        self.job_info.setText(text)
        # tooltip 兜底整串：wordWrap 解决「窗口够宽时看得全」，但窗口再窄
        # 折行也可能在提示行数内放不下（对抗性复核 R3：1000px 时即便开了
        # wordWrap，尾句仍可能被挤掉）。tooltip 让**任何**窗口宽度下都能
        # 读到完整的「超出多少 / 挪到哪 / 照此执行」。
        self.job_info.setToolTip(text)

    def _layout_bounds_notice(self, bb, compiled, spec) -> str:
        """**版面越界**时的告知文案（D2-②）；不越界返回空串。

        缺口：``compiled.bounds`` 判的是**放置之后**的几何（这是对的 ——
        机器切的就是那个）。于是两种情形作业页都会显得「干净」：

        ① anchor 把越界件整体拉回床内 ⇒ ``violations`` 为空、``runnable=True``，
           而**用户排版时看到的东西越界、机器实际切的位置已经变了**；
        ② preserve 原样保留越界件 ⇒ ``violations`` 非空，但那一行只说
           「越界1处(禁执行)」，**没说这份版面本身就排在床外**。

        **「有没有做归位」按 ``spec.placement.mode`` 判，不按 violations 判**
        （Q3）：``place_at_anchor``（coords.py:145）只要 mode==anchor 就
        **无条件**平移，哪怕平移后仍超床。故「归位做了但落点仍超床」必须
        说「已移到 …但落点仍超床」，说成「未做放置归位」是不实陈述。

        **为什么只告知不拦截**：落点**合法**时禁执行等于砸掉「锚点归位把
        大版面挪进床内」这个正当功能（用户主动要的）。落点**也**越界时
        ``compiled.bounds.violations`` → ``runnable=False`` 本来就挡住了
        （``_update_run_btn`` / ``_on_run_job``）。

        只在作业来源是排版时有意义（非排版作业没有「排版页判据」这回事）。

        ⚠ **判据在这里现取，不读缓存**（对抗性复核 R2）。缓存
        :attr:`_layout_out_of_bed` 只在「静默同步」这一条路上被赋值，而那条路
        被「同步作业预览」复选框把守 ⇒ 用户一关该开关，整场会话判据停在
        ``None``，作业页对「版面越界」**零提示**（机器切在 anchor 搬过的落点，
        用户排版的原位置没人告诉他变了）。
        更糟的变体：开关先开（判据取到 40.0mm）→ 关掉 → 再把图元拖到
        -100mm ⇒ 作业页仍**自信地报 40.0mm**（少报 60mm），比不报更具误导性。
        故每次要显示时**回头问排版页**（版面几何的真源在那儿），缓存只当
        「没有排版页时的兜底」。
        """
        over = None
        if self._job_from_layout:
            over = self._layout_out_of_bed_snapshot() or self._layout_out_of_bed
        if not over:
            return ""
        amount, names = over
        head = f"｜⚠排版版面越界{len(names)}处(最大超出 {amount:.1f}mm)"
        # ⚠ **判据是「有没有做放置归位」，不是「落点还越不越界」**（Q3）。
        # ``place_at_anchor``（coords.py:145）只要 mode==anchor 就**无条件**
        # 平移，哪怕平移后仍然超床（300mm 宽的方块挪到 (0,0) 后右边缘仍在
        # 250mm > 210mm；笔径把落点撑出床同理）。此时 ``violations`` 非空，
        # 但几何**确实被搬了** ⇒ 说「未做放置归位」是不实陈述：画布
        # (-50,0)-(250,20) 与机器 (0,0)-(300,20) 差 50mm，而界面否认发生过
        # 任何移动。正确判据 = ``spec.placement.mode``（用户选了什么就说什么）。
        if spec.placement.mode == "preserve":
            # 原样保留：只补「版面本身就排在床外」这层事实（禁执行已由上面那截负责）
            return head + "，未做放置归位"
        if bb:
            # 做了归位但落点**仍**超床 ⇒ 必须同时说清「搬了」和「仍越界」
            # （禁执行那截已说越界，这里不重复，只给落点）。
            tail = ("，但落点仍超床(禁执行)" if compiled.bounds.violations
                    else "，按当前放置执行")
            return (head + f"，落点已移到 bbox ({bb[0]:.0f},{bb[1]:.0f})"
                          f"-({bb[2]:.0f},{bb[3]:.0f})" + tail)
        return head + "，按当前放置执行"

    def _render_preview(self, compiled) -> None:
        """重画预览覆盖层：走线三色 + 内容 bbox/锚点 + 违规红点（快照外清掉）。

        床框/原点十字/网格/材料三框 = PaperScene.drawBackground；标尺 =
        RulerWidget；LiveMarker 常驻（清作业不清它，M114 十字独立于作业）。
        """
        self._scene.set_material(self._material_w, self._material_h,
                                self._material_margin)
        for it in self._overlay_items:
            self._scene.removeItem(it)
        self._overlay_items = []
        self._gcode_item = None
        if compiled is None:
            return
        # 走线：吃 compiled.segments（= parse_lines(将发送的同一份 lines)）
        self._gcode_item = GcodePathItem(
            compiled.segments, compiled.meta.get("tool", self._tool))
        self._scene.addItem(self._gcode_item)
        self._overlay_items.append(self._gcode_item)
        # 内容 bbox + 放置锚点（机器坐标；场景 ≡ 纸面 ≡ 机器，恒等）
        paths = compiled.meta.get("paths") or []
        bb = bbox_of(paths)
        if bb is not None:
            rect = self._scene.addRect(
                QtCore.QRectF(QtCore.QPointF(bb[0], bb[1]),
                              QtCore.QPointF(bb[2], bb[3])),
                QtGui.QPen(QtGui.QColor("#66a")))
            self._overlay_items.append(rect)
            anchor = ("bl" if getattr(self._job_spec, "placement") is None
                      or self._job_spec.placement.mode == "preserve"
                      else self._job_spec.placement.anchor)
            ax, ay = anchor_point(bb, anchor)
            mk = self._scene.addLine(ax - 2.0, ay, ax + 2.0, ay,
                                     QtGui.QPen(QtGui.QColor("#66a")))
            mk2 = self._scene.addLine(ax, ay - 2.0, ax, ay + 2.0,
                                      QtGui.QPen(QtGui.QColor("#66a")))
            self._overlay_items += [mk, mk2]
        # 违规红点（与 bounds 报告同源）
        for v in compiled.bounds.violations:
            dot = self._scene.addEllipse(
                QtCore.QRectF(v.point[0] - 1.0, v.point[1] - 1.0, 2.0, 2.0),
                QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(QtGui.QColor("#c00")))
            self._overlay_items.append(dot)
        self.preview.fit()

    def _preview_draw_lines(self) -> list:
        """预览落笔路径（DRAW 段 2 点线）—— 与 ``parse_lines(_job_lines)`` 同源。"""
        return list(self._gcode_item.draw_lines) if self._gcode_item is not None else []

    def _empty_job_reason(self) -> str | None:
        """当前作业**没有任何几何**时的中文禁执行原因；不是空作业则 None。

        「空作业」= 编译结果 ``meta["paths"]`` 为空。实测空作业
        ``runnable=True``、``lines`` 有 4 行（G90/G21/G0/M400 等头尾指令），
        于是按钮可点、点了把「一个什么都不切的作业」发给 worker，控制台还打
        「开始执行（4 行）…」—— 用户以为机器动过了，其实一个落笔点都没有。
        在切纸机上这是最不该发生的一类静默：空跑一趟、时间白花，而界面上
        一切正常。

        判据用**几何**而不是 ``lines`` 行数：``lines`` 非空只说明有 G90/G21
        这类抬头，与「有没有要切的东西」无关。
        """
        compiled = self._job_compiled
        if compiled is None:
            return None
        if compiled.meta.get("paths"):
            return None
        return "作业里没有图形：先在排版页画点东西，或载入一个 SVG 文件"

    def _update_run_btn(self) -> None:
        """执行按钮绑 CompiledJob.runnable **且** homed 门禁（阶段 4）。

        越界/未标定 → 禁执行（§3.1）；未连接/未归位 → 同样禁（can_start_job），
        tooltip/状态栏给中文原因。**空作业**（0 条几何）也禁，理由见
        :meth:`_empty_job_reason`（D5-④：修前它可点且点了静默空跑）。
        """
        reason = can_start_job(self._current_state(), self._homed)
        if reason is None:
            reason = self._empty_job_reason()
        ok = (not self._job_running and self._job_compiled is not None
              and self._job_compiled.runnable and bool(self._job_lines)
              and reason is None)
        self.run_btn.setEnabled(ok)
        self.run_btn.setToolTip(reason or "把当前作业发给机器执行")

    def _on_run_job(self) -> None:
        if self._job_running:
            return
        # homed 门禁（阶段 4）：未连接/未归位禁执行，给中文原因（can_start_job）
        reason = can_start_job(self._current_state(), self._homed)
        if reason:
            self.status_label.setText(reason)
            self._append_console(f"禁执行：{reason}")
            self._refresh_gate_ui()
            return
        compiled = self._job_compiled
        if compiled is None or not compiled.lines:
            self.status_label.setText("未载入作业")
            return
        # 空作业兜底（与 :meth:`_update_run_btn` 同判据）：按钮态可能陈旧
        # （作业刚被换成空的、还没跑过 _refresh_gate_ui），而 ``runnable``
        # 对空作业是 True ⇒ 光靠它拦不住。
        empty = self._empty_job_reason()
        if empty:
            self.status_label.setText(empty)
            self._append_console(f"禁执行：{empty}")
            self._refresh_gate_ui()
            return
        if not compiled.runnable:  # 越界/未标定 → 禁执行（绑 runnable）
            msgs = compiled.bounds.messages or ["Z 未标定，禁执行"]
            self.status_label.setText("越界/未标定，禁执行")
            self._append_console("禁执行：" + "；".join(msgs))
            return
        self._job_running = True
        # 不可变快照：进度高亮与 worker 收到的 lines 拷贝同源（§2.2）
        self._running_compiled = compiled
        self._set_job_ui_running(True)
        # 运行中 LiveMarker 诚实化（阶段 4-③）：置灰冻结 + 文字标注，
        # 不做任何位置推算（ok≠已移动；M114 的 Count 不可信）。
        self._live_marker.set_stale(True)
        self.live_hint.setText("执行中不轮询位置（ok≠已移动；Count 不可信）")
        self.live_hint.setStyleSheet("color:#c00; font-weight:bold")
        # 明确不做：执行期 M114 轮询。原因——worker 的 run_job 发送/暂停/中止
        # 流控（_job_pause/_job_abort 行间检查）全在工作线程发送循环里，另插
        # M114 轮询会与 job 行抢 send_line/串口并打乱行序与进度语义；动它就要
        # 改 run_job 流控（阶段 4 契约明确不动）。故 LiveMarker 如实冻结置灰，
        # 脱节对照靠停机读 M114/人工复核（REPORT §6）。
        self.progress_bar.setValue(0)
        self._append_console(
            f"开始执行（{len(compiled.lines)} 行）… 注意：全程看着机器"
            "（执行期不轮询 M114，LiveMarker 冻结在执行前位置；ok≠已移动，"
            "脱节对照请停机读 M114/人工复核）")
        self._worker.reqRunJob.emit(list(compiled.lines))

    def _set_job_ui_running(self, running: bool) -> None:
        self._update_run_btn()
        self.pause_btn.setEnabled(running)
        self.resume_btn.setEnabled(False)
        self.job_abort_btn.setEnabled(running)
        self.load_btn.setEnabled(not running)
        self.tool_combo.setEnabled(not running)
        self._set_busy_ui(running)

    def _on_pause_job(self) -> None:
        if self._worker is not None:
            self._worker.pause_job()
            self.pause_btn.setEnabled(False)
            self.resume_btn.setEnabled(True)
            self._append_console("暂停请求：当前段完成后停下")

    def _on_resume_job(self) -> None:
        if self._worker is not None:
            self._worker.resume_job()
            self.pause_btn.setEnabled(True)
            self.resume_btn.setEnabled(False)
            self._append_console("继续")

    def _on_job_abort(self) -> None:
        """中止作业：请求 worker 停喂行；停靠(抬Z回安全点)在 jobDone 后执行。"""
        if self._worker is None:
            return
        self._worker.abort_job()
        self._append_console("中止请求：停止喂行（当前段完成后抬 Z 回安全点）")

    def _on_job_progress(self, i: int, total: int) -> None:
        self.progress_bar.setValue(int(i / max(total, 1) * 100))
        self.progress_label.setText(f"{i}/{total}")
        # 进度高亮用执行开始时的不可变快照（与 worker 收到的拷贝同源）
        if self._gcode_item is not None:
            segs = (self._running_compiled.segments
                    if self._running_compiled is not None else None)
            self._gcode_item.set_progress(i, segs)

    def _on_job_done(self, ok: bool) -> None:
        self._job_running = False
        self._running_compiled = None
        self._live_marker.set_stale(False)  # 执行结束：M114 轮询恢复后即有回报
        self.live_hint.setText("实机十字 = M114 逻辑值（ok≠已移动；Count 不可信）")
        self.live_hint.setStyleSheet("color:#888")
        self._set_job_ui_running(False)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.job_abort_btn.setEnabled(False)
        self._recompile()  # 执行期 no-op 累积的参数改动：完成后补编译
        if ok:
            self.progress_bar.setValue(100)
            self._append_console("作业完成。")
            self._set_pen_state(None)
        else:
            self._append_console("作业中止/出错：抬 Z 回安全点…（状态请人工复核）")
            # 此刻 worker 已回 READY（jobDone 在其后发），可正常走 _send
            from megapro.gui.controller import build_park_sequence

            self._send(build_park_sequence(self._safe_x, self._safe_y, self._safe_z))
            self._set_pen_state(None)

    def _open_log_dir(self) -> None:
        logs = Path("logs")
        logs.mkdir(exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(logs.resolve())))
