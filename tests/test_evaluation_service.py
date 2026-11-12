"""Real Yomk requests with isolated data and no colleague/model network calls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from evaluation.protocol import record_digest, record_id
from framework import api
from model import make_task
from runtime import MomoRuntime

ROOT = Path(__file__).resolve().parents[1]


def journal(employee):
    source = 'nightly:2026-11-11'
    task = str(uuid.uuid4())
    row = dict(id=record_id(source, task), employee_id=employee, name='测试员工', department='电气',
               source_device=employee, source_id=source, kind='nightly',
               period_start='2026-11-10T22:00:00+08:00', period_end='2026-11-11T22:00:00+08:00',
               task_id=task, task_title='共享接口开发', issue_id=str(uuid.uuid4()),
               issue_identifier='EE-101', issue_url='https://linear.app/test/issue/EE-101/shared-api',
               parent_issue_id='', linear_state_type='completed',
               summary='共享接口实现已完成100%，实机联调仍待核实；以上为员工自述。',
               evidence=[dict(id=str(uuid.uuid4()), kind='execution', text='接口单元测试已通过', source='',
                              created_at='2026-11-11T15:00:00+08:00')])
    row['digest'] = record_digest(row)
    return row


class EvaluationRuntimeBoundaryTests(unittest.TestCase):
    def test_fresh_work_and_study_default_off_do_not_open_sockets_or_load_crypto(self):
        # A new process makes lazy-import assertions independent of test order.
        program = r'''
import builtins, json, socket, sys, tempfile
from pathlib import Path
from unittest.mock import patch
from runtime import MomoRuntime
from framework import api
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'evaluation.mesh' or name == 'cryptography' or name.startswith('cryptography.'):
        raise AssertionError('disabled feature imported ' + name)
    return real_import(name, *args, **kwargs)
result = []
with tempfile.TemporaryDirectory() as temporary:
    for profile in ('work', 'study'):
        folder = Path(temporary) / profile
        with patch('builtins.__import__', side_effect=guarded_import), patch('socket.socket', side_effect=AssertionError('default-off opened socket')):
            runtime = MomoRuntime(folder/'tasks.json', profile=profile, auto_tick=False).start()
            try:
                registered = '/EvaluationService' in api.server().service_names()
                if profile == 'work':
                    snapshot = runtime.evaluation.snapshot()
                    assert not snapshot['config']['sharing'] and not snapshot['config']['evaluator']
                    assert snapshot['endpoint'] is None and snapshot['record_count'] == 0
                    assert not (folder/'evaluation/share').exists()
                else:
                    assert runtime.evaluation is None and runtime.evaluation_service is None
                result.append({'profile': profile, 'registered': registered})
            finally:
                runtime.close(apply_update=False)
assert not any(t.name.startswith('orbit-evaluation') for t in __import__('threading').enumerate())
print(json.dumps(result))
'''
        result = subprocess.run([sys.executable, '-c', program], cwd=ROOT,
                                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(next(line for line in reversed(result.stdout.splitlines()) if line.startswith('[')))
        self.assertEqual(report, [dict(profile='work', registered=True), dict(profile='study', registered=False)])


class EvaluationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # Requests still run through the actual framework; only the network loop
        # is withheld so configure/additional queries remain deterministic.
        with patch('services.EvaluationService.EvaluationService.start'):
            self.runtime = MomoRuntime(self.root/'tasks.json', profile='work', auto_tick=False).start()
        self.runtime.tasks.clear_samples()
        self.work = self.runtime.tasks.add(make_task('本机工作任务', 'work'))
        self.demo = self.runtime.tasks.add(make_task('不可组织共享的Demo', 'demo'))
        self.life = self.runtime.tasks.add(make_task('不可组织共享的日常', 'life'))
        self.service = self.runtime.evaluation_service
        self.client = self.runtime.evaluation
        self.employee = str(uuid.uuid4())

    def tearDown(self):
        self.runtime.close(apply_update=False)
        self.temp.cleanup()

    def seed(self):
        row = journal(self.employee)
        self.service.state['peers'].append(dict(employee_id=self.employee, name='测试员工', department='电气',
                                               url='https://127.0.0.1:1', fingerprint='0'*64))
        self.service.replicas.sync(self.employee, [row], [dict(id=row['id'], digest=row['digest'])], '测试员工', '电气')
        return row

    def wait_query(self):
        deadline = time.monotonic() + 3
        while self.client.snapshot()['query']['busy'] and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(self.client.snapshot()['query']['busy'], 'query worker did not finish')

    def test_configure_requires_name_and_explicit_work_scope_and_keeps_defaults_private(self):
        initial = self.client.snapshot()
        self.assertFalse(initial['config']['sharing'])
        self.assertFalse(initial['config']['evaluator'])
        self.assertFalse(initial['linear_write_enabled'])
        for setting in ({'sharing': True}, {'evaluator': True}):
            with self.assertRaisesRegex(ValueError, '姓名'):
                self.client.configure(**setting)
        for ids in ([self.demo['id']], [self.life['id']], [str(uuid.uuid4())], [self.work['id'], self.work['id']]):
            with self.assertRaises(ValueError):
                self.client.configure(name='测试姓名', shared_task_ids=ids)
        self.client.configure(name='测试姓名', department='电气', shared_task_ids=[self.work['id']])
        saved = self.client.snapshot()['config']
        self.assertEqual(saved['shared_task_ids'], [self.work['id']])
        self.assertFalse(saved['sharing'])
        self.assertEqual(self.service.export_records(), [])
        self.assertIsNone(self.service.server)

    def test_log_completion_claims_do_not_change_tasks_or_write_linear_and_no_acceptance_endpoint(self):
        self.client.configure(name='评估人员', evaluator=True)
        self.seed()
        before = copy.deepcopy(self.runtime.tasks.tasks)
        with patch('linear_support.graphql', side_effect=AssertionError('evaluation must not write Linear')):
            self.client.ask(question='完成了吗？', model=False)
            self.wait_query()
            progress = self.client.snapshot()['progress']['issues'][0]
            self.assertIsNone(progress['percent'])
            response = api.request('/EvaluationService/accept_progress', {})
            self.assertNotEqual(response.status, api.ResStatus.eOk)
        self.assertEqual(self.runtime.tasks.tasks, before)
        self.assertEqual(self.runtime.daily_service.store.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0], 0)

    def test_only_paired_employee_replicas_are_queryable_and_local_answer_is_cited(self):
        self.client.configure(name='评估人员', evaluator=True)
        row = self.seed()
        outsider = journal(str(uuid.uuid4()))
        self.service.replicas.sync(outsider['employee_id'], [outsider],
                                  [dict(id=outsider['id'], digest=outsider['digest'])], '未授权员工', '其他')
        before = copy.deepcopy(self.runtime.tasks.tasks)
        with patch('evaluation.qa.find_codex', side_effect=AssertionError('local query must not invoke Codex')):
            self.client.ask(question='共享接口的进展是什么？', model=False)
            self.wait_query()
        snapshot = self.client.snapshot()
        query = snapshot['query']
        self.assertEqual(snapshot['record_count'], 1)
        self.assertEqual(len(snapshot['employees']), 1)
        self.assertIn('本地检索结果', query['answer'])
        self.assertIn('[S1]', query['answer'])
        self.assertIn(row['digest'], query['answer'])
        self.assertNotIn(outsider['employee_id'], json.dumps(query))
        self.assertIsNone(snapshot['progress']['issues'][0]['percent'])
        self.assertEqual(self.runtime.tasks.tasks, before)

    def test_fake_model_query_is_asynchronous_cancelable_and_does_not_block_task_edits(self):
        self.client.configure(name='评估人员', evaluator=True)
        self.seed()
        entered = threading.Event()
        class Runner:
            def run(inner, question, result, progress, cancel, notice):
                entered.set()
                notice('等待测试回答')
                cancel.wait(3)
                if cancel.is_set():
                    raise InterruptedError('已停止测试回答')
                return '不应出现的回答'
        self.service.runner = Runner()
        self.client.ask(question='总结接口', model=True)
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.client.snapshot()['query']['busy'])
        self.runtime.tasks.update(self.work['id'], note='问答过程中继续办公')
        self.assertEqual(self.runtime.tasks.get(self.work['id'])['note'], '问答过程中继续办公')
        with self.assertRaisesRegex(ValueError, '已有问答'):
            self.client.ask(question='重复请求', model=True)
        self.client.cancel()
        self.wait_query()
        self.assertEqual(self.client.snapshot()['query']['answer'], '')
        self.assertIn('已停止', self.client.snapshot()['query']['notice'])

    def test_disabling_evaluator_cancels_current_answer_and_clears_visible_result(self):
        self.client.configure(name='评估人员', evaluator=True)
        self.seed()
        entered = threading.Event()
        class Runner:
            def run(inner, question, result, progress, cancel, notice):
                entered.set()
                cancel.wait(3)
                return '停用后不得展示的回答'
        self.service.runner = Runner()
        self.client.ask(question='总结接口', model=True)
        self.assertTrue(entered.wait(1))
        self.client.configure(evaluator=False)
        self.wait_query()
        snapshot = self.client.snapshot()
        self.assertEqual(snapshot['query']['answer'], '')
        self.assertEqual(snapshot['record_count'], 0)
        self.assertEqual(snapshot['query']['sources'], [])

    def test_runtime_close_cancels_and_drains_query_threads_before_unregister(self):
        self.client.configure(name='评估人员', evaluator=True)
        self.seed()
        entered = threading.Event()
        class Runner:
            def run(inner, question, result, progress, cancel, notice):
                entered.set()
                cancel.wait(3)
                raise InterruptedError('退出取消')
        self.service.runner = Runner()
        self.client.ask(question='退出时停止', model=True)
        self.assertTrue(entered.wait(1))
        worker = self.service.query_worker
        self.runtime.close(apply_update=False)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.service.closed)
        self.assertNotIn('/EvaluationService', api.server().service_names())

    def test_model_receives_only_selected_employee_and_issue_evidence_and_statistics(self):
        self.client.configure(name='评估人员', evaluator=True)
        selected = self.seed()
        other_employee = journal(str(uuid.uuid4()))
        other_employee['summary'] = 'OTHER_EMPLOYEE_CONTENT_MUST_BE_EXCLUDED'
        other_employee['digest'] = record_digest(other_employee)
        same_employee_other_issue = journal(self.employee)
        same_employee_other_issue['summary'] = 'OTHER_ISSUE_CONTENT_MUST_BE_EXCLUDED'
        same_employee_other_issue['digest'] = record_digest(same_employee_other_issue)
        self.service.state['peers'].append(dict(employee_id=other_employee['employee_id'], name='另一员工', department='其他',
                                               url='https://127.0.0.1:2', fingerprint='1'*64))
        self.service.replicas.sync(other_employee['employee_id'], [other_employee],
                                  [dict(id=other_employee['id'], digest=other_employee['digest'])], '另一员工', '其他')
        self.service.replicas.sync(self.employee, [same_employee_other_issue],
                                  [dict(id=row['id'], digest=row['digest']) for row in (selected, same_employee_other_issue)],
                                  '测试员工', '电气')
        received = {}
        class Runner:
            def run(inner, question, result, progress, cancel, notice):
                received.update(result=copy.deepcopy(result), progress=copy.deepcopy(progress))
                return '所选接口的工作记录见 [S1]。'
        self.service.runner = Runner()
        self.client.ask(question='所选接口有哪些进展？', model=True,
                        employee_id=self.employee, issue_id=selected['issue_id'])
        self.wait_query()
        serialized = json.dumps(received, ensure_ascii=False)
        self.assertNotIn('OTHER_EMPLOYEE_CONTENT', serialized)
        self.assertNotIn('OTHER_ISSUE_CONTENT', serialized)
        self.assertNotIn(other_employee['issue_id'], serialized)
        self.assertNotIn(same_employee_other_issue['issue_id'], serialized)
        self.assertEqual(received['result']['statistics']['unique_issues'], 1)
        self.assertEqual(received['result']['statistics']['unique_employees'], 1)
        self.assertEqual({row['id'] for row in received['progress']['issues']}, {selected['issue_id']})

    def test_issue_notes_require_confirmation_and_exact_current_issue_source_without_remote_writes(self):
        self.client.configure(name='评估人员', evaluator=True)
        row = self.seed()
        source = {key: row[key] for key in ('id', 'digest', 'employee_id')}
        arguments = dict(issue_id=row['issue_id'], summary='接口实现已有记录，尚需实机联调',
                         blockers='实机未就绪', next_step='补充联调记录', sources=[source], expected_revision=0)
        before = copy.deepcopy(self.runtime.tasks.tasks)
        with patch('linear_support.graphql', side_effect=AssertionError('notes must remain local')):
            with self.assertRaises(ValueError):
                self.client.save_issue_update(**arguments, confirmed=False)
            with self.assertRaises(ValueError):
                self.client.save_issue_update(**dict(arguments, issue_id=str(uuid.uuid4())), confirmed=True)
            with self.assertRaises(ValueError):
                self.client.save_issue_update(**dict(arguments, sources=[dict(source, digest='0'*64)]), confirmed=True)
            saved = self.client.save_issue_update(**arguments, confirmed=True)
            self.assertEqual(saved['revision'], 1)
            self.assertEqual(saved['editor'], '评估人员')
            self.assertEqual(saved['sources'], [source])
            self.assertEqual(self.client.issue_history(row['issue_id']), [saved])
            self.assertEqual(self.client.snapshot()['ledger']['updates'], [saved])
            # A newer source version requires a fresh review before saving again.
            revised = dict(row, summary='新证据已补充')
            revised['digest'] = record_digest(revised)
            self.service.replicas.sync(self.employee, [revised],
                                      [dict(id=revised['id'], digest=revised['digest'])], '测试员工', '电气')
            current_update = self.client.snapshot()['ledger']['updates'][0]
            self.assertTrue(current_update['source_versions_changed'])
            self.assertEqual(current_update['sources'], [source])
            self.assertTrue(self.client.issue_history(row['issue_id'])[0]['source_versions_changed'])
            with self.assertRaises(ValueError):
                self.client.save_issue_update(**dict(arguments, expected_revision=1), confirmed=True)
        self.assertEqual(self.runtime.tasks.tasks, before)
        self.assertEqual(len(self.service.ledger.history(row['issue_id'])), 1)
        self.assertEqual(self.runtime.daily_service.store.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0], 0)

    def test_parent_issue_query_includes_descendants_and_excludes_other_branches(self):
        self.client.configure(name='评估人员', evaluator=True)
        parent = self.seed()
        child, grandchild, other_parent, other_child = [journal(self.employee) for _ in range(4)]
        child['parent_issue_id'] = parent['issue_id']
        grandchild['parent_issue_id'] = child['issue_id']
        other_child['parent_issue_id'] = other_parent['issue_id']
        for index, row in enumerate((parent, child, grandchild, other_parent, other_child), 201):
            row['issue_identifier'] = 'EE-' + str(index)
            row['issue_url'] = 'https://linear.app/test/issue/' + row['issue_identifier'] + '/branch'
            row['summary'] = ('RELATED_BRANCH_' if index < 204 else 'UNRELATED_BRANCH_') + str(index)
            row['digest'] = record_digest(row)
        rows = [parent, child, grandchild, other_parent, other_child]
        self.service.replicas.sync(self.employee, rows,
                                  [dict(id=row['id'], digest=row['digest']) for row in rows], '测试员工', '电气')
        received = {}
        class Runner:
            def run(inner, question, result, progress, cancel, notice):
                received.update(result=copy.deepcopy(result), progress=copy.deepcopy(progress))
                return '所选项目及其子项的进展见 [S1]。'
        self.service.runner = Runner()
        self.client.ask(question='汇总这个父任务的全部子项', model=True,
                        employee_id=self.employee, issue_id=parent['issue_id'])
        self.wait_query()
        related_ids = {row['issue_id'] for row in (parent, child, grandchild)}
        self.assertEqual(set(received['result']['coverage']['included_issue_ids']), related_ids)
        self.assertEqual({row['issue_id'] for row in received['result']['sources']}, related_ids)
        self.assertEqual(received['result']['coverage']['issue_id'], parent['issue_id'])
        self.assertEqual(received['result']['statistics']['unique_issues'], 3)
        self.assertEqual({row['id'] for row in received['progress']['issues']}, related_ids)
        serialized = json.dumps(received, ensure_ascii=False)
        self.assertNotIn('UNRELATED_BRANCH_', serialized)
        self.assertNotIn(other_parent['issue_id'], serialized)
        self.assertNotIn(other_child['issue_id'], serialized)

    def test_withdrawn_source_hides_issue_notes_and_history_while_raw_ledger_is_retained(self):
        self.client.configure(name='评估人员', evaluator=True)
        old = self.seed()
        source = {key: old[key] for key in ('id', 'digest', 'employee_id')}
        saved = self.client.save_issue_update(issue_id=old['issue_id'], summary='来源撤回后不可继续显示的旧结论',
                                              sources=[source], confirmed=True, expected_revision=0)
        self.assertEqual(self.client.issue_history(old['issue_id']), [saved])
        replacement = journal(self.employee)
        replacement['issue_id'] = old['issue_id']
        replacement['summary'] = '该issue仍有另一份已授权日志，不能复用已撤回的来源'
        replacement['digest'] = record_digest(replacement)
        # A complete new manifest revokes old's record while keeping this issue
        # visible through a different source. Both views must hide the old note.
        self.service.replicas.sync(self.employee, [replacement],
                                  [dict(id=replacement['id'], digest=replacement['digest'])], '测试员工', '电气')
        self.assertEqual(self.client.snapshot()['record_count'], 1)
        self.assertEqual(self.client.snapshot()['ledger']['updates'], [])
        self.assertEqual(self.client.issue_history(old['issue_id']), [])
        self.assertEqual(self.service.ledger.history(old['issue_id']), [saved])
        self.service.replicas.sync(self.employee, [], [], '测试员工', '电气')
        with self.assertRaisesRegex(ValueError, '共享范围'):
            self.client.issue_history(old['issue_id'])
        self.assertEqual(self.client.snapshot()['ledger']['updates'], [])

    def attachment_fixture(self):
        self.client.configure(name='评估人员', evaluator=True)
        row = self.seed()
        content = '仅在授权内存预览中展示的员工资料'.encode('utf-8')
        metadata = dict(path='report.md', size=len(content), digest=hashlib.sha256(content).hexdigest(),
                        previewable=True, modified='2026-11-11T16:00:00+08:00')
        return row, content, metadata

    def finish_report(self):
        self.service.report_worker.join(3)
        self.assertFalse(self.service.report_worker.is_alive(), 'attachment worker did not finish')
        return self.client.snapshot()['reports']

    def test_successful_scope_shrink_clears_completed_online_attachment_preview(self):
        row, content, metadata = self.attachment_fixture()
        with patch.object(self.service, '_vault') as vault, patch('evaluation.mesh.Transport') as transport:
            vault.return_value.load.return_value = ('test-token', None)
            transport.return_value.attachments.return_value = [metadata]
            transport.return_value.attachment.return_value = dict(metadata, content=content)
            self.client.reports(employee_id=self.employee, task_id=row['task_id'], path='report.md')
            before = self.finish_report()
            self.assertEqual(before['text'], content.decode('utf-8'))
            self.assertEqual(before['files'], [metadata])
            transport.return_value.manifest.return_value = dict(employee_id=self.employee, name='测试员工', records=[])
            self.service._sync_peer(copy.deepcopy(self.service.state['peers'][0]))
        after = self.client.snapshot()
        self.assertEqual(after['record_count'], 0)
        self.assertEqual(after['reports']['text'], '')
        self.assertEqual(after['reports']['files'], [])
        with self.assertRaisesRegex(ValueError, '授权资料范围'):
            self.client.reports(employee_id=self.employee, task_id=row['task_id'], path='report.md')

    def test_background_permission_refusal_hides_completed_attachment_and_log_copy(self):
        from evaluation.mesh import PermissionDenied
        row, content, metadata = self.attachment_fixture()
        with patch.object(self.service, '_vault') as vault, patch('evaluation.mesh.Transport') as transport, \
                patch('evaluation.mesh.Discovery') as discovery:
            vault.return_value.load.return_value = ('test-token', None)
            discovery.return_value.snapshot.return_value = []
            transport.return_value.attachments.return_value = [metadata]
            transport.return_value.attachment.return_value = dict(metadata, content=content)
            self.client.reports(employee_id=self.employee, task_id=row['task_id'], path='report.md')
            self.assertEqual(self.finish_report()['text'], content.decode('utf-8'))
            transport.return_value.manifest.side_effect = PermissionDenied('员工撤销此评估端授权')
            self.service.start()
            try:
                deadline = time.monotonic() + 3
                while self.client.snapshot()['record_count'] and time.monotonic() < deadline:
                    time.sleep(.005)
                after = self.client.snapshot()
                self.assertEqual(after['record_count'], 0)
                self.assertEqual(after['reports']['files'], [])
                self.assertEqual(after['reports']['text'], '')
                self.assertIn('授权', after['reports']['notice'])
            finally:
                # Stop while the mocks remain active; never open a real LAN socket.
                self.service.stop()
        self.assertFalse(self.service.worker.is_alive())

    def test_scope_shrink_invalidates_inflight_attachment_request_identity(self):
        row, content, metadata = self.attachment_fixture()
        entered, release = threading.Event(), threading.Event()
        def read(*args):
            entered.set()
            if not release.wait(3):
                raise TimeoutError('test did not release attachment')
            return dict(metadata, content=content)
        with patch.object(self.service, '_vault') as vault, patch('evaluation.mesh.Transport') as transport:
            vault.return_value.load.return_value = ('test-token', None)
            transport.return_value.attachments.return_value = [metadata]
            transport.return_value.attachment.side_effect = read
            self.client.reports(employee_id=self.employee, task_id=row['task_id'], path='report.md')
            try:
                self.assertTrue(entered.wait(1))
                original_request = self.service.report_result
                transport.return_value.manifest.return_value = dict(employee_id=self.employee, name='测试员工', records=[])
                self.service._sync_peer(copy.deepcopy(self.service.state['peers'][0]))
                self.assertIsNot(self.service.report_result, original_request)
            finally:
                release.set()
                after = self.finish_report()
        self.assertEqual(after['files'], [])
        self.assertEqual(after['text'], '')
        self.assertFalse(after['busy'])
        self.assertEqual(self.client.snapshot()['record_count'], 0)

    def test_closed_viewer_does_not_republish_inflight_attachment_while_scope_unchanged(self):
        row, content, metadata = self.attachment_fixture()
        entered, release = threading.Event(), threading.Event()
        def read(*args):
            entered.set()
            if not release.wait(3):
                raise TimeoutError('test did not release attachment')
            return dict(metadata, content=content)
        with patch.object(self.service, '_vault') as vault, patch('evaluation.mesh.Transport') as transport:
            vault.return_value.load.return_value = ('test-token', None)
            transport.return_value.attachments.return_value = [metadata]
            transport.return_value.attachment.side_effect = read
            self.client.reports(employee_id=self.employee, task_id=row['task_id'], path='report.md')
            try:
                self.assertTrue(entered.wait(1))
                original_request = self.service.report_result
                self.client.clear_reports()
                self.assertIsNot(self.service.report_result, original_request)
            finally:
                release.set()
                after = self.finish_report()
        self.assertEqual(after, dict(busy=False, files=[], text='', notice=''))
        self.assertEqual(self.client.snapshot()['record_count'], 1)

    def test_stop_refuses_new_queries_and_export_before_waiting_for_existing_worker(self):
        self.client.configure(name='评估人员', evaluator=True)
        self.seed()
        entered, release = threading.Event(), threading.Event()
        errors = []
        class Runner:
            def run(inner, question, result, progress, cancel, notice):
                entered.set()
                # Hold the worker briefly so the test observes the service during
                # drain, rather than only after resources have already closed.
                release.wait(3)
                return '停机后不得显示的回答'
        self.service.runner = Runner()
        self.client.ask(question='正在退出的旧问答', model=True)
        self.assertTrue(entered.wait(1))
        worker = self.service.query_worker
        def stop():
            try:
                self.service.stop()
            except Exception as exc:
                errors.append(exc)
        stopper = threading.Thread(target=stop, name='evaluation-test-stop')
        try:
            stopper.start()
            deadline = time.monotonic() + 1
            while not self.service.stopping.is_set() and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertTrue(self.service.stopping.is_set())
            self.assertFalse(self.service.alive)
            self.assertTrue(worker.is_alive())
            with self.assertRaisesRegex(OSError, 'stopped'):
                self.client.ask(question='不得新建的问答', model=True)
            with patch('services.EvaluationService.JournalExporter') as exporter:
                with self.assertRaisesRegex(ValueError, '退出'):
                    self.service.export_records()
                exporter.assert_not_called()
            self.assertIs(self.service.query_worker, worker)
        finally:
            release.set()
            stopper.join(3)
        self.assertFalse(stopper.is_alive())
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(self.service.closed)


if __name__ == '__main__':
    unittest.main()
