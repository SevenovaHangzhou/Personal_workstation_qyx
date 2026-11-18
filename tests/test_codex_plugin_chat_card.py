import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from tests import test_codex_plugin as fixtures
from orbit_plugin.chat_card import read_plan, select_plan


class ChatWorkflowTests(unittest.TestCase):
    setUp = fixtures.CodexPluginTests.setUp
    invoke = fixtures.CodexPluginTests.invoke

    def chat(self, ident='chat-1', title=None):
        return dict(id=ident, title=title or self.issue['title'], cwd=str(self.work), hostId='local', kind='codex')

    def sync(self, threads):
        return self.invoke('sync_issue_chats', threads=threads, projects=[dict(projectId='project-1', projectKind='local', hostId='local', path=str(self.work), label='机械臂控制')])

    def test_unique_match_binds_and_repeat_preserves(self):
        plan = self.sync([self.chat()])
        self.assertEqual(plan['bound'][0]['thread_id'], 'chat-1')
        self.assertFalse(plan['missing'])
        repeat = self.sync([self.chat('chat-2')])
        self.assertEqual(repeat['bound'][0]['thread_id'], 'chat-1')
        self.writer.assert_not_called()

    def test_duplicates_and_invalid_paths_are_not_created(self):
        plan = self.sync([self.chat(), self.chat('chat-2')])
        self.assertEqual(len(plan['conflicts']), 1)
        self.assertFalse(plan['missing'])
        chat = self.chat()
        chat['cwd'] = '/nonexistent/orbit-test-dir'
        self.assertEqual(len(self.sync([chat])['conflicts']), 1)
        self.assertFalse(self.invoke('list_tasks')['tasks'])

    def test_missing_requires_card_selection(self):
        plan = self.sync([])
        self.assertEqual(plan['state'], 'waiting')
        with self.assertRaises(ValueError):
            select_plan(self.orbit.directory, plan['plan_id'], [dict(identifier='ENG-123', project_id='fake')])
        selected = select_plan(self.orbit.directory, plan['plan_id'], [dict(identifier='ENG-123', project_id='project-1')])
        self.assertEqual(selected['state'], 'selected')
        self.assertFalse(self.invoke('list_tasks')['tasks'])
        with self.assertRaises(ValueError):
            select_plan(self.orbit.directory, plan['plan_id'], [])

    def test_card_requires_project_and_saves_selection(self):
        from PyQt5.QtWidgets import QApplication
        from orbit_plugin.chat_card_ui import ChatCard
        app = QApplication.instance() or QApplication([])
        plan = self.sync([])
        card = ChatCard(self.orbit.directory, plan['plan_id'])
        card.confirm()
        self.assertEqual(read_plan(self.orbit.directory, plan['plan_id'])['state'], 'waiting')
        card.rows[0][2].setCurrentIndex(1)
        card.confirm()
        self.assertEqual(read_plan(self.orbit.directory, plan['plan_id'])['state'], 'selected')
        card.close()
        app.processEvents()
