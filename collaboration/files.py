"""Whole-project leases, fenced writeback and immutable directory revisions."""
import base64
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import time
import uuid
from collaboration.protocol import identifier,text

MAX_PROJECT=100*1024*1024
MAX_FILE=10*1024*1024
CHUNK=256*1024
LEASE_SECONDS=90
SKIP_DIRS={'.git','.venv','venv','node_modules','__pycache__','.credentials','.idea','.vscode','build','dist'}
SKIP_FILES={'auth.json','credentials.json'}


def allowed_path(value):
    value=text(value,400)
    path=PurePosixPath(value)
    if path.is_absolute() or str(path)!=value or '\\' in value or any(p in ('','..','.') for p in path.parts):raise ValueError('共享路径无效')
    for part in path.parts:
        if any(ord(c)<32 or c in '<>:"|?*' for c in part) or part.endswith(('.',' ')):raise ValueError('路径不兼容 Windows')
        if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part):raise ValueError('路径为 Windows 保留名称')
        if part.lower() in SKIP_DIRS or part.lower() in SKIP_FILES or part.lower().endswith(('.pem','.key','.pyc')) or part.lower().startswith('.env') and part.lower()!='.env.example':raise ValueError('凭据、缓存或构建目录不参与共享')
    return value


def manifest(value):
    if not isinstance(value,list) or len(value)>3000:raise ValueError('项目文件清单无效或超过 3000 个文件')
    total=0;paths=set();result=[]
    for row in value:
        if not isinstance(row,dict) or set(row)!={'path','bytes','sha256'}:raise ValueError('文件信息无效')
        path=allowed_path(row['path']);size=row['bytes'];digest=row['sha256']
        if type(size) is not int or not 0<=size<=MAX_FILE or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('文件大小或校验值无效')
        folded=path.casefold()
        if folded in paths:raise ValueError('文件路径重复或大小写冲突')
        paths.add(folded);total+=size;result.append(dict(path=path,bytes=size,sha256=digest))
    if len(json.dumps(result,ensure_ascii=False).encode())>400000:raise ValueError('文件清单过大，请缩小共享目录范围')
    if total>MAX_PROJECT:raise ValueError('项目超过 100 MiB，请缩小共享范围')
    for path in paths:
        if any(str(parent) in paths for parent in PurePosixPath(path).parents if str(parent)!='.'):raise ValueError('文件与目录路径冲突')
    return sorted(result,key=lambda r:r['path'])


