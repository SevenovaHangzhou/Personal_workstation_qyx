"""Build a server-only source bundle from explicit audited paths, without user data."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]
FILES=['collaboration/__init__.py','collaboration/protocol.py','collaboration/store.py','collaboration/server.py','collaboration/files.py',
       'services/__init__.py','services/base.py','framework.py']


def build(output):
    files={name:(ROOT/name).read_bytes() for name in FILES}
    files['install.py']=(ROOT/'server/install.py').read_bytes()
    files['requirements-server.txt']=b'readerwriterlock==1.0.9\ntyping-extensions==4.16.0\n'
    for name in ('YomkApi.py','YomkServerPy.py'):
        files['runtime/python/'+name]=(ROOT/'vendor/yomkserverpy/src'/name).read_bytes()
    files['runtime/python/YomkServerPyVersion.py']=b'__version__ = "1.5.6"\n'
    files['licenses/YomkServerPy.txt']=(ROOT/'vendor/yomkserverpy/LICENSE.txt').read_bytes()
    manifest={'protocol':'orbit-collaboration/v1','files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    manifest['id']=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:16]
    files['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(output,'w:gz') as archive:
        for name,data in files.items():
            info=tarfile.TarInfo('orbit-knowledge/'+name);info.size=len(data);info.mode=0o644;info.mtime=0
            archive.addfile(info,io.BytesIO(data))
    print(json.dumps({'path':str(output.resolve()),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'id':manifest['id']},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);build(parser.parse_args().output)
