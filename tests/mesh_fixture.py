"""Two isolated Yomk peers with real pinned TLS; no external host or real chat is used."""
import copy
import tempfile
import time
from pathlib import Path
from framework import api
from runtime import MomoRuntime
from model import make_task,parse_binding
from services.CollaborationService import CollaborationService
from collaboration.mesh import pairing_code
from clients import call

class FakeDiscovery:
    def __init__(self,*args):self.peers={};self.error='';self.stopped=False
    def start(self):pass
    def stop(self):self.stopped=True
    def snapshot(self):return copy.deepcopy(self.peers)

class PeerTasks:
    def __init__(self):
        self.task=make_task('同事的本机任务','demo');self.task['bindings']=[parse_binding('codex','codex://threads/peer-chat')]
    def get(self,identifier):return copy.deepcopy(self.task) if identifier==self.task['id'] else None

class MeshFixture:
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.runtime=MomoRuntime(self.root/'a/tasks.json',auto_tick=False,profile='work').start()
        self.runtime.tasks.clear_samples();self.task=make_task('本机机械臂开发','demo');self.task['bindings']=[parse_binding('codex','codex://threads/own-chat')];self.runtime.tasks.add(self.task)
        self.a=self.runtime.collaboration_service;self.a.discovery_factory=FakeDiscovery
        self.a.knowledge_generator=self.fake_knowledge;self.a.chat_catalog=self.fake_chats
        self.peer_tasks=PeerTasks();self.b=CollaborationService(api.server(),self.root/'b',self.peer_tasks,discovery=FakeDiscovery,service_name='/PeerB',listen_port=0,chat_catalog=self.fake_chats,knowledge_generator=self.fake_knowledge)
        assert api.add_service(self.b,'/PeerB')==0;self.b.start()
    def fake_chats(self,query,archived,cancel):
        rows=[dict(id='own-chat',title='本机机械臂聊天',preview='编码器与计划',cwd='/tmp/own'),dict(id='peer-chat',title='同事机械臂聊天',preview='力矩误差',cwd='/tmp/peer')]
        return [r for r in rows if not query or query in r['title']]
    def fake_knowledge(self,folder,url,cancel,progress):
        progress(70,'正在整理合成测试历史')
        return dict(text='# 合成测试知识库\n\n## 背景\n从 '+url+' 的历史得出\n\n## 当前进展\n已核对合成资料',source=dict(thread_url=url,messages=12))
    def share_knowledge(self,peer,project):
        self.until(lambda:self.state(peer)['knowledge_jobs'][project]['status']=='ready')
        row=self.state(peer)['knowledge_jobs'][project]
        self.invoke(peer,'knowledge_apply',dict(project_id=project,id=row['id'],expected=row['base_sha256'],confirmed=True))
        self.invoke(peer,'sync',dict(project_id=project,operation='prepare'))
        self.until(lambda:self.state(peer)['sync']['status']=='prepared')
        self.invoke(peer,'sync',dict(project_id=project,operation='upload',confirmed=True,preview_id=self.state(peer)['sync']['preview']['id']))
        self.until(lambda:self.state(peer)['sync']['status']=='done')
    def tearDown(self):
        self.b.stop();api.del_service('/PeerB');self.runtime.close();self.temp.cleanup()
    def invoke(self,peer,name,pkg=None):return call(peer.service_name+'/'+name,pkg or {})
    def state(self,peer):return self.invoke(peer,'snapshot')
    def until(self,predicate,timeout=6):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if predicate():return
            time.sleep(.025)
        self.assertTrue(predicate())
    def action(self,peer,name,payload):return self.invoke(peer,'action',dict(operation=name,payload=payload,confirmed=True))
    def connect(self):
        for service,name in ((self.a,'同事甲'),(self.b,'同事乙')):
            # Port zero is fixture-only; production configuration validates explicit ports.
            with service.lock:service.state['config'].update(enabled=True,name=name,port=0);service.persist();service.wake.set()
        self.until(lambda:self.a.listener is not None and self.b.listener is not None and self.a.discovery and self.b.discovery)
        for local,remote,name in ((self.a,self.b,'同事乙'),(self.b,self.a,'同事甲')):
            with local.lock:
                local.discovery.peers[remote.identity.id]=dict(id=remote.identity.id,name=name,address='127.0.0.1',port=remote.listener.port,fingerprint=remote.identity.pin)
                local.refresh_peers();local.persist()
    def pair_devices(self):
        self.connect();self.action(self.a,'pair_request',dict(peer_id=self.b.identity.id))
        self.until(lambda:bool(self.state(self.b)['pairs']))
        pair_id=next(iter(self.state(self.a)['pairs']))
        for peer,remote in ((self.a,self.b),(self.b,self.a)):
            self.action(peer,'pair_confirm',dict(id=pair_id,code=pairing_code(pair_id,peer.identity.pin,remote.identity.pin)))
        self.until(lambda:self.a.paired(self.b.identity.id) and self.b.paired(self.a.identity.id));return pair_id
    def project(self):
        self.pair_devices();self.action(self.a,'invite',dict(peer_id=self.b.identity.id,task_id=self.task['id'],thread_url='codex://threads/own-chat',title='一起排查力矩误差'))
        self.until(lambda:bool(self.state(self.b)['invites']));project=next(iter(self.state(self.b)['invites']))
        self.action(self.b,'accept',dict(id=project,task_id=self.peer_tasks.task['id'],thread_url='codex://threads/peer-chat'))
        self.until(lambda:project in self.state(self.a)['projects'])
        for peer in (self.a,self.b):self.share_knowledge(peer,project);self.invoke(peer,'refresh')
        self.until(lambda:project in self.state(self.a)['replicas'] and project in self.state(self.b)['replicas'])
        return project
