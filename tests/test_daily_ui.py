import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from runtime import MomoRuntime
from main import Momo,STYLE
from daily_ui import DailyDialog,TargetDialog

APP=QApplication.instance() or QApplication([])
APP.setStyleSheet(STYLE)


class DailyUiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.runtime=MomoRuntime(Path(self.temp.name)/'tasks.json',auto_tick=False).start()
        self.window=Momo(self.runtime);self.window.show();APP.processEvents()
    def tearDown(self):
        self.window.exiting=True;self.window.close();self.window.deleteLater();APP.processEvents();self.runtime.close();self.temp.cleanup()
    def test_background_revision_refresh_preserves_unsaved_note(self):
        task_id=self.window.selected;self.window.note.setPlainText('尚未自动保存')
        self.runtime.tasks.update(task_id,title='后台标题')
        self.window.tick();self.assertEqual(self.window.note.toPlainText(),'尚未自动保存')
        self.window.flush_editors();self.window.tick();self.assertEqual(self.window.note.toPlainText(),'尚未自动保存')
        self.assertEqual(self.window.seen_revision,self.runtime.tasks.revision()['revision'])
    def test_daily_report_projection_updates_without_overwriting_edited_remote_text(self):
        dialog=DailyDialog(self.window)
        store=self.runtime.daily_service.store
        store.save_report('2026-10-27','pending',{'markdown':'测试日报'},['网络等待重试'])
        dialog.refresh();self.assertIn('测试日报',dialog.preview.toPlainText())
        store.save_report('2026-10-27','pending',{'markdown':'本地摘要已更新'},['网络等待重试'])
        dialog.refresh();self.assertIn('本地摘要已更新',dialog.preview.toPlainText())
        dialog.close();dialog.deleteLater();APP.processEvents()
    def test_team_project_selection_is_async_and_close_waits_for_reader(self):
        entered=threading.Event();release=threading.Event()
        def choices(team_id=''):
            entered.set();release.wait(2)
            return {'viewer':{'id':'me','name':'我'},'teams':[{'id':'team','name':'团队'}],'projects':[]}
        with patch.object(self.runtime.daily,'choices',side_effect=choices):
            dialog=TargetDialog(self.window,self.runtime.daily,{})
            dialog.show();self.assertTrue(entered.wait(1));events=[];QTimer.singleShot(10,lambda:events.append('responsive'));QTest.qWait(40)
            self.assertEqual(events,['responsive']);dialog.reject();self.assertTrue(dialog.closing)
            release.set();deadline=time.monotonic()+3
            while dialog.worker and time.monotonic()<deadline:QTest.qWait(10)
            self.assertIsNone(dialog.worker);self.assertEqual(dialog.result(),dialog.Rejected)
            dialog.deleteLater();APP.processEvents()
