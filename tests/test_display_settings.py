"""Display preview, persistence and responsive editing without real user data."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PyQt5.QtCore import QSettings, QRect
from PyQt5.QtWidgets import QLabel, QPushButton, QScrollArea
from PyQt5.QtTest import QTest
from test_ui import APP
from display_settings import DisplayController, DisplayPreferences
from preferences_ui import PreferencesDialog
from runtime import MomoRuntime
from main import Momo
from learning_ui import CourseReader
from test_learning_knowledge import ReferenceClient, reference_course
from model import make_task


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.store=QSettings(str(self.root/'display.ini'),QSettings.IniFormat)
        self.display=DisplayController(APP,self.store); self.display.apply(DisplayPreferences())
        self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start()
        self.window=Momo(self.runtime,self.display); self.window.resize(1440,960); self.window.show(); APP.processEvents()

    def tearDown(self):
        self.window.exiting=True; self.window.close(); self.window.deleteLater(); APP.processEvents()
        self.runtime.close(); self.display.apply(DisplayPreferences()); self.temp.cleanup()

    def test_persistence_invalid_values_and_default(self):
        self.store.setValue('display/font_size','broken'); self.store.setValue('display/window_mode','unknown')
        self.assertEqual(DisplayPreferences.read(self.store),DisplayPreferences())
        self.store.setValue('display/font_size',300)
        self.assertEqual(DisplayPreferences.read(self.store).font_size,13)
        self.display.save(DisplayPreferences(18,'compact'))
        loaded=DisplayController(APP,QSettings(str(self.root/'display.ini'),QSettings.IniFormat))
        self.assertEqual(loaded.saved,DisplayPreferences(18,'compact'))

    def test_preview_cancel_restores_geometry_and_task_draft(self):
        self.window.note.setPlainText('不因显示预览而丢失的笔记')
        self.window.note_debounce.stop()
        dialog=PreferencesDialog(self.window); dialog.show()
        dialog.font_size.setValue(18); dialog.window_mode.setCurrentIndex(dialog.window_mode.findData('compact')); APP.processEvents()
        self.assertEqual(self.display.current.font_size,18)
        self.assertFalse(self.store.contains('display/font_size'))
        self.assertEqual(self.window.note.toPlainText(),'不因显示预览而丢失的笔记')
        dialog.reject(); APP.processEvents()
        self.assertEqual(self.display.current,DisplayPreferences())
        self.assertEqual(self.window.size().width(),1440)
        self.assertEqual(self.window.note.toPlainText(),'不因显示预览而丢失的笔记')
        dialog.deleteLater()

    def test_save_reset_and_apply_failure_keeps_dialog_open(self):
        dialog=PreferencesDialog(self.window); dialog.show()
        dialog.font_size.setValue(18)
        with patch.object(self.display,'save',side_effect=OSError('配置目录只读')):
            dialog.accept()
        self.assertTrue(dialog.isVisible()); self.assertIn('只读',dialog.notice.text())
        dialog.reset_display(); self.assertEqual(dialog.values(),DisplayPreferences())
        dialog.font_size.setValue(16); dialog.accept()
        self.assertEqual(DisplayPreferences.read(self.store).font_size,16)
        self.assertFalse(dialog.isVisible()); self.assertFalse(self.display.previewing)
        dialog.deleteLater()

    def test_responsive_details_and_large_font_keep_editor_and_actions(self):
        self.window.note.setPlainText('布局切换保留原编辑器'); self.window.note_debounce.stop(); editor=self.window.note
        self.display.apply(DisplayPreferences(18))
        self.window.resize(960,640); QTest.qWait(30)
        self.assertTrue(self.window.compact_layout)
        self.assertTrue(self.window.details_toggle.isVisible())
        self.assertEqual(self.window.scroll.horizontalScrollBar().maximum(),0)
        self.assertGreaterEqual(self.window.scroll.height(),100)
        self.assertGreaterEqual(self.window.focus_card.height(),self.window.focus_button.height())
        self.assertTrue(self.window.focus_button.isVisible())
        settings=next(b for b in self.window.findChildren(QPushButton) if b.text()=='偏好设置')
        self.assertTrue(settings.isVisibleTo(self.window))
        self.assertLess(settings.mapTo(self.window,settings.rect().bottomRight()).y(),self.window.height())
        self.window.open_details(); APP.processEvents()
        self.assertTrue(self.window.details_dialog.isVisible()); self.assertIs(self.window.note,editor)
        self.window.resize(1800,1000); APP.processEvents()
        self.assertFalse(self.window.compact_layout); self.assertFalse(self.window.details_dialog.isVisible())
        self.assertTrue(self.window.details.isVisible()); self.assertIs(self.window.note,editor)
        self.assertEqual(editor.toPlainText(),'布局切换保留原编辑器')

    def test_new_tasks_use_scaled_fonts_and_board_has_no_overflow(self):
        self.display.apply(DisplayPreferences(18))
        task=self.runtime.tasks.add(make_task('显示字体测试','life')); self.window.navigate('all'); APP.processEvents()
        title=next(w for w in self.window.findChildren(QLabel) if w.text()==task['title'])
        self.assertEqual(title.font().pixelSize(),19)
        self.window.setMinimumSize(800,500); self.window.resize(800,600); self.window.navigate('work'); APP.processEvents()
        self.assertEqual(self.window.scroll.horizontalScrollBar().maximum(),0)
        self.window.change_mode('board'); APP.processEvents()
        self.assertEqual(self.window.scroll.horizontalScrollBar().maximum(),0)

    def test_geometry_is_saved_and_constrained_to_current_screen(self):
        self.display.remember_window(self.window)
        self.assertTrue(self.store.contains('display/geometry'))
        screen=Mock(); screen.availableGeometry.return_value=QRect(0,0,1200,700)
        with patch.object(self.window,'screen',return_value=screen):
            self.display.fit_window(self.window,restore=True)
        self.assertLessEqual(self.window.width(),1200); self.assertLessEqual(self.window.height(),700)
        self.assertGreaterEqual(self.window.x(),0); self.assertGreaterEqual(self.window.y(),0)

    def test_reader_reflows_and_preserves_question_and_reference(self):
        reader=CourseReader(self.window,ReferenceClient(reference_course()),'task','course'); reader.show()
        reader.tutor.question.setPlainText('这个公式如何理解？')
        lesson=reader.active_lesson['id']; original=reader.knowledge.content.toPlainText()
        self.display.apply(DisplayPreferences(18)); reader.resize(800,640); QTest.qWait(30)
        self.assertTrue(reader.reference_toggle.isVisible()); self.assertTrue(reader.chapter_nav.isHidden())
        self.assertEqual(reader.scroll.horizontalScrollBar().maximum(),0)
        reader.open_reference(); APP.processEvents()
        self.assertTrue(reader.reference_dialog.isVisible()); self.assertTrue(reader.tutor.question.isVisible())
        self.assertEqual(reader.tutor.question.toPlainText(),'这个公式如何理解？')
        self.assertEqual(reader.knowledge.content.toPlainText(),original)
        reader.resize(1600,900); APP.processEvents()
        self.assertFalse(reader.reference_dialog.isVisible()); self.assertIs(reader.reference_split.parentWidget(),reader.study_split)
        self.assertEqual(reader.active_lesson['id'],lesson)
        reader.close(); reader.deleteLater(); APP.processEvents()


if __name__=='__main__': unittest.main()
