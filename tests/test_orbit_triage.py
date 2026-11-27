"""Read-only inventory behavior, including pagination and stale local bindings."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'plugins/codex-orbit/skills/orbit-triage/scripts/issue_inventory.py'
spec = importlib.util.spec_from_file_location('orbit_triage_inventory', SCRIPT)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


def connection(rows, after):
    offset = int(after or 0)
    more = offset + 50 < len(rows)
    return {'nodes': rows[offset:offset + 50],
            'pageInfo': {'hasNextPage': more, 'endCursor': str(offset + 50) if more else None}}


class InventoryTests(unittest.TestCase):
    def test_installer_schedule_setting_remains_compatible_with_inventory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(inventory, 'ROOT', Path(directory)):
            path = Path(directory)/'settings.json'
            for enabled in (True, False):
                settings = {'data_dir': str(Path(directory)/'data'), 'auto_daily_automation': enabled}
                path.write_text(json.dumps(settings))
                self.assertEqual(inventory.settings(), settings)
            path.write_text(json.dumps({'auto_daily_automation': 'true'}))
            with self.assertRaises(inventory.OrbitError):
                inventory.settings()

    def request(self, query, variables):
        self.assertTrue(query.startswith('query'))
        if 'OrbitTriageIssues' in query:
            self.assertEqual(variables['filter']['assignee'], {'id': {'eq': 'viewer'}})
            rows = [{'id': str(n), 'identifier': f'ENG-{n}', 'assignee': {'id': 'viewer'},
                     'state': {'type': 'started'}} for n in range(103)]
            return {'issues': connection(rows, variables['after'])}
        if 'OrbitTriageContext' in query:
            field = next(field for field in inventory.CONNECTIONS if field + '(first:' in query)
            rows = [{'id': f'{field}-{n}'} for n in range(57)]
            return {'issue': {field: connection(rows, variables['after'])}}
        return {'viewer': {'id': 'viewer', 'name': 'Tester'}}

    def test_all_issue_pages_and_nested_pages_with_selection(self):
        result = inventory.collect(self.request, ['eng-102', 'ENG-999'])
        self.assertEqual(result['total'], 103)
        self.assertEqual(result['selected_count'], 1)
        self.assertEqual(result['missing_identifiers'], ['ENG-999'])
        issue = result['issues'][0]
        self.assertEqual(issue['identifier'], 'ENG-102')
        for field in inventory.CONNECTIONS:
            self.assertEqual(len(issue[field]), 57)

    def test_empty_scope_is_successful_and_has_no_context_requests(self):
        def request(query, variables):
            if 'OrbitTriageIssues' in query:
                return {'issues': connection([], None)}
            self.assertNotIn('OrbitTriageContext', query)
            return {'viewer': {'id': 'viewer', 'name': 'Tester'}}
        self.assertEqual(inventory.collect(request)['total'], 0)

    def test_pagination_duplicates_do_not_inflate_count(self):
        def request(query, variables):
            after = variables['after']
            return {'issues': {'nodes': [{'id': '1'}] if after is None else [{'id': '1'}, {'id': '2'}],
                               'pageInfo': {'hasNextPage': after is None, 'endCursor': 'next'}}}
        self.assertEqual(len(inventory.pages(request, 'query', {}, ['issues'])), 2)

    def test_repeated_or_missing_cursor_fails(self):
        for cursor in ('repeat', None):
            with self.subTest(cursor=cursor), self.assertRaises(inventory.LinearError):
                inventory.pages(lambda q, v: {'issues': {'nodes': [], 'pageInfo': {
                    'hasNextPage': True, 'endCursor': cursor}}}, 'query', {}, ['issues'])

    def test_nested_failure_does_not_return_partial_snapshot(self):
        def request(query, variables):
            if 'comments(first:' in query:
                raise inventory.LinearError('failed')
            return self.request(query, variables)
        with self.assertRaises(inventory.LinearError):
            inventory.collect(request, ['ENG-0'])

    def test_identity_change_and_wrong_assignee_fail(self):
        def request(query, variables):
            if query == 'query { viewer { id } }':
                return {'viewer': {'id': 'other'}}
            return self.request(query, variables)
        with self.assertRaises(inventory.LinearError):
            inventory.collect(request, ['ENG-0'])
        def reassigned(query, variables):
            result = self.request(query, variables)
            if 'issues' in result:
                result['issues']['nodes'][0]['assignee'] = {'id': 'other'}
            return result
        with self.assertRaises(inventory.LinearError):
            inventory.collect(reassigned, ['ENG-0'])

    def test_bindings_are_scoped_and_database_remains_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = {'viewer': {'id': 'viewer'}, 'issues': [{'id': '1'}, {'id': '2'}], 'warnings': []}
            self.assertEqual(inventory.local_bindings(root / 'missing', snapshot), [])
            self.assertFalse((root / 'missing').exists())
            path = root / 'codex.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE tasks (id,issue_id,issue,work_dir,session_id,updated_at)')
                for n, viewer in [('1', 'viewer'), ('2', 'other'), ('3', 'viewer')]:
                    db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',
                               (n, n, json.dumps({'viewer_id': viewer}), '/repo', 'chat-' + n, 'now'))
            before = path.read_bytes()
            result = inventory.local_bindings(root, snapshot)
            self.assertEqual([r['session_id'] for r in result], ['chat-1'])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(snapshot['warnings'], [])

    def test_unreadable_binding_store_preserves_remote_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'codex.sqlite3').write_text('invalid')
            snapshot = {'viewer': {'id': 'viewer'}, 'issues': [{'id': '1'}], 'warnings': []}
            self.assertEqual(inventory.local_bindings(directory, snapshot), [])
            self.assertEqual(len(snapshot['issues']), 1)
            self.assertEqual(len(snapshot['warnings']), 1)

    def test_cli_missing_credentials_and_existing_output_have_no_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {k: v for k, v in os.environ.items() if k not in (
                'LINEAR_API_KEY', 'ORBIT_DATA_DIR', 'ORBIT_CREDENTIALS_DIR')}
            command = [sys.executable, str(SCRIPT), '--data-dir', str(root / 'data'),
                       '--credentials-dir', str(root / 'credentials')]
            run = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertNotIn('Traceback', run.stderr)
            self.assertEqual(list(root.iterdir()), [])
            path = root / 'snapshot.json'
            path.write_text('existing')
            run = subprocess.run([*command, '--output', str(path)], env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(path.read_text(), 'existing')
            self.assertEqual(list(root.iterdir()), [path])


if __name__ == '__main__':
    unittest.main()
