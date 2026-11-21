"""Small native onboarding card; no web server and no model-facing secret field."""
import hashlib
from pathlib import Path
import sys

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import (QApplication, QDialog, QFrame, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QProgressBar, QFileDialog,
                             QMessageBox)
from .core import OrbitError
from platform_support import lock_file


STYLE = '''
QDialog { background: #f3f6fb; color: #23304a; }
QLabel { color: #23304a; font-size: 14px; }
QLabel#brand { color: #69809d; font-size: 12px; font-weight: 600; }
QLabel#title { font-size: 25px; font-weight: 700; color: #1d2a43; }
QLabel#muted { color: #6d7c91; font-size: 12px; }
QLabel#status { color: #326665; background: #e8f4f1; padding: 10px; border-radius: 9px; }
QFrame#card { background: white; border: 1px solid #dce3ed; border-radius: 14px; }
QLineEdit { padding: 10px; background: #f9fbfe; color: #23304a; border: 1px solid #cbd6e5; border-radius: 8px; font-size: 14px; }
QLineEdit:focus { border: 1px solid #629eab; background: white; }
QPushButton { padding: 9px 14px; border: 1px solid #cbd6e5; border-radius: 8px; color: #344760; background: white; font-size: 13px; }
QPushButton:hover { background: #eef4f8; border-color: #9fb7c9; }
QPushButton:disabled { color: #a1acbb; background: #f3f5f8; border-color: #e0e5ec; }
QPushButton#primary { color: white; background: #315c76; border-color: #315c76; font-weight: 600; }
QPushButton#primary:hover { background: #254a62; }
QPushButton#primary:disabled { background: #95aab8; border-color: #95aab8; }
QProgressBar { border: none; background: #e4edf3; border-radius: 2px; max-height: 4px; }
QProgressBar::chunk { background: #76a8b4; }
'''


def label(value, name='', wrap=False):
    widget = QLabel(value)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(wrap)
    if name:
        widget.setObjectName(name)
    return widget


class SettingsJob(QThread):
    succeeded = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, function, parent):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.succeeded.emit(self.function())
        except OrbitError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit('操作未完成，请检查本机凭据权限或网络后重试。')
        finally:
            # Release the closure holding a user-entered key after this request.
            self.function = None


