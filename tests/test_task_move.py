"""Task location controls work without exposing hidden groups or losing drafts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_ui import APP
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QPushButton, QMessageBox
from PyQt5.QtTest import QTest
from main import Momo
from model import make_task
from runtime import MomoRuntime
from task_move_ui import TaskMoveDialog


class TaskMoveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'tasks.json'
        self.runtime=MomoRuntime(self.path,auto_tick=False).start(); self.tasks=self.runtime.tasks; self.tasks.clear_samples()
        self.parent=self.tasks.add(make_task('原父任务','demo'))
        self.child=self.tasks.add_children(self.parent['id'],['需要移动的步骤'])[0]
        self.nested=self.tasks.add_children(self.child['id'],['后代任务'])[0]
        self.other=self.tasks.add(make_task('新归属','demo','2030-01-01'))
        self.foreign=self.tasks.add(make_task('其他分类','learn'))
        self.window=Momo(self.runtime); self.window.navigate('demo'); self.window.show(); APP.processEvents()

    def tearDown(self):
        self.window.exiting=True; self.window.close(); self.window.deleteLater(); APP.processEvents()
        self.runtime.close(); self.temp.cleanup()

    def select(self, dialog, task_id):
        for i in range(dialog.targets.count()):
            item=dialog.targets.item(i)
            if item.data(Qt.UserRole)==task_id: dialog.targets.setCurrentItem(item); return
        self.fail('missing target')

    def test_selection_search_excludes_descendants_and_other_categories_includes_other_dates(self):
        dialog=TaskMoveDialog(self.window,self.child['id'])
        try:
            ids={dialog.targets.item(i).data(Qt.UserRole) for i in range(dialog.targets.count())}
            self.assertEqual(ids,{self.parent['id'],self.other['id']})
            self.select(dialog,self.other['id']); self.assertTrue(dialog.child_button.isEnabled())
            dialog.search.setText('没有匹配'); self.assertFalse(dialog.child_button.isEnabled())
            self.assertIsNone(dialog.target_id); self.assertIn('没有匹配',dialog.reason.text())
            dialog.search.clear(); self.assertTrue(dialog.child_button.isEnabled())
        finally: dialog.deleteLater(); APP.processEvents()

    def test_connection_preserves_note_and_ignores_current_list_filter(self):
        self.window.select_task(self.child['id']); self.window.note.setPlainText('移动前尚未保存的笔记')
        self.window.search.setText('需要移动'); APP.processEvents()
        def choose():
            dialog=APP.activeModalWidget(); self.assertIsInstance(dialog,TaskMoveDialog)
            self.select(dialog,self.other['id']); dialog.child_button.click()
        QTimer.singleShot(10,choose)
        self.assertTrue(self.window.move_task(self.child['id']))
        self.assertEqual(self.tasks.get(self.child['id'])['parent_id'],self.other['id'])
        self.assertEqual(self.tasks.get(self.child['id'])['note'],'移动前尚未保存的笔记')
        self.assertEqual(self.tasks.get(self.nested['id'])['parent_id'],self.child['id'])
        self.assertEqual(self.window.selected,self.other['id'])
        self.assertIn(self.other['id'],self.window.outline_entries)

    def test_cancel_and_dependency_rejection_do_not_change_structure(self):
        before=self.path.read_bytes()
        QTimer.singleShot(10,lambda:APP.activeModalWidget().reject())
        self.assertFalse(self.window.move_task(self.child['id'])); self.assertEqual(self.path.read_bytes(),before)
        sibling=self.tasks.add_children(self.parent['id'],['前置步骤'])[0]
        self.tasks.dependencies(self.child['id'],[sibling['id']])
        dialog=TaskMoveDialog(self.window,self.child['id'])
        try:
            self.select(dialog,self.other['id'])
            self.assertFalse(dialog.child_button.isEnabled()); self.assertFalse(dialog.after_button.isEnabled())
            self.assertIn('先调整依赖',dialog.reason.text())
        finally: dialog.deleteLater(); APP.processEvents()

    def test_follow_selection_uses_explicit_dependency_and_current_parent_disables_redundant_merge(self):
        dialog=TaskMoveDialog(self.window,self.child['id'])
        try:
            self.select(dialog,self.parent['id']); self.assertFalse(dialog.child_button.isEnabled())
        finally: dialog.deleteLater(); APP.processEvents()
        def choose():
            dialog=APP.activeModalWidget(); self.select(dialog,self.other['id']); dialog.after_button.click()
        QTimer.singleShot(10,choose)
        self.assertTrue(self.window.move_task(self.child['id']))
        result=self.tasks.get(self.child['id'])
        self.assertIsNone(result['parent_id']); self.assertEqual(result['depends_on'],[self.other['id']])

    def test_detail_location_shows_full_path_and_detach_control_only_for_children(self):
        self.window.select_task(self.nested['id']); APP.processEvents()
        from PyQt5.QtWidgets import QLabel
        host=self.window.detail_content.parentWidget()
        self.assertTrue(any(label.text()=='原父任务 / 需要移动的步骤' for label in host.findChildren(QLabel)))
        buttons=[b for b in host.findChildren(QPushButton) if b.isVisible()]
        next(b for b in buttons if b.text()=='↖ 打开父任务').click(); APP.processEvents()
        self.assertEqual(self.window.selected,self.child['id'])
        self.window.select_task(self.parent['id']); QTest.qWait(10)
        self.assertFalse(any(b.text()=='移为独立任务' and b.isVisible() for b in host.findChildren(QPushButton)))


if __name__=='__main__': unittest.main()
