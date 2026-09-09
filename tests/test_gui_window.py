"""P0 GUI 主窗口 gating 回归测试（offscreen，独立 QApplication）。

与 test_gui_worker.py 分开：worker 测用 QCoreApplication，而 MainWindow
是 QWidget，必须跑在 QApplication 上；同进程混用会挂死。故本文件在模块
顶部独占创建 QApplication。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def test_mainwindow_ready_enables_controls():
    """回归：连接时禁用控制；收到 READY 必须恢复 Home/Jog/笔/命令可用。

    曾 bug：_on_state('READY') 漏调 _set_busy_ui(False)，导致状态显示
    『就绪』但所有控制按钮永久灰（真机验收发现）。
    """
    from megapro.gui.main_window import MainWindow

    w = MainWindow()
    w.show()

    # 连接中：全部禁用
    w._set_busy_ui(True)
    assert not w.home_btn.isEnabled()
    assert not w._jog_btns[0].isEnabled()
    assert not w.pen_down_btn.isEnabled()
    assert not w.cmd_input.isEnabled()

    # 收到 READY：全部恢复
    w._on_state("READY")
    assert w.home_btn.isEnabled(), "READY 后 Home 仍灰"
    assert w._jog_btns[0].isEnabled(), "READY 后 Jog 仍灰"
    assert w.pen_down_btn.isEnabled(), "READY 后 笔控 仍灰"
    assert w.cmd_input.isEnabled(), "READY 后 命令输入 仍灰"
    w.close()
