"""Native controls for installed per-user daily collection, separate from API credentials."""
import hashlib
import sys
import threading

from PyQt5.QtCore import Qt, QTime, QThread, pyqtSignal
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout, QTimeEdit, QCheckBox, QPushButton, QProgressBar
from platform_support import lock_file
from .scheduler import Scheduler
from .settings_ui import STYLE, label


class Job(QThread):
    done=pyqtSignal(dict)
    failed=pyqtSignal(str)

    def __init__(self,function,parent):
        super().__init__(parent);self.function=function

    def run(self):
        try: self.done.emit(self.function())
        except (ValueError,InterruptedError) as exc: self.failed.emit(str(exc))
        except Exception: self.failed.emit('操作未完成，请检查系统任务调度权限和 Codex 配置。')
        finally: self.function=None


class ScheduleCard(QDialog):
    def __init__(self,orbit):
        super().__init__()
        self.orbit=orbit;self.scheduler=Scheduler(orbit.directory);self.job=None;self.cancel=threading.Event()
        self.setWindowTitle('星序 · 日报定时收集')
        self.setStyleSheet(STYLE+'\nQTimeEdit {font-size:20px;padding:8px;border:1px solid #cbd6e5;border-radius:8px;}')
        self.setMinimumWidth(470);self.resize(550,520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        layout=QVBoxLayout(self)
        layout.setContentsMargins(24,24,24,24);layout.setSpacing(12)
        layout.addWidget(label('自动整理每天的工作','title'))
        layout.addWidget(label('读取任务已绑定的 Codex 聊天，结合结构化结果生成本地日报。原始聊天不共享。','muted',True))
        self.status=label('','status',True);layout.addWidget(self.status)
        self.enabled=QCheckBox('启用每日自动收集');layout.addWidget(self.enabled)
        row=QHBoxLayout();row.addWidget(label('每天 · 本机时间'))
        self.at=QTimeEdit();self.at.setDisplayFormat('HH:mm');row.addWidget(self.at);row.addStretch();layout.addLayout(row)
        layout.addWidget(label('默认 00:00（午夜 12 点）。关闭 Codex 后仍由系统调度；电脑关机时无法运行，恢复登录后可补跑。摘要沿用系统当前 Codex 配置。','muted',True))
        self.save=QPushButton('保存定时设置');self.save.setObjectName('primary');self.save.clicked.connect(self.apply);layout.addWidget(self.save)
        self.run_button=QPushButton('立即检查并补跑');self.run_button.clicked.connect(self.collect);layout.addWidget(self.run_button)
        self.progress=QProgressBar();self.progress.setRange(0,0);self.progress.hide();layout.addWidget(self.progress)
        self.notice=label('只生成本地日报。LAN 仍按勾选的任务共享；不自动修改或评论 Linear。','muted',True);layout.addWidget(self.notice)
        self.close_button=QPushButton('关闭');self.close_button.clicked.connect(self.close);layout.addWidget(self.close_button)
        value=self.scheduler.config();self.at.setTime(QTime.fromString(value['time'],'HH:mm'));self.enabled.setChecked(value['enabled'])
        self.refresh()

    def refresh(self):
        value=self.scheduler.status();reg=value['registration'];config=value['config']
        text=('每天 '+config['time']+' · '+('已启用' if config['enabled'] else '已停用')) if reg.get('registered') else '尚未注册系统定时任务 · 保存后生效'
        if value['last_run']:
            text+='\n上次收集：'+value['last_run'].get('updated_at','')+' · 待补跑 '+str(len(value['last_run'].get('pending',[])))+' 天'
        self.status.setText(text)

    def begin(self,function,done):
        if self.job and self.job.isRunning(): return
        self.cancel.clear();self.progress.show();self.save.setEnabled(False);self.run_button.setEnabled(False)
        self.job=Job(function,self);self.job.done.connect(done);self.job.failed.connect(self.notice.setText)
        self.job.finished.connect(self.finished);self.job.start()

    def apply(self):
        at,enabled=self.at.time().toString('HH:mm'),self.enabled.isChecked()
        self.begin(lambda:self.scheduler.configure(at,enabled),lambda _:self.notice.setText('系统定时任务已更新，重复保存不会重复创建。'))

    def collect(self):
        from .nightly import DailyCollector
        self.notice.setText('正在读取绑定聊天并生成摘要，可继续使用 Codex。')
        self.begin(lambda:DailyCollector(self.orbit).collect(cancel=self.cancel),lambda value:self.notice.setText(
            f"检查完成：处理 {value['periods']} 个周期，待重试 {value['pending']} 个周期。"))

    def finished(self):
        self.progress.hide();self.save.setEnabled(True);self.run_button.setEnabled(True);self.refresh()

    def closeEvent(self,event):
        if self.job and self.job.isRunning():
            self.cancel.set();self.notice.setText('正在停止本次操作，请稍候。');event.ignore()
        else: event.accept()


def run_card(orbit):
    from qt_runtime import configure_qt_plugins
    configure_qt_plugins()
    app=QApplication(sys.argv[:1]);app.setApplicationName('星序 · 日报定时收集')
    identity='orbit-schedule-'+hashlib.sha256(str(orbit.directory.resolve()).encode()).hexdigest()[:24]
    existing=QLocalSocket();existing.connectToServer(identity)
    if existing.waitForConnected(400):
        existing.write(b'activate');existing.waitForBytesWritten(400);return 0
    orbit.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    guard=(orbit.directory/'schedule-ui.lock').open('a+')
    try: lock_file(guard)
    except OSError: guard.close();return 0
    QLocalServer.removeServer(identity);server=QLocalServer();server.setSocketOptions(QLocalServer.UserAccessOption)
    if not server.listen(identity): guard.close();return 2
    card=ScheduleCard(orbit)
    def activate():
        socket=server.nextPendingConnection()
        if socket: socket.disconnectFromServer();socket.deleteLater()
        card.showNormal();card.raise_();card.activateWindow()
    server.newConnection.connect(activate);card.show()
    try: return app.exec_()
    finally: server.close();guard.close()
