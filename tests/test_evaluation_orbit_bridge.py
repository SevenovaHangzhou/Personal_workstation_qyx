"""Real ORBIT employee service to Evaluator, pinned TLS and isolated ledgers."""
import base64
import copy
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clients import call
from credentials import CredentialStore
from daily_store import DailyStore
from evaluation.mesh import PermissionDenied, Transport
from evaluation.report_archive import CollectedReports, ReportArchive
from evaluation.sharing import JournalShareService
from framework import api
from runtime import MomoRuntime
from test_evaluation_journals import task, report, progress
from test_evaluation_integration import SilentDiscovery


class OrbitBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.keyring = patch.object(CredentialStore, 'keyring', return_value=None)
        self.keyring.start()
        self.addCleanup(self.keyring.stop)
        self.discovery = patch('evaluation.mesh.Discovery', SilentDiscovery)
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        with patch('services.EvaluationService.EvaluationService.start'):
            self.runtime = MomoRuntime(self.root/'reviewer/tasks.json', profile='work', auto_tick=False).start()
        self.addCleanup(self.runtime.close, apply_update=False)
        self.service = self.runtime.evaluation_service
        self.client = self.runtime.evaluation
        self.client.configure(name='隔离管理员', evaluator=True)
        self.store = DailyStore(self.root/'employee/daily')
        self.addCleanup(self.store.close)
        self.manual = DailyStore(self.store.folder/'work-logs')
        self.addCleanup(self.manual.close)
        self.work = task('工程接口', thread='work-chat')
        self.other = task('未授权工作', thread='private-chat', number=2)
        self.demo = task('有趣 Demo', 'demo', thread='demo-chat', number=3)
        self.tasks = SimpleNamespace(tasks=[self.work, self.other, self.demo])
        self.daily = SimpleNamespace(store=self.store, busy=False,
            work_logs=SimpleNamespace(store=self.manual, active=None, lock=threading.RLock()))
        self.content = report(self.tasks.tasks,
            mapping={'work-chat':[self.work['id']], 'private-chat':[self.other['id']], 'demo-chat':[self.demo['id']]},
            summaries={'work-chat':'接口单测完成，实机联调待进行。', 'private-chat':'PRIVATE_WORK', 'demo-chat':'DEMO_FULL_REPORT_ONLY'},
            evidence=[progress(self.work)], markdown='# 完整日报\nDEMO_FULL_REPORT_ONLY\n日常安排')
        self.store.save_report('2026-11-11', 'done', self.content, [])
        week = copy.deepcopy(self.content)
        week.update(kind='week', markdown='# 完整周报\nDEMO_FULL_REPORT_ONLY')
        self.manual.save_report('week-20261111-180000-1234abcd', 'pending', week, [])
        self.employee = JournalShareService(api.server(), self.root/'employee/evaluation', self.tasks, self.daily)
        self.assertEqual(api.add_service(self.employee, '/JournalShareService'), 0)
        self.addCleanup(self.stop_employee)
        self.employee.start()
        self.configure()
        self.wait(lambda: self.employee.endpoint is not None)

    def stop_employee(self):
        self.employee.stop()
        api.del_service('/JournalShareService')

    def configure(self, ids=None, enabled=True):
        call('/JournalShareService/configure', dict(name='隔离员工', department='电气', sharing=enabled,
             shared_task_ids=[self.work['id']] if ids is None else ids, confirmed=True))

    def wait(self, predicate, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.02)
        self.assertTrue(predicate())

    def connect(self, full=False):
        grant = call('/JournalShareService/grant', dict(label='隔离管理员', full_reports=full, confirmed=True))
        grant['url'] = 'https://127.0.0.1:' + str(grant['port'])
        self.client.add_peer(grant)
        self.peer = copy.deepcopy(self.service.state['peers'][0])
        transport = Transport(grant['url'], grant['fingerprint'], grant['token'], grant['employee_id'])
        return grant, transport

    def test_direct_employee_authorization_and_daily_weekly_issue_statistics(self):
        grant, transport = self.connect()
        self.service._sync_peer(self.peer)
        records = self.service.visible_records()
        self.assertEqual({row['kind'] for row in records}, {'nightly', 'week'})
        self.assertEqual({row['issue_id'] for row in records}, {self.work['linear_issue']['id']})
        text = str(records)
        self.assertNotIn('PRIVATE_WORK', text)
        self.assertNotIn('DEMO_FULL_REPORT_ONLY', text)
        self.assertNotIn('RAW_CHAT_MUST_STAY_LOCAL', text)
        self.assertEqual(len(self.client.snapshot()['progress']['issues']), 1)
        self.assertEqual(self.client.snapshot()['complete_reports'], {})
        with self.assertRaises(PermissionDenied):
            transport.report_manifest()
        self.assertTrue(transport.manifest()['records'])
        self.configure(ids=[])
        self.service._sync_peer(self.peer)
        self.assertEqual(self.service.visible_records(), [])

    def test_complete_reports_separate_grant_collection_and_exclusion_from_questions(self):
        grant, transport = self.connect(full=True)
        self.service._sync_peer(self.peer)
        employee = grant['employee_id']
        snapshot = self.client.snapshot()
        archive = snapshot['complete_reports'][employee]
        self.assertTrue(archive['available'])
        self.assertEqual(len(archive['files']), 2)
        text = self.client.complete_report_text(employee, '日报/自动/2026-11-11.md')
        self.assertIn('DEMO_FULL_REPORT_ONLY', text)
        self.assertNotIn('DEMO_FULL_REPORT_ONLY', str(self.service.visible_records()))
        self.client.ask(question='工程接口进度', model=False)
        self.wait(lambda: not self.client.snapshot()['query']['busy'])
        self.assertNotIn('DEMO_FULL_REPORT_ONLY', str(self.client.snapshot()['query']))
        # Narrow only the complete-report scope without affecting issue access.
        with self.employee.server.lock:
            self.employee.server.state['grants'][0]['full_reports'] = False
            self.employee.server._save()
        self.service._sync_peer(self.peer)
        self.assertFalse(self.client.snapshot()['complete_reports'][employee]['available'])
        self.assertEqual(len(self.service.visible_records()), 2)
        with self.assertRaises(ValueError):
            self.client.complete_report_text(employee, '日报/自动/2026-11-11.md')
        call('/JournalShareService/revoke', {'id':grant['grant_id']})
        with self.assertRaises(PermissionDenied):
            transport.manifest()

    def test_invalid_changed_report_keeps_verified_previous_copy(self):
        grant, transport = self.connect(full=True)
        self.service._sync_peer(self.peer)
        employee = grant['employee_id']
        prior = self.client.complete_report_text(employee, '日报/自动/2026-11-11.md')
        changed = dict(self.content, markdown='# 新版日报\n更新内容')
        self.store.save_report('2026-11-11', 'done', changed, [])
        with patch.object(transport, 'report', return_value=dict(data=base64.b64encode(b'corrupt').decode())):
            self.service._sync_complete_reports(self.peer, transport)
        self.assertEqual(self.client.complete_report_text(employee, '日报/自动/2026-11-11.md'), prior)
        self.assertIn('未完成', self.client.snapshot()['complete_reports'][employee]['notice'])
        self.service._sync_complete_reports(self.peer, transport)
        self.assertIn('更新内容', self.client.complete_report_text(employee, '日报/自动/2026-11-11.md'))

    def test_active_reports_and_local_secrets_never_exposed_as_files(self):
        grant, transport = self.connect(full=True)
        (self.store.folder/'auth.json').write_text('PRIVATE')
        raw = self.store.folder/'compression/private.md'
        raw.parent.mkdir()
        raw.write_text('PRIVATE')
        self.manual.save_report('today-20261111-190000-1234abcd', 'running', self.content, [])
        self.assertEqual(len(transport.report_manifest()['files']), 2)
        self.daily.busy = True
        with self.assertRaises(ValueError):
            transport.manifest()
        with self.assertRaises(ValueError):
            transport.report_manifest()
        self.daily.busy = False
        original = self.store.folder/'2026-11-11.md'
        original.unlink()
        try:
            original.symlink_to(raw)
        except OSError:
            if os.name == 'nt':
                self.skipTest('当前 Windows 账号不具备创建符号链接的权限')
            raise
        with self.assertRaises(ValueError):
            transport.report_manifest()

    def test_saved_grants_identity_and_port_survive_employee_restart(self):
        grant, transport = self.connect()
        self.configure(enabled=False)
        self.wait(lambda: self.employee.endpoint is None)
        self.configure(enabled=True)
        self.wait(lambda: self.employee.endpoint is not None)
        self.assertEqual(self.employee.endpoint['port'], grant['port'])
        self.assertEqual(self.employee.endpoint['fingerprint'], grant['fingerprint'])
        self.assertEqual(len(transport.manifest()['records']), 2)

    def test_export_rejects_a_source_change_during_collection(self):
        from evaluation.journals import JournalExporter, _source_reports
        exporter = JournalExporter(self.daily, self.tasks, self.employee.state['employee_id'], '隔离员工')
        def changed(store):
            for row in _source_reports(store):
                yield row
                store.put('report_revision', store.get('report_revision', 0) + 1)
        with patch('evaluation.journals._source_reports', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '导出期间更新'):
                exporter.records([self.work['id']])


if __name__ == '__main__':
    unittest.main()
