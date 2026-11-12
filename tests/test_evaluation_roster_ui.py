"""Native roster behavior with explicit identities and no runtime/network calls."""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_ui import APP
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton, QWidget
from display_settings import DisplayController, DisplayPreferences
from evaluation_roster_ui import AccountDialog, DeviceBindingDialog, EmployeeDialog, RosterPanel
from evaluation_ui import selection


def identity():
    return str(uuid.uuid4())


class Owner(QWidget):
    def __init__(self, snapshot):
        super().__init__()
        self.snapshot_data = snapshot
        self.client = MagicMock()
        self.invocations = 0

    def perform(self, callback):
        self.invocations += 1
        return callback()


class RosterUiTests(unittest.TestCase):
    def setUp(self):
        self.first, self.second, self.inactive = identity(), identity(), identity()
        self.device, self.unassigned, self.unpaired = identity(), identity(), identity()
        self.account, self.user = identity(), identity()
        self.issue = identity()
        self.snapshot = dict(config={'evaluator': True}, employees=[{'id': self.device, 'name': '设备名称不是员工身份'}], roster=dict(
            revision=7,
            employees=[dict(id=self.first, name='同名工程师', department='电气', active=True),
                       dict(id=self.second, name='同名工程师', department='机械', active=True),
                       dict(id=self.inactive, name='已停用员工', department='研发', active=False)],
            devices=[dict(device_id=self.device, employee_id=self.first, label='办公室电脑', bound_at='2026-11-11')],
            connections=[dict(device_id=self.device, name='工程师电脑', connected=True, online=True,
                              last_seen='2026-11-11T19:10:00+08:00', last_synced='2026-11-11T19:00:00+08:00'),
                         dict(device_id=self.unassigned, name='同名工程师', connected=True, online=False),
                         dict(device_id=self.unpaired, name='仅发现设备', connected=False, online=True)],
            accounts=[dict(id=self.account, employee_id=self.first, workspace='sevenova', user_id=self.user, display_name='电气账号')],
            account_candidates=[dict(workspace='sevenova', user_id=self.user, issue_identifier='EE-101')],
            summaries=[dict(id=self.first, name='同名工程师', department='电气', active=True,
                devices=[{'device_id': self.device}], accounts=[{'id': self.account}], authored_record_count=5,
                contributed_issue_ids=[self.issue], assigned_issue_ids=[self.issue], last_report='2026-11-11T22:00:00+08:00',
                last_synced='2026-11-11T19:00:00+08:00', blockers=[dict(issue_identifier='EE-101', text='等待实机夹具')],
                issues=[dict(id=self.issue, identifier='EE-101', title='控制接口与实机联调', percent=60.0,
                    linear_state_type='started', relation='负责人', owner_notice='已明确绑定负责人账号',
                    last_report='2026-11-11T22:00:00+08:00')]),
                dict(id=self.second, name='同名工程师', department='机械', active=True, devices=[], accounts=[],
                     authored_record_count=0, contributed_issue_ids=[], assigned_issue_ids=[], blockers=[], issues=[])],
            history=[], unassigned_device_count=2))
        self.owner = Owner(self.snapshot)
        self.panel = RosterPanel(self.owner)
        self.owner.resize(1180, 780)
        self.owner.show()
        self.panel.resize(1160, 720)
        self.panel.show()
        APP.processEvents()

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        self.owner.close()
        self.owner.deleteLater()
        APP.processEvents()

    def select(self, widget, identity):
        row = next(row for row in range(widget.rowCount()) if widget.item(row, 0).data(Qt.UserRole) == identity)
        widget.selectRow(row)
        APP.processEvents()
        return row

    def test_roster_identity_is_explicit_and_unassigned_sources_stay_separate(self):
        self.assertEqual(self.panel.employees.rowCount(), 3)
        self.assertEqual(self.panel.devices.rowCount(), 3)
        self.assertEqual([self.panel.tabs.tabText(i) for i in range(3)], ['员工名册', '设备归档', 'Linear 账号'])
        self.assertNotIn(self.device, [self.panel.employees.item(i, 0).data(Qt.UserRole) for i in range(3)])
        unassigned = self.select(self.panel.devices, self.unassigned)
        self.assertEqual(self.panel.devices.item(unassigned, 1).text(), '未归档')
        self.assertEqual(self.panel.devices.item(unassigned, 3).text(), '未发现')
        self.assertTrue(self.panel.bind_device_button.isEnabled())
        self.assertIn('未发现不能判定设备离线', self.panel.device_detail.text())
        self.select(self.panel.devices, self.unpaired)
        self.assertFalse(self.panel.bind_device_button.isEnabled())
        with patch('evaluation_roster_ui.DeviceBindingDialog') as form:
            self.panel.bind_device()
            form.assert_not_called()
        self.owner.client.roster_bind_device.assert_not_called()

    def test_details_show_issue_progress_state_contribution_and_cited_blockers_without_employee_rate(self):
        self.select(self.panel.employees, self.first)
        self.assertEqual(self.panel.issues.rowCount(), 1)
        self.assertEqual(self.panel.issues.item(0, 3).text(), '60.0%')
        self.assertEqual(self.panel.issues.item(0, 4).text(), '进行中')
        self.assertEqual(self.panel.issues.item(0, 2).text(), '负责人')
        self.panel.issues.selectRow(0)
        APP.processEvents()
        self.assertIn('等待实机夹具', self.panel.detail.toPlainText())
        self.assertIn('据日志整理的阻碍（尚需核实）', self.panel.detail.toPlainText())
        self.assertIn('最近日志：2026-11-11', self.panel.detail.toPlainText())
        self.assertIn('日志 5 份', self.panel.employee_summary.text())
        self.assertNotIn('%', self.panel.employee_summary.text())
        self.assertFalse(any('评分' in button.text() or '验收' in button.text() for button in self.panel.findChildren(QPushButton)))
        self.select(self.panel.employees, self.second)
        self.assertEqual(self.panel.issues.rowCount(), 0)
        self.assertNotIn('等待实机夹具', self.panel.detail.toPlainText())

    def test_disabled_evaluator_keeps_roster_readable_and_disables_mutations(self):
        self.select(self.panel.employees, self.first)
        self.select(self.panel.devices, self.device)
        self.select(self.panel.accounts, self.account)
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['config']['evaluator'] = False
        self.panel.refresh(snapshot)
        self.assertEqual(self.panel.employees.rowCount(), 3)
        self.assertEqual(self.panel.issues.rowCount(), 1)
        buttons = [self.panel.new_employee, self.panel.edit_employee_button, self.panel.active_button,
                   self.panel.bind_device_button, self.panel.unbind_device_button, self.panel.new_account,
                   self.panel.edit_account_button, self.panel.remove_account_button]
        self.assertTrue(all(not button.isEnabled() for button in buttons))
        self.assertIn('需启用评估端', self.panel.notice.text())
        self.assertEqual(self.owner.invocations, 0)

    def test_employee_form_and_refresh_keep_drafts_and_use_captured_revision(self):
        self.select(self.panel.employees, self.first)
        editor = EmployeeDialog(self.panel, self.snapshot['roster']['employees'][0])
        try:
            editor.name.setText('尚未保存的姓名')
            for revision in (8, 9):
                updated = copy.deepcopy(self.snapshot)
                updated['roster']['revision'] = revision
                updated['roster']['employees'][0]['department'] = '后台更新部门'
                self.panel.refresh(updated)
            self.assertEqual(selection(self.panel.employees), self.first)
            self.assertEqual(editor.name.text(), '尚未保存的姓名')
            self.owner.client.roster_save_employee.assert_not_called()
        finally:
            editor.close()
        form = MagicMock()
        form.values.return_value = dict(employee_id=self.first, name='确认姓名', department='电气', active=True)
        def accept_while_polling():
            refreshed = copy.deepcopy(self.snapshot)
            refreshed['roster']['revision'] = 99
            self.panel.refresh(refreshed)
            return QDialog.Accepted
        form.exec_.side_effect = accept_while_polling
        with patch('evaluation_roster_ui.EmployeeDialog', return_value=form):
            self.panel.edit_employee()
        self.owner.client.roster_save_employee.assert_called_once_with(**form.values.return_value, expected_revision=9)

    def test_employee_deactivation_requires_confirmation_and_keeps_identity(self):
        self.select(self.panel.employees, self.first)
        with patch('evaluation_roster_ui.QMessageBox.question', return_value=QMessageBox.No):
            self.panel.toggle_employee()
        self.owner.client.roster_save_employee.assert_not_called()
        with patch('evaluation_roster_ui.QMessageBox.question', return_value=QMessageBox.Yes):
            self.panel.toggle_employee()
        self.owner.client.roster_save_employee.assert_called_once_with(employee_id=self.first,
            name='同名工程师', department='电气', active=False, expected_revision=7)

    def test_device_transfer_requires_explicit_history_confirmation_and_unbind_is_versioned(self):
        self.select(self.panel.devices, self.device)
        form = MagicMock()
        form.exec_.return_value = QDialog.Accepted
        form.values.return_value = dict(device_id=self.device, employee_id=self.second, label='调拨电脑')
        with patch('evaluation_roster_ui.DeviceBindingDialog', return_value=form), \
                patch('evaluation_roster_ui.QMessageBox.question', return_value=QMessageBox.No) as confirm:
            self.panel.bind_device()
            self.assertIn('历史日志保留原员工归属，仅新日志跟随新员工', confirm.call_args.args[2])
        self.owner.client.roster_bind_device.assert_not_called()
        with patch('evaluation_roster_ui.DeviceBindingDialog', return_value=form), \
                patch('evaluation_roster_ui.QMessageBox.question', return_value=QMessageBox.Yes):
            self.panel.bind_device()
        self.owner.client.roster_bind_device.assert_called_once_with(**form.values.return_value, expected_revision=7)
        with patch('evaluation_roster_ui.QMessageBox.question', return_value=QMessageBox.Yes):
            self.panel.unbind_device()
        self.owner.client.roster_unbind_device.assert_called_once_with(device_id=self.device, expected_revision=7)

    def test_account_candidates_fill_identifiers_only_and_still_require_employee(self):
        form = AccountDialog(self.panel, self.snapshot['roster']['employees'],
                             candidates=self.snapshot['roster']['account_candidates'])
        try:
            self.assertEqual(form.workspace.text(), '')
            self.assertEqual(form.user_id.text(), '')
            form.candidate.setCurrentIndex(1)
            self.assertEqual(form.workspace.text(), 'sevenova')
            self.assertEqual(form.user_id.text(), self.user)
            self.assertEqual(form.employee.currentData(), '')
            with self.assertRaisesRegex(ValueError, '明确选择'):
                form.values()
            form.employee.setCurrentIndex(form.employee.findData(self.second))
            self.assertEqual(form.values()['employee_id'], self.second)
            self.assertEqual(form.values()['user_id'], self.user)
            self.assertEqual(form.values()['display_name'], '')
            form.workspace.setText('https://linear.app/sevenova')
            with self.assertRaisesRegex(ValueError, 'slug'):
                form.values()
            form.workspace.setText('sevenova')
            form.user_id.setText('同名工程师')
            with self.assertRaisesRegex(ValueError, '完整'):
                form.values()
            self.owner.client.roster_save_account.assert_not_called()
        finally:
            form.close()

    def test_account_save_and_remove_are_explicit_and_versioned(self):
        self.select(self.panel.employees, self.first)
        form = MagicMock()
        form.values.return_value = dict(account_id='', employee_id=self.first, workspace='sevenova',
                                       user_id=self.user, display_name='人工核对账号')
        form.exec_.return_value = QDialog.Rejected
        with patch('evaluation_roster_ui.AccountDialog', return_value=form):
            self.panel.edit_account(False)
        self.owner.client.roster_save_account.assert_not_called()
        form.exec_.return_value = QDialog.Accepted
        with patch('evaluation_roster_ui.AccountDialog', return_value=form):
            self.panel.edit_account(False)
        self.owner.client.roster_save_account.assert_called_once_with(**form.values.return_value, expected_revision=7)
        self.select(self.panel.accounts, self.account)
        with patch('evaluation_roster_ui.QMessageBox.question', return_value=QMessageBox.Yes):
            self.panel.remove_account()
        self.owner.client.roster_remove_account.assert_called_once_with(account_id=self.account, expected_revision=7)

    def test_empty_candidates_explain_old_sources_and_same_names_remain_distinct(self):
        form = AccountDialog(self.panel, self.snapshot['roster']['employees'])
        device = DeviceBindingDialog(self.panel, self.snapshot['roster']['connections'][0], self.snapshot['roster']['employees'])
        try:
            self.assertTrue(any('旧版来源未提供负责人字段' in label.text() for label in form.findChildren(QLabel)))
            values = [device.employee.itemData(index) for index in range(device.employee.count())]
            self.assertIn(self.first, values)
            self.assertIn(self.second, values)
            self.assertNotIn(self.inactive, values)
            self.assertEqual(device.employee.currentData(), '')
        finally:
            form.close()
            device.close()

    def test_small_window_large_stylesheet_font_all_pages_and_forms_remain_accessible(self):
        original_font, original_style = QFont(APP.font()), APP.styleSheet()
        forms = []
        try:
            DisplayController(APP).apply(DisplayPreferences(font_size=18))
            self.panel.resize(810, 460)
            self.assertEqual(APP.font().pixelSize(), 18)
            self.select(self.panel.employees, self.first)
            for index in range(self.panel.tabs.count()):
                self.panel.tabs.setCurrentIndex(index)
                APP.processEvents()
                area = self.panel.tabs.widget(index)
                self.assertTrue(area.isVisible())
                self.assertGreater(area.viewport().height(), 100)
                self.assertLessEqual(self.panel.width(), 810)
            forms = [EmployeeDialog(self.panel), AccountDialog(self.panel, self.snapshot['roster']['employees'],
                     candidates=self.snapshot['roster']['account_candidates']), DeviceBindingDialog(self.panel,
                     self.snapshot['roster']['connections'][0], self.snapshot['roster']['employees'])]
            for form in forms:
                form.resize(700, 450)
                form.show()
                APP.processEvents()
                self.assertLessEqual(form.width(), 700)
                self.assertLessEqual(form.height(), 450)
                save = next(button for button in form.findChildren(QPushButton) if button.text() == '保存')
                self.assertTrue(save.isVisible())
        finally:
            for form in forms:
                form.close()
            APP.setFont(original_font)
            APP.setStyleSheet(original_style)
            APP.processEvents()


if __name__ == '__main__':
    unittest.main()
