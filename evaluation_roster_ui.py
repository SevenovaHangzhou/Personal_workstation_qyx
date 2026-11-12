"""Explicit local employee, device and Linear-account administration."""
import json
import re
import uuid

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QPlainTextEdit, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget)

from evaluation_ui import action, fill, note, selection, table


STATE_NAMES = {'completed': '已完成', 'started': '进行中', 'unstarted': '未开始',
               'backlog': '待办', 'canceled': '已取消', 'cancelled': '已取消', 'triage': '待分类'}


def employee_name(employee):
    return employee.get('name', '') + (' · ' + employee['department'] if employee.get('department') else '')


def employee_picker(employees, current=''):
    picker = QComboBox()
    picker.addItem('请选择员工', '')
    for employee in employees:
        if employee.get('active', True) or employee['id'] == current:
            # IDs stay attached even when names are identical.
            picker.addItem(employee_name(employee) + ' · ' + employee['id'][:8] +
                ('（已停用）' if not employee.get('active', True) else ''), employee['id'])
            picker.setItemData(picker.count() - 1, '员工 ID：' + employee['id'], Qt.ToolTipRole)
    picker.setCurrentIndex(max(0, picker.findData(current)))
    picker.setMinimumContentsLength(12)
    picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    return picker


class RosterForm(QDialog):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 500)
        self.setMinimumSize(420, 330)
        layout = QVBoxLayout(self)
        area = QScrollArea()
        area.setWidgetResizable(True)
        host = QWidget()
        self.body = QVBoxLayout(host)
        self.form = QFormLayout()
        self.form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.body.addLayout(self.form)
        area.setWidget(host)
        layout.addWidget(area, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('保存')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def submit(self):
        try:
            self.values()
        except ValueError as exc:
            QMessageBox.warning(self, '请检查填写内容', str(exc))
            return
        self.accept()


class EmployeeDialog(RosterForm):
    def __init__(self, parent, employee=None):
        employee = employee or {}
        super().__init__(parent, '编辑员工' if employee else '新建员工')
        self.employee_id = employee.get('id', '')
        self.name = QLineEdit(employee.get('name', ''))
        self.name.setMaxLength(120)
        self.department = QLineEdit(employee.get('department', ''))
        self.department.setMaxLength(120)
        self.active = QCheckBox('员工在职／启用')
        self.active.setChecked(employee.get('active', True))
        self.form.addRow('姓名', self.name)
        self.form.addRow('部门', self.department)
        self.form.addRow(self.active)
        if self.employee_id:
            self.body.addWidget(note('员工 ID：' + self.employee_id))
        self.body.addWidget(note('员工身份由独立 ID 区分。同名人员不会合并；设备和 Linear 账号需要分别明确绑定。'))
        self.body.addStretch()

    def values(self):
        name = self.name.text().strip()
        if not name:
            raise ValueError('请填写员工姓名。')
        return dict(employee_id=self.employee_id, name=name,
                    department=self.department.text().strip(), active=self.active.isChecked())


class DeviceBindingDialog(RosterForm):
    def __init__(self, parent, device, employees, binding=None):
        super().__init__(parent, '归档设备')
        binding = binding or {}
        self.device_id = device['device_id']
        self.employee = employee_picker(employees, binding.get('employee_id', ''))
        self.label = QLineEdit(binding.get('label', ''))
        self.label.setMaxLength(120)
        self.form.addRow('已配对设备', note(device.get('name', '') + '\n' + self.device_id))
        self.form.addRow('归属员工', self.employee)
        self.form.addRow('设备备注', self.label)
        self.body.addWidget(note('请核对设备与员工。归档只管理日志的员工归属，不代替共享授权。'))
        self.body.addStretch()

    def values(self):
        if not self.employee.currentData():
            raise ValueError('请明确选择设备归属员工。')
        return dict(device_id=self.device_id, employee_id=self.employee.currentData(), label=self.label.text().strip())


class AccountDialog(RosterForm):
    def __init__(self, parent, employees, account=None, candidates=None, employee_id=''):
        account = account or {}
        super().__init__(parent, '编辑 Linear 账号' if account else '绑定 Linear 账号')
        self.account_id = account.get('id', '')
        self.employee = employee_picker(employees, account.get('employee_id', employee_id))
        self.candidate = QComboBox()
        self.candidate.addItem('手动填写 Workspace 与用户 UUID', None)
        seen = set()
        for candidate in candidates or []:
            identity = (candidate.get('workspace', ''), candidate.get('user_id', ''))
            if not all(identity) or identity in seen:
                continue
            seen.add(identity)
            self.candidate.addItem(identity[0] + ' · ' + identity[1] +
                (' · ' + candidate['issue_identifier'] if candidate.get('issue_identifier') else ''), identity)
        self.candidate.setMinimumContentsLength(16)
        self.candidate.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.workspace = QLineEdit(account.get('workspace', ''))
        self.workspace.setMaxLength(100)
        self.workspace.setPlaceholderText('Linear URL 中的 workspace slug')
        self.user_id = QLineEdit(account.get('user_id', ''))
        self.user_id.setMaxLength(36)
        self.user_id.setPlaceholderText('完整用户 UUID，不是姓名或邮箱')
        self.display_name = QLineEdit(account.get('display_name', ''))
        self.display_name.setMaxLength(120)
        self.candidate.currentIndexChanged.connect(self.choose_candidate)
        self.form.addRow('归属员工', self.employee)
        self.form.addRow('已同步的负责人账号', self.candidate)
        self.form.addRow('Workspace', self.workspace)
        self.form.addRow('Linear 用户 UUID', self.user_id)
        self.form.addRow('账号备注名（可选）', self.display_name)
        if not seen:
            self.body.addWidget(note('暂无已同步的负责人候选。旧版来源未提供负责人字段时，可手动填写完整用户 UUID。'))
        self.body.addWidget(note('候选仅填入账号标识，仍需明确选择员工。此处无需 API key，不按姓名推断账号归属。'))
        self.body.addStretch()

    def choose_candidate(self):
        candidate = self.candidate.currentData()
        if candidate:
            self.workspace.setText(candidate[0])
            self.user_id.setText(candidate[1])

    def values(self):
        if not self.employee.currentData():
            raise ValueError('请明确选择账号归属员工。')
        workspace = self.workspace.text().strip()
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,98}[A-Za-z0-9])?', workspace):
            raise ValueError('Workspace 请填写 Linear URL 中的 slug，不填写完整网址。')
        try:
            user_id = str(uuid.UUID(self.user_id.text().strip()))
        except ValueError:
            raise ValueError('请填写完整的 Linear 用户 UUID。') from None
        return dict(account_id=self.account_id, employee_id=self.employee.currentData(),
                    workspace=workspace, user_id=user_id, display_name=self.display_name.text().strip())


