# PyInstaller recipe. Paths are relative to the repository, never a user's home.
from pathlib import Path

root = Path(SPECPATH).parent
data = [(str(root/'assets'), 'assets'),
        (str(root/'.agents/skills/learning-course-authoring'), '.agents/skills/learning-course-authoring')]
for name, source in [('YomkServerPy', root/'vendor/yomkserverpy/LICENSE.txt'),
                     ('Work-Station', root/'vendor/work_station/LICENSE')]:
    data.append((str(source), 'licenses/'+name))
a = Analysis([str(root/'main.py')],
             pathex=[str(root), str(root/'runtime/python'), str(root/'vendor/work_station')],
             binaries=[], datas=data,
             hiddenimports=['YomkApi', 'YomkServerPy', 'YomkServerPyVersion'],
             hookspath=[], runtime_hooks=[], excludes=['tkinter', 'PyQt6', 'PySide2', 'PySide6'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='orbit-bin', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='orbit')
