"""Native LAN controls. Secrets are entered locally and never passed through a model."""
import hashlib
import json
import sys
import threading
import uuid
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QListWidget, QListWidgetItem, QTabWidget, QWidget, QPlainTextEdit,
    QMessageBox, QInputDialog, QProgressBar, QSplitter)
from platform_support import lock_file
from evaluation.shared_store import SharedStore
from evaluation.mesh import Discovery
from . import lan
from .journals import export_local
from .settings_ui import STYLE, label


class LanJob(QThread):
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, function, parent):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.done.emit(self.function())
        except (ValueError, PermissionError, InterruptedError) as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit('连接未完成，请检查员工在线状态、授权、依赖与网络。上次副本保留。')
        finally:
            self.function = None


class LanCard(QDialog):
    def __init__(self, orbit):
        super().__init__()
        self.orbit = orbit
        self.config = lan.LanConfig(orbit.directory)
        self.contacts = lan.Contacts(orbit.directory)
        self.store = SharedStore(orbit.directory / 'shared-store')
        self.job, self.cancel = None, threading.Event()
        self.setWindowTitle('星序 · 局域网日志')
        self.setStyleSheet(STYLE + '\nQListWidget {font-size: 13px;} QPlainTextEdit {font-size: 14px;}')
        self.resize(920, 760)
        self.setMinimumSize(700, 580)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        layout = QVBoxLayout(self)
        layout.addWidget(label('局域网日志', 'title'))
        layout.addWidget(label('UDP 发现 · TLS/TCP 直传 · Shared Store 离线副本', 'muted', True))
        self.status = label('', 'status', True)
        layout.addWidget(self.status)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        share, admin = QWidget(), QWidget()
        tabs.addTab(share, '我的共享')
        tabs.addTab(admin, '管理员 · 日志库')
        own = QVBoxLayout(share)
        self.name, self.department = QLineEdit(), QLineEdit()
        row = QHBoxLayout()
        row.addWidget(label('显示名'))
        row.addWidget(self.name)
        row.addWidget(label('部门'))
        row.addWidget(self.department)
        own.addLayout(row)
        self.enabled = QCheckBox('启用局域网发现与日志共享（后台持续运行）')
        own.addWidget(self.enabled)
        own.addWidget(label('仅勾选的任务共享结果日志与日报摘要；草稿也包含在内。邀请独立授权，原始聊天不共享。', 'muted', True))
        self.tasks = QListWidget()
        own.addWidget(self.tasks, 1)
        self.save = QPushButton('保存共享范围')
        self.save.clicked.connect(self.save_config)
        own.addWidget(self.save)
        own.addWidget(label('管理员授权 · 撤销只阻止后续读取，无法远程删除已收集副本', 'muted', True))
        self.grants = QListWidget()
        self.grants.setMaximumHeight(100)
        own.addWidget(self.grants)
        row = QHBoxLayout()
        create = QPushButton('为管理员创建邀请')
        create.clicked.connect(self.create_invite)
        revoke = QPushButton('撤销选中授权')
        revoke.clicked.connect(self.revoke)
        row.addWidget(create)
        row.addWidget(revoke)
        own.addLayout(row)
        view = QVBoxLayout(admin)
        self.discovery_toggle = QPushButton('发现的设备 · 展开（发现不代表授权）')
        self.discovery_toggle.setCheckable(True)
        self.discovery_toggle.toggled.connect(self.show_peers)
        view.addWidget(self.discovery_toggle)
        self.peers = QListWidget()
        self.peers.setMaximumHeight(64)
        self.peers.hide()
        view.addWidget(self.peers)
        add = QPushButton('导入员工邀请')
        add.clicked.connect(self.add_contact)
        view.addWidget(add)
        row = QHBoxLayout()
        self.contact_list = QListWidget()
        self.contact_list.setFixedHeight(54)
        self.contact_list.currentItemChanged.connect(lambda *_: self.show_files())
        view.addWidget(self.contact_list)
        self.pull = QPushButton('收集副本')
        self.pull.clicked.connect(self.pull_contact)
        self.stop = QPushButton('取消收集')
        self.stop.clicked.connect(self.cancel.set)
        self.stop.setEnabled(False)
        remove = QPushButton('移除联系人')
        remove.clicked.connect(self.remove_contact)
        for button in (self.pull, self.stop, remove):
            row.addWidget(button)
        view.addLayout(row)
        self.collected = label('选择员工后查看本机副本；员工离线仍可阅读，收集时间不代表实时状态。', 'muted', True)
        view.addWidget(self.collected)
        splitter = QSplitter()
        self.files, self.reader = QListWidget(), QPlainTextEdit()
        self.reader.setReadOnly(True)
        self.reader.setMinimumHeight(140)
        self.files.currentItemChanged.connect(self.read_file)
        splitter.addWidget(self.files)
        splitter.addWidget(self.reader)
        splitter.setSizes([260, 500])
        view.addWidget(splitter, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.notice = label('', 'muted', True)
        layout.addWidget(self.notice)
        close = QPushButton('关闭卡片')
        close.clicked.connect(self.close)
        layout.addWidget(close)
        value = self.config.read()
        # An administrator can discover employees without publishing their own logs.
        self.discovery = Discovery(value['employee_id'] or str(uuid.uuid4()), '日志管理员', 0, '')
        self.discovery.start()
        self.name.setText(value['name'])
        self.department.setText(value['department'])
        self.enabled.setChecked(value['enabled'])
        for task in orbit.list_tasks(limit=1000)['tasks']:
            item = QListWidgetItem(task['issue']['identifier'] + ' · ' + task['issue']['title'])
            item.setData(Qt.UserRole, task['id'])
            item.setCheckState(Qt.Checked if task['id'] in value['task_ids'] else Qt.Unchecked)
            self.tasks.addItem(item)
        self.load_contacts()
        self.load_grants()
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        if value['enabled']:
            lan.start_node(orbit)

    def show_peers(self, visible):
        self.peers.setVisible(visible)
        self.discovery_toggle.setText('发现的设备 · ' + ('收起' if visible else '展开') + '（发现不代表授权）')

    def action(self, function):
        try:
            return function()
        except Exception as exc:
            self.notice.setText(str(exc) if isinstance(exc, ValueError) else '操作未完成，请检查本机权限或连接。')

    def begin(self, function, done):
        if self.job and self.job.isRunning():
            return
        self.progress.show()
        self.pull.setEnabled(False)
        self.stop.setEnabled(True)
        self.cancel.clear()
        self.job = LanJob(function, self)
        self.job.done.connect(done)
        self.job.failed.connect(self.notice.setText)
        self.job.finished.connect(self.finished)
        self.job.start()

    def finished(self):
        self.progress.hide()
        self.pull.setEnabled(True)
        self.stop.setEnabled(False)

    def refresh(self):
        runtime = self.config.runtime()
        if runtime['state'] == 'running':
            self.status.setText(f"日志共享已启动 · TCP {runtime['port']} · UDP 45874 · 指纹 {runtime['fingerprint'][:16]}…")
            self.status.setToolTip('证书 SHA-256：' + runtime['fingerprint'])
        else:
            self.status.setText(runtime.get('notice') or '共享未启动。管理员仍可查看已经收集的本地副本。')
        old = self.peers.currentItem().data(Qt.UserRole) if self.peers.currentItem() else None
        self.peers.clear()
        peers = {(peer['employee_id'], peer['fingerprint']): peer for peer in runtime.get('peers', [])}
        peers.update({(peer['employee_id'], peer['fingerprint']): peer for peer in self.discovery.snapshot()})
        for peer in peers.values():
            item = QListWidgetItem(f"{peer['name']} · {peer['address']}:{peer['port']}")
            item.setData(Qt.UserRole, peer)
            self.peers.addItem(item)
            if old and old['employee_id'] == peer['employee_id']:
                self.peers.setCurrentItem(item)
        if runtime.get('notice'):
            self.notice.setText(runtime['notice'])

    def save_config(self):
        def save():
            ids = [self.tasks.item(i).data(Qt.UserRole) for i in range(self.tasks.count()) if self.tasks.item(i).checkState() == Qt.Checked]
            self.config.save(self.orbit, self.name.text(), self.department.text(), self.enabled.isChecked(), ids)
            export_local(self.orbit)
            if self.enabled.isChecked():
                lan.start_node(self.orbit)
            self.notice.setText('范围已保存。关闭卡片不停止共享；取消勾选启用并保存可停止。')
        self.action(save)

    def load_grants(self):
        self.grants.clear()
        if not self.config.read()['employee_id']:
            return
        for grant in lan.share_server(self.orbit, self.config).grants():
            item = QListWidgetItem(grant['label'] + (' · 已撤销' if grant['revoked'] else ' · 可读取选中任务的日志库'))
            item.setData(Qt.UserRole, grant['id'])
            self.grants.addItem(item)

    def create_invite(self):
        name, accepted = QInputDialog.getText(self, '管理员邀请', '接收授权的管理员姓名：')
        if not accepted:
            return
        value = self.action(lambda: lan.invitation(self.orbit, name))
        if not value:
            return
        self.load_grants()
        dialog = QDialog(self)
        dialog.setWindowTitle('私密邀请 · 请勿发送到聊天或 issue')
        dialog.resize(670, 430)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('含只读令牌，仅通过私密渠道交给该管理员。对方导入后核对证书指纹。', 'muted', True))
        text = QPlainTextEdit(json.dumps(value, ensure_ascii=False, indent=2))
        text.setReadOnly(True)
        layout.addWidget(text)
        copy = QPushButton('复制邀请')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text.toPlainText()))
        layout.addWidget(copy)
        dialog.exec_()
        text.clear()

    def revoke(self):
        item = self.grants.currentItem()
        if item and QMessageBox.question(self, '撤销授权', '停止该管理员之后的读取？已有离线副本无法远程收回。') == QMessageBox.Yes:
            self.action(lambda: lan.share_server(self.orbit, self.config).revoke(item.data(Qt.UserRole)))
            self.load_grants()

    def add_contact(self):
        if self.job and self.job.isRunning():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('导入员工邀请')
        dialog.resize(650, 440)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('核对员工与证书指纹；本操作仅导入并检查授权，不自动收集日志。', 'muted', True))
        url = QLineEdit()
        peer = self.peers.currentItem()
        if peer:
            peer = peer.data(Qt.UserRole)
            url.setText(f"https://{peer['address']}:{peer['port']}")
        url.setPlaceholderText('https://员工局域网IP:卡片显示的TCP端口')
        layout.addWidget(url)
        invitation = QPlainTextEdit()
        invitation.setPlaceholderText('在此粘贴员工提供的私密邀请 JSON')
        layout.addWidget(invitation)
        accept = QPushButton('确认身份并连接')
        accept.clicked.connect(dialog.accept)
        layout.addWidget(accept)
        if dialog.exec_() != QDialog.Accepted:
            invitation.clear()
            return
        raw, address = invitation.toPlainText(), url.text().strip()
        invitation.clear()
        self.notice.setText('正在核对授权…')
        self.begin(lambda: self.contacts.add(raw, address), lambda _: self.contact_added())

    def contact_added(self):
        self.load_contacts()
        self.notice.setText('授权已核对。选择员工并点击「收集副本」。')

    def load_contacts(self):
        self.contact_list.clear()
        for contact in self.contacts.summary():
            item = QListWidgetItem(contact['name'] + ' · ' + contact['url'])
            item.setData(Qt.UserRole, contact['id'])
            self.contact_list.addItem(item)

    def remove_contact(self):
        item = self.contact_list.currentItem()
        if item and QMessageBox.question(self, '移除联系人', '移除本机连接授权？历史 Shared Store 文件保留。') == QMessageBox.Yes:
            self.action(lambda: self.contacts.remove(item.data(Qt.UserRole)))
            self.load_contacts()

    def pull_contact(self):
        item = self.contact_list.currentItem()
        if item:
            contact = item.data(Qt.UserRole)
            self.notice.setText('正在收集并校验，期间可继续使用 Codex…')
            self.begin(lambda: lan.collect(self.orbit, contact, self.cancel), self.collected_done)

    def collected_done(self, value):
        self.notice.setText(f"已收集 {value['files']} 个文件，传输 {value['transferred_bytes']} 字节，复用 {value['reused_files']} 个文件。")
        self.show_files()

    def show_files(self):
        self.files.clear()
        self.reader.clear()
        item = self.contact_list.currentItem()
        if not item:
            return
        value = self.action(lambda: self.store.current(item.data(Qt.UserRole)))
        if not value:
            self.collected.setText('尚无成功收集的副本。')
            return
        when = datetime.fromisoformat(value['collected_at']).astimezone().strftime('%Y-%m-%d %H:%M')
        self.collected.setText('本机快照 · 收集于 ' + when + ' · 非实时状态')
        self.collected.setToolTip(value['collected_at'])
        for row in value['manifest']['files']:
            name = row['path']
            if name.startswith('日志/'):
                day, result = name.split('/')[1:]
                name = day + ' · ' + result[:8] + (' · 日志' if name.endswith('.md') else ' · 数据')
            file = QListWidgetItem(name)
            file.setToolTip(row['path'])
            file.setData(Qt.UserRole, row['path'])
            self.files.addItem(file)

    def read_file(self, item, *_):
        contact = self.contact_list.currentItem()
        if item and contact:
            value = self.action(lambda: self.store.read(contact.data(Qt.UserRole), item.data(Qt.UserRole)))
            self.reader.setPlainText(value or '')

    def closeEvent(self, event):
        if self.job and self.job.isRunning():
            self.cancel.set()
            self.notice.setText('正在停止当前读取，请稍候。')
            event.ignore()
        else:
            self.timer.stop()
            self.discovery.stop()
            event.accept()


def run_card(orbit):
    from qt_runtime import configure_qt_plugins
    configure_qt_plugins()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('星序 · 局域网日志')
    identity = 'orbit-lan-' + hashlib.sha256(str(orbit.directory.resolve()).encode()).hexdigest()[:24]
    existing = QLocalSocket()
    existing.connectToServer(identity)
    if existing.waitForConnected(400):
        existing.write(b'activate')
        existing.waitForBytesWritten(400)
        return 0
    orbit.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    guard = (orbit.directory / 'lan-ui.lock').open('a+')
    try:
        lock_file(guard)
    except OSError:
        guard.close()
        return 0
    QLocalServer.removeServer(identity)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    if not server.listen(identity):
        guard.close()
        return 2
    card = LanCard(orbit)

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
    code = app.exec_()
    server.close()
    guard.close()
    return code
