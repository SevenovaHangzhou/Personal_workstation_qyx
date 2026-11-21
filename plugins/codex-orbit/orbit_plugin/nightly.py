"""Deterministic daily windows and recovery; only bound-chat text needs a model."""
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import threading

from evaluation.mesh import _private_write
from evaluation.protocol import record_digest, record_id, validate_record, valid_uuid
from evaluation.report_archive import write_json, safe_file
from platform_support import lock_file
from .journals import result_log, markdown, log_text, rows
from .journal_codex import CodexText, messages
from .scheduler import Scheduler, latest_boundary, local_boundary

FORMAT='orbit-daily-log/v1'


def report_markdown(report):
    task=report['task']
    lines=[f"# 星序日报 · {report['day']} · {task['issue_identifier']}", '',task['title'],
        f"周期：{report['period_start']} → {report['period_end']}（左闭右开）",
        '收集状态：'+report['status'], f"绑定聊天消息数：{report['message_count']}；结构化结果数：{len(report['results'])}",
        '', '## 聊天进展摘要',report['summary'] or '本周期未取得可总结的聊天内容，不能据此认定没有工作。','']
    if report['warnings']:
        lines+=['## 待处理',*[f'- {text}' for text in report['warnings']],'']
    for result in report['results']:
        lines+=['---',markdown(result),'']
    lines+=['本地日报不自动更新 Linear，不作为完成率或验收依据。']
    return '\n'.join(lines)


def published_reports(orbit,task_ids):
    root=orbit.directory/'daily/reports'
    wanted=set(task_ids)
    for path in sorted(root.glob('????-??-??-????/*.json')):
        if not valid_uuid(path.stem) or path.stem not in wanted: continue
        path=safe_file(root, str(path.relative_to(root)))
        if not path.is_file() or path.stat().st_size>10*1024*1024: raise ValueError('日报文件过大或不存在')
        report=json.loads(path.read_text(encoding='utf-8'))
        if report.get('format')!=FORMAT or report.get('task',{}).get('id')!=path.stem:
            raise ValueError('日报格式或任务身份无效')
        yield path.parent.name,report


