"""Native, cancellable metadata inventory. There are deliberately no delete APIs."""
import threading
from PyQt5.QtCore import QThread,Qt,QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QDialog,QTableWidget,QTableWidgetItem,QAbstractItemView,QHeaderView
from ui_helpers import box,label,button
from storage_usage import scan_usage,format_bytes


class UsageWorker(QThread):
    def __init__(self,root,parent):
        super().__init__(parent);self.root=root;self.stop=threading.Event();self.result=None;self.error=''
    def run(self):
        try:self.result=scan_usage(self.root,self.stop.is_set)
        except OSError as exc:self.error=str(exc)


class StorageDialog(QDialog):
    def __init__(self,owner):
        super().__init__(owner);self.root=owner.runtime.path.parent;self.worker=None;self.closing=False
        self.setWindowTitle('本地存储用量');self.resize(850,570)
        layout=box(self,margins=(24,24,24,24),spacing=12)
        layout.addWidget(label('看清本地留存','h2'))
        layout.addWidget(label('只统计文件元数据，不读取聊天正文、不跟随符号链接。数值是扫描时的逻辑大小，运行中的文件可能变化。','muted',True))
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['类别','用量','文件数','距修改 ≥30天','距修改 ≥90天'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);layout.addWidget(self.table,1)
        self.notice=label('准备扫描…','muted',True);layout.addWidget(self.notice)
        layout.addWidget(label('天数列只显示原始输出和 Git 留存的年龄分布，并非可安全删除清单。任务、作答、课程正文、日报与同步账本均不纳入这些列。当前没有删除或自动清理功能。','muted',True))
        actions=box(horizontal=True)
        self.refresh_button=button('重新统计','outline',callback=self.scan);actions.addWidget(self.refresh_button)
        actions.addWidget(button('打开数据目录','linkButton',callback=lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root)))))
        actions.addStretch();actions.addWidget(button('关闭',callback=self.reject));layout.addLayout(actions)
        self.scan()

    def scan(self):
        if self.worker:return
        self.refresh_button.setEnabled(False);self.notice.setText('正在后台扫描，可继续操作应用。')
        self.worker=UsageWorker(self.root,self);self.worker.finished.connect(self.loaded);self.worker.start()

    def loaded(self):
        worker=self.worker;self.worker=None;self.refresh_button.setEnabled(True)
        if self.closing:worker.deleteLater();super().done(QDialog.Rejected);return
        if worker.error:self.notice.setText('统计失败：'+worker.error);worker.deleteLater();return
        data=worker.result;rows=data['rows'];self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            values=[row['title'],format_bytes(row['bytes']),str(row['files']),format_bytes(row['older30']) if row['history'] else '保留',format_bytes(row['older90']) if row['history'] else '保留']
            for j,value in enumerate(values):
                item=QTableWidgetItem(value);item.setToolTip(row['path'] or '数据根目录其他文件');self.table.setItem(i,j,item)
        total=sum(row['bytes'] for row in rows)
        self.notice.setText(f"合计 {format_bytes(total)} · {sum(row['files'] for row in rows)} 个文件 · 跳过 {data['skipped_links']} 个链接 · {data['errors']} 处不可读或已变化。"+('扫描已取消。' if data['cancelled'] else ''))
        worker.deleteLater()

    def done(self,code):
        if self.worker:
            self.closing=True;self.worker.stop.set();self.notice.setText('正在结束扫描…');return
        super().done(code)

    def closeEvent(self,event):
        if self.worker:self.done(QDialog.Rejected);event.ignore()
        else:super().closeEvent(event)
