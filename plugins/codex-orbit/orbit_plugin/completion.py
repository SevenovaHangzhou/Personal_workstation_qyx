"""Evidence-gated issue completion and a durable handoff to native chat archival."""
import hmac
import json
import uuid
from contextlib import contextmanager

from linear_support import LinearWriter, LinearError
from .core import OrbitError, now


ISSUE = '''id identifier title description url dueDate priority estimate
  state { id name type } assignee { id } project { id }
  team { id autoCloseParentIssues autoCloseChildIssues }
  parent { id state { type } team { id autoCloseParentIssues } }'''


class CompletionLinear(LinearWriter):
    def inspect(self, issue_id, cancel):
        def check():
            if cancel.is_set(): raise InterruptedError()
        check()
        data=self.request('query OrbitCompletionIssue($id: String!) { viewer { id } issue(id: $id) { '+ISSUE+' } }',{'id':issue_id})
        node=data.get('issue'); viewer=(data.get('viewer') or {}).get('id')
        if not isinstance(node,dict) or node.get('id')!=issue_id or not viewer:
            raise LinearError('完成前无法核对 issue 身份。')
        team=node.get('team') or {}; state=node.get('state') or {}
        if not team.get('id') or not all(name in team and (team[name] is None or type(team[name]) is bool) for name in ('autoCloseParentIssues','autoCloseChildIssues')):
            raise LinearError('无法核对团队的父子 issue 自动状态规则。')
        if not all(isinstance(state.get(name),str) and state[name] for name in ('id','name','type')):
            raise LinearError('无法核对当前工作流状态。')
        # Paginate every descendant. A completed direct child can still hide unfinished work.
        descendants=[]; queue=[issue_id]; seen={issue_id}
        for parent_id in queue:
            check()
            children=self._pages('''query OrbitCompletionChildren($id: String!, $after: String) {
              issue(id: $id) { children(first: 50, after: $after, includeArchived: true) {
                nodes { id identifier state { id type } } pageInfo { hasNextPage endCursor }
              } } }''','children',{'id':parent_id},'issue')
            for child in children:
                child_state=child.get('state') or {}
                if not child.get('id') or child['id'] in seen or not child_state.get('type'):
                    raise LinearError('子 issue 结构不完整或存在重复，未自动完成。')
                seen.add(child['id']); queue.append(child['id']); descendants.append(child)
                if len(seen)>1000: raise LinearError('子 issue 超过自动核对上限，请人工处理。')
        check()
        states=self._pages('''query OrbitCompletionStates($id: String!, $after: String) {
          team(id: $id) { states(first: 50, after: $after) {
            nodes { id name type } pageInfo { hasNextPage endCursor }
          } } }''','states',{'id':team['id']},'team')
        check()
        return dict(viewer_id=viewer,issue=node,descendants=descendants,states=states)

    def update(self, issue_id, state_id):
        result=self.request('''mutation OrbitCompleteIssue($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) { success issue { id state { id type } } }
        }''',{'id':issue_id,'input':{'stateId':state_id}}).get('issueUpdate') or {}
        if not result.get('success') or (result.get('issue') or {}).get('id')!=issue_id:
            raise LinearError('Linear 未确认完成状态；保留收尾记录，下次先读回核对。')


def identity(node):
    # Only the state may change. Concurrent scope, ownership or placement edits need review.
    return {name:(node.get(name) or '') if name in ('description','dueDate') else node.get(name)
        for name in ('id','identifier','title','description','url','dueDate','priority','estimate')} | {
        name:(node.get(name) or {}).get('id') for name in ('team','assignee','project','parent')}


def evidence(result, scope_completed):
    record=result['snapshot']['result']
    if scope_completed is not True or record['status']!='validated' or not record['completed'].strip() or not record['tests'].strip():
        raise OrbitError('completion_evidence_required','仅能收尾范围全部完成、具有验证证据且标记 validated 的结果。')
    if any(record[name].strip() for name in ('remaining','blockers','next_step')):
        raise OrbitError('completion_has_remaining','仍有剩余事项、阻碍或下一步，不能自动完成；无事项时这些字段须为空。')


