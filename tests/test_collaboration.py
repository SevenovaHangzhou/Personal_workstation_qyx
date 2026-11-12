"""Mutual pairing, whole-project exclusion, fencing and immutable writeback."""
import base64
import hashlib
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
from collaboration.protocol import FIELDS
from collaboration.store import KnowledgeStore
from collaboration.client import Transport
from collaboration.server import KnowledgeHTTP
from collaboration.summary import selected_messages
from collaboration.workspace import scan,transfer
from collaboration.files import allowed_path
from collaboration.discovery import Discovery
from collaboration.knowledge import NOTE_FILE,initialize


def uid():return str(uuid.uuid4())
def content():return {k:('进展' if k=='summary' else '') for k in FIELDS}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=1000
        self.store=KnowledgeStore(self.temp.name,'server-join-code',clock=lambda:self.now)
        self.a,self.b,self.c=uid(),uid(),uid();self.tokens={}
        for device in (self.a,self.b,self.c):
            token='x'*48+device;self.tokens[device]=token
            self.store.dispatch('register',None,dict(device_id=device,token=token,name=device[:6],join_code='server-join-code'))
        self.project=uid();self.store.dispatch('invite',self.a,dict(id=self.project,peer_id=self.b,title='项目',thread_url='codex://threads/chat-a'))
    def tearDown(self):self.store.close();self.temp.cleanup()
    def pair(self):self.store.dispatch('accept',self.b,dict(id=self.project,thread_url='codex://threads/chat-b'))
    def acquire(self,device):
        lease=uid();self.call('lease_acquire',device,id=lease);return lease
    def call(self,operation,device,**values):return self.store.dispatch(operation,device,dict(project_id=self.project,**values))
    def client(self,device):
        owner=self
        class Direct:
            def request(self,operation,payload):return owner.store.dispatch(operation,device,payload)
        return Direct()
    def test_pairing_requires_recipient_and_hides_project_from_others(self):
        self.assertEqual(self.store.dispatch('state',self.a,{})['projects'],[])
        for device in (self.a,self.c):
            with self.assertRaises(PermissionError):self.store.dispatch('accept',device,dict(id=self.project,thread_url='codex://threads/wrong'))
        self.pair();self.assertEqual(len(self.store.dispatch('state',self.b,{})['projects']),1)
        with self.assertRaises(PermissionError):self.call('project',self.c)
        self.assertEqual(self.store.dispatch('state',self.c,{})['projects'],[])
    def test_whole_project_lock_and_expiry_fence_old_owner(self):
        self.pair();lease=self.acquire(self.a)
        with self.assertRaisesRegex(ValueError,'正在使用'):self.acquire(self.b)
        with self.assertRaisesRegex(ValueError,'项目锁'):self.call('files_manifest',self.b,lease_id=lease)
        self.now+=91;new=self.acquire(self.b)
        with self.assertRaisesRegex(ValueError,'项目锁'):self.call('lease_renew',self.a,lease_id=lease)
        with self.assertRaisesRegex(ValueError,'不能释放'):self.call('lease_release',self.a,lease_id=lease)
        self.assertEqual(self.call('project',self.b)['lease']['id'],new)
    def test_repeated_lock_request_and_release_are_idempotent(self):
        self.pair();lease=self.acquire(self.a)
        self.call('lease_acquire',self.a,id=lease)
        self.call('lease_release',self.a,lease_id=lease);self.call('lease_release',self.a,lease_id=lease)
        with self.assertRaisesRegex(ValueError,'旧锁'):self.call('lease_acquire',self.a,id=lease)
        self.assertIsNone(self.call('project',self.b)['lease'])
    def test_publish_is_confirmed_by_client_and_server_requires_current_lock(self):
        self.pair();lease=self.acquire(self.a);data=dict(id=uid(),lease_id=lease,content=content())
        for _ in range(2):self.call('publish',self.a,**data)
        self.assertEqual(len(self.call('entries',self.a,lease_id=lease)['entries']),1)
        with self.assertRaisesRegex(ValueError,'项目锁'):self.call('entries',self.b,lease_id=lease)
        self.call('lease_release',self.a,lease_id=lease);other=self.acquire(self.b)
        rows=self.call('entries',self.b,lease_id=other)['entries'];self.assertEqual(rows[0]['thread_url'],'codex://threads/chat-a')
        with self.assertRaisesRegex(ValueError,'项目锁'):self.call('publish',self.a,**data)
    def test_upload_download_preserves_revisions_and_git_and_credentials_excluded(self):
        self.pair();lease=self.acquire(self.a);local=Path(self.temp.name)/'local';local.mkdir()
        (local/'PLAN.md').write_text('# 计划\n先验证，再实现');(local/'.env').write_text('DO_NOT_SHARE=secret');(local/'.git').mkdir();(local/'.git/config').write_text('private')
        prepared=scan(local,threading.Event());self.assertEqual([r['path'] for r in prepared['files']],['PLAN.md']);self.assertIn('.env',prepared['excluded'])
        preview=dict(prepared,upload_id=uid(),base_version=0)
        result=transfer(self.client(self.a),self.project,lease,'upload',self.temp.name,threading.Event(),lambda *a:None,preview)
        self.assertEqual(result['version'],1)
        # An ambiguous successful upload resolves by its original id, even though the base is older.
        again=transfer(self.client(self.a),self.project,lease,'upload',self.temp.name,threading.Event(),lambda *a:None,preview);self.assertEqual(again['version'],1)
        self.call('lease_release',self.a,lease_id=lease);lease2=self.acquire(self.b)
        downloaded=transfer(self.client(self.b),self.project,lease2,'download',self.temp.name,threading.Event(),lambda *a:None)
        root=Path(downloaded['root']);self.assertEqual((root/'PLAN.md').read_text(),(local/'PLAN.md').read_text());self.assertFalse((root/'.env').exists())
        (root/'PLAN.md').write_text('# 修改后的计划')
        update=dict(scan(root,threading.Event()),upload_id=uid(),base_version=1)
        transfer(self.client(self.b),self.project,lease2,'upload',self.temp.name,threading.Event(),lambda *a:None,update)
        revisions=list((self.store.folder/'shared'/self.project/'revisions').iterdir());self.assertEqual(len(revisions),2)
        self.assertTrue(any((r/'PLAN.md').read_text()=='# 计划\n先验证，再实现' for r in revisions))
    def test_incomplete_chunks_or_stale_base_never_publish(self):
        self.pair();lease=self.acquire(self.a);upload=uid();data=b'complete';row=dict(path='src/main.py',bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        self.call('files_begin',self.a,id=upload,lease_id=lease,base_version=0,files=[row])
        self.call('files_chunk',self.a,id=upload,lease_id=lease,path='src/main.py',offset=0,data=base64.b64encode(b'comp').decode())
        with self.assertRaisesRegex(ValueError,'不完整'):self.call('files_commit',self.a,id=upload,lease_id=lease)
        self.assertEqual(self.call('project',self.a)['version'],0)
        self.call('files_chunk',self.a,id=upload,lease_id=lease,path='src/main.py',offset=4,data=base64.b64encode(b'lete').decode())
        self.call('files_commit',self.a,id=upload,lease_id=lease)
        with self.assertRaisesRegex(ValueError,'版本已变化'):self.call('files_begin',self.a,id=uid(),lease_id=lease,base_version=0,files=[])
        with self.assertRaisesRegex(ValueError,'过期|项目锁'):
            self.now+=91;self.call('files_commit',self.a,id=upload,lease_id=lease)
    def test_changed_file_after_preview_is_not_transmitted(self):
        self.pair();lease=self.acquire(self.a);root=Path(self.temp.name)/'preview';root.mkdir();(root/'a.py').write_text('expected')
        preview=dict(scan(root,threading.Event()),upload_id=uid(),base_version=0);(root/'a.py').write_text('unexpected')
        with self.assertRaisesRegex(ValueError,'预览后'):transfer(self.client(self.a),self.project,lease,'upload',self.temp.name,threading.Event(),lambda *a:None,preview)
        self.assertEqual(self.call('project',self.a)['version'],0)
    def test_freeform_empty_and_absent_notes_all_sync(self):
        self.pair();lease=self.acquire(self.a);root=Path(self.temp.name)/'freeform';root.mkdir()
        for version,note in enumerate(('只有一段正在排查的思路，没有固定栏目。','',None)):
            path=root/NOTE_FILE
            if note is None:path.unlink()
            else:path.write_text(note)
            preview=dict(scan(root,threading.Event()),upload_id=uid(),base_version=version)
            result=transfer(self.client(self.a),self.project,lease,'upload',self.temp.name,threading.Event(),lambda *a:None,preview)
            self.assertEqual(result['version'],version+1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0],3)
    def test_old_index_is_an_ordinary_file_and_can_be_removed(self):
        self.pair();lease=self.acquire(self.a);root=Path(self.temp.name)/'old';root.mkdir();initialize(root,self.project,'合成项目')
        old=root/'project.knowledge.json';old.write_text('未完成的旧索引，不需要解析')
        for version in (0,1):
            if version:old.unlink()
            preview=dict(scan(root,threading.Event()),upload_id=uid(),base_version=version)
            result=transfer(self.client(self.a),self.project,lease,'upload',self.temp.name,threading.Event(),lambda *a:None,preview)
            self.assertEqual(result['version'],version+1)
    def test_invalid_paths_identity_takeover_and_expired_invite_rejected(self):
        for value in ('../escape','/absolute','a\\b','.git/config','.env','CON.txt','a:stream','a?b.txt','quote".py'):
            with self.assertRaises(ValueError):allowed_path(value)
        with self.assertRaises(PermissionError):self.store.authenticate(self.a,'wrong')
        with self.assertRaises(ValueError):self.store.dispatch('register',None,dict(device_id=self.a,token='other'*15,name='冒充',join_code='server-join-code'))
        self.now+=86401
        with self.assertRaisesRegex(ValueError,'过期'):self.pair()


