"""User-level display preferences; independent of task and service data."""
from dataclasses import dataclass
from PyQt5.QtCore import QObject, QSettings, QByteArray, Qt, pyqtSignal
from PyQt5.QtGui import QFont
from theme import scaled_style

WINDOWS = [('auto', '适配屏幕 · 记住手动调整'), ('compact', '紧凑窗口 · 1024 × 720'),
           ('standard', '标准窗口 · 1280 × 840'), ('wide', '宽屏窗口 · 1440 × 960'),
           ('maximized', '最大化')]


@dataclass(frozen=True)
class DisplayPreferences:
    font_size: int = 13
    window_mode: str = 'auto'

    @classmethod
    def read(cls, store):
        if store is None: return cls()
        try: size = int(store.value('display/font_size', 13))
        except (TypeError, ValueError): size = 13
        mode = store.value('display/window_mode', 'auto')
        return cls(size if 12 <= size <= 18 else 13,
                   mode if mode in dict(WINDOWS) else 'auto')


class DisplayController(QObject):
    changed = pyqtSignal()

    def __init__(self, app, store=None):
        super().__init__()
        self.app = app; self.store = store
        self.saved = DisplayPreferences.read(store); self.current = self.saved
        self.previewing = False

    def apply(self, preferences):
        self.current = preferences
        font = QFont(self.app.font()); font.setPixelSize(preferences.font_size)
        self.app.setFont(font); self.app.setStyleSheet(scaled_style(preferences.font_size))
        self.changed.emit()

    def save(self, preferences):
        if self.store is not None:
            self.store.setValue('display/font_size', preferences.font_size)
            self.store.setValue('display/window_mode', preferences.window_mode)
            self.store.sync()
            if self.store.status() != QSettings.NoError:
                raise OSError('显示设置暂未写入磁盘，请检查配置目录权限。')
        self.saved = preferences; self.apply(preferences)

    def remember_window(self, window):
        if self.store is not None and not self.previewing:
            self.store.setValue('display/geometry', window.saveGeometry())
            self.store.sync()

    def fit_window(self, window, mode=None, restore=False):
        mode = mode or self.current.window_mode
        screen = window.screen() or self.app.primaryScreen()
        available = screen.availableGeometry()
        window.setMinimumSize(min(960, available.width()), min(600, available.height()))
        if restore and mode == 'auto' and self.store is not None:
            geometry = self.store.value('display/geometry')
            if isinstance(geometry, QByteArray) and window.restoreGeometry(geometry):
                if window.isMaximized(): return
                self._clamp(window, available)
                return
        if mode == 'maximized':
            window.showMaximized(); return
        window.showNormal() if window.isVisible() and window.isMaximized() else window.setWindowState(window.windowState() & ~Qt.WindowMaximized)
        sizes = {'compact': (1024,720), 'standard': (1280,840), 'wide': (1440,960)}
        width, height = sizes.get(mode, (min(1440, round(available.width()*.92)), min(960, round(available.height()*.9))))
        window.resize(min(width, available.width()), min(height, available.height()))
        window.move(available.center()-window.rect().center())

    @staticmethod
    def _clamp(window, available):
        window.resize(min(window.width(), available.width()), min(window.height(), available.height()))
        x = max(available.left(), min(window.x(), available.right()-window.width()+1))
        y = max(available.top(), min(window.y(), available.bottom()-window.height()+1))
        window.move(x,y)
