"""python -m megapro.gui —— 启动上位机（需在仓库根目录，profile/logs 为相对路径）。"""

import sys


def main(argv=None) -> int:
    from .app import run

    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
