import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/codex-orbit'
INSTALLER = PLUGIN / 'scripts/install.py'
VERSION = json.loads((PLUGIN / 'plugin.json').read_text())['version']


def load_installer():
    spec = importlib.util.spec_from_file_location('orbit_installer', INSTALLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CodexPluginInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dist = self.root / '中文 空格' / 'marketplace'
        self.module = load_installer()

    def install(self, *arguments, script=INSTALLER):
        return subprocess.run([sys.executable, str(script), '--headless', '--no-codex', '--install-dir', str(self.dist), *arguments],
                              capture_output=True, text=True, encoding='utf-8', errors='replace')

    def test_readme_and_tool_count_match_the_plugin(self):
        sys.path.insert(0, str(PLUGIN))
        from orbit_plugin import __version__
        from orbit_plugin.server import TOOLS
        self.assertEqual(VERSION, __version__)
        readme = (PLUGIN / 'README.md').read_text()
        self.assertIn(VERSION, readme)
        self.assertIn(f'{len(TOOLS)} 个 MCP 工具', readme)
        self.module.validate_manifest(ROOT)

    def test_install_is_self_contained_and_upgrade_preserves_settings(self):
        for iteration in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
            plugin = self.dist / 'plugins/codex-orbit'
            settings = plugin / 'settings.json'
            if iteration:
                self.assertEqual(json.loads(settings.read_text())['data_dir'], str(self.root / '个人数据'))
            settings.write_text(json.dumps({'data_dir': str(self.root / '个人数据')}))
            self.assertTrue((plugin / 'shared/linear_support.py').is_file())
            server = json.loads((plugin / 'mcp.json').read_text())['mcpServers']['orbit']
            self.assertEqual(server['command'], sys.executable)
        self.assertFalse((self.root / '个人数据').exists())
        self.assertFalse(list(self.dist.parent.glob('marketplace.previous-*')))
        self.assertFalse(list(self.dist.parent.glob('orbit-prepare-*')))

    def test_zip_install_moves_to_stable_directory(self):
        self.module.prepare_distribution(ROOT, self.root / 'download')
        script = self.root / 'download/plugins/codex-orbit/scripts/install.py'
        result = self.install(script=script)
        self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
        shutil.rmtree(self.root / 'download')
        self.module.verify_server(sys.executable, self.dist, VERSION)
        self.assertTrue((self.dist / 'install.sh').is_file())
        self.assertTrue((self.dist / 'install.ps1').is_file())
        self.assertTrue((self.dist / 'INSTALL.md').is_file())
        result = self.install(script=self.dist / 'plugins/codex-orbit/scripts/install.py')
        self.assertEqual(result.returncode, 0, result.stderr+result.stdout)

    def test_data_directory_option_and_invalid_option_preserve_old_install(self):
        self.assertEqual(self.install('--data-dir', str(self.root / 'data')).returncode, 0)
        settings = self.dist / 'plugins/codex-orbit/settings.json'
        previous = settings.read_bytes()
        result = self.install('--data-dir', 'relative')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(settings.read_bytes(), previous)

    def test_dry_run_never_creates_runtime_or_distribution(self):
        result = self.install('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
        self.assertIn('未修改任何文件', result.stdout)
        self.assertFalse(self.dist.parent.exists())
        with mock.patch.object(self.module, 'codex_json', side_effect=AssertionError('CLI must not run')), \
             mock.patch.object(self.module, 'find_codex', return_value='codex'), \
             mock.patch.object(sys, 'argv', [str(INSTALLER), '--dry-run', '--source', str(ROOT), '--install-dir', str(self.dist)]):
            self.module.main()

    def test_missing_codex_is_checked_before_writes(self):
        result = subprocess.run([sys.executable, str(INSTALLER), '--headless', '--codex', str(self.root/'missing-codex'),
                                 '--install-dir', str(self.dist)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.dist.parent.exists())

    def test_foreign_target_and_source_overlap_are_rejected(self):
        self.dist.mkdir(parents=True)
        important = self.dist/'keep.txt'
        important.write_text('keep')
        result = self.install()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(important.read_text(), 'keep')
        for target in [ROOT, ROOT/'plugins/codex-orbit/nested', ROOT.parent]:
            with self.assertRaises(self.module.InstallError):
                self.module.validate_target(ROOT, target)

    def test_migration_preserves_registered_settings_without_copying_keys(self):
        old = self.root/'legacy/marketplace'
        settings = old/'plugins/codex-orbit/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'data_dir': str(self.root/'original-data'), 'credentials_dir': str(self.root/'credentials')}))
        value = self.module.previous_settings(self.dist, str(old), ROOT)
        self.assertEqual(value['data_dir'], str(self.root/'original-data'))
        settings.write_text('{broken')
        with self.assertRaises(self.module.InstallError):
            self.module.previous_settings(self.dist, str(old), ROOT)

    def row(self, path):
        return {'name':'sevenova','root':str(path), 'marketplaceSource':{'sourceType':'local','source':str(path)}}

    def test_json_registration_and_verified_installed_version(self):
        calls=[]
        def json_call(codex,*args):
            calls.append(args)
            if args[0]=='list':
                return {'installed':[{'pluginId':'orbit@sevenova','version':VERSION,'installed':True,'enabled':True}]}
            return {}
        with mock.patch.object(self.module, 'codex_json', side_effect=json_call):
            self.module.install_codex('codex',self.dist,VERSION,None)
            self.assertEqual(calls[0],('marketplace','add',str(self.dist)))
            calls.clear()
            self.module.install_codex('codex',self.dist,VERSION,self.row(self.dist))
            self.assertFalse(any(args[0]=='marketplace' for args in calls))
        with mock.patch.object(self.module, 'codex_json', return_value={'installed':[]}):
            with self.assertRaises(self.module.InstallError):
                self.module.install_codex('codex',self.dist,VERSION,self.row(self.dist))

    def test_failed_codex_update_restores_files_and_registration(self):
        self.assertEqual(self.install().returncode,0)
        marker=self.dist/'original.txt'
        marker.write_text('original')
        settings=self.dist/'plugins/codex-orbit/settings.json'
        original=settings.read_bytes()
        args=argparse.Namespace(source=str(ROOT),python=sys.executable,install_dir=str(self.dist),headless=True,
                               use_existing_python=False,data_dir=str(self.root/'new'),credentials_from=None,
                               no_codex=False,codex='codex',enable_plugins=False,dry_run=False)
        with mock.patch.object(self.module,'find_codex',return_value='codex'), \
             mock.patch.object(self.module,'configured_marketplaces',return_value={'sevenova':self.row(self.dist)}), \
             mock.patch.object(self.module,'install_codex',side_effect=self.module.InstallError('failure')), \
             mock.patch.object(self.module,'restore_registration') as restore:
            with self.assertRaises(self.module.InstallError):
                self.module.install(args)
            restore.assert_called_once()
        self.assertEqual(settings.read_bytes(),original)
        self.assertEqual(marker.read_text(),'original')
        self.assertFalse(list(self.dist.parent.glob('marketplace.previous-*')))

    def test_missing_runtime_dependencies_abort_without_replacing_files(self):
        self.assertEqual(self.install().returncode,0)
        original=(self.dist/'plugins/codex-orbit/mcp.json').read_bytes()
        with mock.patch.object(self.module,'execute',return_value='0'):
            with self.assertRaises(self.module.InstallError):
                self.module.prepare_python(sys.executable,self.dist,existing=True)
        self.assertEqual((self.dist/'plugins/codex-orbit/mcp.json').read_bytes(),original)

    def test_successful_codex_install_prepares_native_schedule_using_the_stable_entry(self):
        args=argparse.Namespace(source=str(ROOT),python=sys.executable,install_dir=str(self.dist),headless=True,
            use_existing_python=False,data_dir=str(self.root/'data'),credentials_from=None,no_codex=False,
            codex='codex',enable_plugins=False,dry_run=False,daily_time='23:30',no_schedule=False)
        execute=self.module.execute
        registered=[]
        def spy(command,**kwargs):
            if '--prepare-codex-schedule' in command:
                registered.append([str(value) for value in command])
                return json.dumps(dict(config=dict(time='23:30',enabled=True)))
            return execute(command,**kwargs)
        with mock.patch.object(self.module,'find_codex',return_value='codex'), \
             mock.patch.object(self.module,'configured_marketplaces',return_value={}), \
             mock.patch.object(self.module,'install_codex'),mock.patch.object(self.module,'execute',side_effect=spy):
            self.module.install(args)
        self.assertEqual(registered,[[sys.executable,str(self.dist/'plugins/codex-orbit/scripts/mcp.py'),
                                     '--prepare-codex-schedule','--daily-time','23:30']])
        self.assertFalse((self.root/'data').exists())  # Installation never collects real chats.

    def test_invalid_daily_time_is_rejected_before_installation(self):
        result=self.install('--daily-time','24:00')
        self.assertEqual(result.returncode,2)
        self.assertFalse(self.dist.exists())

    def test_no_schedule_disables_bootstrap_and_upgrade_preserves_choice(self):
        for options in (('--no-schedule',), ()):
            result=self.install(*options)
            self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
            settings=self.dist/'plugins/codex-orbit/settings.json'
            self.assertFalse(json.loads(settings.read_text())['auto_daily_automation'])
            env=dict(os.environ, ORBIT_DATA_DIR=str(self.root/'data'))
            process=subprocess.run([sys.executable, str(self.dist/'plugins/codex-orbit/scripts/mcp.py')],
                input=json.dumps(dict(jsonrpc='2.0',id=1,method='initialize',params={}))+'\n',
                capture_output=True,text=True,env=env,timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertNotIn('prepare_daily_automation', json.loads(process.stdout)['result']['instructions'])
            self.assertFalse((self.root/'data').exists())

        result=self.install('--schedule-backend', 'codex')
        self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
        self.assertTrue(json.loads(settings.read_text())['auto_daily_automation'])

    def test_plugins_flag_opt_in_preserves_other_toml(self):
        original='model = "test"\n[features]\nweb = true\n[other]\nvalue = 1\n'
        updated=self.module.with_plugins_flag(original)
        import tomllib
        parsed=tomllib.loads(updated)
        self.assertTrue(parsed['features']['plugins'])
        self.assertEqual(parsed['other']['value'],1)
        self.assertEqual(self.module.with_plugins_flag(updated),updated)


if __name__ == '__main__':
    unittest.main()
