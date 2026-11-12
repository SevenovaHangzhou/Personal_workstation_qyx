"""Isolated native UI checks using the actual work-profile Yomk runtime."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_ui import APP
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QMessageBox, QPushButton, QWidget
from display_settings import DisplayController, DisplayPreferences
from evaluation_ui import EvaluationDialog, IssueUpdateDialog, WeightsDialog, selection
from model import make_task
from runtime import MomoRuntime
from test_evaluation_service import journal
from test_integrations import issue


class EvaluationUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        with patch('services.EvaluationService.EvaluationService.start'):
            self.runtime = MomoRuntime(self.root/'tasks.json', profile='work', auto_tick=False).start()
        self.tasks = self.runtime.tasks
        self.tasks.clear_samples()
        self.work = self.add_work('明确关联issue的工作事项', 1)
        self.unlinked = self.tasks.add(make_task('未关联issue', 'work'))
        self.demo = self.tasks.add(make_task('不纳入共享的Demo', 'demo'))
        self.life = self.tasks.add(make_task('不纳入共享的日常任务', 'life'))
        self.owner = QWidget()
        self.owner.runtime = self.runtime
        self.owner.tasks = self.tasks
        self.dialog = EvaluationDialog(self.owner)
        self.dialog.show()
        APP.processEvents()
        self.dialog.timer.stop()
        self.employee = str(uuid.uuid4())

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.owner.close()
        self.owner.deleteLater()
        APP.processEvents()
        self.runtime.close(apply_update=False)
        self.temp.cleanup()

    def add_work(self, title, number):
        task = self.tasks.add(make_task(title, 'work'))
        linked = dict(issue(number), id=str(uuid.uuid4()))
        self.tasks.attach_issue(task['id'], linked)
        return self.tasks.get(task['id'])

    def seed(self):
        row = journal(self.employee)
        service = self.runtime.evaluation_service
        service.state['peers'].append(dict(employee_id=self.employee, name='测试员工', department='电气',
                                           url='https://127.0.0.1:1', fingerprint='0'*64))
        service.replicas.sync(self.employee, [row], [dict(id=row['id'], digest=row['digest'])], '测试员工', '电气')
        self.runtime.evaluation.configure(name='评估人员', evaluator=True)
        self.dialog.refresh()
        return row

    def wait_query(self):
        deadline = time.monotonic() + 3
        while self.runtime.evaluation.snapshot()['query']['busy'] and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(.005)
        self.assertFalse(self.runtime.evaluation.snapshot()['query']['busy'], '问答线程未按时完成')
        self.dialog.refresh()
        APP.processEvents()

    def test_default_private_native_interface_only_lists_eligible_work_issues(self):
        config = self.runtime.evaluation.snapshot()['config']
        self.assertFalse(config['sharing'])
        self.assertFalse(config['evaluator'])
        self.assertFalse(self.dialog.sharing.isChecked())
        self.assertFalse(self.dialog.evaluator.isChecked())
        self.assertEqual(self.dialog.shared_tasks.count(), 1)
        self.assertEqual(self.dialog.shared_tasks.item(0).data(Qt.UserRole), self.work['id'])
        self.assertFalse(self.dialog.local_button.isEnabled())
        self.assertFalse(self.dialog.ai_button.isEnabled())
        self.assertIsNone(self.runtime.learning)
        self.assertIsNone(self.runtime.focus)
        self.assertEqual([self.dialog.tabs.tabText(index) for index in range(self.dialog.tabs.count())],
                         ['我的共享', '员工与同步', '日志问答', '进度台账', '汇报资料', '完整日报与周报', '员工与设备'])
        buttons = [button.text() for button in self.dialog.findChildren(QPushButton)]
        self.assertFalse(any('课程' in text or '原始聊天' in text or '打开聊天' in text for text in buttons))
        self.assertTrue(self.dialog.answer.isReadOnly())
        self.assertTrue(self.dialog.sources.isReadOnly())
        self.assertTrue(self.dialog.report_text.isReadOnly())

    def test_complete_report_preview_is_separate_and_hidden_when_grant_removed(self):
        import base64
        import hashlib
        from evaluation.report_archive import version
        self.seed()
        service = self.runtime.evaluation_service
        service.state['peers'][0]['full_reports'] = True
        text = '# 日报\n仅完整报告可见的 Demo 资料'
        raw = text.encode()
        files = [dict(path='日报/自动/2026-11-11.md', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())]
        class Source:
            def report_manifest(inner): return dict(version=version(files), files=files)
            def report(inner, revision, path, offset): return dict(data=base64.b64encode(raw[offset:]).decode())
        service.complete_reports.pull(self.employee, Source(), threading.Event(), lambda:True)
        self.dialog.refresh()
        panel = self.dialog.complete_report_panel
        self.dialog.tabs.setCurrentWidget(panel)
        APP.processEvents()
        self.assertTrue(panel.preview.isReadOnly())
        self.assertEqual(panel.preview.toPlainText(), text)
        self.assertNotIn('仅完整报告可见', str(service.visible_records()))
        service.state['peers'][0]['full_reports'] = False
        self.dialog.refresh()
        self.assertEqual(panel.preview.toPlainText(), '')
        self.assertEqual(panel.sources.topLevelItemCount(), 0)

    def test_timer_refresh_preserves_unsaved_form_and_shared_task_drafts(self):
        self.dialog.name.setText('尚未保存的姓名')
        self.dialog.department.setText('研发草稿')
        self.dialog.root.setText(str(self.root/'report-draft'))
        self.dialog.sharing.setChecked(True)
        self.dialog.shared_tasks.item(0).setCheckState(Qt.Checked)
        self.dialog.question.setPlainText('未发送的问题')
        self.dialog.start.setText('2026-11-10')
        for _ in range(3):
            self.dialog.refresh()
        self.assertEqual(self.dialog.name.text(), '尚未保存的姓名')
        self.assertEqual(self.dialog.department.text(), '研发草稿')
        self.assertTrue(self.dialog.sharing.isChecked())
        self.assertEqual(self.dialog.shared_tasks.item(0).checkState(), Qt.Checked)
        self.assertEqual(self.dialog.question.toPlainText(), '未发送的问题')
        self.assertEqual(self.dialog.start.text(), '2026-11-10')
        # A changed task list forces the actual list rebuild path, not just a
        # cached refresh. Existing checks must survive while new tasks stay off.
        other = self.add_work('后台新增工作事项', 2)
        self.tasks.update(self.work['id'], title='后台改名的工作事项')
        self.dialog.refresh()
        states = {self.dialog.shared_tasks.item(index).data(Qt.UserRole): self.dialog.shared_tasks.item(index).checkState()
                  for index in range(self.dialog.shared_tasks.count())}
        self.assertEqual(states[self.work['id']], Qt.Checked)
        self.assertEqual(states[other['id']], Qt.Unchecked)
        self.assertEqual(self.dialog.name.text(), '尚未保存的姓名')
        self.assertEqual(self.runtime.evaluation.snapshot()['config']['name'], '')
        self.assertEqual(self.runtime.evaluation.snapshot()['config']['shared_task_ids'], [])

    def test_no_acceptance_controls_or_endpoint_are_available(self):
        from clients import call
        self.seed()
        buttons = [button.text() for button in self.dialog.findChildren(QPushButton)]
        self.assertFalse(any('验收' in text or '评分' in text for text in buttons))
        self.assertFalse(hasattr(self.runtime.evaluation, 'accept_progress'))
        before = self.runtime.evaluation.snapshot()['progress']
        with self.assertRaisesRegex(OSError, 'function not found'):
            call('/EvaluationService/accept_progress', {'confirmed': True, 'accepted': True})
        after = self.runtime.evaluation.snapshot()['progress']
        self.assertEqual(before, after)
        self.assertTrue(all(issue['percent'] is None and issue['basis'] == 'linear_subissues_weighted'
                            for issue in after['issues']))

    def test_weights_dialog_requires_full_list_confirmation_and_does_not_save_on_its_own(self):
        from evaluation.protocol import record_digest
        parent = self.seed()
        child = journal(self.employee)
        child.update(parent_issue_id=parent['issue_id'], issue_identifier='EE-102',
                     issue_url='https://linear.app/test/issue/EE-102/child-api')
        child['digest'] = record_digest(child)
        self.runtime.evaluation_service.replicas.sync(self.employee, [child],
            [{'id': parent['id'], 'digest': parent['digest']}, {'id': child['id'], 'digest': child['digest']}], '测试员工', '电气')
        snapshot = self.runtime.evaluation.snapshot()
        issues = snapshot['progress']['issues']
        parent_issue = next(issue for issue in issues if issue['id'] == parent['issue_id'])
        weights = WeightsDialog(self.dialog, parent_issue, issues, snapshot['employees'])
        try:
            self.assertEqual(weights.grid.rowCount(), 1)
            self.assertEqual(weights.grid.item(0, 0).data(Qt.UserRole), child['issue_id'])
            with patch('evaluation_ui.QMessageBox.warning') as warning:
                weights.submit()
                warning.assert_called_once()
            self.assertEqual(weights.result(), weights.Rejected)
            weights.grid.item(0, 1).setText('25.5')
            weights.grid.item(0, 3).setText('接口实现与联调')
            self.assertEqual(self.runtime.evaluation.snapshot()['progress']['revision'], 0)
            weights.complete.setChecked(True)
            weights.submit()
            self.assertEqual(weights.result(), weights.Accepted)
            values = weights.values()
            self.assertEqual(values[0]['id'], child['issue_id'])
            self.assertEqual(values[0]['weight'], 25.5)
            self.assertEqual(values[0]['description'], '接口实现与联调')
            self.assertNotIn('accepted', values[0])
            self.assertEqual(self.runtime.evaluation.snapshot()['progress']['revision'], 0)
            # A not-yet-shared child can be explicitly declared by its real
            # UUID. It must remain unknown, rather than being silently dropped
            # or counted complete from a similarly named visible issue.
            missing = str(uuid.uuid4())
            weights.add(missing, 'EE-999')
            values = weights.values()
            self.assertEqual({item['id'] for item in values}, {child['issue_id'], missing})
            self.assertEqual(weights.grid.item(1, 4).text(), '缺少共享资料')
            self.runtime.evaluation.configure_progress(issue_id=parent['issue_id'], items=values,
                                                       confirmed=True, expected_revision=0)
            progress = self.runtime.evaluation.snapshot()['progress']
            configured = next(issue for issue in progress['issues'] if issue['id'] == parent['issue_id'])
            self.assertIsNone(configured['percent'])
            self.assertIn(missing, configured['coverage']['missing_issue_ids'])
        finally:
            weights.deleteLater()

    def test_real_linear_child_states_show_weighted_progress_and_missing_child_stays_unestimated(self):
        from evaluation.protocol import record_digest
        parent = self.seed()
        children = []
        for number, state in ((102, 'completed'), (103, 'started')):
            child = journal(self.employee)
            child.update(parent_issue_id=parent['issue_id'], issue_identifier=f'EE-{number}',
                         issue_url=f'https://linear.app/test/issue/EE-{number}/child-api',
                         linear_state_type=state)
            child['digest'] = record_digest(child)
            children.append(child)
        self.runtime.evaluation_service.replicas.sync(self.employee, children,
            [{'id': row['id'], 'digest': row['digest']} for row in [parent, *children]], '测试员工', '电气')
        items = [dict(id=child['issue_id'], title=child['task_title'], weight=weight,
                      assignee_id=self.employee, description='显式核对子 issue 范围')
                 for child, weight in zip(children, (60, 40))]
        self.runtime.evaluation.configure_progress(issue_id=parent['issue_id'], items=items,
                                                   confirmed=True, expected_revision=0)
        self.dialog.tabs.setCurrentIndex(3)
        self.dialog.refresh()
        def parent_row():
            return next(index for index in range(self.dialog.issues.rowCount())
                        if self.dialog.issues.item(index, 0).data(Qt.UserRole) == parent['issue_id'])
        self.assertEqual(self.dialog.issues.item(parent_row(), 3).text(), '已完成')
        self.assertEqual(self.dialog.issues.item(parent_row(), 4).text(), '60.0%')
        snapshot = self.runtime.evaluation.snapshot()['progress']
        estimated = next(issue for issue in snapshot['issues'] if issue['id'] == parent['issue_id'])
        self.assertEqual(estimated['basis'], 'linear_subissues_weighted')
        self.assertEqual(estimated['percent'], 60.0)
        self.assertEqual(estimated['asof'], parent['period_end'])
        self.assertEqual(len(estimated['effective_leaves']), 2)
        missing = str(uuid.uuid4())
        items.append(dict(id=missing, title='尚未共享的子 issue', weight=10,
                          assignee_id='', description='等待对方共享资料'))
        self.runtime.evaluation.configure_progress(issue_id=parent['issue_id'], items=items,
                                                   confirmed=True, expected_revision=snapshot['revision'])
        self.dialog.refresh()
        self.assertEqual(self.dialog.issues.item(parent_row(), 4).text(), '待估')
        unknown = next(issue for issue in self.runtime.evaluation.snapshot()['progress']['issues']
                       if issue['id'] == parent['issue_id'])
        self.assertIsNone(unknown['percent'])
        self.assertIn(missing, unknown['coverage']['missing_issue_ids'])
        self.assertFalse(any('验收' in button.text() for button in self.dialog.findChildren(QPushButton)))

    def test_issue_update_dialog_keeps_explicit_source_versions_and_does_not_save_implicitly(self):
        row = self.seed()
        snapshot = self.runtime.evaluation.snapshot()
        issue = snapshot['progress']['issues'][0]
        sources = [{'id': row['id'], 'digest': row['digest'], 'employee_id': self.employee,
                    'name': row['name'], 'date': row['period_end'], 'kind': row['kind']}]
        update = IssueUpdateDialog(self.dialog, issue, {}, sources)
        try:
            update.fields['summary'].setPlainText('接口实现已交付，实机结果待核对')
            update.fields['blockers'].setPlainText('缺少实机测试记录')
            update.fields['next_step'].setPlainText('补充联调证据')
            values = update.values()
            self.assertEqual(values['sources'], [{'id': row['id'], 'digest': row['digest'], 'employee_id': self.employee}])
            self.assertEqual(values['blockers'], '缺少实机测试记录')
            self.assertEqual(self.runtime.evaluation.issue_history(row['issue_id']), [])
            update.sources.item(0).setCheckState(Qt.Unchecked)
            self.assertEqual(update.values()['sources'], [])
        finally:
            update.reject()
            update.deleteLater()

    def test_issue_selection_survives_new_log_and_query_result_refresh(self):
        row = self.seed()
        self.dialog.tabs.setCurrentIndex(3)
        self.dialog.issues.selectRow(0)
        self.assertEqual(selection(self.dialog.issues), row['issue_id'])
        self.runtime.evaluation_service.query['answer'] = '本地新答复'
        self.dialog.refresh()
        self.assertEqual(selection(self.dialog.issues), row['issue_id'])
        second = journal(self.employee)
        second['issue_identifier'] = 'EE-102'
        second['issue_url'] = 'https://linear.app/test/issue/EE-102/second-api'
        from evaluation.protocol import record_digest
        second['digest'] = record_digest(second)
        self.runtime.evaluation_service.replicas.sync(self.employee, [second],
            [{'id': row['id'], 'digest': row['digest']}, {'id': second['id'], 'digest': second['digest']}], '测试员工', '电气')
        self.dialog.refresh()
        self.assertEqual(self.dialog.issues.rowCount(), 2)
        self.assertEqual(selection(self.dialog.issues), row['issue_id'])
        self.assertEqual(self.dialog.answer.toPlainText(), '本地新答复')

    def test_local_query_worker_keeps_tabs_and_name_edit_responsive(self):
        row = self.seed()
        entered, release = threading.Event(), threading.Event()
        from evaluation.insights import render_local_answer
        def slow_answer(question, result, progress):
            entered.set()
            if not release.wait(3):
                raise TimeoutError('测试没有释放查询线程')
            return render_local_answer(question, result, progress)
        try:
            with patch('services.EvaluationService.render_local_answer', side_effect=slow_answer):
                self.dialog.tabs.setCurrentIndex(2)
                self.dialog.question.setPlainText('接口工作进展')
                self.dialog.local_button.click()
                self.assertTrue(entered.wait(1))
                self.dialog.refresh()
                self.assertFalse(self.dialog.local_button.isEnabled())
                self.assertFalse(self.dialog.ai_button.isEnabled())
                self.assertTrue(self.dialog.cancel_button.isEnabled())
                self.dialog.tabs.setCurrentIndex(0)
                self.dialog.name.setText('问答期间继续编辑')
                APP.processEvents()
                self.assertEqual(self.dialog.tabs.currentIndex(), 0)
                self.assertEqual(self.dialog.name.text(), '问答期间继续编辑')
                self.tasks.update(self.work['id'], note='问答期间更新任务备注')
                release.set()
                self.wait_query()
        finally:
            release.set()
        self.assertEqual(self.dialog.name.text(), '问答期间继续编辑')
        self.assertIn('[S1]', self.dialog.answer.toPlainText())
        self.assertIn(row['digest'], self.dialog.sources.toPlainText())
        self.assertTrue(self.dialog.local_button.isEnabled())
        self.assertFalse(self.dialog.cancel_button.isEnabled())

    def test_canceled_model_confirmation_does_not_start_query(self):
        self.seed()
        self.dialog.question.setPlainText('总结员工日志')
        with patch('evaluation_ui.QMessageBox.question', return_value=QMessageBox.No):
            with patch.object(self.dialog.client, 'ask', wraps=self.dialog.client.ask) as ask:
                self.dialog.ask(True)
                ask.assert_not_called()
        self.assertFalse(self.runtime.evaluation.snapshot()['query']['busy'])
        self.assertIsNone(self.runtime.evaluation_service.query_worker)

    def test_close_stops_polling_and_clears_online_attachment_text(self):
        self.dialog.timer.start()
        self.dialog.report_text.setPlainText('只存在本次在线查看中的附件内容')
        self.dialog.cached['report_text'] = self.dialog.report_text.toPlainText()
        self.dialog.close()
        APP.processEvents()
        self.assertFalse(self.dialog.timer.isActive())
        self.assertEqual(self.dialog.report_text.toPlainText(), '')
        self.assertNotIn('report_text', self.dialog.cached)
        self.assertEqual(list((self.root/'evaluation').rglob('*.md')), [])

    def test_small_window_and_large_font_keep_primary_query_controls_available(self):
        self.seed()
        previous_font, previous_style = QFont(APP.font()), APP.styleSheet()
        try:
            # Controls use stylesheet pixel sizes, so setting the dialog font
            # alone does not exercise the actual accessibility preference.
            DisplayController(APP).apply(DisplayPreferences(font_size=18))
            self.assertEqual(APP.font().pixelSize(), 18)
            self.assertNotEqual(APP.styleSheet(), previous_style)
            self.dialog.resize(850, 580)
            self.dialog.tabs.setCurrentIndex(2)
            APP.processEvents()
            self.assertLessEqual(self.dialog.width(), 850)
            self.assertLessEqual(self.dialog.height(), 580)
            self.assertTrue(self.dialog.local_button.isVisible())
            self.assertTrue(self.dialog.ai_button.isVisible())
            self.assertTrue(self.dialog.answer.isVisible())
            self.assertGreater(self.dialog.answer.height(), 30)
            self.assertGreater(self.dialog.question.height(), 30)
        finally:
            APP.setFont(previous_font)
            APP.setStyleSheet(previous_style)
            APP.processEvents()


if __name__ == '__main__':
    unittest.main()
