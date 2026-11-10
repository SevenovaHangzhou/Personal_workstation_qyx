"""Windows metadata scans and reviewed Recycle Bin moves without following links."""
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import stat
import time
import uuid
from storage_usage import GROUPS


def checked(root,relative=''):
    root=Path(root).absolute()
    parts=PurePosixPath(relative).parts
    if any(part in ('.','..','/') or '\\' in part or ':' in part for part in parts):raise ValueError('无效留存路径')
    path=root
    for component in [None,*parts]:
        if component:path=path/component
        info=path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('链接或重解析点，保留')
    return path


def scan_usage(root,cancelled=None,now=None):
    now=time.time() if now is None else now
    rows=[dict(path=p,title=t,history=h,bytes=0,files=0,older30=0,older90=0) for p,t,h in GROUPS]
    result=dict(rows=rows,skipped_links=0,errors=0,cancelled=False,scanned_at=now);stack=['']
    while stack:
        if cancelled and cancelled():result['cancelled']=True;break
        name=stack.pop()
        try:
            path=checked(root,name)
            with os.scandir(path) as entries:
                for entry in entries:
                    info=entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:result['skipped_links']+=1;continue
                    relative=(name+'/' if name else '')+entry.name
                    if stat.S_ISDIR(info.st_mode):stack.append(relative);continue
                    if not stat.S_ISREG(info.st_mode):continue
                    row=next(r for r in rows if not r['path'] or relative.startswith(r['path']+'/'))
                    row['bytes']+=info.st_size;row['files']+=1
                    if row['history']:
                        if now-info.st_mtime>=30*86400:row['older30']+=info.st_size
                        if now-info.st_mtime>=90*86400:row['older90']+=info.st_size
        except ValueError:result['skipped_links']+=1
        except OSError:result['errors']+=1
    return result


class WindowsRetention:
    def signature(self,relative,cutoff):
        records=[];size=0;stack=[relative]
        while stack:
            name=stack.pop();path=checked(self.root,name);info=path.lstat()
            if info.st_mtime>=cutoff:raise ValueError('近期修改，保留')
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):raise ValueError('特殊文件，保留')
            records.append((name[len(relative):],info.st_dev,info.st_ino,info.st_mode,info.st_size if path.is_file() else 0,info.st_mtime_ns))
            if path.is_file():size+=info.st_size
            else:
                with os.scandir(path) as entries:stack.extend(name+'/'+entry.name for entry in entries)
        return hashlib.sha256(json.dumps(sorted(records)).encode()).hexdigest(),size

    def apply(self,identifier,cancelled=lambda:False):
        from send2trash import send2trash
        with self.lock:
            plan=self.plan
            if not plan or plan['id']!=identifier or time.time()-plan['created_at']>600:raise ValueError('确认清单已失效，请重新预览')
            self.plan=None
        for row in plan['rows']:
            if self.signature(row['path'],plan['cutoff'])[0]!=row['stamp']:raise ValueError('文件已变化，未清理')
        result=dict(trashed=[],errors=[],cancelled=False)
        batch=self.root/'.cleanup-staging'/uuid.uuid4().hex;batch.mkdir(parents=True)
        checked(self.root,str(batch.relative_to(self.root)).replace('\\','/'))
        for i,row in enumerate(plan['rows']):
            if cancelled():result['cancelled']=True;break
            source=checked(self.root,row['path']);target=batch/str(i);moved=False
            try:
                if self.signature(row['path'],plan['cutoff'])[0]!=row['stamp']:raise ValueError('文件已变化，停止清理')
                source.rename(target);moved=True
                if self.signature(target.relative_to(self.root).as_posix(),plan['cutoff'])[0]!=row['stamp']:raise ValueError('移动期间内容变化，恢复原位置')
                send2trash(str(target));moved=False;result['trashed'].append(row['path'])
            except (OSError,ValueError) as exc:
                if moved:
                    try:
                        if source.exists():raise OSError('原路径已重新创建')
                        target.rename(source)
                    except OSError:result['errors'].append('文件保留在 '+str(target))
                result['errors'].append(row['path']+'：'+str(exc));break
        try:batch.rmdir()
        except OSError:pass
        return result
