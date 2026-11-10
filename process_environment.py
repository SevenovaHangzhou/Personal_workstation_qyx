"""Keep bundled libraries out of system tools without changing parent state."""
import os
import shutil
from pathlib import Path
import sys


def system_environment():
    env = dict(os.environ)
    if not getattr(sys, 'frozen', False):
        return env
    original = env.pop('LD_LIBRARY_PATH_ORIG', None)
    if original is None:
        env.pop('LD_LIBRARY_PATH', None)
    else:
        env['LD_LIBRARY_PATH'] = original
    bundle = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    for key in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH'):
        if key not in env:
            continue
        paths = [p for p in env[key].split(os.pathsep) if p and not Path(p).is_relative_to(bundle)]
        if paths:
            env[key] = os.pathsep.join(paths)
        else:
            env.pop(key)
    return env


def codex_environment():
    """Keep PATH precedence and include common user installs for desktop launches."""
    env = system_environment()
    paths = env.get('PATH', os.defpath).split(os.pathsep)
    home = Path.home()
    for directory in (home/'.local/bin', home/'bin', home/'.npm-global/bin', home/'.volta/bin', home/'AppData/Roaming/npm', home/'AppData/Local/Programs/Codex'):
        value = str(directory)
        if directory.is_dir() and value not in paths:
            paths.append(value)
    env['PATH'] = os.pathsep.join(paths)
    return env


def find_codex():
    """Resolve the same CLI path used by child processes; never change login."""
    path=codex_environment()['PATH']
    if os.name=='nt':
        # Launch the native npm payload directly, not a .cmd wrapper through a
        # shell (which would change quoting and process-tree cancellation).
        wrapper=shutil.which('codex',path=path)
        if wrapper:
            base=Path(wrapper).parent
            matches=list(base.glob('node_modules/@openai/codex*/**/codex.exe'))
            if matches:return str(matches[0])
        return shutil.which('codex.exe',path=path)
    return shutil.which('codex',path=path)
