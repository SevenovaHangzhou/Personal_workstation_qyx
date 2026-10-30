#!/usr/bin/env python3
"""Read-only runtime diagnostics; does not initialize services or write data."""
import argparse
import json
import platform
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        print("星序 ORBIT 需要 Python 3.11+；请指定 MOMO_PYTHON 或创建 .venv", file=sys.stderr)
        return 1
    try:
        from framework import api
        import YomkServerPy
        from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
        from qt_runtime import configure_qt_plugins
        configure_qt_plugins()
        from typedefine.TypeDefine import APP_VERSION
        import starlette.concurrency
        from zoneinfo import ZoneInfo
        ZoneInfo("Asia/Shanghai")
    except (ImportError, RuntimeError, OSError, KeyError) as exc:
        print("星序 ORBIT 依赖检查失败：" + str(exc), file=sys.stderr)
        print("请参阅 README.md 的运行环境与框架配置说明。", file=sys.stderr)
        return 1
    if not args.quiet:
        print(json.dumps({"app_version": APP_VERSION, "framework_version": api.version(),
                          "framework_api": api.__file__, "framework_runtime": YomkServerPy.__file__,
                          "python": platform.python_version(), "platform": platform.platform(),
                          "qt": QT_VERSION_STR, "pyqt": PYQT_VERSION_STR}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": sys.exit(main())
