import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from daily_engine import DailyEngine
from daily_history import HistoryIndex, cutoff
from model import JsonTaskRepository, make_task, validate_task
from progress_evidence import new_record, period_records, validate_records
from runtime import MomoRuntime


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.runtime = MomoRuntime(self.root/'tasks.json', auto_tick=False).start()
        self.tasks = self.runtime.tasks; self.tasks.clear_samples()
        self.task = make_task('进度测试', 'learn'); self.task['created_at'] = '2026-10-25T00:00:00+00:00'
        self.tasks.add(self.task)

    def tearDown(self):
        self.runtime.close(); self.temp.cleanup()

    def test_append_retract_persist_and_snapshot_isolation(self):
        first = self.tasks.record_progress(self.task['id'], 'verification', '12 项通过，未测断网', '/tmp/result.txt')
        original = copy.deepcopy(first)
        first['text'] = '污染返回值'
        self.assertEqual(self.tasks.get(self.task['id'])['evidence'], [original])
        self.tasks.retract_progress(self.task['id'], original['id'])
        row = self.tasks.get(self.task['id'])['evidence'][0]
        self.assertEqual(row['text'], original['text']); self.assertIn('retracted_at', row)
        version = self.tasks.revision()
        self.tasks.retract_progress(self.task['id'], original['id'])
        self.assertEqual(version, self.tasks.revision())
        self.assertEqual(JsonTaskRepository(self.tasks.path).get(self.task['id'])['evidence'], [row])
        self.assertEqual(self.tasks.get(self.task['id'])['status'], 'todo')

    def test_invalid_inputs_cannot_mutate_disk_or_overwrite_history(self):
        self.tasks.record_progress(self.task['id'], 'plan', '先做一个最小实验')
        before = self.tasks.path.read_bytes()
        for kind, text, source in [('fake', '记录', ''), ('plan', ' ', ''), ('plan', 'x'*6001, ''), ('plan', '记录', 'x'*1001), ([], '记录', ''), ('plan', None, '')]:
            with self.assertRaises(ValueError):
                self.tasks.record_progress(self.task['id'], kind, text, source)
        with self.assertRaises(ValueError):
            self.tasks.update(self.task['id'], evidence=[])
        with self.assertRaises(ValueError):
            self.tasks.retract_progress(self.task['id'], 'not-found')
        self.assertEqual(self.tasks.path.read_bytes(), before)

    def test_failed_write_restores_memory_and_revision(self):
        before = self.tasks.get(self.task['id']); version = self.tasks.revision()
        with patch.object(JsonTaskRepository, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.tasks.record_progress(self.task['id'], 'execution', '实验已运行')
        self.assertEqual(self.tasks.get(self.task['id']), before)
        self.assertEqual(self.tasks.revision(), version)

    def test_concurrent_append_does_not_lose_records(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda i:self.tasks.record_progress(self.task['id'], 'execution', f'结果 {i}'), range(12)))
        actual = self.tasks.get(self.task['id'])['evidence']
        self.assertEqual({r['id'] for r in rows}, {r['id'] for r in actual})
        self.assertEqual(len(actual), 12)

    def test_structure_undo_keeps_later_evidence(self):
        parent = make_task('父任务', 'learn'); self.tasks.add(parent)
        self.tasks.merge(self.task['id'], parent['id'])
        row = self.tasks.record_progress(self.task['id'], 'acceptance', '人工确认示例可复现')
        self.tasks.undo_structure(self.tasks.undo_state()['id'])
        task = self.tasks.get(self.task['id'])
        self.assertIsNone(task['parent_id']); self.assertEqual(task['evidence'], [row])

    def test_validation_supports_legacy_tasks_and_rejects_bad_timestamps_duplicates(self):
        validate_task(self.task)
        row = new_record('plan', '计划')
        for records in [[row, row], [{**row, 'created_at':'2026-10-27T10:00:00'}], [{**row, 'retracted_at':'2000-01-01T00:00:00+00:00'}], [None]]:
            with self.assertRaises(ValueError): validate_records(records)

    def test_cutoff_and_later_withdrawals_are_reconstructed_without_mutation(self):
        earlier = new_record('plan', '原计划'); earlier.update(created_at='2026-10-26T21:00:00+08:00', retracted_at='2026-10-27T21:00:00+08:00')
        current = new_record('execution', '执行'); current.update(created_at='2026-10-27T21:59:59+08:00', retracted_at='2026-10-27T22:00:00+08:00')
        later = new_record('verification', '验证'); later['created_at'] = '2026-10-27T22:00:00+08:00'
        tasks = [{**self.task, 'evidence':[earlier, current, later]}]; before = copy.deepcopy(tasks)
        rows = period_records(tasks, cutoff('2026-10-26'), cutoff('2026-10-27'))
        self.assertEqual([r['id'] for r in rows], [earlier['id'], current['id']])
        self.assertIn('retracted_at', rows[0]); self.assertNotIn('retracted_at', rows[1])
        next_rows = period_records(tasks, cutoff('2026-10-27'), cutoff('2026-10-28'))
        self.assertEqual([r['id'] for r in next_rows], [current['id'], later['id']])
        self.assertEqual(tasks, before)

    def test_daily_local_evidence_is_frozen_redacted_and_excluded_from_issue_comment(self):
        self.tasks.update(self.task['id'], category='work')
        self.tasks.bind(self.task['id'], 'codex', 'codex://threads/work-chat')
        row = new_record('verification', '本地证据 ONLY_LOCAL lin_api_fake_secret_test_12345', '/tmp/report')
        row['created_at'] = '2026-10-27T21:00:00+08:00'
        with patch('progress_evidence.new_record', return_value=row):
            self.tasks.record_progress(self.task['id'], row['kind'], row['text'], row['source'])
        history = Mock(); history.scan.return_value = []; history.between.return_value = {'work-chat':[{'text':'聊天'}]}
        writer = Mock(); writer.ensure_comment.return_value = {'id':'comment'}
        summarizer = Mock(); summarizer.summarize.return_value = '仅聊天摘要'
        store = self.runtime.daily_service.store
        engine = DailyEngine(store, self.tasks, lambda:writer, self.runtime.station, history, summarizer)
        engine.synchronize = Mock(return_value={'id':'issue'})
        result = engine.run('2026-10-27', {}, threading.Event())
        self.assertEqual(result['state'], 'done')
        report = store.report('2026-10-27'); markdown = report['content']['markdown']
        self.assertIn('ONLY_LOCAL', markdown); self.assertIn('[密钥已隐藏]', markdown); self.assertNotIn('fake_secret', markdown)
        body = writer.ensure_comment.call_args.args[0]['payload']['body']
        self.assertIn('仅聊天摘要', body); self.assertNotIn('ONLY_LOCAL', body)
        frozen = copy.deepcopy(report['content']['evidence'])
        self.tasks.retract_progress(self.task['id'], row['id'])
        self.tasks.record_progress(self.task['id'], 'execution', '后续修改')
        engine.run('2026-10-27', {}, threading.Event())
        self.assertEqual(store.report('2026-10-27')['content']['evidence'], frozen)
        self.assertEqual(writer.ensure_comment.call_count, 1)

    def test_evidence_only_report_needs_no_model_or_linear(self):
        row = new_record('execution', '读完入门章节'); row['created_at'] = '2026-10-27T15:00:00+08:00'
        with patch('progress_evidence.new_record', return_value=row):
            self.tasks.record_progress(self.task['id'], row['kind'], row['text'])
        writer = Mock(); summarizer = Mock(); store = self.runtime.daily_service.store
        (self.root/'codex/sessions').mkdir(parents=True)
        engine = DailyEngine(store, self.tasks, writer, self.runtime.station, HistoryIndex(store, self.root/'codex'), summarizer)
        self.assertEqual(engine.run('2026-10-27', {}, threading.Event())['state'], 'done')
        self.assertIn('读完入门章节', store.report('2026-10-27')['content']['markdown'])
        writer.assert_not_called(); summarizer.summarize.assert_not_called()
