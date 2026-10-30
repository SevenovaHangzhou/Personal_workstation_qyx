"""Keep bundled libraries out of system tools without changing parent state."""
import os
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
