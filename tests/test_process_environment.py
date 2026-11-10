import os
import sys
import unittest
from unittest.mock import patch
from process_environment import system_environment
from station_executor import CodexExecutor


class EnvironmentTests(unittest.TestCase):
    def test_source_environment_is_preserved_and_return_is_a_copy(self):
        original = {'LD_LIBRARY_PATH':'/user/lib', 'CODEX_HOME':'/user/codex', 'PATH':'/bin'}
        with patch.dict(os.environ, original, clear=True), patch.object(sys, 'frozen', False, create=True):
            env = system_environment(); self.assertEqual(env, original)
            env['CODEX_HOME'] = 'changed'; self.assertEqual(os.environ['CODEX_HOME'], '/user/codex')

    def test_frozen_child_restores_system_paths_but_keeps_user_auth_and_custom_plugins(self):
        original = {'LD_LIBRARY_PATH':'/app/_internal:/user/lib', 'LD_LIBRARY_PATH_ORIG':'/user/lib',
                    'QT_PLUGIN_PATH':'/app/_internal/PyQt5/plugins:/custom/plugins',
                    'QT_QPA_PLATFORM_PLUGIN_PATH':'/app/_internal/PyQt5/plugins',
                    'CODEX_HOME':'/user/current-login', 'OPENAI_API_KEY':'existing-provider',
                    'LINEAR_API_KEY':'do-not-forward'}
        with patch.dict(os.environ, original, clear=True), patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', '/app/_internal', create=True):
            env = CodexExecutor().environment()
            self.assertEqual(env['LD_LIBRARY_PATH'], '/user/lib'); self.assertNotIn('LD_LIBRARY_PATH_ORIG', env)
            self.assertEqual(env['QT_PLUGIN_PATH'], '/custom/plugins'); self.assertNotIn('QT_QPA_PLATFORM_PLUGIN_PATH', env)
            self.assertEqual(env['CODEX_HOME'], original['CODEX_HOME']); self.assertEqual(env['OPENAI_API_KEY'], 'existing-provider')
            self.assertNotIn('LINEAR_API_KEY', env); self.assertEqual(dict(os.environ), original)

    def test_frozen_process_without_original_library_path_removes_bundle_override(self):
        with patch.dict(os.environ, {'LD_LIBRARY_PATH':'/app/_internal'}, clear=True), patch.object(sys, 'frozen', True, create=True):
            self.assertNotIn('LD_LIBRARY_PATH', system_environment())


class CodexDiscoveryTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.desktop_path = str(self.home/'desktop-bin')
        self.env = patch.dict(os.environ, {'HOME':str(self.home), 'PATH':self.desktop_path,
                              'CODEX_HOME':str(self.home/'existing-auth')}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def executable(self, folder, name='codex'):
        directory = self.home/folder
        directory.mkdir(parents=True,exist_ok=True)
        path = directory/name
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
        return path

    def test_desktop_path_finds_local_cli_and_child_inherits_its_directory(self):
        from process_environment import find_codex
        binary = self.executable('.local/bin')
        self.assertEqual(find_codex(),str(binary))
        env=CodexExecutor().environment()
        self.assertIn(str(binary.parent),env['PATH'].split(os.pathsep))
        self.assertEqual(env['CODEX_HOME'],str(self.home/'existing-auth'))
        self.assertEqual(os.environ['PATH'],self.desktop_path)

    def test_existing_path_precedence_is_preserved(self):
        from process_environment import find_codex
        binary=self.executable('preferred/bin')
        self.executable('.local/bin')
        with patch.dict(os.environ,{'PATH':str(binary.parent)+':/usr/bin:/bin'}):
            self.assertEqual(find_codex(),str(binary))

    def test_nonexecutable_files_and_missing_install_return_none(self):
        from process_environment import find_codex
        binary=self.executable('.local/bin');binary.chmod(0o644)
        self.assertIsNone(find_codex())

    def test_symlinked_npm_cli_can_find_node_in_same_user_directory(self):
        from process_environment import find_codex
        target=self.executable('npm-package/bin','codex.js')
        directory=self.home/'.npm-global/bin';directory.mkdir(parents=True)
        (directory/'codex').symlink_to(target)
        node=self.executable('.npm-global/bin','node')
        self.assertEqual(find_codex(),str(directory/'codex'))
        import shutil
        self.assertEqual(shutil.which('node',path=CodexExecutor().environment()['PATH']),str(node))

    def test_frozen_bundle_cleanup_and_discovery_work_together(self):
        from process_environment import find_codex
        binary=self.executable('.local/bin')
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'_MEIPASS','/bundle/_internal',create=True),patch.dict(os.environ,{'LD_LIBRARY_PATH':'/bundle/_internal','LINEAR_API_KEY':'fixture'}):
            self.assertEqual(find_codex(),str(binary))
            env=CodexExecutor().environment()
            self.assertNotIn('LD_LIBRARY_PATH',env)
            self.assertNotIn('LINEAR_API_KEY',env)
            self.assertEqual(env['CODEX_HOME'],str(self.home/'existing-auth'))
