"""Read-only progress polling; network and download work belongs to UpdateService."""
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QCheckBox,QDialog,QLineEdit,QProgressBar
from ui_helpers import box,label,button


class UpdateDialog(QDialog):
    def __init__(self,parent,client):
        super().__init__(parent);self.client=client;self.setWindowTitle('应用更新');self.resize(520,390)
        layout=box(self,margins=(24,22,24,22),spacing=12)
        layout.addWidget(label('让星序保持新鲜','h2'))
        self.versions=label('','section',True);layout.addWidget(self.versions)
        self.enabled=QCheckBox('自动检查并在后台准备正式更新');layout.addWidget(self.enabled)
        self.message=label('','muted',True);layout.addWidget(self.message)
        self.progress=QProgressBar();layout.addWidget(self.progress)
        self.checked=label('','muted',True);layout.addWidget(self.checked)
        layout.addWidget(label('完整退出后切换，下次启动生效；关窗口缩到托盘不会切换。\n更新保存在本地数据目录的 updates/，无需 sudo，保留原安装用于回退。','muted',True))
        row=box(horizontal=True);self.check=button('立即检查更新','primary',callback=lambda:self.action(lambda:client.check()))
        row.addWidget(self.check);self.cancel=button('停止准备','outline',callback=lambda:self.action(client.cancel));row.addWidget(self.cancel);layout.addLayout(row)
        layout.addWidget(button('返回',callback=self.accept))
        self.refresh();self.enabled.toggled.connect(lambda enabled:self.action(lambda:client.configure(enabled)))
        self.timer=QTimer(self);self.timer.setInterval(600);self.timer.timeout.connect(self.refresh);self.timer.start()

    def action(self,operation):
        try:operation()
        except (OSError,ValueError) as exc:self.message.setText(str(exc));return
        self.refresh()

    def refresh(self):
        try:state=self.client.snapshot()
        except (OSError,ValueError) as exc:self.message.setText(str(exc));return
        self.versions.setText('当前版本 '+state['current']+(' · 新版本 '+state['available'] if state['available'] else ''))
        self.enabled.blockSignals(True);self.enabled.setChecked(state['enabled']);self.enabled.blockSignals(False)
        self.message.setText(state['message']);self.checked.setText('上次检查：'+(state['last_checked'] or '尚未检查'))
        busy=state['busy'];total=state['total'];self.progress.setVisible(busy or state['status']=='ready')
        if busy and not total:self.progress.setRange(0,0)
        else:self.progress.setRange(0,100);self.progress.setValue(int(state['done']*100/total) if total else 100)
        self.check.setEnabled(not busy);self.cancel.setEnabled(busy or bool(state['ready']))
        self.cancel.setText('停止准备' if busy else '取消待更新')

    def done(self,result):
        self.timer.stop();super().done(result)
