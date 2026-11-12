"""Yomk-owned peer collaboration: mutual pairing, durable discussion and knowledge replicas."""
import copy
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import socket
import threading
import time
import uuid
from collaboration.mesh import Identity,PeerListener,PeerTransport,MeshDiscovery,verify,pairing_code,canonical
from collaboration.protocol import FIELDS,content,chat_url,identifier,text
from collaboration.knowledge import DOCUMENTS,NOTE_FILE,initialize,safe_file
from collaboration.replica import Replicas
from collaboration.workspace import scan,difference
from ota_core import atomic_text
from observation import job
from services.base import MomoService,ok,require,request_data,ServiceRequestError


def uid():return str(uuid.uuid4())


class CollaborationService(MomoService):
    def __init__(self,server,folder,tasks,transport=PeerTransport,discovery=MeshDiscovery,generator=None,
                 service_name='/CollaborationService',listen_port=45874,chat_catalog=None,knowledge_generator=None,report_folder=None):
        super().__init__(server);self.set_name(service_name);self.service_name=service_name
        self.folder=Path(folder);self.tasks=tasks;self.transport_factory=transport;self.discovery_factory=discovery;self.generator=generator
        self.report_folder=report_folder;self.report_versions=Replicas(self.folder/'report-collections');self.report_value=None;self.report_due=0
        self.chat_catalog=chat_catalog;self.knowledge_generator=knowledge_generator;self.chat_choices={}
        self.path=self.folder/'mesh-state.json'
        self.state=dict(format='orbit-lan-mesh/v2',config=dict(enabled=False,name=socket.gethostname()[:80],port=listen_port),
                        device_id=uid(),peers={},pairs={},invites={},projects={},links={},actions=[],drafts={},entries={},
                        report_sharing=dict(enabled=False,admin_id=None,collect=False),report_inbox={},report_status={},report_source_status='',report_sync={},
                        knowledge_jobs={},workspaces={},published={},replicas={},cursors={},received={},sync=dict(status='idle',message='',done=0,total=0))
        if self.path.exists():
            saved=json.loads(self.path.read_text());require(saved.get('format')==self.state['format'],'局域网协作记录格式无效')
            self.state.update(saved)
            for action in self.state['actions']:
                if action['status']=='sending':action['status']='queued'
        for task in self.state['knowledge_jobs'].values():
            if task['status'] in ('queued','running'):task.update(status='interrupted',message='上次生成已中断；点击重新生成继续',progress=0)
        identifier(self.state['device_id'])
        self.identity=Identity(self.folder/'.credentials',self.state['device_id'])
        self.replicas=Replicas(self.folder/'knowledge-versions')
        self.legacy_available=(self.folder/'state.json').exists()
        self.listener=None;self.discovery=None;self.worker=None;self.summary_worker=None;self.sync_worker=None;self.knowledge_worker=None
        self.stopping=threading.Event();self.wake=threading.Event();self.cancel=threading.Event();self.sync_cancel=threading.Event()
        self.report_cancel=threading.Event();self.knowledge_cancel=threading.Event();self.knowledge_project=None
        self.notice='尚未启用 LAN Mesh；知识库与讨论保存在本机';self.summary_status='';self.revision=0;self.poll_due=0;self.persist()

    def init(self):
        for name,handler,description in [
          ('report_configure',self.report_configure,'指定日报周报只读共享的管理员，或启停管理员收集'),
          ('snapshot',self.snapshot,'读取本地协作状态'),('configure',self.configure,'显式启停局域网直连'),
          ('action',self.action,'排队发送用户确认的配对、邀请或讨论'),('receive',self.receive,'验证签名与双方配对后接收直连消息'),
          ('chats',self.chats,'读取可明确选择的本机聊天'),
          ('knowledge_generate',self.knowledge_generate,'后台根据完整聊天历史生成知识库'),
          ('knowledge_cancel',self.cancel_knowledge,'停止所选知识库生成'),
          ('knowledge_apply',self.apply_knowledge,'核对后保存生成的知识库到本地'),
          ('generate',self.generate,'将指定聊天整理为本地摘要草稿'),('save_draft',self.save_draft,'保存摘要，不发送'),
          ('save_note',self.save_note,'仅保存本机知识库笔记'),('cancel',self.cancel_generation,'停止摘要生成'),
          ('retry',self.retry,'按原标识重试发送'),('entries',self.entries,'读取本地讨论记录'),('watch',self.watch,'刷新所选协作'),
          ('workspace',self.workspace,'读取自动创建的专用知识库'),('knowledge_init',self.knowledge_init,'补建知识库模板'),
          ('sync',self.sync,'准备预览或确认共享知识库'),('sync_cancel',self.cancel_sync,'停止知识库传输'),('refresh',self.refresh,'唤醒后台直连同步')]:
            if name=='chats':self.install_func('/chats',handler,'dict',description)
            else:self.install('/'+name,handler,'dict',description)
        self.alive=True;return 0

    def persist(self):
        atomic_text(self.path,json.dumps(self.state,ensure_ascii=False));self.revision+=1

    def start(self):
        if self.worker and self.worker.is_alive():return
        self.worker=threading.Thread(target=self.run,name='orbit-collaboration',daemon=True);self.worker.start()

    def snapshot(self,pkg):
        require(pkg=={},'请求无效');value=copy.deepcopy(self.state)
        value.update(notice=self.notice,revision=self.revision,summary_status=self.summary_status,
          busy=bool(self.summary_worker and self.summary_worker.is_alive()),legacy_available=self.legacy_available,
          discovered=self.discovery.snapshot() if self.discovery else {},discovery_error=self.discovery.error if self.discovery else '',
          report_folder=str(self.report_folder.root) if self.report_folder else '',
          fingerprint=self.identity.pin,listening_port=self.listener.port if self.listener else None)
        return ok(value)

    def configure(self,pkg):
        require(isinstance(pkg,dict) and set(pkg)=={'enabled','name','port'},'仅需设置启用状态、显示名称和本机端口')
        require(type(pkg['enabled']) is bool,'启用状态无效')
        require(type(pkg['port']) is int and 1<=pkg['port']<=65535,'本机端口应为 1–65535')
        self.state['config']=dict(enabled=pkg['enabled'],name=text(pkg['name'],80),port=pkg['port'])
        if not pkg['enabled']:self.cancel.set();self.sync_cancel.set();self.report_cancel.set()
        self.notice='正在启用局域网直连' if pkg['enabled'] else '协作网络已停止；本机资料保留'
        self.persist();self.wake.set();return ok()

    def chats(self,pkg):
        from codex_support import CodexRpc,metadata
        try:
            require(isinstance(pkg,dict),'聊天列表请求无效')
            query=pkg.get('query','');archived=pkg.get('archived',False);cancel=pkg.get('cancel')
            require(isinstance(query,str) and len(query)<=200 and type(archived) is bool,'搜索内容无效')
            with self.lock:require(self.alive and not self.stopping.is_set(),'协作服务已停止')
            if self.chat_catalog:rows=self.chat_catalog(query,archived,cancel)
            else:
                with CodexRpc(cancel) as rpc:
                    rows=[metadata(row) for row in rpc.list_threads(False,query)]
                    if archived:rows.extend(metadata(row) for row in rpc.list_threads(True,query))
            choices={}
            for row in rows:
                url=chat_url('codex://threads/'+row['id'])
                choices[url]=dict(id=row['id'],title=text(row.get('title') or '未命名聊天',200),
                                 preview=row.get('preview','')[:1000],cwd=row.get('cwd',''),updated_at=row.get('updated_at',0))
            if cancel and cancel.is_set():raise ValueError('已取消读取聊天')
            with self.lock:
                require(self.alive and not self.stopping.is_set(),'协作服务已停止')
                self.chat_choices.update(choices)
            return ok(list(choices.values()))
        except Exception as exc:
            from framework import api
            return api.YomkResponse(api.ResStatus.eInvalid,str(exc)[:400])

    def binding(self,task_id,url):
        chat_url(url);choice=self.chat_choices.get(url)
        task=self.tasks.get(task_id) if task_id else None
        # Old task-based invitations remain readable; new selection uses the chat catalog.
        legacy=task and task['category'] in ('work','demo','life') and any(
            b.get('kind')=='codex' and b.get('url')==url and not b.get('demo') for b in task['bindings'])
        require(choice or legacy,'请先从本机聊天列表明确选择聊天')
        return dict(thread_url=url,thread_title=choice['title'] if choice else task['title'],
                    **({'task_id':task['id']} if legacy else {}))

    def project_binding(self,project,active=True):
        identifier(project);row=self.state['projects'].get(project);link=self.state['links'].get(project)
        require(row and link,'本机尚未关联此协作任务')
        require(not active or row['state']=='active','协作已结束，历史资料仍可查看')
        chat_url(link['thread_url']);return link

    def paired(self,peer):
        return any(p['peer_id']==peer and p['own_confirmed'] and p['peer_confirmed'] and p['state']=='active' for p in self.state['pairs'].values())

    def queue(self,operation,peer,payload):
        action=dict(id=uid(),operation=operation,peer_id=peer,payload=copy.deepcopy(payload),status='queued',error='',next_attempt=0)
        self.state['actions'].append(action);self.persist();self.wake.set();return action['id']

    def action(self,pkg):
        require(isinstance(pkg,dict) and set(pkg)=={'operation','payload','confirmed'} and pkg['confirmed'] is True,'需要用户预览并确认后发送')
        require(self.state['config']['enabled'],'请先启用 LAN Mesh')
        operation=pkg['operation'];payload=copy.deepcopy(pkg['payload']);require(isinstance(payload,dict),'动作内容无效')
        if operation=='pair_request':
            peer_id=identifier(payload.get('peer_id'));peer=self.state['peers'].get(peer_id)
            require(peer,'请先发现同事设备');require(not self.paired(peer_id),'已经与此同事配对')
            require(not any(p['peer_id']==peer_id and p['state']=='active' for p in self.state['pairs'].values()),'已有配对请求，请核对或取消')
            pair_id=uid();self.state['pairs'][pair_id]=dict(id=pair_id,peer_id=peer_id,fingerprint=peer['fingerprint'],own_confirmed=False,peer_confirmed=False,state='active',incoming=False)
            return ok(self.queue(operation,peer_id,dict(id=pair_id)))
        if operation in ('pair_confirm','pair_reject'):
            pair=self.state['pairs'].get(identifier(payload.get('id')));require(pair and pair['state']=='active','配对请求已失效')
            peer_id=pair['peer_id']
            if operation=='pair_confirm':
                require(payload.get('code')==pairing_code(pair['id'],self.identity.pin,pair['fingerprint']),'配对核对码已变化，请重新核对')
                pair['own_confirmed']=True
            else:pair['state']='rejected'
            return ok(self.queue(operation,peer_id,dict(id=pair['id'])))
        if operation=='invite':
            peer_id=identifier(payload.get('peer_id'));require(self.paired(peer_id),'双方核对配对码并确认后才能邀请')
            link=self.binding(payload.get('task_id'),payload.get('thread_url'));project=uid()
            payload=dict(id=project,title=text(payload.get('title'),200),thread_url=payload['thread_url'])
            self.state['links'][project]=link
            self.state['invites'][project]=dict(payload,sender=self.identity.id,recipient=peer_id,state='pending')
            return ok(self.queue(operation,peer_id,payload))
        if operation in ('accept','reject','withdraw'):
            project=identifier(payload.get('id'));invite=self.state['invites'].get(project)
            require(invite and invite['state']=='pending','邀请已失效')
            incoming=invite['recipient']==self.identity.id
            require(incoming if operation in ('accept','reject') else not incoming,'邀请方向无效')
            peer_id=invite['sender'] if incoming else invite['recipient']
            if operation=='accept':
                self.state['links'][project]=self.binding(payload.get('task_id'),payload.get('thread_url'))
                self.create_project(invite,payload['thread_url'])
                payload=dict(id=project,thread_url=payload['thread_url'])
            else:invite['state']=operation;payload=dict(id=project)
            return ok(self.queue(operation,peer_id,payload))
        require(operation in ('publish','message','leave'),'不支持此动作')
        project=identifier(payload.get('project_id'));link=self.project_binding(project);row=self.state['projects'][project];peer_id=row['peer_id']
        if operation=='leave':row['state']='archived';return ok(self.queue(operation,peer_id,dict(id=uid(),project_id=project)))
        value=content(payload.get('content')) if operation=='publish' else text(payload.get('text'),12000)
        if operation=='publish':require(self.state['drafts'].get(project,{}).get('content')==value,'草稿已变化，请重新预览后发送')
        message=dict(id=uid(),project_id=project,kind=operation,body=value,created=datetime.now(timezone.utc).isoformat())
        self.append_entry(project,self.identity.id,message)
        return ok(self.queue(operation,peer_id,message))

    def create_project(self,invite,recipient_thread):
        project=invite['id'];own=self.identity.id
        peer=invite['sender'] if invite['recipient']==own else invite['recipient']
        if project in self.state['projects']:return
        root=self.folder/'knowledge'/project/uid();root.mkdir(parents=True,exist_ok=False)
        initialize(root,project,invite['title'])
        invite['state']='accepted'
        self.state['projects'][project]=dict(id=project,title=invite['title'],state='active',peer_id=peer,
           members=[dict(id=invite['sender'],thread_url=invite['thread_url']),dict(id=invite['recipient'],thread_url=recipient_thread)])
        self.state['workspaces'][project]=dict(root=str(root),version='',files=[])
        self.queue_knowledge(project)

    def append_entry(self,project,author,message):
        require(isinstance(message,dict) and set(message)=={'id','project_id','kind','body','created'},'讨论格式无效')
        identifier(message['id']);require(message['project_id']==project and message['kind'] in ('publish','message'),'讨论归属无效')
        body=content(message['body']) if message['kind']=='publish' else text(message['body'],12000)
        text(message['created'],60)
        row=dict(message,body=body,author=author)
        rows=self.state['entries'].setdefault(project,[])
        previous=next((r for r in rows if r['id']==row['id']),None)
        require(previous is None or previous==row,'相同讨论标识的内容不一致')
        if previous is None:rows.append(row)

    def member(self,project,peer):
        row=self.state['projects'].get(identifier(project))
        require(row and row['peer_id']==peer and row['state']=='active','尚未确认此协作或协作已结束')
        return row

    def receive(self,pkg):
        require(self.state['config']['enabled'] and not self.stopping.is_set(),'此设备尚未启用协作')
        message,pin=verify(pkg.get('envelope'),self.identity.id);peer_id=message['sender'];operation=message['operation'];payload=message['payload']
        require(isinstance(payload,dict),'消息内容无效')
        if operation=='pair_request':
            require(set(payload)=={'id','name','port'},'配对内容无效');pair_id=identifier(payload['id'])
            require(type(payload['port']) is int and 1<=payload['port']<=65535,'同事监听端口无效')
            name=text(payload['name'],80);old=self.state['peers'].get(peer_id)
            require(not old or old['fingerprint']==pin,'设备身份变化，请先在本机核对；未接受配对')
            pair=self.state['pairs'].get(pair_id)
            if pair:require(pair['peer_id']==peer_id and pair['fingerprint']==pin,'配对请求不匹配')
            else:
                require(sum(p['state']=='active' and not p['own_confirmed'] for p in self.state['pairs'].values())<50,'待核对的配对请求过多')
                self.state['pairs'][pair_id]=dict(id=pair_id,peer_id=peer_id,fingerprint=pin,own_confirmed=False,peer_confirmed=False,state='active',incoming=True)
            self.state['peers'][peer_id]=dict(id=peer_id,name=name,port=payload['port'],address=pkg['address'],fingerprint=pin,online=True)
            self.persist();return ok(dict(name=self.state['config']['name']))
        peer=self.state['peers'].get(peer_id);require(peer and peer['fingerprint']==pin,'设备尚未配对或指纹已变化')
        if operation in ('pair_confirm','pair_reject'):
            require(set(payload)=={'id'},'配对确认格式无效');pair=self.state['pairs'].get(identifier(payload['id']))
            require(pair and pair['peer_id']==peer_id and pair['fingerprint']==pin,'没有对应配对请求')
            if operation=='pair_confirm' and pair['state']=='active':pair['peer_confirmed']=True
            elif operation=='pair_reject':pair['state']='rejected'
            self.persist();return ok({})
        require(self.paired(peer_id),'双方尚未确认配对，不能交换任务、聊天标识或资料')
        if operation=='reports_manifest':
            require(payload=={},'报告目录请求无效')
            share=self.state['report_sharing']
            if not share['enabled'] or share['admin_id']!=peer_id:return ok(dict(available=False))
            if not self.report_value:return ok(dict(available=True,ready=False,message=self.state['report_source_status'] or '正在准备报告目录'))
            return ok(dict(available=True,ready=True,**self.report_value))
        if operation=='reports_read':
            require(set(payload)=={'version','path','offset'},'报告读取请求无效')
            share=self.state['report_sharing']
            require(share['enabled'] and share['admin_id']==peer_id,'未获准收集此设备的报告')
            require(self.report_folder is not None,'此设备没有报告目录')
            return ok(self.report_folder.read(payload['version'],payload['path'],payload['offset']))
        if operation=='poll':return ok(self.poll(peer_id,payload))
        if operation=='kb_manifest':
            require(set(payload)=={'project_id'},'知识库清单请求无效');self.member(payload['project_id'],peer_id)
            return ok(self.state['published'].get(payload['project_id']))
        if operation=='kb_read':
            require(set(payload)=={'project_id','version','path','offset'},'资料读取格式无效');project=payload['project_id'];self.member(project,peer_id)
            version=self.state['published'].get(project)
            require(version and version['version']==payload['version'],'共享版本已变化，请重新同步')
            return ok(self.replicas.read(project,self.identity.id,version,payload['path'],payload['offset']))
        action_id=identifier(payload.get('id'));key=peer_id+':'+action_id
        digest=hashlib.sha256(canonical([operation,payload])).hexdigest();previous=self.state['received'].get(key)
        if previous:
            require((previous.get('hash') if isinstance(previous,dict) else previous)==digest,'重复消息的内容不一致')
            return ok(previous.get('result',{}) if isinstance(previous,dict) else {})
        if operation=='invite':
            require(set(payload)=={'id','title','thread_url'},'邀请格式无效')
            require(action_id not in self.state['invites'],'邀请标识已占用')
            self.state['invites'][action_id]=dict(id=action_id,title=text(payload['title'],200),thread_url=chat_url(payload['thread_url']),sender=peer_id,recipient=self.identity.id,state='pending')
        elif operation in ('accept','reject','withdraw'):
            require(set(payload)==({'id','thread_url'} if operation=='accept' else {'id'}),'邀请回复格式无效')
            invite=self.state['invites'].get(action_id);require(invite,'邀请不存在')
            require(invite['sender']==peer_id if operation=='withdraw' else invite['recipient']==peer_id,'邀请回复设备不匹配')
            if operation=='accept' and invite['state'] in ('withdraw','reject'):
                self.state['received'][key]=dict(hash=digest,result=dict(cancelled=True));self.persist();return ok(dict(cancelled=True))
            require(invite['state']=='pending' or operation=='withdraw','邀请已处理')
            if operation=='withdraw' and action_id in self.state['projects']:self.state['projects'][action_id]['state']='archived'
            if operation=='accept':
                link=self.state['links'].get(action_id);require(link,'本机任务关联不存在')
                chat_url(link['thread_url']);self.create_project(invite,chat_url(payload['thread_url']))
            else:invite['state']=operation
        elif operation in ('message','publish'):
            project=payload.get('project_id');self.member(project,peer_id)
            require(payload.get('kind')==operation,'讨论类型无效');self.append_entry(project,peer_id,payload)
        elif operation=='leave':
            require(set(payload)=={'id','project_id'},'结束协作格式无效')
            self.member(payload['project_id'],peer_id)['state']='archived'
        else:raise ValueError('不支持的直连动作')
        self.state['received'][key]=dict(hash=digest,result={});self.persist();self.wake.set();return ok({})

    def poll(self,peer_id,payload):
        require(set(payload)=={'cursors'} and isinstance(payload['cursors'],dict) and len(payload['cursors'])<=1000,'同步游标无效')
        projects=[]
        for project,row in self.state['projects'].items():
            if row['peer_id']!=peer_id:continue
            after=payload['cursors'].get(project,0);require(type(after) is int and after>=0,'同步游标无效')
            rows=[r for r in self.state['entries'].get(project,[]) if r['author']==self.identity.id]
            require(after<=len(rows),'同步游标超出已有记录')
            # Bound a poll to one project/page; callers paginate without losing records.
            page=rows[after:after+2]
            projects.append(dict(id=project,state=row['state'],entries=[{k:v for k,v in r.items() if k!='author'} for r in page],
              cursor=after+len(page),knowledge=({'version':self.state['published'][project]['version']} if row['state']=='active' and project in self.state['published'] else None)))
            if len(canonical(projects))>450000:
                projects.pop();break
        return dict(projects=projects)

    def save_draft(self,pkg):
        project=pkg.get('project_id');self.project_binding(project)
        self.state['drafts'][project]=dict(self.state['drafts'].get(project,{}),id=uid(),content=content(pkg.get('content')))
        self.persist();return ok()

    def save_note(self,pkg):
        project=pkg.get('project_id');self.project_binding(project)
        body=pkg.get('text');require(isinstance(body,str) and len(body.encode())<=512000 and '\x00' not in body,'笔记过长或含无效字符')
        path=safe_file(self.state['workspaces'][project]['root'],NOTE_FILE)
        current=path.read_bytes() if path.exists() else b''
        require(pkg.get('expected')==hashlib.sha256(current).hexdigest(),'笔记已被 Codex 或其他程序修改，请先保存当前编辑到别处并重新打开核对，不会覆盖已有内容')
        if path.exists():
            history=self.folder/'note-history'/project;history.mkdir(parents=True,exist_ok=True)
            (history/(uid()+'.md')).write_bytes(path.read_bytes())
        atomic_text(path,body);self.persist();return ok()

    def knowledge_baseline(self,project):
        with self.lock:
            mine=copy.deepcopy(self.state['workspaces'].get(project,{}));peer=copy.deepcopy(self.state['replicas'].get(project,{}))
        documents={}
        for prefix,workspace in (('我的知识库/',mine),('同事知识库/',peer)):
            if not workspace.get('root'):continue
            previous={r['path']:r['sha256'] for r in workspace.get('files',[])}
            for name in DOCUMENTS:
                path=safe_file(workspace['root'],name)
                if path.is_file() and path.stat().st_size<=512000:
                    raw=path.read_bytes();documents[prefix+name]=dict(version=workspace.get('version',''),body=raw.decode('utf-8'),modified_locally=hashlib.sha256(raw).hexdigest()!=previous.get(name))
        return dict(documents=documents)

    def queue_knowledge(self,project):
        self.project_binding(project)
        path=safe_file(self.state['workspaces'][project]['root'],NOTE_FILE)
        self.state['knowledge_jobs'][project]=dict(id=uid(),status='queued',progress=0,
            message='等待整理所选聊天的完整历史',base_sha256=hashlib.sha256(path.read_bytes() if path.exists() else b'').hexdigest())
        self.persist()
        if not self.knowledge_worker or not self.knowledge_worker.is_alive():
            self.knowledge_cancel.clear()
            self.knowledge_worker=threading.Thread(target=self.run_knowledge,name='orbit-collaboration-knowledge',daemon=True)
            self.knowledge_worker.start()

    def knowledge_generate(self,pkg):
        project=pkg.get('project_id');self.project_binding(project)
        require(pkg.get('confirmed') is True,'请确认根据所选聊天生成本地知识库')
        old=self.state['knowledge_jobs'].get(project,{})
        require(old.get('status') not in ('queued','running'),'此知识库正在生成')
        self.queue_knowledge(project);return ok()

    def cancel_knowledge(self,pkg):
        project=pkg.get('project_id');self.project_binding(project)
        row=self.state['knowledge_jobs'].get(project,{})
        if row.get('status')=='queued':row.update(status='cancelled',message='已停止生成');self.persist()
        if row.get('status')=='running' and self.knowledge_project==project:self.knowledge_cancel.set()
        return ok()

    @job('@CollaborationWorker','/CollaborationService','/generate-knowledge')
    def run_knowledge(self):
        while not self.stopping.is_set():
            with self.lock:
                pending=next((p for p,j in self.state['knowledge_jobs'].items() if j['status']=='queued'),None)
                if pending is None:
                    self.knowledge_worker=None;return
                row=self.state['knowledge_jobs'][pending];job_id=row['id'];self.knowledge_project=pending;self.knowledge_cancel.clear()
                row.update(status='running',message='正在读取所选聊天的完整历史',progress=1);self.persist()
                url=self.state['links'][pending]['thread_url']
            def progress(value,message):
                with self.lock:
                    row.update(progress=value,message=message);self.revision+=1
            try:
                from collaboration.summary import generate_knowledge
                result=(self.knowledge_generator or generate_knowledge)(self.folder,url,self.knowledge_cancel,progress)
                require(not self.knowledge_cancel.is_set() and not self.stopping.is_set(),'已停止生成')
                require(isinstance(result.get('text'),str) and 0<len(result['text'].encode())<=512000,'知识库生成结果为空或过长')
                with self.lock:
                    self.project_binding(pending)
                    row.update(status='ready',progress=100,message='已生成，核对后保存到本地；共享仍需确认',text=result['text'],source=result.get('source',{}))
                    self.persist()
            except Exception as exc:
                with self.lock:
                    row.update(status='cancelled' if self.knowledge_cancel.is_set() else 'failed',message='生成未完成：'+str(exc)[:400]);self.persist()
            finally:
                with self.lock:self.knowledge_project=None

    def apply_knowledge(self,pkg):
        project=pkg.get('project_id');self.project_binding(project);row=self.state['knowledge_jobs'].get(project,{})
        require(pkg.get('confirmed') is True,'请先核对生成内容')
        require(row.get('status')=='ready' and pkg.get('id')==row['id'],'知识库草稿已变化，请重新核对')
        require(pkg.get('expected')==row['base_sha256'],'笔记版本已变化，请手动合并生成内容，不会覆盖已有编辑')
        self.save_note(dict(project_id=project,text=row['text'],expected=row['base_sha256']))
        row.update(status='applied',message='已保存本地知识库；预览并确认后再同步给同事');self.persist();return ok()

    def generate(self,pkg):
        project=pkg.get('project_id');link=self.project_binding(project)
        require(not self.summary_worker or not self.summary_worker.is_alive(),'已有摘要正在生成')
        require(pkg.get('hours') in (24,168),'请选择最近 24 小时或 7 天')
        end=datetime.now(timezone.utc);start=end-timedelta(hours=pkg['hours']);self.cancel.clear();self.summary_status='正在整理指定聊天与双方本地知识库；不会自动发送'
        @job('@CollaborationWorker','/CollaborationService','/generate-summary')
        def run():
            try:
                if self.generator:result=self.generator(self.folder,link['thread_url'],start,end,self.cancel)
                else:
                    from collaboration.summary import generate
                    result=generate(self.folder,link['thread_url'],start,end,self.cancel,self.knowledge_baseline(project))
                if self.cancel.is_set():raise InterruptedError('已停止生成')
                with self.lock:
                    self.state['drafts'][project]=dict(result,id=uid());self.summary_status='摘要已生成，预览确认后再发送';self.persist()
            except Exception as exc:
                with self.lock:self.summary_status='生成未完成：'+str(exc)[:400];self.revision+=1
        self.summary_worker=threading.Thread(target=run,name='orbit-collaboration-summary',daemon=True);self.summary_worker.start();return ok()

    def cancel_generation(self,pkg):self.cancel.set();return ok()
    def cancel_sync(self,pkg):self.sync_cancel.set();return ok()
    def refresh(self,pkg):self.report_due=0;self.poll_due=0;self.wake.set();return ok()
    def watch(self,pkg):identifier(pkg.get('project_id'));self.wake.set();return ok()
    def entries(self,pkg):return ok(copy.deepcopy(self.state['entries'].get(pkg.get('project_id'),[])))

    def workspace(self,pkg):
        project=pkg.get('project_id');self.project_binding(project)
        require(not pkg.get('root'),'协作只同步自动创建的专用知识库，不接管代码目录')
        return ok(self.state['workspaces'][project])

    def knowledge_init(self,pkg):
        project=pkg.get('project_id');self.project_binding(project);require(pkg.get('confirmed') is True,'请先确认创建笔记')
        result=initialize(self.state['workspaces'][project]['root'],project,self.state['projects'][project]['title']);self.persist();return ok(result)

    def sync(self,pkg):
        project=pkg.get('project_id');self.project_binding(project)
        operation=pkg.get('operation');require(operation in ('prepare','upload','download'),'知识库操作无效')
        if operation=='download':self.sync_cancel.clear();self.wake.set();return ok()
        require(not self.sync_worker or not self.sync_worker.is_alive(),'知识库正在准备，请稍后')
        workspace=copy.deepcopy(self.state['workspaces'][project]);preview=copy.deepcopy(self.state['sync'].get('preview'))
        if operation=='upload':
            require(pkg.get('confirmed') is True,'请先预览并确认共享')
            require(preview and preview['project_id']==project and preview['id']==pkg.get('preview_id'),'预览已变化，请重新准备')
        self.sync_cancel.clear();self.state['sync']=dict(status='preparing',project_id=project,message='正在后台准备知识库',done=0,total=0)
        @job('@CollaborationWorker','/CollaborationService','/knowledge-snapshot')
        def run():
            try:
                if operation=='prepare':
                    value=scan(workspace['root'],self.sync_cancel)
                    value.update(id=uid(),project_id=project,diff=difference(value['files'],workspace['files']))
                    with self.lock:self.state['sync'].update(status='prepared',preview=value,message='清单已准备，确认后同事才能拉取此版本');self.persist()
                else:
                    value=self.replicas.commit_local(project,self.identity.id,preview,self.sync_cancel)
                    with self.lock:
                        self.project_binding(project)
                        self.state['published'][project]=value;self.state['workspaces'][project].update(version=value['version'],files=value['files'])
                        self.state['sync'].update(status='done',message='此版本已共享；双方在线时对方会同步到本地');self.persist();self.wake.set()
            except Exception as exc:
                with self.lock:self.state['sync'].update(status='failed',message=str(exc)[:400]);self.persist()
        self.sync_worker=threading.Thread(target=run,name='orbit-collaboration-snapshot',daemon=True);self.sync_worker.start();return ok()

    def report_configure(self,pkg):
        require(isinstance(pkg,dict) and set(pkg)=={'enabled','admin_id','collect','confirmed'} and pkg['confirmed'] is True,'请确认报告共享设置')
        require(type(pkg['enabled']) is bool and type(pkg['collect']) is bool,'报告共享开关无效')
        admin=pkg['admin_id']
        if admin is not None:
            identifier(admin);require(self.paired(admin),'请先与管理员设备配对并核对配对码')
        if pkg['enabled']:
            require(self.report_folder is not None,'此设备没有日报周报目录');require(admin is not None,'请选择管理员设备')
        self.state['report_sharing']=dict(enabled=pkg['enabled'],admin_id=admin,collect=pkg['collect'])
        self.report_value=None;self.report_due=0;self.poll_due=0
        if not pkg['collect']:self.report_cancel.set()
        else:self.report_cancel.clear()
        self.state['report_source_status']='正在准备只读报告目录' if pkg['enabled'] else '报告共享未启用'
        self.persist();self.wake.set();return ok()

    def prepare_reports(self):
        with self.lock:
            share=copy.deepcopy(self.state['report_sharing'])
            if not share['enabled'] or not self.paired(share['admin_id']) or not self.report_folder:return
        try:
            value=self.report_folder.refresh(self.stopping)
            with self.lock:
                if self.state['report_sharing']==share:
                    self.report_value=value;self.state['report_source_status']='只读共享 '+str(len(value['files']))+' 份报告，后续报告自动加入';self.revision+=1
        except Exception as exc:
            with self.lock:
                self.report_value=None;self.state['report_source_status']='报告目录等待更新：'+str(exc)[:240];self.revision+=1

    def pull_reports(self,peer_id):
        from collaboration.reports import REPORT_SCOPE,report_manifest
        with self.lock:
            if not self.state['report_sharing']['collect']:return
            old=copy.deepcopy(self.state['report_inbox'].get(peer_id,{}))
        try:
            value=self.request(peer_id,'reports_manifest',{})
            require(isinstance(value,dict),'同事报告目录无效')
            if not value.get('available'):
                with self.lock:self.state['report_status'][peer_id]='未向本机共享报告';self.revision+=1
                return
            if not value.get('ready'):
                with self.lock:self.state['report_status'][peer_id]=value.get('message','报告尚未就绪');self.revision+=1
                return
            value=report_manifest(value)
            if old.get('version')==value['version']:
                with self.lock:self.state['report_status'][peer_id]='已收集，等待新报告';self.revision+=1
                return
            def progress(done,total,message):
                with self.lock:self.state['report_sync']=dict(status='receiving',done=done,total=total,message=message);self.revision+=1
            def request(operation,payload):
                return self.request(peer_id,'reports_read',{k:v for k,v in payload.items() if k!='project_id'})
            replica=self.report_versions.pull(REPORT_SCOPE,peer_id,value,request,self.report_cancel,progress)
            with self.lock:
                require(self.state['report_sharing']['collect'] and not self.report_cancel.is_set(),'管理员收集已停止，旧报告保留')
                self.state['report_inbox'][peer_id]=dict(replica,collected_at=datetime.now(timezone.utc).isoformat())
                self.state['report_status'][peer_id]='已收集 '+str(len(replica['files']))+' 份报告'
                self.state['report_sync']=dict(status='done',done=1,total=1,message='报告已收集到本地');self.persist()
        except Exception as exc:
            with self.lock:
                self.state['report_status'][peer_id]='收集未完成，原副本保留：'+str(exc)[:240]
                self.state['report_sync']=dict(status='failed',message=str(exc)[:240]);self.revision+=1

    def retry(self,pkg):
        action=next((a for a in self.state['actions'] if a['id']==pkg.get('id')),None)
        require(action and action['status'] in ('failed','queued'),'没有需要重试的发送')
        action.update(status='queued',error='',next_attempt=0);self.persist();self.wake.set();return ok()

    def receive_network(self,pkg):
        try:return request_data(self.service_name+'/receive',pkg)
        except ServiceRequestError as exc:raise ValueError(str(exc)) from None

    def refresh_peers(self):
        discovered=self.discovery.snapshot() if self.discovery else {}
        for peer_id,value in discovered.items():
            old=self.state['peers'].get(peer_id)
            if old and old['fingerprint']!=value['fingerprint']:continue
            self.state['peers'][peer_id]=dict(value,online=True)
        for peer_id,peer in self.state['peers'].items():peer['online']=peer_id in discovered
        self.revision+=1

    def request(self,peer_id,operation,payload):
        with self.lock:
            peer=copy.deepcopy(self.state['peers'].get(peer_id));enabled=self.state['config']['enabled']
        require(enabled and not self.stopping.is_set(),'局域网协作已停止')
        require(peer,'尚未发现同事设备');return self.transport_factory(self.identity,peer).request(operation,payload)

    def deliver(self,action):
        operation=action['operation'];payload=copy.deepcopy(action['payload']);peer=action['peer_id']
        with self.lock:
            require(self.state['config']['enabled'],'局域网协作已停止')
            if operation=='pair_request':payload.update(name=self.state['config']['name'],port=self.listener.port)
            if operation=='invite':
                invite=self.state['invites'][payload['id']];require(invite['state']=='pending','邀请已取消')
                link=self.state['links'][payload['id']];chat_url(link['thread_url'])
            if operation=='accept':self.project_binding(payload['id'])
            if operation in ('message','publish'):self.project_binding(payload['project_id'])
        result=self.request(peer,operation,payload)
        with self.lock:
            if operation=='accept' and result.get('cancelled'):
                self.state['projects'][payload['id']]['state']='archived';self.state['invites'][payload['id']]['state']='withdraw'
            action.update(status='sent',error='');self.persist()

    def pull_peer(self,peer_id):
        with self.lock:
            cursors={p:c for p,c in self.state['cursors'].items() if self.state['projects'].get(p,{}).get('peer_id')==peer_id}
        result=self.request(peer_id,'poll',dict(cursors=cursors))
        require(isinstance(result,dict) and isinstance(result.get('projects'),list) and len(result['projects'])<=1000,'同事同步信息无效')
        for remote in result['projects']:
            project=identifier(remote.get('id'))
            with self.lock:
                row=self.state['projects'].get(project)
                if not row or row['peer_id']!=peer_id:continue
                require(remote['state'] in ('active','archived'),'同事协作状态无效')
                if remote['state']=='archived':row['state']='archived'
                if row['state']!='active':self.persist();continue
                entries=remote['entries'];require(isinstance(entries,list) and len(entries)<=10,'讨论分页无效')
                before=self.state['cursors'].get(project,0);require(type(remote['cursor']) is int and remote['cursor']==before+len(entries),'讨论游标不连续')
                for message in entries:self.append_entry(project,peer_id,message)
                self.state['cursors'][project]=remote['cursor'];self.persist()
                current=copy.deepcopy(self.state['replicas'].get(project,{}))
            value=remote.get('knowledge')
            if not value or current.get('version')==value.get('version') or self.sync_cancel.is_set():continue
            def progress(done,total,message):
                with self.lock:self.state['incoming_sync']=dict(status='receiving',project_id=project,done=done,total=total,message=message);self.revision+=1
            # Incoming replicas are versioned separately; never touch the editable local knowledge root.
            value=self.request(peer_id,'kb_manifest',dict(project_id=project))
            replica=self.replicas.pull(project,peer_id,value,lambda op,p:self.request(peer_id,op,p),self.sync_cancel,progress)
            with self.lock:
                if self.state['projects'][project]['state']=='active':
                    self.state['replicas'][project]=replica;self.state['incoming_sync']=dict(status='done',project_id=project,done=1,total=1,message='同事知识库已同步到本地；你的笔记保持不变');self.persist()

    @job('@CollaborationWorker','/CollaborationService','/mesh-network')
    def run(self):
        active_config=None
        try:
            while not self.stopping.is_set():
                with self.lock:config=copy.deepcopy(self.state['config'])
                if config!=active_config:
                    if self.discovery:self.discovery.stop();self.discovery=None
                    if self.listener:self.listener.stop();self.listener=None
                    active_config=None
                    if config['enabled']:
                        try:
                            listener=PeerListener(self.identity,self.receive_network,port=config['port']);listener.start();self.listener=listener
                            self.discovery=self.discovery_factory(self.identity,config['name'],listener.port);self.discovery.start()
                            active_config=config;self.sync_cancel.clear();self.report_cancel.clear()
                            with self.lock:self.notice='LAN Mesh 已启用 · 双方配对后可讨论与同步知识库';self.revision+=1
                        except Exception as exc:
                            with self.lock:self.notice='局域网连接未就绪：'+str(exc)[:240];self.revision+=1
                    else:active_config=config
                if not self.listener or not config['enabled']:
                    self.wake.wait(1);self.wake.clear();continue
                with self.lock:
                    self.refresh_peers()
                    actions=[a for a in self.state['actions'] if a['status']=='queued' and a.get('next_attempt',0)<=time.time()][:8]
                for action in actions:
                    if self.stopping.is_set():break
                    with self.lock:action['status']='sending';self.persist()
                    try:self.deliver(action)
                    except Exception as exc:
                        with self.lock:
                            retry=isinstance(exc,(OSError,TimeoutError,ConnectionError))
                            action.update(status='queued' if retry else 'failed',error=str(exc)[:240],next_attempt=time.time()+10);self.persist()
                if time.monotonic()>=self.report_due:
                    self.prepare_reports();self.report_due=time.monotonic()+5
                if time.monotonic()>=self.poll_due:
                    with self.lock:peers=[p for p in self.state['peers'] if self.paired(p)]
                    for peer_id in peers:
                        if self.stopping.is_set():break
                        try:
                            self.pull_peer(peer_id)
                            self.pull_reports(peer_id)
                            with self.lock:self.state['peers'][peer_id].update(online=True,error='');self.revision+=1
                        except Exception as exc:
                            with self.lock:
                                self.state['peers'][peer_id].update(online=False,error=str(exc)[:200])
                                incoming=self.state.get('incoming_sync',{})
                                if incoming.get('status')=='receiving':incoming.update(status='failed',message='同步未完成，旧副本保留：'+str(exc)[:200])
                                self.revision+=1
                    self.poll_due=time.monotonic()+5
                self.wake.wait(.5);self.wake.clear()
        finally:
            if self.discovery:self.discovery.stop();self.discovery=None
            if self.listener:self.listener.stop();self.listener=None

    def stop(self):
        self.stopping.set();self.cancel.set();self.sync_cancel.set();self.wake.set()
        self.knowledge_cancel.set();self.report_cancel.set()
        for worker in (self.worker,self.summary_worker,self.sync_worker,self.knowledge_worker):
            if worker:worker.join(15)
            if worker and worker.is_alive():raise RuntimeError('协作后台线程尚未停止')
        with self.lock:self.persist()
