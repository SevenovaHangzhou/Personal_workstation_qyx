"""Manual local work logs, isolated from nightly reports and remote operations."""
import copy
import threading
import uuid
from datetime import datetime,timedelta
from urllib.parse import urlparse
from daily_history import HistoryIndex,ZONE
from daily_store import DailyStore
from daily_summary import CodexSummarizer
from progress_evidence import period_records,report_lines
from model import CATEGORIES,STATUSES
from codex_support import redact
from observation import job

KINDS={'today':'今日工作日志','week':'本周工作周报'}
ACTIVE={'queued','running'}


def period(kind,now):
    if kind not in KINDS:raise ValueError('请选择今日日志或本周周报')
    if now.tzinfo is None:raise ValueError('日志截止时间需要时区')
    end=now.astimezone(ZONE)
    start=end.replace(hour=0,minute=0,second=0,microsecond=0)
    if kind=='week':start-=timedelta(days=start.weekday())
    return start,end


def render(content):
    start=datetime.fromisoformat(content['start']).astimezone(ZONE).strftime('%Y-%m-%d %H:%M')
    end=datetime.fromisoformat(content['end']).astimezone(ZONE).strftime('%Y-%m-%d %H:%M')
    lines=['# '+KINDS[content['kind']],'','统计范围：'+start+' → '+end+'（北京时间）','',
           '本机生成 · 报告共享按 LAN Mesh 设置执行 · 22:00 自动日报照常。','']
    tasks={task['id']:task for task in content['tasks']}
    groups=content.get('groups',{})
    lines+=['聊天 '+str(len(groups))+' 个 · 文本消息 '+str(sum(len(messages) for messages in groups.values()))+' 条','']
    for thread,messages in groups.items():
        assigned=[tasks[tid] for tid in content['mapping'].get(thread,[]) if tid in tasks]
        lines+=['## '+(' / '.join(CATEGORIES[t['category']]+' · '+redact(t['title']) for t in assigned) or '聊天归属待核对'),
                '','来源：codex://threads/'+thread,'','消息数：'+str(len(messages)),'']
        for task in assigned:lines+=['任务状态快照：'+STATUSES[task['status']],'']
        lines+=[content.get('summaries',{}).get(thread,'摘要尚未完成；原始记录已保留，可重试。'),'']
    if not groups:lines+=[('正在收集记录…' if content['state'] in ACTIVE else '收集未完成，可重试。' if not content.get('collected') else '本周期没有找到已绑定聊天的用户／助手文本消息。'),'']
    lines+=report_lines(content.get('evidence',[]))
    if content.get('errors'):lines+=['## 待处理','']+['- '+error for error in content['errors']]
    return '\n'.join(lines)


