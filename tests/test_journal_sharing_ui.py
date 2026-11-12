"""Native employee entry, safe defaults, scope and small-window behavior."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox, QPushButton
from test_ui import APP
from test_integrations import issue
from journal_sharing_ui import JournalSharingDialog
from main import Momo
from model import make_task
from report_sharing_ui import open_report_sharing
from runtime import MomoRuntime


class JournalSharingUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = MomoRuntime(Path(self.temp.name)/'tasks.json', auto_tick=False, profile='work').start()
        self.addCleanup(self.runtime.close, apply_update=False)
        self.runtime.tasks.clear_samples()
        self.work = make_task('接口开发', 'work')
        self.work['linear_issue'] = dict(issue(901), id=str(uuid.uuid4()), identifier='EE-901',
            url='https://linear.app/test/issue/EE-901/test', state_type='started')
        self.runtime.tasks.add(self.work)
        self.runtime.tasks.add(make_task('没有 issue 的任务', 'work'))
        self.runtime.tasks.add(make_task('Demo 不进入统计', 'demo'))
        self.owner = Momo(self.runtime)
        self.dialog = JournalSharingDialog(self.owner, self.runtime.journal_share)
        self.dialog.show()
        APP.processEvents()

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        if getattr(self.owner, 'collaboration_dialog', None):
            self.owner.collaboration_dialog.close()
            self.owner.collaboration_dialog.deleteLater()
        self.owner.exiting = True
        self.owner.close()
        self.owner.deleteLater()
        APP.processEvents()
        self.runtime.close(apply_update=False)
        self.temp.cleanup()

    def test_entry_defaults_and_eligible_tasks(self):
        state = self.runtime.journal_share.snapshot()
        self.assertFalse(state['config']['sharing'])
        self.assertIsNone(state['endpoint'])
        self.assertFalse(self.dialog.grant_button.isEnabled())
        self.assertEqual(self.dialog.tasks.count(), 1)
        self.assertEqual(self.dialog.tasks.item(0).data(Qt.UserRole), self.work['id'])
        open_report_sharing(self.owner)
        buttons = self.owner.collaboration_dialog.report_panel.findChildren(QPushButton)
        self.assertIn('连接评估员', [button.text() for button in buttons])
        self.dialog.resize(560, 430)
        APP.processEvents()
        self.assertEqual(self.dialog.width(), 560)
        self.assertTrue(self.dialog.notice.isVisible())

    def test_unsaved_selection_survives_refresh_and_no_is_no_network(self):
        self.dialog.name.setText('员工甲')
        self.dialog.enabled.setChecked(True)
        self.dialog.tasks.item(0).setCheckState(Qt.Checked)
        self.dialog.refresh()
        self.assertEqual(self.dialog.name.text(), '员工甲')
        self.assertEqual(self.dialog.tasks.item(0).checkState(), Qt.Checked)
        with patch('journal_sharing_ui.QMessageBox.question', return_value=QMessageBox.No):
            self.dialog.configure()
        self.assertFalse(self.runtime.journal_share.snapshot()['config']['sharing'])
        with self.assertRaises(ValueError):
            self.runtime.journal_share.configure('员工甲', '电气', False, ['unknown-task'])


if __name__ == '__main__':
    unittest.main()
