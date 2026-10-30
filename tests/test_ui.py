import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton
from PyQt5.QtTest import QTest
from main import Momo, STYLE, TaskDialog, BindDialog, TaskRow, BoardCard
from model import JsonTaskRepository, make_task
from codex_ui import CodexCreateDialog
from runtime import MomoRuntime
from integration_ui import LinearDialog, RepositoryDialog
from test_integrations import issue, page
from test_codex import recommendations

APP=QApplication.instance() or QApplication([])
APP.setStyle("Fusion"); APP.setStyleSheet(STYLE)


class UiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.runtime=MomoRuntime(Path(self.temp.name)/"tasks.json",auto_tick=False).start()
        self.tasks=self.runtime.tasks
        self.window=Momo(self.runtime); self.window.show(); APP.processEvents()

    def tearDown(self):
        self.window.close(); self.window.deleteLater(); APP.processEvents(); self.runtime.close(); self.temp.cleanup()

    def test_create_filter_edit_complete_restart(self):
        def create():
            dialog=APP.activeModalWidget(); self.assertIsInstance(dialog,TaskDialog)
            dialog.title_input.setText("测试一件日常小事"); dialog.category.setCurrentIndex(3); dialog.child_titles.setPlainText("打开笔记"); dialog.submit()
        QTimer.singleShot(20,create); self.window.new_task()
        task=self.tasks.tasks[0]; self.assertEqual(task["category"],"life")
        self.window.navigate("life"); self.assertIn(task,self.window.visible_tasks())
        self.window.search.setText("测试一件"); self.assertEqual(self.window.visible_tasks(),[task])
        self.window.search.clear(); self.window.select_task(task["id"])
        def add_children():
            dialog=APP.activeModalWidget()
            from plan_ui import PlanDialog
            self.assertIsInstance(dialog,PlanDialog)
            dialog.quick_input.setPlainText("写下第一行\n检查结果")
            dialog.add_parallel()
            dialog.accept()
        QTimer.singleShot(20,add_children); self.window.create_children(task["id"])
        self.window.check_step(task["id"],0,True)
        self.window.toggle_done(task["id"])
        self.assertNotIn(task,self.window.visible_tasks())
        self.window.navigate("done"); self.assertIn(task["id"],[t["id"] for t in self.window.visible_tasks()])
        restored=JsonTaskRepository(self.tasks.path).get(task["id"])
        self.assertTrue(restored["steps"][0]["done"]); self.assertEqual(restored["status"],"done")

    def test_notes_and_next_step_stay_with_correct_task(self):
        first,second=self.tasks.tasks[:2]
        self.window.note.setPlainText("第一个任务的续接笔记")

        self.window.select_task(second["id"])
        APP.processEvents()
        self.window.note.setPlainText("第二个任务的续接笔记")

        self.window.select_task(first["id"]); APP.processEvents()
        self.assertEqual(self.tasks.get(first["id"])["note"],"第一个任务的续接笔记")
        self.assertEqual(self.tasks.get(second["id"])["note"],"第二个任务的续接笔记")
        self.assertFalse(hasattr(self.window,"next_edit"))

        self.window.note.setPlainText("自动保存后的笔记"); QTest.qWait(600)
        self.assertEqual(JsonTaskRepository(self.tasks.path).get(first["id"])["note"],"自动保存后的笔记")

    def test_focus_pause_resume_switch_and_mini(self):
        first,second=self.tasks.tasks[:2]
        with patch("services.FocusService.time.monotonic",return_value=1000): self.window.toggle_focus()
        with patch("services.FocusService.time.monotonic",return_value=1005):
            self.runtime.focus.tick(); self.window.tick(); self.window.pause_focus()
        self.assertEqual(self.window.remaining,1495); self.assertEqual(self.tasks.get(first["id"])["focus_seconds"],5)
        with patch("services.FocusService.time.monotonic",return_value=2000): self.window.toggle_focus()
        with patch("services.FocusService.time.monotonic",return_value=2003):
            self.window.select_task(second["id"]); self.window.focus_selected()
        self.assertEqual(self.tasks.get(first["id"])["focus_seconds"],8); self.assertEqual(self.window.focus_task_id,second["id"])
        with patch("services.FocusService.time.monotonic",return_value=2007): self.window.pause_focus()
        self.assertEqual(self.tasks.get(second["id"])["focus_seconds"],4)
        self.assertEqual(self.tasks.get(first["id"])["status"],"doing"); self.assertEqual(self.tasks.get(second["id"])["status"],"todo")
        self.window.show_mini(); self.assertEqual(self.window.mini_time.text(),self.window.timer_label.text())
        self.window.mini.close()

    def test_binding_dialog_and_system_open(self):
        task=self.tasks.tasks[0]; dialog=BindDialog(self.window,task)
        dialog.input.setText("codex://threads/real-test-thread"); dialog.bind()
        task=self.tasks.get(task["id"])
        self.assertEqual(task["bindings"][-1]["url"],"codex://threads/real-test-thread")
        with patch("main.QDesktopServices.openUrl",return_value=True) as open_url:
            self.window.launch_link(task["bindings"][-1]); open_url.assert_called_once()
        dialog.deleteLater()

    def test_issue_button_opens_linear_without_reading_codex_history(self):
        task=make_task("需要关联 issue 的工作任务", "work")
        self.tasks.add(task); self.window.select_task(task["id"]); APP.processEvents()
        issue_button=next(b for b in self.window.detail_content.parentWidget().findChildren(QPushButton)
                          if b.isVisible() and b.text()=="＋  关联一个 issue")
        observed={}; errors=[]
        url="https://linear.app/example/issue/APP-1/task"
        def bind_issue():
            dialog=APP.activeModalWidget()
            try:
                observed.update(kind=dialog.kind.currentData(), title=dialog.windowTitle(),
                                heading=dialog.heading.text(), history_hidden=dialog.history_host.isHidden(),
                                placeholder=dialog.input.placeholderText(), help=dialog.error.text())
                dialog.input.setText(url); dialog.bind_button.click()
            except Exception as exc:
                errors.append(exc)
            finally:
                if dialog: dialog.reject()
        with patch("services.CodexService.recommend",return_value=recommendations()) as recommend:
            QTimer.singleShot(20,bind_issue); issue_button.click()
            recommend.assert_not_called()
        self.assertEqual(errors,[])
        self.assertEqual(observed["kind"],"linear")
        self.assertEqual(observed["title"],"关联 Linear issue")
        self.assertTrue(observed["history_hidden"])
        self.assertIn("issue",observed["heading"])
        self.assertIn("linear.app",observed["placeholder"])
        self.assertIn("Linear issue",observed["help"])
        saved=JsonTaskRepository(self.tasks.path).get(task["id"])
        self.assertEqual([(b["kind"],b["url"]) for b in saved["bindings"]],[("linear",url)])

    def test_binding_type_switch_updates_context_and_removes_correct_link(self):
        task=make_task("分别管理聊天和 issue", "work"); self.tasks.add(task)
        self.tasks.bind(task["id"],"codex","codex://threads/history-for-task")
        self.tasks.bind(task["id"],"linear","https://linear.app/example/issue/APP-1/task")
        dialog=BindDialog(self.window,self.tasks.get(task["id"]),"linear")
        with patch("services.CodexService.recommend",return_value=recommendations()) as recommend:
            dialog.show(); APP.processEvents(); recommend.assert_not_called()
            remove_buttons=dialog.linked_host.findChildren(QPushButton)
            self.assertEqual(len(remove_buttons),1)
            remove_buttons[0].click()
            self.assertEqual([b["kind"] for b in self.tasks.get(task["id"])["bindings"]],["codex"])
            dialog.kind.setCurrentIndex(dialog.kind.findData("codex")); self.wait_history(dialog)
            recommend.assert_called_once()
            self.assertEqual(dialog.windowTitle(),"绑定已有聊天")
            self.assertTrue(dialog.history_host.isVisible())
            self.assertIn("聊天",dialog.heading.text())
            self.assertIn("codex://threads",dialog.input.placeholderText())
            dialog.kind.setCurrentIndex(dialog.kind.findData("linear")); APP.processEvents()
            self.assertEqual(dialog.windowTitle(),"关联 Linear issue")
            self.assertTrue(dialog.history_host.isHidden())
            self.assertIn("Linear issue",dialog.error.text())
        dialog.reject(); dialog.deleteLater(); APP.processEvents()

    def test_overdue_tasks_and_board(self):
        task=self.tasks.tasks[0]; self.tasks.update(task["id"],day=(date.today()-timedelta(days=2)).isoformat())
        self.assertIn(task["id"],[t["id"] for t in self.window.visible_tasks()])
        self.window.change_mode("board"); APP.processEvents()
        self.assertGreater(len(self.window.list_host.findChildren(BoardCard)),0)
        self.window.resize(1180,800); APP.processEvents(); self.assertEqual(self.window.width(),1180)

    def test_failed_save_keeps_editor_and_prevents_close(self):
        first,second=self.tasks.tasks[:2]
        self.window.note.setPlainText("还没有成功保存的笔记")
        with patch.object(JsonTaskRepository,"save",side_effect=OSError("disk full")), patch("main.QMessageBox.warning"):
            self.window.select_task(second["id"])
            self.assertEqual(self.window.selected,first["id"])
            self.assertEqual(self.window.note.toPlainText(),"还没有成功保存的笔记")
            self.window.close()
            self.assertTrue(self.window.isVisible())
        self.window.select_task(second["id"])
        self.assertEqual(JsonTaskRepository(self.tasks.path).get(first["id"])["note"],"还没有成功保存的笔记")

    def test_delete_cancel_confirm_failure_and_empty_details(self):
        leaf=next(t for t in self.tasks.tasks if not t['steps'])
        self.window.select_task(leaf['id']); task_id=leaf['id']
        with patch("main.QMessageBox.question",return_value=QMessageBox.No): self.window.delete_task()
        self.assertIsNotNone(self.tasks.get(task_id))
        with patch("main.QMessageBox.question",return_value=QMessageBox.Yes), patch.object(JsonTaskRepository,"save",side_effect=OSError("disk full")), patch("main.QMessageBox.warning"):
            self.window.delete_task()
        self.assertEqual(self.window.selected,task_id)
        self.assertIsNotNone(self.tasks.get(task_id))
        self.window.note.setPlainText("删除前的笔记")
        with patch("main.QMessageBox.question",return_value=QMessageBox.Yes): self.window.delete_task()
        self.assertIsNone(self.tasks.get(task_id))
        self.assertNotEqual(self.window.selected,task_id)
        self.assertNotEqual(self.window.note.toPlainText(),"删除前的笔记")
        self.runtime.focus.clear_selection(); self.tasks.clear_samples()
        self.window.selected=None; self.window.note_task_id=None; self.window.refresh()
        self.assertFalse(self.window.delete_button.isEnabled())
        self.assertFalse(self.window.detail_focus.isEnabled())

    def wait_pull(self,dialog):
        deadline=time.monotonic()+3
        while dialog.busy and time.monotonic()<deadline: QTest.qWait(10)
        self.assertFalse(dialog.busy,"Linear worker did not finish")
        APP.processEvents()

    def test_my_issues_async_select_import_and_remote_status(self):
        dialog=LinearDialog(self.window); dialog.show()
        dialog.key.setText("ui-test-key")
        with patch("linear_support.graphql",return_value=page([issue(1),issue(2)])):
            dialog.pull(); self.wait_pull(dialog)
        self.assertEqual(len(dialog.checks),2)
        self.assertFalse(dialog.import_button.isEnabled())
        dialog.select_all.setChecked(True)
        self.assertTrue(dialog.import_button.isEnabled())
        dialog.import_selected()
        self.assertEqual(self.window.view,"work")
        self.assertEqual(self.tasks.get(self.window.selected)["linear_issue"]["id"],"issue-1")
        self.assertIn("新增 2",dialog.status.text())
        self.assertEqual(dialog.key.text(),"")
        dialog.reject(); dialog.deleteLater(); APP.processEvents()

    def test_pull_keeps_ui_responsive_and_close_cancels_safely(self):
        dialog=LinearDialog(self.window); dialog.show()
        self.runtime.linear.configure("ui-test-key")
        entered=threading.Event(); release=threading.Event(); events=[]
        def delayed(*args):
            entered.set(); release.wait(2)
            return page([issue()])
        try:
            with patch("linear_support.graphql",side_effect=delayed):
                dialog.pull()
                deadline=time.monotonic()+2
                while not entered.is_set() and time.monotonic()<deadline: QTest.qWait(10)
                self.assertTrue(entered.is_set())
                QTimer.singleShot(5,lambda:events.append("responsive")); QTest.qWait(20)
                self.assertEqual(events,["responsive"])
                self.assertFalse(dialog.pull_button.isEnabled())
                dialog.reject(); self.assertTrue(dialog.busy)
                release.set(); self.wait_pull(dialog)
            self.assertFalse(dialog.isVisible())
            self.assertFalse(any("linear_issue" in t for t in self.tasks.tasks))
        finally:
            release.set()
            if dialog.busy: self.wait_pull(dialog)
            dialog.reject(); dialog.deleteLater(); APP.processEvents()

    def test_work_repository_dialog_and_open(self):
        task_id=self.window.selected
        dialog=RepositoryDialog(self.window,task_id)
        dialog.kind.setCurrentIndex(1)
        dialog.location.setText("git@example.com:team/my-repo.git"); dialog.bind()
        self.assertEqual(len(self.tasks.get(task_id)["repositories"]),1)
        with patch("main.QDesktopServices.openUrl",return_value=True) as open_url:
            self.window.open_repository(0)
        self.assertEqual(open_url.call_args.args[0].toString(),"https://example.com/team/my-repo")
        dialog.remove(0)
        self.assertEqual(self.tasks.get(task_id)["repositories"],[])
        dialog.deleteLater(); APP.processEvents()

    def wait_history(self, dialog):
        deadline=time.monotonic()+3
        APP.processEvents()
        while dialog.busy and time.monotonic()<deadline: QTest.qWait(10)
        self.assertFalse(dialog.busy,"Codex worker did not finish")
        APP.processEvents()

    def test_history_recommendation_auto_load_and_explicit_binding(self):
        dialog=BindDialog(self.window,self.tasks.get(self.window.selected))
        before=len(self.tasks.get(self.window.selected)["bindings"])
        with patch("services.CodexService.recommend",return_value=recommendations()) as recommend:
            dialog.show(); self.wait_history(dialog)
        recommend.assert_called_once()
        self.assertEqual(len(self.tasks.get(self.window.selected)["bindings"]),before)
        dialog.bind_candidate(dialog.result["candidates"][0])
        self.assertEqual(self.tasks.get(self.window.selected)["bindings"][-1]["label"],"机械臂操作台的反馈")
        self.assertEqual(dialog.result["candidates"],[])
        dialog.reject(); dialog.deleteLater(); APP.processEvents()

    def test_history_close_cancels_and_keeps_ui_responsive(self):
        dialog=BindDialog(self.window,self.tasks.get(self.window.selected))
        entered=threading.Event(); events=[]
        def delayed(task,query,archived,cancel,cache):
            entered.set()
            cancel.wait(2)
            from codex_support import CodexError
            raise CodexError("已取消读取历史聊天")
        with patch("services.CodexService.recommend",side_effect=delayed):
            dialog.show(); APP.processEvents()
            deadline=time.monotonic()+2
            while not entered.is_set() and time.monotonic()<deadline: QTest.qWait(10)
            self.assertTrue(entered.is_set())
            QTimer.singleShot(5,lambda:events.append("responsive"))
            deadline=time.monotonic()+0.3
            while not events and time.monotonic()<deadline: QTest.qWait(10)
            self.assertEqual(events,["responsive"])
            dialog.reject(); self.wait_history(dialog)
        self.assertFalse(dialog.isVisible())
        dialog.deleteLater(); APP.processEvents()

    def test_new_chat_creates_without_history_and_open_retry_does_not_duplicate(self):
        dialog=CodexCreateDialog(self.window,self.tasks.get(self.window.selected))
        from unittest.mock import Mock, MagicMock
        rpc=Mock(); rpc.create.return_value="new-ui-chat"
        factory=MagicMock(); factory.return_value.__enter__.return_value=rpc
        with patch.object(self.runtime.codex,"recommend") as history, patch("services.CodexService.CodexRpc",factory), patch("codex_ui.QDesktopServices.openUrl",return_value=False) as opened:
            dialog.show(); APP.processEvents()
            rpc.create.assert_not_called(); history.assert_not_called()
            dialog.directory.setText("not-an-absolute-directory")
            dialog.create_button.click(); rpc.create.assert_not_called()
            dialog.directory.setText(self.temp.name)
            dialog.create_button.click(); self.wait_history(dialog)
            self.assertEqual(opened.call_args.args[0].toString(),"codex://threads/new-ui-chat")
            dialog.create_button.click()
            self.assertEqual(opened.call_count,2)
            rpc.create.assert_called_once(); history.assert_not_called()
        self.assertEqual(self.tasks.get(self.window.selected)["bindings"][-1]["url"],"codex://threads/new-ui-chat")
        self.assertIn(self.tasks.get(self.window.selected)["title"],APP.clipboard().text())
        dialog.reject(); dialog.deleteLater(); APP.processEvents()

    def test_chat_buttons_route_to_independent_dialogs_with_or_without_bindings(self):
        for category in ("work","learn","demo","life"):
            task=self.tasks.add(make_task("选择聊天入口",category))
            if category=="work": self.tasks.bind(task["id"],"codex","codex://threads/already-bound")
            self.window.select_task(task["id"]); APP.processEvents()
            buttons={b.text():b for b in self.window.detail_content.parentWidget().findChildren(QPushButton) if b.isVisible()}
            for text,dialog_type in [("新建聊天",CodexCreateDialog),("绑定已有聊天",BindDialog)]:
                observed=[]
                def close_dialog(expected=dialog_type):
                    dialog=APP.activeModalWidget()
                    observed.append(isinstance(dialog,expected))
                    if isinstance(dialog,BindDialog):
                        observed.append(dialog.kind.currentData()=="codex" and dialog.windowTitle()=="绑定已有聊天")
                    dialog.reject()
                with patch("services.CodexService.recommend",return_value=recommendations()), patch.object(self.runtime.codex,"create") as create:
                    QTimer.singleShot(20,close_dialog)
                    buttons[text].click()
                    self.assertEqual(observed,[True,True] if dialog_type is BindDialog else [True]); create.assert_not_called()

    def test_new_chat_close_waits_for_creation_and_preserves_binding(self):
        dialog=CodexCreateDialog(self.window,self.tasks.get(self.window.selected))
        entered=threading.Event(); release=threading.Event()
        create=self.runtime.codex.create
        def delayed(task_id,directory):
            entered.set(); release.wait(2)
            return create(task_id,directory)
        from unittest.mock import Mock, MagicMock
        rpc=Mock(); rpc.create.return_value="closing-ui-chat"
        factory=MagicMock(); factory.return_value.__enter__.return_value=rpc
        try:
            with patch.object(self.runtime.codex,"create",side_effect=delayed), patch("services.CodexService.CodexRpc",factory), patch("codex_ui.QDesktopServices.openUrl") as opened:
                dialog.show(); dialog.directory.setText(self.temp.name); dialog.create_button.click()
                deadline=time.monotonic()+2
                while not entered.is_set() and time.monotonic()<deadline: QTest.qWait(10)
                self.assertTrue(entered.is_set())
                dialog.reject(); self.assertTrue(dialog.busy)
                release.set(); self.wait_history(dialog)
                self.assertFalse(dialog.isVisible()); opened.assert_not_called()
                self.assertEqual(self.tasks.get(self.window.selected)["bindings"][-1]["url"],"codex://threads/closing-ui-chat")
        finally:
            release.set()
            if dialog.busy: self.wait_history(dialog)
            dialog.reject(); dialog.deleteLater(); APP.processEvents()


if __name__=="__main__": unittest.main()
