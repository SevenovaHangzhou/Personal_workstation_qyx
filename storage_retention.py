"""One-use, reviewed retention plans. Archive to system trash; never erase it."""
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import stat
import subprocess
import threading
import time
import uuid

FLAGS=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW


class Retention:
    def __init__(self,root):self.root=Path(root);self.lock=threading.Lock();self.plan=None

    def parent_fd(self,relative):
        parts=PurePosixPath(relative).parts
        if not parts or any(p in ('.','..','/') for p in parts):raise ValueError('无效留存路径')
        fd=os.open(self.root,FLAGS)
        try:
            for part in parts[:-1]:
                child=os.open(part,FLAGS,dir_fd=fd);os.close(fd);fd=child
            return fd,parts[-1]
        except Exception:os.close(fd);raise

    def signature(self,relative,cutoff):
        """Record every file and directory; links and recent writes reject the bundle."""
        records=[];total=0;stack=[relative]
        while stack:
            name=stack.pop();fd,leaf=self.parent_fd(name)
            try:
                info=os.stat(leaf,dir_fd=fd,follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or info.st_mtime>=cutoff:raise ValueError('链接或近期修改，保留')
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):raise ValueError('特殊文件，保留')
                suffix=name[len(relative):]
                records.append((suffix,info.st_dev,info.st_ino,info.st_mode,info.st_size if stat.S_ISREG(info.st_mode) else 0,info.st_mtime_ns))
                if stat.S_ISREG(info.st_mode):total+=info.st_size
                else:
                    child=os.open(leaf,FLAGS,dir_fd=fd)
                    try:
                        with os.scandir(child) as entries:stack.extend(name+'/'+entry.name for entry in entries)
                    finally:os.close(child)
            finally:os.close(fd)
        digest=hashlib.sha256(json.dumps(sorted(records)).encode()).hexdigest()
        return digest,total

    def preview(self,candidates,cancelled=lambda:False,now=None):
        now=time.time() if now is None else now;cutoff=now-30*86400;rows=[]
        for path in sorted(set(candidates)):
            if cancelled():raise ValueError('已取消留存预览')
            try:stamp,size=self.signature(path,cutoff)
            except (OSError,ValueError):continue
            rows.append(dict(path=path,bytes=size,stamp=stamp))
            if len(rows)>=200:break
        plan=dict(id=str(uuid.uuid4()),created_at=now,cutoff=cutoff,rows=rows)
        with self.lock:self.plan=plan
        return plan

    def apply(self,identifier,cancelled=lambda:False):
        if not shutil.which('gio'):raise ValueError('未找到系统回收站工具 gio，未移动文件')
        with self.lock:
            plan=self.plan
            if not plan or plan['id']!=identifier or time.time()-plan['created_at']>600:raise ValueError('确认清单已失效，请重新预览')
            self.plan=None
        for row in plan['rows']:
            if self.signature(row['path'],plan['cutoff'])[0]!=row['stamp']:raise ValueError('清单中的文件已变化，未清理；请重新预览')
        result=dict(trashed=[],errors=[],cancelled=False)
        batch='.cleanup-staging/'+uuid.uuid4().hex
        rootfd=os.open(self.root,FLAGS)
        try:
            try:os.mkdir('.cleanup-staging',0o700,dir_fd=rootfd)
            except FileExistsError:pass
            basefd=os.open('.cleanup-staging',FLAGS,dir_fd=rootfd)
            try:os.mkdir(batch.split('/')[-1],0o700,dir_fd=basefd)
            finally:os.close(basefd)
        finally:os.close(rootfd)
        stagefd=os.open(self.root/batch,FLAGS)
        try:
            for i,row in enumerate(plan['rows']):
                if cancelled():result['cancelled']=True;break
                sourcefd,leaf=self.parent_fd(row['path']);staged=str(i);moved=False
                try:
                    if self.signature(row['path'],plan['cutoff'])[0]!=row['stamp']:raise ValueError('文件已变化，停止本次清理')
                    os.rename(leaf,staged,src_dir_fd=sourcefd,dst_dir_fd=stagefd);moved=True
                    if self.signature(batch+'/'+staged,plan['cutoff'])[0]!=row['stamp']:raise ValueError('移动期间内容变化，恢复原位置')
                    command=subprocess.run(['gio','trash','--',str(self.root/batch/staged)],capture_output=True,timeout=20)
                    if command.returncode:raise OSError('系统回收站操作失败')
                    moved=False;result['trashed'].append(row['path'])
                except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
                    if moved:
                        try:
                            try:os.stat(leaf,dir_fd=sourcefd,follow_symlinks=False)
                            except FileNotFoundError:os.rename(staged,leaf,src_dir_fd=stagefd,dst_dir_fd=sourcefd)
                            else:raise OSError('原路径已被重新创建')
                        except OSError:result['errors'].append('文件保留在 '+batch+'/'+staged)
                    result['errors'].append(row['path']+'：'+str(exc));break
                finally:os.close(sourcefd)
        finally:
            os.close(stagefd)
            try:(self.root/batch).rmdir()
            except OSError:pass
        return result
