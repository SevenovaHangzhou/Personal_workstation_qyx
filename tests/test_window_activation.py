import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QDialog, QMainWindow
from PyQt5.QtTest import QTest
from test_ui import APP
from window_activation import WindowActivation, reveal_window

ROOT = Path(__file__).resolve().parents[1]


class WindowActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.activation = WindowActivation(self.directory)
        self.events = []
        self.activation.requested.connect(self.events.append)

    def tearDown(self):
        self.activation.close()
        self.temp.cleanup()

    def child(self, code, *args):
        return subprocess.Popen([sys.executable, '-c', code, *map(str,args)], cwd=ROOT,
                                env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)

    def finish(self, child):
        deadline = time.monotonic() + 15
        try:
            while child.poll() is None and time.monotonic() < deadline:
                QTest.qWait(10)
            if child.poll() is None:
                child.kill()
                self.fail('Secondary process did not finish')
            out,err = child.communicate(timeout=2)
            self.assertEqual(child.returncode,0,out+err)
            return out
        finally:
            if child.poll() is None: child.kill();child.wait()

    def test_same_directory_alias_uses_same_channel_but_other_data_stays_separate(self):
        alias = self.directory/'alias'
        alias.symlink_to(self.directory,target_is_directory=True)
        self.assertEqual(self.activation.name,WindowActivation(alias).name)
        self.assertNotEqual(self.activation.name,WindowActivation(self.directory/'other').name)
        self.assertFalse(self.activation.request(timeout_ms=50))

    def test_cross_process_wake_acknowledged_after_callback(self):
        self.activation.listen()
        child=self.child('''
from PyQt5.QtCore import QCoreApplication
from window_activation import WindowActivation
import sys
app=QCoreApplication([])
assert WindowActivation(sys.argv[1]).request(timeout_ms=3000)
''',self.directory)
        self.finish(child)
        self.assertEqual(self.events,[''])

    def test_second_main_launch_wakes_owner_without_creating_tasks(self):
        self.activation.listen()
        child=subprocess.Popen([sys.executable,str(ROOT/'main.py'),'--data-dir',str(self.directory)],
             cwd=self.directory,env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),
             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.finish(child)
        self.assertEqual(len(self.events),1)
        self.assertFalse((self.directory/'tasks.json').exists())
        self.assertFalse((self.directory/'momo.lock').exists())

    def test_default_launch_does_not_migrate_a_running_legacy_space(self):
        import fcntl
        data=self.directory/'data'; legacy=data/'MomoLocal/Momo'; legacy.mkdir(parents=True)
        self.activation.close(); self.activation=WindowActivation(legacy); self.activation.requested.connect(self.events.append)
        self.activation.listen()
        with (legacy/'momo.lock').open('a+') as owner:
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            child=subprocess.Popen([sys.executable,str(ROOT/'main.py')],cwd=self.directory,
                env=dict(os.environ,QT_QPA_PLATFORM='offscreen',XDG_DATA_HOME=str(data)),
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            self.finish(child)
        self.assertEqual(len(self.events),1); self.assertFalse((data/'Sevenova/Orbit').exists())
        self.assertFalse((legacy/'tasks.json').exists())

    def test_retry_wakes_primary_that_is_still_starting(self):
        QTimer.singleShot(250,self.activation.listen)
        child=self.child('''
from PyQt5.QtCore import QCoreApplication
from window_activation import WindowActivation
import sys
app=QCoreApplication([])
assert WindowActivation(sys.argv[1]).request(timeout_ms=3000,retry=True)
''',self.directory)
        self.finish(child)
        self.assertEqual(len(self.events),1)

    def test_stale_endpoint_is_recovered_and_removed_on_close(self):
        # Create an orphan socket as a crash would; no data owner is running.
        child=self.child('''
from PyQt5.QtCore import QCoreApplication
from PyQt5.QtNetwork import QLocalServer
import os,sys
app=QCoreApplication([]);server=QLocalServer()
assert server.listen(sys.argv[1])
os._exit(0)
''',self.activation.name)
        self.finish(child)
        self.activation.listen()
        self.assertTrue(self.activation.server.isListening())
        self.activation.close()
        self.assertFalse(self.activation.request(timeout_ms=50))

    def test_invalid_message_cannot_wake_window(self):
        self.activation.listen()
        child=self.child('''
from PyQt5.QtCore import QCoreApplication
from PyQt5.QtNetwork import QLocalSocket
import sys
app=QCoreApplication([]);s=QLocalSocket();s.connectToServer(sys.argv[1])
assert s.waitForConnected(2000)
s.write(b'{"command":"delete"}\\n');s.flush()
s.waitForReadyRead(2000)
assert bytes(s.readAll()) != b'activated\\n'
''',self.activation.name)
        self.finish(child)
        self.assertEqual(self.events,[])

    def test_restore_hidden_and_minimized_window_preserves_maximized_state(self):
        window=QMainWindow()
        try:
            window.showMaximized();APP.processEvents()
            window.hide()
            reveal_window(window)
            self.assertTrue(window.isVisible())
            self.assertTrue(window.isMaximized())
            window.showMinimized();APP.processEvents()
            reveal_window(window)
            self.assertFalse(window.isMinimized())
            self.assertTrue(window.isMaximized())
        finally: window.close()

    def test_restore_focuses_existing_modal_dialog(self):
        window=QMainWindow();dialog=QDialog(window)
        try:
            window.show();dialog.setModal(True);dialog.show();APP.processEvents()
            window.hide();reveal_window(window);APP.processEvents()
            self.assertTrue(window.isVisible())
            self.assertTrue(dialog.isVisible())
            self.assertIs(APP.activeModalWidget(),dialog)
        finally: dialog.close();window.close()
