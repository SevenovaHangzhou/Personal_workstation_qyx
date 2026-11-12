"""Native employee sharing, evidence questions and an auditable local issue ledger."""
import json
from datetime import datetime, date, time, timedelta
from PyQt5.QtCore import Qt, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)


def action(text, callback, parent=None):
    button = QPushButton(text, parent)
    button.setObjectName('outline')
    button.clicked.connect(lambda checked=False: callback())
    return button


def note(text):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.PlainText)
    widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return widget


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QTableWidget.NoEditTriggers)
    widget.setSelectionBehavior(QTableWidget.SelectRows)
    widget.setSelectionMode(QTableWidget.SingleSelection)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


def fill(widget, rows):
    selected = widget.item(widget.currentRow(), 0)
    selected = selected.data(Qt.UserRole) if selected else None
    widget.setRowCount(len(rows))
    for row, (identifier, values) in enumerate(rows):
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            if col == 0:
                item.setData(Qt.UserRole, identifier)
            widget.setItem(row, col, item)
        if selected == identifier:
            widget.selectRow(row)


def selection(widget):
    item = widget.item(widget.currentRow(), 0)
    return item.data(Qt.UserRole) if item else None


def choices(widget, values):
    old = widget.currentData()
    widget.blockSignals(True)
    widget.clear()
    for title, value in values:
        widget.addItem(title, value)
    index = widget.findData(old)
    widget.setCurrentIndex(max(0, index))
    widget.blockSignals(False)


class IssueUpdateDialog(QDialog):
    def __init__(self, parent, issue, current, sources):
        super().__init__(parent)
        self.setWindowTitle('记录进展 · ' + issue['identifier'])
        self.resize(760, 670)
        self.setMinimumSize(620, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(note('据共享日志整理本机 issue 进展，保留编辑人、来源哈希与修改历史。不做验收，也不写回 Linear。'))
        self.fields = {}
        for key, title in [('summary', '进展摘要'), ('blockers', '阻碍与待核实'), ('next_step', '下一步')]:
            layout.addWidget(note(title))
            editor = QPlainTextEdit(current.get(key, ''))
            editor.setMaximumHeight(100)
            self.fields[key] = editor
            layout.addWidget(editor, 1)
        layout.addWidget(note('引用当前版本日志（至少选一份；正文更新后需重新确认）'))
        self.sources = QListWidget()
        self.sources.setMinimumHeight(100)
        for source in sources:
            item = QListWidgetItem(source['name']+' · '+source['date']+' · '+source['kind'])
            item.setData(Qt.UserRole, {k:source[k] for k in ('id', 'digest', 'employee_id')})
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            same = any(s['id']==source['id'] and s['digest']==source['digest'] for s in current.get('sources', []))
            item.setCheckState(Qt.Checked if not current.get('sources') or same else Qt.Unchecked)
            self.sources.addItem(item)
        layout.addWidget(self.sources, 1)
        controls = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)

    def values(self):
        return {**{k:w.toPlainText().strip() for k,w in self.fields.items()}, 'sources': [self.sources.item(i).data(Qt.UserRole)
                for i in range(self.sources.count()) if self.sources.item(i).checkState()==Qt.Checked]}


