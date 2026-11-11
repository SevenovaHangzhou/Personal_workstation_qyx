"""Work layout keeps tools reachable while native title dragging stays portable."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5.QtCore import Qt,QPoint,QEvent
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication,QPushButton,QLabel
from PyQt5.QtTest import QTest
from main import Momo,TitleBar,DetailSection
from runtime import MomoRuntime
from model import make_task
from display_settings import DisplayController,DisplayPreferences

APP=QApplication.instance() or QApplication([])

class WorkSimplicityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.runtime=MomoRuntime(Path(self.temp.name)/'tasks.json',profile='work',auto_tick=False).start()
        self.runtime.tasks.clear_samples()
        self.task=self.runtime.tasks.add(make_task('检查控制接口','work'))
        self.display=DisplayController(APP);self.display.apply(DisplayPreferences())
        self.window=Momo(self.runtime,self.display);self.window.navigate('all');self.window.resize(1440,900);self.window.show();QTest.qWait(30)

    def tearDown(self):
        self.window.exiting=True;self.window.close();self.window.deleteLater();APP.processEvents()
        self.runtime.close();self.display.apply(DisplayPreferences());self.temp.cleanup()

    def test_titlebar_uses_native_move_and_controls_remain_clickable(self):
        bar=self.window.findChild(TitleBar)
        with patch.object(bar,'start_system_move',return_value=True) as native:
            QTest.mousePress(bar,Qt.LeftButton,pos=QPoint(200,20));QTest.mouseRelease(bar,Qt.LeftButton,pos=QPoint(200,20))
            native.assert_called_once();self.assertIsNone(bar.drag)
        for title in bar.findChildren(QLabel): self.assertTrue(title.testAttribute(Qt.WA_TransparentForMouseEvents))
        maximize=next(b for b in bar.findChildren(QPushButton) if b.accessibleName()=='最大化 / 还原')
        with patch.object(bar,'start_system_move') as native:
            maximize.click();APP.processEvents();self.assertTrue(self.window.isMaximized());native.assert_not_called()
            maximize.click();APP.processEvents();self.assertFalse(self.window.isMaximized())

    def test_native_move_uses_window_handle_and_fallback_moves_window(self):
        bar=self.window.findChild(TitleBar)
        with patch.object(self.window,'windowHandle') as handle:
            handle.return_value.startSystemMove.return_value=True
            self.assertTrue(bar.start_system_move());handle.return_value.startSystemMove.assert_called_once()
        with patch.object(bar,'start_system_move',return_value=False):
            press=QPoint(100,20);global_press=bar.mapToGlobal(press)
            bar.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress,press,global_press,Qt.LeftButton,Qt.LeftButton,Qt.NoModifier))
            original=self.window.pos();delta=QPoint(50,30)
            bar.mouseMoveEvent(QMouseEvent(QEvent.MouseMove,press+delta,global_press+delta,Qt.NoButton,Qt.LeftButton,Qt.NoModifier))
            self.assertEqual(self.window.pos(),original+delta)
            QTest.mouseRelease(bar,Qt.LeftButton);self.assertIsNone(bar.drag)

    def test_optional_tools_are_folded_and_remembered_per_task(self):
        section=next(s for s in self.window.findChildren(DetailSection) if s.title=='更多任务设置')
        self.assertFalse(section.content.isVisible());section.toggle.click();APP.processEvents()
        self.assertTrue(section.content.isVisible())
        self.assertTrue(any(b.text()=='关联仓库' and b.isVisible() for b in section.findChildren(QPushButton)))
        self.window.refresh_detail();APP.processEvents()
        section=next(s for s in self.window.findChildren(DetailSection) if s.title=='更多任务设置' and s.isVisible())
        self.assertTrue(section.content.isVisible())
        other=self.runtime.tasks.add(make_task('另一件任务','demo'));self.window.select_task(other['id']);APP.processEvents()
        section=next(s for s in self.window.findChildren(DetailSection) if s.key==(other['id'],'settings'))
        self.assertFalse(section.content.isVisible())

    def test_chat_actions_move_into_menu_after_binding_and_view_menu_works(self):
        self.runtime.tasks.bind(self.task['id'],'codex','codex://threads/test-real-binding')
        self.window.refresh();QTest.qWait(10)
        buttons=[b for b in self.window.findChildren(QPushButton) if b.isVisible()]
        self.assertTrue(any(b.text()=='打开任务 Codex' for b in buttons))
        self.assertFalse(any(b.text() in ('新建聊天','绑定已有聊天') for b in buttons))
        menu=next(b.menu() for b in buttons if b.text()=='聊天设置')
        self.assertEqual([a.text() for a in menu.actions()],['新建聊天','绑定已有聊天'])
        with patch.object(self.window,'bind_task') as bind:
            menu.actions()[1].trigger();bind.assert_called_once_with('codex')
        self.window.sync_task_menu();self.window.view_actions['board'].trigger()
        self.assertEqual(self.window.list_mode,'board')
        self.assertFalse(self.window.undo_action.isEnabled())

    def test_independent_drop_target_only_appears_while_dragging_child(self):
        child=self.runtime.tasks.add_children(self.task['id'],['单独验证'])[0]
        self.window.expanded_tasks.add(self.task['id']);self.window.refresh();QTest.qWait(10)
        from task_order_ui import DraggableTaskCard
        source=next(c for c in self.window.list_host.findChildren(DraggableTaskCard) if c.task_id==child['id'])
        self.assertFalse(self.window.independent_drop.isVisible())
        with patch('task_order_ui.QDrag') as drag:
            drag.return_value.exec_.side_effect=lambda unused:self.assertTrue(self.window.independent_drop.isVisible())
            source._press_position=QPoint(10,10)
            source.mouseMoveEvent(QMouseEvent(QEvent.MouseMove,QPoint(80,30),Qt.NoButton,Qt.LeftButton,Qt.NoModifier))
        self.assertFalse(self.window.independent_drop.isVisible());self.assertFalse(self.window.task_drag_active)
        self.assertEqual(self.runtime.tasks.get(child['id'])['parent_id'],self.task['id'])

    def test_small_window_large_type_retains_space_and_draft(self):
        self.window.note.setPlainText('布局改变时保留笔记');self.window.note_debounce.stop()
        self.display.apply(DisplayPreferences(18));self.window.resize(960,640);QTest.qWait(30)
        self.assertFalse(self.window.subtitle.isVisible());self.assertFalse(self.window.breadcrumb.isVisible())
        self.assertFalse(self.window.independent_drop.isVisible());self.assertGreater(self.window.scroll.height(),300)
        self.assertEqual(self.window.scroll.horizontalScrollBar().maximum(),0)
        self.window.navigate('work');QTest.qWait(30)
        self.assertLessEqual(self.window.my_issues_button.mapTo(self.window,self.window.my_issues_button.rect().bottomRight()).x(),self.window.width())
        self.assertLessEqual(self.window.new_task_button.mapTo(self.window,self.window.new_task_button.rect().bottomRight()).x(),self.window.width())
        self.assertLessEqual(abs(self.window.my_issues_button.mapTo(self.window,self.window.my_issues_button.rect().center()).y()-self.window.new_task_button.mapTo(self.window,self.window.new_task_button.rect().center()).y()),1)
        self.window.open_details();APP.processEvents();self.assertTrue(self.window.note.isVisible())
        self.assertEqual(self.window.note.toPlainText(),'布局改变时保留笔记')
        self.assertTrue(self.window.details_toggle.isVisible())

    def test_missing_issues_action_stays_next_to_new_task_and_sample_entry_is_removed(self):
        for view in ('today','all','work','demo','life','done'):
            self.window.navigate(view);APP.processEvents()
            self.assertTrue(self.window.my_issues_button.isVisible())
            self.assertFalse(self.window.sidebar.isAncestorOf(self.window.my_issues_button))
            self.assertEqual(self.window.my_issues_button.parentWidget(),self.window.new_task_button.parentWidget())
        self.assertFalse(any(b.text()=='示例空间' for b in self.window.findChildren(QPushButton)))
        with patch('main.LinearDialog') as dialog, patch.object(self.runtime.linear,'connection',return_value={'configured':True}), patch('main.QTimer.singleShot') as schedule:
            self.window.my_issues_button.click()
            dialog.assert_called_once_with(self.window);dialog.return_value.exec_.assert_called_once()
            schedule.assert_called_once_with(0,dialog.return_value.pull)

if __name__=='__main__':unittest.main()
