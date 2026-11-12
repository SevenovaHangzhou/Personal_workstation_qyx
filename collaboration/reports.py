"""Read-only report folder projection. Never expose the surrounding daily database."""
import base64
import hashlib
from pathlib import Path
import re
import threading
from collaboration.files import CHUNK,MAX_FILE,manifest
from collaboration.knowledge import safe_file
from collaboration.replica import revision

REPORT_SCOPE='a386d576-c9d1-4b7e-9701-c8ec2f9d1393'
DAY=r'\d{4}-\d{2}-\d{2}'
LOG=r'(today|week)-\d{8}-\d{6}-[0-9a-f]{8}'


def report_path(source):
    if re.fullmatch(DAY+r'\.md',source):return '日报/自动/'+source
    match=re.fullmatch(r'work-logs/('+LOG+r')\.md',source)
    if match:return ('周报/' if match[2]=='week' else '日报/手动/')+source.split('/')[-1]
    raise ValueError('只共享已生成的日报和周报 Markdown 文件')


def report_manifest(value):
    rows=manifest(value['files'])
    for row in rows:
        name=row['path'];source=name.rsplit('/',1)[-1]
        if not name.startswith('日报/自动/'):source='work-logs/'+source
        if report_path(source)!=name:raise ValueError('共享清单含非报告文件')
    if revision(rows)!=value['version']:raise ValueError('报告文件清单校验失败')
    return dict(version=value['version'],files=rows)


class ReportFolder:
    def __init__(self,root,inventory):
        self.root=Path(root);self.inventory=inventory;self.lock=threading.RLock();self.current=None;self.sources={}

    def refresh(self,cancel):
        files=[];sources={}
        for source in self.inventory():
            if cancel.is_set():raise InterruptedError('已停止报告共享')
            name=report_path(source);path=safe_file(self.root,source)
            if not path.exists():continue  # Retention may have removed a Markdown projection.
            if not path.is_file() or path.stat().st_size>MAX_FILE:raise ValueError('报告文件无效或超过 10 MiB')
            raw=path.read_bytes()
            if len(raw)>MAX_FILE:raise ValueError('报告文件超过 10 MiB')
            files.append(dict(path=name,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()));sources[name]=source
        value=dict(files=manifest(files));value['version']=revision(value['files'])
        with self.lock:self.current=value;self.sources=sources
        return value

    def read(self,version,name,offset):
        with self.lock:
            value=self.current;source=self.sources.get(name)
            if not value or version!=value['version'] or not source:raise ValueError('报告版本已变化，请重新收集')
            row=next((r for r in value['files'] if r['path']==name),None)
        if type(offset) is not int or not 0<=offset<row['bytes']:raise ValueError('报告读取位置无效')
        if source not in self.inventory():raise ValueError('报告尚未结束或已停止共享，保留上次收集版本')
        path=safe_file(self.root,source)
        if path.stat().st_size!=row['bytes']:raise ValueError('报告已更新，请稍后重新收集')
        with path.open('rb') as stream:stream.seek(offset);raw=stream.read(CHUNK)
        return dict(data=base64.b64encode(raw).decode())
