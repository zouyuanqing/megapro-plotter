"""Z 触纸/触床标定向导（P1c）。

非标笔每次装笔笔尖伸出量不定 → 用向导手动下探，测出「当前绝对 Z = 触纸」，
回写 profile（笔 → pen_down_z，刀 → cut_down_z），供写字/裁纸落刀使用。

流程（全由用户在机器旁操作，向导驱动 worker）：
1. 先抬到 safe_z（绝对，慢速）——安全起点。
2. 用户每点一次「下探 d mm」→ 发 G91 相对下移一小步（慢 F300）。
   建议步长 1mm，接近纸面时改 0.2mm。
3. 笔尖刚触纸时用户点「触到了」→ 发 M114 读当前绝对 Z → 记录。
4. 抬回 safe_z → 确认 → 把 Z 写入 profile（pen_down_z / cut_down_z）。

安全：只在 safe_z 与纸面之间小步下探；绝不一次盲下大距离；
Z 移动需 allow_z（worker 已带）。全程可随时点「取消」（仅关向导，不动机器）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

_DEFAULT_STEP = 1.0
_FINE_STEP = 0.2
_Z_FEED = 300.0


class CalibrateZDialog(QtWidgets.QDialog):
    """Z 触纸标定向导。传入 worker（已连接）与目标键（pen_down_z/cut_down_z）。"""

    def __init__(
        self,
        worker,
        *,
        tool_key: str,  # "pen_down_z" 或 "cut_down_z"
        tool_name: str,  # "笔" / "裁刀"
        safe_z: float,
        profile_path: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._worker = worker
        self.tool_key = tool_key
        self.tool_name = tool_name
        self.safe_z = safe_z
        self.profile_path = profile_path
        self._result_z: float | None = None

        self.setWindowTitle(f"Z 标定：{tool_name} 触纸/触床高度")
        lay = QtWidgets.QVBoxLayout(self)

        tip = QtWidgets.QLabel(
            f"把{tool_name}尖移到纸面上方需要落笔的位置。\n"
            f"向导会先把 Z 抬到 {safe_z:g}，然后你逐步下探，"
            f"{tool_name}尖刚碰到纸面时点『触到了』。\n"
            "步长：粗调 1mm，接近纸面用 0.2mm。"
        )
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # 当前步长 + 下探按钮
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("步长："))
        self.step_combo = QtWidgets.QComboBox()
        for label, v in (("1 mm", 1.0), ("0.5 mm", 0.5), ("0.2 mm", 0.2)):
            self.step_combo.addItem(label, v)
        row.addWidget(self.step_combo)
        self.step_combo.setCurrentIndex(0)
        self.down_btn = QtWidgets.QPushButton("下探（相对下降）")
        self.down_btn.clicked.connect(self._on_step_down)
        row.addWidget(self.down_btn)
        self.up_btn = QtWidgets.QPushButton("上抬一点")
        self.up_btn.clicked.connect(self._on_step_up)
        row.addWidget(self.up_btn)
        lay.addLayout(row)

        # 当前 Z 显示 + 动作
        self.z_label = QtWidgets.QLabel("当前 Z：未知（先抬到安全高度）")
        lay.addWidget(self.z_label)
        act = QtWidgets.QHBoxLayout()
        self.lift_btn = QtWidgets.QPushButton(f"① 抬到安全高度 Z{safe_z:g}")
        self.lift_btn.clicked.connect(self._on_lift)
        act.addWidget(self.lift_btn)
        self.touch_btn = QtWidgets.QPushButton("② 触到了！记录并回写")
        self.touch_btn.clicked.connect(self._on_touch)
        act.addWidget(self.touch_btn)
        lay.addLayout(act)

        self.status = QtWidgets.QLabel("就绪")
        self.status.setStyleSheet("color:#666")
        lay.addWidget(self.status)

        cancel = QtWidgets.QPushButton("取消（不动机器）")
        cancel.clicked.connect(self.reject)
        lay.addWidget(cancel)

        # 订阅 worker 位置信号刷新 Z
        self._worker.position.connect(self._on_position)

    # -- worker 交互 --------------------------------------------------------

    def _send(self, line: str) -> None:
        if self._worker is not None:
            self._worker.reqSendLine.emit(line, None)

    def _on_lift(self) -> None:
        self._send("G90")
        self._send(f"G0 Z{self.safe_z:g} F{_Z_FEED:g}")
        self._poll_z()

    def _on_step_down(self) -> None:
        step = self.step_combo.currentData()
        self._send("G91")
        self._send(f"G0 Z{-step:g} F{_Z_FEED:g}")
        self._send("G90")
        self._poll_z()

    def _on_step_up(self) -> None:
        step = self.step_combo.currentData()
        self._send("G91")
        self._send(f"G0 Z{step:g} F{_Z_FEED:g}")
        self._send("G90")
        self._poll_z()

    def _poll_z(self) -> None:
        if self._worker is not None:
            self._worker.reqPoll.emit()

    def _on_position(self, x, y, z) -> None:
        if z is not None:
            self.z_label.setText(f"当前 Z：{z:.2f}（绝对）")
            self._current_z = z

    def _on_touch(self) -> None:
        """触到了：记录当前绝对 Z（机器坐标），写回 profile。"""
        z = getattr(self, "_current_z", None)
        if z is None:
            self.status.setText("还没读到 Z 坐标——先抬到安全高度并至少下探一次。")
            return
        self._result_z = z
        self._write_profile(z)
        self.status.setText(
            f"已记录：{self.tool_name} 触纸绝对 Z = {z:.2f} → 已写 profile({self.tool_key})。\n"
            "请抬回安全高度后关闭本窗。"
        )
        self.touch_btn.setEnabled(False)

    def _write_profile(self, z: float) -> None:
        """把 z 写回 profile 的 tool_key（flat-YAML 追加/替换该键）。"""
        try:
            from megapro.cli.main import _load_profile, _save_profile

            data = _load_profile(self.profile_path) or {}
            data[self.tool_key] = float(f"{z:.3f}")
            _save_profile(data, self.profile_path)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"回写 profile 失败：{exc}")

    @property
    def result_z(self) -> float | None:
        return self._result_z
