"""QApplication 装配（中文界面）。入口：python -m megapro.gui"""

from __future__ import annotations

import os
import sys

from PySide6 import QtCore, QtWidgets


def _setup_qt_environment() -> None:
    # 让 Qt 走与现有环境一致的平台插件路径；报错更清晰。
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")


def run(argv: list[str] | None = None) -> int:
    _setup_qt_environment()
    app = QtWidgets.QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("MegaPro 上位机")
    app.setApplicationDisplayName("Mega Pro 写字/裁纸上位机")
    app.setOrganizationName("megapro")

    # 延迟 import：Qt 不可用时（如无显示环境的测试）可先报清晰错误。
    from .main_window import MainWindow

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
