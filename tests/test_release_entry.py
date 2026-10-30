import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from typedefine.TypeDefine import APP_VERSION

ROOT = Path(__file__).resolve().parents[1]


class ReleaseEntryTests(unittest.TestCase):
    def probe(self, *args, root):
        env = dict(os.environ); env.update(HOME=str(root), XDG_DATA_HOME=str(root/'data'),
                                          QT_QPA_PLATFORM='offscreen')
        return subprocess.run([sys.executable, str(ROOT/'main.py'), *args], cwd=root,
                              env=env, capture_output=True, text=True, timeout=30)

    def test_version_without_display_or_user_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); result = self.probe('--version', root=root)
            self.assertEqual(result.returncode, 0, result.stderr); self.assertIn(APP_VERSION, result.stdout)
            self.assertFalse((root/'data').exists())

    def test_selftest_uses_temporary_data_and_no_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); result = self.probe('--self-test', root=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"model_called": false', result.stdout)
            self.assertIn('course-14-chapters', result.stdout)
            self.assertFalse(list(root.rglob('tasks.json')))

    def test_selftest_rejects_explicit_user_data_before_creating_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); target = root/'keep-untouched'
            result = self.probe('--self-test', '--data-dir', str(target), root=root)
            self.assertEqual(result.returncode, 2); self.assertFalse(target.exists())
