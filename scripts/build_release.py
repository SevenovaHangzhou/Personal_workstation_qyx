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
sys.path.insert(0,str(ROOT))
from app_profile import profile
PROFILE=profile(os.environ.get("ORBIT_APP_PROFILE","work"))
SLUG=PROFILE.slug
MANIFEST="luma-manifest.json" if SLUG=="luma" else "manifest.json"
SUMS="LUMA-SHA256SUMS" if SLUG=="luma" else "SHA256SUMS"
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


def check_desktop_dependencies(plugins, log_path, libraries=None):
    """An offscreen smoke test cannot detect missing X11 plugin libraries."""
    xcb=plugins/'platforms/libqxcb.so'
    if not xcb.is_file():
        raise RuntimeError('Qt xcb 桌面插件缺失：'+str(xcb))
    env=dict(os.environ)
    if libraries:
        env['LD_LIBRARY_PATH']=str(libraries)
    checks=[xcb]+sorted((plugins/'xcbglintegrations').glob('*.so'))
    missing=set(); reports=[]
    for plugin in checks:
        result=subprocess.run(['ldd',str(plugin)],env=env,capture_output=True,text=True)
        reports.append(str(plugin)+'\n'+result.stdout+result.stderr)
        for row in result.stdout.splitlines():
            if '=> not found' in row: missing.add(row.split('=>',1)[0].strip())
        if result.returncode:
            log_path.write_text('\n'.join(reports))
            raise RuntimeError('Qt 桌面依赖检查失败：'+str(plugin))
    log_path.write_text('\n'.join(reports))
    if missing:
        raise RuntimeError('Qt 桌面依赖缺失：'+', '.join(sorted(missing))+
                           '。请补齐系统运行库后重新打包；Ubuntu 常用依赖：libxcb-xinerama0、libxkbcommon-x11-0。')


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
    stem = f'{SLUG}-{release_version}-linux-x86_64'
    tar_path = output/(stem+'.tar.gz'); deb_path = output/f'sevenova-{SLUG}_{release_version}_amd64.deb'
    if any(p.exists() for p in (tar_path, deb_path, output/MANIFEST)):
        parser.error('Release files already exist. Use a new output directory to avoid replacing a published build.')
    # ASCII import alias fixes PyQt5 wheels whose Qt prefix loses Unicode paths.
    # Reuse the installed local dependencies; do not download a second runtime.
    import PyQt5
    check_desktop_dependencies(Path(PyQt5.__file__).resolve().parent/'Qt5/plugins',work/'desktop-source-dependencies.log')
    env = dict(os.environ); env['ORBIT_APP_PROFILE']=PROFILE.key; env['PYINSTALLER_CONFIG_DIR'] = str(work/'cache')
    with tempfile.TemporaryDirectory(prefix='orbit-build-imports-') as alias:
        site = Path(alias)/'site'; site.symlink_to(Path(PyQt5.__file__).resolve().parent.parent, target_is_directory=True)
        env['PYTHONPATH'] = str(site) + (os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        with (work/'build.log').open('w') as log:
            run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                 '--distpath', str(work/'dist'), '--workpath', str(work/'pyinstaller'),
                 str(ROOT/'packaging/orbit.spec')], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    bundle = work/'dist'/SLUG
    check_desktop_dependencies(bundle/'_internal/PyQt5/Qt5/plugins',work/'desktop-bundle-dependencies.log',bundle/'_internal')
    shutil.copy2(ROOT/'packaging/launch.sh', bundle/SLUG); (bundle/SLUG).chmod(0o755)
    shutil.copy2(ROOT/'packaging/ota-launch.sh', bundle/'ota-launch.sh')
    (bundle/'VERSION').write_text(release_version+'\n')
    (bundle/'PRODUCT').write_text(PROFILE.key+'\n')
    shutil.copy2(ROOT/'assets'/(SLUG+'.svg'), bundle/'orbit.svg')
    versions = add_notices(bundle)
    (bundle/'README.txt').write_text(PROFILE.display_name+' '+release_version+'\n运行 ./'+SLUG+'；无需安装 Python。\n'
        +'数据：~/.local/share/Sevenova/'+PROFILE.data_name+'\n关闭窗口驻留托盘；从托盘完整退出后更新生效。\n',encoding='utf-8')
    probe_env = dict(os.environ); probe_env['QT_QPA_PLATFORM'] = 'offscreen'
    # Probe the frozen binary from a different working directory, without source imports.
    for key in ('PYTHONPATH', 'PYTHONHOME', 'YOMK_PREFIX_PATH', 'LD_LIBRARY_PATH'):
        probe_env.pop(key, None)
    probe_env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    with tempfile.TemporaryDirectory(prefix='orbit-package-test-') as temporary:
        probe_env['HOME'] = temporary
        probe_env['XDG_DATA_HOME'] = str(Path(temporary)/'data')
        probe_env['XDG_STATE_HOME'] = str(Path(temporary)/'state')
        for switches, label in [(['--version'], 'version'), (['--self-test'], 'self-test'),
                                (['--self-test', '--graph', '--graph-port', '0'], 'graph-self-test')]:
            result = subprocess.run([str(bundle/SLUG), *switches], cwd=temporary, env=probe_env,
                                    capture_output=True, text=True, timeout=60)
            (work/(label+'.log')).write_text(result.stdout+result.stderr)
            result.check_returncode()
            if label == 'version' and release_version not in result.stdout:
                raise RuntimeError('Frozen version check failed')
        run([str(bundle/(SLUG+'-bin')), '--data-dir', str(Path(temporary)/'screenshot-data'),
             '--screenshot', str(work/'application.png')], cwd=temporary, env=probe_env,
            capture_output=True, timeout=60)
    # A fixed timestamp/order makes the tar envelope stable for identical inputs.
    with tar_path.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            archive.add(bundle, arcname=SLUG, filter=normalized)
    deb_root = work/'deb'
    if deb_root.exists():
        shutil.rmtree(deb_root)
    (deb_root/'DEBIAN').mkdir(parents=True)
    app_dir = deb_root/('opt/sevenova-'+SLUG); shutil.copytree(bundle, app_dir)
    (deb_root/'usr/bin').mkdir(parents=True); (deb_root/('usr/bin/'+SLUG)).symlink_to('/opt/sevenova-'+SLUG+'/'+SLUG)
    desktop = deb_root/'usr/share/applications'; desktop.mkdir(parents=True)
    shutil.copy2(ROOT/'packaging'/(PROFILE.app_id+'.desktop'), desktop)
    icons = deb_root/'usr/share/icons/hicolor/scalable/apps'; icons.mkdir(parents=True)
    shutil.copy2(ROOT/'assets'/(SLUG+'.svg'), icons/(PROFILE.app_id+'.svg'))
    size = sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file())//1024
    (deb_root/'DEBIAN/control').write_text(
        f'Package: sevenova-{SLUG}\nVersion: {release_version}\nArchitecture: amd64\nMaintainer: Sevenova\n'
        f'Installed-Size: {size}\nSection: utils\nPriority: optional\n'
        'Depends: libc6 (>= 2.35), libglib2.0-0, libgl1, libegl1, libx11-6, libxcb1, libxcb-xinerama0, libxkbcommon-x11-0, libfontconfig1, git, libglib2.0-bin\n'
        'Recommends: fonts-noto-cjk\nHomepage: https://github.com/SevenovaHangzhou/Personal_workstation_qyx\n'
        'Description: ORBIT native desktop task and learning application\n'
        ' Local tasks, progress records, Codex chat links, guided courses and nightly reports.\n')
    run(['dpkg-deb', '--root-owner-group', '-Zxz', '--build', str(deb_root), str(deb_path)])
    with tempfile.TemporaryDirectory(prefix='orbit-artifact-test-') as temporary:
        extracted = Path(temporary)
        with tarfile.open(tar_path) as archive:
            archive.extractall(extracted/'portable', filter='data')
        run(['dpkg-deb', '--extract', str(deb_path), str(extracted/'debian')])
        for name, binary in [('portable', extracted/'portable'/SLUG/SLUG),
                             ('debian', extracted/('debian/opt/sevenova-'+SLUG)/SLUG)]:
            result = run([str(binary), '--self-test'], cwd=temporary, env=probe_env,
                         capture_output=True, text=True, timeout=60)
            (work/(name+'-self-test.log')).write_text(result.stdout+result.stderr)
            result = run([str(binary), '--self-test', '--graph', '--graph-port', '0'], cwd=temporary,
                         env=probe_env, capture_output=True, text=True, timeout=60)
            (work/(name+'-graph-self-test.log')).write_text(result.stdout+result.stderr)
        # Reproduce /usr/bin/orbit without installing into the host system.
        entry = extracted/'command-bin'/SLUG; entry.parent.mkdir()
        entry.symlink_to(extracted/('debian/opt/sevenova-'+SLUG)/SLUG)
        result = run([str(entry), '--self-test'], cwd=temporary, env=probe_env,
                     capture_output=True, text=True, timeout=60)
        (work/'debian-command-self-test.log').write_text(result.stdout+result.stderr)
    artifacts = [dict(file=p.name, bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (tar_path, deb_path)]
    revision = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(app_id=PROFILE.app_id,product=PROFILE.key,version=release_version, platform='linux-x86_64', minimum_glibc='2.35',
                    source_commit=revision, built_at=datetime.now(timezone.utc).isoformat(),
                    python=platform.python_version(), pyinstaller=metadata.version('pyinstaller'),
                    runtime_packages=versions, artifacts=artifacts,
                    verification=['Qt xcb and GL plugin dependency checks', 'frozen --version', 'frozen --self-test', 'offscreen startup/screenshot',
                                  'extracted portable --self-test', 'extracted Debian --self-test',
                                  'Debian-style command symlink --self-test',
                                  'frozen and extracted portable/Debian --self-test --graph (assets + product services)'],
                    user_data_included=False, codex_credentials_included=False)
    (output/MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    (output/SUMS).write_text(''.join(f'{row["sha256"]}  {row["file"]}\n' for row in artifacts))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def normalized(info):
    info.uid = info.gid = 0; info.uname = info.gname = 'root'; info.mtime = 0
    return info


if __name__ == '__main__':
    main()
