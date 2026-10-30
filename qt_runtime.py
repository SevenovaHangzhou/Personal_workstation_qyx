"""Locate this interpreter's Qt plugins without losing non-ASCII paths."""
from pathlib import Path
import PyQt5
from PyQt5.QtCore import QCoreApplication


def configure_qt_plugins():
    # Some PyQt5 wheels inject their prefix through a byte string. QLibraryInfo
    # can then report '??' for Chinese directories after relocating a venv.
    # Supply the actual Unicode path before constructing QApplication.
    plugins = Path(PyQt5.__file__).resolve().parent / "Qt5" / "plugins"
    if plugins.is_dir():
        QCoreApplication.addLibraryPath(str(plugins))