def shared_files(orbit,task_ids):
    files={}
    for boundary,report in published_reports(orbit,task_ids):
        stem='日报/'+boundary+'/'+report['task']['id']
        files[stem+'.json']=(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
        files[stem+'.md']=report_markdown(report).encode('utf-8')
    return files


def shared_records(orbit,settings):
    result=[]
    for boundary,report in published_reports(orbit,settings['task_ids']):
        task=report['task']
        summary=('聊天消息数：'+str(report['message_count'])+'\n收集状态：'+report['status']+'\n'+report['summary']+
                 '\n待处理：'+'；'.join(report['warnings'])+'\n结构化结果 ID：'+','.join(log['result_id'] for log in report['results']))
        source='codex-daily:'+boundary
        evidence=[dict(id=hashlib.sha256((source+task['id']+str(i)).encode()).hexdigest(),kind='execution',text=summary[i:i+4000],
                       source=task['issue_url'],created_at=report['period_end']) for i in range(0,len(summary),4000)]
        record=dict(id=record_id(source,task['id']),employee_id=settings['employee_id'],name=settings['name'],
            department=settings['department'],source_device=settings['employee_id'],source_id=source,kind='nightly',
            period_start=report['period_start'],period_end=report['period_end'],task_id=task['id'],task_title=task['title'],
            issue_id=task['issue_id'],issue_identifier=task['issue_identifier'],issue_url=task['issue_url'],
            parent_issue_id=task['parent_issue_id'],linear_state_type=task['linear_state_type'],summary=summary,evidence=evidence)
        if valid_uuid(task['linear_assignee_id']): record['linear_assignee_id']=task['linear_assignee_id']
        record['digest']=record_digest(record)
        result.append(validate_record(record))
    return result


class DailyCollector:
    def __init__(self,orbit,*,read_messages=None,summarize=None):
        self.orbit=orbit
        self.scheduler=Scheduler(orbit.directory)
        self.read_messages=read_messages or messages
        self.summarize=summarize
        self.root=orbit.directory/'daily'
        self.state_path=self.root/'state.json'

    def collect(self,now=None,cancel=None):
        cancel=cancel or threading.Event()
        config=self.scheduler.config()
        if not config['enabled']: return dict(state='disabled',periods=0,pending=0)
        if not config['first_boundary']: return dict(state='not_configured',periods=0,pending=0)
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (self.root/'run.lock').open('a+') as guard:
            try: lock_file(guard)
            except OSError: return dict(state='already_running',periods=0,pending=0)
            return self._collect(config,now or datetime.now().astimezone(),cancel)

    def _collect(self,config,now,cancel):
        state=json.loads(self.state_path.read_text(encoding='utf-8')) if self.state_path.exists() else dict(last_boundary='',last_end='',pending=[],windows={})
        due=latest_boundary(now,config['time'])
        cursor=datetime.fromisoformat(state['last_end']) if state['last_end'] else None
        first=local_boundary(cursor.astimezone().date() if cursor else date.fromisoformat(config['first_boundary']),config['time'])
        if cursor and first.timestamp()<=cursor.timestamp():
            first=local_boundary(first.date()+timedelta(days=1),config['time'])
        windows={key:state['windows'][key] for key in state['pending']}
        while first.timestamp()<=due.timestamp():
            start=cursor or local_boundary(first.date()-timedelta(days=1),config['time'])
            key=first.strftime('%Y-%m-%d-%H%M')
            windows[key]=dict(start=start.isoformat(),end=first.isoformat())
            cursor=first
            first=local_boundary(first.date()+timedelta(days=1),config['time'])
        registration=self.scheduler.registration()
        home=Path(registration.get('codex_home') or Path.home()/'.codex')
        codex=CodexText(self.orbit.directory,registration,cancel)
        summarize=self.summarize or codex.summarize
        with self.orbit.db() as db:
            tasks=[self.orbit.task_row(row) for row in db.execute('SELECT * FROM tasks ORDER BY id')]
        bindings={}
        for task in tasks:
            if task['session_id']: bindings.setdefault(task['session_id'],[]).append(task['id'])
        result_rows=rows(self.orbit)
        completed=processed=0
        for key,window in sorted(windows.items()):
            if cancel.is_set(): raise InterruptedError('日报收集已取消')
            start,end=datetime.fromisoformat(window['start']),datetime.fromisoformat(window['end'])
            incomplete=False
            for task in tasks:
                if datetime.fromisoformat(task['created_at']).timestamp()>=end.timestamp(): continue
                issue=task['issue']
                folder=self.root/'reports'/key
                path=folder/(task['id']+'.json')
                # Successfully saved task/day reports are idempotent even when another task failed.
                if path.exists():
                    previous=json.loads(path.read_text(encoding='utf-8'))
                    if previous['status'] in ('complete','no_activity'): continue
                warnings=[]
                transcript=[]
                if task['session_id'] and len(bindings[task['session_id']])>1:
                    warnings.append('同一聊天绑定多个 issue，未自动归属，请核对关联。')
                elif task['session_id']:
                    try: transcript=self.read_messages(home,task['session_id'],start,end)
                    except (OSError,ValueError) as exc: warnings.append(log_text(str(exc)))
                else:
                    warnings.append('尚未绑定 Codex 聊天，仅整理已有结构化结果。')
                summary=''
                if transcript:
                    try: summary=log_text(summarize(transcript))
                    except (OSError,ValueError,RuntimeError) as exc: warnings.append(log_text(str(exc)))
                    if not summary: warnings.append('聊天摘要待重试。')
                selected=[result_log(row) for row in result_rows if row['task_id']==task['id'] and
                    start.timestamp()<=datetime.fromisoformat(row['created_at']).timestamp()<end.timestamp()]
                pending=bool(task['session_id'] and warnings)
                report=dict(format=FORMAT,day=(end-timedelta(microseconds=1)).date().isoformat(),period_start=start.isoformat(),
                    period_end=end.isoformat(),generated_at=now.isoformat(),status='pending' if pending else ('complete' if transcript or selected else 'no_activity'),
                    task=dict(id=task['id'],issue_id=task['issue_id'],issue_identifier=issue['identifier'],title=log_text(issue['title']),
                        issue_url=issue['url'],parent_issue_id=issue.get('parent_id','') or '',linear_state_type=issue.get('state_type',''),
                        linear_assignee_id=issue.get('assignee_id',''),bound_chat_id=task['session_id']),
                    message_count=len(transcript),summary=summary,results=selected,warnings=warnings)
                write_json(path,report)
                _private_write(path.with_suffix('.md'),report_markdown(report).encode('utf-8'))
                incomplete|=pending
            if incomplete:
                if key not in state['pending']: state['pending'].append(key)
                state['windows'][key]=window
            else:
                state['pending']=[value for value in state['pending'] if value!=key]
                state['windows'].pop(key,None)
                completed+=1
            if not state['last_end'] or end.timestamp()>datetime.fromisoformat(state['last_end']).timestamp():
                state['last_boundary'],state['last_end']=key,end.isoformat()
            state.update(updated_at=now.isoformat(),state='pending' if state['pending'] else 'complete')
            write_json(self.state_path,state)
            processed+=1
        return dict(state='pending' if state['pending'] else 'complete',periods=processed,completed_periods=completed,
                    pending=len(state['pending']),directory=str(self.root/'reports'))
