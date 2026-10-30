"""Service and native UI checks for structure undo and pending issue previews."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_ui import APP
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox
from model import make_task
from main import Momo
from daily_ui import DailyDialog
from runtime import MomoRuntime


class LoopUiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.runtime=MomoRuntime(Path(self.temp.name)/'tasks.json',auto_tick=False).start()
        self.tasks=self.runtime.tasks; self.tasks.clear_samples()
        self.first=self.tasks.add(make_task('独立方向','work'))
        self.second=self.tasks.add(make_task('汇总方向','work'))
        self.window=Momo(self.runtime); self.window.show(); APP.processEvents()

    def tearDown(self):
        self.window.exiting=True; self.window.close(); self.window.deleteLater()
        APP.processEvents(); self.runtime.close(); self.temp.cleanup()

    def test_undo_button_flushes_current_notes_and_restores_structure_once(self):
        self.tasks.merge(self.first['id'],self.second['id'],'child')
        self.window.select_task(self.first['id']); self.window.refresh()
        self.assertTrue(self.window.undo_structure_button.isEnabled())
        self.window.note.setPlainText('合入后新写的内容')
        with patch.object(QMessageBox,'question',return_value=QMessageBox.No):
            self.window.undo_task_structure()
        self.assertEqual(self.tasks.get(self.first['id'])['parent_id'],self.second['id'])
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            self.window.undo_task_structure()
        task=self.tasks.get(self.first['id'])
        self.assertIsNone(task['parent_id']); self.assertEqual(task['note'],'合入后新写的内容')
        self.assertFalse(self.window.undo_structure_button.isEnabled())
        self.assertFalse(self.tasks.undo_state()['available'])

    def test_pending_preview_tracks_changes_and_locates_without_publishing(self):
        with patch.object(self.runtime.daily_service.engine,'run',side_effect=AssertionError('must not publish')):
            dialog=DailyDialog(self.window)
            try:
                self.assertEqual(dialog.pending.count(),2)
                item=next(dialog.pending.item(i) for i in range(dialog.pending.count())
                          if dialog.pending.item(i).data(Qt.UserRole)['task_id']==self.second['id'])
                dialog.pending.setCurrentItem(item)
                self.assertIn('尚未设置',dialog.pending_detail.text())
                self.tasks.update(self.second['id'],title='新工作名称'); dialog.refresh()
                self.assertIn('新工作名称',dialog.pending.currentItem().text())
                dialog.locate_task(); self.assertEqual(self.window.selected,self.second['id'])
            finally: dialog.deleteLater(); APP.processEvents()


if __name__=='__main__': unittest.main()
