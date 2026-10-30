#!/usr/bin/env python3
"""Build and test a self-contained Linux bundle and Debian package locally."""
import argparse
import ast
import gzip
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGES = ['PyQt5', 'PyQt5-Qt5', 'PyQt5-sip', 'readerwriterlock',
                    'typing_extensions', 'starlette', 'anyio', 'idna']


def version():
    tree = ast.parse((ROOT/'typedefine/TypeDefine.py').read_text())
    return next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'APP_VERSION' for t in node.targets))


def run(command, **options):
    return subprocess.run(command, check=True, **options)


def add_notices(bundle):
    versions = {name: metadata.version(name) for name in RUNTIME_PACKAGES}
    for name in RUNTIME_PACKAGES + ['pyinstaller']:
        dist = metadata.distribution(name)
        for relative in dist.files or []:
            if not any(word in relative.name.upper() for word in ('LICENSE', 'COPYING', 'NOTICE')):
                continue
            source = Path(dist.locate_file(relative))
            if not source.is_file():
                continue
            target = bundle/'licenses'/name/Path(*relative.parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
    for folder in (Path(sys.base_prefix), Path(sysconfig.get_path('stdlib'))):
        for name in ('LICENSE', 'LICENSE.txt'):
            path = folder/name
            if path.is_file():
                shutil.copy2(path, bundle/'licenses'/('Python-'+name))
    (bundle/'THIRD_PARTY.md').write_text(
        '# Third-party software\n\nLicenses are included in licenses/ and _internal/licenses/.\n'
        'Python, PyQt5/Qt and their dependencies retain their respective licenses.\n'
        'YomkServerPy and Work Station source, pins and notices are in the source repository.\n'
        'Application source: https://github.com/SevenovaHangzhou/Personal_workstation_qyx/tree/v'+version()+'\n\n'
        + '\n'.join(f'- {name} {value}' for name, value in versions.items())+'\n')
    return versions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'release'/version())
    parser.add_argument('--work-dir', type=Path, default=ROOT/'build/release')
    args = parser.parse_args()
    if sys.platform != 'linux' or platform.machine() != 'x86_64':
        parser.error('This recipe targets Linux x86_64. Build on that platform.')
    if not shutil.which('dpkg-deb') or not shutil.which('git'):
        parser.error('Install dpkg-deb and git before building.')
    release_version = version()
    output = args.output.resolve(); work = args.work_dir.resolve()
    output.mkdir(parents=True, exist_ok=True); work.mkdir(parents=True, exist_ok=True)
    stem = f'orbit-{release_version}-linux-x86_64'
    tar_path = output/(stem+'.tar.gz'); deb_path = output/f'sevenova-orbit_{release_version}_amd64.deb'
    if any(p.exists() for p in (tar_path, deb_path, output/'manifest.json')):
        parser.error('Release files already exist. Use a new output directory to avoid replacing a published build.')
    # ASCII import alias fixes PyQt5 wheels whose Qt prefix loses Unicode paths.
    # Reuse the installed local dependencies; do not download a second runtime.
    import PyQt5
    env = dict(os.environ); env['PYINSTALLER_CONFIG_DIR'] = str(work/'cache')
    with tempfile.TemporaryDirectory(prefix='orbit-build-imports-') as alias:
        site = Path(alias)/'site'; site.symlink_to(Path(PyQt5.__file__).resolve().parent.parent, target_is_directory=True)
        env['PYTHONPATH'] = str(site) + (os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        with (work/'build.log').open('w') as log:
            run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                 '--distpath', str(work/'dist'), '--workpath', str(work/'pyinstaller'),
                 str(ROOT/'packaging/orbit.spec')], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    bundle = work/'dist/orbit'
    shutil.copy2(ROOT/'packaging/launch.sh', bundle/'orbit'); (bundle/'orbit').chmod(0o755)
    shutil.copy2(ROOT/'assets/orbit.svg', bundle/'orbit.svg')
    versions = add_notices(bundle)
    (bundle/'README.txt').write_text(
        f'星序 ORBIT {release_version}\n\n运行 ./orbit；无需安装 Python。\n'
        '需要 Linux x86_64、glibc 2.35+ 和桌面图形环境。\n'
        'Codex CLI、Git 及其配置使用系统现有安装，登录不包含在包中。\n'
        '数据沿用 ~/.local/share/MomoLocal/Momo，关闭主窗口会驻留托盘。\n'
        '启动日志：${XDG_STATE_HOME:-~/.local/state}/orbit/startup.log\n'
        '退出：托盘菜单“停止执行并退出”。\n'
        '离线自检：QT_QPA_PLATFORM=offscreen ./orbit --self-test\n')
    probe_env = dict(os.environ); probe_env['QT_QPA_PLATFORM'] = 'offscreen'
    # Probe the frozen binary from a different working directory, without source imports.
    for key in ('PYTHONPATH', 'PYTHONHOME', 'YOMK_PREFIX_PATH'):
        probe_env.pop(key, None)
    probe_env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    with tempfile.TemporaryDirectory(prefix='orbit-package-test-') as temporary:
        probe_env['HOME'] = temporary
        probe_env['XDG_DATA_HOME'] = str(Path(temporary)/'data')
        probe_env['XDG_STATE_HOME'] = str(Path(temporary)/'state')
        for switch in ('--version', '--self-test'):
            result = run([str(bundle/'orbit'), switch], cwd=temporary, env=probe_env,
                         capture_output=True, text=True, timeout=60)
            (work/(switch[2:]+'.log')).write_text(result.stdout+result.stderr)
            if switch == '--version' and release_version not in result.stdout:
                raise RuntimeError('Frozen version check failed')
        run([str(bundle/'orbit-bin'), '--data-dir', str(Path(temporary)/'screenshot-data'),
             '--screenshot', str(work/'application.png')], cwd=temporary, env=probe_env,
            capture_output=True, timeout=60)
    # A fixed timestamp/order makes the tar envelope stable for identical inputs.
    with tar_path.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            archive.add(bundle, arcname='orbit', filter=normalized)
    deb_root = work/'deb'
    if deb_root.exists():
        shutil.rmtree(deb_root)
    (deb_root/'DEBIAN').mkdir(parents=True)
    app_dir = deb_root/'opt/sevenova-orbit'; shutil.copytree(bundle, app_dir)
    (deb_root/'usr/bin').mkdir(parents=True); (deb_root/'usr/bin/orbit').symlink_to('/opt/sevenova-orbit/orbit')
    desktop = deb_root/'usr/share/applications'; desktop.mkdir(parents=True)
    shutil.copy2(ROOT/'packaging/com.sevenova.Orbit.desktop', desktop)
    icons = deb_root/'usr/share/icons/hicolor/scalable/apps'; icons.mkdir(parents=True)
    shutil.copy2(ROOT/'assets/orbit.svg', icons/'com.sevenova.Orbit.svg')
    size = sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file())//1024
    (deb_root/'DEBIAN/control').write_text(
        f'Package: sevenova-orbit\nVersion: {release_version}\nArchitecture: amd64\nMaintainer: Sevenova\n'
        f'Installed-Size: {size}\nSection: utils\nPriority: optional\n'
        'Depends: libc6 (>= 2.35), libglib2.0-0, libgl1, libegl1, libx11-6, libxcb1, libxkbcommon-x11-0, libfontconfig1, git, libglib2.0-bin\n'
        'Recommends: fonts-noto-cjk\nHomepage: https://github.com/SevenovaHangzhou/Personal_workstation_qyx\n'
        'Description: ORBIT native desktop task and learning application\n'
        ' Local tasks, progress records, Codex chat links, guided courses and nightly reports.\n')
    run(['dpkg-deb', '--root-owner-group', '-Zxz', '--build', str(deb_root), str(deb_path)])
    with tempfile.TemporaryDirectory(prefix='orbit-artifact-test-') as temporary:
        extracted = Path(temporary)
        with tarfile.open(tar_path) as archive:
            archive.extractall(extracted/'portable', filter='data')
        run(['dpkg-deb', '--extract', str(deb_path), str(extracted/'debian')])
        for name, binary in [('portable', extracted/'portable/orbit/orbit'),
                             ('debian', extracted/'debian/opt/sevenova-orbit/orbit')]:
            result = run([str(binary), '--self-test'], cwd=temporary, env=probe_env,
                         capture_output=True, text=True, timeout=60)
            (work/(name+'-self-test.log')).write_text(result.stdout+result.stderr)
    artifacts = [dict(file=p.name, bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (tar_path, deb_path)]
    revision = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(version=release_version, platform='linux-x86_64', minimum_glibc='2.35',
                    source_commit=revision, built_at=datetime.now(timezone.utc).isoformat(),
                    python=platform.python_version(), pyinstaller=metadata.version('pyinstaller'),
                    runtime_packages=versions, artifacts=artifacts,
                    verification=['frozen --version', 'frozen --self-test', 'offscreen startup/screenshot',
                                  'extracted portable --self-test', 'extracted Debian --self-test'],
                    user_data_included=False, codex_credentials_included=False)
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    (output/'SHA256SUMS').write_text(''.join(f'{row["sha256"]}  {row["file"]}\n' for row in artifacts))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def normalized(info):
    info.uid = info.gid = 0; info.uname = info.gname = 'root'; info.mtime = 0
    return info


if __name__ == '__main__':
    main()