class WorkLogs:
    def __init__(self,folder,tasks,execution_lock=None,history=None,summarizer=None):
        self.tasks=tasks;self.store=DailyStore(folder)
        self.history=history or HistoryIndex(self.store)
        self.summarizer=summarizer or CodexSummarizer(self.store)
        self.execution_lock=execution_lock or threading.Lock()
        self.lock=threading.RLock();self.worker=None;self.cancel=threading.Event()
        self.stopping=False;self.closed=False;self.active=None
        self.rows=self.store.get('log_rows',[]);self.revision=self.store.get('log_revision',0)
        # A stopped application must not silently resend previous model requests.
        for row in self.rows:
            if row['state'] in ACTIVE:
                row['state']='cancelled';row['phase']='上次运行已中断，可重试'
                report=self.store.report(row['id'])
                if report:
                    content=report['content'];content.update(state='cancelled',phase=row['phase'])
                    self._save(content)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy({'rows':self.rows,'revision':self.revision,'active':self.active,
                                  'busy':self.active is not None,'folder':str(self.store.folder)})

    def text(self,identifier):
        with self.lock:
            if not any(row['id']==identifier for row in self.rows):raise ValueError('工作日志不存在')
        return self.store.get('log_text:'+identifier,'')

    def start(self,kind,now=None):
        start,end=period(kind,now or datetime.now(ZONE))
        with self.lock:
            if self.stopping:raise ValueError('工作日志服务已停止')
            if self.active:return {'id':self.active,'existing':True}
            tasks=copy.deepcopy([t for t in self.tasks.tasks if t['category'] in ('work','demo','life') and not t.get('is_sample')])
            identifier=kind+'-'+end.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
            content={'id':identifier,'kind':kind,'title':KINDS[kind]+' · '+end.strftime('%Y-%m-%d'),
                     'start':start.isoformat(),'end':end.isoformat(),'tasks':tasks,'collected':False,
                     'groups':{},'mapping':{},'summaries':{},'evidence':period_records(tasks,start,end),
                     'errors':[],'state':'queued','phase':'等待后台收集','completed':0,'total':0,'markdown':''}
            self._save(content);self._launch(content)
            return {'id':identifier,'existing':False}

    def retry(self,identifier):
        with self.lock:
            if self.stopping:raise ValueError('工作日志服务已停止')
            if self.active:return {'id':self.active,'existing':True}
            if not any(row['id']==identifier for row in self.rows):raise ValueError('工作日志不存在')
            content=self.store.report(identifier)['content']
            content.update(state='queued',phase='等待重试',errors=[])
            self._save(content);self._launch(content)
            return {'id':identifier,'existing':False}

    def _launch(self,content):
        self.cancel=threading.Event();self.active=content['id']
        self.worker=threading.Thread(target=self._run,args=(content,self.cancel),name='orbit-work-log',daemon=False)
        self.worker.start()

    def cancel_active(self):
        with self.lock:self.cancel.set()

    def _save(self,content):
        content['markdown']=render(content)
        self.store.save_report(content['id'],content['state'],content,[])
        self.store.put('log_text:'+content['id'],content['markdown'])
        row={key:content[key] for key in ('id','kind','title','start','end','state','phase','completed','total','errors')}
        with self.lock:
            self.rows=[copy.deepcopy(row)]+[r for r in self.rows if r['id']!=content['id']]
            self.rows=self.rows[:60];self.revision+=1
            self.store.put('log_rows',self.rows);self.store.put('log_revision',self.revision)

    @job('@DailyWorker','/DailyService','/work-log')
    def _run(self,content,cancel):
        acquired=False
        try:
            while not acquired:
                if cancel.is_set():raise InterruptedError('已取消生成，可重试')
                acquired=self.execution_lock.acquire(timeout=.1)
            if cancel.is_set():raise InterruptedError('已取消生成，可重试')
            content.update(state='running',phase='收集已绑定聊天与人工进展记录');self._save(content)
            if not content['collected']:
                start=datetime.fromisoformat(content['start']);end=datetime.fromisoformat(content['end'])
                allowed={urlparse(b['url']).path.strip('/') for t in content['tasks'] for b in t.get('bindings',[])
                         if b['kind']=='codex' and not b.get('demo')}
                self.history.allowed_threads=allowed
                earliest=self.store.get('index_start')
                if earliest is None or start<datetime.fromisoformat(earliest):
                    # A wider period must re-read previously skipped older messages.
                    with self.store.lock,self.store.db:self.store.db.execute('DELETE FROM sources')
                warnings=self.history.scan(cancel,start if earliest is None else min(start,datetime.fromisoformat(earliest)))
                self.store.put('index_start',min(start,datetime.fromisoformat(earliest)).isoformat() if earliest else start.isoformat())
                groups=self.history.between(start,end)
                content['summaries']={tid:text for tid,text in content['summaries'].items()
                                      if content['groups'].get(tid)==groups.get(tid) and tid in groups}
                content['groups']=groups
                content['mapping']={tid:[t['id'] for t in content['tasks'] if any(b['kind']=='codex' and not b.get('demo') and urlparse(b['url']).path.strip('/')==tid for b in t.get('bindings',[]))] for tid in content['groups']}
                content['scan_errors']=warnings;content['collected']=not warnings
                content['total']=len(content['groups']);self._save(content)
            content['errors']=list(content.get('scan_errors',[]))
            for index,(tid,messages) in enumerate(content['groups'].items()):
                if cancel.is_set():raise InterruptedError('已取消生成，可重试')
                content.update(phase='总结聊天 '+str(index+1)+' / '+str(content['total']),completed=index)
                self._save(content)
                if tid not in content['summaries']:
                    try:content['summaries'][tid]=self.summarizer.summarize(messages,cancel)
                    except Exception as exc:
                        if cancel.is_set() or isinstance(exc,InterruptedError):raise InterruptedError('已取消生成，可重试') from exc
                        content['errors'].append('聊天 '+tid[:8]+' 摘要未完成：'+redact(str(exc)))
                content['completed']=index+1;self._save(content)
            if cancel.is_set():raise InterruptedError('已取消生成，可重试')
            content.update(state='pending' if content['errors'] else 'done',phase='部分内容未完成，可重试' if content['errors'] else '已生成，报告共享按设置执行')
        except InterruptedError as exc:content.update(state='cancelled',phase=str(exc))
        except Exception as exc:
            content.update(state='pending',phase='生成未完成，可重试')
            content['errors']=list(content.get('errors',[]))+[redact(str(exc))]
        finally:
            try:self._save(content)
            except Exception as exc:
                # Keep a retry action available even when the final disk write fails.
                with self.lock:
                    for row in self.rows:
                        if row['id']==content['id']:
                            row.update(state='pending',phase='本地保存未完成，可重试',errors=[redact(str(exc))])
                    self.revision+=1
            finally:
                if acquired:self.execution_lock.release()
                with self.lock:self.active=None

    def stop(self):
        with self.lock:self.stopping=True;self.cancel.set();worker=self.worker
        if worker:worker.join()

    def close(self):
        self.stop()
        if not self.closed:self.store.close();self.closed=True
