"""Code-driven nightly pipeline with durable retry and immutable report inputs."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from daily_history import HistoryIndex, cutoff, ZONE
from daily_summary import CodexSummarizer
from daily_git import daily_snapshot, git
from model import CATEGORIES, STATUSES, issue_keys
from progress_evidence import period_records, report_lines


class DailyEngine:
    def __init__(self, store, tasks, writer_factory, station, history=None, summarizer=None):
        self.store, self.tasks, self.writer_factory, self.station = store, tasks, writer_factory, station
        self.history = history or HistoryIndex(store)
        self.summarizer = summarizer or CodexSummarizer(store)
        self.progress = lambda message: None
        self._resolved = {}; self._choices = {}
        self.sync_cutoff = None

    def eligible(self, tasks, end):
        return [t for t in tasks if not t.get('created_at') or datetime.fromisoformat(t['created_at']) < end]

    def synchronize(self, task_id, config, cancel, stack=None):
        if cancel.is_set(): raise InterruptedError('日报已停止')
        stack = set(stack or ())
        if task_id in stack: raise ValueError('任务父子关系存在循环')
        stack.add(task_id)
        task = self.tasks.get(task_id)
        if not task or task['category'] != 'work':
            raise ValueError('原工作任务已删除或更改分类，停止远端同步')
        real = [b for b in task['bindings'] if b['kind']=='linear' and not b.get('demo')]
        if len(real) > 1 or len(issue_keys(task)) > 1 or any(issue_keys(task)&issue_keys(t) for t in self.tasks.tasks if t['id'] != task_id):
            raise ValueError('历史 issue 存在多重映射；请在管理关联中整理为一一对应')
        cached = getattr(self, '_resolved', {}).get(task_id)
        if cached and not task.get('linear_parent_pending') and any(b['url'] == cached['url'] for b in real):
            return cached
        writer = self.writer_factory()
        pending = task.get('linear_parent_pending')
        limit = self.sync_cutoff or datetime.now(timezone.utc)
        if pending and datetime.fromisoformat(pending['due_at']) > limit and not real and not task.get('linear_issue'):
            raise ValueError('新的任务层级尚未到 22:00 同步时间，保留本地任务等待下一期')
        if not real and task.get('linear_issue'):
            issue = writer.issue(task['linear_issue']['identifier'])
            return self.synchronize_parent(task, issue, config, cancel, stack, writer)
        if real:
            issue = writer.issue(real[0]['label'])
            if issue_keys({'bindings':real}) != issue_keys({'bindings':[{'kind':'linear','url':issue['url'],'label':issue['identifier']}]}):
                raise ValueError('手动链接与当前 Linear 工作区不一致，请重新绑定')
            if task.get('linear_issue') != issue:
                self.tasks.attach_issue(task_id, issue)
            return self.synchronize_parent(task, issue, config, cancel, stack, writer)
        parent = None
        if task.get('parent_id'):
            parent = self.synchronize(task['parent_id'], config, cancel, stack)
        target = ({'team_id':parent['team_id'], 'project_id':parent.get('project_id','')} if parent else
                  task.get('linear_target') or config.get('target', {}))
        if not target.get('team_id'):
            raise ValueError('请在「日报与同步」选择默认 Linear 团队，或设置根任务归属')
        # Validate access/project membership with current server data rather than a free-text ID.
        choices = getattr(self,'_choices',{})
        if target['team_id'] not in choices:
            choices[target['team_id']] = writer.choices(target['team_id'])
        options = choices[target['team_id']]
        if target.get('project_id') and target['project_id'] not in {p['id'] for p in options['projects']}:
            raise ValueError('所选项目不属于此团队，请重新选择归属')
        payload = {'title':task['title'], 'description':task.get('description',''),
                   'teamId':target['team_id'], 'assigneeId':options['viewer']['id']}
        if target.get('project_id'): payload['projectId'] = target['project_id']
        if parent: payload['parentId'] = parent['id']
        key = 'issue:'+task_id
        operation = self.store.operation(key, payload)
        # A changed destination after an uncertain write needs a human decision.
        for field in ('teamId', 'projectId', 'parentId'):
            if operation['payload'].get(field) != payload.get(field):
                raise ValueError('issue 创建记录的归属已改变，保留原同步记录，请人工核实')
        issue = operation['result'] if operation['state']=='done' else writer.ensure_issue(operation)
        self.store.complete(key, issue)
        self.tasks.attach_issue(task_id, issue)
        return self.synchronize_parent(task, issue, config, cancel, stack, writer)

    def synchronize_parent(self, task, issue, config, cancel, stack, writer):
        pending = task.get('linear_parent_pending')
        limit = self.sync_cutoff or datetime.now(timezone.utc)
        if pending and datetime.fromisoformat(pending['due_at']) > limit:
            self._resolved[task['id']] = issue
            return issue
        if pending:
            parent = self.synchronize(task['parent_id'], config, cancel, stack) if task.get('parent_id') else None
            if parent and parent['team_id'] != issue['team_id']:
                raise ValueError('父子 issue 团队不同，请先在 Linear 中确认归属后重试')
            key = 'parent:'+task['id']+':'+pending['revision']
            operation = self.store.operation(key, {'issue_id':issue['id'], 'parent_id':parent['id'] if parent else None})
            if cancel.is_set(): raise InterruptedError('日报已停止')
            # Always reconcile current remote state, even after a lost response or later move.
            issue = writer.ensure_parent(operation)
            self.store.complete(key, issue)
            current = self.tasks.get(task['id'])
            if not current or current.get('linear_parent_pending') != pending:
                raise ValueError('本地父级在同步期间已改变，保留新关系待下一次同步')
            self.tasks.attach_issue(task['id'], issue)
            if not self.tasks.acknowledge_parent(task['id'], pending):
                raise ValueError('本地父级已改变，稍后重新核对')
        elif task.get('linear_issue') != issue:
            self.tasks.attach_issue(task['id'], issue)
        self._resolved[task['id']] = issue
        return issue

    def run(self, day, config, cancel):
        end = cutoff(day); start = end-timedelta(days=1)
        self.sync_cutoff = end
        self._resolved = {}; self._choices = {}
        self.progress("收集带时间戳的本机聊天")
        earliest = self.store.get('history_start')
        if earliest is None:
            earliest = start.isoformat(); self.store.put('history_start', earliest)
        errors = []; scan_errors = self.history.scan(cancel, datetime.fromisoformat(earliest))
        errors.extend(scan_errors)
        existing = self.store.report(day)
        if existing:
            content = existing['content']
        else:
            tasks = self.eligible(self.tasks.tasks, end)
            groups = self.history.between(start, end)
            mapping = {}
            for task in tasks:
                for binding in task['bindings']:
                    if binding['kind']=='codex' and not binding.get('demo'):
                        thread = urlparse(binding['url']).path.strip('/')
                        mapping.setdefault(thread, []).append(task['id'])
            content = {'day':day,'start':start.isoformat(),'end':end.isoformat(),
                       'tasks':tasks,'groups':groups,'mapping':mapping,'summaries':{},'git':{},'markdown':''}
            content['evidence'] = period_records(tasks, start, end)
            self.store.save_report(day, 'pending', content, errors)
        taskmap = {t['id']:t for t in content['tasks']}
        issue_cache = {}
        self.progress("同步工作任务与父子 issue")
        for task in content['tasks']:
            if cancel.is_set(): raise InterruptedError('日报已停止')
            if task['category'] != 'work' or task.get('is_sample'):
                continue
            try:
                issue_cache[task['id']] = self.synchronize(task['id'], config, cancel)
            except Exception as exc:
                if isinstance(exc, InterruptedError): raise
                errors.append(task['title']+'：issue 同步未完成；'+str(exc))
        self.progress("留存未完成代码任务的 Git 补丁与文件")
        # Completed tasks and nonwork categories never produce daily repository refs.
        for task in content['tasks']:
            if task['category'] != 'work' or task['status']=='done' or task.get('is_sample'): continue
            current = self.tasks.get(task['id'])
            if not current or current['category']!='work' or current['status']=='done': continue
            if issue_cache.get(task['id'],{}).get('state_type') in ('completed','canceled'): continue
            repos = list(current.get('repositories', []))
            for repo in repos:
                if repo['kind'] != 'local': continue
                key = task['id']+':'+repo['location']
                if key in content['git']: continue
                try:
                    if task['id'] not in issue_cache: raise ValueError('等待绑定真实 issue 后创建每日分支')
                    root = git(repo['location'], 'rev-parse','--show-toplevel').decode().strip()
                    with self.station.reserve_repository(root):
                        content['git'][key] = daily_snapshot(root, self.store.folder/'git', day, self.tasks.get(task['id']), cancel)
                except Exception as exc:
                    if isinstance(exc, InterruptedError): raise
                    errors.append(task['title']+'：Git 留存未完成；'+str(exc))
        self.store.save_report(day, "pending", content, errors)
        self.progress("正在压缩必要文本")
        for thread, messages in content['groups'].items():
            if cancel.is_set(): raise InterruptedError('日报已停止')
            if thread not in content['summaries']:
                try:
                    content['summaries'][thread] = self.summarizer.summarize(messages, cancel)
                except Exception as exc:
                    if isinstance(exc, InterruptedError): raise
                    errors.append('聊天 '+thread[:8]+'：文本摘要未完成；'+str(exc))
            assigned = content['mapping'].get(thread, [])
            # A user may remove a mistaken association before retry. Never add a new
            # historical destination implicitly or reuse deleted/reclassified tasks.
            assigned = [i for i in assigned if (self.tasks.get(i) or {}).get('category') == taskmap[i]['category'] and
                        any(b['kind']=='codex' and b['url']=='codex://threads/'+thread for b in self.tasks.get(i)['bindings'])]
            content['mapping'][thread] = assigned
            if len(assigned) > 1:
                errors.append('聊天 '+thread[:8]+' 绑定多个任务；本地保留，暂不上传，需明确唯一归属')
        self.store.save_report(day, "pending", content, errors)
        self.progress("追加各工作 issue 的日期日报评论")
        # One daily comment per task, aggregating all of its explicitly-bound chats.
        for task_id, issue in issue_cache.items():
            threads = [tid for tid, ids in content['mapping'].items() if ids == [task_id] and tid in content['groups']]
            if not threads:
                continue
            if scan_errors or any(t not in content['summaries'] for t in threads):
                continue  # Never publish a partial transcript as a complete report.
            body = '## Momo 日报 · '+day+'\n\n周期：'+content['start']+' → '+content['end']+'\n\n'
            body += '本地任务状态：'+STATUSES[taskmap[task_id]['status']]+'（不自动更改 Linear 状态）\n\n'
            body += '\n\n'.join(content['summaries'][t]+'\n\n来源：codex://threads/'+t for t in threads)
            opkey = 'comment:'+day+':'+task_id
            operation = self.store.operation(opkey, {'issueId':issue['id'],'body':body})
            if operation['payload']['issueId'] != issue['id']:
                errors.append(taskmap[task_id]['title']+'：日报目标 issue 已变化，请人工核实'); continue
            try:
                if operation['state'] != 'done':
                    result = self.writer_factory().ensure_comment(operation)
                    self.store.complete(opkey, result)
            except Exception as exc:
                errors.append(taskmap[task_id]['title']+'：日报评论等待重试；'+str(exc))
        lines = ['# Momo 日报 · '+day,'', '周期：'+content['start']+' → '+content['end'],
                 '22:00 后的记录进入次日日报。任务状态为本地快照，模型摘要不自动判定完成。','']
        for thread, messages in content['groups'].items():
            ids = content['mapping'].get(thread, [])
            names = [CATEGORIES[taskmap[i]['category']]+' · '+taskmap[i]['title'] for i in ids if i in taskmap]
            lines += ['## '+(' / '.join(names) if names else '未绑定聊天 · '+thread[:8]),
                      '来源：codex://threads/'+thread,'消息数：'+str(len(messages)), '',
                      content['summaries'].get(thread,'文本摘要等待重试；带时间戳的原文已保存在本地报告数据库。'),'']
        if not content['groups']: lines += ['本周期没有可收集的用户/助手文本消息。','']
        lines += report_lines(content.get('evidence', []))
        lines += ['## Git 每日留存','']
        for snapshot in content['git'].values():
            lines.append('- '+json.dumps(snapshot, ensure_ascii=False))
        content['markdown'] = '\n'.join(lines)
        errors = list(dict.fromkeys(errors))
        self.store.save_report(day, 'pending' if errors else 'done', content, errors)
        return {'day':day,'errors':errors,'state':'pending' if errors else 'done'}
