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

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from megapro.gui.controller import (
    MachineError,
    MachineState,
    build_full_home_sequence,
    build_home_sequence,
    build_jog_sequence,
    build_park_sequence,
    build_pen_down,
    build_pen_up,
    sequence_ok,
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
        self._allow_z = bool(self._profile.get("pen_down_z"))  # 有标定才允许负 Z
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
        self._material_w = 210.0
        self._material_h = 210.0
        self._material_margin = 5.0
        self._job_paths: list = []  # 当前载入的 polylines（工件坐标）
        self._job_lines: list = []
        self._job_running = False
        self._feed_xy = 1200.0  # 作业 XY 进给率 mm/min
        self._feed_z = 300.0  # 作业 Z 进给率 mm/min
        self._travel_lift_mm = 5.0  # 段间跳段抬离量（纸面上方）
        self._pen_diameter_mm = 0.5  # 笔尖直径（去重容差 + 边界补偿）
        self._opt_dedup = False  # 重叠去重
        self._opt_sort = True  # 轨迹顺序优化
        # P1c 工件原点（GUI 维护 XY 偏移，机器坐标原点；Z 恒为机器绝对）
        self._work_origin: tuple[float, float] | None = None
        self._machine_pos: tuple[float | None, float | None, float | None] = (None, None, None)

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
        self.layout_page.status_message.connect(self._append_console)
        self.tabs.addTab(self.layout_page, "排版 / 制作")
        root.addWidget(self.tabs, 1)

        root.addLayout(self._build_estop_row())

        # E-stop 快捷键：Space 或 Esc（当命令行未聚焦时）
        for key in (QtGui.QKeySequence("Space"), QtGui.QKeySequence("Esc")):
            sc = QtGui.QShortcut(key, self)
            sc.activated.connect(self._on_estop_clicked)

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
        row.addStretch(1)
        lay.addLayout(row)

        # 材料（裁纸用：纸张尺寸 / 下压深度 / 安全边距；A4 超程警示）
        mat = QtWidgets.QHBoxLayout()
        mat.addWidget(QtWidgets.QLabel("材料："))
        self.material_combo = QtWidgets.QComboBox()
        for label, key, w_, h_ in (
            ("A4 (210×297，超行程!)", "A4", 210.0, 297.0),
            ("A5 (148×210)", "A5", 148.0, 210.0),
            ("A6 (105×148)", "A6", 105.0, 148.0),
            ("自定义 ≤210", "custom", 210.0, 210.0),
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
        mat.addWidget(self.cut_depth_spin)
        mat.addWidget(QtWidgets.QLabel("边距："))
        self.margin_spin = QtWidgets.QDoubleSpinBox()
        self.margin_spin.setRange(0.0, 20.0)
        self.margin_spin.setValue(self._material_margin)
        self.margin_spin.setSuffix(" mm")
        mat.addWidget(self.margin_spin)
        mat.addStretch(1)
        self.material_row = mat
        lay.addLayout(mat)

        # 材料（裁纸）/ 信息行
        info = QtWidgets.QHBoxLayout()
        self.job_info = QtWidgets.QLabel("未载入文件")
        self.job_info.setStyleSheet("color:#666")
        info.addWidget(self.job_info, 1)
        lay.addLayout(info)

        # 预览
        self.preview = QtWidgets.QGraphicsView()
        self.preview.setMinimumHeight(120)
        self._scene = QtWidgets.QGraphicsScene(self)
        self.preview.setScene(self._scene)
        lay.addWidget(self.preview, 1)

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

    def _on_sequence_done(self) -> None:
        """长序列（如一键寻零）发送完成：恢复控制、笔态置抬起。"""
        self._set_busy_ui(False)
        self._set_pen_state(None)  # 寻零结束在安全点，笔抬起
        if self._current_state() == "READY":
            self._append_console("序列完成。")

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

    # -- 工件原点（P1c，GUI 维护 XY 偏移） ---------------------------------

    def _on_set_origin(self) -> None:
        """把当前机器 XY 设为工件原点：记录偏移，DRO XY 归零。"""
        x, y, _ = self._machine_pos
        if x is None or y is None:
            self.status_label.setText("暂无坐标，先连接并归位")
            return
        if not self._require_ready():
            return
        self._work_origin = (x, y)
        self.origin_label.setText(f"工件原点：机器({x:.2f},{y:.2f})")
        self._append_console(f"已设工件原点：机器 ({x:.2f},{y:.2f}) = 工件 (0,0)")
        self._on_position(*self._machine_pos)  # 刷新 DRO 为工件坐标

    def _on_clear_origin(self) -> None:
        self._work_origin = None
        self.origin_label.setText("工件原点：机器(0,0)")
        self._on_position(*self._machine_pos)

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

    def _send(self, lines: list[str], *, home: bool = False) -> None:
        if not self._require_ready():
            return
        if not lines:
            return
        bad = sequence_ok(lines, allow_z=True, lift_configured=self._pen_down_z is not None)
        if bad:
            self._append_console(f"已拦截（安全门）：{bad}")
            return
        self._worker.reqSendSequence.emit(list(lines), home)

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
        """一键寻零：抬 Z+safe_z → G28 XY → G28 Z → 抬 Z+safe_z → Y 回 safe_y。"""
        seq = build_full_home_sequence(safe_y=self._safe_y, safe_z=self._safe_z)
        self._append_console(
            "一键寻零：抬 Z → XY 归零 → Z 归零 → 抬 Z → Y 回安全点（约 1–2min）…"
        )
        self._set_busy_ui(True)  # 长序列期间锁定控制
        self._send(seq, home=True)

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
        self._jog_enabled = not busy
        widgets = [self.home_full_btn, self.home_btn, self.pen_down_btn,
                   self.pen_up_btn, self.send_btn, self.cmd_input, self.abort_btn]
        widgets += getattr(self, "_jog_btns", [])
        for w in widgets:
            w.setEnabled(not busy)

    # -- P1 文件作业 --------------------------------------------------------

    def _on_tool_changed(self) -> None:
        self._tool = self.tool_combo.currentData()
        self._on_clear_job()
        tip = "裁刀" if self._tool == "knife" else "笔"
        self._append_console(f"工具：{tip}")
        self._refresh_tool_info()

    def _on_material_changed(self) -> None:
        data = self.material_combo.currentData()
        self._apply_material(data)
        self._on_clear_job()

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
        """速度/跳段抬升改变：记录 + 若已载入作业则重新生成 G-code。"""
        self._feed_xy = float(self.feed_xy_spin.value())
        self._feed_z = float(self.feed_z_spin.value())
        self._travel_lift_mm = float(self.lift_spin.value())
        if self._job_paths and not self._job_running:
            try:
                self._job_lines = self._gen_job_lines(self._job_paths)
                self.job_info.setText(
                    f"{self.job_info.text().split('：')[0]}："
                    f"{len(self._job_paths)} 段，{len(self._job_lines)} 行"
                    f"（XY {self._feed_xy:g} / Z {self._feed_z:g} / 抬 {self._travel_lift_mm:g}）"
                )
            except (_JobNotCalibrated, ValueError):
                pass

    def _on_opt_changed(self) -> None:
        """去重/顺序/笔径改变：记录 + 重新载入当前 job（重跑去重/边界）。"""
        self._opt_dedup = self.dedup_cb.isChecked()
        self._opt_sort = self.sort_cb.isChecked()
        self._pen_diameter_mm = float(self.pen_diameter_spin.value())
        self._refresh_tool_info()
        # 若已载入路径（原始工件坐标丢失——需重新从文件载入），提示重载
        # 这里最简单：若 _job_source 有记录则重载
        src = getattr(self, "_job_source", None)
        if src and not self._job_running:
            self._load_svg_path(src)
        self._append_console(
            f"优化：去重{'开' if self._opt_dedup else '关'} / "
            f"顺序{'开' if self._opt_sort else '关'} / "
            f"笔径 {self._pen_diameter_mm:g}mm")

    def _cut_depth_mm(self) -> float:
        return float(self.cut_depth_spin.value())

    def _on_load_svg(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "载入 SVG", "", "SVG 文件 (*.svg);;所有文件 (*)"
        )
        if path:
            self._load_svg_path(path)

    def _load_svg_path(self, path: str, *, already_paper: bool = False) -> bool:
        """载入一个 SVG 文件进作业流水线（含平移/越界/gcode 生成）。成功 True。

        already_paper=True：文件已是纸面坐标(y-up, 排版导出)——不再翻转。
        False（外部 SVG）：内容 y-down → 翻成纸面 y-up（flip_y 210−y）。
        统一后作业/打印都在 paper 坐标（原点左下、X右、Y上=远），所见即所得。
        """
        from megapro.gui.job import check_bounds, gcode_for_cutting, \
            gcode_for_drawing, polylines_for_svg

        self._job_source = str(path)  # 记源，供优化参数变更时重载
        try:
            paths = polylines_for_svg(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "载入失败", f"无法解析 SVG：{exc}")
            return False
        if not paths:
            QtWidgets.QMessageBox.warning(self, "载入失败", "SVG 中没有可绘制的图形。")
            return False
        # 重叠去重 / 顺序优化（工件坐标做，最稳；去重容差=笔径/2）
        if self._opt_dedup or self._opt_sort:
            from megapro.gui.opt import dedup_and_optimize

            paths = dedup_and_optimize(
                paths, dedup=self._opt_dedup,
                tol=self._pen_diameter_mm / 2.0,
                optimize=self._opt_sort)
            if not paths:
                QtWidgets.QMessageBox.warning(self, "载入失败", "去重后无剩余路径。")
                return False
        # 翻成纸面坐标：外部 SVG(y-down)→y-up。排版导出(already_paper)已是 y-up 不翻。
        if not already_paper:
            from megapro.gui.job import flip_y

            paths = flip_y(paths, bed_h=210.0)
        # 工件原点平移：SVG 是工件坐标 → 平移到机器坐标（GUI 维护偏移，不发 G92）
        ox, oy = self._job_origin_xy()
        if ox or oy:
            from megapro.gui.controller import translate_paths

            paths = translate_paths(paths, ox, oy)
        # 越界预检：裁纸按材料可达区域 min(材料, 210×210) − 边距；写字按 210×210。
        # 笔径 → 边界补偿（笔心距边留半径）
        pen_r = self._pen_diameter_mm / 2.0
        if self._tool == "knife":
            mx = min(self._material_w, 210.0)
            my = min(self._material_h, 210.0)
            bad = check_bounds(paths, max_x=mx, max_y=my,
                               margin=self._material_margin, pen_radius=pen_r)
        else:
            bad = check_bounds(paths, max_x=210, max_y=210, pen_radius=pen_r)
        if bad:
            QtWidgets.QMessageBox.warning(
                self, "越界",
                f"图形超出可达区域，已拒绝：\n{bad[0]}"
            )
            return False
        self._job_paths = paths
        # 生成机器绝对 Z 的 G-code（不落盘）
        try:
            self._job_lines = self._gen_job_lines(paths)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "参数错误", str(exc))
            return False
        except _JobNotCalibrated as exc:
            QtWidgets.QMessageBox.warning(self, "未标定", str(exc))
            return False
        self.job_info.setText(
            f"{Path(path).name}：{len(paths)} 段，{len(self._job_lines)} 行"
        )
        self._draw_preview(paths)
        self.run_btn.setEnabled(True)
        self._append_console(f"已载入 {path}（{len(self._job_lines)} 行）")
        return True

    def _gen_job_lines(self, paths) -> list:
        """按当前工具/标定/速度生成作业 G-code（机器绝对 Z）。"""
        from megapro.gui.job import cut_z_for_depth, gcode_for_cutting, \
            gcode_for_drawing

        if self._tool == "knife":
            if self._cut_touch_z is None:
                raise _JobNotCalibrated(
                    "裁刀触纸高度未标定（profile 无 cut_touch_z）。\n"
                    "先切到『刀』并做 Z 触纸标定。"
                )
            cut_z = cut_z_for_depth(self._cut_touch_z, self._cut_depth_mm())
            return gcode_for_cutting(paths, cut_down_z=cut_z,
                                     safe_z=self._safe_z,
                                     feed_xy=self._feed_xy,
                                     feed_z=self._feed_z,
                                     travel_lift_mm=self._travel_lift_mm)
        else:
            if self._pen_down_z is None:
                raise _JobNotCalibrated(
                    "笔触纸高度未标定（profile 无 pen_down_z）。先做 Z 标定。"
                )
            return gcode_for_drawing(paths, pen_down_z=self._pen_down_z,
                                     safe_z=self._safe_z,
                                     feed_xy=self._feed_xy,
                                     feed_z=self._feed_z,
                                     travel_lift_mm=self._travel_lift_mm)

    def _on_layout_export(self, svg: str) -> None:
        """排版页导出 → 写临时 SVG → 载入作业流水线（切到作业 tab）。"""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False,
                                         mode="w", encoding="utf-8") as fh:
            fh.write(svg)
            tmp = fh.name
        try:
            ok = self._load_svg_path(tmp, already_paper=True)  # 排版导出已是 paper
            if ok:
                self.tabs.setCurrentIndex(0)
                self._append_console("排版已送去作业页 —— 检查预览后点『开始执行』")
        finally:
            Path(tmp).unlink(missing_ok=True)

    def _on_clear_job(self) -> None:
        self._job_paths = []
        self._job_lines = []
        self._scene.clear()
        self.job_info.setText("未载入文件")
        self.run_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("")

    def _draw_preview(self, paths) -> None:
        """画 210×210 纸框 + 蓝色落笔路径（工件坐标，毫米映射到场景）。"""
        self._scene.clear()
        scale = 1.6
        pen_b = QtGui.QPen(QtGui.QColor("#08c"))
        pen_b.setWidthF(0)
        # 纸框
        self._scene.addRect(0, 0, 210 * scale, 210 * scale,
                            QtGui.QPen(QtGui.QColor("#888")))
        for p in paths:
            if len(p) < 2:
                continue
            pts = [QtCore.QPointF(x * scale, (210 - y) * scale) for x, y in p]
            poly = QtGui.QPolygonF(pts)
            self._scene.addPolygon(poly, pen_b)
        self.preview.fitInView(self._scene.itemsBoundingRect(),
                               QtCore.Qt.KeepAspectRatio)

    def _on_run_job(self) -> None:
        if not self._job_lines or not self._require_ready():
            return
        if self._job_running:
            return
        if self._tool == "knife" and self._cut_touch_z is None:
            QtWidgets.QMessageBox.warning(self, "未标定", "裁刀触纸高度未标定。")
            return
        self._job_running = True
        self._set_job_ui_running(True)
        self.progress_bar.setValue(0)
        self._append_console(f"开始执行（{len(self._job_lines)} 行）… 注意：全程看着机器")
        self._worker.reqRunJob.emit(list(self._job_lines))

    def _set_job_ui_running(self, running: bool) -> None:
        self.run_btn.setEnabled(not running and bool(self._job_lines))
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

    def _on_job_done(self, ok: bool) -> None:
        self._job_running = False
        self._set_job_ui_running(False)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.job_abort_btn.setEnabled(False)
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