class WeightsDialog(QDialog):
    def __init__(self, parent, issue, issues, employees):
        super().__init__(parent)
        self.setWindowTitle('核对并设置子 Issue 权重 · '+issue['identifier'])
        self.resize(900, 610)
        self.setMinimumSize(680, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(note('按直接子 issue 的 Linear 完成状态计算。各层权重分别归一化，最终叶节点只计一次。缺少子项资料时显示待估；本系统不做验收。'))
        self.grid = QTableWidget(0, 5)
        self.grid.setHorizontalHeaderLabels(['子 Issue', '相对权重', '备注人员', '说明', 'Linear 快照状态'])
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.grid.horizontalHeader().setStretchLastSection(True)
        self.employees = employees
        existing = {i['id']:i for i in issue.get('items', [])}
        known = {i['id']:i for i in issues if i['parent_id']==issue['id']}
        for child in sorted(set(existing)|set(known)):
            saved, current = existing.get(child, {}), known.get(child, {})
            self.add(child, current.get('identifier') or saved.get('title') or child, saved, current)
        layout.addWidget(self.grid, 1)
        row = QHBoxLayout()
        row.addWidget(action('补入尚未同步的子 Issue', self.manual_child))
        row.addWidget(action('移除选中项', lambda:self.grid.removeRow(self.grid.currentRow()) if self.grid.currentRow()>=0 else None))
        row.addStretch()
        layout.addLayout(row)
        self.complete = QCheckBox('我已核对这份清单包含该 Issue 的全部直接子 Issue')
        layout.addWidget(self.complete)
        controls = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        controls.accepted.connect(self.submit)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)

    def add(self, identifier, title, saved=None, current=None):
        saved, current = saved or {}, current or {}
        row = self.grid.rowCount()
        self.grid.insertRow(row)
        item = QTableWidgetItem(title+' · '+current.get('title', ''))
        item.setData(Qt.UserRole, identifier)
        item.setData(Qt.UserRole+1, saved.get('title') or current.get('title') or title)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        self.grid.setItem(row, 0, item)
        self.grid.setItem(row, 1, QTableWidgetItem(str(saved.get('weight', 1))))
        employee = QComboBox()
        employee.addItem('未指定', '')
        for value in self.employees:
            employee.addItem(value['name'], value['id'])
        assigned = saved.get('assignee_id') or current.get('owner_person_id', '')
        if assigned and employee.findData(assigned)<0:
            employee.addItem(assigned, assigned)
        employee.setCurrentIndex(max(0,employee.findData(assigned)))
        self.grid.setCellWidget(row, 2, employee)
        self.grid.setItem(row, 3, QTableWidgetItem(saved.get('description', '')))
        state = QTableWidgetItem(current.get('linear_state_type') or '缺少共享资料')
        state.setFlags(state.flags() & ~Qt.ItemIsEditable)
        self.grid.setItem(row, 4, state)

    def manual_child(self):
        value, accepted = QInputDialog.getText(self, '补入子 Issue', '填写 Linear 子 Issue UUID（不是 EE-123 编号）；缺少资料时保持待估')
        if accepted:
            from evaluation.protocol import valid_uuid
            if not valid_uuid(value.strip()):
                QMessageBox.warning(self, '格式无效', '请输入完整的小写 UUID。')
                return
            self.add(value.strip(), value.strip())

    def values(self):
        return [dict(id=self.grid.item(r,0).data(Qt.UserRole),title=self.grid.item(r,0).data(Qt.UserRole+1),
            weight=float(self.grid.item(r,1).text()), assignee_id=self.grid.cellWidget(r,2).currentData(),
            description=self.grid.item(r,3).text()) for r in range(self.grid.rowCount())]

    def submit(self):
        if not self.complete.isChecked():
            QMessageBox.warning(self, '请先核对子项', '不能把局部共享清单当作完整项目进度。')
            return
        self.accept()


class EvaluationDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.client, self.tasks = owner.runtime.evaluation, owner.tasks
        self.setWindowTitle('日志评估 · 星序')
        self.resize(1160, 790)
        self.setMinimumSize(850, 580)
        self.snapshot_data = {}
        self.cached = {}
        self.initialized = False
        layout = QVBoxLayout(self)
        layout.addWidget(note('员工授权共享 · 日志副本保存在本机 · Linear 只读'))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.build_share()
        self.build_peers()
        self.build_query()
        self.build_ledger()
        self.build_reports()
        from evaluation_report_ui import CompleteReportsPanel
        self.complete_report_panel = CompleteReportsPanel(self)
        self.tabs.addTab(self.complete_report_panel, '完整日报与周报')
        from evaluation_roster_ui import RosterPanel
        self.roster_panel = RosterPanel(self)
        self.tabs.addTab(self.roster_panel, '员工与设备')
        self.status = note('')
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(action('关闭', self.close))
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def page(self, title, scroll=False):
        host = QWidget()
        layout = QVBoxLayout(host)
        if scroll:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setWidget(host)
            self.tabs.addTab(area, title)
        else:
            self.tabs.addTab(host, title)
        return layout

    def perform(self, callback):
        try:
            result = callback()
            self.refresh()
            return result
        except (ValueError, OSError, TypeError, KeyError) as exc:
            QMessageBox.warning(self, '暂未完成', str(exc))
            return None

    def build_share(self):
        layout = self.page('我的共享', True)
        form = QFormLayout()
        self.name, self.department = QLineEdit(), QLineEdit()
        self.sharing = QCheckBox('启用组织共享（仅下方勾选的工作 issue）')
        self.evaluator = QCheckBox('启用评估端（接收员工授权后同步）')
        self.root = QLineEdit()
        root_row = QHBoxLayout()
        root_row.addWidget(self.root)
        root_row.addWidget(action('选择目录', self.pick_root))
        form.addRow('姓名／员工标识', self.name)
        form.addRow('部门', self.department)
        form.addRow('汇报资料根目录', root_row)
        form.addRow(self.sharing)
        form.addRow(self.evaluator)
        layout.addLayout(form)
        self.shared_tasks = QListWidget()
        self.shared_tasks.setMinimumHeight(120)
        self.shared_tasks.setMaximumHeight(220)
        layout.addWidget(self.shared_tasks)
        row = QHBoxLayout()
        row.addWidget(action('保存共享设置', self.save))
        row.addWidget(action('打开选中任务汇报目录', self.open_report_folder))
        row.addStretch()
        layout.addLayout(row)
        self.endpoint = note('')
        layout.addWidget(self.endpoint)
        layout.addWidget(note('为每个评估人员单独创建授权；请私下核对姓名和 TLS 指纹再导入。撤销授权会阻止后续读取，无法远程删除离线副本。'))
        row = QHBoxLayout()
        row.addWidget(action('创建评估端授权', self.new_grant))
        row.addWidget(action('撤销选中授权', self.revoke))
        row.addStretch()
        layout.addLayout(row)
        self.grant_reports = QCheckBox('新授权另外允许读取完整日报、周报（含 Demo 和日常内容）')
        layout.addWidget(self.grant_reports)
        self.grants = table(['接收人员', '授权 ID'])
        self.grants.setMaximumHeight(170)
        layout.addWidget(self.grants)
        layout.addStretch()

    def pick_root(self):
        folder = QFileDialog.getExistingDirectory(self, '选择汇报资料根目录', self.root.text())
        if folder:
            self.root.setText(folder)

    def save(self):
        ids = [self.shared_tasks.item(i).data(Qt.UserRole) for i in range(self.shared_tasks.count())
               if self.shared_tasks.item(i).checkState() == Qt.Checked]
        self.perform(lambda: self.client.configure(name=self.name.text(), department=self.department.text(),
             reports_root=self.root.text(), sharing=self.sharing.isChecked(), evaluator=self.evaluator.isChecked(), shared_task_ids=ids))

    def open_report_folder(self):
        item = self.shared_tasks.currentItem()
        if not item:
            return
        result = self.perform(lambda: self.client.report_folder(item.data(Qt.UserRole)))
        if result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(result['path']))

    def new_grant(self):
        name, accepted = QInputDialog.getText(self, '创建独立授权', '接收此授权的评估人员姓名')
        if not accepted:
            return
        reports = self.grant_reports.isChecked()
        if reports and QMessageBox.question(self, '另外授权完整报告', '此授权将允许该评估员读取完整日报、周报，可能包含 Demo 和日常内容。完整报告只单独展示，不用于 issue 统计或模型问答。确认？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        payload = self.perform(lambda: self.client.grant(name, reports))
        if payload:
            dialog = QDialog(self)
            dialog.setWindowTitle('交给评估人员的授权 · 请勿公开')
            dialog.resize(650, 420)
            layout = QVBoxLayout(dialog)
            layout.addWidget(note('授权只显示这一次。评估人员导入后填写员工电脑的局域网 IP，双方核对指纹。'))
            text = QPlainTextEdit(json.dumps(payload, ensure_ascii=False, indent=2))
            text.setReadOnly(True)
            layout.addWidget(text)
            layout.addWidget(action('复制授权', lambda: QApplication.clipboard().setText(text.toPlainText())))
            layout.addWidget(action('关闭', dialog.accept))
            dialog.exec_()
            text.clear()

    def revoke(self):
        identifier = selection(self.grants)
        if identifier and QMessageBox.question(self, '撤销授权', '阻止该评估端后续读取？离线副本无法远程删除。', QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.perform(lambda: self.client.revoke(identifier))

    def build_peers(self):
        layout = self.page('员工与同步')
        layout.addWidget(note('自动发现只提供地址和指纹。必须先取得员工单独授权，才能读取日志。员工离线时可查最后成功副本，已撤回范围在下次成功同步后隐藏。'))
        self.discovered = table(['姓名', '局域网地址', 'TLS 指纹'])
        self.discovered.setMaximumHeight(180)
        layout.addWidget(self.discovered)
        row = QHBoxLayout()
        row.addWidget(action('导入员工授权', self.import_peer))
        row.addWidget(action('立即同步', lambda: self.perform(self.client.sync)))
        row.addWidget(action('移除选中员工', self.remove_peer))
        row.addStretch()
        layout.addLayout(row)
        self.peers = table(['员工', '部门', '相关 Issue', '日志数', '最后成功同步', '连接状态'])
        layout.addWidget(self.peers, 1)

    def import_peer(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('导入员工授权')
        dialog.resize(680, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(note('粘贴员工端生成的授权，在安全渠道核对姓名和指纹。连接仅接受局域网 HTTPS。'))
        payload = QPlainTextEdit()
        payload.setPlaceholderText('粘贴完整授权 JSON')
        layout.addWidget(payload)
        address = QLineEdit()
        found = selection(self.discovered)
        found = next((p for p in self.snapshot_data.get('discovered', []) if p['employee_id'] == found), None)
        if found:
            address.setText(found['address'])
        address.setPlaceholderText('员工端局域网 IPv4，例如 192.168.100.42')
        layout.addWidget(address)
        def submit():
            try:
                data = json.loads(payload.toPlainText())
                data['url'] = 'https://' + address.text().strip() + ':' + str(int(data['port']))
                fingerprint = data['fingerprint']
                scope = '工作 issue ＋ 完整日报周报' if 'full_reports' in data.get('capabilities', []) else '仅工作 issue 日志与进度'
                if QMessageBox.question(dialog, '核对连接', f"员工：{data['name']}\n地址：{data['url']}\nTLS 指纹：{fingerprint}\n读取范围：{scope}\n\n已通过员工本人核对？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
                self.client.add_peer(data)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                QMessageBox.warning(dialog, '授权无效', str(exc))
                return
            payload.clear()
            dialog.accept()
            self.refresh()
        controls = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        controls.accepted.connect(submit)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        dialog.exec_()

    def remove_peer(self):
        employee = selection(self.peers)
        if employee and QMessageBox.question(self, '移除连接', '停止同步并从查询范围隐藏该员工？本机历史副本保留。', QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.perform(lambda: self.client.remove_peer(employee))

    def build_query(self):
        layout = self.page('日志问答')
        row = QHBoxLayout()
        self.employee, self.issue = QComboBox(), QComboBox()
        self.employee.setMinimumContentsLength(8)
        self.issue.setMinimumContentsLength(10)
        self.issue.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        row.addWidget(self.employee, 1)
        row.addWidget(self.issue, 1)
        date_toggle = action('日期范围', lambda: self.date_fields.setVisible(not self.date_fields.isVisible()))
        row.addWidget(date_toggle)
        layout.addLayout(row)
        self.date_fields = QWidget()
        row = QHBoxLayout(self.date_fields)
        row.setContentsMargins(0, 0, 0, 0)
        self.start, self.end = QLineEdit(), QLineEdit()
        self.start.setPlaceholderText('起始 YYYY-MM-DD（可选）')
        self.end.setPlaceholderText('截至 YYYY-MM-DD（可选）')
        row.addWidget(self.start)
        row.addWidget(self.end)
        layout.addWidget(self.date_fields)
        self.date_fields.hide()
        self.question = QPlainTextEdit()
        self.question.setPlaceholderText('例如：所选时间范围内，各 issue 有哪些进展、阻碍和待跟进事项？')
        self.question.setMaximumHeight(70)
        layout.addWidget(self.question)
        row = QHBoxLayout()
        self.local_button = action('本地检索与统计', lambda: self.ask(False))
        self.ai_button = action('Codex 问答', lambda: self.ask(True))
        self.cancel_button = action('停止', lambda: self.perform(self.client.cancel))
        row.addWidget(self.local_button)
        row.addWidget(self.ai_button)
        row.addWidget(self.cancel_button)
        row.addStretch()
        row.addWidget(action('复制答复／更新草稿', lambda: QApplication.clipboard().setText(self.answer.toPlainText())))
        layout.addLayout(row)
        result_tabs = QTabWidget()
        self.answer, self.sources = QPlainTextEdit(), QPlainTextEdit()
        self.answer.setReadOnly(True)
        self.sources.setReadOnly(True)
        self.answer.setPlaceholderText('本地检索不调用模型。Codex 问答由你主动发送，回答不自动改变进度或 Linear。')
        result_tabs.addTab(self.answer, '答复')
        result_tabs.addTab(self.sources, '证据来源')
        layout.addWidget(result_tabs, 1)
        self.query_notice = note('')
        layout.addWidget(self.query_notice)

    def ask(self, model):
        if model and QMessageBox.question(self, '发送所选证据给 Codex', '将本次检索片段和代码统计发送给系统当前 Codex 配置的模型。模型不执行命令，也不更新 Linear。继续？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        selected = self.employee.currentData() or ''
        person = selected[7:] if selected.startswith('person:') else ''
        self.perform(lambda: self.client.ask(question=self.question.toPlainText(), model=model,
            employee_id='' if person else selected, person_id=person, issue_id=self.issue.currentData() or '',
            start=self.date_bound(self.start.text()), end=self.date_bound(self.end.text(), end=True)))

    @staticmethod
    def date_bound(text, end=False):
        text = text.strip()
        if len(text) == 10:
            day = date.fromisoformat(text) + (timedelta(days=1) if end else timedelta())
            return datetime.combine(day, time.min).astimezone().isoformat()
        return text

    def build_ledger(self):
        layout = self.page('进度台账')
        layout.addWidget(note('按已核对的子 Issue 清单及 Linear 完成状态加权，日志补充进展与阻碍。状态来自同步快照；缺少子项资料时显示待估。本系统不做验收或评分。'))
        self.issues = table(['Issue', '工作内容', '日志贡献者', 'Linear 状态', '开发进度', '资料覆盖', '最早依据日志', 'Linear 负责人'])
        self.issues.itemSelectionChanged.connect(self.show_items)
        layout.addWidget(self.issues, 1)
        row = QHBoxLayout()
        row.addWidget(action('记录进展', self.edit_update))
        row.addWidget(action('设置子 Issue 权重', self.edit_weights))
        row.addWidget(action('查看修改历史', self.show_audit))
        row.addStretch()
        layout.addLayout(row)
        self.issue_notes = QPlainTextEdit()
        self.issue_notes.setReadOnly(True)
        layout.addWidget(self.issue_notes, 1)

    def selected_issue(self):
        identifier = selection(self.issues)
        return next((i for i in self.snapshot_data.get('progress', {}).get('issues', []) if i['id'] == identifier), None)

    def show_items(self):
        issue = self.selected_issue()
        current = next((u for u in self.snapshot_data.get('ledger', {}).get('updates', []) if issue and u['issue_id']==issue['id']), None)
        if not current:
            self.issue_notes.setPlainText('选择 issue 后可记录进展，保存时须引用当前日志证据。')
            return
        text = '进展摘要\n'+current['summary']+'\n\n阻碍与待核实\n'+current['blockers']+'\n\n下一步\n'+current['next_step']
        text += '\n\n记录人：'+current['editor']+' · '+current['updated_at']+'\n来源：'+str(len(current['sources']))+' 份日志'
        if current.get('source_versions_changed'):
            text += '\n\n引用日志已更新；此处保留原记录，请对照新版重新核对。'
        self.issue_notes.setPlainText(text)

    def edit_update(self):
        issue = self.selected_issue()
        if not issue:
            return
        revision = self.snapshot_data['ledger']['revision']
        current = next((u for u in self.snapshot_data['ledger']['updates'] if u['issue_id']==issue['id']), {})
        dialog = IssueUpdateDialog(self, issue, current, self.snapshot_data['issue_sources'].get(issue['id'], []))
        if dialog.exec_() == QDialog.Accepted:
            self.perform(lambda: self.client.save_issue_update(issue_id=issue['id'], **dialog.values(), confirmed=True, expected_revision=revision))

    def edit_weights(self):
        issue = self.selected_issue()
        if not issue:
            return
        revision = self.snapshot_data['progress']['revision']
        dialog = WeightsDialog(self, issue, self.snapshot_data['progress']['issues'], self.snapshot_data['roster']['employees'])
        if dialog.exec_() == QDialog.Accepted:
            self.perform(lambda: self.client.configure_progress(issue_id=issue['id'],items=dialog.values(),confirmed=True,expected_revision=revision))

    def show_audit(self):
        issue = self.selected_issue()
        if not issue:
            return
        dialog = QDialog(self)
        history = self.perform(lambda: self.client.issue_history(issue['id']))
        if history is None:
            return
        dialog.setWindowTitle('进展修改记录')
        dialog.resize(700, 500)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(json.dumps(history, ensure_ascii=False, indent=2))
        text.setReadOnly(True)
        layout.addWidget(text)
        layout.addWidget(action('关闭', dialog.accept))
        dialog.exec_()

    def build_reports(self):
        layout = self.page('汇报资料')
        layout.addWidget(note('仅在线读取明确共享任务的汇报目录。可预览 UTF-8 文本、Markdown、CSV、JSON；图片和其他文档仅列文件信息，不自动下载。'))
        self.report_task = QComboBox()
        self.report_task.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.report_task.setMinimumContentsLength(18)
        layout.addWidget(self.report_task)
        row = QHBoxLayout()
        row.addWidget(action('读取在线文件列表', lambda: self.read_report()))
        row.addWidget(action('预览选中文本', lambda: self.read_report(selection(self.files))))
        row.addStretch()
        layout.addLayout(row)
        self.files = table(['文件', '大小', '预览'])
        layout.addWidget(self.files, 1)
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)
        layout.addWidget(self.report_text, 2)
        self.report_notice = note('')
        layout.addWidget(self.report_notice)

    def read_report(self, path=None):
        value = self.report_task.currentData()
        if value:
            self.perform(lambda: self.client.reports(employee_id=value[0], task_id=value[1], path=path))

    def refresh(self):
        try:
            snapshot = self.client.snapshot()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return
        self.snapshot_data = snapshot
        self.complete_report_panel.refresh(snapshot)
        self.roster_panel.refresh(snapshot)
        config = snapshot['config']
        if not self.initialized:
            self.name.setText(config['name'])
            self.department.setText(config['department'])
            self.root.setText(config['reports_root'])
            self.sharing.setChecked(config['sharing'])
            self.evaluator.setChecked(config['evaluator'])
            self.initialized = True
        tasks = [t for t in self.tasks.tasks if t['category'] == 'work' and (t.get('linear_issue') or {}).get('id')]
        key = [(t['id'], t['title'], t['linear_issue']['identifier']) for t in tasks]
        if self.cached.get('tasks') != key:
            checked = {self.shared_tasks.item(i).data(Qt.UserRole) for i in range(self.shared_tasks.count()) if self.shared_tasks.item(i).checkState() == Qt.Checked} if 'tasks' in self.cached else set(config['shared_task_ids'])
            self.shared_tasks.clear()
            for t in tasks:
                item = QListWidgetItem(t['linear_issue']['identifier'] + ' · ' + t['title'])
                item.setData(Qt.UserRole, t['id'])
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if t['id'] in checked else Qt.Unchecked)
                self.shared_tasks.addItem(item)
            self.cached['tasks'] = key
        endpoint = snapshot.get('endpoint')
        self.endpoint.setText(('共享端口：'+str(endpoint['port'])+' · TLS 指纹：'+endpoint['fingerprint']) if endpoint else '共享服务未开启或正在连接。')
        self.update_table('grants', self.grants, [(g['id'], [g['label'], g['id']]) for g in snapshot['grants']])
        self.update_table('found', self.discovered, [(d['employee_id'], [d['name'], str(d['address'])+':'+str(d['port']), d['fingerprint']]) for d in snapshot['discovered']])
        employees = {e['id']: e for e in snapshot['employees']}
        issues = snapshot['progress']['issues']
        self.update_table('peers', self.peers, [(p['employee_id'], [p['name'], p['department'], sum(p['employee_id'] in i['employee_ids'] and not i.get('placeholder') for i in issues), employees.get(p['employee_id'], {}).get('record_count', 0), employees.get(p['employee_id'], {}).get('last_synced') or '尚未成功同步', employees.get(p['employee_id'], {}).get('error') or '可查询最后副本']) for p in config['peers']])
        roster = snapshot['roster']
        people = {person['id']:person for person in roster['employees']}
        choice_key = [(e['id'], e['name']) for e in snapshot['employees']], [(i['id'], i['identifier']) for i in issues], snapshot['report_tasks'], roster['revision']
        if self.cached.get('choices') != choice_key:
            person_choices = [(e['name']+' · '+e['id'][:8]+('（已停用）' if not e['active'] else ''), 'person:'+e['id']) for e in roster['employees']]
            bound_devices = {d['device_id'] for d in roster['devices']}
            device_choices = [(e['name']+' · '+('设备 ' if e['id'] in bound_devices else '未归档设备 ')+e['id'][:8],e['id']) for e in snapshot['employees']]
            choices(self.employee, [('全部已授权来源', '')]+person_choices+device_choices)
            choices(self.issue, [('全部已授权 Issue', '')]+[(i['identifier']+' · '+i['title'], i['id']) for i in issues])
            choices(self.report_task, [(r['issue']+' · '+r['title'], (r['employee_id'], r['task_id'])) for r in snapshot['report_tasks']])
            self.cached['choices'] = choice_key
        rows = []
        for issue in issues:
            coverage = issue['coverage']
            percent = str(issue['percent'])+'%' if issue['percent'] is not None else '待估'
            if issue.get('progress_state') == 'canceled':
                percent = '已取消'
            state = {'completed':'已完成','started':'进行中','unstarted':'未开始','backlog':'待排期','triage':'待分派','canceled':'已取消'}.get(issue.get('linear_state_type'), '未知')
            contributors = [people[i]['name']+' · '+i[:8] for i in issue.get('contributor_person_ids', []) if i in people]
            contributors += ['未归档设备 '+i[:8] for i in issue.get('unassigned_device_ids', [])]
            owner = people.get(issue.get('owner_person_id'), {}).get('name') or issue.get('owner_notice', '负责人待核对')
            rows.append((issue['id'], [issue['identifier'], issue['title'], '、'.join(contributors), state, percent,
                str(coverage['estimated_leaf_count'])+'/'+str(coverage['total_leaf_count'])+' 叶项有资料', issue.get('asof') or issue.get('last_report', ''), owner]))
        changed = self.update_table('issues', self.issues, rows)
        if changed or self.cached.get('revision') != (snapshot['progress']['revision'], snapshot['ledger']['revision']):
            self.show_items()
            self.cached['revision'] = (snapshot['progress']['revision'], snapshot['ledger']['revision'])
        query = snapshot['query']
        self.local_button.setEnabled(config['evaluator'] and not query['busy'])
        self.ai_button.setEnabled(config['evaluator'] and not query['busy'])
        self.cancel_button.setEnabled(query['busy'])
        self.query_notice.setText(('处理中 · ' if query['busy'] else '') + query['notice'])
        self.update_text('answer', self.answer, query['answer'])
        self.update_text('sources', self.sources, '\n\n'.join(s.get('label', '')+' · '+s.get('date', '')+' · '+s.get('record_id', '')+'\n'+s.get('excerpt', '')+'\nSHA256 '+s.get('digest', '') for s in query['sources']))
        reports = snapshot['reports']
        self.update_table('files', self.files, [(f['path'], [f['path'], str(f['size'])+' B', '可预览' if f['previewable'] else '仅元数据']) for f in reports['files']])
        self.update_text('report_text', self.report_text, reports['text'])
        self.report_notice.setText(reports['notice'])
        self.status.setText(snapshot['notice']+' · '+str(snapshot['record_count'])+' 份授权日志'+(' · 正在同步' if snapshot['syncing'] else ''))

    def update_table(self, key, widget, rows):
        if self.cached.get(key) == rows:
            return False
        self.cached[key] = rows
        fill(widget, rows)
        return True

    def update_text(self, key, widget, text):
        if self.cached.get(key) != text:
            widget.setPlainText(text)
            self.cached[key] = text

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        # Online attachments are never retained by a closed viewer.
        self.report_text.clear()
        self.cached.pop('report_text', None)
        try:
            self.client.clear_reports()
        except (ValueError, OSError):
            pass
        super().hideEvent(event)
