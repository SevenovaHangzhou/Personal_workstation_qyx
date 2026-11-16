import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/codex-orbit'))
from orbit_plugin.core import Orbit, OrbitError
from orbit_plugin.settings import Settings, open_card, desktop_environment
from credentials import CredentialStore


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / 'plugin'
        self.shared = self.root / 'desktop'
        self.environment = patch.dict(os.environ, {'LINEAR_API_KEY': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.keyring = patch.object(CredentialStore, 'keyring', return_value=None)
        self.keyring.start()
        self.addCleanup(self.keyring.stop)
        self.request = Mock(return_value={'viewer': {'id': 'user', 'name': '小七'}})
        self.settings = Settings(self.directory, self.shared, request=self.request)

    def test_settings_summary_never_returns_key_or_initializes_data(self):
        summary = self.settings.summary()
        self.assertFalse(summary['configured'])
        self.assertFalse(self.directory.exists())
        self.request.assert_not_called()

    def test_verified_save_is_private_and_applies_immediately(self):
        work_dir = self.root / '研发 仓库'
        work_dir.mkdir()
        saved = self.settings.save('local-secret', str(work_dir))
        self.assertTrue(saved['saved'])
        self.assertEqual(saved['account'], '小七')
        self.assertEqual(saved['default_work_dir'], str(work_dir))
        self.assertNotIn('local-secret', json.dumps(saved))
        self.assertNotIn('local-secret', (self.directory / 'preferences.json').read_text())
        self.assertEqual(Orbit(self.directory).credential(), 'local-secret')
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE((self.directory / '.credentials/linear.key').stat().st_mode), 0o600)
        self.assertFalse((self.directory / 'codex.sqlite3').exists())

    def test_invalid_key_or_directory_does_not_replace_old_settings(self):
        self.settings.save('old-secret')
        previous = (self.directory / 'preferences.json').read_bytes()
        from linear_support import LinearError
        self.request.side_effect = LinearError('contains secret new-secret')
        with self.assertRaisesRegex(OrbitError, '连接未通过') as caught:
            self.settings.save('new-secret')
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(Orbit(self.directory).credential(), 'old-secret')
        self.assertEqual((self.directory / 'preferences.json').read_bytes(), previous)
        with self.assertRaises(OrbitError):
            self.settings.save('new-secret', str(self.root / 'missing'))

    def test_plugin_override_and_removal_preserve_desktop_credentials(self):
        CredentialStore(self.shared).save('desktop-secret')
        shared_bytes = (self.shared / '.credentials/linear.key').read_bytes()
        self.assertEqual(self.settings.summary()['source'], 'desktop')
        self.settings.save('plugin-secret')
        self.assertEqual(Orbit(self.directory, credential_directory=self.shared).credential(), 'plugin-secret')
        self.assertEqual(self.settings.summary()['source'], 'plugin')
        self.assertEqual(self.settings.clear_plugin_key()['source'], 'desktop')
        self.assertEqual((self.shared / '.credentials/linear.key').read_bytes(), shared_bytes)
        self.assertEqual(Orbit(self.directory, credential_directory=self.shared).credential(), 'desktop-secret')

    def test_environment_key_has_explicit_precedence(self):
        with patch.dict(os.environ, {'LINEAR_API_KEY': 'environment-secret'}):
            self.assertEqual(self.settings.summary()['source'], 'environment')
            self.assertEqual(self.settings.test_connection()['account'], '小七')
            with self.assertRaisesRegex(OrbitError, '环境变量'):
                self.settings.save('new-secret')
        self.assertFalse(self.directory.exists())

    def test_saved_default_directory_is_used_for_acquisition(self):
        self.settings.save('key', str(self.root))
        issue = {'id': 'issue', 'identifier': 'A-1', 'title': '任务', 'viewer_id': 'user', 'assignee_id': 'user', 'state_type': 'started'}
        orbit = Orbit(self.directory, fetch=lambda *a, **k: {'viewer': {'id': 'user'}, 'issues': [issue]}, details=lambda *a, **k: issue)
        task = orbit.start_task(threading.Event(), identifier='A-1')['task']
        self.assertEqual(task['work_dir'], str(self.root))

    def test_launch_passes_only_local_paths_and_no_secret(self):
        with patch('orbit_plugin.settings.importlib.util.find_spec', return_value=object()), \
                patch.dict(os.environ, {'DISPLAY': ':test', 'QT_QPA_PLATFORM': ''}), \
                patch('orbit_plugin.settings.subprocess.Popen') as process:
            result = open_card(self.directory, self.shared)
        self.assertEqual(result['state'], 'opening')
        args = process.call_args.args[0]
        self.assertIn('--settings', args)
        self.assertIn(str(self.directory), args)
        self.assertNotIn('secret', repr(process.call_args))
        with patch('orbit_plugin.settings.importlib.util.find_spec', return_value=None):
            self.assertEqual(open_card(self.directory, self.shared)['reason'], 'missing_pyqt')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux GUI session')
    def test_desktop_recovery_copies_only_gui_variables(self):
        response = Mock(returncode=0, stdout='DISPLAY=:99\nWAYLAND_DISPLAY=wayland-7\nLINEAR_API_KEY=untrusted-secret\nUNRELATED=ignored\n')
        env = {'PATH': os.environ.get('PATH', '')}
        with patch.dict(os.environ, env, clear=True), patch('orbit_plugin.settings.subprocess.run', return_value=response):
            recovered = desktop_environment()
        self.assertEqual(recovered['DISPLAY'], ':99')
        self.assertEqual(recovered['WAYLAND_DISPLAY'], 'wayland-7')
        self.assertNotIn('LINEAR_API_KEY', recovered)
        self.assertNotIn('UNRELATED', recovered)


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from qt_runtime import configure_qt_plugins
    configure_qt_plugins()
    from PyQt5.QtWidgets import QApplication, QLineEdit, QLabel
    from PyQt5.QtTest import QTest
    from orbit_plugin.settings_ui import SettingsCard
    APP = QApplication.instance() or QApplication([])
except ImportError:
    APP = None


@unittest.skipIf(APP is None, 'Native UI dependencies unavailable')
class SettingsCardTests(unittest.TestCase):
    setUp = SettingsTests.setUp

    def tearDown(self):
        APP.processEvents()

    def test_card_masks_input_and_saves_without_blocking_other_ui(self):
        ready, release = threading.Event(), threading.Event()
        def delayed(*args):
            ready.set()
            release.wait(3)
            return {'viewer': {'id': 'user', 'name': '小七'}}
        self.settings.request = delayed
        card = SettingsCard(self.settings)
        card.show()
        self.addCleanup(card.close)
        self.assertEqual(card.key.echoMode(), QLineEdit.Password)
        card.key.setText('private-test-key')
        card.directory.setText(str(self.root))
        card.begin(True)
        try:
            self.assertTrue(ready.wait(1))
            self.assertTrue(card.progress.isVisible())
            self.assertFalse(card.save.isEnabled())
            sibling = QLabel('仍可操作')
            sibling.setText('其他界面已响应')
            APP.processEvents()
            self.assertEqual(sibling.text(), '其他界面已响应')
        finally:
            release.set()
        for _ in range(150):
            QTest.qWait(20)
            if card.job.isFinished() and card.save.isEnabled():
                break
        self.assertTrue(card.job.isFinished())
        self.assertEqual(card.key.text(), '')
        self.assertIn('小七', card.notice.text())
        self.assertNotIn('private-test-key', card.notice.text())
        self.assertEqual(self.settings.summary()['source'], 'plugin')
        self.assertFalse(card.progress.isVisible())

    def test_invalid_input_stays_in_form_and_existing_key_remains(self):
        self.settings.save('old-key')
        from linear_support import LinearError
        self.settings.request = Mock(side_effect=LinearError('bad private-key'))
        card = SettingsCard(self.settings)
        card.show()
        self.addCleanup(card.close)
        card.key.setText('private-key')
        card.begin(True)
        for _ in range(150):
            QTest.qWait(20)
            if card.job.isFinished() and card.save.isEnabled():
                break
        self.assertTrue(card.job.isFinished())
        self.assertIn('连接未通过', card.notice.text())
        self.assertNotIn('private-key', card.notice.text())
        self.assertEqual(Orbit(self.directory).credential(), 'old-key')

    def test_second_process_activates_existing_card_instead_of_opening_another(self):
        script = '''
import sys
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from qt_runtime import configure_qt_plugins
configure_qt_plugins()
from PyQt5.QtCore import QTimer
from orbit_plugin.settings import Settings
import orbit_plugin.settings_ui as ui
class TestCard(ui.SettingsCard):
    def __init__(self,settings):
        super().__init__(settings)
        self.activations=0
        QTimer.singleShot(50,lambda:print('ready',flush=True))
        QTimer.singleShot(5000,self.close)
    def activateWindow(self):
        self.activations+=1
        print('activated',flush=True)
        QTimer.singleShot(20,self.close)
        super().activateWindow()
ui.SettingsCard=TestCard
raise SystemExit(ui.run_card(Settings(__import__('pathlib').Path(sys.argv[3]))))
'''
        process = subprocess.Popen([sys.executable, '-c', script, str(ROOT / 'plugins/codex-orbit'), str(ROOT), str(self.directory)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        try:
            line = process.stdout.readline().strip()
            if line != 'ready':
                process.wait(timeout=5)
                self.fail('Native card did not start: ' + process.stderr.read())
            second = subprocess.run([sys.executable, str(ROOT / 'plugins/codex-orbit/scripts/mcp.py'), '--settings', '--data-dir', str(self.directory)],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(second.returncode, 0, second.stderr)
            remaining, stderr = process.communicate(timeout=6)
            self.assertIn('activated', remaining)
            self.assertEqual(process.returncode, 0, stderr)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()


if __name__ == '__main__':
    unittest.main()
