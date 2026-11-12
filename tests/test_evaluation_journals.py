import copy
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from daily_store import DailyStore
from evaluation.journals import JournalExporter, ReplicaStore
from evaluation.protocol import (FIELDS, OPTIONAL_FIELDS, PROTOCOL, canonical,
                                 record_digest, record_id, validate_record)
from model import make_task

START = '2026-11-10T22:00:00+08:00'
END = '2026-11-11T22:00:00+08:00'


def task(title='工作一', category='work', thread='chat-one', number=1):
    result = make_task(title, category)
    result['linear_issue'] = dict(id=str(uuid.uuid4()), identifier='ENG-' + str(number),
                                  url='https://linear.app/test/issue/ENG-' + str(number) + '/sample',
                                  state_type='started')
    result['bindings'] = [dict(kind='codex', label=title, url='codex://threads/' + thread)]
    return result


def progress(row_task, **updates):
    result = dict(id=str(uuid.uuid4()), kind='execution', text='已完成接口实现', source='',
                  created_at='2026-11-11T12:00:00+08:00', task_id=row_task['id'],
                  title=row_task['title'], category=row_task['category'])
    result.update(updates)
    return result


def report(tasks, mapping=None, summaries=None, evidence=None, **updates):
    result = dict(start=START, end=END, tasks=copy.deepcopy(tasks),
                  groups={'chat-one': [{'role': 'user', 'text': 'RAW_CHAT_MUST_STAY_LOCAL'}]},
                  mapping=mapping or {'chat-one': [tasks[0]['id']]},
                  summaries=summaries if summaries is not None else {'chat-one': '已交付接口，联调待完成。'},
                  evidence=evidence or [], git={'private': '/home/private/repo'},
                  markdown='COMBINED_MARKDOWN_MUST_STAY_LOCAL',
                  operations={'credential': 'DATABASE_OPERATION_MUST_STAY_LOCAL'})
    result.update(updates)
    return result


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.daily = DailyStore(self.root / 'daily')
        self.manual = DailyStore(self.root / 'work-logs')
        self.service = SimpleNamespace(store=self.daily, work_logs=SimpleNamespace(store=self.manual))
        self.employee = str(uuid.uuid4())
        self.work = task()
        self.tasks = SimpleNamespace(tasks=[self.work])
        self.exporter = JournalExporter(self.service, self.tasks, self.employee, '测试员工', '电气')

    def tearDown(self):
        self.daily.close()
        self.manual.close()
        self.temp.cleanup()

    def save(self, content, identifier='2026-11-11', store=None):
        (store or self.daily).save_report(identifier, 'done', content, [])

    def test_export_is_per_task_whitelist_not_database_or_combined_markdown(self):
        other = task('未授权工作', thread='private-chat', number=2)
        study = task('学习', 'learn', 'study-chat', 3)
        demo = task('Demo', 'demo', 'demo-chat', 4)
        self.tasks.tasks += [other, study, demo]
        record = progress(self.work, text='lin_api_test_secret_123 api_key=hiddenvalue 已验证',
                          source='/home/private/secret.txt')
        data = report(self.tasks.tasks,
                      mapping={'chat-one': [self.work['id']], 'private-chat': [other['id']],
                               'study-chat': [study['id']], 'demo-chat': [demo['id']]},
                      summaries={'chat-one': '接口已实现 /home/private/repo secret=privatevalue',
                                 'private-chat': 'PRIVATE_SUMMARY', 'study-chat': 'STUDY_SUMMARY',
                                 'demo-chat': 'DEMO_SUMMARY'},
                      evidence=[record, progress(other, text='PRIVATE_EVIDENCE')])
        self.save(data)
        with self.daily.lock, self.daily.db:
            self.daily.db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?)',
                                  ('/home/private/chat.jsonl', 1, 'chat-one', START, 'user', 'SQLITE_RAW_TEXT'))
            self.daily.db.execute('INSERT INTO operations VALUES(?,?,?,?,?)',
                                  ('private-op', 'remote-id', 'done', '{"token":"SQLITE_CREDENTIAL"}', '{}'))
        rows = self.exporter.records([self.work['id'], study['id'], demo['id']])
        self.assertEqual(len(rows), 1)
        encoded = json.dumps(rows, ensure_ascii=False)
        for forbidden in ('PRIVATE_SUMMARY', 'STUDY_SUMMARY', 'DEMO_SUMMARY', 'PRIVATE_EVIDENCE',
                          'RAW_CHAT_MUST_STAY_LOCAL', 'COMBINED_MARKDOWN', 'DATABASE_OPERATION',
                          'SQLITE_RAW_TEXT', 'SQLITE_CREDENTIAL', '/home/private',
                          'test_secret_123', 'hiddenvalue', 'privatevalue'):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(rows[0]['source_device'], self.employee)
        self.assertEqual(rows[0]['issue_id'], self.work['linear_issue']['id'])
        self.assertEqual(len(rows[0]['evidence']), 1)
        self.assertNotIn('task_id', rows[0]['evidence'][0])
        self.assertEqual(validate_record(rows[0]), rows[0])
        self.assertEqual(self.exporter.records([]), [])

    def test_historical_or_current_multi_task_chat_never_leaks_summary(self):
        other = task('另一项', thread='chat-one', number=2)
        self.tasks.tasks.append(other)
        self.save(report(self.tasks.tasks,
                         mapping={'chat-one': [self.work['id'], other['id']]},
                         summaries={'chat-one': 'MIXED_TASK_SECRET'}))
        rows = self.exporter.records([self.work['id']])
        self.assertEqual(len(rows), 1)
        self.assertIn('摘要缺口', rows[0]['summary'])
        self.assertNotIn('MIXED_TASK_SECRET', json.dumps(rows))
        # Even a historical singleton cannot hide a current second binding.
        self.save(report([self.work], summaries={'chat-one': 'CURRENT_MIXED_SECRET'}))
        self.assertNotIn('CURRENT_MIXED_SECRET', json.dumps(self.exporter.records([self.work['id']])))

    def test_missing_summary_and_removed_binding_expose_only_explicit_gap(self):
        self.save(report([self.work], summaries={}))
        self.assertIn('尚未生成', self.exporter.records([self.work['id']])[0]['summary'])
        self.save(report([self.work], summaries={'chat-one': 'OLD_BINDING_SECRET'}))
        self.work['bindings'] = []
        rows = self.exporter.records([self.work['id']])
        self.assertIn('解除', rows[0]['summary'])
        self.assertNotIn('OLD_BINDING_SECRET', json.dumps(rows))

    def test_actual_unique_issue_is_required_and_rebinding_rejects_old_issue(self):
        self.save(report([self.work]))
        saved_issue = self.work.pop('linear_issue')
        self.assertEqual(self.exporter.records([self.work['id']]), [])
        self.work['linear_issue'] = saved_issue
        duplicate = task('重复 issue', thread='different')
        duplicate['linear_issue'] = saved_issue
        self.tasks.tasks.append(duplicate)
        self.assertEqual(self.exporter.records([self.work['id']]), [])
        self.tasks.tasks.pop()
        self.work['linear_issue'] = dict(saved_issue, id=str(uuid.uuid4()))
        self.assertEqual(self.exporter.records([self.work['id']]), [])

    def test_newly_synced_issue_can_fill_frozen_report_but_old_url_cannot(self):
        frozen = copy.deepcopy(self.work)
        frozen.pop('linear_issue')
        self.save(report([frozen]))
        self.assertEqual(self.exporter.records([self.work['id']])[0]['issue_id'], self.work['linear_issue']['id'])
        frozen['bindings'].append(dict(kind='linear', url='https://linear.app/test/issue/ENG-999/old', label='ENG-999'))
        self.save(report([frozen]))
        self.assertEqual(self.exporter.records([self.work['id']]), [])

    def test_nightly_today_week_keep_distinct_sources_and_stable_ids(self):
        data = report([self.work])
        self.save(data)
        self.save(dict(data, kind='today'), 'today-20261111', self.manual)
        self.save(dict(data, kind='week'), 'week-20261111', self.manual)
        rows = self.exporter.records([self.work['id']])
        self.assertEqual({r['kind'] for r in rows}, {'nightly', 'today', 'week'})
        self.assertEqual(len({r['id'] for r in rows}), 3)
        self.assertEqual(rows, self.exporter.records([self.work['id']]))
        self.assertEqual({r['source_id'] for r in rows},
                         {'nightly:2026-11-11', 'today:today-20261111', 'week:week-20261111'})

    def test_export_capacity_failure_never_returns_truncated_manifest(self):
        data = report([self.work])
        self.save(data)
        self.save(dict(data, kind='today'), 'today-20261111', self.manual)
        with patch('evaluation.journals.MAX_BATCH', 1):
            with self.assertRaisesRegex(ValueError, '10000'):
                self.exporter.records([self.work['id']])
        with patch('evaluation.journals.MAX_SNAPSHOT_BYTES', 100):
            with self.assertRaisesRegex(ValueError, '32 MiB'):
                self.exporter.records([self.work['id']])

    def test_current_explicit_assignee_is_optional_and_never_inferred_from_viewer_or_author(self):
        # Historical logs predate the metadata. Only the current issue snapshot
        # may add it, and it is independent of the employee who wrote the log.
        self.save(report([self.work]))
        viewer, assignee = str(uuid.uuid4()), str(uuid.uuid4())
        self.work['linear_issue'].update(viewer_id=viewer, assignee_id=assignee)
        row = self.exporter.records([self.work['id']])[0]
        self.assertEqual(row['linear_assignee_id'], assignee)
        self.assertNotEqual(assignee, row['employee_id'])
        self.assertNotIn('viewer_id', row)
        self.assertEqual(validate_record(row), row)
        del self.work['linear_issue']['assignee_id']
        unknown = self.exporter.records([self.work['id']])[0]
        self.assertNotIn('linear_assignee_id', unknown)
        self.assertEqual(set(unknown), FIELDS)
        self.assertNotEqual(row['digest'], unknown['digest'])
        # An old log's owner cannot fill a currently unknown assignee either.
        frozen = copy.deepcopy(self.work)
        frozen['linear_issue']['assignee_id'] = assignee
        self.save(report([frozen]))
        self.assertNotIn('linear_assignee_id', self.exporter.records([self.work['id']])[0])

    def test_invalid_or_missing_assignee_is_omitted_without_losing_authorized_log(self):
        self.save(report([self.work]))
        for invalid in (None, '', 'not-a-uuid', str(uuid.uuid4()).upper(), 1, True, [], {}):
            with self.subTest(assignee=invalid):
                self.work['linear_issue']['assignee_id'] = invalid
                rows = self.exporter.records([self.work['id']])
                self.assertEqual(len(rows), 1)
                self.assertNotIn('linear_assignee_id', rows[0])
                validate_record(rows[0])


class ReplicaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ReplicaStore(self.root)
        self.employee = str(uuid.uuid4())

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def record(self, source='nightly:2026-11-11'):
        issue = task()['linear_issue']
        tid = str(uuid.uuid4())
        result = dict(id=record_id(source, tid), employee_id=self.employee, name='员工', department='电气',
                      source_device=self.employee, source_id=source, kind='nightly', period_start=START,
                      period_end=END, task_id=tid, task_title='接口工作', issue_id=issue['id'],
                      issue_identifier=issue['identifier'], issue_url=issue['url'], parent_issue_id='',
                      linear_state_type='started', summary='实现已完成，等待联调。', evidence=[])
        result['digest'] = record_digest(result)
        return result

    def test_canonical_digest_and_strict_wire_validation(self):
        record = self.record()
        reverse = dict(reversed(list(record.items())))
        self.assertEqual(canonical(record), canonical(reverse))
        self.assertEqual(record_digest(record), record['digest'])
        validate_record(record)
        for changes in ({'summary': '伪造内容'}, {'source_device': str(uuid.uuid4())},
                        {'period_start': '2026-11-11T12:00:00'}, {'raw_groups': []},
                        {'issue_url': 'https://other.example/issue/ENG-1'},
                        {'id': '0' * 64}, {'summary': 'a' * 100001}):
            invalid = dict(record, **changes)
            if set(changes) != {'summary'}:
                invalid['digest'] = record_digest(invalid)
            with self.assertRaises(ValueError):
                validate_record(invalid)

    def test_optional_assignee_preserves_v1_required_fields_and_rejects_unknown_metadata(self):
        legacy = self.record()
        self.assertEqual(PROTOCOL, 'orbit-evaluator/v1')
        self.assertEqual(set(legacy), FIELDS)
        self.assertEqual(OPTIONAL_FIELDS, {'linear_assignee_id'})
        self.assertEqual(validate_record(legacy), legacy)
        upgraded = dict(legacy, linear_assignee_id=str(uuid.uuid4()))
        upgraded['digest'] = record_digest(upgraded)
        self.assertEqual(validate_record(upgraded), upgraded)
        self.assertNotEqual(upgraded['digest'], legacy['digest'])
        tampered = dict(upgraded, linear_assignee_id=str(uuid.uuid4()))
        with self.assertRaisesRegex(ValueError, '摘要校验'):
            validate_record(tampered)
        for invalid in (None, '', 'not-a-uuid', str(uuid.uuid4()).upper(), 1, True, [], {}):
            row = dict(legacy, linear_assignee_id=invalid)
            row['digest'] = record_digest(row)
            with self.subTest(assignee=invalid), self.assertRaisesRegex(ValueError, '负责人 UUID'):
                validate_record(row)
        for field in ('viewer_id', 'linear_account_token', 'raw_groups'):
            row = dict(upgraded, **{field: str(uuid.uuid4())})
            row['digest'] = record_digest(row)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, '未授权字段'):
                validate_record(row)
        for field in FIELDS:
            row = copy.deepcopy(upgraded)
            del row[field]
            with self.subTest(missing=field), self.assertRaisesRegex(ValueError, '不完整'):
                validate_record(row)

    def test_optional_assignee_round_trips_and_creates_revision_without_schema_migration(self):
        legacy = self.record()
        self.store.ingest(self.employee, [legacy])
        upgraded = dict(legacy, linear_assignee_id=str(uuid.uuid4()))
        upgraded['digest'] = record_digest(upgraded)
        self.assertEqual(self.store.ingest(self.employee, [upgraded]), dict(created=0, updated=1, unchanged=0))
        self.assertEqual(self.store.ingest(self.employee, [upgraded]), dict(created=0, updated=0, unchanged=1))
        self.store.close()
        self.store = ReplicaStore(self.root)
        self.assertEqual(self.store.records(), [upgraded])
        revisions = [json.loads(row[0]) for row in self.store.db.execute('SELECT content FROM revisions')]
        self.assertCountEqual(revisions, [legacy, upgraded])

    def test_idempotent_update_preserves_revision_and_survives_reopen(self):
        record = self.record()
        self.assertEqual(self.store.ingest(self.employee, [record]), dict(created=1, updated=0, unchanged=0))
        self.assertEqual(self.store.ingest(self.employee, [record]), dict(created=0, updated=0, unchanged=1))
        changed = dict(record, summary='补充：联调已通过，待验收。')
        changed['digest'] = record_digest(changed)
        self.assertEqual(self.store.ingest(self.employee, [changed]), dict(created=0, updated=1, unchanged=0))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 2)
        changed['summary'] = '调用方后续修改不可污染副本'
        self.store.close()
        self.store = ReplicaStore(self.root)
        self.assertEqual(self.store.records()[0]['summary'], '补充：联调已通过，待验收。')
        self.assertEqual(self.store.employees()[0]['record_count'], 1)

    def test_cross_employee_tamper_and_duplicate_batch_are_rejected_before_write(self):
        record = self.record()
        invalid = self.record('nightly:2026-11-10')
        invalid['summary'] = '修改后未重算哈希'
        for batch in ([record, invalid], [record, record]):
            with self.assertRaises(ValueError):
                self.store.ingest(self.employee, batch)
            self.assertEqual(self.store.records(), [])
            self.assertEqual(self.store.employees(), [])
        with self.assertRaises(ValueError):
            self.store.ingest(str(uuid.uuid4()), [record])
        self.assertEqual(self.store.records(), [])

    def test_database_failure_rolls_back_records_revisions_and_employee(self):
        first, second = self.record(), self.record('nightly:2026-11-10')
        self.store.db.execute('''CREATE TRIGGER reject_second BEFORE INSERT ON records
          WHEN NEW.id=''' + "'" + second['id'] + "'" + ''' BEGIN SELECT RAISE(ABORT,'test failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.ingest(self.employee, [first, second])
        self.assertEqual(self.store.records(), [])
        self.assertEqual(self.store.employees(), [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 0)

    def test_complete_manifest_revokes_query_without_deleting_history(self):
        first, second = self.record(), self.record('nightly:2026-11-10')
        self.store.ingest(self.employee, [first, second])
        with self.assertRaises(ValueError):
            self.store.apply_manifest(self.employee, [dict(id=first['id'], digest='0' * 64)])
        self.assertEqual(len(self.store.records()), 2)
        self.store.apply_manifest(self.employee, [dict(id=first['id'], digest=first['digest'])])
        self.assertEqual([r['id'] for r in self.store.records()], [first['id']])
        self.assertEqual(self.store.employees()[0]['record_count'], 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 2)
        self.store.apply_manifest(self.employee, [])
        self.assertEqual(self.store.records(), [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM records').fetchone()[0], 2)

    def test_error_does_not_advance_last_success_and_employee_identity_is_not_name(self):
        self.store.mark_sync(self.employee, '同名员工', '电气')
        before = self.store.employees()[0]['last_synced']
        self.store.mark_sync(self.employee, '同名员工', '电气', '离线')
        self.assertEqual(self.store.employees()[0]['last_synced'], before)
        self.assertEqual(self.store.employees()[0]['error'], '离线')
        self.store.mark_sync(str(uuid.uuid4()), '同名员工', '电气')
        self.assertEqual(len(self.store.employees()), 2)

    def test_received_payload_does_not_mark_an_incomplete_sync_successful(self):
        record = self.record()
        self.store.ingest(self.employee, [record])
        self.assertEqual(self.store.employees()[0]['last_synced'], '')
        with self.assertRaises(ValueError):
            self.store.apply_manifest(self.employee, [dict(id=record['id'], digest='0' * 64)])
        self.store.mark_sync(self.employee, '员工', '电气', '完整清单尚未同步')
        self.assertEqual(self.store.employees()[0]['last_synced'], '')
        self.store.apply_manifest(self.employee, [dict(id=record['id'], digest=record['digest'])])
        self.store.mark_sync(self.employee, '员工', '电气')
        self.assertTrue(self.store.employees()[0]['last_synced'])

    def test_atomic_sync_validates_complete_manifest_and_commits_all_components(self):
        first, second = self.record(), self.record('nightly:2026-11-10')
        manifest = [dict(id=row['id'], digest=row['digest']) for row in (first, second)]
        result = self.store.sync(self.employee, [first, second], manifest, '员工', '电气')
        self.assertEqual(result['created'], 2)
        self.assertEqual(len(self.store.records()), 2)
        synced = self.store.employees()[0]['last_synced']
        self.assertTrue(synced)
        third = self.record('nightly:2026-11-09')
        incomplete = [dict(id=third['id'], digest=third['digest']), dict(id=first['id'], digest='0' * 64)]
        with self.assertRaisesRegex(ValueError, '完整下载'):
            self.store.sync(self.employee, [third], incomplete, '员工', '电气')
        self.assertEqual(len(self.store.records()), 2)
        self.assertEqual(self.store.employees()[0]['last_synced'], synced)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 2)
        with self.assertRaisesRegex(ValueError, '不在'):
            self.store.sync(self.employee, [third], manifest, '员工', '电气')

    def test_failure_at_sync_timestamp_rolls_back_content_visibility_and_history(self):
        first, second = self.record(), self.record('nightly:2026-11-10')
        manifest = [dict(id=row['id'], digest=row['digest']) for row in (first, second)]
        self.store.sync(self.employee, [first, second], manifest, '员工', '电气')
        synced = self.store.employees()[0]['last_synced']
        changed = dict(first, summary='本次更新不得半份可见')
        changed['digest'] = record_digest(changed)
        self.store.db.execute('''CREATE TRIGGER fail_timestamp BEFORE UPDATE OF last_synced ON employees
          BEGIN SELECT RAISE(ABORT,'timestamp failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.sync(self.employee, [changed],
                            [dict(id=changed['id'], digest=changed['digest'])], '员工', '电气')
        self.assertEqual({row['id']: row for row in self.store.records()}, {row['id']: row for row in (first, second)})
        self.assertEqual(self.store.employees()[0]['last_synced'], synced)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 2)

    def test_explicit_authentication_suspension_hides_cache_and_preserves_history(self):
        record = self.record()
        manifest = [dict(id=record['id'], digest=record['digest'])]
        self.store.sync(self.employee, [record], manifest, '员工', '电气')
        synced = self.store.employees()[0]['last_synced']
        # A routine offline error leaves the authorized replica available.
        self.store.mark_sync(self.employee, '员工', '电气', '离线')
        self.assertEqual(self.store.records(), [record])
        self.store.suspend(self.employee, '员工已撤销授权')
        self.assertEqual(self.store.records(), [])
        employee = self.store.employees()[0]
        self.assertEqual(employee['last_synced'], synced)
        self.assertEqual(employee['error'], '员工已撤销授权')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 1)
        # A new valid authorization can restore visibility from the same payload.
        self.store.sync(self.employee, [], manifest, '员工', '电气')
        self.assertEqual(self.store.records(), [record])

    def test_snapshot_capacity_failure_writes_no_partial_batch(self):
        record = self.record()
        with patch('evaluation.journals.MAX_SNAPSHOT_BYTES', 100):
            with self.assertRaisesRegex(ValueError, '32 MiB'):
                self.store.sync(self.employee, [record], [dict(id=record['id'], digest=record['digest'])], '员工', '电气')
        self.assertEqual(self.store.records(), [])
        self.assertEqual(self.store.employees(), [])


if __name__ == '__main__':
    unittest.main()