class TLSTests(unittest.TestCase):
    def test_wrong_pin_sends_no_request_and_valid_pin_authenticates(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cert=root/'cert.pem';key=root/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=ORBIT-test','-keyout',str(key),'-out',str(cert)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
            store=KnowledgeStore(root/'db','join-code-for-tests');calls=[]
            def dispatch(pkg):calls.append(pkg);return store.dispatch(pkg['operation'],pkg['device'],pkg['payload'])
            server=KnowledgeHTTP(('127.0.0.1',0),store,dispatch);context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key);server.tls_context=context
            thread=threading.Thread(target=server.serve_forever);thread.start()
            try:
                url='https://127.0.0.1:'+str(server.server_port);device=uid();token='t'*64
                with self.assertRaisesRegex(ValueError,'指纹不符'):Transport(url,'0'*64,device,token).request('register',dict(device_id=device,token=token,name='Alice',join_code='join-code-for-tests'))
                self.assertEqual(calls,[])
                pin=hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest();client=Transport(url,pin,device,token)
                client.request('register',dict(device_id=device,token=token,name='Alice',join_code='join-code-for-tests'))
                self.assertEqual(client.request('heartbeat',{})['projects'],[])
                with self.assertRaisesRegex(ValueError,'认证失败'):Transport(url,pin,device,'wrong').request('state',{})
            finally:server.shutdown();server.server_close();thread.join();store.close()


