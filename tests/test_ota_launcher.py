import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.base=self.root/'base';self.base.mkdir();self.data=self.root/'profile'
        self.updates=self.data/'updates';self.updates.mkdir(parents=True)
        shutil.copy2(ROOT/'packaging/launch.sh',self.base/'orbit');(self.base/'orbit').chmod(0o755)
        shutil.copy2(ROOT/'packaging/ota-launch.sh',self.base/'ota-launch.sh')
        (self.base/'VERSION').write_text('0.9.1\n');self.script(self.base/'orbit-bin','echo BASE')
    def tearDown(self):self.temp.cleanup()
    def script(self,path,body):path.write_text('#!/bin/bash\n'+body+'\n');path.chmod(0o755)
    def candidate(self,version,body):
        folder=self.updates/'releases'/version;folder.mkdir(parents=True)
        self.script(folder/'orbit',body);self.script(folder/'orbit-bin','exit 0')
        (folder/'ota-install.json').write_text('{}')
        (self.updates/'current-version').write_text(version+'\n')
    def launch(self,source=False,entry=None):
        env=dict(os.environ);env.update(HOME=str(self.root),XDG_STATE_HOME=str(self.root/'state'))
        env.pop('ORBIT_SKIP_OTA',None);env.pop('ORBIT_OTA_HEALTH_FILE',None)
        return subprocess.run([str(entry) if entry else str(ROOT/'run.sh') if source else str(self.base/'orbit'),'--data-dir',str(self.data),'--version'],
                              capture_output=True,text=True,timeout=5,env=env,cwd=self.root)

    def test_new_version_is_launched_from_both_source_and_packaged_entry(self):
        self.candidate('0.10.0','echo UPDATED')
        for source in (True,False):
            result=self.launch(source);self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'UPDATED')

    def test_old_cache_cannot_downgrade_newer_base_install(self):
        self.candidate('0.9.0','echo OLD');result=self.launch()
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('BASE',result.stdout);self.assertNotIn('OLD',result.stdout)

    def test_debian_style_symlink_resolves_base_and_updated_application(self):
        entry=self.root/'bin/orbit';entry.parent.mkdir();entry.symlink_to(self.base/'orbit')
        result=self.launch(entry=entry);self.assertEqual(result.returncode,0,result.stderr);self.assertIn('BASE',result.stdout)
        self.candidate('0.10.0','echo UPDATED')
        result=self.launch(entry=entry);self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'UPDATED')

    def test_source_symlink_can_load_prepared_application(self):
        entry=self.root/'start';entry.symlink_to(ROOT/'run.sh');self.candidate('0.10.0','echo UPDATED')
        result=self.launch(entry=entry);self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'UPDATED')

    def test_startup_failure_restores_base_and_marks_failed_version(self):
        self.candidate('0.9.2','exit 3');result=self.launch()
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('BASE',result.stdout)
        self.assertFalse((self.updates/'current-version').exists());self.assertEqual((self.updates/'failed-version').read_text(),'0.9.2\n')

    def test_startup_failure_can_restore_last_successful_overlay(self):
        self.candidate('0.9.2','echo PREVIOUS');self.candidate('0.9.3','exit 3')
        (self.updates/'previous-version').write_text('0.9.2\n');result=self.launch()
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'PREVIOUS')
        self.assertEqual((self.updates/'current-version').read_text(),'0.9.2\n')

    def test_duplicate_instance_does_not_roll_back(self):
        self.candidate('0.9.2','exit 75');result=self.launch()
        self.assertEqual(result.returncode,75);self.assertEqual((self.updates/'current-version').read_text(),'0.9.2\n')
        self.assertFalse((self.updates/'failed-version').exists())

    def test_late_error_after_healthy_start_does_not_roll_back(self):
        self.candidate('0.9.2','echo 0.9.2 > "$ORBIT_OTA_HEALTH_FILE"\nexit 3');result=self.launch()
        self.assertEqual(result.returncode,3);self.assertEqual((self.updates/'current-version').read_text(),'0.9.2\n')
        self.assertFalse((self.updates/'failed-version').exists());self.assertFalse(list(self.updates.glob('launch-health.*')))

    def test_symlink_pointer_is_not_followed(self):
        self.candidate('0.9.2','echo UPDATED');(self.updates/'current-version').unlink()
        elsewhere=self.root/'untrusted';elsewhere.write_text('0.9.2\n');(self.updates/'current-version').symlink_to(elsewhere)
        result=self.launch();self.assertEqual(result.returncode,0,result.stderr);self.assertIn('BASE',result.stdout)
