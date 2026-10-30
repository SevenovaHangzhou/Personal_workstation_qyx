"""Native, cancellable metadata inventory. There are deliberately no delete APIs."""
import threading
from PyQt5.QtCore import QThread,Qt,QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QDialog,QTableWidget,QTableWidgetItem,QAbstractItemView,QHeaderView,QMessageBox
from ui_helpers import box,label,button
from storage_usage import scan_usage,format_bytes


class UsageWorker(QThread):
    def __init__(self,root,parent,operation=None):
        super().__init__(parent);self.root=root;self.stop=threading.Event();self.result=None;self.error='';self.operation=operation
    def run(self):
        try:self.result=self.operation(self.stop) if self.operation else scan_usage(self.root,self.stop.is_set)
        except (OSError,ValueError) as exc:self.error=str(exc)


class StorageDialog(QDialog):
    def __init__(self,owner):
        super().__init__(owner);self.root=owner.runtime.path.parent;self.client=getattr(owner.runtime,'daily',None);self.worker=None;self.closing=False
        self.setWindowTitle('本地存储用量');self.resize(850,570)
        layout=box(self,margins=(24,24,24,24),spacing=12)
        layout.addWidget(label('看清本地留存','h2'))
        layout.addWidget(label('只统计文件元数据，不读取聊天正文、不跟随符号链接。数值是扫描时的逻辑大小，运行中的文件可能变化。','muted',True))
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['类别','用量','文件数','距修改 ≥30天','距修改 ≥90天'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);layout.addWidget(self.table,1)
        self.notice=label('准备扫描…','muted',True);layout.addWidget(self.notice)
        layout.addWidget(label('保留30天。清理须逐次预览并确认，仅移入系统回收站；任务、作答、课程正文、聊天绑定与同步账本保留。年龄统计不是最终清理清单，活跃或待处理记录会排除。','muted',True))
        actions=box(horizontal=True)
        self.refresh_button=button('重新统计','outline',callback=self.scan);actions.addWidget(self.refresh_button)
        self.clean_button=button('预览30天前留存','outline',callback=self.preview_retention);self.clean_button.setEnabled(bool(self.client));actions.addWidget(self.clean_button)
        actions.addWidget(button('打开数据目录','linkButton',callback=lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root)))))
        actions.addStretch();actions.addWidget(button('关闭',callback=self.reject));layout.addLayout(actions)
        self.scan()

    def scan(self):
        if self.worker:return
        self.operation='usage'
        self.refresh_button.setEnabled(False);self.notice.setText('正在后台扫描，可继续操作应用。')
        self.clean_button.setEnabled(False)
        self.worker=UsageWorker(self.root,self);self.worker.finished.connect(self.loaded);self.worker.start()

    def start_operation(self,kind,operation):
        if self.worker:return
        self.operation=kind;self.refresh_button.setEnabled(False);self.clean_button.setEnabled(False)
        self.notice.setText('正在核对留存清单…' if kind=='preview' else '正在移入系统回收站，关闭可取消尚未开始的部分…')
        self.worker=UsageWorker(self.root,self,operation);self.worker.finished.connect(self.loaded);self.worker.start()

    def preview_retention(self):
        self.start_operation('preview',self.client.retention_preview)

    def loaded(self):
        worker=self.worker;self.worker=None;self.refresh_button.setEnabled(True)
        self.clean_button.setEnabled(bool(self.client))
        if self.closing:worker.deleteLater();super().done(QDialog.Rejected);return
        if worker.error:self.notice.setText('操作未完成：'+worker.error);worker.deleteLater();return
        if self.operation=='preview':
            plan=worker.result;worker.deleteLater();rows=plan['rows']
            if not rows:self.notice.setText('没有符合条件的30天前留存；待处理、活跃、近期修改及核心记录均保留。');return
            dialog=QMessageBox(self);dialog.setWindowTitle('确认本次留存清单');dialog.setIcon(QMessageBox.Question)
            dialog.setText(f"将 {len(rows)} 组留存（{format_bytes(sum(row['bytes'] for row in rows))}）移入系统回收站？")
            dialog.setInformativeText('请展开详细信息核对全部相对路径。最多200组；取消不移动任何文件。文件变化会中止并要求重新预览，清空系统回收站前可自行恢复。')
            dialog.setDetailedText('\n'.join(row['path']+' · '+format_bytes(row['bytes']) for row in rows))
            dialog.setStandardButtons(QMessageBox.Yes|QMessageBox.No);dialog.setDefaultButton(QMessageBox.No)
            if dialog.exec_()==QMessageBox.Yes:self.start_operation('apply',lambda cancel:self.client.retention_apply(plan['id'],cancel))
            return
        if self.operation=='apply':
            result=worker.result;self.notice.setText(f"已移入回收站 {len(result['trashed'])} 组。"+('已取消后续处理。' if result['cancelled'] else '')+'\n'+'\n'.join(result['errors']))
            worker.deleteLater();return
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
