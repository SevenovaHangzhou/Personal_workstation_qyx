"""Employee setup for the separate Evaluator administrator app."""
import json
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFormLayout, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QScrollArea, QTreeWidget, QTreeWidgetItem, QWidget)
from ui_helpers import box, label, button


class JournalSharingDialog(QDialog):
    def __init__(self, owner, client):
        super().__init__(owner)
        self.client = client
        self.loaded = None
        self.task_key = None
        self.setWindowTitle('连接评估员 · 日报与周报共享')
        self.resize(700, 650)
        self.setMinimumSize(560, 430)
        layout = box(self, margins=(16, 16, 16, 16))
        layout.addWidget(label('工作 issue 的摘要与人工进度用于统计；完整日报、周报需要单独授权。', 'muted', True))
        area = QScrollArea()
        area.setWidgetResizable(True)
        host = QWidget()
        content = box(host)
        form = QFormLayout()
        self.name, self.department = QLineEdit(), QLineEdit()
        self.enabled = QCheckBox('启用评估员共享（仅下方勾选的工作 issue）')
        form.addRow('姓名／员工标识', self.name)
        form.addRow('部门', self.department)
        form.addRow(self.enabled)
        content.addLayout(form)
        self.tasks = QListWidget()
        self.tasks.setMinimumHeight(110)
        self.tasks.setMaximumHeight(170)
        content.addWidget(self.tasks)
        content.addWidget(button('保存共享范围', 'primary', callback=self.configure))
        self.endpoint = label('', 'muted', True)
        self.endpoint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content.addWidget(self.endpoint)
        row = box(horizontal=True)
        self.grant_button = button('创建评估员授权', 'outline', callback=self.new_grant)
        row.addWidget(self.grant_button)
        row.addWidget(button('撤销选中授权', 'outline', callback=self.revoke))
        row.addStretch()
        content.addLayout(row)
        self.grants = QTreeWidget()
        self.grants.setHeaderLabels(['评估员', '读取范围', '授权状态'])
        self.grants.setRootIsDecorated(False)
        self.grants.setMinimumHeight(85)
        self.grants.setMaximumHeight(150)
        content.addWidget(self.grants)
        content.addWidget(label('管理员在 Evaluator「员工与同步」导入授权，核对设备地址和证书指纹。原始聊天、密钥和数据库不共享。新增任务须另行勾选；撤销只阻止后续读取，已收到的离线副本无法远程删除。', 'muted', True))
        content.addStretch()
        area.setWidget(host)
        layout.addWidget(area, 1)
        self.notice = label('', 'muted', True)
        layout.addWidget(self.notice)
        layout.addWidget(button('关闭', 'outline', callback=self.close))
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def perform(self, callback):
        try:
            value = callback()
            self.refresh()
            return value
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '暂未完成', str(exc))

    def current(self):
        return dict(name=self.name.text(), department=self.department.text(), sharing=self.enabled.isChecked(),
            shared_task_ids=[self.tasks.item(i).data(Qt.UserRole) for i in range(self.tasks.count()) if self.tasks.item(i).checkState() == Qt.Checked])

    def refresh(self):
        try:
            value = self.client.snapshot()
        except (ValueError, OSError) as exc:
            self.notice.setText(str(exc))
            return
        config = value['config']
        saved = {key: config[key] for key in ('name', 'department', 'sharing', 'shared_task_ids')}
        clean = self.loaded is None or self.current() == self.loaded
        checked = set(saved['shared_task_ids']) if clean else set(self.current()['shared_task_ids'])
        if clean:
            self.name.setText(saved['name'])
            self.department.setText(saved['department'])
            self.enabled.setChecked(saved['sharing'])
            self.loaded = saved
        key = [(task['id'], task['title'], task['issue'], task['id'] in checked) for task in value['tasks']]
        if key != self.task_key:
            self.task_key = key
            self.tasks.clear()
            for task in value['tasks']:
                item = QListWidgetItem(task['issue'] + ' · ' + task['title'])
                item.setData(Qt.UserRole, task['id'])
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if task['id'] in checked else Qt.Unchecked)
                self.tasks.addItem(item)
            if not value['tasks']:
                self.tasks.addItem('尚无可共享的工作 issue；请先绑定或拉取 Linear issue。')
                self.tasks.item(0).setFlags(Qt.NoItemFlags)
        endpoint = value['endpoint']
        self.grant_button.setEnabled(bool(endpoint) and config['sharing'])
        self.endpoint.setText(('端口 ' + str(endpoint['port']) + '\nTLS 指纹 ' + endpoint['fingerprint']) if endpoint else '共享服务未开启或正在准备。')
        selected = self.grants.currentItem()
        selected = selected.data(0, Qt.UserRole) if selected else None
        self.grants.clear()
        for grant in value['grants']:
            item = QTreeWidgetItem([grant['label'], '工作 issue ＋ 完整日报周报' if grant.get('full_reports') else '仅工作 issue', '已撤销' if grant['revoked'] else '有效'])
            item.setData(0, Qt.UserRole, grant['id'])
            self.grants.addTopLevelItem(item)
            if grant['id'] == selected:
                self.grants.setCurrentItem(item)
        self.notice.setText(value['notice'])

    def configure(self):
        value = self.current()
        if value['sharing'] and QMessageBox.question(self, '确认工作 issue 范围',
            '仅所选工作 issue 的日报／周报摘要、人工进度和 Linear 状态快照可供已授权评估员读取。完整报告需另外授权。保存？',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        def save():
            result = self.client.configure(**value)
            self.loaded = value
            return result
        self.perform(save)

    def new_grant(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('为评估员创建独立授权')
        dialog.resize(520, 260)
        layout = box(dialog, margins=(20, 20, 20, 20))
        name = QLineEdit()
        name.setPlaceholderText('接收授权的评估员姓名')
        layout.addWidget(name)
        reports = QCheckBox('另外授权读取完整日报、周报（含 Demo、日常等内容）')
        layout.addWidget(reports)
        layout.addWidget(label('完整报告单独供管理员查看，不自动纳入 issue 统计或模型问答。授权只显示一次，请通过私密渠道交给该评估员。', 'muted', True))
        controls = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        if dialog.exec_() != QDialog.Accepted:
            return
        result = self.perform(lambda: self.client.grant(name.text(), reports.isChecked()))
        if not result:
            return
        token_dialog = QDialog(self)
        token_dialog.setWindowTitle('交给评估员的授权 · 请勿公开')
        token_dialog.resize(650, 430)
        layout = box(token_dialog, margins=(16, 16, 16, 16))
        layout.addWidget(label('请核对姓名、范围和证书指纹；管理员可通过 LAN Mesh 发现本机地址。', 'muted', True))
        text = QPlainTextEdit(json.dumps(result, ensure_ascii=False, indent=2))
        text.setReadOnly(True)
        layout.addWidget(text, 1)
        layout.addWidget(button('复制授权', 'outline', callback=lambda: QApplication.clipboard().setText(text.toPlainText())))
        layout.addWidget(button('关闭', 'outline', callback=token_dialog.accept))
        token_dialog.exec_()
        text.clear()
        token_dialog.deleteLater()

    def revoke(self):
        item = self.grants.currentItem()
        if item and QMessageBox.question(self, '撤销授权', '阻止该评估员后续读取？已接收的离线副本无法远程删除。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.perform(lambda: self.client.revoke(item.data(0, Qt.UserRole)))

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)


def open_journal_sharing(owner):
    # The report panel belongs to CollaborationDialog; its owner is the main app.
    main = owner.owner
    dialog = getattr(main, 'journal_sharing_dialog', None)
    if dialog is None:
        dialog = JournalSharingDialog(main, main.runtime.journal_share)
        main.journal_sharing_dialog = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
