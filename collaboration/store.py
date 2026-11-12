"""Serialized SQLite authority for identities, mutual task pairing and knowledge."""
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid
from collaboration.protocol import identifier, text, chat_url, content, PROTOCOL


def digest(token): return hashlib.sha256(text(token,256).encode()).hexdigest()


class KnowledgeStore:
    def __init__(self,folder,join_code,clock=time.time):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.join_digest=digest(join_code);self.clock=clock;self.lock=threading.RLock()
        self.db=sqlite3.connect(self.folder/'knowledge.sqlite3',check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,name TEXT,token TEXT,seen REAL);
        CREATE TABLE IF NOT EXISTS invites(id TEXT PRIMARY KEY,sender TEXT,recipient TEXT,title TEXT,thread TEXT,state TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,title TEXT,state TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS members(project TEXT,device TEXT,thread TEXT,PRIMARY KEY(project,device));
        CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY,project TEXT,author TEXT,body TEXT,created REAL);
        ''');self.db.commit()
        from collaboration.files import ProjectFiles
        self.files=ProjectFiles(self.folder/'shared',self.db,self.clock)

    def close(self): self.db.close()

    def check(self,condition,message):
        if not condition: raise ValueError(message)

    def register(self,pkg):
        now=self.clock();device=identifier(pkg.get('device_id'));token=text(pkg.get('token'),256)
        self.check(len(token)>=40,'设备令牌过短')
        self.check(secrets.compare_digest(digest(pkg.get('join_code')),self.join_digest),'加入码无效')
        name=text(pkg.get('name'),80)
        old=self.db.execute('SELECT token FROM devices WHERE id=?',(device,)).fetchone()
        self.check(not old or secrets.compare_digest(old[0],digest(token)),'该设备标识已注册，请勿覆盖')
        self.db.execute('INSERT OR REPLACE INTO devices VALUES (?,?,?,?)',(device,name,digest(token),now))
        return {'device_id':device,'protocol':PROTOCOL}

    def authenticate(self,device,token):
        device=identifier(device)
        with self.lock:
            row=self.db.execute('SELECT token FROM devices WHERE id=?',(device,)).fetchone()
            if not row or not secrets.compare_digest(row[0],digest(token)): raise PermissionError('设备认证失败')
        return device

    def member(self,project,device,active=False):
        project=identifier(project)
        row=self.db.execute('SELECT p.state,m.thread FROM projects p JOIN members m ON m.project=p.id WHERE p.id=? AND m.device=?',(project,device)).fetchone()
        if not row: raise PermissionError('没有这个项目的访问权限')
        self.check(not active or row[0]=='active','协作已结束，知识库只读')
        return row[1]

    def state(self,device):
        now=self.clock()
        peers=[dict(id=i,name=n,online=now-s<75) for i,n,s in self.db.execute('SELECT id,name,seen FROM devices WHERE id!=? ORDER BY name',(device,))]
        invites=[dict(id=i,sender=s,recipient=r,title=t,thread_url=th,state=st,created=c)
          for i,s,r,t,th,st,c in self.db.execute("SELECT * FROM invites WHERE (sender=? OR recipient=?) AND state='pending' AND created>? ORDER BY created DESC",(device,device,now-86400))]
        projects=[]
        for i,t,s,c in self.db.execute('SELECT p.* FROM projects p JOIN members m ON p.id=m.project WHERE m.device=? ORDER BY p.created DESC',(device,)):
            members=[dict(device_id=d,name=n,thread_url=th) for d,n,th in self.db.execute('SELECT m.device,d.name,m.thread FROM members m JOIN devices d ON d.id=m.device WHERE m.project=?',(i,))]
            projects.append(dict(id=i,title=t,state=s,created=c,members=members))
        return dict(peers=peers,invites=invites,projects=projects)

    def dispatch(self,operation,device,pkg):
        if not isinstance(pkg,dict): raise ValueError('请求格式无效')
        with self.lock,self.db:
            if operation=='register': return self.register(pkg)
            if operation=='heartbeat':
                if 'name' in pkg:self.db.execute('UPDATE devices SET name=? WHERE id=?',(text(pkg['name'],80),device))
                self.db.execute('UPDATE devices SET seen=? WHERE id=?',(self.clock(),device));return self.state(device)
            if operation=='state': return self.state(device)
            if operation=='invite':
                peer=identifier(pkg.get('peer_id'));request=identifier(pkg.get('id'))
                self.check(peer!=device,'不能邀请自己')
                self.check(self.db.execute('SELECT 1 FROM devices WHERE id=?',(peer,)).fetchone(),'对方尚未连接同一知识库服务器')
                title=text(pkg.get('title'),200);thread=chat_url(pkg.get('thread_url'))
                old=self.db.execute('SELECT sender,recipient,title,thread FROM invites WHERE id=?',(request,)).fetchone()
                self.check(not old or old==(device,peer,title,thread),'请求标识已用于不同内容')
                if not old:
                    pending=self.db.execute("SELECT COUNT(*) FROM invites WHERE sender=? AND state='pending' AND created>?",(device,self.clock()-86400)).fetchone()[0]
                    self.check(pending<20,'待确认邀请过多，请先撤回旧邀请')
                    self.db.execute('INSERT INTO invites VALUES (?,?,?,?,?,?,?)',(request,device,peer,title,thread,'pending',self.clock()))
                return {'id':request}
            if operation in ('accept','reject','withdraw'):
                invite=identifier(pkg.get('id'))
                row=self.db.execute('SELECT sender,recipient,title,thread,state,created FROM invites WHERE id=?',(invite,)).fetchone()
                if not row or (row[0] if operation=='withdraw' else row[1])!=device: raise PermissionError('不能处理此邀请')
                if operation=='accept' and row[4]=='accepted':
                    self.check(self.member(invite,device)==chat_url(pkg.get('thread_url')),'已接收的聊天关联不同');return {'id':invite}
                self.check(row[4]=='pending' and self.clock()-row[5]<86400,'邀请已处理或过期')
                state={'accept':'accepted','reject':'rejected','withdraw':'withdrawn'}[operation]
                if operation=='accept':
                    thread=chat_url(pkg.get('thread_url'))
                    self.db.execute('INSERT INTO projects VALUES (?,?,?,?)',(invite,row[2],'active',self.clock()))
                    self.db.executemany('INSERT INTO members VALUES (?,?,?)',[(invite,row[0],row[3]),(invite,device,thread)])
                self.db.execute('UPDATE invites SET state=? WHERE id=?',(state,invite));return {'id':invite}
            project=identifier(pkg.get('project_id'));self.member(project,device)
            if operation=='project':return self.files.status(project,device)
            if operation.startswith('files_') or operation.startswith('lease_'):
                self.member(project,device,operation in ('files_begin','files_chunk','files_commit'))
                return self.files.dispatch(operation,project,device,pkg)
            if operation=='entries':
                self.files.check_lease(project,device,pkg)
                before=pkg.get('before',9223372036854775807)
                self.check(type(before) is int and before>0,'分页标识无效')
                rows=self.db.execute('SELECT e.id,e.author,d.name,e.body,e.created,m.thread,p.title,e.rowid FROM entries e JOIN devices d ON d.id=e.author JOIN members m ON m.project=e.project AND m.device=e.author JOIN projects p ON p.id=e.project WHERE e.project=? AND e.rowid<? ORDER BY e.rowid DESC LIMIT 21',(project,before)).fetchall()
                return {'entries':[dict(id=i,author_id=a,author_name=n,content=json.loads(b),created=c,thread_url=th,project_title=t) for i,a,n,b,c,th,t,_ in rows[:20]],'next_before':rows[19][-1] if len(rows)>20 else None}
            self.member(project,device,True)
            if operation=='leave':
                self.files.check_lease(project,device,pkg)
                self.files.release(project,device,pkg)
                self.db.execute("UPDATE projects SET state='archived' WHERE id=?",(project,));return {}
            if operation=='publish':
                self.files.check_lease(project,device,pkg)
                eid=identifier(pkg.get('id'));body=json.dumps(content(pkg.get('content')),ensure_ascii=False,sort_keys=True)
                old=self.db.execute('SELECT project,author,body FROM entries WHERE id=?',(eid,)).fetchone()
                self.check(not old or old==(project,device,body),'发送标识已用于不同内容')
                if not old:self.db.execute('INSERT INTO entries VALUES (?,?,?,?,?)',(eid,project,device,body,self.clock()))
                return {'id':eid}
            raise ValueError('未知协作操作')