def current(orbit, result, db=None):
    def inspect(database):
        task=orbit.task_row(database.execute('SELECT * FROM tasks WHERE id=?',(result['task_id'],)).fetchone())
        original=result['snapshot']['task']
        if any(task[name]!=original[name] for name in ('issue_id','work_dir','session_id','revision')):
            raise OrbitError('completion_context_changed','结果创建后任务或会话绑定已改变，不能收尾旧上下文。')
        if not task['session_id']:
            raise OrbitError('completion_chat_required','任务没有明确绑定的 Codex 会话，未修改 issue。')
        latest=database.execute('SELECT id FROM results WHERE task_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(task['id'],)).fetchone()
        if latest is None or latest['id']!=result['id']:
            raise OrbitError('completion_stale_result','任务已有更新的结果，不能根据旧结果自动收尾。')
        # Archiving one shared chat would hide unfinished work for the other task.
        other=database.execute('SELECT id FROM tasks WHERE session_id=? AND id!=?',(task['session_id'],task['id'])).fetchone()
        if other:
            raise OrbitError('completion_shared_chat','同一会话绑定多个任务，请先核对关联，未修改 issue。')
        return task
    if db is not None: return inspect(db)
    with orbit.db() as database: return inspect(database)


def check_remote(snapshot,task):
    issue=snapshot['issue']; original=task['issue']
    if (issue.get('id')!=task['issue_id'] or snapshot['viewer_id']!=original.get('viewer_id') or
            (issue.get('assignee') or {}).get('id')!=original.get('viewer_id') or
            (issue.get('team') or {}).get('id')!=original.get('team_id')):
        raise OrbitError('completion_issue_changed','远端 issue、账号、团队或负责人已改变，未继续收尾。')
    if any((issue.get(name) or '')!=(original.get(name) or '') for name in ('identifier','title','description','url')):
        raise OrbitError('completion_scope_changed','Issue 范围或说明在结果创建后已改变，请重新核对并记录验证结果。')
    if any((issue.get(remote) or {}).get('id','')!=(original.get(local) or '') for remote,local in
           (('project','project_id'),('parent','parent_issue_id'))):
        raise OrbitError('completion_scope_changed','Issue 项目或父级已改变，请重新核对任务范围。')
    unfinished=[row['identifier'] for row in snapshot['descendants'] if row['state']['type']!='completed']
    if unfinished:
        raise OrbitError('completion_open_children','存在未完成子 issue：'+'、'.join(unfinished[:20]))
    if issue['state']['type']=='canceled':
        raise OrbitError('completion_issue_canceled','Issue 已取消，不能自动改为已完成。')


def response(row):
    value={key:row[key] for key in ('result_id','task_id','state','thread_id','created_at','issue_verified_at','archived_at','last_error')}
    value['issue']=json.loads(row['after_json'] or row['before_json'])['issue']
    value['archived']=row['state']=='archived'
    if row['state']=='issue_verified':
        value['archive_request']=dict(completion_id=row['result_id'],archive_token=row['archive_token'],
            thread_id=row['thread_id'],host_id='local',archived=True,
            next_action='使用 Codex 宿主 set_thread_archived 归档准确 thread_id；在已归档清单核对后调用 confirm_chat_archived。无宿主能力时保留待归档，不改写 Codex 内部数据库。')
    return value


@contextmanager
def guard(orbit):
    from platform_support import lock_file
    orbit.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (orbit.directory/'completion.lock').open('a+') as handle:
        try: lock_file(handle)
        except OSError: raise OrbitError('completion_busy','另一个收尾正在进行，请稍后用同一结果 ID 重试。') from None
        yield


def complete(orbit,cancel,result_id,body_sha256,scope_completed,completed_state_id=''):
    with guard(orbit):
        return _complete(orbit,cancel,result_id,body_sha256,scope_completed,completed_state_id)


def _complete(orbit,cancel,result_id,body_sha256,scope_completed,completed_state_id):
    result=orbit.preview_result(result_id)
    if body_sha256!=result['body_sha256']:
        raise OrbitError('preview_mismatch','结果哈希不一致，请读取准确结果后重试。')
    evidence(result,scope_completed)
    with orbit.db() as db:
        previous=db.execute('SELECT * FROM task_completions WHERE result_id=?',(result_id,)).fetchone()
        if previous and previous['state']=='archived': return response(dict(previous))
    task=current(orbit,result)
    client=(orbit.completion_client or CompletionLinear)(orbit.credential())
    before=client.inspect(task['issue_id'],cancel);check_remote(before,task)
    node=before['issue']
    states=[row for row in before['states'] if row['type']=='completed']
    if completed_state_id:
        candidates=[row for row in states if row['id']==completed_state_id]
    elif node['state']['type']=='completed':
        candidates=[row for row in states if row['id']==node['state']['id']]
    else:
        candidates=states
    if len(candidates)!=1:
        names='、'.join(row['name']+' ('+row['id']+')' for row in states)
        raise OrbitError('completion_state_ambiguous','无法唯一确定完成状态，请指定团队中的 completed_state_id：'+(names or '没有可用完成状态'))
    target=candidates[0]['id']
    parent=node.get('parent') or {}
    if node['state']['type']!='completed' and parent and parent.get('state',{}).get('type') not in ('completed','canceled') and (parent.get('team') or {}).get('autoCloseParentIssues') is not False:
        raise OrbitError('completion_parent_automation','团队启用了自动关闭父 issue。为避免连带关单，请先在 Linear 核对该规则或人工完成。')
    stamp=now()
    with orbit.db() as db:
        current(orbit,result,db)
        row=db.execute('SELECT * FROM task_completions WHERE result_id=?',(result_id,)).fetchone()
        if row:
            previous=dict(row)
            if previous['state']=='archived': return response(previous)
            if previous['target_state_id']!=target or identity(json.loads(previous['before_json'])['issue'])!=identity(node):
                raise OrbitError('completion_issue_changed','收尾期间 issue 的目标状态或内容发生改变，请核对后创建新结果。')
            if previous['state']=='issue_verified' and node['state']['id']!=target:
                raise OrbitError('completion_reopened','已核验的 issue 被重新打开或改动，保留会话，不再次自动关单。')
        else:
            db.execute('''INSERT INTO task_completions
              (result_id,task_id,thread_id,archive_token,state,target_state_id,before_json,created_at)
              VALUES (?,?,?,?,?,?,?,?)''',(result_id,task['id'],task['session_id'],str(uuid.uuid4()),'prepared',target,json.dumps(before,ensure_ascii=False),stamp))
    try:
        orbit.check_cancel(cancel)
        if node['state']['id']!=target:
            client.update(task['issue_id'],target)
        orbit.check_cancel(cancel)
        after=client.inspect(task['issue_id'],cancel);check_remote(after,task)
        if after['issue']['state']['id']!=target or after['issue']['state']['type']!='completed' or identity(after['issue'])!=identity(node):
            raise OrbitError('completion_unverified','Linear 状态或内容读回不一致；保留会话，下次先核对同一收尾记录。')
        with orbit.db() as db:
            task=current(orbit,result,db)
            issue=dict(task['issue'],state_id=target,state_type='completed',state_name=after['issue']['state']['name'])
            db.execute('UPDATE tasks SET issue=?,updated_at=? WHERE id=?',(json.dumps(issue,ensure_ascii=False),now(),task['id']))
            db.execute("UPDATE task_completions SET state='issue_verified',after_json=?,issue_verified_at=?,last_error='' WHERE result_id=? AND state!='archived'",
                       (json.dumps(after,ensure_ascii=False),now(),result_id))
            row=dict(db.execute('SELECT * FROM task_completions WHERE result_id=?',(result_id,)).fetchone())
        return response(row)
    except BaseException as exc:
        # Never replay a comment or undo a remote status after an uncertain response.
        from .core import safe_text
        with orbit.db() as db:
            db.execute('UPDATE task_completions SET last_error=? WHERE result_id=?',(safe_text(str(exc))[:1000] or '收尾未完成，可用相同结果 ID 重试。',result_id))
        raise


def confirm_archive(orbit,result_id,archive_token,thread_id,host_verified):
    if host_verified is not True:
        raise OrbitError('archive_unverified','需要宿主归档成功，并在已归档清单找到同一会话。')
    with orbit.db() as db:
        row=db.execute('SELECT * FROM task_completions WHERE result_id=?',(result_id,)).fetchone()
        if row is None or row['state'] not in ('issue_verified','archived'):
            raise OrbitError('archive_not_ready','Linear 尚未核验完成，不能确认归档。')
        if not hmac.compare_digest(row['archive_token'],archive_token) or row['thread_id']!=thread_id:
            raise OrbitError('archive_target_mismatch','归档凭据或会话 ID 不匹配，未记录成功。')
        if row['state']!='archived':
            stored=db.execute('SELECT * FROM results WHERE id=?',(result_id,)).fetchone()
            result=dict(stored);result['snapshot']=json.loads(result['snapshot']);current(orbit,result,db)
            db.execute("UPDATE task_completions SET state='archived',archived_at=?,last_error='' WHERE result_id=?",(now(),result_id))
        return response(dict(db.execute('SELECT * FROM task_completions WHERE result_id=?',(result_id,)).fetchone()))


def list_completions(orbit,limit=20):
    import hashlib
    candidates=[]
    with orbit.db() as db:
        values=[response(dict(row)) for row in db.execute('SELECT * FROM task_completions ORDER BY created_at DESC LIMIT ?',(limit,))]
        # Find earlier verified results too; discovery alone never changes an issue or archives a chat.
        for stored in db.execute('''SELECT r.* FROM results r WHERE r.rowid=(
          SELECT latest.rowid FROM results latest WHERE latest.task_id=r.task_id
          ORDER BY latest.created_at DESC, latest.rowid DESC LIMIT 1)
          AND NOT EXISTS(SELECT 1 FROM task_completions c WHERE c.result_id=r.id)
          ORDER BY r.created_at DESC,r.rowid DESC'''):
            result=dict(stored);result['snapshot']=json.loads(result['snapshot'])
            try:
                evidence(result,True);task=current(orbit,result,db)
            except OrbitError: continue
            candidates.append(dict(result_id=result['id'],task_id=result['task_id'],thread_id=task['session_id'],
                identifier=task['issue']['identifier'],title=task['issue']['title'],
                body_sha256=hashlib.sha256(result['body'].encode()).hexdigest(),requires_scope_review=True))
            if len(candidates)>limit: break
    for value in values:
        value.pop('archive_request',None)  # A historical read cannot authorize archival after a remote reopen.
        if value['state']!='archived': value['next_tool']='complete_task'
    return {'completions':values,'candidates':candidates[:limit],'candidates_truncated':len(candidates)>limit}