class RosterPanel(QWidget):
    """An evaluator-only editor refreshed from the owner's versioned snapshot."""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.client = owner, owner.client
        self.roster = {}
        self.snapshot = {}
        self.cached = {}
        layout = QVBoxLayout(self)
        layout.addWidget(note('明确归档员工、设备与 Linear 账号。进度以 Issue 为单位，日志贡献与负责人分开记录。'))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.build_employees()
        self.build_devices()
        self.build_accounts()
        self.notice = note('')
        layout.addWidget(self.notice)
        self.refresh(getattr(owner, 'snapshot_data', {}))

    def page(self, title):
        host = QWidget()
        layout = QVBoxLayout(host)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(host)
        self.tabs.addTab(area, title)
        return layout

    def build_employees(self):
        layout = self.page('员工名册')
        row = QHBoxLayout()
        self.new_employee = action('新建员工', lambda: self.edit_employee(False))
        self.edit_employee_button = action('编辑员工', self.edit_employee)
        self.active_button = action('停用员工', self.toggle_employee)
        for button in (self.new_employee, self.edit_employee_button, self.active_button):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.employees = table(['姓名', '部门', '状态', '设备', 'Linear 账号', '日志'])
        self.employees.setMinimumHeight(110)
        self.employees.setMaximumHeight(160)
        self.employees.itemSelectionChanged.connect(self.show_employee)
        layout.addWidget(self.employees)
        self.employee_summary = note('选择员工查看其 Issue 与日志。')
        layout.addWidget(self.employee_summary)
        self.issues = table(['Issue', '工作内容', '关系', '开发进度', 'Linear 状态', '最近日志'])
        self.issues.setMinimumHeight(120)
        self.issues.itemSelectionChanged.connect(self.show_issue)
        layout.addWidget(self.issues, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(100)
        self.detail.setMaximumHeight(180)
        layout.addWidget(self.detail)

    def build_devices(self):
        layout = self.page('设备归档')
        layout.addWidget(note('只可归档已明确配对的设备。未归档的来源保持独立，不根据设备名称或员工同名合并。'))
        row = QHBoxLayout()
        self.bind_device_button = action('归档／转移设备', self.bind_device)
        self.unbind_device_button = action('解除设备归档', self.unbind_device)
        row.addWidget(self.bind_device_button)
        row.addWidget(self.unbind_device_button)
        row.addStretch()
        layout.addLayout(row)
        self.devices = table(['来源设备', '归属员工', '设备备注', '局域网发现', '最近同步'])
        self.devices.itemSelectionChanged.connect(self.refresh_actions)
        self.devices.setMinimumHeight(180)
        layout.addWidget(self.devices, 1)
        self.device_detail = note('选择设备后可核对其完整来源 ID。')
        layout.addWidget(self.device_detail)

    def build_accounts(self):
        layout = self.page('Linear 账号')
        layout.addWidget(note('用 Workspace 与用户 UUID 明确关联负责人。账号备注用于识别，不参与姓名匹配。'))
        row = QHBoxLayout()
        self.new_account = action('绑定 Linear 账号', lambda: self.edit_account(False))
        self.edit_account_button = action('编辑账号绑定', self.edit_account)
        self.remove_account_button = action('解除账号绑定', self.remove_account)
        for button in (self.new_account, self.edit_account_button, self.remove_account_button):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.accounts = table(['员工', 'Workspace', 'Linear 用户 UUID', '账号备注'])
        self.accounts.itemSelectionChanged.connect(self.refresh_actions)
        self.accounts.setMinimumHeight(180)
        layout.addWidget(self.accounts, 1)

    def roster_employees(self):
        return self.roster.get('employees', [])

    def employee(self, identity):
        return next((row for row in self.roster_employees() if row['id'] == identity), None)

    def summary(self):
        identity = selection(self.employees)
        return next((row for row in self.roster.get('summaries', []) if row['id'] == identity), {})

    def mutate(self, method, values):
        return self.owner.perform(lambda: method(**values, expected_revision=self.roster.get('revision', 0)))

    def refresh(self, snapshot):
        self.snapshot = snapshot or {}
        self.roster = self.snapshot.get('roster') or {}
        # Rebuild tables only when roster content changes, retaining selections
        # while the owner's background polling refreshes other pages.
        employees = {row['id']: row for row in self.roster_employees()}
        summaries = {row['id']: row for row in self.roster.get('summaries', [])}
        employee_rows = []
        for employee in self.roster_employees():
            summary = summaries.get(employee['id'], {})
            employee_rows.append((employee['id'], [employee['name'], employee.get('department', ''),
                '启用' if employee.get('active', True) else '已停用', len(summary.get('devices', [])),
                len(summary.get('accounts', [])), summary.get('authored_record_count', 0)]))
        self.fill_changed(self.employees, 'employees', employee_rows)
        for row in range(self.employees.rowCount()):
            cell = self.employees.item(row, 0)
            cell.setToolTip(cell.text() + '\n员工 ID：' + cell.data(Qt.UserRole))
        bindings = {row['device_id']: row for row in self.roster.get('devices', [])}
        connections = {row['device_id']: row for row in self.roster.get('connections', [])}
        device_rows = []
        for identity in sorted(set(bindings) | set(connections)):
            binding, connection = bindings.get(identity, {}), connections.get(identity, {})
            employee = employees.get(binding.get('employee_id'))
            device_rows.append((identity, [connection.get('name') or identity,
                employee_name(employee) if employee else '未归档', binding.get('label', ''),
                '已发现' if connection.get('online') else '未发现',
                connection.get('last_synced') or '—']))
        self.fill_changed(self.devices, 'devices', device_rows)
        for row in range(self.devices.rowCount()):
            cell = self.devices.item(row, 0)
            cell.setToolTip(cell.text() + '\n来源设备 ID：' + cell.data(Qt.UserRole))
        account_rows = [(row['id'], [employee_name(employees[row['employee_id']]) if row['employee_id'] in employees else '员工不存在',
            row.get('workspace', ''), row.get('user_id', ''), row.get('display_name', '')]) for row in self.roster.get('accounts', [])]
        self.fill_changed(self.accounts, 'accounts', account_rows)
        self.notice.setText('未归档设备：' + str(self.roster.get('unassigned_device_count', 0)) +
            (' · 员工与账号编辑需启用评估端' if not self.snapshot.get('config', {}).get('evaluator') else ''))
        self.show_employee()
        self.refresh_actions()

    def fill_changed(self, widget, key, rows):
        signature = json.dumps(rows, ensure_ascii=False, sort_keys=True)
        if self.cached.get(key) != signature:
            widget.blockSignals(True)
            fill(widget, rows)
            widget.blockSignals(False)
            self.cached[key] = signature

    def refresh_actions(self):
        editable = self.snapshot.get('config', {}).get('evaluator', False)
        employee = self.employee(selection(self.employees))
        self.new_employee.setEnabled(editable)
        self.edit_employee_button.setEnabled(editable and employee is not None)
        self.active_button.setEnabled(editable and employee is not None)
        self.active_button.setText('停用员工' if not employee or employee.get('active', True) else '启用员工')
        device = selection(self.devices)
        connection = next((row for row in self.roster.get('connections', []) if row['device_id'] == device), {})
        binding = next((row for row in self.roster.get('devices', []) if row['device_id'] == device), None)
        self.bind_device_button.setEnabled(bool(editable and connection.get('connected') and any(row.get('active', True) for row in self.roster_employees())))
        self.unbind_device_button.setEnabled(editable and binding is not None)
        if device:
            self.device_detail.setText('来源 ID：' + device +
                '\n授权连接：' + ('已配对' if connection.get('connected') else '未配置') +
                ' · 最近发现：' + str(connection.get('last_seen') or '暂无') +
                '（未发现不能判定设备离线）' + ('\n连接提示：' + connection['error'] if connection.get('error') else ''))
        self.new_account.setEnabled(bool(editable and any(row.get('active', True) for row in self.roster_employees())))
        selected_account = selection(self.accounts)
        self.edit_account_button.setEnabled(editable and selected_account is not None)
        self.remove_account_button.setEnabled(editable and selected_account is not None)

    def show_employee(self):
        summary = self.summary()
        employee = self.employee(selection(self.employees))
        if not employee:
            self.employee_summary.setText('选择员工查看其 Issue 与日志。')
        else:
            self.employee_summary.setText(employee_name(employee) + ' · 日志 ' + str(summary.get('authored_record_count', 0)) +
                ' 份 · 最近同步 ' + str(summary.get('last_synced') or '—'))
        issue_rows = []
        for issue in summary.get('issues', []):
            percent = str(issue['percent']) + '%' if issue.get('percent') is not None else '待估'
            if issue.get('linear_state_type') in ('canceled', 'cancelled'):
                percent = '已取消'
            issue_rows.append((issue['id'], [issue.get('identifier', ''), issue.get('title', ''),
                issue.get('relation', ''), percent, STATE_NAMES.get(issue.get('linear_state_type'), issue.get('linear_state_type') or '未知'),
                issue.get('last_report') or '—']))
        self.fill_changed(self.issues, 'employee_issues', issue_rows)
        self.show_issue()
        self.refresh_actions()

    def show_issue(self):
        summary = self.summary()
        lines = []
        issue = next((row for row in summary.get('issues', []) if row['id'] == selection(self.issues)), None)
        if issue:
            lines += [issue.get('identifier', '') + ' · ' + issue.get('title', ''),
                      '关系：' + issue.get('relation', ''), '最近日志：' + str(issue.get('last_report') or '暂无')]
            if issue.get('owner_notice'):
                lines.append('负责人说明：' + str(issue['owner_notice']))
        blockers = summary.get('blockers', [])
        if issue:
            blockers = [row for row in blockers if (row.get('issue_id') == issue['id'] if row.get('issue_id')
                        else row.get('issue_identifier') == issue.get('identifier'))]
        if blockers:
            lines.append('据日志整理的阻碍（尚需核实）：')
            lines.extend('• ' + str(row.get('issue_identifier', '')) + ' ' + str(row.get('text', '')) +
                         ('（引用日志已更新，请核对）' if row.get('source_versions_changed') else '') for row in blockers)
        elif summary:
            lines.append('当前共享日志未列出阻碍；这不等同于已核实没有阻碍。')
        text = '\n'.join(lines)
        if self.detail.toPlainText() != text:
            self.detail.setPlainText(text)

    def edit_employee(self, existing=True):
        employee = self.employee(selection(self.employees)) if existing else None
        if existing and not employee:
            return
        dialog = EmployeeDialog(self, employee)
        revision = self.roster.get('revision', 0)
        if dialog.exec_() == QDialog.Accepted:
            self.owner.perform(lambda: self.client.roster_save_employee(**dialog.values(), expected_revision=revision))

    def toggle_employee(self):
        employee = self.employee(selection(self.employees))
        if not employee:
            return
        active = not employee.get('active', True)
        if QMessageBox.question(self, '启用员工' if active else '停用员工',
            ('启用' if active else '停用') + '「' + employee['name'] + '」？历史员工、设备与日志记录会保留。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.mutate(self.client.roster_save_employee, dict(employee_id=employee['id'], name=employee['name'],
                department=employee.get('department', ''), active=active))

    def bind_device(self):
        identity = selection(self.devices)
        device = next((row for row in self.roster.get('connections', []) if row['device_id'] == identity and row.get('connected')), None)
        if not device:
            return
        binding = next((row for row in self.roster.get('devices', []) if row['device_id'] == identity), {})
        dialog = DeviceBindingDialog(self, device, self.roster_employees(), binding)
        revision = self.roster.get('revision', 0)
        if dialog.exec_() != QDialog.Accepted:
            return
        values = dialog.values()
        if binding.get('employee_id') and binding['employee_id'] != values['employee_id']:
            old, new = self.employee(binding['employee_id']), self.employee(values['employee_id'])
            message = '将设备从「' + (old['name'] if old else binding['employee_id']) + '」转移至「' + (new['name'] if new else values['employee_id']) + '」？\n历史日志保留原员工归属，仅新日志跟随新员工。'
            if QMessageBox.question(self, '确认设备转移', message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        self.owner.perform(lambda: self.client.roster_bind_device(**values, expected_revision=revision))

    def unbind_device(self):
        identity = selection(self.devices)
        if not any(row['device_id'] == identity for row in self.roster.get('devices', [])):
            return
        if QMessageBox.question(self, '解除设备归档', '解除后，新日志显示为未归档。历史日志仍保留原员工归属。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.mutate(self.client.roster_unbind_device, dict(device_id=identity))

    def edit_account(self, existing=True):
        account = next((row for row in self.roster.get('accounts', []) if row['id'] == selection(self.accounts)), None) if existing else None
        if existing and not account:
            return
        dialog = AccountDialog(self, self.roster_employees(), account, self.roster.get('account_candidates', []), selection(self.employees) or '')
        revision = self.roster.get('revision', 0)
        if dialog.exec_() == QDialog.Accepted:
            self.owner.perform(lambda: self.client.roster_save_account(**dialog.values(), expected_revision=revision))

    def remove_account(self):
        identity = selection(self.accounts)
        if not any(row['id'] == identity for row in self.roster.get('accounts', [])):
            return
        if QMessageBox.question(self, '解除账号绑定', '解除本机员工与该 Linear 账号的关联？历史变更仍会保留。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.mutate(self.client.roster_remove_account, dict(account_id=identity))
