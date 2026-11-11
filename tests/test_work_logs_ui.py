import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication,QPushButton
from test_ui import APP
from main import Momo
from model import make_task
from runtime import MomoRuntime
from work_logs_ui import WorkLogsDialog
from daily_ui import DailyDialog
from test_work_logs import write_chat
from daily_history import ZONE
from datetime import datetime
from display_settings import DisplayController,DisplayPreferences

class WorkLogUiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.runtime=MomoRuntime(self.root/'tasks.json',profile='work',auto_tick=False).start();self.runtime.tasks.clear_samples()
        self.first=self.runtime.tasks.add(make_task('工作中的验证','work'));self.other=self.runtime.tasks.add(make_task('另一件事','demo'))
        self.runtime.tasks.bind(self.first['id'],'codex','codex://threads/ui-work')
        manager=self.runtime.daily_service.work_logs;manager.history.root=self.root/'codex'
        write_chat(manager.history.root,'ui-work',[(datetime.now(ZONE).isoformat(),'已运行边界测试')])
        self.display=DisplayController(APP);self.display.apply(DisplayPreferences())
        self.window=Momo(self.runtime,self.display);self.window.resize(1440,900);self.window.show();QTest.qWait(20)
    def tearDown(self):
        if self.window.worklog_dialog:self.window.worklog_dialog.close()
        self.window.exiting=True;self.window.close();self.window.deleteLater();APP.processEvents();self.runtime.close();self.display.apply(DisplayPreferences());self.temp.cleanup()
    def wait_done(self):
        deadline=time.monotonic()+3
        while self.runtime.daily.worklog_snapshot()['busy'] and time.monotonic()<deadline:QTest.qWait(10)
        self.assertFalse(self.runtime.daily.worklog_snapshot()['busy'])
        self.window.worklog_dialog.refresh()

    def test_sidebar_routes_day_week_and_generation_does_not_block_tasks(self):
        entered=threading.Event();release=threading.Event()
        def summarize(messages,cancel):entered.set();release.wait(2);return '完成边界验证，仍需实机检查。'
        try:
            with patch.object(self.runtime.daily_service.work_logs.summarizer,'summarize',side_effect=summarize):
                self.window.end_day_button.click();self.assertTrue(entered.wait(1));dialog=self.window.worklog_dialog
                self.assertIsInstance(dialog,WorkLogsDialog);self.assertFalse(dialog.isModal());self.assertTrue(dialog.isVisible())
                self.assertEqual(self.runtime.daily.worklog_snapshot()['rows'][0]['kind'],'today')
                events=[];QTimer.singleShot(5,lambda:events.append('responsive'));QTest.qWait(20);self.assertEqual(events,['responsive'])
                self.window.select_task(self.other['id']);self.window.note.setPlainText('生成期间继续编辑');self.window.flush_editors()
                self.assertEqual(self.runtime.tasks.get(self.other['id'])['note'],'生成期间继续编辑')
                self.assertTrue(self.window.background_busy())
                dialog.close();self.assertTrue(self.runtime.daily.worklog_snapshot()['busy'])
                release.set();self.wait_done();self.window.open_worklog();self.assertIs(self.window.worklog_dialog,dialog)
                self.assertIn('完成边界验证',dialog.preview.toPlainText());self.assertTrue(dialog.copy.isEnabled())
                dialog.copy.click();self.assertTrue(APP.clipboard().text().startswith('# 今日工作日志'))
                self.window.weeklog_button.click();self.wait_done()
                self.assertEqual(self.runtime.daily.worklog_snapshot()['rows'][0]['kind'],'week')
        finally:release.set()

    def test_history_entry_closes_modal_daily_settings(self):
        def click():
            dialog=APP.activeModalWidget();self.assertIsInstance(dialog,DailyDialog)
            next(b for b in dialog.findChildren(QPushButton) if b.text()=='查看手动工作日志与周报').click()
        QTimer.singleShot(20,click);self.window.open_daily();APP.processEvents()
        self.assertIsNone(APP.activeModalWidget());self.assertTrue(self.window.worklog_dialog.isVisible())
        self.assertFalse(self.runtime.daily.worklog_snapshot()['busy'])

    def test_small_large_font_layout_and_close_leave_background_job_owned_by_service(self):
        self.display.apply(DisplayPreferences(18));self.window.resize(960,640);QTest.qWait(30)
        self.window.open_worklog();dialog=self.window.worklog_dialog;dialog.resize(800,600);QTest.qWait(30)
        self.assertLessEqual(dialog.week.mapTo(dialog,dialog.week.rect().bottomRight()).x(),dialog.width())
        self.assertGreater(dialog.preview.height(),200)
        self.assertLessEqual(dialog.cancel.mapTo(dialog,dialog.cancel.rect().bottomRight()).x(),dialog.width())
        self.assertEqual(dialog.rows.horizontalScrollBar().maximum(),0)
        self.assertEqual(self.window.end_day_button.text(),'下班')
        self.assertEqual(self.window.weeklog_button.text(),'总结周报')

if __name__=='__main__':unittest.main()
