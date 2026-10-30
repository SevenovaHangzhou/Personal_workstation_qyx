"""Native UI checks for issue scope, attention review and tray ownership."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton, QTabWidget
from PyQt5.QtTest import QTest
from main import Momo, TaskDialog, STYLE
from model import make_task, parse_binding
from runtime import MomoRuntime
from station_ui import AttentionDialog
from test_station import ControlledExecutor

APP = QApplication.instance() or QApplication([])
APP.setStyleSheet(STYLE)


class StationUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = MomoRuntime(Path(self.temp.name) / "tasks.json", auto_tick=False).start()
        self.window = Momo(self.runtime)
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.exiting = True
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.runtime.close()
        self.temp.cleanup()

    def test_work_draft_allowed_and_other_categories_hide_controls(self):
        dialog = TaskDialog(self.window)
        dialog.show()
        dialog.title_input.setText("工作事项")
        dialog.submit()
        self.assertEqual(dialog.result(), dialog.Accepted)
        dialog.show()
        self.assertTrue(dialog.issue_url.isVisible())
        dialog.issue_url.setText("https://linear.app/test/issue/APP-1/task")
        dialog.submit()
        self.assertEqual(dialog.result(), dialog.Accepted)
        dialog = TaskDialog(self.window)
        dialog.category.setCurrentIndex(1)
        dialog.show()
        self.assertFalse(dialog.issue_url.isVisible())
        self.window.navigate("work")
        self.assertTrue(self.window.my_issues_button.isVisible())
        self.window.navigate("learn")
        self.assertFalse(self.window.my_issues_button.isVisible())
        dialog.reject()

    def test_classification_requires_explicit_detach_confirmation(self):
        task = make_task("有 issue 的任务")
        task["bindings"] = [parse_binding("linear", "https://linear.app/test/issue/APP-1/task")]
        dialog = TaskDialog(self.window, task)
        dialog.category.setCurrentIndex(1)
        with patch("main.QMessageBox.question", return_value=QMessageBox.No):
            dialog.submit()
        self.assertNotEqual(dialog.result(), dialog.Accepted)
        with patch("main.QMessageBox.question", return_value=QMessageBox.Yes):
            dialog.submit()
        self.assertTrue(dialog.values()["detach_linear"])

    def test_only_attention_ui_remains_and_opening_never_calls_model(self):
        with patch.object(self.runtime.station.executor, "status", return_value={"available":True,"message":"ChatGPT 已登录"}), patch.object(self.runtime.station.executor, "run") as execute:
            self.window.open_attention()
            dialog = self.window.station_dialog
            self.assertIsInstance(dialog, AttentionDialog)
            self.assertTrue(self.window.station_dialog.isVisible())
            captions = {b.text() for b in self.window.findChildren(QPushButton)}
            captions.update(a.text() for a in self.window.tray.contextMenu().actions())
            self.assertFalse(captions & {"项目空间", "本机执行", "交给本机执行", "关联本机项目", "补充 / 重试", "新建项目"})
            self.assertEqual(dialog.findChildren(QTabWidget), [])
            self.assertEqual(self.runtime.station.db.list_tasks(), [])
            execute.assert_not_called()
            self.assertFalse(any(b.isEnabled() for b in dialog.review_buttons))

    def test_attention_can_review_existing_delivery_without_starting_another_execution(self):
        station = self.runtime.station
        executor = ControlledExecutor()
        executor.release.set()
        station.executor = executor
        station.call("configure", concurrency=1)
        project = station.call("create_project", name="保留的后台项目")
        record = station.call("submit", request_id=str(uuid.uuid4()), momo_id=self.window.selected,
                              project_id=project["project_id"], cwd=self.temp.name, goal="已有交付",
                              acceptance="用户验收", sandbox="read-only")
        deadline = time.monotonic() + 3
        while station.db.get_task(record["task_id"]).status != "review" and time.monotonic() < deadline:
            QTest.qWait(10)
        self.window.open_attention()
        dialog = self.window.station_dialog
        self.assertEqual(dialog.items.count(), 1)
        self.assertIn("已有交付", dialog.detail.toPlainText())
        self.assertTrue(all(b.isEnabled() for b in dialog.review_buttons))
        with patch("station_ui.QInputDialog.getMultiLineText", return_value=("已检查", True)):
            dialog.review(True)
        self.assertEqual(station.db.get_task(record["task_id"]).status, "accepted")
        self.assertEqual(dialog.items.count(), 0)
        self.assertEqual(len(executor.started), 1)

    def test_close_to_tray_keeps_runtime_and_focus_alive(self):
        self.runtime.focus.start(self.window.selected)
        with patch.object(self.window.tray, "isVisible", return_value=True):
            self.window.close()
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.runtime.started)
        self.assertTrue(self.runtime.focus.snapshot()["running"])
        self.assertTrue(self.window.tick_timer.isActive())
        self.window.restore_window()
        self.assertTrue(self.window.isVisible())


if __name__ == "__main__": unittest.main()
