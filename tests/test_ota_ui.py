import tempfile
import threading
import unittest
from pathlib import Path
from PyQt5.QtTest import QTest
from test_ui import APP
from runtime import MomoRuntime
from update_ui import UpdateDialog
from test_ota import fixture


class UpdateUiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.runtime=MomoRuntime(Path(self.temp.name)/'tasks.json',auto_tick=False).start()
        self.service=self.runtime.update_service;self.dialog=UpdateDialog(None,self.runtime.updates);self.dialog.show();QTest.qWait(20)
    def tearDown(self):self.dialog.close();self.dialog.deleteLater();QTest.qWait(10);self.runtime.close();self.temp.cleanup()

    def test_close_dialog_leaves_download_in_background_and_cancel_stops_it(self):
        started=threading.Event();self.service.source=fixture('0.10.0')[1]
        def stager(folder,release,event,*args):started.set();event.wait(3);raise InterruptedError()
        self.service.stager=stager;self.dialog.check.click();self.assertTrue(started.wait(2))
        self.dialog.refresh();self.assertFalse(self.dialog.check.isEnabled());self.assertTrue(self.dialog.cancel.isEnabled())
        self.dialog.close();self.assertTrue(self.service.worker.is_alive())
        self.runtime.updates.cancel();self.service.worker.join(3);self.assertFalse(self.service.worker.is_alive())

    def test_preferences_and_ready_cancel_are_visible_without_installing_now(self):
        self.dialog.enabled.setChecked(False);self.assertFalse(self.runtime.updates.snapshot()['enabled'])
        self.service.source=fixture('0.10.0')[1];self.dialog.check.click();self.service.worker.join(5);self.dialog.refresh()
        self.assertIn('0.10.0',self.dialog.versions.text());self.assertEqual(self.dialog.cancel.text(),'取消待更新')
        self.assertFalse((self.service.folder/'current-version').exists());self.dialog.cancel.click()
        self.assertIsNone(self.runtime.updates.snapshot()['ready'])
