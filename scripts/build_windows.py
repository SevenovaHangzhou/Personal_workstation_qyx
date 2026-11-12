"""Build and verify the native Windows x64 portable application and installer."""
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from typedefine.TypeDefine import APP_VERSION
from packaging_product import build_product
PROFILE=build_product()
SLUG=PROFILE.slug
MANIFEST="evaluator-windows-manifest.json" if SLUG=="orbit-evaluator" else "luma-windows-manifest.json" if SLUG=="luma" else "windows-manifest.json"
SUMS="EVALUATOR-WINDOWS-SHA256SUMS" if SLUG=="orbit-evaluator" else "LUMA-WINDOWS-SHA256SUMS" if SLUG=="luma" else "WINDOWS-SHA256SUMS"
os.environ["ORBIT_APP_PROFILE"]=PROFILE.key


def command(args,**kwargs):return subprocess.run(args,check=True,**kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'release'/APP_VERSION)
    parser.add_argument('--work-dir',type=Path,default=ROOT/'build/windows');args=parser.parse_args()
    if sys.platform!='win32' or platform.machine().lower() not in ('amd64','x86_64'):parser.error('Build on Windows x64.')
    work=args.work_dir.resolve();output=args.output.resolve();work.mkdir(parents=True,exist_ok=True);output.mkdir(parents=True,exist_ok=True)
    archive=output/f'{SLUG}-{APP_VERSION}-windows-x86_64.zip';installer=output/f'{SLUG}-{APP_VERSION}-windows-x86_64-setup.exe'
    if any(p.exists() for p in (archive,installer,output/MANIFEST)):parser.error('Existing release files will not be replaced.')
    for spec in ('orbit.spec','windows-launcher.spec'):
        command([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--distpath',str(work/'dist'),
                 '--workpath',str(work/'pyinstaller'/spec),str(ROOT/'packaging'/spec)],cwd=ROOT)
    bundle=work/'dist'/SLUG;shutil.copy2(work/'dist'/(SLUG+'.exe'),bundle/(SLUG+'.exe'))
    (bundle/'VERSION').write_text(APP_VERSION+'\n',encoding='utf-8')
    shutil.copy2(ROOT/'assets'/(PROFILE.icon_slug+'.ico'),bundle/(SLUG+'.ico'))
    (bundle/'README.txt').write_text(PROFILE.display_name+' '+APP_VERSION+'\n运行 '+SLUG+'.exe。Windows 10/11 x64。无需安装 Python。\n数据：%LOCALAPPDATA%/Sevenova/'+PROFILE.data_name+'\n',encoding='utf-8')
    licenses=bundle/'licenses';licenses.mkdir(exist_ok=True)
    for name in ('PyQt5','PyQt5-Qt5','PyQt5-sip','readerwriterlock','typing_extensions','starlette','anyio','idna','tzdata','Send2Trash','cryptography','cffi','pycparser','psutil','pyinstaller'):
        dist=metadata.distribution(name)
        for relative in dist.files or []:
            if any(word in relative.name.upper() for word in ('LICENSE','COPYING','NOTICE')):
                src=Path(dist.locate_file(relative))
                if src.is_file():
                    dest=licenses/name/Path(*relative.parts[1:]);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
    for candidate in (Path(sys.base_prefix)/'LICENSE.txt',Path(sys.base_prefix)/'LICENSE'):
        if candidate.is_file():shutil.copy2(candidate,licenses/'Python-LICENSE.txt');break
    (bundle/'THIRD_PARTY.md').write_text(
        '# Third-party software\n\nLicenses are included in licenses/ and _internal/licenses/.\n'
        'Python, PyQt5/Qt and dependencies retain their respective licenses.\n'
        'YomkServerPy and Work Station source, pins and notices are in the source repository.\n'
        'Application source: https://github.com/SevenovaHangzhou/Personal_workstation_qyx/tree/'+subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()+'\n',encoding='utf-8')
    def probe(root,label):
        with tempfile.TemporaryDirectory(prefix='orbit-win-probe-') as temporary:
            env=dict(os.environ,QT_QPA_PLATFORM='offscreen',USERPROFILE=temporary,HOME=temporary,LOCALAPPDATA=temporary+'/local',APPDATA=temporary+'/roaming',PYINSTALLER_RESET_ENVIRONMENT='1',ORBIT_SKIP_OTA='1')
            for key in ('PYTHONPATH','PYTHONHOME','YOMK_PREFIX_PATH','CODEX_HOME'):env.pop(key,None)
            for switches,suffix in [(['--version'],'version'),(['--self-test'],'self-test'),(['--self-test','--graph','--graph-port','0'],'graph-self-test')]:
                r=subprocess.run([str(root/(SLUG+'-bin.exe')),*switches],cwd=temporary,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
                if r.returncode:
                    # Only public code/module identifiers; never emit the raw
                    # captured text, exception message, variables or full paths.
                    captured=r.stdout+'\n'+r.stderr
                    locations=[f'{Path(file).name}:{line} ({function})' for file,line,function in re.findall(r'File "([^"\n]+)", line ([0-9]+), in ([A-Za-z_<>][A-Za-z0-9_<>]*)',captured)]
                    kinds=re.findall(r'^([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))(?::|$)',captured,re.MULTILINE)
                    missing=re.findall(r"No module named '([A-Za-z_][A-Za-z0-9_.]*)'",captured)
                    print(f'::error title=Windows frozen probe::{label}/{suffix} exit={r.returncode} types={kinds} missing_modules={missing} code='+ ' > '.join(locations[-6:]),flush=True)
                    r.check_returncode()
                (work/(label+'-'+suffix+'.log')).write_text(r.stdout+r.stderr,encoding='utf-8')
                if suffix=='version':assert APP_VERSION in r.stdout
                else:assert 'daily-mcp-config-override' in r.stdout
            # Verify the no-console launcher path itself with an isolated app startup.
            screenshot=work/(label+'-launcher.png')
            command([str(root/(SLUG+'.exe')),'--data-dir',temporary+'/probe','--screenshot',str(screenshot)],cwd=temporary,env=env,timeout=60)
            assert screenshot.is_file() and screenshot.stat().st_size>1000
    probe(bundle,'frozen')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():z.write(path,SLUG+'/' +path.relative_to(bundle).as_posix())
    with tempfile.TemporaryDirectory(prefix='orbit-win-zip-') as temporary:
        import threading
        from ota_core import extract_windows_package
        extracted=extract_windows_package(archive,Path(temporary),threading.Event(),expected_slug=SLUG)
        probe(extracted,'portable')
    iscc=shutil.which('ISCC.exe') or str(Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Inno Setup 6/ISCC.exe')
    command([iscc,'/DAppVersion='+APP_VERSION,'/DAppSlug='+SLUG,'/DAppIcon='+PROFILE.icon_slug,'/DAppName='+PROFILE.name+' '+PROFILE.wordmark,'/DProductId='+PROFILE.app_id,'/DBundleDir='+str(bundle),'/DOutputDir='+str(output),str(ROOT/'packaging/windows-installer.iss')])
    with tempfile.TemporaryDirectory(prefix='orbit-win-install-') as temporary:
        target=Path(temporary)/'app'
        command([str(installer),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS','/DIR='+str(target)],timeout=90)
        probe(target,'installer')
        command([str(target/'unins000.exe'),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],timeout=90)
    artifacts=[dict(file=p.name,bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (archive,installer)]
    revision=command(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True).stdout.strip()
    manifest=dict(app_id=PROFILE.app_id,product=PROFILE.product,version=APP_VERSION,platform='windows-x86_64',minimum_windows='10',source_commit=revision,built_at=datetime.now(timezone.utc).isoformat(),
        python=platform.python_version(),runtime_packages={name:metadata.version(name) for name in ('PyQt5','PyQt5-Qt5','PyQt5-sip','readerwriterlock','starlette','tzdata','Send2Trash','pyinstaller')},artifacts=artifacts,verification=['Windows native tests','frozen self-test + graph','portable self-test + graph','installer install/uninstall + self-test + graph','no-console launcher screenshots'],user_data_included=False,codex_credentials_included=False)
    (output/MANIFEST).write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/SUMS).write_text(''.join(f'{row["sha256"]}  {row["file"]}\n' for row in artifacts),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main()
    except Exception as error:
        import traceback
        frames=traceback.extract_tb(error.__traceback__)
        where=' > '.join(f'{Path(f.filename).name}:{f.lineno} ({f.name})' for f in frames[-6:])
        print('::error title=Windows build location::'+type(error).__name__+' | '+where)
        raise
