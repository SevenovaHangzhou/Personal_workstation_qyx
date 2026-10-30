"""Native daily log and Linear destination dialogs."""
from pathlib import Path
from PyQt5.QtCore import QThread, QTimer, Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QDialog, QComboBox, QCheckBox, QListWidget, QListWidgetItem, QTextEdit, QTabWidget, QWidget
from ui_helpers import box, label, button


class ChoicesWorker(QThread):
    def __init__(self, client, team_id, owner):
        super().__init__(owner); self.client=client; self.team_id=team_id; self.result=None; self.error=''
    def run(self):
        try: self.result=self.client.choices(self.team_id)
        except Exception as exc: self.error=str(exc)


class TargetDialog(QDialog):
    def __init__(self, owner, client, target):
        super().__init__(owner); self.client=client; self.initial=target; self.worker=None; self.closing=False
        self.setWindowTitle('Linear 归属'); self.setMinimumWidth(460)
        layout=box(self,margins=(24,24,24,24),spacing=14)
        layout.addWidget(label('让工作回到合适的地方。','h2'))
        layout.addWidget(label('负责人为当前 Linear 用户；子任务继承父任务归属。','muted',True))
        self.team=QComboBox(); self.project=QComboBox(); self.project.addItem('不指定项目','')
        layout.addWidget(label('团队（必选）','section')); layout.addWidget(self.team)
        layout.addWidget(label('项目（可选）','section')); layout.addWidget(self.project)
        self.status=label('正在读取可访问团队…','muted',True); layout.addWidget(self.status)
        self.save=button('使用此归属','primary',callback=self.accept); self.save.setEnabled(False); layout.addWidget(self.save)
        layout.addWidget(button('取消',callback=self.reject))
        self.team.currentIndexChanged.connect(self.load_projects)
        self.load('')

    def load(self, team_id):
        self.team.setEnabled(False); self.project.setEnabled(False); self.save.setEnabled(False)
        self.worker=ChoicesWorker(self.client,team_id,self); self.worker.finished.connect(self.loaded); self.worker.start()

    def loaded(self):
        worker=self.worker; self.worker=None
        if self.closing: worker.deleteLater(); super().reject(); return
        if worker.error:
            self.status.setText(worker.error); self.team.setEnabled(True); worker.deleteLater(); return
        data=worker.result; worker.deleteLater()
        if not worker.team_id:
            self.team.blockSignals(True); self.team.clear(); self.team.addItem('请选择团队','')
            for team in data['teams']: self.team.addItem(team['name'],team['id'])
            index=self.team.findData(self.initial.get('team_id','')); self.team.setCurrentIndex(max(0,index)); self.team.blockSignals(False)
            self.team.setEnabled(True)
            if self.team.currentData(): self.load(self.team.currentData()); return
        else:
            self.project.clear(); self.project.addItem('不指定项目','')
            for project in data['projects']: self.project.addItem(project['name'],project['id'])
            index=self.project.findData(self.initial.get('project_id','')); self.project.setCurrentIndex(max(0,index))
        self.team.setEnabled(True); self.project.setEnabled(True); self.save.setEnabled(bool(self.team.currentData()))
        self.status.setText('负责人：'+data['viewer']['name'])

    def load_projects(self):
        if self.worker: return
        if self.team.currentData(): self.load(self.team.currentData())
        else: self.save.setEnabled(False)

    def value(self): return {'team_id':self.team.currentData(),'project_id':self.project.currentData() or ''}
    def reject(self):
        if self.worker:
            self.closing=True; self.status.setText('正在结束读取，请稍候…'); return
        super().reject()
    def closeEvent(self,event):
        if self.worker: self.reject(); event.ignore()
        else: event.accept()


class DailyDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner); self.owner=owner; self.client=owner.runtime.daily
        self.setWindowTitle('每日日报 · 进度留存'); self.resize(850,760)
        layout=box(self,margins=(25,23,25,23),spacing=12)
        layout.addWidget(label('把今天的小小进展，收好。','h2'))
        layout.addWidget(label('每天 22:00 自动收集前一日 22:00 至今天 22:00 的 Codex 文本。\n本地记录所有分类；仅明确绑定的工作聊天会上传对应 issue 的日期评论。','muted',True))
        self.enabled=QCheckBox('启用每日 22:00 日报、工作 issue 同步和本地 Git 留存')
        self.enabled.setChecked(self.client.snapshot()['config']['enabled']); self.enabled.toggled.connect(self.configure); layout.addWidget(self.enabled)
        row=box(horizontal=True); row.addWidget(button('选择默认团队 / 项目','outline',callback=self.target)); row.addWidget(button('补跑到期日报 / 重试','primary',callback=self.retry)); layout.addLayout(row)
        layout.addWidget(button('查看本地存储用量','linkButton',callback=self.storage))
        self.target_info=label('','muted',True); layout.addWidget(self.target_info)
        layout.addWidget(label('未完成代码任务：每日本地分支＋补丁及未跟踪文件快照；不自动提交、不切换当前分支。\n关闭主窗口后托盘继续；应用完全退出或电脑关闭期间不能运行，下次启动会补跑。','muted',True))
        self.notice=label('','muted',True); layout.addWidget(self.notice)
        self.tabs=QTabWidget(); layout.addWidget(self.tabs,1)
        reports_page=QWidget(); report_layout=box(reports_page,spacing=10); self.tabs.addTab(reports_page,'日报记录与失败原因')
        self.reports=QListWidget(); self.reports.setMaximumHeight(155); self.reports.currentItemChanged.connect(self.show_report); report_layout.addWidget(self.reports)
        self.preview=QTextEdit(); self.preview.setReadOnly(True); report_layout.addWidget(self.preview,1)
        pending_page=QWidget(); pending_layout=box(pending_page,spacing=10); self.tabs.addTab(pending_page,'待同步工作任务')
        pending_layout.addWidget(label('这里只预览本地待创建 issue 与父级调整，不提前发布。日报评论与 Git 留存结果在日报记录中查看。','muted',True))
        self.pending=QListWidget(); self.pending.setWordWrap(True); pending_layout.addWidget(self.pending,1)
        self.pending_detail=label('','muted',True); pending_layout.addWidget(self.pending_detail)
        self.locate=button('定位工作任务','outline',callback=self.locate_task); pending_layout.addWidget(self.locate)
        self.pending.currentItemChanged.connect(self.show_pending)
        layout.addWidget(button('打开本地日报文件夹','linkButton',callback=self.open_folder))
        layout.addWidget(button('关闭',callback=self.accept))
        self.timer=QTimer(self); self.timer.setInterval(1500); self.timer.timeout.connect(self.refresh); self.timer.start(); self.refresh()

    def configure(self, value):
        if self.owner.write(lambda:self.client.configure(enabled=value)): self.refresh()
    def storage(self):
        from storage_ui import StorageDialog
        dialog=StorageDialog(self.owner); dialog.setParent(self,Qt.Dialog); self.owner.storage_dialog=dialog
        try: dialog.exec_()
        finally: self.owner.storage_dialog=None; dialog.deleteLater()
    def retry(self):
        self.owner.write(self.client.retry); self.refresh()
    def target(self):
        dialog=TargetDialog(self,self.client,self.client.snapshot()['config']['target'])
        if dialog.exec_()==QDialog.Accepted:
            self.owner.write(lambda:self.client.configure(target=dialog.value())); self.refresh()
    def refresh(self):
        snapshot=self.client.snapshot(); config=snapshot['config']; target=config['target']
        self.target_info.setText('默认归属：'+('已设置团队'+('和项目' if target.get('project_id') else '') if target.get('team_id') else '尚未选择；新 issue 等待归属配置')+'\n下次日报日期：'+config['next_day']+' · Asia/Shanghai 22:00')
        self.notice.setText(('● 正在处理  ' if snapshot['busy'] else '')+snapshot['notice'])
        pending=snapshot.get('pending_sync',[])
        if getattr(self,'pending_signature',None)!=pending:
            self.pending_signature=pending
            current=self.pending.currentItem(); selected_id=current.data(Qt.UserRole)['task_id'] if current else None
            self.pending.clear()
            for row in pending:
                item=QListWidgetItem(row['title']+'\n'+row['action']); item.setData(Qt.UserRole,row); self.pending.addItem(item)
                if row['task_id']==selected_id:self.pending.setCurrentItem(item)
            if self.pending.currentRow()<0 and pending:self.pending.setCurrentRow(0)
            self.show_pending()
        self.tabs.setTabText(1,f'待同步工作任务 · {len(pending)}')
        selected=self.reports.currentItem().data(Qt.UserRole)['day'] if self.reports.currentItem() else None
        rows=snapshot['reports']
        signature=(snapshot['report_revision'],[(r['day'],r['state'],r['errors']) for r in rows])
        if getattr(self,'signature',None)==signature: return
        self.signature=signature; self.reports.clear()
        for row in rows:
            item=QListWidgetItem(row['day']+' · '+('已完成' if row['state']=='done' else '待处理 / 将重试'))
            item.setData(Qt.UserRole,row); self.reports.addItem(item)
            if row['day']==selected: self.reports.setCurrentItem(item)
        if self.reports.currentRow()<0 and rows: self.reports.setCurrentRow(0)
    def show_report(self, item, previous=None):
        if not item: self.preview.clear(); return
        path=Path(item.data(Qt.UserRole)['path'])
        try: self.preview.setPlainText(path.read_text(encoding='utf-8'))
        except OSError: self.preview.setPlainText('本地报告投影暂时不可读，数据库中的记录仍保留。')
    def show_pending(self, *unused):
        item=self.pending.currentItem(); self.locate.setEnabled(bool(item))
        if not item:self.pending_detail.setText('目前没有待创建 issue 或待调整父级的工作任务。');return
        row=item.data(Qt.UserRole)
        self.pending_detail.setText('目标父级：'+row['parent']+'\n同步时间：'+row['due_at']+'\n'+row['reason']+('' if row['enabled'] else '\n自动同步已暂停'))
    def locate_task(self):
        item=self.pending.currentItem()
        if item:self.owner.select_task(item.data(Qt.UserRole)['task_id']);self.accept()
    def open_folder(self): QDesktopServices.openUrl(QUrl.fromLocalFile(self.client.snapshot()['folder']))
