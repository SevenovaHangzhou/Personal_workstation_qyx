"""Preview-selected project snapshots; download into fresh folders, never overwrite."""
import base64
import hashlib
import os
from pathlib import Path
import time
import uuid
from collaboration.files import allowed_path,manifest,CHUNK,MAX_PROJECT


def scan(root,cancel):
    root=Path(root).resolve()
    if not root.is_dir():raise ValueError('请选择存在的项目目录')
    files=[];excluded=[];total=0
    for folder,directories,names in os.walk(root,followlinks=False):
        if cancel.is_set():raise InterruptedError('已停止准备')
        base=Path(folder)
        for name in directories[:]:
            path=base/name;relative=path.relative_to(root).as_posix()
            try:allowed_path(relative)
            except ValueError:directories.remove(name);excluded.append(relative+'/');continue
            if path.is_symlink():raise ValueError('项目含符号链接，请先明确实际共享范围：'+relative)
        for name in names:
            path=base/name;relative=path.relative_to(root).as_posix()
            try:allowed_path(relative)
            except ValueError:excluded.append(relative);continue
            if path.is_symlink() or not path.is_file():raise ValueError('不共享符号链接或特殊文件：'+relative)
            if path.stat().st_size>10*1024*1024:raise ValueError('文件超过 10 MiB：'+relative)
            data=path.read_bytes();total+=len(data)
            if len(files)>=3000 or total>MAX_PROJECT:raise ValueError('项目超过 100 MiB 或 3000 个文件，请缩小共享范围')
            files.append(dict(path=relative,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
    return dict(root=str(root),files=manifest(files),excluded=excluded)


def difference(files,previous):
    old={r['path']:r for r in previous};new={r['path']:r for r in files}
    return dict(added=sorted(set(new)-set(old)),changed=sorted(p for p in new.keys()&old.keys() if new[p]!=old[p]),removed=sorted(set(old)-set(new)))


def transfer(transport,project,lease,operation,folder,cancel,progress,preview=None):
    def request(name,**values):
        if cancel.is_set():raise InterruptedError('已停止目录同步；已保存版本不受影响')
        return transport.request(name,dict(project_id=project,lease_id=lease,**values))
    last_renew=time.monotonic()
    def renew():
        nonlocal last_renew
        if time.monotonic()-last_renew>15:request('lease_renew');last_renew=time.monotonic()
    current=request('files_manifest')
    if operation=='download':
        # Every pull uses a fresh folder, including failed partial downloads.
        target=Path(folder)/'workspaces'/project/str(uuid.uuid4());target.mkdir(parents=True,exist_ok=False)
        total=sum(r['bytes'] for r in current['files']);done=0
        for row in manifest(current['files']):
            path=target/row['path'];path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('wb') as stream:
                while stream.tell()<row['bytes']:
                    renew();result=request('files_read',version=current['version'],path=row['path'],offset=stream.tell())
                    data=base64.b64decode(result['data'],validate=True)
                    if not data or len(data)>min(CHUNK,row['bytes']-stream.tell()):raise ValueError('服务器文件块长度无效')
                    stream.write(data);done+=len(data);progress(done,total,'正在拉取 '+row['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('下载文件校验失败，未采用此副本')
        request('lease_renew')
        return dict(root=str(target),version=current['version'],files=current['files'])
    if not preview:raise ValueError('请先准备并预览写回清单')
    local=scan(preview['root'],cancel)
    if local['files']!=preview['files']:raise ValueError('预览后本机文件已变化，请重新准备并确认')
    upload=preview['upload_id'];rows=local['files'];root=Path(local['root'])
    try:
        status=request('files_status',id=upload)
        if status['state']=='done':return dict(root=str(root),version=status['result']['version'],files=rows)
    except ValueError as exc:
        if '上传记录不存在' not in str(exc):raise
    if preview['base_version']!=current['version']:raise ValueError('共享版本已变化，请重新拉取并核对，不会覆盖')
    result=request('files_begin',id=upload,base_version=preview['base_version'],files=rows)
    needed=set(result.get('needed',[r['path'] for r in rows]));total=sum(r['bytes'] for r in rows if r['path'] in needed);done=0
    for row in rows:
        if row['path'] not in needed:continue
        path=root/row['path']
        if path.is_symlink() or not path.resolve().is_relative_to(root):raise ValueError('预览后文件路径发生变化，已停止')
        data=path.read_bytes()
        if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('预览后文件内容发生变化，请重新确认')
        for offset in range(0,len(data),CHUNK):
            renew();chunk=data[offset:offset+CHUNK];request('files_chunk',id=upload,path=row['path'],offset=offset,data=base64.b64encode(chunk).decode())
            done+=len(chunk);progress(done,total,'正在写回 '+row['path'])
    result=request('files_commit',id=upload)
    return dict(root=str(root),version=result['version'],files=rows)
