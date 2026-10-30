import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtTest import QTest
from test_ui import APP
from main import Momo
from model import make_task
from progress_evidence_ui import ProgressDialog, ProgressPanel
from runtime import MomoRuntime


class ProgressUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = MomoRuntime(Path(self.temp.name)/'tasks.json', auto_tick=False).start()
        self.runtime.tasks.clear_samples(); self.task = make_task('机械臂轨迹边界验证', 'demo')
        self.task['note'] = '边界测试通过，实机验收待进行。'; self.runtime.tasks.add(self.task)
        self.window = Momo(self.runtime); self.window.show(); self.window.select_task(self.task['id'])
        self.dialog = ProgressDialog(self.window, self.window, self.task['id']); self.dialog.show(); QTest.qWait(30)

    def tearDown(self):
        self.dialog.text.clear(); self.dialog.source.clear()
        self.dialog.close(); self.dialog.deleteLater(); self.window.hide(); self.window.tray.hide()
        self.window.deleteLater(); QTest.qWait(20); self.runtime.close(); self.temp.cleanup()

    def test_note_prefill_save_select_and_retract_preserve_original_and_status(self):
        self.assertTrue(self.window.findChild(ProgressPanel))
        self.dialog.use_note(); self.assertEqual(self.dialog.text.toPlainText(), self.task['note'])
        self.dialog.kind.setCurrentIndex(2); self.dialog.source.setText('/tmp/boundary-tests.txt')
        self.dialog.save_record()
        self.assertFalse(self.dialog.text.toPlainText()); self.assertEqual(self.dialog.rows.count(), 1)
        self.assertIn('实机验收待进行', self.dialog.details.toPlainText())
        self.assertEqual(self.runtime.tasks.get(self.task['id'])['status'], 'todo')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            self.dialog.retract_record()
        self.assertTrue(self.dialog.retract_button.isEnabled())
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.dialog.retract_record()
        self.assertFalse(self.dialog.retract_button.isEnabled())
        self.assertIn('已撤回', self.dialog.details.toPlainText())
        self.assertEqual(self.runtime.tasks.get(self.task['id'])['evidence'][0]['text'], self.task['note'])

    def test_failed_save_preserves_draft_and_deleted_task_does_not_accept_records(self):
        self.dialog.text.setPlainText('未保存的草稿'); self.dialog.source.setText('/tmp/source')
        with patch.object(self.runtime.tasks, 'record_progress', side_effect=OSError('disk full')), patch.object(QMessageBox, 'warning'):
            self.dialog.save_record()
        self.assertEqual(self.dialog.text.toPlainText(), '未保存的草稿')
        self.assertEqual(self.dialog.source.text(), '/tmp/source')
        self.runtime.tasks.delete(self.task['id']); self.dialog.refresh()
        self.assertFalse(self.dialog.save_button.isEnabled()); self.assertFalse(self.dialog.note_button.isEnabled())
        self.assertEqual(self.dialog.text.toPlainText(), '未保存的草稿')

    def test_return_with_unsaved_draft_allows_continuing_to_edit(self):
        self.dialog.text.setPlainText('还在整理的验证结果')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            self.dialog.accept()
        self.assertTrue(self.dialog.isVisible()); self.assertEqual(self.dialog.text.toPlainText(), '还在整理的验证结果')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.dialog.accept()
        self.assertFalse(self.dialog.isVisible())

    def test_small_window_keeps_composer_history_and_actions_visible(self):
        self.dialog.resize(640, 600); QTest.qWait(30)
        self.assertLessEqual(self.dialog.minimumSizeHint().width(), 640)
        self.assertLessEqual(self.dialog.minimumSizeHint().height(), 600)
        for widget in (self.dialog.text, self.dialog.source, self.dialog.rows, self.dialog.save_button, self.dialog.retract_button):
            point = widget.mapTo(self.dialog, widget.rect().bottomRight())
            self.assertTrue(self.dialog.rect().contains(point), widget.accessibleName() or widget.text())
