import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.protocol import record_id, record_digest
from evaluation.mesh import (ShareServer, Transport, Discovery, PROTOCOL, endpoint,
                             _Handler, _json_bytes, MAX_RESPONSE, MAX_MANIFEST, MAX_ATTACHMENT_BYTES, PermissionDenied)


def journal(employee, summary='完成接口联调'):
    row = {'employee_id': employee, 'source_device': employee, 'name': '测试员工',
           'department': '电气', 'source_id': 'day:2026-11-11', 'kind': 'nightly',
           'period_start': '2026-11-10T22:00:00+08:00', 'period_end': '2026-11-11T22:00:00+08:00',
           'task_id': 'task-1', 'task_title': '接口联调', 'issue_id': str(uuid.uuid4()),
           'issue_identifier': 'EQ-1', 'issue_url': 'https://linear.app/test/issue/EQ-1/task',
           'parent_issue_id': '', 'linear_state_type': 'started', 'summary': summary, 'evidence': []}
    row['id'] = record_id(row['source_id'], row['task_id'])
    row['digest'] = record_digest(row)
    return row


class ShareTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.employee = str(uuid.uuid4())
        self.rows = [journal(self.employee)]
        self.calls = 0
        def records():
            self.calls += 1
            return copy.deepcopy(self.rows)
        self.server = ShareServer(self.folder, self.employee, '测试员工', records)
        self.grant = self.server.grant('测试评估端')
        self.info = self.server.start('127.0.0.1')
        self.url = 'https://127.0.0.1:' + str(self.info['port'])
        self.client = Transport(self.url, self.info['fingerprint'], self.grant['token'], self.employee)

    def tearDown(self):
        self.server.stop()
        self.temporary.cleanup()

    def test_real_tls_reads_only_manifest_and_authorized_current_record(self):
        manifest = self.client.manifest()
        self.assertEqual(manifest, {'employee_id': self.employee, 'name': '测试员工',
                                   'records': [{'id': self.rows[0]['id'], 'digest': self.rows[0]['digest']}]})
        self.assertEqual(self.client.record(self.rows[0]['id'], self.rows[0]['digest']), self.rows[0])
        old = copy.deepcopy(self.rows[0])
        self.rows[0]['summary'] = '补充新的验证结果'
        self.rows[0]['digest'] = record_digest(self.rows[0])
        with self.assertRaisesRegex(ValueError, '修订|请求失败'):
            self.client.record(old['id'], old['digest'])
        self.assertEqual(self.client.record(old['id'], self.rows[0]['digest'])['summary'], '补充新的验证结果')
        self.rows.clear()
        with self.assertRaises(ValueError):
            self.client.record(old['id'], old['digest'])

    def test_optional_assignee_and_legacy_records_round_trip_with_versioned_digest(self):
        legacy = copy.deepcopy(self.rows[0])
        self.assertNotIn('linear_assignee_id', self.client.record(legacy['id'], legacy['digest']))
        self.rows[0]['linear_assignee_id'] = str(uuid.uuid4())
        self.rows[0]['digest'] = record_digest(self.rows[0])
        updated = copy.deepcopy(self.rows[0])
        self.assertNotEqual(updated['digest'], legacy['digest'])
        self.assertEqual(self.client.manifest()['records'], [dict(id=updated['id'], digest=updated['digest'])])
        self.assertEqual(self.client.record(updated['id'], updated['digest']), updated)
        with self.assertRaisesRegex(ValueError, '修订|请求失败'):
            self.client.record(legacy['id'], legacy['digest'])
        self.rows[0] = legacy
        self.assertEqual(self.client.record(legacy['id'], legacy['digest']), legacy)

    def test_wrong_pin_does_not_send_http_or_credentials(self):
        calls = []
        original = _Handler.do_POST
        def spy(handler):
            calls.append(dict(handler.headers))
            return original(handler)
        bad = Transport(self.url, '0' * 64, self.grant['token'], self.employee)
        with patch.object(_Handler, 'do_POST', spy):
            with self.assertRaisesRegex(ValueError, '指纹不符'):
                bad.manifest()
        self.assertEqual(calls, [])
        self.assertEqual(self.calls, 0)

    def test_grants_independent_revocable_and_only_hash_persisted(self):
        persisted = (self.folder / 'grants.json').read_text()
        self.assertNotIn(self.grant['token'], persisted)
        self.assertIn(hashlib.sha256(self.grant['token'].encode()).hexdigest(), persisted)
        self.assertTrue(all('token' not in row and 'token_hash' not in row for row in self.server.grants()))
        with self.assertRaises(PermissionError):
            Transport(self.url, self.info['fingerprint'], 'x' * 43, self.employee).manifest()
        second = self.server.grant('另一个评估端')
        other = Transport(self.url, self.info['fingerprint'], second['token'], self.employee)
        self.server.revoke(self.grant['id'])
        self.server.revoke(self.grant['id'])
        with self.assertRaises(PermissionError):
            self.client.manifest()
        self.assertEqual(len(other.manifest()['records']), 1)
        self.server.stop()
        restored = ShareServer(self.folder, self.employee, '测试员工', lambda: copy.deepcopy(self.rows))
        self.server = restored
        self.info = restored.start('127.0.0.1')
        self.assertEqual(self.info['fingerprint'], self.client.fingerprint)
        url = 'https://127.0.0.1:' + str(self.info['port'])
        with self.assertRaises(PermissionError):
            Transport(url, self.info['fingerprint'], self.grant['token'], self.employee).manifest()
        self.assertEqual(len(Transport(url, self.info['fingerprint'], second['token'], self.employee).manifest()['records']), 1)
        if os.name != 'nt':
            self.assertEqual((self.folder / 'private-key.pem').stat().st_mode & 0o777, 0o600)

    def test_duplicate_unknown_foreign_or_modified_record_rejected(self):
        row = copy.deepcopy(self.rows[0])
        self.rows.append(copy.deepcopy(row))
        with self.assertRaises(ValueError):
            self.client.manifest()
        self.rows = [row]
        row['raw_chat_path'] = '/private/codex/chat.jsonl'
        row['digest'] = record_digest(row)
        with self.assertRaises(ValueError):
            self.client.record(row['id'], row['digest'])
        del row['raw_chat_path']
        row['digest'] = record_digest(row)
        row['summary'] = '摘要被篡改且没有更新哈希'
        with self.assertRaises(ValueError):
            self.client.record(row['id'], row['digest'])
        row['employee_id'] = str(uuid.uuid4())
        with self.assertRaises(ValueError):
            self.client.manifest()

    def test_expected_employee_mismatch_cannot_return_other_person(self):
        other = Transport(self.url, self.info['fingerprint'], self.grant['token'], str(uuid.uuid4()))
        with self.assertRaisesRegex(ValueError, '已配对员工'):
            other.manifest()
        with self.assertRaisesRegex(ValueError, '不属于此员工'):
            ShareServer(self.folder, str(uuid.uuid4()), '其他员工', lambda: [])

    def test_requests_have_no_path_or_upload_surface_and_are_bounded(self):
        context = ssl._create_unverified_context()
        def request(path, body, extra=None):
            connection = http.client.HTTPSConnection('127.0.0.1', self.info['port'], context=context, timeout=3)
            headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.grant['token'],
                       'X-Orbit-Protocol': PROTOCOL}
            headers.update(extra or {})
            connection.request('POST', path, body, headers)
            response = connection.getresponse()
            status = response.status
            response.read()
            connection.close()
            return status
        self.assertEqual(request('/v1/upload', b'{}'), 404)
        self.assertEqual(request('/v1/manifest', b'{"path":"/etc/passwd"}'), 400)
        self.assertEqual(request('/v1/record', b'{"id":"x","digest":"x","path":"../../secret"}'), 400)
        self.assertEqual(request('/v1/manifest', b'{}', {'Content-Length': '9999999'}), 400)
        self.assertEqual(request('/v1/manifest', b'{}', {'X-Orbit-Protocol': 'other/v1'}), 400)
        self.assertEqual(request('/v1/manifest', b'{}', {'Content-Type': 'text/plain'}), 400)

    def test_transport_rejects_unbounded_and_mismatching_responses(self):
        original = _Handler.reply
        def oversized(handler, status, value):
            handler.send_response(200)
            handler.send_header('X-Orbit-Protocol', PROTOCOL)
            handler.send_header('Content-Length', str(MAX_MANIFEST + 1))
            handler.end_headers()
            handler.close_connection = True
        with patch.object(_Handler, 'reply', oversized):
            with self.assertRaisesRegex(ValueError, '响应过大'):
                self.client.manifest()
        def redirect(handler, status, value):
            handler.send_response(302)
            handler.send_header('X-Orbit-Protocol', PROTOCOL)
            handler.send_header('Location', 'https://8.8.8.8/stolen')
            handler.send_header('Content-Length', '2')
            handler.end_headers()
            handler.wfile.write(b'{}')
            handler.close_connection = True
        with patch.object(_Handler, 'reply', redirect):
            with self.assertRaises(ValueError):
                self.client.manifest()

    def test_early_auth_failure_explicitly_closes_response_stream(self):
        # Connection: close transfers ownership from HTTPSConnection to the
        # response. Keep captured responses alive so GC cannot hide a leak.
        self.server.revoke(self.grant['id'])
        responses = []
        original = http.client.HTTPResponse.close
        def close(response):
            responses.append(response)
            return original(response)
        with patch.object(http.client.HTTPResponse, 'close', close):
            with self.assertRaises(PermissionDenied):
                self.client.manifest()
        self.assertTrue(responses)
        self.assertTrue(all(response.isclosed() for response in responses))

    def test_revoke_during_read_prevents_returning_content(self):
        entered, release = threading.Event(), threading.Event()
        original = self.server.records_callback
        def blocked():
            entered.set()
            release.wait(2)
            return original()
        self.server.records_callback = blocked
        errors = []
        def request():
            try:
                self.client.record(self.rows[0]['id'], self.rows[0]['digest'])
            except Exception as exc:
                errors.append(exc)
        reader = threading.Thread(target=request)
        reader.start()
        self.assertTrue(entered.wait(1))
        self.server.revoke(self.grant['id'])
        release.set()
        reader.join(2)
        self.assertFalse(reader.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PermissionError)

    def test_stop_waits_for_inflight_read_without_holding_grant_lock(self):
        entered, release = threading.Event(), threading.Event()
        original = self.server.records_callback
        def blocked():
            entered.set()
            release.wait(2)
            return original()
        self.server.records_callback = blocked
        values, errors = [], []
        def request():
            try:
                values.append(self.client.manifest())
            except Exception as exc:
                errors.append(exc)
        reader = threading.Thread(target=request)
        reader.start()
        self.assertTrue(entered.wait(1))
        stopping = threading.Thread(target=self.server.stop)
        stopping.start()
        stopping.join(.15)
        self.assertTrue(stopping.is_alive())
        release.set()
        stopping.join(3)
        reader.join(3)
        self.assertFalse(stopping.is_alive())
        self.assertFalse(reader.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(values), 1)

    def attach_reports(self):
        task = str(uuid.uuid4())
        content = '接口测试通过，剩余现场验证。\n'.encode()
        metadata = {'path': '联调/结果.md', 'size': len(content), 'digest': hashlib.sha256(content).hexdigest(),
                    'previewable': True, 'modified': '2026-11-11T12:00:00+00:00'}
        photo = {'path': '现场.jpg', 'size': 100, 'digest': 'a' * 64, 'previewable': False}
        large = {'path': '测试视频.mp4', 'size': 5 * 1024 * 1024, 'digest': '', 'previewable': False}
        calls = []
        allowed = [True]
        def callback(task_id, path=None):
            calls.append((task_id, path))
            if task_id != task or not allowed[0]:
                raise PermissionError('任务未纳入共享')
            if path is None:
                return [dict(metadata), dict(photo), dict(large)]
            if path != metadata['path']:
                raise AssertionError('不能读取非预览资料')
            return dict(metadata, content=content, text=content.decode())
        self.server.attachments_callback = callback
        return task, metadata, content, calls, allowed

    def test_online_attachment_preview_without_replica_or_unsupported_download(self):
        task, metadata, content, calls, allowed = self.attach_reports()
        before = {str(path.relative_to(self.folder)): path.read_bytes() for path in self.folder.rglob('*') if path.is_file()}
        rows = self.client.attachments(task)
        self.assertEqual(len(rows), 3)
        self.assertEqual(next(row for row in rows if row['path'].endswith('.mp4'))['digest'], '')
        value = self.client.attachment(task, metadata['path'])
        self.assertEqual(value['content'], content)
        self.assertEqual(value['digest'], metadata['digest'])
        self.assertNotIn('text', value)
        for path in ('现场.jpg', '测试视频.mp4'):
            with self.assertRaises(ValueError):
                self.client.attachment(task, path)
        self.assertTrue(all(path in (None, metadata['path']) for _, path in calls))
        after = {str(path.relative_to(self.folder)): path.read_bytes() for path in self.folder.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_attachment_task_permission_checked_on_every_request(self):
        task, metadata, content, calls, allowed = self.attach_reports()
        self.client.attachments(task)
        allowed[0] = False
        with self.assertRaises(PermissionDenied):
            self.client.attachment(task, metadata['path'])
        with self.assertRaises(PermissionDenied):
            self.client.attachments(str(uuid.uuid4()))
        allowed[0] = True
        self.server.revoke(self.grant['id'])
        with self.assertRaises(PermissionDenied):
            self.client.attachments(task)
        self.assertEqual(len(calls), 3)

    def test_attachment_paths_hidden_credentials_and_unknown_types_rejected(self):
        task, metadata, content, calls, allowed = self.attach_reports()
        for path in ('../private.txt', '/etc/passwd', '.private/key.txt', 'auth.json', 'credentials.json',
                     'C:/secret.md', 'part\\secret.txt', 'link/.env', 'NUL.txt', 'private-key.pem', 'file.txt/..'):
            with self.assertRaises(ValueError, msg=path):
                self.client.attachment(task, path)
        self.assertEqual(calls, [])
        with self.assertRaises(ValueError):
            self.client.attachments('not-a-task-uuid')
        hidden = dict(metadata, path='.private.txt')
        self.server.attachments_callback = lambda task_id, path=None: [hidden]
        with self.assertRaises(ValueError):
            self.client.attachments(task)
        invalid = dict(metadata, path='unsafe.html', previewable=True)
        self.server.attachments_callback = lambda task_id, path=None: [invalid]
        with self.assertRaises(ValueError):
            self.client.attachments(task)

    def test_attachment_changed_content_oversize_and_list_limit_are_rejected(self):
        task, metadata, content, calls, allowed = self.attach_reports()
        def altered(task_id, path=None):
            return [dict(metadata)] if path is None else dict(metadata, content=content + b'changed')
        self.server.attachments_callback = altered
        with self.assertRaises(ValueError):
            self.client.attachment(task, metadata['path'])
        oversized = dict(metadata, size=MAX_ATTACHMENT_BYTES + 1)
        self.server.attachments_callback = lambda task_id, path=None: [oversized]
        with self.assertRaises(ValueError):
            self.client.attachments(task)
        self.server.attachments_callback = lambda task_id, path=None: [dict(metadata, path=str(i) + '.txt') for i in range(501)]
        with self.assertRaises(ValueError):
            self.client.attachments(task)
        bad_utf8 = b'\xff\xfe'
        invalid = dict(metadata, size=len(bad_utf8), digest=hashlib.sha256(bad_utf8).hexdigest())
        self.server.attachments_callback = lambda task_id, path=None: [invalid] if path is None else dict(invalid, content=bad_utf8)
        with self.assertRaises(ValueError):
            self.client.attachment(task, metadata['path'])

    def test_stop_drains_handshake_and_can_restart(self):
        socket_before = socket.create_connection(('127.0.0.1', self.info['port']), timeout=2)
        # A blocked TLS handshake remains bounded by the per-socket timeout.
        socket_before.close()
        self.server.stop()
        self.server.stop()
        info = self.server.start('127.0.0.1', self.info['port'])
        self.assertEqual(info['fingerprint'], self.info['fingerprint'])
        self.assertEqual(len(self.client.manifest()['records']), 1)


class DiscoveryTests(unittest.TestCase):
    def packet(self, **changes):
        value = {'protocol': PROTOCOL, 'employee_id': str(uuid.uuid4()), 'name': '员工',
                 'port': 18766, 'fingerprint': 'a' * 64}
        value.update(changes)
        return _json_bytes(value)

    def test_presence_minimal_validated_bounded_and_expires(self):
        discovery = Discovery(str(uuid.uuid4()), '评估端', 0, '')
        raw = self.packet()
        discovery._accept(raw, '192.168.100.22')
        rows = discovery.snapshot()
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {'employee_id', 'name', 'address', 'port', 'fingerprint', 'seen'})
        self.assertNotIn('token', raw.decode())
        self.assertNotIn('issue', raw.decode())
        for packet in (self.packet(protocol='foreign'), self.packet(port=0), self.packet(token='secret'),
                       self.packet(fingerprint='bad'), b'[]', b'x' * 1025):
            discovery._accept(packet, '192.168.100.22')
        discovery._accept(self.packet(), '8.8.8.8')
        self.assertEqual(len(discovery.snapshot()), 1)
        for _ in range(270):
            discovery._accept(self.packet(), '192.168.100.22')
        self.assertEqual(len(discovery.snapshot()), 256)
        with patch('evaluation.mesh.time.monotonic', return_value=time.monotonic() + 40):
            self.assertEqual(discovery.snapshot(), [])

    def test_numeric_public_and_dangerous_urls_are_refused(self):
        for url in ('http://192.168.1.2', 'https://8.8.8.8', 'https://0.0.0.0', 'https://224.0.0.1',
                    'https://user:pass@192.168.1.2', 'https://192.168.1.2/path',
                    'https://192.168.1.2/?q=a', 'https://192.168.1.2/#a',
                    'https://192.168.1.2:99999', 'https://192.168.1.2\\@8.8.8.8'):
            with self.assertRaises(ValueError, msg=url):
                endpoint(url)
        self.assertEqual(endpoint('https://127.0.0.1:18766'), ('127.0.0.1', 18766))
        self.assertEqual(endpoint('https://[::1]:18766'), ('::1', 18766))
        transport = Transport('https://staff.local:18766', 'a' * 64, 'x' * 43, str(uuid.uuid4()))
        with patch('evaluation.mesh.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 18766))]):
            with self.assertRaisesRegex(ValueError, '非局域网'):
                transport.manifest()

    def test_listen_only_emits_no_broadcast_and_stops(self):
        discovery = Discovery(str(uuid.uuid4()), '评估端', 0, '')
        fake = unittest.mock.MagicMock()
        def no_packet(*_):
            discovery.stopping.set()
            raise socket.timeout()
        fake.recvfrom.side_effect = no_packet
        with patch('evaluation.mesh.socket.socket', return_value=fake):
            discovery.start()
            discovery.thread.join(2)
            discovery.stop()
        fake.sendto.assert_not_called()
        fake.close.assert_called()


if __name__ == '__main__':
    unittest.main()
