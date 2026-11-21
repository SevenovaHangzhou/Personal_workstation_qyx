"""Real UDP/TLS transfers and durable offline snapshots in isolated data spaces."""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/codex-orbit'))
from orbit_plugin.core import Orbit
from orbit_plugin.journals import JournalLibrary, export_local
from orbit_plugin.lan import LanConfig, Contacts, share_server, collect, status
from evaluation.mesh import Discovery, Transport, PermissionDenied, PROTOCOL, _json_bytes
from evaluation.protocol import DISCOVERY_PORT, validate_record
from evaluation.shared_store import SharedStore, FileTransport, file_manifest, validate_manifest, shared_path


class PluginLanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.work = self.root / 'repo'
        self.work.mkdir()
        self.issue = dict(id=str(uuid.uuid4()), identifier='EE-1', title='力控接口',
             url='https://linear.app/example/issue/EE-1', description='not-for-shared-description',
             state_type='started', state_name='进行中', viewer_id=str(uuid.uuid4()), assignee_id='')
        self.issue['assignee_id'] = self.issue['viewer_id']
        self.orbit = Orbit(self.root / 'employee', fetch=lambda *a, **k:dict(viewer={'id':self.issue['viewer_id']},issues=[self.issue]),
                           details=lambda *a, **k:dict(self.issue))
        self.orbit.credential = Mock(return_value='local-only-key')
        self.cancel = threading.Event()
        self.task = self.orbit.start_task(self.cancel, 'EE-1', str(self.work))['task']
        self.result = self.draft()
        self.config = LanConfig(self.orbit.directory)
        self.config.save(self.orbit, '张工', '电气', True, [self.task['id']])
        self.library = JournalLibrary(self.orbit, self.config.read)
        self.admin = Orbit(self.root / 'admin')

    def draft(self, completed='完成接口分离'):
        return self.orbit.draft_result(self.task['id'], 'implemented_waiting_validation', completed,
                  'controller.py：接口分离', 'python -m unittest：3 passed，未上实机', '实机检查', '等待工位', '接入驱动')

    def server(self):
        server = share_server(self.orbit, self.config)
        info = server.start('127.0.0.1')
        self.addCleanup(server.stop)
        grant = server.grant('管理员甲', reports=True)
        url = f"https://127.0.0.1:{info['port']}"
        client = FileTransport(url, info['fingerprint'], grant['token'], self.config.read()['employee_id'])
        invite = dict(protocol=PROTOCOL, employee_id=self.config.read()['employee_id'], name='张工',
            fingerprint=info['fingerprint'], token=grant['token'], capabilities=['journals','full_reports','shared_store'])
        return server, client, grant, invite, url

    def test_drafts_export_sanitized_stable_files_without_context_or_raw_chat(self):
        secret = 'lin_api_' + 'a'*32
        result = self.draft('测试 ' + secret)
        logs = list((self.orbit.directory/'journals').rglob('*.json'))
        self.assertEqual(len(logs), 2)
        log = json.loads(next(p for p in logs if p.stem==result['id']).read_text())
        self.assertEqual(log['format'], 'orbit-work-log/v1')
        raw = json.dumps(log)
        self.assertNotIn(secret, raw)
        self.assertNotIn(str(self.work), raw)
        self.assertNotIn('not-for-shared-description', raw)
        again = self.draft('测试 ' + secret)
        self.assertEqual(result['id'], again['id'])
        self.assertEqual(len(list((self.orbit.directory/'journals').rglob('*.json'))), 2)
        self.assertFalse((self.admin.directory/'lan').exists())
        status(self.admin)
        self.assertFalse(self.admin.directory.exists())

    def test_log_redaction_covers_embedded_paths_and_named_secrets(self):
        result = self.draft('password=do-not-share /home/private/repo/source.py ' +
                            r'C:\Users\private\source.py https://alice:password@internal/path')
        logs = self.library.files()
        raw = next(raw.decode() for name, raw in logs.items() if result['id'] in name and name.endswith('.json'))
        for secret in ('do-not-share', '/home/private', 'alice:password', r'C:\\Users'):
            self.assertNotIn(secret, raw)

    def test_explicit_scope_and_legacy_evaluator_records(self):
        record = self.library.records()[0]
        validate_record(record)
        self.assertEqual(record['issue_id'], self.issue['id'])
        self.assertEqual(record['linear_assignee_id'], self.issue['viewer_id'])
        self.assertEqual(len(self.library.files()), 3)
        self.config.save(self.orbit, '张工', '电气', True, [])
        self.assertEqual(self.library.records(), [])
        self.assertEqual(list(self.library.files()), ['日志统计标准.md'])
        with self.assertRaises(ValueError):
            self.config.save(self.orbit, '张工', '', True, [str(uuid.uuid4())])

    def test_real_tcp_transfer_legacy_readback_incremental_and_offline_read(self):
        server, transport, grant, invite, url = self.server()
        legacy = Transport(url, transport.fingerprint, transport.token, transport.employee_id)
        record = legacy.manifest()['records'][0]
        validate_record(legacy.record(record['id'], record['digest']))
        self.assertEqual(len(legacy.report_manifest()['files']), 1)
        saved = Contacts(self.admin.directory).add(json.dumps(invite), url)
        self.assertEqual(saved['employee_id'], transport.employee_id)
        self.assertNotIn(invite['token'], json.dumps(status(self.admin)))
        first = collect(self.admin, transport.employee_id, self.cancel)
        self.assertEqual(first['files'], 3)
        self.assertGreater(first['transferred_bytes'], 0)
        second = collect(self.admin, transport.employee_id, self.cancel)
        self.assertEqual(second['transferred_bytes'], 0)
        self.assertEqual(second['reused_files'], 3)
        self.draft('第二条成果')
        third = collect(self.admin, transport.employee_id, self.cancel)
        self.assertEqual(third['files'], 5)
        self.assertEqual(third['reused_files'], 3)
        store = SharedStore(self.admin.directory/'shared-store')
        name = next(row['path'] for row in store.current(transport.employee_id)['manifest']['files'] if row['path'].endswith('.json'))
        server.stop()
        text = store.read(transport.employee_id, name)
        self.assertEqual(json.loads(text)['issue_id'], self.issue['id'])
        self.assertFalse(self.admin.read_shared_log(transport.employee_id, name)['live'])
        with self.assertRaises(OSError):
            collect(self.admin, transport.employee_id, self.cancel)
        self.assertEqual(store.read(transport.employee_id, name), text)

    def test_unpaired_revocation_pin_mismatch_and_disabled_sharing(self):
        server, transport, grant, invite, url = self.server()
        bad = FileTransport(url, '0'*64, transport.token, transport.employee_id)
        with self.assertRaisesRegex(ValueError, '指纹'):
            bad.shared_manifest()
        readonly = server.grant('仅结构化日志', reports=False)
        limited = FileTransport(url, transport.fingerprint, readonly['token'], transport.employee_id)
        with self.assertRaises(PermissionDenied):
            limited.shared_manifest()
        self.assertEqual(len(transport.shared_manifest()['files']), 3)
        # Revoke through a different process-owned server object, as the native card does.
        share_server(self.orbit, self.config).revoke(grant['id'])
        with self.assertRaises(PermissionDenied):
            transport.shared_manifest()
        grant2 = share_server(self.orbit, self.config).grant('管理员乙', reports=True)
        allowed = FileTransport(url, transport.fingerprint, grant2['token'], transport.employee_id)
        self.assertEqual(len(allowed.shared_manifest()['files']), 3)
        self.config.save(self.orbit, '张工', '电气', False, [self.task['id']])
        with self.assertRaises(PermissionDenied):
            allowed.shared_manifest()

    def test_multichunk_binary_transfer_and_scope_change_during_read(self):
        server, transport, *_ = self.server()
        name = '日志/2026-11-21/' + str(uuid.uuid4()) + '.md'
        raw = ('批量传输证据\n' * 90000).encode('utf-8')
        self.assertGreater(len(raw), 1024 * 1024)
        files = {name: raw}
        expected = file_manifest(files)
        def callback(operation, payload):
            return expected if operation=='shared_manifest' else raw
        server.shared_callback = callback
        store = SharedStore(self.admin.directory/'shared-store')
        result = store.pull(transport.employee_id, transport, self.cancel)
        self.assertEqual(result['transferred_bytes'], len(raw))
        self.assertEqual(store.read(transport.employee_id, name).encode(), raw)
        def changing(operation, payload):
            if operation=='shared_file':
                self.config.save(self.orbit, '张工', '电气', True, [])
                return raw
            return expected
        server.shared_callback = changing
        import io
        with self.assertRaises(PermissionDenied):
            transport.download(expected['version'], expected['files'][0], io.BytesIO(), self.cancel)

    def test_atomic_snapshot_failure_removal_and_corruption(self):
        server, transport, *_ = self.server()
        store = SharedStore(self.admin.directory/'shared-store')
        employee = transport.employee_id
        first = store.pull(employee, transport, self.cancel)
        self.draft('新成果')
        def fail(*args):
            raise OSError('transfer interrupted')
        with patch.object(transport, 'download', side_effect=fail):
            with self.assertRaises(OSError):
                store.pull(employee, transport, self.cancel)
        self.assertEqual(store.current(employee)['manifest'], first['manifest'])
        self.assertFalse(list((store.root/employee).glob('.pending-*')))
        self.config.save(self.orbit, '张工', '电气', True, [])
        empty = store.pull(employee, transport, self.cancel)
        self.assertEqual(len(empty['manifest']['files']), 1)
        self.assertTrue((store.root/employee/first['manifest']['version']).is_dir())
        path = store.root/employee/empty['manifest']['version']/'日志统计标准.md'
        path.write_text('tampered')
        with self.assertRaises(ValueError):
            store.read(employee, '日志统计标准.md')

    def test_manifest_bounds_traversal_symlink_and_cancelled_pull(self):
        for name in ('../credentials', '/etc/passwd', '日志/../../secret', '日志/a.md', 'auth.json'):
            with self.assertRaises(ValueError):
                shared_path(name)
        value = file_manifest({'日志统计标准.md': b'hello'})
        value['files'][0]['bytes'] = 100*1024*1024
        with self.assertRaises(ValueError):
            validate_manifest(value)
        if os.name != 'nt':
            (self.root/'linked').symlink_to(self.work, target_is_directory=True)
            with self.assertRaises(ValueError):
                SharedStore(self.root/'linked').current(str(uuid.uuid4()))
        server, transport, *_ = self.server()
        self.cancel.set()
        with self.assertRaises(InterruptedError):
            SharedStore(self.admin.directory/'shared-store').pull(transport.employee_id, transport, self.cancel)

    def test_udp_broadcast_discovery_and_ttl(self):
        local_id, remote_id = str(uuid.uuid4()), str(uuid.uuid4())
        discovery = Discovery(local_id, '管理员', 0, '')
        discovery.start()
        self.addCleanup(discovery.stop)
        self.assertFalse(discovery.error)
        packet = _json_bytes(dict(protocol=PROTOCOL, employee_id=remote_id, name='员工', port=45875, fingerprint='a'*64))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sender.sendto(packet, ('255.255.255.255', DISCOVERY_PORT))
        deadline = time.monotonic()+3
        while time.monotonic()<deadline and not discovery.snapshot():
            time.sleep(.03)
        self.assertEqual(discovery.snapshot()[0]['employee_id'], remote_id)
        discovery._accept(packet, '8.8.8.8')
        self.assertEqual(len(discovery.snapshot()), 1)
        with discovery.lock:
            next(iter(discovery.peers.values()))['seen'] -= 40
        self.assertEqual(discovery.snapshot(), [])


