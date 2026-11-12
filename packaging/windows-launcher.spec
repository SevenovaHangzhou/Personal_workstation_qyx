from pathlib import Path
import os
slug="luma" if os.environ.get("ORBIT_APP_PROFILE")=="study" else "orbit"
evaluator=os.environ.get('ORBIT_EVALUATOR_BUILD')=='1'
if evaluator: slug='orbit-evaluator'
icon_slug='orbit' if evaluator else slug
root=Path(SPECPATH).parent
a=Analysis([str(root/('evaluator_windows_launcher.py' if evaluator else 'luma_windows_launcher.py' if slug=='luma' else 'orbit_windows_launcher.py'))],pathex=[str(root)],binaries=[],datas=[],hiddenimports=[],hookspath=[],runtime_hooks=[],excludes=['PyQt5'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name=slug,debug=False,strip=False,upx=False,console=False,icon=str(root/('assets/'+icon_slug+'.ico')))
