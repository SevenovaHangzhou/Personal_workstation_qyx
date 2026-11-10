"""Per-user Windows OTA bootstrap. Keep the base install as the fallback."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

VERSION_RE=re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z')

def version(value):
    if not VERSION_RE.fullmatch(value):raise ValueError('invalid version')
    return tuple(map(int,value.split('.')))

def main():
    base=Path(sys.executable).resolve().parent
    current=(base/'VERSION').read_text().strip()
    home=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))
    data=home/'Sevenova/Orbit'
    args=sys.argv[1:]
    for i,arg in enumerate(args):
        if arg=='--data-dir' and i+1<len(args):data=Path(args[i+1]).absolute()
        elif arg.startswith('--data-dir='):data=Path(arg.split('=',1)[1]).absolute()
    updates=data/'updates';target=base;candidate=''
    if not os.environ.get('ORBIT_SKIP_OTA') and '--self-test' not in args:
        try:
            pointer=updates/'current-version'
            if not pointer.is_symlink():
                candidate=pointer.read_text().strip();version(candidate)
                path=updates/'releases'/candidate
                if version(candidate)>version(current) and not path.is_symlink() and path.is_dir():
                    stamp=json.loads((path/'ota-install.json').read_text())
                    if stamp['version']==candidate and (path/'orbit-bin.exe').is_file():target=path
        except (OSError,ValueError,KeyError):pass
    logs=home/'Sevenova/Orbit/logs';logs.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ);env['ORBIT_SKIP_OTA']='1';marker=None
    if target!=base:
        updates.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='launch-health.',dir=updates);os.close(fd);marker=Path(name)
        env['ORBIT_OTA_HEALTH_FILE']=str(marker)
    try:
        with (logs/'startup.log').open('ab') as stream:
            result=subprocess.run([str(target/'orbit-bin.exe'),*args],env=env,stdout=stream,stderr=stream,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
            if target!=base and result.returncode not in (0,75) and not marker.read_text().strip():
                (updates/'failed-version').write_text(candidate+'\n')
                try:(updates/'current-version').unlink()
                except FileNotFoundError:pass
                result=subprocess.run([str(base/'orbit-bin.exe'),*args],env=env,stdout=stream,stderr=stream,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            return result.returncode
    finally:
        if marker:marker.unlink(missing_ok=True)

if __name__=='__main__':raise SystemExit(main())
