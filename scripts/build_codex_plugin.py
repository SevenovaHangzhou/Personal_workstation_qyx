"""Build a self-contained local marketplace and ZIP using an explicit allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SHARED = ('linear_support.py', 'model.py', 'git_support.py', 'task_plan.py', 'dag_layout.py',
          'process_environment.py', 'credentials.py', 'windows_credentials.py', 'data_paths.py', 'platform_support.py', 'qt_runtime.py')


def build(destination):
    destination = Path(destination).resolve()
    # Never replace an existing distribution that might contain local configuration.
    destination.mkdir(parents=True, exist_ok=False)
    plugin = destination / 'plugins/codex-orbit'
    source = ROOT / 'plugins/codex-orbit'
    shutil.copytree(source, plugin, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'shared', '.credentials', 'codex.sqlite3*', 'settings.json'))
    # Configuration is per device. A built ZIP always starts with portable paths.
    mcp_path = plugin / 'mcp.json'
    portable = json.loads(mcp_path.read_text(encoding='utf-8'))
    portable['mcpServers']['orbit'] = {'type': 'stdio', 'command': 'python3',
                                      'args': ['${PLUGIN_ROOT}/scripts/mcp.py'], 'cwd': '${PLUGIN_ROOT}'}
    mcp_path.write_text(json.dumps(portable, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    shared = plugin / 'shared'
    shared.mkdir()
    for name in SHARED:
        shutil.copy2(ROOT / name, shared / name)
    marketplace = {'name': 'sevenova', 'interface': {'displayName': 'Sevenova / 七耀星'},
                   'plugins': [{'name': 'orbit', 'source': {'source': 'local', 'path': './plugins/codex-orbit'},
                                'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'},
                                'category': 'Productivity'}]}
    catalog = destination / '.agents/plugins/marketplace.json'
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps(marketplace, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    version = json.loads((plugin / 'plugin.json').read_text(encoding='utf-8'))['version']
    files = {str(path.relative_to(destination)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(destination.rglob('*')) if path.is_file()}
    (destination / 'BUILD.json').write_text(json.dumps({'plugin': 'orbit', 'version': version, 'sha256': files}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    archive = destination.parent / f'orbit-codex-{version}.zip'
    if archive.exists():
        raise FileExistsError(archive)
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(destination.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(destination))
    (archive.parent / (archive.name + '.sha256')).write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n', encoding='utf-8')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='不存在的输出目录')
    print(build(parser.parse_args().output))
