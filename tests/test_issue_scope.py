"""Work-only issue association, draft creation and explicit reclassification."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from model import make_task, parse_binding, JsonTaskRepository
from runtime import MomoRuntime

URL = 'https://linear.app/example/issue/APP-1/task'


class IssueScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'tasks.json'
        self.runtime = MomoRuntime(self.path, auto_tick=False).start()
        self.tasks = self.runtime.tasks

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_new_work_requires_real_issue_other_categories_reject_issue(self):
        for category in ('learn', 'demo', 'life'):
            task = make_task('独立任务', category)
            self.tasks.add(task)
            with self.assertRaisesRegex(ValueError, '只有工作任务'):
                self.tasks.bind(task['id'], 'linear', URL)
            forged = make_task('注入关联', category)
            forged['bindings'] = [parse_binding('linear', URL)]
            with self.assertRaisesRegex(ValueError, '只有工作任务'):
                self.tasks.add(forged)
        task = make_task('待晚间同步')
        self.tasks.add(task)
        self.assertIsNotNone(self.tasks.get(task['id']))
        self.tasks.bind(task['id'], 'linear', URL)
        self.tasks.remove_binding(task['id'], 0)
        self.assertFalse(self.tasks.get(task['id'])['bindings'])

    def test_atomic_conversion_detaches_issue_only_after_explicit_choice(self):
        task = make_task('工作任务'); task['bindings'] = [parse_binding('linear', URL)]
        task['note'] = '保留我的进展'; self.tasks.add(task)
        with self.assertRaisesRegex(ValueError, '先解除'):
            self.tasks.update(task['id'], category='learn')
        before = self.tasks.get(task['id'])
        with patch.object(JsonTaskRepository, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.tasks.update(task['id'], category='learn', detach_linear=True)
        self.assertEqual(self.tasks.get(task['id']), before)
        updated = self.tasks.update(task['id'], category='learn', detach_linear=True)
        self.assertEqual((updated['category'], updated['bindings'], updated['note']), ('learn', [], '保留我的进展'))
        self.tasks.update(task['id'], category='work')
        updated = self.tasks.update(task['id'], category='work', issue_url=URL)
        self.assertEqual(updated['bindings'][0]['url'], URL)

    def test_legacy_work_without_issue_is_preserved_until_user_supplies_one(self):
        self.runtime.close()
        task = make_task('历史工作任务')
        self.path.write_text(json.dumps({'version': 1, 'sample': False, 'tasks': [task]}))
        self.runtime = MomoRuntime(self.path, auto_tick=False).start(); self.tasks = self.runtime.tasks
        self.tasks.update(task['id'], note='历史记录仍可编辑')
        self.assertEqual(len(self.tasks.tasks), 1)
        self.tasks.update(task['id'], issue_url=URL)
        self.assertEqual(JsonTaskRepository(self.path).get(task['id'])['bindings'][0]['url'], URL)


if __name__ == '__main__':
    unittest.main()
