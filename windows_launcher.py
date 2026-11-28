"""Per-user Windows OTA bootstrap. Keep the base install as the fallback."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from app_profile import PROFILE
EVALUATOR=os.environ.get('ORBIT_EVALUATOR_BUILD')=='1'
SLUG='orbit-evaluator' if EVALUATOR else PROFILE.slug
DATA_NAME='OrbitEvaluator' if EVALUATOR else PROFILE.data_name

VERSION_RE=re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z')

def version(value):
    if not VERSION_RE.fullmatch(value):raise ValueError('invalid version')
    return tuple(map(int,value.split('.')))

def _launch():
    base=Path(sys.executable).resolve().parent
    current=(base/'VERSION').read_text(encoding='utf-8').strip()
    home=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))
    data=home/'Sevenova'/DATA_NAME
    args=sys.argv[1:]
    for i,arg in enumerate(args):
        if arg=='--data-dir' and i+1<len(args):data=Path(args[i+1]).absolute()
        elif arg.startswith('--data-dir='):data=Path(arg.split('=',1)[1]).absolute()
    updates=data/'updates';target=base;candidate=''
    if not os.environ.get('ORBIT_SKIP_OTA') and '--self-test' not in args:
        try:
            pointer=updates/'current-version'
            if not pointer.is_symlink():
                candidate=pointer.read_text(encoding='utf-8').strip();version(candidate)
                path=updates/'releases'/candidate
                if version(candidate)>version(current) and not path.is_symlink() and path.is_dir():
                    stamp=json.loads((path/'ota-install.json').read_text(encoding='utf-8'))
                    if stamp.get('app_id')==PROFILE.app_id and stamp['version']==candidate and (path/(SLUG+'-bin.exe')).is_file():target=path
        except (OSError,ValueError,KeyError):pass
    logs=home/'Sevenova'/DATA_NAME/'logs';logs.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ);env['ORBIT_SKIP_OTA']='1';marker=None
    if target!=base:
        updates.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='launch-health.',dir=updates);os.close(fd);marker=Path(name)
        env['ORBIT_OTA_HEALTH_FILE']=str(marker)
    try:
        with (logs/'startup.log').open('ab') as stream:
            result=subprocess.run([str(target/(SLUG+'-bin.exe')),*args],env=env,stdout=stream,stderr=stream,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
            if target!=base and result.returncode not in (0,75) and not marker.read_text(encoding='utf-8').strip():
                (updates/'failed-version').write_text(candidate+'\n',encoding='utf-8')
                try:(updates/'current-version').unlink()
                except FileNotFoundError:pass
                result=subprocess.run([str(base/(SLUG+'-bin.exe')),*args],env=env,stdout=stream,stderr=stream,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            return result.returncode
    finally:
        if marker:marker.unlink(missing_ok=True)

def show_startup_error():
    import ctypes
    logs = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData/Local')))/'Sevenova'/DATA_NAME/'logs/startup.log'
    title = '星序评估启动失败' if EVALUATOR else PROFILE.name+'启动失败'
    message = ('应用未能正常启动。请完整解压便携包，或关闭旧版后重新运行安装程序。\n'
               '问题仍存在时，请将报错情况和下方日志交给支持人员。日志可能包含工作信息，请勿公开上传。\n\n'
               '启动日志（如已生成）：\n'+str(logs))
    ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)


def main():
    try:
        result = _launch()
    except (OSError, ValueError):
        result = 1
    if EVALUATOR and result not in (0, 75) and not any(arg in ('--self-test', '--version', '--screenshot') for arg in sys.argv[1:]):
        show_startup_error()
    return result


if __name__=='__main__':raise SystemExit(main())
