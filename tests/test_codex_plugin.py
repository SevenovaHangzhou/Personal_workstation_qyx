import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/codex-orbit'
sys.path.insert(0, str(PLUGIN))
from orbit_plugin.core import Orbit, OrbitError
from orbit_plugin.server import call, validate


class CodexPluginTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.work = self.root / '项目 with spaces'
        self.work.mkdir()
        self.cancel = threading.Event()
        self.issue = dict(id=str(uuid.uuid4()), identifier='ENG-123', title='修复状态接口',
                          url='https://linear.app/example/issue/ENG-123', description='接口任务',
                          state_type='started', state_name='进行中', viewer_id='person', assignee_id='person')
        self.fetch = Mock(side_effect=lambda *a, **kw: {'viewer': {'id': 'person', 'name': '员工'}, 'issues': [dict(self.issue)]})
        self.details = Mock(side_effect=lambda *a, **kw: dict(self.issue))
        self.writer = Mock()
        self.orbit = Orbit(self.root / 'data', fetch=self.fetch, details=self.details, writer=self.writer)
        self.orbit.credential = Mock(return_value='test-key')

    def invoke(self, name, **arguments):
        return call(self.orbit, name, arguments, self.cancel)

    def start(self, **options):
        return self.invoke('start_task', identifier='ENG-123', work_dir=str(self.work), **options)['task']

    def draft(self, task=None, **options):
        task = task or self.start()
        arguments = dict(task_id=task['id'], status='implemented_waiting_validation', completed='已实现接口',
                         changes='api.py: 增加返回字段', tests='python -m unittest: 3 passed；尚未现场验证',
                         remaining='现场验证', blockers='', next_step='检查现场输出')
        arguments.update(options)
        return self.invoke('draft_result', **arguments)

    def test_discovery_does_not_initialize_or_use_network(self):
        result = self.invoke('status')
        self.assertTrue(result['credential_configured'])
        self.assertFalse(self.orbit.directory.exists())
        self.fetch.assert_not_called()
        self.details.assert_not_called()
        self.writer.assert_not_called()

    def test_acquisition_is_idempotent_and_preserves_source(self):
        task = self.start(session_id='session-1')
        again = self.start(session_id='session-1')
        self.assertEqual(task['id'], again['id'])
        self.assertEqual(again['revision'], 1)
        self.assertEqual(again['work_dir'], str(self.work))
        self.assertEqual(again['session_url'], 'codex://threads/session-1')
        self.assertEqual(self.invoke('list_my_issues')['issues'][0]['local_task_id'], task['id'])
        self.writer.assert_not_called()
        self.assertEqual(list(self.work.iterdir()), [])

    def test_requires_my_issue_and_current_owner(self):
        self.issue['identifier'] = 'ENG-124'
        with self.assertRaisesRegex(OrbitError, 'My Issues'):
            self.start()
        self.issue['identifier'] = 'ENG-123'
        self.issue['assignee_id'] = 'someone-else'
        with self.assertRaisesRegex(OrbitError, '负责人'):
            self.start()
        self.assertFalse(self.orbit.directory.exists())

    def test_context_replacement_is_explicit_and_old_results_stay_bound(self):
        task = self.start(session_id='first')
        result = self.draft(task)
        other = self.root / 'other'
        other.mkdir()
        with self.assertRaisesRegex(OrbitError, '已有'):
            self.invoke('start_task', identifier='ENG-123', work_dir=str(other), session_id='second')
        changed = self.invoke('start_task', identifier='ENG-123', work_dir=str(other), session_id='second', replace_context=True)['task']
        self.assertEqual(changed['revision'], 2)
        self.assertEqual(changed['id'], task['id'])
        old = self.invoke('preview_result', result_id=result['id'])['snapshot']['task']
        self.assertEqual(old['work_dir'], str(self.work))
        self.assertEqual(old['session_id'], 'first')

    def test_drafts_deduplicate_across_refresh_and_process_restart(self):
        task = self.start()
        first = self.draft(task)
        self.start()
        self.assertEqual(self.draft(task)['id'], first['id'])
        reopened = Orbit(self.orbit.directory)
        self.assertEqual(reopened.preview_result(first['id'])['body_sha256'], first['body_sha256'])
        changed = self.draft(task, completed='还修复了取消逻辑')
        self.assertNotEqual(first['id'], changed['id'])
        self.assertEqual(len(self.invoke('list_results', task_id=task['id'])['results']), 2)

    def test_validation_and_missing_evidence(self):
        for arguments in ({'limit': True}, {'limit': 0}, {'limit': 101}, {'unknown': 1}):
            with self.assertRaises(OrbitError):
                validate('list_tasks', arguments)
        with self.assertRaises(OrbitError):
            self.invoke('start_task', identifier='ENG-123', work_dir='relative')
        with self.assertRaises(OrbitError):
            self.start(session_id='guessed/url')
        with self.assertRaisesRegex(OrbitError, '证据'):
            self.draft(status='validated', tests='')

    def test_delivery_is_explicit_and_checks_preview(self):
        result = self.draft()
        for confirm, digest in [(False, result['body_sha256']), (True, '0' * 64)]:
            with self.assertRaises(OrbitError):
                self.invoke('publish_result', result_id=result['id'], body_sha256=digest, confirm=confirm)
        self.writer.assert_not_called()
        self.assertEqual(self.invoke('preview_result', result_id=result['id'])['state'], 'draft')

    def test_real_writer_readback_and_retry_after_uncertain_success(self):
        from linear_support import LinearWriter
        result = self.draft()
        comments, operations = {}, []
        fail_once = [True]

        def graphql(key, variables, query):
            operations.append(query)
            if 'commentCreate' in query:
                item = variables['input']
                comments[item['id']] = {'id': item['id'], 'body': item['body'], 'issue': {'id': item['issueId']}}
                if fail_once.pop() if fail_once else False:
                    raise TimeoutError('response lost after successful creation')
                return {'commentCreate': {'success': True, 'comment': {'id': item['id']}}}
            return {'comments': {'nodes': [comments[variables['filter']['id']['eq']]] if variables['filter']['id']['eq'] in comments else []}}

        self.orbit.writer = LinearWriter
        arguments = dict(result_id=result['id'], body_sha256=result['body_sha256'], confirm=True)
        with patch('linear_support.graphql', side_effect=graphql):
            with self.assertRaises(TimeoutError):
                self.invoke('publish_result', **arguments)
            self.assertEqual(self.invoke('preview_result', result_id=result['id'])['state'], 'draft')
            published = self.invoke('publish_result', **arguments)
            count = len(operations)
            repeated = self.invoke('publish_result', **arguments)
        self.assertEqual(len(comments), 1)
        self.assertEqual(sum('mutation' in query for query in operations), 1)
        self.assertEqual(len(operations), count)
        self.assertTrue(repeated['already_published'])
        self.assertEqual(published['comment_id'], result['id'])
        self.assertEqual(comments[result['id']]['issue']['id'], self.issue['id'])
        self.assertEqual(comments[result['id']]['body'], result['body'])

    def test_remote_edit_is_not_overwritten(self):
        from linear_support import LinearWriter, LinearError
        result = self.draft()
        self.orbit.writer = LinearWriter
        response = {'comments': {'nodes': [{'id': result['id'], 'body': 'human edit', 'issue': {'id': self.issue['id']}}]}}
        with patch('linear_support.graphql', return_value=response) as transport:
            with self.assertRaises(LinearError):
                self.invoke('publish_result', result_id=result['id'], body_sha256=result['body_sha256'], confirm=True)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(self.invoke('preview_result', result_id=result['id'])['state'], 'draft')

    def test_owner_change_and_cancellation_block_new_writes(self):
        result = self.draft()
        self.issue['assignee_id'] = 'different'
        with self.assertRaises(OrbitError):
            self.invoke('publish_result', result_id=result['id'], body_sha256=result['body_sha256'], confirm=True)
        self.cancel.set()
        with self.assertRaises(InterruptedError):
            self.invoke('publish_result', result_id=result['id'], body_sha256=result['body_sha256'], confirm=True)
        self.writer.assert_not_called()

    def test_concurrent_sessions_keep_one_assignment_and_draft(self):
        def worker(_):
            return self.draft(self.start())['id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            identifiers = list(pool.map(worker, range(8)))
        self.assertEqual(len(set(identifiers)), 1)
        self.assertEqual(len(self.orbit.list_tasks()['tasks']), 1)

    def test_redacts_credential_patterns_before_publication(self):
        result = self.draft(changes='lin_api_abcdefghijklmn and sk-abcdefghijklmnop')
        self.assertNotIn('lin_api_', result['body'])
        self.assertNotIn('sk-abc', result['body'])
        self.assertNotIn('lin_api_', json.dumps(result['snapshot']['result']))

    def test_native_project_dispatch_is_reserved_once_and_bound_idempotently(self):
        task = self.start()
        projects = [{'projectId': 'native-project', 'projectKind': 'local', 'hostId': 'local', 'path': str(self.work)}]
        reserved = self.invoke('prepare_chat', task_id=task['id'], projects=projects)
        self.assertEqual(reserved['state'], 'ready')
        self.assertEqual(reserved['target']['projectId'], 'native-project')
        self.assertIn(task['id'], reserved['prompt'])
        again = self.invoke('prepare_chat', task_id=task['id'], projects=projects)
        self.assertEqual(again['state'], 'dispatch_pending')
        self.assertFalse(again['create_new'])
        args = dict(task_id=task['id'], dispatch_token=reserved['dispatch_token'], project_id='native-project', thread_id='real-session')
        first = self.invoke('bind_project_chat', **args)
        self.assertEqual(self.invoke('bind_project_chat', **args), first)
        self.assertEqual(self.invoke('prepare_chat', task_id=task['id'], projects=projects)['state'], 'bound')
        self.assertEqual(self.invoke('get_task', task_id=task['id'])['session_url'], 'codex://threads/real-session')
        self.assertIn('codex://threads/real-session', self.draft()['body'])
        with self.assertRaises(OrbitError):
            self.invoke('bind_project_chat', **{**args, 'thread_id': 'different-session'})

    def test_unregistered_project_never_returns_a_fake_create_target(self):
        task = self.start()
        result = self.invoke('prepare_chat', task_id=task['id'], projects=[])
        self.assertEqual(result['state'], 'project_registration_required')
        self.assertNotIn('target', result)
        self.assertFalse(list(self.work.iterdir()))

    def test_dispatch_cannot_overwrite_a_context_changed_during_creation(self):
        task = self.start()
        projects = [{'projectId': 'native', 'projectKind': 'local', 'hostId': 'local', 'path': str(self.work)}]
        reserved = self.invoke('prepare_chat', task_id=task['id'], projects=projects)
        self.start(session_id='user-bound-session', replace_context=True)
        with self.assertRaises(OrbitError):
            self.invoke('bind_project_chat', task_id=task['id'], dispatch_token=reserved['dispatch_token'], project_id='native', thread_id='new-chat')
        self.assertEqual(self.invoke('get_task', task_id=task['id'])['session_id'], 'user-bound-session')


class CodexPackageTests(unittest.TestCase):
    def test_first_use_requests_settings_once_without_exposing_a_key(self):
        script = '''
import sys
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from orbit_plugin.core import Orbit,OrbitError
from orbit_plugin.server import serve
class MissingOrbit(Orbit):
    auto_setup=True
    def credential(self):raise OrbitError('missing_credentials','请配置')
    def open_settings(self):return {'state':'opening'}
serve(MissingOrbit(sys.argv[3]))
'''
        with tempfile.TemporaryDirectory() as temporary:
            process = subprocess.Popen([sys.executable, '-c', script, str(PLUGIN), str(ROOT), temporary],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            try:
                replies = []
                for number in (1, 2):
                    process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': number, 'method': 'tools/call', 'params': {'name': 'list_my_issues', 'arguments': {}}}) + '\n')
                    process.stdin.flush()
                    result = json.loads(process.stdout.readline())['result']
                    replies.append(json.loads(result['content'][0]['text']))
                self.assertEqual(replies[0]['settings']['state'], 'opening')
                self.assertNotIn('settings', replies[1])
                self.assertEqual(replies[1]['code'], 'missing_credentials')
            finally:
                process.stdin.close()
                process.wait(timeout=10)
                process.stdout.close()
                process.stderr.close()
            self.assertEqual(process.returncode, 0)

    def test_protocol_cancellation_keeps_session_usable(self):
        script = '''
import sys,threading
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from orbit_plugin.core import Orbit
from orbit_plugin.server import serve
class WaitingOrbit(Orbit):
    def list_my_issues(self,cancel,limit=20):
        while not cancel.wait(.02): pass
        raise InterruptedError()
serve(WaitingOrbit(sys.argv[3]))
'''
        with tempfile.TemporaryDirectory() as temporary:
            process = subprocess.Popen([sys.executable, '-c', script, str(PLUGIN), str(ROOT), temporary],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding='utf-8')
            try:
                for message in [
                    {'id': 1, 'method': 'tools/call', 'params': {'name': 'list_my_issues', 'arguments': {}}},
                    {'method': 'notifications/cancelled', 'params': {'requestId': 1}},
                    {'id': 2, 'method': 'ping', 'params': {}}]:
                    process.stdin.write(json.dumps({'jsonrpc': '2.0', **message}) + '\n')
                process.stdin.flush()
                error = json.loads(process.stdout.readline())
                self.assertEqual(json.loads(error['result']['content'][0]['text'])['code'], 'cancelled')
                self.assertEqual(json.loads(process.stdout.readline())['result'], {})
            finally:
                process.stdin.close()
                process.wait(timeout=10)
                process.stdout.close()
                process.stderr.close()
            self.assertEqual(process.returncode, 0)

    def test_standalone_package_handshake_and_clean_install_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = importlib.util.spec_from_file_location('build_codex_plugin', ROOT / 'scripts/build_codex_plugin.py')
            build = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(build)
            archive = build.build(root / 'marketplace')
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(root / '移动 测试')
                self.assertFalse(any('.credentials' in name or '__pycache__' in name or '.venv' in name for name in bundle.namelist()))
            plugin = root / '移动 测试/plugins/codex-orbit'
            data = root / 'custom data'
            configure = subprocess.run([sys.executable, str(plugin / 'scripts/configure.py'), '--data-dir', str(data)], capture_output=True, text=True, check=True)
            self.assertIn('Python', configure.stdout)
            manifest = json.loads((plugin / 'mcp.json').read_text())
            self.assertEqual(manifest['mcpServers']['orbit']['command'], sys.executable)
            env = {key: value for key, value in os.environ.items() if key not in ('LINEAR_API_KEY', 'ORBIT_DATA_DIR', 'ORBIT_CREDENTIALS_DIR', 'PYTHONPATH')}
            process = subprocess.Popen([sys.executable, str(plugin / 'scripts/mcp.py')], cwd=root, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', env=env)
            try:
                def rpc(number, method, params):
                    process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': number, 'method': method, 'params': params}) + '\n')
                    process.stdin.flush()
                    return json.loads(process.stdout.readline())
                handshake = rpc(1, 'initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}})
                self.assertEqual(handshake['result']['serverInfo']['name'], 'orbit')
                tools = rpc(2, 'tools/list', {})['result']['tools']
                self.assertEqual(len(tools), 12)
                self.assertNotIn('sync_once', {tool['name'] for tool in tools})
                status = json.loads(rpc(3, 'tools/call', {'name': 'status', 'arguments': {}})['result']['content'][0]['text'])
                self.assertEqual(status['data_directory'], str(data))
                self.assertFalse(data.exists())
                error = rpc(4, 'tools/call', {'name': 'list_my_issues', 'arguments': {}})['result']
                self.assertTrue(error['isError'])
                self.assertEqual(json.loads(error['content'][0]['text'])['code'], 'missing_credentials')
                malformed = rpc(5, 'tools/call', {'name': 'publish_result', 'arguments': {'confirm': False}})['result']
                self.assertTrue(malformed['isError'])
            finally:
                process.stdin.close()
                process.wait(timeout=10)
                stderr = process.stderr.read()
                process.stdout.close()
                process.stderr.close()
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stderr, '')
            self.assertFalse(data.exists())


if __name__ == '__main__':
    unittest.main()
