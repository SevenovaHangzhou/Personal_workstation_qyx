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
