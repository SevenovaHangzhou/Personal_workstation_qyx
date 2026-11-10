from pathlib import Path
root=Path(SPECPATH).parent
a=Analysis([str(root/'windows_launcher.py')],pathex=[str(root)],binaries=[],datas=[],hiddenimports=[],hookspath=[],runtime_hooks=[],excludes=['PyQt5'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='orbit',debug=False,strip=False,upx=False,console=False,icon=str(root/'assets/orbit.ico'))
