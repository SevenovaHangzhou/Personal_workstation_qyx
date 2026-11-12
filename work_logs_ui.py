"""Nonmodal local day/week logs; the Yomk service owns background generation."""
from PyQt5.QtCore import Qt,QTimer,QUrl,QVariant
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QApplication,QDialog,QListWidget,QListWidgetItem,QTextBrowser,QProgressBar,QSplitter,QWidget
from ui_helpers import box,label,button


class LogPreview(QTextBrowser):
    def __init__(self):
        super().__init__();self.setOpenLinks(False);self.setOpenExternalLinks(False)
        self.setObjectName('workLogPreview')
    def loadResource(self,kind,url):
        # Model Markdown must not fetch external images or read local files.
        return QVariant()


class WorkLogsDialog(QDialog):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.client=owner.runtime.daily;self.selected=None;self.preview_text=""
        self.setWindowTitle('工作日志与周报 · 星序');self.setWindowModality(Qt.NonModal)
        self.resize(1040,760);self.setMinimumSize(760,540)
        layout=box(self,margins=(22,20,22,18),spacing=12)
        head=box(horizontal=True);head.addWidget(label('工作日志与周报','h2'));head.addStretch()
        from report_sharing_ui import open_report_sharing
        head.addWidget(button('日志共享','outline',callback=lambda:open_report_sharing(self.owner)))
        self.today=button('下班','primary',callback=lambda:owner.open_worklog('today'));self.today.setToolTip('后台总结今日工作日志，按共享设置提供给管理员');head.addWidget(self.today)
        self.week=button('总结周报','outline',callback=lambda:owner.open_worklog('week'));self.week.setToolTip('总结本周一 00:00 至当前的工作记录，按共享设置提供给管理员');head.addWidget(self.week);layout.addLayout(head)
        layout.addWidget(label('今日从 00:00 开始；本周从周一 00:00 开始，均统计到点击时刻。生成后保存在本机；启用日志共享后供指定管理员收集。22:00 自动同步照常。','muted',True))
        self.notice=label('','muted',True);layout.addWidget(self.notice)
        self.progress=QProgressBar();self.progress.setTextVisible(False);layout.addWidget(self.progress)
        split=QSplitter(Qt.Horizontal);split.setChildrenCollapsible(False)
        self.rows=QListWidget();self.rows.setMinimumWidth(190);self.rows.setMaximumWidth(300);self.rows.setWordWrap(True)
        self.rows.setAccessibleName('手动工作日志历史');self.rows.currentItemChanged.connect(self.show_selected);split.addWidget(self.rows)
        self.preview=LogPreview();self.preview.setReadOnly(True);self.preview.setAccessibleName('工作日志正文')
        self.preview.setPlaceholderText('生成后在这里查看日志；可以关闭此窗口继续处理其他任务。');split.addWidget(self.preview)
        split.setSizes([250,740]);layout.addWidget(split,1)
        actions=box(horizontal=True)
        self.retry=button('重试所选日志','outline',callback=self.retry_selected);actions.addWidget(self.retry)
        self.cancel=button('取消生成','outline',callback=self.cancel_active);actions.addWidget(self.cancel)
        self.copy=button('复制日志','soft',callback=lambda:QApplication.clipboard().setText(self.preview_text));actions.addWidget(self.copy)
        actions.addWidget(button('打开文件夹','linkButton',callback=self.open_folder));actions.addStretch()
        actions.addWidget(button('返回任务',callback=self.close));layout.addLayout(actions)
        self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.refresh)
        self.signature=None;self.refresh()

    def choose(self,identifier):
        self.selected=identifier;self.signature=None;self.refresh()

    def refresh(self):
        state=self.client.worklog_snapshot();self.folder=state['folder']
        active=next((r for r in state['rows'] if r['id']==state['active']),None)
        self.today.setEnabled(not state['busy']);self.week.setEnabled(not state['busy']);self.cancel.setEnabled(state['busy'])
        self.progress.setVisible(state['busy'])
        if active:
            self.notice.setText('后台正在生成：'+active['title']+' · '+active['phase'])
            self.progress.setRange(0,active['total']);self.progress.setValue(active['completed'])
        signature=(state['revision'],self.selected)
        if signature!=self.signature:
            self.rows.blockSignals(True);self.rows.clear()
            for row in state['rows']:
                status={'done':'已生成','pending':'待重试','cancelled':'已取消','queued':'排队中','running':'生成中'}[row['state']]
                item=QListWidgetItem(row['title']+'\n'+row['end'][11:19]+' · '+status)
                item.setData(Qt.UserRole,row);self.rows.addItem(item)
                if row['id']==self.selected:self.rows.setCurrentItem(item)
            if self.rows.currentRow()<0 and state['rows']:self.rows.setCurrentRow(0)
            self.rows.blockSignals(False);self.signature=signature
            self.show_selected()
        selected=self.rows.currentItem().data(Qt.UserRole) if self.rows.currentItem() else None
        self.retry.setEnabled(bool(selected and selected['state'] in ('pending','cancelled') and not state['busy']))
        self.copy.setEnabled(bool(self.preview.toPlainText()))
        if not active:self.notice.setText(selected['phase'] if selected else '尚未生成手动工作日志。')

    def show_selected(self,*unused):
        item=self.rows.currentItem()
        if not item:self.preview.clear();self.preview_text="";self.selected=None;return
        row=item.data(Qt.UserRole);self.selected=row['id']
        text=self.client.worklog_text(self.selected)
        if text!=self.preview_text:
            bar=self.preview.verticalScrollBar();position=bar.value() if getattr(self,'preview_id',None)==self.selected else 0
            self.preview_id=self.selected
            self.preview_text=text;self.preview.setMarkdown(text);bar.setValue(position)

    def retry_selected(self):
        if self.selected:
            result=[]
            if self.owner.write(lambda:result.append(self.client.worklog_retry(self.selected))):self.choose(result[0]['id'])
    def cancel_active(self):
        self.owner.write(self.client.worklog_cancel);self.refresh()
    def open_folder(self):QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder))
    def showEvent(self,event):self.timer.start();self.refresh();super().showEvent(event)
    def hideEvent(self,event):self.timer.stop();super().hideEvent(event)