class PluginLanUiTests(unittest.TestCase):
    setUp = PluginLanTests.setUp
    draft = PluginLanTests.draft
    server = PluginLanTests.server

    @classmethod
    def setUpClass(cls):
        from qt_runtime import configure_qt_plugins
        configure_qt_plugins()
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_native_card_reads_offline_files_and_never_exposes_tokens(self):
        from orbit_plugin.lan_ui import LanCard
        server, transport, _, invite, url = self.server()
        Contacts(self.admin.directory).add(json.dumps(invite), url)
        collect(self.admin, transport.employee_id, self.cancel)
        server.stop()
        card = LanCard(self.admin)
        self.addCleanup(card.close)
        card.show()
        self.app.processEvents()
        card.contact_list.setCurrentRow(0)
        self.assertEqual(card.files.count(), 3)
        card.files.setCurrentRow(0)
        self.assertTrue(card.reader.toPlainText())
        self.assertIn('非实时', card.collected.text())
        self.assertNotIn(invite['token'], card.status.text()+card.reader.toPlainText())
        card.resize(700,580)
        self.app.processEvents()
        self.assertGreater(card.reader.height(), 40)


class PluginLanDaemonTests(unittest.TestCase):
    setUp = PluginLanTests.setUp
    draft = PluginLanTests.draft

    def test_background_process_shutdown_and_stable_port(self):
        command = [sys.executable, str(ROOT/'plugins/codex-orbit/scripts/mcp.py'), '--lan-serve', '--data-dir', str(self.orbit.directory)]
        processes = []
        def cleanup():
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
        self.addCleanup(cleanup)
        def launch():
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(process)
            deadline = time.monotonic()+8
            while time.monotonic()<deadline:
                value = self.config.runtime()
                if value['state']=='running':
                    return process, value
                if process.poll() is not None:
                    break
                time.sleep(.05)
            self.fail('LAN node did not become ready')
        first, info = launch()
        duplicate = subprocess.run(command, capture_output=True, timeout=5)
        self.assertEqual(duplicate.returncode,0)
        first.terminate()
        self.assertEqual(first.wait(timeout=10),0)
        second, restarted = launch()
        self.assertEqual(info['port'], restarted['port'])
        self.assertEqual(info['fingerprint'], restarted['fingerprint'])
        self.config.save(self.orbit, '张工','电气',False,[self.task['id']])
        self.assertEqual(second.wait(timeout=10),0)
        self.assertEqual(self.config.runtime()['state'],'stopped')


if __name__ == '__main__':
    unittest.main()
