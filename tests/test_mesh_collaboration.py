import hashlib
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import uuid
from collaboration.mesh import Identity,PeerTransport,MeshDiscovery,verify,pairing_code
from collaboration.protocol import FIELDS
from collaboration.knowledge import NOTE_FILE
from mesh_fixture import MeshFixture

class MeshTests(MeshFixture,unittest.TestCase):
    def test_network_off_and_old_server_state_is_preserved(self):
        self.assertFalse(self.state(self.a)['config']['enabled']);self.assertIsNone(self.a.listener);self.assertIsNone(self.a.discovery)
        self.assertNotIn('url',self.state(self.a)['config'])
        from services.CollaborationService import CollaborationService
        from framework import api
        legacy=self.root/'legacy';legacy.mkdir();old=b'{"config":{"enabled":true,"url":"https://192.168.100.30:18765"},"links":{}}';(legacy/'state.json').write_bytes(old)
        restored=CollaborationService(api.server(),legacy,self.peer_tasks)
        self.assertTrue(restored.legacy_available);self.assertFalse(restored.state['config']['enabled']);self.assertEqual((legacy/'state.json').read_bytes(),old)
        with self.assertRaisesRegex(ValueError,'启用'):self.action(self.a,'pair_request',{'peer_id':self.b.identity.id})
    def test_mutual_pairing_is_required_before_any_task_exchange(self):
        self.connect()
        with self.assertRaisesRegex(ValueError,'尚未确认配对'):self.b.receive(dict(envelope=self.a.identity.envelope(self.b.identity.id,'poll',{'cursors':{}}),address='127.0.0.1'))
        self.action(self.a,'pair_request',dict(peer_id=self.b.identity.id));self.until(lambda:bool(self.state(self.b)['pairs']))
        pair=next(iter(self.state(self.a)['pairs']));code=pairing_code(pair,self.a.identity.pin,self.b.identity.pin)
        self.action(self.a,'pair_confirm',dict(id=pair,code=code))
        with self.assertRaisesRegex(ValueError,'双方核对'):self.action(self.a,'invite',dict(peer_id=self.b.identity.id))
        with self.assertRaisesRegex(ValueError,'配对核对码'):self.action(self.b,'pair_confirm',dict(id=pair,code='wrong'))
        self.assertFalse(self.a.paired(self.b.identity.id));self.assertFalse(self.b.paired(self.a.identity.id))
    def test_tls_pin_checked_before_sending_and_signed_body_cannot_change(self):
        self.connect();peer=self.state(self.a)['peers'][self.b.identity.id];peer['fingerprint']='0'*64
        with self.assertRaisesRegex(ValueError,'指纹已变化'):PeerTransport(self.a.identity,peer).request('pair_request',{'id':str(uuid.uuid4())})
        self.assertFalse(self.state(self.b)['pairs'])
        envelope=self.a.identity.envelope(self.b.identity.id,'poll',{'cursors':{}});envelope['message']['payload']['steal']='changed'
        with self.assertRaisesRegex(ValueError,'签名无效'):verify(envelope,self.b.identity.id)
    def test_confirmed_collaboration_creates_and_syncs_two_independent_knowledge_bases(self):
        project=self.project();a=self.state(self.a);b=self.state(self.b)
        own=Path(a['workspaces'][project]['root']);other=Path(a['replicas'][project]['root'])
        self.assertNotEqual(own,other);self.assertIn('## 背景',(own/NOTE_FILE).read_text());self.assertIn('## 当前进展',(other/NOTE_FILE).read_text())
        self.assertEqual(len(list(own.iterdir())),1);self.assertNotIn('leases',a)
        (own/'资料.bin').write_bytes(b'abc123'*100000)
        original=(own/NOTE_FILE).read_text();self.invoke(self.a,'save_note',dict(project_id=project,text='我的思路 A',expected=hashlib.sha256(original.encode()).hexdigest()))
        self.invoke(self.b,'save_note',dict(project_id=project,text='同事思路 B',expected=hashlib.sha256((Path(b['workspaces'][project]['root'])/NOTE_FILE).read_bytes()).hexdigest()))
        # Edits stay private until explicit preview/confirm.
        self.invoke(self.a,'refresh');self.invoke(self.b,'refresh')
        self.assertNotEqual((Path(self.state(self.b)['replicas'][project]['root'])/NOTE_FILE).read_text(),'我的思路 A')
        self.invoke(self.a,'sync',dict(project_id=project,operation='prepare'));self.until(lambda:self.state(self.a)['sync']['status']=='prepared')
        preview=self.state(self.a)['sync']['preview']
        with self.assertRaisesRegex(ValueError,'确认'):self.invoke(self.a,'sync',dict(project_id=project,operation='upload',preview_id=preview['id']))
        self.invoke(self.a,'sync',dict(project_id=project,operation='upload',confirmed=True,preview_id=preview['id']));self.until(lambda:self.state(self.a)['sync']['status']=='done')
        self.invoke(self.b,'refresh');self.until(lambda:self.state(self.b)['replicas'][project]['version']==self.state(self.a)['published'][project]['version'])
        new=Path(self.state(self.b)['replicas'][project]['root'])
        self.assertEqual((new/'资料.bin').read_bytes(),b'abc123'*100000)
        self.assertEqual((new/NOTE_FILE).read_text(),'我的思路 A');self.assertEqual((Path(self.state(self.b)['workspaces'][project]['root'])/NOTE_FILE).read_text(),'同事思路 B')
        self.assertTrue(other.exists());self.assertIn('同事知识库/'+NOTE_FILE,self.a.knowledge_baseline(project)['documents'])
    def test_preview_change_external_edit_and_credential_paths_are_guarded(self):
        project=self.project();root=Path(self.state(self.a)['workspaces'][project]['root']);note=root/NOTE_FILE;old=note.read_bytes();note.write_text('Codex changed externally')
        with self.assertRaisesRegex(ValueError,'已被 Codex'):self.invoke(self.a,'save_note',dict(project_id=project,text='stale editor',expected=hashlib.sha256(old).hexdigest()))
        (root/'.env').write_text('PRIVATE_SECRET');(root/'diagram.txt').write_text('knowledge')
        self.invoke(self.a,'sync',dict(project_id=project,operation='prepare'));self.until(lambda:self.state(self.a)['sync']['status']=='prepared');preview=self.state(self.a)['sync']['preview']
        self.assertIn('.env',preview['excluded']);self.assertNotIn('.env',[r['path'] for r in preview['files']]);note.write_text('changed after preview')
        self.invoke(self.a,'sync',dict(project_id=project,operation='upload',confirmed=True,preview_id=preview['id']));self.until(lambda:self.state(self.a)['sync']['status']=='failed')
        self.assertIn('预览后',self.state(self.a)['sync']['message'])
    def test_discussion_is_durable_deduplicated_and_never_reads_raw_chat(self):
        project=self.project()
        with patch('collaboration.summary.selected_messages',side_effect=AssertionError('raw chat must not be read')):
            self.action(self.a,'message',dict(project_id=project,text='先核对编码器方向，再讨论计划。'))
            self.until(lambda:len(self.state(self.b)['entries'].get(project,[]))==1)
        action=next(a for a in self.state(self.a)['actions'] if a['operation']=='message')
        self.a.request(self.b.identity.id,'message',action['payload']);self.assertEqual(len(self.state(self.b)['entries'][project]),1)
        self.invoke(self.a,'refresh');self.invoke(self.b,'refresh');self.until(lambda:self.state(self.a)['cursors'].get(project)==0 and self.state(self.b)['cursors'].get(project)==1)
        self.assertEqual(len(self.state(self.b)['entries'][project]),1)
        saved=json.loads(self.b.path.read_text());self.assertEqual(saved['entries'][project][0]['body'],'先核对编码器方向，再讨论计划。')
    def test_background_summary_draft_requires_explicit_send_but_not_a_task(self):
        project=self.project();started=threading.Event();release=threading.Event()
        def generate(*args):started.set();release.wait(2);return dict(content={k:('仅测试摘要' if k=='summary' else '') for k in FIELDS},source={})
        self.a.generator=generate;self.invoke(self.a,'generate',dict(project_id=project,hours=24));self.until(started.is_set)
        self.assertTrue(self.state(self.a)['busy']);self.assertEqual(self.state(self.b)['entries'].get(project,[]),[])
        release.set();self.until(lambda:not self.state(self.a)['busy']);self.assertEqual(self.state(self.b)['entries'].get(project,[]),[])
        self.action(self.a,'publish',dict(project_id=project,content=self.state(self.a)['drafts'][project]['content']));self.until(lambda:len(self.state(self.b)['entries'].get(project,[]))==1)
        self.runtime.tasks.remove_binding(self.task['id'],0)
        self.invoke(self.a,'generate',dict(project_id=project,hours=24));self.until(lambda:not self.state(self.a)['busy'])
    def test_chat_first_generation_uses_selected_history_without_a_task_and_stays_private(self):
        self.pair_devices();self.runtime.tasks.delete(self.task['id'])
        for peer in (self.a,self.b):self.invoke(peer,'chats',dict(query='',archived=False))
        with self.assertRaisesRegex(ValueError,'明确选择'):
            self.action(self.a,'invite',dict(peer_id=self.b.identity.id,thread_url='codex://threads/unselected',title='不能猜测'))
        self.action(self.a,'invite',dict(peer_id=self.b.identity.id,thread_url='codex://threads/own-chat',title='从聊天协作'))
        self.until(lambda:bool(self.state(self.b)['invites']));project=next(iter(self.state(self.b)['invites']))
        self.action(self.b,'accept',dict(id=project,thread_url='codex://threads/peer-chat'))
        self.until(lambda:project in self.state(self.a)['projects'])
        for peer,url in ((self.a,'own-chat'),(self.b,'peer-chat')):
            self.until(lambda:self.state(peer)['knowledge_jobs'][project]['status']=='ready')
            state=self.state(peer);row=state['knowledge_jobs'][project]
            self.assertIn(url,row['text']);self.assertNotIn('task_id',state['links'][project])
            self.assertNotIn(project,state['published']);self.assertNotIn(project,state['replicas'])
            self.assertNotIn('从 codex://', (Path(state['workspaces'][project]['root'])/NOTE_FILE).read_text())
        self.share_knowledge(self.a,project);self.invoke(self.b,'refresh')
        self.until(lambda:project in self.state(self.b)['replicas'])
        self.assertIn('own-chat',(Path(self.state(self.b)['replicas'][project]['root'])/NOTE_FILE).read_text())
        self.assertNotIn(project,self.state(self.a)['replicas'])

    def test_generated_knowledge_does_not_overwrite_edits_and_can_be_cancelled(self):
        project=self.project();started=threading.Event()
        def generate(folder,url,cancel,progress):
            started.set();cancel.wait(3)
            if cancel.is_set():raise InterruptedError('已取消')
            return dict(text='generated',source={})
        self.a.knowledge_generator=generate;self.invoke(self.a,'knowledge_generate',dict(project_id=project,confirmed=True));self.until(started.is_set)
        self.invoke(self.a,'knowledge_cancel',dict(project_id=project));self.until(lambda:self.state(self.a)['knowledge_jobs'][project]['status']=='cancelled')
        self.a.knowledge_generator=self.fake_knowledge;self.invoke(self.a,'knowledge_generate',dict(project_id=project,confirmed=True))
        self.until(lambda:self.state(self.a)['knowledge_jobs'][project]['status']=='ready');row=self.state(self.a)['knowledge_jobs'][project]
        path=Path(self.state(self.a)['workspaces'][project]['root'])/NOTE_FILE;path.write_text('保留用户的新计划')
        with self.assertRaisesRegex(ValueError,'已被 Codex'):
            self.invoke(self.a,'knowledge_apply',dict(project_id=project,id=row['id'],expected=row['base_sha256'],confirmed=True))
        self.assertEqual(path.read_text(),'保留用户的新计划');self.assertEqual(self.state(self.a)['knowledge_jobs'][project]['status'],'ready')

    def test_discovery_uses_lan_mesh_packets_without_chat_or_server_and_no_echo(self):
        a=MeshDiscovery(self.a.identity,'同事甲',45874);b=MeshDiscovery(self.b.identity,'同事乙',45874)
        packet=a.packet();self.assertNotIn(b'thread',packet);self.assertNotIn(b'server',packet);self.assertIn(b'lan-mesh',packet)
        b.ingest(packet,'192.168.1.7');self.assertEqual(b.snapshot()[self.a.identity.id]['port'],45874)
        value=json.loads(packet);value['role']='worker';b.peers.clear();b.ingest(json.dumps(value),'192.168.1.7');self.assertEqual(b.snapshot(),{})
    def test_cancelled_invitation_wins_over_late_acceptance(self):
        self.pair_devices();self.action(self.a,'invite',dict(peer_id=self.b.identity.id,task_id=self.task['id'],thread_url='codex://threads/own-chat',title='取消测试'))
        self.until(lambda:bool(self.state(self.b)['invites']));project=next(iter(self.state(self.b)['invites']))
        with self.a.lock:self.a.state['invites'][project]['state']='withdraw';self.a.persist()
        self.action(self.b,'accept',dict(id=project,task_id=self.peer_tasks.task['id'],thread_url='codex://threads/peer-chat'))
        self.until(lambda:self.state(self.b)['projects'][project]['state']=='archived')
        self.assertNotIn(project,self.state(self.a)['projects'])
        sent=next(a for a in self.state(self.b)['actions'] if a['operation']=='accept')
        self.assertEqual(self.b.request(self.a.identity.id,'accept',sent['payload']),{'cancelled':True})
    def test_restart_keeps_identity_pairing_and_message_ids(self):
        from services.CollaborationService import CollaborationService
        from framework import api
        project=self.project();self.action(self.a,'message',dict(project_id=project,text='持久化讨论'))
        self.until(lambda:bool(self.state(self.b)['entries'].get(project)))
        original_id=self.b.identity.id;original_pin=self.b.identity.pin
        self.b.stop();api.del_service('/PeerB')
        from mesh_fixture import FakeDiscovery
        self.b=CollaborationService(api.server(),self.root/'b',self.peer_tasks,discovery=FakeDiscovery,service_name='/PeerB',listen_port=0)
        self.assertEqual(self.b.identity.id,original_id);self.assertEqual(self.b.identity.pin,original_pin)
        self.assertTrue(self.b.paired(self.a.identity.id));self.assertEqual(self.b.state['entries'][project][0]['body'],'持久化讨论')
        self.action(self.a,'message',dict(project_id=project,text='断线后再发送'))
        self.until(lambda:any(a['operation']=='message' and a['status']=='queued' and a['error'] for a in self.state(self.a)['actions']))
        pending=next(a for a in self.state(self.a)['actions'] if a['operation']=='message' and a['status']=='queued')
        assert api.add_service(self.b,'/PeerB')==0;self.b.start();self.until(lambda:self.b.listener is not None and self.b.discovery is not None)
        with self.a.lock:self.a.discovery.peers[self.b.identity.id]['port']=self.b.listener.port;self.a.refresh_peers();self.a.persist()
        self.invoke(self.a,'retry',{'id':pending['id']});self.until(lambda:len(self.state(self.b)['entries'][project])==2)
        self.a.request(self.b.identity.id,'message',pending['payload']);self.assertEqual(len(self.state(self.b)['entries'][project]),2)
    def test_bad_replica_hash_never_replaces_old_copy(self):
        import base64
        from collaboration.replica import revision
        project=self.project();old=self.state(self.a)['replicas'][project];raw=b'good';files=[dict(path='资料.md',bytes=4,sha256=hashlib.sha256(raw).hexdigest())];value=dict(version=revision(files),files=files)
        with self.assertRaisesRegex(ValueError,'校验失败'):
            self.a.replicas.pull(project,self.b.identity.id,value,lambda op,p:dict(data=base64.b64encode(b'bad!').decode()),threading.Event(),lambda *args:None)
        self.assertEqual(self.state(self.a)['replicas'][project],old);self.assertTrue(Path(old['root']).exists());self.assertFalse(self.a.replicas.folder(project,self.b.identity.id,value['version']).exists())
    def test_udp_discovery_accepts_loopback_presence_without_a_coordinator(self):
        import socket
        with patch('lan_mesh.host_info.get_broadcast_targets',return_value=['127.0.0.1']):
            discovery=MeshDiscovery(self.b.identity,'同事乙',45874,port=0);discovery.start()
            try:
                port=discovery.socket.getsockname()[1]
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:sender.sendto(MeshDiscovery(self.a.identity,'同事甲',45874).packet(),('127.0.0.1',port))
                self.until(lambda:self.a.identity.id in discovery.snapshot())
                self.assertEqual(discovery.snapshot()[self.a.identity.id]['address'],'127.0.0.1')
            finally:discovery.stop()
    def test_stopping_joins_all_owned_threads_and_retains_history(self):
        project=self.project();self.b.stop();self.assertIsNone(self.b.listener);self.assertIsNone(self.b.discovery)
        self.assertIn(project,json.loads(self.b.path.read_text())['projects'])
        self.action(self.a,'message',dict(project_id=project,text='离线待重连'))
        self.until(lambda:any(a['operation']=='message' and a['status']=='queued' and a['error'] for a in self.state(self.a)['actions']))

if __name__=='__main__':unittest.main()