class CollectionTests(unittest.TestCase):
    def test_only_selected_chat_period_and_user_assistant_messages_collected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'sessions').mkdir();now=datetime.now(timezone.utc);identifier=uid()
            records=[dict(type='session_meta',payload={'id':identifier})]
            for role,value in (('user','本机进展'),('tool','私有工具输出')):
                records.append(dict(type='response_item',timestamp=now.isoformat(),payload={'type':'message','role':role,'content':[{'type':'input_text','text':value}]}))
            (root/'sessions'/('rollout-'+identifier+'.jsonl')).write_text('\n'.join(json.dumps(r) for r in records)+'\n')
            rows=selected_messages('codex://threads/'+identifier,now-timedelta(days=1),now+timedelta(seconds=1),threading.Event(),root)
            self.assertEqual([r['text'] for r in rows],['本机进展'])
            with self.assertRaisesRegex(ValueError,'没有可总结'):selected_messages('codex://threads/'+uid(),now-timedelta(days=1),now,threading.Event(),root)


class DiscoveryTests(unittest.TestCase):
    def test_presence_filters_protocol_and_server_without_echoing_or_sharing_content(self):
        discovery=Discovery(uid(),'Alice','https://127.0.0.1:18765');peer=uid();sent=[]
        from collaboration.protocol import PROTOCOL
        records=iter([dict(protocol='other',server=discovery.server,device_id=uid(),name='Ignored'),
          dict(protocol=PROTOCOL,server='other',device_id=uid(),name='Ignored'),
          dict(protocol=PROTOCOL,server=discovery.server,device_id=peer,name='Bob')])
        class Datagram:
            def sendto(self,raw,address):sent.append(json.loads(raw))
            def recvfrom(self,size):
                try:return json.dumps(next(records)).encode(),('192.168.1.2',45873)
                except StopIteration:discovery.stopping.set();raise OSError('done')
        discovery.socket=Datagram();discovery.run()
        self.assertEqual(list(discovery.snapshot()),[peer]);self.assertEqual(len(sent),1)
        self.assertEqual(set(sent[0]),{'protocol','server','device_id','name'})
        with patch('collaboration.discovery.time.monotonic',return_value=discovery.peers[peer]['seen']+36):self.assertEqual(discovery.snapshot(),{})

if __name__=='__main__':unittest.main()
