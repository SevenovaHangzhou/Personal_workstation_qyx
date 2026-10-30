"""Merged tasks stay discoverable without competing with their parent cards."""
import copy
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from task_outline import TaskOutline
from model import make_task


class OutlineProjectionTests(unittest.TestCase):
    def setUp(self):
        self.root = make_task('整件事', 'demo')
        self.first = make_task('第一步', 'demo'); self.first['parent_id'] = self.root['id']
        self.second = make_task('第二步', 'demo'); self.second['parent_id'] = self.root['id']
        self.nested = make_task('内部检查', 'demo'); self.nested['parent_id'] = self.first['id']
        self.follow = make_task('后续任务', 'demo'); self.follow['depends_on'] = [self.root['id']]
        # Repository order can place a child before its parent after a merge.
        self.tasks = [self.nested, self.first, self.follow, self.root, self.second]
        self.outline = TaskOutline(self.tasks)

    def test_default_hides_children_but_keeps_followup_independent(self):
        before = copy.deepcopy(self.tasks)
        rows = self.outline.rows(self.tasks)
        self.assertEqual([row.task['id'] for row in rows], [self.follow['id'], self.root['id']])
        self.assertEqual(self.tasks, before)

    def test_expansion_traverses_parent_before_children_with_stable_sibling_order(self):
        rows = self.outline.rows(self.tasks, [self.root['id'], self.first['id']])
        self.assertEqual([(row.task['title'], row.depth) for row in rows],
                         [('后续任务', 0), ('整件事', 0), ('第一步', 1), ('内部检查', 2), ('第二步', 1)])

    def test_search_exposes_deep_match_with_context_not_unrelated_siblings(self):
        rows = self.outline.rows([self.nested], reveal_matches=True)
        self.assertEqual([(row.task['title'], row.context) for row in rows],
                         [('整件事', True), ('第一步', True), ('内部检查', False)])
        self.assertEqual([row.depth for row in rows], [0, 1, 2])

    def test_date_or_status_excluded_parent_opens_matching_child(self):
        rows = self.outline.rows([self.second])
        self.assertEqual([row.task['id'] for row in rows], [self.root['id'], self.second['id']])
        self.assertTrue(rows[0].context)

    def test_manual_collapse_can_override_search_auto_expansion(self):
        rows = self.outline.rows([self.nested], collapsed=[self.first['id']], reveal_matches=True)
        self.assertEqual([row.task['title'] for row in rows], ['整件事', '第一步'])
        self.assertFalse(rows[-1].expanded)


# Import main before creating QApplication so qt_runtime configures plugin paths.
from test_ui import APP
from PyQt5.QtCore import Qt, QPoint, QTimer
from PyQt5.QtWidgets import QPushButton
from PyQt5.QtTest import QTest
from main import Momo
from runtime import MomoRuntime
from task_order_ui import TaskDropArea
from test_task_order import DragEnter, Drop


class OutlineUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'tasks.json'
        self.runtime = MomoRuntime(self.path, auto_tick=False).start()
        self.tasks = self.runtime.tasks
        self.tasks.clear_samples()
        self.parent = self.tasks.add(make_task('机械臂控制优化', 'demo'))
        self.a, self.b = self.tasks.add_children(self.parent['id'], ['验证通信链路', '绘制时序图'])
        self.nested = self.tasks.add_children(self.a['id'], ['检查同步时钟'])[0]
        self.tasks.set_status(self.b['id'], 'doing')
        self.follow = self.tasks.add(make_task('整理交付说明', 'demo'))
        self.tasks.merge(self.follow['id'], self.parent['id'], 'after')
        self.window = Momo(self.runtime)
        self.window.navigate('all'); self.window.show(); APP.processEvents()

    def tearDown(self):
        self.window.exiting = True; self.window.close(); self.window.deleteLater()
        APP.processEvents(); self.runtime.close(); self.temp.cleanup()

    def cards(self):
        return self.window.list_host.cards()

    def ids(self):
        return [card.task_id for card in self.cards()]

    def toggle(self, task_id):
        card = next(card for card in self.cards() if card.task_id == task_id)
        card.findChild(QPushButton, 'stepToggle').click(); APP.processEvents()

    def test_default_summary_expansion_nested_cues_and_collapse_do_not_write_tasks(self):
        before = self.path.read_bytes()
        self.assertEqual(set(self.ids()), {self.parent['id'], self.follow['id']})
        parent = next(card for card in self.cards() if card.task_id == self.parent['id'])
        self.assertIn('展开 2 个子步骤', parent.findChild(QPushButton, 'stepToggle').text())
        self.toggle(self.parent['id'])
        child = next(card for card in self.cards() if card.task_id == self.a['id'])
        self.assertTrue(child.property('substep'))
        from task_outline_ui import RelationLabel
        self.assertIn('机械臂控制优化', child.findChild(RelationLabel, 'substepBadge').full_text)
        self.assertGreater(self.window.list_host.card_rect(child).left(), 0)
        self.toggle(self.a['id']); self.assertIn(self.nested['id'], self.ids())
        self.toggle(self.parent['id']); self.assertNotIn(self.a['id'], self.ids())
        self.assertEqual(self.path.read_bytes(), before)

    def test_search_and_status_reveal_matches_and_context_then_allow_manual_collapse(self):
        self.window.search.setText('同步时钟'); APP.processEvents()
        self.assertEqual(self.ids(), [self.parent['id'], self.a['id'], self.nested['id']])
        self.assertTrue(self.cards()[0].property('context'))
        self.toggle(self.a['id']); self.assertNotIn(self.nested['id'], self.ids())
        self.window.search.clear(); self.window.set_filter('doing'); APP.processEvents()
        self.assertEqual(self.ids(), [self.parent['id'], self.b['id']])
        self.assertTrue(self.cards()[0].property('context'))

    def test_selected_deep_task_opens_path_and_keeps_unsaved_notes(self):
        self.window.select_task(self.parent['id'])
        self.window.note.setPlainText('先保留这段笔记')
        self.window.select_task(self.nested['id']); APP.processEvents()
        self.assertIn(self.nested['id'], self.ids())
        self.assertEqual(self.tasks.get(self.parent['id'])['note'], '先保留这段笔记')
        self.assertEqual(self.window.selected, self.nested['id'])

    def test_opening_child_outside_current_filter_changes_to_all_and_locates_it(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.tasks.update(self.nested['id'], day=tomorrow)
        self.window.navigate('today'); self.window.search.setText('机械臂控制优化')
        self.window.select_task(self.nested['id']); APP.processEvents()
        self.assertEqual(self.window.view, 'all')
        self.assertEqual(self.window.search.text(), '')
        self.assertIn(self.nested['id'], self.ids())

    def test_completed_and_linked_children_keep_parent_path(self):
        self.tasks.set_status(self.a['id'], 'done')
        self.window.navigate('done'); APP.processEvents()
        self.assertEqual(self.ids(), [self.parent['id'], self.a['id']])
        self.tasks.bind(self.b['id'], 'codex', 'codex://threads/bound-step')
        self.window.navigate('linked'); APP.processEvents()
        self.assertEqual(self.ids(), [self.parent['id'], self.b['id']])

    def test_board_shares_expansion_and_uses_child_status_columns(self):
        self.toggle(self.parent['id']); self.window.change_mode('board'); APP.processEvents()
        columns = {area.status: area for area in self.window.list_host.findChildren(TaskDropArea)}
        self.assertIn(self.a['id'], [card.task_id for card in columns['todo'].cards()])
        self.assertIn(self.b['id'], [card.task_id for card in columns['doing'].cards()])
        child = columns['doing'].cards()[0]
        self.assertTrue(child.property('substep'))
        self.window.change_mode('list'); APP.processEvents()
        self.assertIn(self.a['id'], self.ids())

    def test_new_merge_collapses_parent_and_selects_it_even_if_source_was_selected(self):
        source = self.tasks.add(make_task('可并入的方向', 'demo'))
        self.window.select_task(source['id']); self.window.expanded_tasks.add(self.parent['id'])
        def confirm():
            APP.activeModalWidget().findChild(QPushButton, 'merge_child').click()
        QTimer.singleShot(10, confirm)
        self.assertTrue(self.window.merge_task(source['id'], self.parent['id']))
        APP.processEvents()
        self.assertNotIn(source['id'], self.ids())
        self.assertNotIn(self.a['id'], self.ids())
        self.assertEqual(self.window.selected, self.parent['id'])
        self.assertEqual(self.tasks.get(source['id'])['parent_id'], self.parent['id'])

    def test_nested_card_drop_geometry_and_sibling_reorder_keep_hierarchy(self):
        self.toggle(self.parent['id']); area = self.window.list_host
        cards = {card.task_id: card for card in self.cards()}
        source, target = cards[self.b['id']], cards[self.a['id']]
        self.assertIs(area.merge_target(area.card_rect(target).center(), source), target)
        from PyQt5.QtCore import QMimeData
        from task_order_ui import TASK_MIME
        mime = QMimeData(); mime.setData(TASK_MIME, source.task_id.encode())
        position = QPoint(5, area.card_rect(target).top())
        event = DragEnter(source, position, mime); APP.sendEvent(area, event)
        self.assertTrue(event.isAccepted())
        drop = Drop(source, position, mime); APP.sendEvent(area, drop); APP.processEvents()
        self.assertTrue(drop.isAccepted())
        self.assertEqual([step['id'] for step in self.tasks.get(self.parent['id'])['steps']],
                         [self.b['id'], self.a['id']])
        for tid in (self.a['id'], self.b['id']):
            self.assertEqual(self.tasks.get(tid)['parent_id'], self.parent['id'])
        before = self.path.read_bytes()
        self.assertFalse(self.window.reorder_task(self.a['id'], self.follow['id'], False, self.ids()))
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