class SettingsCard(QDialog):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.job = None
        self.setWindowTitle('星序 · 连接设置')
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(460)
        self.resize(520, 570)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(12)
        layout.addWidget(label('✧  七耀星 · ORBIT', 'brand'))
        layout.addWidget(label('连接你的工作', 'title'))
        layout.addWidget(label('让 Linear 任务与 Codex 的开发结果接上。', 'muted', True))
        self.status = label('', 'status', True)
        layout.addWidget(self.status)
        card = QFrame()
        card.setObjectName('card')
        content = QVBoxLayout(card)
        content.setContentsMargins(18, 18, 18, 18)
        content.setSpacing(10)
        content.addWidget(label('Linear API Key'))
        key_row = QHBoxLayout()
        self.key = QLineEdit()
        self.key.setObjectName('apiKey')
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setMaxLength(4096)
        self.key.setPlaceholderText('在这里输入，或保留已保存的密钥')
        self.key.setAttribute(Qt.WA_InputMethodEnabled, False)
        self.key.setAccessibleName('Linear API Key')
        key_row.addWidget(self.key, 1)
        self.reveal = QPushButton('显示')
        self.reveal.setCheckable(True)
        self.reveal.toggled.connect(self.show_key)
        key_row.addWidget(self.reveal)
        content.addLayout(key_row)
        content.addWidget(label('密钥仅存本机，不进入聊天或开发日志。', 'muted', True))
        content.addSpacing(7)
        content.addWidget(label('默认工作目录 · 可选'))
        directory_row = QHBoxLayout()
        self.directory = QLineEdit()
        self.directory.setPlaceholderText('领取任务时仍可另选目录')
        self.directory.setAccessibleName('默认工作目录')
        directory_row.addWidget(self.directory, 1)
        self.browse = QPushButton('选择')
        self.browse.clicked.connect(self.choose_directory)
        directory_row.addWidget(self.browse)
        content.addLayout(directory_row)
        content.addWidget(label('选择本地仓库目录，便于后续关联 Codex 项目。', 'muted', True))
        layout.addWidget(card)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.notice = label('先测试连接，确认账号后即可保存。', 'muted', True)
        layout.addWidget(self.notice)
        actions = QHBoxLayout()
        self.test = QPushButton('测试连接')
        self.test.clicked.connect(lambda: self.begin(False))
        self.save = QPushButton('保存并继续')
        self.save.setObjectName('primary')
        self.save.clicked.connect(lambda: self.begin(True))
        actions.addWidget(self.test)
        actions.addWidget(self.save, 1)
        layout.addLayout(actions)
        bottom = QHBoxLayout()
        self.remove = QPushButton('移除插件密钥')
        self.remove.clicked.connect(self.remove_key)
        self.close_button = QPushButton('稍后设置')
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.remove)
        bottom.addStretch()
        bottom.addWidget(self.close_button)
        layout.addLayout(bottom)
        self.lan_button = QPushButton('局域网日志与管理员共享')
        self.lan_button.clicked.connect(self.open_lan)
        layout.addWidget(self.lan_button)
        self.schedule_button = QPushButton('日报定时收集')
        self.schedule_button.clicked.connect(self.open_schedule)
        layout.addWidget(self.schedule_button)
        layout.addStretch()
        self.refresh()

    def open_lan(self):
        from .core import Orbit
        from .lan import open_card
        value = open_card(Orbit(self.settings.directory))
        self.notice.setText(value.get('message', '当前无可见桌面，请在本机运行 scripts/mcp.py --lan。'))

    def open_schedule(self):
        from .core import Orbit
        from .scheduler import open_card
        value=open_card(Orbit(self.settings.directory))
        self.notice.setText(value.get('message','请在本机运行 scripts/mcp.py --schedule。'))

    def refresh(self):
        try:
            summary = self.settings.summary()
            self.directory.setText(summary['default_work_dir'])
            source = summary['source']
            messages = {'none': '尚未连接 Linear · 填写密钥后即可开始',
                        'plugin': '已保存插件密钥 · 可以测试连接或更换',
                        'desktop': '正在复用桌面版密钥 · 更换只保存在插件中',
                        'environment': '正在使用系统环境中的密钥 · 可测试当前连接'}
            self.status.setText(messages[source])
            self.key.setEnabled(source != 'environment')
            self.reveal.setEnabled(source != 'environment')
            self.remove.setEnabled(source == 'plugin')
            self.close_button.setText('关闭' if summary['configured'] else '稍后设置')
        except Exception:
            self.status.setText('本机配置暂不可读取 · 请检查目录权限')
            self.remove.setEnabled(False)

    def show_key(self, show):
        self.key.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)
        self.reveal.setText('隐藏' if show else '显示')

    def choose_directory(self):
        value = QFileDialog.getExistingDirectory(self, '选择默认工作目录', self.directory.text() or str(Path.home()))
        if value:
            self.directory.setText(value)

    def busy(self, value):
        self.progress.setVisible(value)
        for widget in (self.key, self.reveal, self.directory, self.browse, self.test, self.save, self.remove, self.close_button):
            widget.setEnabled(not value)

    def begin(self, save):
        if self.job and self.job.isRunning():
            return
        key, directory = self.key.text(), self.directory.text()
        function = (lambda: self.settings.save(key, directory)) if save else (lambda: self.settings.test_connection(key))
        self.busy(True)
        self.notice.setText('正在核对 Linear 账号…')
        self.job = SettingsJob(function, self)
        self.job.succeeded.connect(lambda result: self.succeeded(result, save))
        self.job.failed.connect(self.failed)
        self.job.finished.connect(self.finished)
        self.job.start()

    def succeeded(self, result, saved):
        self.notice.setText(('已保存 · ' if saved else '连接成功 · ') + result['account'] +
                            ('。返回 Codex 即可继续，配置立即生效。' if saved else '。点击「保存并继续」完成设置。'))
        self.status.setText('已确认账号：' + result['account'])
        if saved:
            self.key.clear()
            self.reveal.setChecked(False)
            self.close_button.setText('完成')

    def failed(self, message):
        self.notice.setText(message)

    def finished(self):
        self.busy(False)
        try:
            summary = self.settings.summary()
            self.remove.setEnabled(summary['source'] == 'plugin')
            self.key.setEnabled(summary['source'] != 'environment')
            self.reveal.setEnabled(summary['source'] != 'environment')
        except Exception:
            self.remove.setEnabled(False)

    def remove_key(self):
        if QMessageBox.question(self, '移除插件密钥', '移除插件自己的 Linear 密钥？桌面版密钥和任务记录会保留。',
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.settings.clear_plugin_key()
            self.key.clear()
            self.refresh()
            self.notice.setText('已移除插件密钥。若配置了桌面版来源，将恢复复用。')
        except Exception:
            self.notice.setText('移除未完成，请检查本机凭据权限。')

    def closeEvent(self, event):
        if self.job and self.job.isRunning():
            self.notice.setText('连接检查即将完成，请稍候；Codex 仍可继续操作。')
            event.ignore()
        else:
            self.key.clear()
            event.accept()


def run_card(settings):
    from qt_runtime import configure_qt_plugins
    configure_qt_plugins()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('星序 · 连接设置')
    app.setOrganizationName('Sevenova')
    identity = 'orbit-settings-' + hashlib.sha256(str(settings.directory.resolve()).encode()).hexdigest()[:24]
    existing = QLocalSocket()
    existing.connectToServer(identity)
    if existing.waitForConnected(500):
        existing.write(b'activate')
        existing.waitForBytesWritten(500)
        existing.disconnectFromServer()
        return 0
    settings.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    guard = (settings.directory / 'settings-ui.lock').open('a+')
    try:
        lock_file(guard)
    except OSError:
        guard.close()
        # The other card may still be starting; do not unlink its socket.
        existing.abort()
        existing.connectToServer(identity)
        if existing.waitForConnected(1000):
            existing.write(b'activate')
            existing.waitForBytesWritten(500)
        return 0
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    if not server.listen(identity):
        QLocalServer.removeServer(identity)
        if not server.listen(identity):
            guard.close()
            return 2
    card = SettingsCard(settings)

    def activate():
        connection = server.nextPendingConnection()
        if connection:
            connection.disconnectFromServer()
            connection.deleteLater()
        card.showNormal()
        card.raise_()
        card.activateWindow()

    server.newConnection.connect(activate)
    card.show()
    card.raise_()
    code = app.exec_()
    server.close()
    guard.close()
    return code
