from pathlib import Path
import os
slug="luma" if os.environ.get("ORBIT_APP_PROFILE")=="study" else "orbit"
root=Path(SPECPATH).parent
a=Analysis([str(root/('luma_windows_launcher.py' if slug=='luma' else 'orbit_windows_launcher.py'))],pathex=[str(root)],binaries=[],datas=[],hiddenimports=[],hookspath=[],runtime_hooks=[],excludes=['PyQt5'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name=slug,debug=False,strip=False,upx=False,console=False,icon=str(root/('assets/'+slug+'.ico')))
