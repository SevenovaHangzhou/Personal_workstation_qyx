"""Exercise the published file set, npm global command and npx cache behavior."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VERSION = json.loads((ROOT / 'package.json').read_text())['version']


class CodexNpmPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which('node')
        cls.npm = shutil.which('npm')
        if not cls.node or not cls.npm or int(subprocess.check_output([cls.node,'-p','process.versions.node.split(".")[0]'],text=True)) < 18:
            raise unittest.SkipTest('npm integration requires Node.js 18+ and npm')

    def test_tarball_global_install_npx_and_stable_plugin_survive_uninstall(self):
        with tempfile.TemporaryDirectory(prefix='orbit-npm-') as temporary:
            root = Path(temporary)
            source = root / '包源码 with spaces'
            source.mkdir()
            package = json.loads((ROOT / 'package.json').read_text())
            for name in [*package['files'], 'package.json', 'README.md']:
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, target)
            private = source / 'plugins/codex-orbit/settings.json'
            private.write_text('{"data_dir":"/private/machine"}')
            keys = private.parent / '.credentials'
            keys.mkdir()
            (keys / 'linear.key').write_text('canary-not-a-real-key')
            (source / 'unrelated-desktop-file.py').write_text('not packaged')
            env = {key:value for key,value in os.environ.items() if key not in
                   ('LINEAR_API_KEY','ORBIT_DATA_DIR','ORBIT_CREDENTIALS_DIR','PYTHONPATH')}
            env.update(CODEX_HOME=str(root/'codex'),NPM_CONFIG_USERCONFIG=str(root/'empty.npmrc'),NPM_CONFIG_CACHE=str(root/'cache'))
            (root/'empty.npmrc').write_text('')

            def run(args, cwd=root):
                result = subprocess.run(list(map(str,args)),env=env,cwd=cwd,capture_output=True,
                                        text=True,encoding='utf-8',errors='replace',timeout=90)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                return result.stdout

            packed = json.loads(run([self.npm,'pack','--json','--offline','--ignore-scripts'],source))[0]
            # npm 8 JSON retains @scope/name; its actual archive uses scope-name.
            archive = source / packed['filename'].lstrip('@').replace('/', '-')
            names = {item['path'] for item in packed['files']}
            expected = set(package['files']) | {'package.json','README.md'}
            self.assertEqual(names, expected)
            self.assertFalse(any('settings.json' in name or '.credentials/' in name or '__pycache__/' in name for name in names))
            with tarfile.open(archive) as tar:
                self.assertTrue(all(member.isfile() or member.isdir() for member in tar.getmembers()))
                self.assertFalse(any(b'canary-not-a-real-key' in tar.extractfile(member).read()
                                     for member in tar.getmembers() if member.isfile()))
            prefix = root / 'npm prefix'
            run([self.npm,'install','--global','--prefix',prefix,'--offline','--ignore-scripts','--no-audit','--no-fund',archive])
            module_root = Path(run([self.npm,'root','--global','--prefix',prefix]).strip())
            installed = module_root / '@sevenova/orbit-codex'
            cli = installed / 'bin/orbit-codex.cjs'
            self.assertEqual(run([self.node,cli,'--version']).strip(),VERSION)
            if os.name != 'nt':
                self.assertEqual(run([prefix/'bin/orbit-codex','--version']).strip(),VERSION)
            self.assertEqual(run([self.npm,'exec','--yes','--offline','--package='+str(archive),'--','orbit-codex','--version']).strip(),VERSION)
            destination = root / '安装 $(echo literal); 空格' / 'marketplace'
            command = [self.node,cli,'install','--python',sys.executable,'--headless','--no-codex','--install-dir',destination]
            run(command)
            settings = destination / 'plugins/codex-orbit/settings.json'
            settings.write_text(json.dumps({'data_dir':str(root/'my-data')}))
            run([self.npm,'exec','--yes','--offline','--package='+str(archive),'--','orbit-codex',*command[2:]])
            self.assertEqual(json.loads(settings.read_text())['data_dir'],str(root/'my-data'))
            failed = subprocess.run([*command,'--data-dir','relative'],env=env,cwd=root,capture_output=True,text=True,timeout=20)
            self.assertEqual(failed.returncode,2)
            self.assertEqual(json.loads(settings.read_text())['data_dir'],str(root/'my-data'))
            run([self.npm,'uninstall','--global','--prefix',prefix,'--offline','--ignore-scripts','--no-audit','--no-fund','@sevenova/orbit-codex'])
            self.assertFalse(installed.exists())
            shutil.rmtree(root/'cache')
            status = json.loads(run([sys.executable,destination/'plugins/codex-orbit/scripts/mcp.py','--status']))
            self.assertEqual(status['version'],VERSION)
            self.assertEqual(status['data_directory'],str(root/'my-data'))
            self.assertFalse((root/'my-data').exists())


if __name__ == '__main__':
    unittest.main()