class ProjectFiles:
    def __init__(self,root,db,clock=time.time):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.db=db;self.clock=clock
        db.executescript('''
        CREATE TABLE IF NOT EXISTS leases(project TEXT PRIMARY KEY,owner TEXT,id TEXT,expires REAL);
        CREATE TABLE IF NOT EXISTS revisions(project TEXT,version INTEGER,id TEXT,manifest TEXT,owner TEXT,created REAL,PRIMARY KEY(project,version));
        CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,project TEXT,owner TEXT,lease TEXT,base INTEGER,manifest TEXT,state TEXT,result TEXT);
        ''');db.commit()
        for project,version,revision in db.execute('SELECT r.project,r.version,r.id FROM revisions r WHERE r.version=(SELECT MAX(v.version) FROM revisions v WHERE v.project=r.project)'):
            self.point(project,revision)

    def current(self,project):
        row=self.db.execute('SELECT version,id,manifest FROM revisions WHERE project=? ORDER BY version DESC LIMIT 1',(project,)).fetchone()
        return dict(version=row[0],id=row[1],files=json.loads(row[2])) if row else dict(version=0,id=None,files=[])

    def status(self,project,device):
        row=self.db.execute('SELECT l.owner,d.name,l.id,l.expires FROM leases l JOIN devices d ON l.owner=d.id WHERE l.project=?',(project,)).fetchone()
        lease=None
        if row and row[3]>self.clock():lease=dict(owner=row[0],name=row[1],mine=row[0]==device,expires=row[3],id=row[2] if row[0]==device else None)
        current=self.current(project)
        return dict(lease=lease,version=current['version'],files=current['files'] if lease and lease['mine'] else [],limits=dict(max_project=MAX_PROJECT,max_file=MAX_FILE))

    def check_lease(self,project,device,pkg):
        lease=identifier(pkg.get('lease_id'))
        row=self.db.execute('SELECT owner,id,expires FROM leases WHERE project=?',(project,)).fetchone()
        if not row or row[0]!=device or row[1]!=lease or row[2]<=self.clock():raise ValueError('项目锁已失效或由同事占用；未写回，请保留本机修改后重新取得锁')
        return row

    def release(self,project,device,pkg):
        row=self.db.execute('SELECT owner,id FROM leases WHERE project=?',(project,)).fetchone()
        if row and row==(device,pkg.get('lease_id')):self.db.execute('UPDATE leases SET expires=0 WHERE project=?',(project,))
        elif row:raise ValueError('不能释放同事或新一轮项目锁')
        return {}

    def dispatch(self,operation,project,device,pkg):
        if operation=='lease_acquire':
            request=identifier(pkg.get('id'));row=self.db.execute('SELECT owner,id,expires FROM leases WHERE project=?',(project,)).fetchone()
            if row and row[2]>self.clock() and (row[0]!=device or row[1]!=request):raise ValueError('项目正在使用中，请等待持有人释放')
            # An expired request id cannot be revived and fence an old process back in.
            if row and row[2]<=self.clock() and row[1]==request:raise ValueError('旧锁已到期，请重新申请新锁')
            self.db.execute('INSERT OR REPLACE INTO leases VALUES (?,?,?,?)',(project,device,request,self.clock()+LEASE_SECONDS));return self.status(project,device)
        if operation=='lease_release':return self.release(project,device,pkg)
        if operation=='files_status':
            row=self.upload(pkg.get('id'),project,device);return dict(state=row['state'],result=json.loads(row['result']))
        self.check_lease(project,device,pkg)
        if operation=='lease_renew':
            self.db.execute('UPDATE leases SET expires=? WHERE project=?',(self.clock()+LEASE_SECONDS,project));return self.status(project,device)
        current=self.current(project)
        if operation=='files_manifest':return current
        if operation=='files_read':
            if pkg.get('version')!=current['version']:raise ValueError('共享版本已变化，请重新拉取')
            name=allowed_path(pkg.get('path'));row=next((r for r in current['files'] if r['path']==name),None)
            if not row:raise ValueError('文件不在共享清单中')
            offset=pkg.get('offset');self.integer(offset,0,row['bytes'])
            path=self.root/project/'revisions'/current['id']/name
            with path.open('rb') as stream:stream.seek(offset);raw=stream.read(CHUNK)
            return dict(data=base64.b64encode(raw).decode(),offset=offset)
        if operation=='files_begin':
            upload=identifier(pkg.get('id'));rows=manifest(pkg.get('files'));base=pkg.get('base_version');encoded=json.dumps(rows,ensure_ascii=False,sort_keys=True)
            old=self.db.execute('SELECT project,owner,lease,base,manifest FROM uploads WHERE id=?',(upload,)).fetchone()
            expected=(project,device,pkg['lease_id'],base,encoded)
            if old:
                if old!=expected:raise ValueError('上传标识已用于不同内容')
                return {'id':upload}
            if type(base) is not int or base!=current['version']:raise ValueError('服务器项目版本已变化，不能覆盖；请先核对新版本')
            pending=self.db.execute("SELECT COUNT(*) FROM uploads WHERE project=? AND state='staging'",(project,)).fetchone()[0]
            if pending>=10:raise ValueError('未完成上传过多，请管理员核对后整理，不自动删除任何数据')
            target=self.root/project/'staging'/upload;target.mkdir(parents=True,exist_ok=False,mode=0o700)
            oldfiles={r['path']:r for r in current['files']}
            for row in rows:
                dest=target/row['path'];dest.parent.mkdir(parents=True,exist_ok=True)
                oldfile=oldfiles.get(row['path'])
                if oldfile==row:shutil.copyfile(self.root/project/'revisions'/current['id']/row['path'],dest)
                elif row['bytes']==0:dest.write_bytes(b'')
            self.db.execute('INSERT INTO uploads VALUES (?,?,?,?,?,?,?,?)',(upload,project,device,pkg['lease_id'],base,encoded,'staging','{}'))
            return {'id':upload,'needed':[r['path'] for r in rows if oldfiles.get(r['path'])!=r]}
        if operation in ('files_chunk','files_commit'):
            upload=self.upload(pkg.get('id'),project,device)
            if upload['lease']!=pkg['lease_id']:raise ValueError('上传不属于当前项目锁，请重新开始写回')
            if upload['state']=='done':return json.loads(upload['result'])
            rows=json.loads(upload['manifest']);target=self.root/project/'staging'/pkg['id']
            if operation=='files_chunk':
                name=allowed_path(pkg.get('path'));row=next((r for r in rows if r['path']==name),None)
                if not row:raise ValueError('上传文件不在预览清单中')
                offset=pkg.get('offset');self.integer(offset,0,row['bytes'])
                try:raw=base64.b64decode(pkg.get('data'),validate=True)
                except (ValueError,TypeError):raise ValueError('文件块编码无效') from None
                if not raw or len(raw)>CHUNK or offset+len(raw)>row['bytes']:raise ValueError('文件块长度无效')
                path=target/name;path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('r+b' if path.exists() else 'wb') as stream:stream.seek(offset);stream.write(raw)
                return {}
            if current['version']!=upload['base']:raise ValueError('服务器版本已变化，未覆盖；请保留本机修改')
            if not target.exists():
                candidate=self.root/project/'revisions'/pkg['id']
                if candidate.is_dir():target=candidate
            for row in rows:
                path=target/row['path']
                if not path.is_file() or path.stat().st_size!=row['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('上传尚不完整或校验未通过，公共目录保持原样')
                with path.open('rb') as stream:os.fsync(stream.fileno())
            # A lease may expire during hashing; recheck before publication.
            self.check_lease(project,device,pkg)
            revision=self.root/project/'revisions'/pkg['id'];revision.parent.mkdir(exist_ok=True)
            if target!=revision and target.exists():target.rename(revision)
            directory=os.open(revision.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
            version=current['version']+1;result=dict(version=version,id=pkg['id'])
            self.db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?)',(project,version,pkg['id'],upload['manifest'],device,self.clock()))
            self.db.execute("UPDATE uploads SET state='done',result=? WHERE id=?",(json.dumps(result),pkg['id']))
            # DB is authoritative. The projection is repaired at startup if a crash
            # occurs between the transaction and pointer replacement.
            self.db.commit();self.point(project,pkg['id']);return result
        raise ValueError('未知项目目录操作')

    def point(self,project,revision):
        root=self.root/project;root.mkdir(parents=True,exist_ok=True)
        link=root/'current.next';link.unlink(missing_ok=True);link.symlink_to(Path('revisions')/revision);link.replace(root/'current')

    def upload(self,value,project,device):
        key=identifier(value);row=self.db.execute('SELECT owner,lease,base,manifest,state,result FROM uploads WHERE id=? AND project=?',(key,project)).fetchone()
        if not row or row[0]!=device:raise ValueError('上传记录不存在或不属于本机')
        return dict(zip(('owner','lease','base','manifest','state','result'),row))

    @staticmethod
    def integer(value,minimum,maximum):
        if type(value) is not int or not minimum<=value<=maximum:raise ValueError('偏移或长度无效')
