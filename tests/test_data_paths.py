import errno
import fcntl
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from data_paths import default_data_directory, credential_identity, MIGRATION_MARKER
from credentials import CredentialStore


class DataPathTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.home=Path(self.temp.name)
        self.legacy=self.home/'MomoLocal/Momo'; self.target=self.home/'Sevenova/Orbit'

    def tearDown(self): self.temp.cleanup()

    def create_legacy(self):
        self.legacy.mkdir(parents=True)
        (self.legacy/'tasks.json').write_text('{"preserved":"任务和绑定"}')
        course=self.legacy/'learning/generations/job'; course.mkdir(parents=True)
        (course/'events.jsonl').write_text('existing generation events\n')
        credentials=self.legacy/'.credentials'; credentials.mkdir(mode=0o700)
        (credentials/'linear.key').write_text('test-fixture-key'); (credentials/'linear.key').chmod(0o600)

    def test_new_install_uses_branded_path_without_creating_old_data(self):
        self.assertEqual(default_data_directory(self.home),self.target)
        self.assertFalse(self.legacy.exists())

    def test_migration_keeps_old_absolute_paths_contents_lock_and_keyring_identity(self):
        self.create_legacy(); before=credential_identity(self.legacy)
        (self.legacy/'momo.lock').touch(); inode=(self.legacy/'momo.lock').stat().st_ino
        contents=(self.legacy/'tasks.json').read_bytes()
        self.assertEqual(default_data_directory(self.home),self.target)
        self.assertTrue(self.legacy.is_symlink()); self.assertEqual(self.legacy.resolve(),self.target)
        self.assertEqual((self.target/'tasks.json').read_bytes(),contents)
        self.assertEqual((self.legacy/'learning/generations/job/events.jsonl').read_text(),'existing generation events\n')
        self.assertEqual((self.target/'momo.lock').stat().st_ino,inode)
        self.assertEqual(CredentialStore(self.target).service,before)
        with patch.object(CredentialStore,'keyring',return_value=None):
            self.assertEqual(CredentialStore(self.target).load(),('test-fixture-key','file'))
        self.assertEqual(default_data_directory(self.home),self.target)

    def test_running_legacy_instance_is_not_moved(self):
        self.create_legacy()
        with (self.legacy/'momo.lock').open('a+') as owner:
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(default_data_directory(self.home),self.legacy)
            self.assertFalse(self.target.exists()); self.assertFalse((self.legacy/MIGRATION_MARKER).exists())
        self.assertEqual(default_data_directory(self.home),self.target)

    def test_distinct_existing_spaces_are_never_merged(self):
        self.create_legacy(); self.target.mkdir(parents=True); (self.target/'keep').write_text('new')
        with self.assertRaisesRegex(RuntimeError,'两个独立'): default_data_directory(self.home)
        self.assertEqual((self.target/'keep').read_text(),'new'); self.assertFalse(self.legacy.is_symlink())

    def test_cross_device_failure_keeps_original_files(self):
        self.create_legacy()
        with patch('data_paths.os.rename',side_effect=OSError(errno.EXDEV,'different filesystems')):
            with self.assertRaises(OSError): default_data_directory(self.home)
        self.assertTrue((self.legacy/'tasks.json').is_file()); self.assertFalse(self.target.exists())

    def test_alias_failure_rolls_back_move(self):
        self.create_legacy()
        with patch.object(Path,'symlink_to',side_effect=OSError('fixture alias failure')):
            with self.assertRaises(OSError): default_data_directory(self.home)
        self.assertTrue((self.legacy/'tasks.json').is_file()); self.assertFalse(self.target.exists())

    def test_recovery_after_rename_before_alias(self):
        self.target.mkdir(parents=True); (self.target/'tasks.json').write_text('preserved')
        (self.target/MIGRATION_MARKER).write_text(json.dumps({'version':1,'legacy_path':str(self.legacy)}))
        self.assertEqual(default_data_directory(self.home),self.target)
        self.assertEqual((self.legacy/'tasks.json').read_text(),'preserved')

    def test_custom_legacy_alias_requires_explicit_choice(self):
        other=self.home/'custom';other.mkdir();self.legacy.parent.mkdir(parents=True);self.legacy.symlink_to(other)
        with self.assertRaisesRegex(RuntimeError,'自定义'): default_data_directory(self.home)
        self.assertEqual(self.legacy.resolve(),other)
