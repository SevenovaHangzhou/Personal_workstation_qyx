"""Reject desktop bundles that pass offscreen tests but cannot load xcb."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock

spec=importlib.util.spec_from_file_location('release_desktop',Path(__file__).resolve().parents[1]/'scripts/build_release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)

class DesktopDependenciesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.plugins=self.root/'plugins';(self.plugins/'platforms').mkdir(parents=True)
        (self.plugins/'platforms/libqxcb.so').touch();(self.plugins/'xcbglintegrations').mkdir()
        (self.plugins/'xcbglintegrations/libqxcb-glx-integration.so').touch()
        self.log=self.root/'dependencies.log'
    def tearDown(self):self.temp.cleanup()
    def test_missing_library_aborts_and_reports_exact_dependency(self):
        result=Mock(returncode=0,stdout='libxcb-xinerama.so.0 => not found\n',stderr='')
        with patch.object(release.subprocess,'run',return_value=result):
            with self.assertRaisesRegex(RuntimeError,'libxcb-xinerama.so.0'):
                release.check_desktop_dependencies(self.plugins,self.log)
        self.assertIn('not found',self.log.read_text())
    def test_all_xcb_plugins_checked_with_bundle_library_directory(self):
        result=Mock(returncode=0,stdout='libxcb.so.1 => /lib/libxcb.so.1\n',stderr='')
        with patch.object(release.subprocess,'run',return_value=result) as run:
            release.check_desktop_dependencies(self.plugins,self.log,self.root/'_internal')
        self.assertEqual(run.call_count,2)
        self.assertTrue(run.call_args.kwargs['env']['LD_LIBRARY_PATH'].startswith(str(self.root/'_internal')))
        self.assertIn('libqxcb-glx-integration.so',self.log.read_text())
    def test_missing_plugin_or_loader_failure_aborts(self):
        (self.plugins/'platforms/libqxcb.so').unlink()
        with self.assertRaisesRegex(RuntimeError,'插件缺失'):release.check_desktop_dependencies(self.plugins,self.log)
        (self.plugins/'platforms/libqxcb.so').touch()
        with patch.object(release.subprocess,'run',return_value=Mock(returncode=1,stdout='',stderr='loader error')):
            with self.assertRaisesRegex(RuntimeError,'检查失败'):release.check_desktop_dependencies(self.plugins,self.log)

if __name__=='__main__':unittest.main()
