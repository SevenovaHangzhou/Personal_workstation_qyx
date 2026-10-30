"""Select a real YomkServerPy runtime; never provide a substitute framework."""
import importlib
import os
import platform
import sys
from pathlib import Path


def load_framework():
    root = Path(__file__).resolve().parent
    common = root / "runtime" / "common"
    if common.is_dir():
        sys.path.append(str(common))
    prefix = os.environ.get("YOMK_PREFIX_PATH")
    if prefix:
        site = Path(prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        if not (site / "YomkApi.py").is_file():
            raise RuntimeError(f"YOMK_PREFIX_PATH 中没有匹配当前 Python 的 YomkApi：{site}")
        sys.path.insert(0, str(site))
    else:
        try:
            return importlib.import_module("YomkApi")
        except ModuleNotFoundError as exc:
            if exc.name != "YomkApi":
                raise RuntimeError(f"已安装的 YomkServerPy 缺少依赖：{exc.name}") from exc
        if sys.platform == "linux" and platform.machine() == "x86_64" and sys.version_info[:2] == (3, 10):
            sys.path.insert(0, str(root / "runtime" / "linux-x86_64-py310"))
        else:
            sys.path.insert(0, str(root / "runtime" / "python"))
    try:
        return importlib.import_module("YomkApi")
    except (ImportError, OSError) as exc:
        raise RuntimeError("YomkServerPy 加载失败，请核对 Python ABI 和共享库依赖：" + str(exc)) from exc


api = load_framework()
