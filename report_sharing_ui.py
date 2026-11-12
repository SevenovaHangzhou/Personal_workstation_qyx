"""Native report sharing and administrator collection; source reports stay read-only."""
import csv
from datetime import datetime
from pathlib import Path
from PyQt5.QtCore import Qt,QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QWidget,QCheckBox,QComboBox,QFormLayout,QTreeWidget,QTreeWidgetItem,
                            QPlainTextEdit,QFileDialog,QProgressBar,QDialog)
from collaboration.knowledge import safe_file
from ui_helpers import box,label,button


def open_report_sharing(owner):
    owner.open_collaboration()
    dialog=owner.collaboration_dialog;dialog.tabs.setCurrentWidget(dialog.report_panel)


def counts(files):
    return (sum(r['path'].startswith('日报/') for r in files),sum(r['path'].startswith('周报/') for r in files))


def csv_cell(text):
    value=str(text)
    return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value


class ReportSharingPanel(QWidget):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.client=owner.client;self.loaded_config=None;self.state={};self.signature=None;self.peer=None;self.file_signature=None
        layout=box(self,margins=(6,8,6,6),spacing=8)
        row=box(horizontal=True);self.info=label('','muted',True);row.addWidget(self.info,1)
        from journal_sharing_ui import open_journal_sharing
        row.addWidget(button('连接评估员','primary',callback=lambda:open_journal_sharing(self.owner)))
        row.addWidget(button('报告文件共享设置','outline',callback=self.open_settings));layout.addLayout(row)
        self.settings=QWidget();form=QFormLayout(self.settings)
        self.enabled=QCheckBox('自动将日报和周报只读共享给指定管理员');form.addRow(self.enabled)
        self.admin=QComboBox();form.addRow('管理员设备',self.admin)
        self.collect=QCheckBox('我是管理员，自动收集同事授权给本机的报告');form.addRow(self.collect)
        form.addRow(button('保存共享设置','primary',callback=self.configure))
        self.settings_dialog=QDialog(self);self.settings_dialog.setWindowTitle('日报周报共享设置');self.settings_dialog.resize(590,260)
        settings_layout=box(self.settings_dialog,margins=(20,20,20,20));settings_layout.addWidget(self.settings)
        settings_layout.addWidget(button('关闭','outline',callback=self.settings_dialog.hide));self.settings_dialog.hide()
        self.progress=QProgressBar();self.progress.setTextVisible(False);self.progress.setMaximumHeight(6);layout.addWidget(self.progress);self.progress.hide()
        self.sources=QTreeWidget();self.sources.setHeaderLabels(['同事设备','日报','周报','收集状态']);self.sources.setRootIsDecorated(False)
        self.sources.setMinimumHeight(72);self.sources.setMaximumHeight(130);self.sources.currentItemChanged.connect(self.select_peer)
        self.sources.setColumnWidth(0,170);self.sources.setColumnWidth(1,50);self.sources.setColumnWidth(2,50);layout.addWidget(self.sources)
        self.files=QComboBox();self.files.currentIndexChanged.connect(self.show_report);layout.addWidget(self.files)
        self.preview=QPlainTextEdit();self.preview.setReadOnly(True);self.preview.setPlaceholderText('管理员收集报告后，在这里选择同事并查看正文。');layout.addWidget(self.preview,1)
        row=box(horizontal=True);row.addWidget(button('打开本机报告目录','outline',callback=self.open_local))
        row.addWidget(button('打开收集目录','outline',callback=self.open_collected));row.addStretch()
        row.addWidget(button('导出汇总清单','outline',callback=self.export_index));layout.addLayout(row)
        self.notice=label('评估员统计请使用「连接评估员」。本页为星序之间的报告文件共享；原始聊天、数据库与密钥保留在本机。','muted',True);layout.addWidget(self.notice)

    def open_settings(self):
        self.settings_dialog.show();self.settings_dialog.raise_();self.settings_dialog.activateWindow()

    def current_config(self):return dict(enabled=self.enabled.isChecked(),admin_id=self.admin.currentData(),collect=self.collect.isChecked())

    def refresh(self,state):
        self.state=state;config=state['report_sharing'];selection=self.admin.currentData()
        if self.loaded_config is None or self.current_config()==self.loaded_config:
            selection=config['admin_id'];self.enabled.setChecked(config['enabled']);self.collect.setChecked(config['collect']);self.loaded_config=dict(config)
        peer_rows=[p for p in state['peers'].values() if any(r['peer_id']==p['id'] and r['state']=='active' and r['own_confirmed'] and r['peer_confirmed'] for r in state['pairs'].values())]
        self.admin.blockSignals(True);self.admin.clear();self.admin.addItem('请选择已配对的管理员设备',None)
        for p in peer_rows:self.admin.addItem(p['name']+' · '+p['id'][:8],p['id'])
        if selection and self.admin.findData(selection)<0:self.admin.addItem('原管理员设备 · 需要重新配对',selection)
        self.admin.setCurrentIndex(max(0,self.admin.findData(selection)));self.admin.blockSignals(False)
        name=state['peers'].get(config['admin_id'],{}).get('name','未选择')
        self.info.setText(('只读共享给：'+name if config['enabled'] else '报告共享未启用')+(' · 管理员收集已开启' if config['collect'] else ''))
        self.info.setToolTip(state.get('report_folder','')+'\n'+state.get('report_source_status',''))
        signature=[(p['id'],p['name'],state['report_inbox'].get(p['id'],{}).get('version'),state['report_status'].get(p['id'],''),p.get('online')) for p in state['peers'].values()]
        if signature!=self.signature:
            self.signature=signature;self.sources.blockSignals(True);self.sources.clear()
            for p in state['peers'].values():
                cache=state['report_inbox'].get(p['id']);daily,weekly=counts(cache.get('files',[]) if cache else [])
                status=state['report_status'].get(p['id'],'等待启用管理员收集' if not config['collect'] else '等待同事授权或上线')
                item=QTreeWidgetItem([p['name'],str(daily),str(weekly),status]);item.setData(0,Qt.UserRole,p['id']);item.setToolTip(3,status);item.setToolTip(0,p['id']);self.sources.addTopLevelItem(item)
                if p['id']==self.peer:self.sources.setCurrentItem(item)
            if self.sources.currentItem() is None and self.sources.topLevelItemCount():self.sources.setCurrentItem(self.sources.topLevelItem(0))
            self.sources.blockSignals(False);self.select_peer()
        sync=state.get('report_sync',{});busy=sync.get('status')=='receiving'
        self.progress.setVisible(busy)
        if busy:self.progress.setRange(0,sync.get('total',0));self.progress.setValue(sync.get('done',0))

    def configure(self):
        config=self.current_config()
        if config['enabled']:
            if not config['admin_id']:self.notice.setText('请先与管理员配对，再选择管理员设备。');return
            name=self.state['peers'].get(config['admin_id'],{}).get('name','所选管理员')
            if not self.owner.confirm('共享日报与周报','将现有及后续生成的日报、下班日志和周报只读共享给「'+name+'」。对方启用管理员收集后可自动保存报告副本。停止共享会阻止后续读取，已收集的副本仍由对方保留。'):return
        try:
            self.client.report_configure(**config);self.loaded_config=None;self.owner.refresh();self.settings_dialog.hide()
            self.notice.setText('已保存共享设置；双方启用 LAN Mesh 并在线时自动收集。')
        except (ValueError,OSError) as exc:self.notice.setText(str(exc))

    def select_peer(self,*args):
        item=self.sources.currentItem();self.peer=item.data(0,Qt.UserRole) if item else None
        cache=self.state.get('report_inbox',{}).get(self.peer,{})
        signature=(self.peer,cache.get('version'))
        if signature==self.file_signature:return
        self.file_signature=signature;selected=self.files.currentData();self.files.blockSignals(True);self.files.clear()
        for row in cache.get('files',[]):
            import re
            name=row['path'];match=re.search(r'(today|week)-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-',name)
            title=(('周报' if match[1]=='week' else '下班日志')+' · '+match[2]+'-'+match[3]+'-'+match[4]+' '+match[5]+':'+match[6]+':'+match[7]) if match else name.replace('日报/自动/','自动日报 · ').removesuffix('.md')
            self.files.addItem(title,name);self.files.setItemData(self.files.count()-1,name,Qt.ToolTipRole)
        self.files.setCurrentIndex(max(0,self.files.findData(selected)));self.files.blockSignals(False);self.show_report()

    def show_report(self,*args):
        cache=self.state.get('report_inbox',{}).get(self.peer,{});name=self.files.currentData()
        if not name:self.preview.clear();return
        try:
            path=safe_file(cache['root'],name)
            if path.stat().st_size>512000:self.preview.setPlainText('报告较大，请从收集目录打开查看。');return
            self.preview.setPlainText(path.read_text(encoding='utf-8'))
        except (ValueError,OSError,UnicodeError) as exc:self.preview.setPlainText('读取报告未完成：'+str(exc))

    def open_local(self):
        if self.state.get('report_folder'):QDesktopServices.openUrl(QUrl.fromLocalFile(self.state['report_folder']))

    def open_collected(self):
        root=self.state.get('report_inbox',{}).get(self.peer,{}).get('root')
        if root:QDesktopServices.openUrl(QUrl.fromLocalFile(root))
        else:self.notice.setText('尚未收集所选同事的报告。')

    def export_index(self):
        if not self.state.get('report_inbox'):self.notice.setText('尚未收集报告，暂时没有可导出的清单。');return
        path,_=QFileDialog.getSaveFileName(self,'导出报告汇总清单','工作报告汇总-'+datetime.now().strftime('%Y%m%d')+'.csv','CSV (*.csv)')
        if not path:return
        try:
            with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(['同事设备','设备标识','类型','报告文件','收集时间','校验值'])
                for peer,cache in self.state['report_inbox'].items():
                    for row in cache['files']:
                        writer.writerow([csv_cell(self.state['peers'].get(peer,{}).get('name',peer)),peer,'周报' if row['path'].startswith('周报/') else '日报',row['path'],cache.get('collected_at',''),row['sha256']])
            self.notice.setText('汇总清单已导出。日报与周报按文件统计，不自动评价任务完成情况。')
        except OSError as exc:self.notice.setText(str(exc))
