"""Real Yomk + loopback TLS end-to-end evidence; no real employee/model traffic."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from credentials import CredentialStore
from evaluation.mesh import ShareServer, PermissionDenied
from evaluation.protocol import PROTOCOL, record_digest, record_id
from evaluation.reports import ReportFolders
from runtime import MomoRuntime


class SilentDiscovery:
    def __init__(self, *args, **kwargs): self.error = ''
    def start(self): pass
    def stop(self): pass
    def snapshot(self): return []


def journal(employee, task):
    source = 'nightly:2026-11-11'
    value = dict(id=record_id(source, task), employee_id=employee, name='隔离测试员工', department='电气',
        source_device=employee, source_id=source, kind='nightly',
        period_start='2026-11-10T22:00:00+08:00', period_end='2026-11-11T22:00:00+08:00',
        task_id=task, task_title='接口开发测试', issue_id=str(uuid.uuid4()),
        issue_identifier='EE-901', issue_url='https://linear.app/test/issue/EE-901/test',
        parent_issue_id='', linear_state_type='started', summary='完成接口联调，验收尚未确认。', evidence=[])
    value['digest'] = record_digest(value)
    return value


class EvaluationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.keyring = patch.object(CredentialStore, 'keyring', return_value=None)
        self.keyring.start()
        self.addCleanup(self.keyring.stop)
        self.discovery = patch('evaluation.mesh.Discovery', SilentDiscovery)
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        with patch('services.EvaluationService.EvaluationService.start'):
            self.runtime = MomoRuntime(self.folder/'reviewer/tasks.json', profile='work', auto_tick=False).start()
        self.addCleanup(self.runtime.close, apply_update=False)
        self.service = self.runtime.evaluation_service
        self.client = self.runtime.evaluation
        self.client.configure(name='隔离评估端', evaluator=True)
        self.employee = str(uuid.uuid4())
        self.task = str(uuid.uuid4())
        self.rows = [journal(self.employee, self.task)]
        self.fetches = []
        def callback():
            self.fetches.append(time.monotonic())
            return copy.deepcopy(self.rows)
        self.employee_server = ShareServer(self.folder/'employee/share', self.employee, '隔离测试员工', callback)
        self.addCleanup(self.employee_server.stop)
        self.grant = self.employee_server.grant('隔离评估端')
        self.endpoint = self.employee_server.start('127.0.0.1')
        self.pair()

    def pair(self):
        self.client.add_peer(dict(protocol=PROTOCOL, employee_id=self.employee, name='隔离测试员工', department='电气',
            url='https://127.0.0.1:'+str(self.endpoint['port']), fingerprint=self.endpoint['fingerprint'], token=self.grant['token']))
        self.peer = copy.deepcopy(self.service.state['peers'][0])

    def wait(self, predicate, message='background operation did not finish', timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.01)
        self.fail(message)

    def sync(self):
        self.service._sync_peer(self.peer)

    def test_complete_sync_is_idempotent_revisioned_and_withdrawn_scope_hidden(self):
        self.sync()
        first = self.client.snapshot()
        self.assertEqual(first['record_count'], 1)
        self.assertEqual(first['employees'][0]['error'], '')
        self.assertTrue(first['employees'][0]['last_synced'])
        before = self.service.replicas.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0]
        self.sync()
        self.assertEqual(self.service.replicas.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], before)
        self.rows[0]['summary'] = '补充现场联调证据，仍需验收'
        self.rows[0]['digest'] = record_digest(self.rows[0])
        self.sync()
        self.assertEqual(self.service.replicas.records()[0]['summary'], self.rows[0]['summary'])
        self.assertEqual(self.service.replicas.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], before + 1)
        self.rows.clear()
        self.sync()
        self.assertEqual(self.client.snapshot()['record_count'], 0)
        self.assertEqual(self.service.replicas.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], before + 1)

    def test_offline_retains_last_successful_copy_and_auth_denial_hides_it(self):
        self.sync()
        self.employee_server.stop()
        self.service.start()
        self.wait(lambda: bool(self.client.snapshot()['employees'][0]['error']))
        self.assertEqual(self.client.snapshot()['record_count'], 1)
        self.endpoint = self.employee_server.start('127.0.0.1', self.endpoint['port'])
        self.employee_server.revoke(self.grant['id'])
        self.client.sync()
        self.wait(lambda: self.client.snapshot()['record_count'] == 0)
        self.assertIn('授权', self.client.snapshot()['employees'][0]['error'])
        self.assertGreater(self.service.replicas.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 0)

    def test_pairing_secrets_are_absent_from_state_snapshot_and_query(self):
        self.sync()
        self.client.ask(question='接口的开发进展是什么？', model=False)
        self.wait(lambda: not self.client.snapshot()['query']['busy'])
        snapshot = self.client.snapshot()
        self.assertIn('[S1]', snapshot['query']['answer'])
        self.assertIn(self.rows[0]['digest'], json.dumps(snapshot['query']))
        for text in ((self.service.folder/'state.json').read_text(), json.dumps(snapshot), json.dumps(self.employee_server.grants())):
            self.assertNotIn(self.grant['token'], text)
        self.assertNotIn(self.grant['token'], (self.folder/'employee/share/grants.json').read_text())
        self.assertTrue((self.service.folder/'peer-credentials'/self.employee/'.credentials/linear.key').exists())

    def test_successful_scope_shrink_invalidates_completed_answer(self):
        self.sync()
        self.client.ask(question='接口开发进展', model=False)
        self.wait(lambda: not self.client.snapshot()['query']['busy'])
        self.assertTrue(self.client.snapshot()['query']['answer'])
        self.rows.clear()
        self.sync()
        self.assertEqual(self.client.snapshot()['record_count'], 0)
        self.assertEqual(self.client.snapshot()['query']['answer'], '')
        self.assertEqual(self.client.snapshot()['query']['sources'], [])

    def test_successful_scope_shrink_cancels_inflight_model_answer(self):
        self.sync()
        entered = threading.Event()
        class Runner:
            def run(inner, question, result, progress, cancel, notice):
                entered.set()
                cancel.wait(3)
                return '已经撤回的证据不应再出现在回答中'
        self.service.runner = Runner()
        self.client.ask(question='总结接口开发', model=True)
        self.assertTrue(entered.wait(1))
        self.rows.clear()
        self.sync()
        self.wait(lambda: not self.client.snapshot()['query']['busy'])
        self.assertEqual(self.client.snapshot()['query']['answer'], '')
        self.assertEqual(self.client.snapshot()['query']['sources'], [])

    def test_real_report_folder_preview_stays_online_and_never_becomes_replica(self):
        self.sync()
        row = self.rows[0]
        root = self.folder/'employee/reports'
        task_folder = root/self.task
        task_folder.mkdir(parents=True)
        marker = task_folder/'.orbit-report.json'
        marker.write_text(json.dumps(dict(task_id=self.task, issue_id=row['issue_id'], employee_id=self.employee)))
        marker.chmod(0o600)
        payload = 'ONLY_IN_MEMORY_ATTACHMENT_测试记录正文'
        (task_folder/'验证.md').write_text(payload, encoding='utf-8')
        (task_folder/'auth.json').write_text('PRIVATE_TEST_CREDENTIAL', encoding='utf-8')
        task = dict(id=self.task, category='work', is_sample=False,
                    linear_issue=dict(id=row['issue_id'], identifier=row['issue_identifier'], url=row['issue_url']))
        reports = ReportFolders(root, lambda: [task])
        self.employee_server.attachments_callback = lambda task_id, path=None: reports.list(task_id) if path is None else reports.read(task_id, path)
        self.client.reports(employee_id=self.employee, task_id=self.task, path='验证.md')
        self.wait(lambda: not self.client.snapshot()['reports']['busy'])
        result = self.client.snapshot()['reports']
        self.assertEqual(result['text'], payload)
        self.assertEqual([item['path'] for item in result['files']], ['验证.md'])
        for path in self.service.folder.rglob('*'):
            if path.is_file():
                self.assertNotIn(payload.encode(), path.read_bytes(), str(path))
        self.employee_server.stop()
        self.client.reports(employee_id=self.employee, task_id=self.task, path='验证.md')
        self.wait(lambda: not self.client.snapshot()['reports']['busy'])
        self.assertEqual(self.client.snapshot()['reports']['text'], '')
        self.assertIn('不可用', self.client.snapshot()['reports']['notice'])
        self.assertEqual(self.client.snapshot()['record_count'], 1)

    def test_model_context_excludes_other_employees_and_unselected_issue(self):
        self.sync()
        other_employee = str(uuid.uuid4())
        other = journal(other_employee, str(uuid.uuid4()))
        other['name'] = '不在本次问题范围的员工B'
        other['digest'] = record_digest(other)
        self.service.state['peers'].append(dict(employee_id=other_employee, name=other['name'], department='其他',
            url='https://127.0.0.1:1', fingerprint='0'*64))
        self.service.replicas.sync(other_employee, [other], [dict(id=other['id'], digest=other['digest'])], other['name'], '其他')
        same_employee_other_issue = journal(self.employee, str(uuid.uuid4()))
        same_employee_other_issue['summary'] = '未选择的第二项工作'
        same_employee_other_issue['digest'] = record_digest(same_employee_other_issue)
        self.service.replicas.ingest(self.employee, [same_employee_other_issue])
        captured = []
        class Runner:
            def run(inner, question, result, progress, cancel, notice):
                captured.append(json.dumps(dict(result=result, progress=progress), ensure_ascii=False))
                return '仅选定范围的回答 [S1]'
        self.service.runner = Runner()
        self.client.ask(question='总结指定接口', employee_id=self.employee, issue_id=self.rows[0]['issue_id'], model=True)
        self.wait(lambda: not self.client.snapshot()['query']['busy'])
        self.assertEqual(len(captured), 1)
        self.assertIn(self.rows[0]['issue_id'], captured[0])
        self.assertNotIn(other_employee, captured[0])
        self.assertNotIn(other['issue_id'], captured[0])
        self.assertNotIn(same_employee_other_issue['issue_id'], captured[0])

    def test_service_share_reuses_its_port_after_disable_enable(self):
        self.client.configure(sharing=True)
        self.service.start()
        self.wait(lambda: self.client.snapshot()['endpoint'] is not None)
        first = self.client.snapshot()['endpoint']
        self.client.configure(sharing=False)
        self.wait(lambda: self.client.snapshot()['endpoint'] is None)
        self.client.configure(sharing=True)
        self.wait(lambda: self.client.snapshot()['endpoint'] is not None)
        second = self.client.snapshot()['endpoint']
        self.assertEqual(second['fingerprint'], first['fingerprint'])
        self.assertEqual(second['port'], first['port'], 'saved reviewer invitations must keep working after employee sharing restarts')


if __name__ == '__main__': unittest.main()
