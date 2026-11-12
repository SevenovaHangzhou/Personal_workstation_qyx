# PyInstaller recipe. Paths are relative to the repository, never a user's home.
from pathlib import Path
import ssl
import os
product=os.environ.get("ORBIT_APP_PROFILE","work")
slug="luma" if product=="study" else "orbit"
evaluator=os.environ.get('ORBIT_EVALUATOR_BUILD')=='1'
if evaluator: slug='orbit-evaluator'
icon_slug='orbit' if evaluator else slug

root = Path(SPECPATH).parent
data = [(str(root/'assets'), 'assets'),
        (str(root/'yomk_graph/web'), 'yomk_graph/web'),
        (str(root/'vendor/yomkgraph-pin.json'), 'licenses/YomkGraph'),
        (str(root/'.agents/skills/learning-course-authoring'), '.agents/skills/learning-course-authoring')]
# Some bundled Python runtimes load an adjacent public CA policy file when
# creating SSLContext. Keep that runtime policy intact in the frozen app.
tls_policy = Path(ssl.__file__).with_name('codex-rejected-cas.pem')
if tls_policy.is_file():
    data.append((str(tls_policy), '.'))
for name, source in [('YomkServerPy', root/'vendor/yomkserverpy/LICENSE.txt'),
                     ('Work-Station', root/'vendor/work_station/LICENSE')]:
    data.append((str(source), 'licenses/'+name))
a = Analysis([str(root/('evaluator.py' if evaluator else slug+'.py'))],
             pathex=[str(root), str(root/'runtime/python'), str(root/'vendor/work_station')],
             binaries=[], datas=data,
             hiddenimports=['YomkApi', 'YomkServerPy', 'YomkServerPyVersion', 'yomk_graph', 'yomk_graph.python', 'yomk_graph.collector', 'yomk_graph.server'],
             hookspath=[], runtime_hooks=[], excludes=['tkinter', 'PyQt6', 'PySide2', 'PySide6'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [('X utf8', None, 'OPTION')], exclude_binaries=True, name=slug+'-bin', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=True, icon=str(root/('assets/'+icon_slug+'.ico')) if (root/('assets/'+icon_slug+'.ico')).exists() else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=slug)
