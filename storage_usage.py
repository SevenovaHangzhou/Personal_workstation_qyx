"""Read only file metadata. Never follows links, opens content or deletes files."""
import os
import stat
import time
from pathlib import Path

GROUPS=(('daily/git','Git 每日留存',True),('daily/compression','日报压缩输出',True),
        ('learning/generations','课程生成原始输出',True),('learning/tutor/runs','答疑原始输出',True),
        ('learning/courses','课程正文',False),('learning','学习状态与作答',False),
        ('daily','日报与同步账本',False),('station','工作站数据',False),('','其他本地文件',False))


def scan_usage(root, cancelled=None, now=None):
    root=Path(root);now=time.time() if now is None else now
    rows=[dict(path=path,title=title,history=history,bytes=0,files=0,older30=0,older90=0) for path,title,history in GROUPS]
    result=dict(rows=rows,skipped_links=0,errors=0,cancelled=False,scanned_at=now)
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    try:root_fd=os.open(root,flags)
    except OSError:
        result['errors']=1;return result
    stack=[()]
    try:
        while stack:
            if cancelled and cancelled():result['cancelled']=True;break
            parts=stack.pop();fd=os.dup(root_fd)
            try:
                for part in parts:
                    child_fd=os.open(part,flags,dir_fd=fd);os.close(fd);fd=child_fd
                with os.scandir(fd) as entries:
                    for entry in entries:
                        if cancelled and cancelled():result['cancelled']=True;return result
                        try:
                            info=entry.stat(follow_symlinks=False)
                            if stat.S_ISLNK(info.st_mode):result['skipped_links']+=1;continue
                            if stat.S_ISDIR(info.st_mode):stack.append((*parts,entry.name));continue
                            if not stat.S_ISREG(info.st_mode):continue
                            relative='/'.join((*parts,entry.name))
                            row=next(row for row in rows if not row['path'] or relative.startswith(row['path']+'/'))
                            row['bytes']+=info.st_size;row['files']+=1
                            if row['history']:
                                age=now-info.st_mtime
                                if age>=30*86400:row['older30']+=info.st_size
                                if age>=90*86400:row['older90']+=info.st_size
                        except OSError:result['errors']+=1
            except OSError:result['errors']+=1
            finally:os.close(fd)
    finally:os.close(root_fd)
    return result


def format_bytes(value):
    for unit in ('B','KiB','MiB','GiB','TiB'):
        if value<1024 or unit=='TiB':return f'{value:.1f} {unit}' if unit!='B' else f'{value} B'
        value/=1024
