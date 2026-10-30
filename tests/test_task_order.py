"""Ordering preserves hidden tasks, task content, and explicit dependencies."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt5.QtCore import Qt, QPoint, QPointF, QMimeData, QEvent
from PyQt5.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QPushButton
from test_ui import APP
from main import Momo, TaskRow, BoardCard
from model import JsonTaskRepository, make_task
from runtime import MomoRuntime
from clients import call
from msgs.YomkMsgs import TaskOrder
from task_order_ui import TaskDropArea, TASK_MIME
from task_plan import plan_rows


class TaskOrderServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "tasks.json"
        self.runtime = MomoRuntime(self.path, auto_tick=False).start()
        self.tasks = self.runtime.tasks
        self.runtime.focus.clear_selection(); self.tasks.clear_samples()
        for title in reversed(["A", "hidden", "B", "C"]):
            self.tasks.add(make_task(title, "demo"))
        self.before = self.tasks.tasks
        self.ids = [task["id"] for task in self.before]

    def tearDown(self):
        self.runtime.close(); self.temp.cleanup()

    def test_partial_order_persists_without_moving_hidden_or_replacing_content(self):
        a, hidden, b, c = self.ids
        self.tasks.update(b, note="排序期间也要保留我的笔记")
        before = {task["id"]: task for task in self.tasks.tasks}
        revision = self.tasks.revision()["revision"]
        self.assertTrue(self.tasks.reorder([c, a, b]))
        self.assertEqual([task["id"] for task in self.tasks.tasks], [c, hidden, a, b])
        self.assertEqual({task["id"]: task for task in self.tasks.tasks}, before)
        self.assertEqual(JsonTaskRepository(self.path).tasks, self.tasks.tasks)
        self.assertEqual(self.tasks.revision()["revision"], revision + 1)
        self.assertFalse(self.tasks.reorder([c, a, b]))
        self.assertEqual(self.tasks.revision()["revision"], revision + 1)

    def test_invalid_or_failed_save_preserves_memory_disk_and_revision(self):
        revision = self.tasks.revision()["revision"]; data = self.path.read_bytes()
        for order in ([], [self.ids[0], self.ids[0]], ["deleted-task"], [None], "invalid"):
            with self.subTest(order=order), self.assertRaises(ValueError):
                self.tasks.reorder(order)
        with self.assertRaises(ValueError):
            call("/TaskService/reorder", {"task_ids": self.ids})
        with patch.object(JsonTaskRepository, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.tasks.reorder(list(reversed(self.ids)))
        self.assertEqual(self.tasks.tasks, self.before)
        self.assertEqual(self.path.read_bytes(), data)
        self.assertEqual(self.tasks.revision()["revision"], revision)

    def test_reordering_steps_preserves_parent_and_explicit_dependencies(self):
        parent = self.tasks.add(make_task("父任务", "demo"))
        children = self.tasks.add_children(parent["id"], ["起步", "后续", "并行"])
        a, b, c = [child["id"] for child in children]
        baseline = plan_rows(self.tasks.tasks, parent["id"])
        planned = copy.deepcopy(baseline); planned[1]["depends_on"] = [a]
        self.tasks.apply_plan(parent["id"], planned, baseline)
        before = {child["id"]: self.tasks.get(child["id"]) for child in children}
        data = self.path.read_bytes()
        with patch.object(JsonTaskRepository, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.tasks.reorder([c, b, a])
        self.assertEqual(self.path.read_bytes(), data)
        self.assertEqual([step["id"] for step in self.tasks.get(parent["id"])["steps"]], [a, b, c])
        self.assertTrue(call("/TaskService/reorder", TaskOrder([c, b, a])))
        self.assertEqual([step["id"] for step in self.tasks.get(parent["id"])["steps"]], [c, b, a])
        self.assertEqual({child["id"]: self.tasks.get(child["id"]) for child in children}, before)
        self.assertEqual(JsonTaskRepository(self.path).tasks, self.tasks.tasks)


class DragEnter(QDragEnterEvent):
    def __init__(self, source, pos, mime):
        super().__init__(pos, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        self.drag_source = source; self.drag_mime = mime

    def source(self): return self.drag_source


class DragMove(QDragMoveEvent):
    def __init__(self, source, pos, mime):
        super().__init__(pos, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        self.drag_source = source; self.drag_mime = mime

    def source(self): return self.drag_source


class Drop(QDropEvent):
    def __init__(self, source, pos, mime):
        super().__init__(QPointF(pos), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        self.drag_source = source; self.drag_mime = mime

    def source(self): return self.drag_source


class TaskOrderUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "tasks.json"
        self.runtime = MomoRuntime(self.path, auto_tick=False).start()
        self.tasks = self.runtime.tasks
        self.runtime.focus.clear_selection(); self.tasks.clear_samples()
        self.rows = [make_task(title, category) for title, category in
                     [("A", "work"), ("hidden", "learn"), ("B", "work"), ("waiting", "work"), ("C", "work")]]
        self.rows[3]["status"] = "waiting"
        for task in reversed(self.rows): self.tasks.add(copy.deepcopy(task))
        self.window = Momo(self.runtime); self.window.show(); self.window.navigate("all"); APP.processEvents()
        self.ids = [task["id"] for task in self.rows]

    def tearDown(self):
        self.window.close(); self.window.deleteLater(); APP.processEvents()
        self.runtime.close(); self.temp.cleanup()

    def mime(self, source):
        mime = QMimeData(); mime.setData(TASK_MIME, source.task_id.encode("utf-8")); return mime

    def send_drop(self, area, source, pos, mime=None):
        mime = mime or self.mime(source)
        enter = DragEnter(source, pos, mime); APP.sendEvent(area, enter)
        if not enter.isAccepted(): return False
        move = DragMove(source, pos, mime); APP.sendEvent(area, move)
        self.assertTrue(move.isAccepted()); self.assertTrue(area.guide.isVisible())
        drop = Drop(source, pos, mime); APP.sendEvent(area, drop)
        self.assertTrue(area.guide.isHidden()); self.assertFalse(area.scroll_timer.isActive())
        return drop.isAccepted()

    def test_filtered_list_drop_keeps_hidden_positions_and_selection(self):
        self.window.navigate("work"); self.window.set_filter("todo"); APP.processEvents()
        cards = self.window.list_host.cards(); selected = self.window.selected
        self.assertTrue(self.send_drop(self.window.list_host, cards[-1], QPoint(5, cards[0].y())))
        APP.processEvents()
        a, hidden, b, waiting, c = self.ids
        self.assertEqual([task["id"] for task in self.tasks.tasks], [c, hidden, a, waiting, b])
        self.assertEqual([card.task_id for card in self.window.list_host.cards()], [c, a, b])
        self.assertEqual(self.window.selected, selected)
        self.assertEqual(JsonTaskRepository(self.path).tasks, self.tasks.tasks)

    def test_board_reorders_within_column_and_rejects_cross_column(self):
        self.window.change_mode("board"); APP.processEvents()
        columns = {area.status: area for area in self.window.list_host.findChildren(TaskDropArea)}
        todo = columns["todo"]; waiting = columns["waiting"]
        before = self.tasks.tasks; first = todo.cards()[0]
        self.assertFalse(self.send_drop(waiting, first, QPoint(5, 30)))
        self.assertEqual(self.tasks.tasks, before)
        self.assertTrue(self.send_drop(todo, first, QPoint(5, todo.height() - 1)))
        APP.processEvents()
        a, hidden, b, waiting_id, c = self.ids
        self.assertEqual([task["id"] for task in self.tasks.tasks], [hidden, b, c, waiting_id, a])
        self.assertTrue(all(self.tasks.get(task["id"])["status"] == task["status"] for task in before))
        self.window.change_mode("list"); APP.processEvents()
        self.assertEqual([card.task_id for card in self.window.list_host.cards()], [hidden, b, c, waiting_id, a])

    def test_press_drag_release_does_not_destroy_source_or_select_it(self):
        area = self.window.list_host; source = area.cards()[-1]; selected = self.window.selected
        QTest.mousePress(source, Qt.LeftButton, pos=QPoint(8, 8))
        self.assertIs(area.cards()[-1], source)
        with patch("task_order_ui.QDrag") as factory:
            drag = factory.return_value
            def execute(action):
                self.assertEqual(action, Qt.MoveAction)
                self.assertTrue(self.window.task_drag_active)
                self.window.refresh(); self.assertIs(area.cards()[-1], source)
                mime = drag.setMimeData.call_args.args[0]
                self.assertTrue(self.send_drop(area, source, QPoint(5, area.cards()[0].y()), mime))
                self.assertIs(area.cards()[-1], source)
                return Qt.MoveAction
            drag.exec_.side_effect = execute
            event = QMouseEvent(QEvent.MouseMove, QPointF(40, 40), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
            APP.sendEvent(source, event)
            drag.exec_.assert_called_once()
        QTest.mouseRelease(source, Qt.LeftButton, pos=QPoint(40, 40)); APP.processEvents()
        self.assertFalse(self.window.task_drag_active)
        self.assertEqual(self.window.selected, selected)
        self.assertEqual(area.cards()[0].task_id, self.ids[-1])

    def test_click_still_selects_and_status_button_still_completes(self):
        source = self.window.list_host.cards()[-1]
        QTest.mouseClick(source, Qt.LeftButton, pos=QPoint(8, 8)); APP.processEvents()
        self.assertEqual(self.window.selected, source.task_id)
        source = self.window.list_host.cards()[-1]
        status = source.findChild(QPushButton, "statusDot"); status.click(); APP.processEvents()
        self.assertEqual(self.tasks.get(self.ids[-1])["status"], "done")
        self.assertEqual([task["id"] for task in self.tasks.tasks], self.ids)

    def test_failed_drop_preserves_notes_and_order(self):
        area = self.window.list_host; before = self.tasks.tasks
        self.window.note.setPlainText("未保存的笔记也必须保留")
        with patch.object(JsonTaskRepository, "save", side_effect=OSError("disk full")), patch("main.QMessageBox.warning"):
            self.assertFalse(self.send_drop(area, area.cards()[-1], QPoint(5, area.cards()[0].y())))
        self.assertEqual(self.tasks.tasks, before)
        self.assertEqual(self.window.note.toPlainText(), "未保存的笔记也必须保留")

    def test_external_payload_is_rejected(self):
        area = self.window.list_host; source = area.cards()[-1]
        mime = QMimeData(); mime.setText(source.task_id)
        self.assertFalse(self.send_drop(area, source, QPoint(5, 5), mime))
        event = DragEnter(None, QPoint(5, 5), self.mime(source)); APP.sendEvent(area, event)
        self.assertFalse(event.isAccepted())

    def test_dragging_near_edges_scrolls_and_cleanup_stops_scrolling(self):
        for index in range(18): self.tasks.add(make_task(f"长列表 {index}", "demo"))
        self.window.refresh(); QTest.qWait(20)
        area = self.window.list_host; source = area.cards()[0]
        enter = DragEnter(source, QPoint(5, source.y()), self.mime(source)); APP.sendEvent(area, enter)
        self.assertTrue(enter.isAccepted())
        viewport = self.window.scroll.viewport(); bar = self.window.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        with patch("task_order_ui.QCursor.pos", return_value=viewport.mapToGlobal(QPoint(10, viewport.height() - 2))):
            for _ in range(5): area.auto_scroll()
        scrolled = bar.value(); self.assertGreater(scrolled, 0)
        with patch("task_order_ui.QCursor.pos", return_value=viewport.mapToGlobal(QPoint(10, 2))):
            area.auto_scroll()
        self.assertLess(bar.value(), scrolled)
        area.clear_drag(); stopped = bar.value(); area.auto_scroll()
        self.assertEqual(bar.value(), stopped)
        self.assertTrue(area.guide.isHidden()); self.assertFalse(area.scroll_timer.isActive())


if __name__ == "__main__": unittest.main()
