"""megapro.gui — 简单轴控制上位机（PySide6, Windows-first, 中文 UI）。

依赖 src/megapro 核心（transport/guard/dialect/toolchain/preview），
自身保持「纯逻辑 controller + QThread worker + 薄 Qt 界面」的分层，
controller 层无 Qt 依赖、可单测。
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
