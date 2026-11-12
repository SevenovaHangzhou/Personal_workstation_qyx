"""Independent authored knowledge bases; immutable incoming copies never overwrite notes."""
import base64
import hashlib
from pathlib import Path
import re
import shutil
import uuid
from collaboration.files import manifest, CHUNK, allowed_path
from collaboration.knowledge import safe_file
from collaboration.mesh import canonical
from collaboration.workspace import scan


def revision(files):
    return hashlib.sha256(canonical(manifest(files))).hexdigest()


class Replicas:
    def __init__(self, root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)

    def folder(self, project, author, version):
        from collaboration.protocol import identifier
        identifier(project);identifier(author)
        if not isinstance(version,str) or not re.fullmatch('[0-9a-f]{64}',version):raise ValueError('知识库版本无效')
        target=self.root/project/author/version
        for path in (self.root,self.root/project,self.root/project/author,target):
            if path.is_symlink():raise ValueError('知识库副本路径不能是符号链接')
        return target

    def commit_local(self, project, author, preview, cancel):
        current=scan(preview['root'],cancel)
        if current['files']!=preview['files']:raise ValueError('预览后知识库已变化，请重新预览并确认')
        value=dict(version=revision(current['files']),files=current['files'])
        target=self.folder(project,author,value['version'])
        if target.exists():
            if scan(target,cancel)['files']!=value['files']:raise ValueError('旧版本副本被修改，已停止共享；请保留本机修改')
            return value
        target.parent.mkdir(parents=True,exist_ok=True)
        pending=target.with_name('.pending-'+str(uuid.uuid4()));pending.mkdir()
        try:
            for row in value['files']:
                if cancel.is_set():raise InterruptedError('已停止准备知识库')
                source=safe_file(current['root'],row['path']);raw=source.read_bytes()
                if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                    raise ValueError('预览后资料已变化，请重新确认：'+row['path'])
                dest=pending/row['path'];dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
            pending.rename(target)
        except BaseException:shutil.rmtree(pending,ignore_errors=True);raise
        return value

    def read(self, project, author, current, name, offset):
        allowed_path(name)
        row=next((r for r in current['files'] if r['path']==name),None)
        if row is None or type(offset) is not int or not 0<=offset<row['bytes']:
            raise ValueError('知识库文件或读取位置无效')
        path=safe_file(self.folder(project,author,current['version']),name)
        if path.stat().st_size!=row['bytes']:
            raise ValueError('共享版本已被本机修改，请重新预览并共享')
        with path.open('rb') as stream:
            stream.seek(offset);raw=stream.read(CHUNK)
        # The receiver verifies the complete file against the signed manifest.
        return dict(data=base64.b64encode(raw).decode())

    def pull(self, project, author, value, request, cancel, progress):
        files=manifest(value['files'])
        if value['version']!=revision(files):raise ValueError('同事知识库清单校验失败')
        target=self.folder(project,author,value['version'])
        if target.exists():
            if scan(target,cancel)['files']!=files:raise ValueError('已有同事副本被本机修改，已保留；请手动另存后重试')
            return dict(root=str(target),**value)
        target.parent.mkdir(parents=True,exist_ok=True)
        pending=target.with_name('.pending-'+str(uuid.uuid4()));pending.mkdir()
        total=sum(r['bytes'] for r in files);done=0
        try:
            for row in files:
                path=pending/row['path'];path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('xb') as stream:
                    while stream.tell()<row['bytes']:
                        if cancel.is_set():raise InterruptedError('已停止同步；本机知识库和旧副本均保留')
                        result=request('kb_read',dict(project_id=project,version=value['version'],path=row['path'],offset=stream.tell()))
                        raw=base64.b64decode(result['data'],validate=True)
                        if not raw or len(raw)>min(CHUNK,row['bytes']-stream.tell()):raise ValueError('同事资料块大小无效')
                        stream.write(raw);done+=len(raw);progress(done,total,'正在同步 '+row['path'])
                if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('同事资料校验失败，未采用此副本')
            pending.rename(target)
        except BaseException:shutil.rmtree(pending,ignore_errors=True);raise
        return dict(root=str(target),version=value['version'],files=files)
