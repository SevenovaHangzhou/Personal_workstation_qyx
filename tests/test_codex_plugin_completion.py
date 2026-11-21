"""Completion must precede native archival, remain evidence-gated and survive retries."""
from copy import deepcopy
import threading
import unittest
from unittest.mock import Mock
import uuid

import test_codex_plugin as fixtures
from orbit_plugin.core import Orbit, OrbitError
from orbit_plugin.completion import CompletionLinear, guard
from linear_support import LinearError


class Remote:
    def __init__(self,issue):
        self.node=dict(id=issue['id'],identifier=issue['identifier'],title=issue['title'],description=issue['description'],
            url=issue['url'],dueDate=None,priority=0,estimate=None,state=dict(id='started',name='进行中',type='started'),
            team=dict(id='team',autoCloseParentIssues=False,autoCloseChildIssues=False),assignee=dict(id='person'),project=None,parent=None)
        self.states=[dict(id='done',name='已完成',type='completed')]
        self.children=[];self.calls=[];self.failure=None;self.after=None

    def inspect(self,issue_id,cancel):
        self.calls.append('inspect')
        if cancel.is_set(): raise InterruptedError()
        if self.after and 'update' in self.calls: self.after(self)
        return deepcopy(dict(viewer_id='person',issue=self.node,descendants=self.children,states=self.states))

    def update(self,issue_id,state_id):
        self.calls.append('update')
        self.node['state']=dict(next(row for row in self.states if row['id']==state_id))
        if self.failure:
            exc=self.failure;self.failure=None;raise exc


class CompletionTests(unittest.TestCase):
    invoke=fixtures.CodexPluginTests.invoke
    start=fixtures.CodexPluginTests.start
    draft=fixtures.CodexPluginTests.draft

    def setUp(self):
        fixtures.CodexPluginTests.setUp(self)
        self.issue['team_id']='team'
        self.thread=str(uuid.uuid4())
        self.task=self.start(session_id=self.thread)
        self.result=self.draft(self.task,status='validated',tests='python -m unittest: 8 passed；接口回归通过。',remaining='',blockers='',next_step='')
        self.remote=Remote(self.issue)
        self.factory=Mock(return_value=self.remote);self.orbit.completion_client=self.factory

    def complete(self,**options):
        args=dict(result_id=self.result['id'],body_sha256=self.result['body_sha256'],scope_completed=True)
        args.update(options);return self.invoke('complete_task',**args)

    def confirm(self,done,**options):
        args=dict(result_id=self.result['id'],archive_token=done['archive_request']['archive_token'],thread_id=self.thread,host_verified=True)
        args.update(options);return self.invoke('confirm_chat_archived',**args)

    def test_completion_reads_back_before_requesting_exact_archive(self):
        done=self.complete()
        self.assertEqual(self.remote.calls,['inspect','update','inspect'])
        self.assertEqual(done['state'],'issue_verified');self.assertFalse(done['archived'])
        self.assertEqual(done['archive_request']['thread_id'],self.thread)
        self.assertEqual(self.orbit.get_task(self.task['id'])['issue']['state_type'],'completed')
        self.writer.assert_not_called()  # Status completion is not comment publication.
        self.assertEqual(self.orbit.preview_result(self.result['id'])['state'],'draft')
        archived=self.confirm(done)
        self.assertTrue(archived['archived']);self.assertTrue(archived['archived_at'])
        self.assertEqual(self.confirm(done),archived)
        before=len(self.remote.calls)
        self.assertEqual(self.complete()['state'],'archived')
        self.assertEqual(len(self.remote.calls),before)

    def test_partial_or_unvalidated_results_and_wrong_hash_never_contact_linear(self):
        with self.assertRaisesRegex(OrbitError,'哈希'): self.complete(body_sha256='0'*64)
        for options in (dict(status='implemented'),dict(remaining='仍需实机验证'),dict(blockers='等待权限'),dict(next_step='补测接口')):
            arguments=dict(status='validated',remaining='',blockers='',next_step='')|options
            self.result=self.draft(self.task,**arguments)
            with self.assertRaises(OrbitError): self.complete()
        self.factory.assert_not_called()

    def test_old_result_or_rebound_chat_never_mutates(self):
        self.draft(self.task,completed='新增后续需求')
        with self.assertRaisesRegex(OrbitError,'更新'): self.complete()
        self.factory.assert_not_called()
        self.result=self.draft(self.task,status='validated',completed='新需求也验证完成',remaining='',blockers='',next_step='')
        self.start(session_id=str(uuid.uuid4()),replace_context=True)
        with self.assertRaisesRegex(OrbitError,'绑定'): self.complete()
        self.factory.assert_not_called()

    def test_missing_or_shared_chat_blocks_closing(self):
        with self.orbit.db() as db:
            db.execute('''INSERT INTO tasks (id,issue_id,issue,work_dir,session_id,revision,created_at,updated_at)
              SELECT ?,?,issue,work_dir,session_id,revision,created_at,updated_at FROM tasks WHERE id=?''',
              (str(uuid.uuid4()),str(uuid.uuid4()),self.task['id']))
        with self.assertRaisesRegex(OrbitError,'多个任务'): self.complete()
        self.factory.assert_not_called()
        with self.orbit.db() as db:
            db.execute('UPDATE tasks SET session_id=? WHERE id=?',('',self.task['id']))
        self.result=self.draft(self.orbit.get_task(self.task['id']),status='validated',remaining='',blockers='',next_step='')
        with self.assertRaisesRegex(OrbitError,'会话'): self.complete()
        self.factory.assert_not_called()

    def test_owner_team_or_canceled_issue_blocks_closing(self):
        original=deepcopy(self.remote.node)
        for change in (dict(assignee=dict(id='other')),dict(team=dict(id='other')),dict(state=dict(id='cancel',name='取消',type='canceled'))):
            self.remote.node=deepcopy(original)|change
            with self.assertRaises(OrbitError): self.complete()
            self.assertNotIn('update',self.remote.calls)

    def test_unfinished_descendants_block_even_when_direct_child_is_done(self):
        self.remote.children=[dict(id='child',identifier='ENG-124',state=dict(type='completed')),
                              dict(id='grandchild',identifier='ENG-125',state=dict(type='started'))]
        with self.assertRaisesRegex(OrbitError,'ENG-125'): self.complete()
        self.assertNotIn('update',self.remote.calls)

    def test_parent_team_automation_is_checked_without_guessing(self):
        self.remote.node['parent']=dict(id='parent',state=dict(type='started'),team=dict(id='parent-team',autoCloseParentIssues=True))
        self.issue['parent_issue_id']='parent'
        with self.orbit.db() as db:
            import json
            db.execute('UPDATE tasks SET issue=? WHERE id=?',(json.dumps(self.issue),self.task['id']))
        self.result=self.draft(self.orbit.get_task(self.task['id']),status='validated',remaining='',blockers='',next_step='')
        with self.assertRaisesRegex(OrbitError,'父 issue'): self.complete()
        self.assertNotIn('update',self.remote.calls)
        self.remote.node['parent']['team']['autoCloseParentIssues']=None
        with self.assertRaises(OrbitError): self.complete()
        self.remote.node['parent']['team']['autoCloseParentIssues']=False
        self.assertEqual(self.complete()['state'],'issue_verified')

    def test_changed_issue_scope_is_not_closed_based_on_old_validation(self):
        self.remote.node['description']='新增了尚未实现的要求'
        with self.assertRaisesRegex(OrbitError,'范围'): self.complete()
        self.assertNotIn('update',self.remote.calls)

    def test_nullable_linear_description_matches_empty_local_description(self):
        self.issue['description']=''
        self.task=self.start(session_id=self.thread)
        self.result=self.draft(self.task,status='validated',tests='python -m unittest: 8 passed',remaining='',blockers='',next_step='')
        self.remote.node['description']=None
        self.assertEqual(self.complete()['state'],'issue_verified')

    def test_multiple_completed_states_require_explicit_selection(self):
        self.remote.states.append(dict(id='released',name='已发布',type='completed'))
        with self.assertRaisesRegex(OrbitError,'completed_state_id'): self.complete()
        with self.assertRaises(OrbitError): self.complete(completed_state_id='started')
        self.assertNotIn('update',self.remote.calls)
        self.assertEqual(self.complete(completed_state_id='released')['issue']['state']['id'],'released')

    def test_uncertain_mutation_is_persisted_and_restart_reads_before_retry(self):
        self.remote.failure=TimeoutError('response lost after successful status update')
        with self.assertRaises(TimeoutError): self.complete()
        pending=self.orbit.list_completions()['completions'][0]
        self.assertEqual(pending['state'],'prepared');self.assertTrue(pending['last_error'])
        self.assertNotIn('archive_request',pending)
        self.orbit=Orbit(self.orbit.directory,completion_client=self.factory)
        self.orbit.credential=Mock(return_value='test-key')
        self.assertEqual(self.complete()['state'],'issue_verified')
        self.assertEqual(self.remote.calls.count('update'),1)

    def test_wrong_readback_or_late_child_retains_chat(self):
        def edit(remote): remote.node['description']='范围已由用户修改'
        self.remote.after=edit
        with self.assertRaises(OrbitError): self.complete()
        pending=self.orbit.list_completions()['completions'][0]
        self.assertEqual(pending['state'],'prepared');self.assertNotIn('archive_request',pending)
        self.remote.after=None
        self.remote.node['description']=self.issue['description']
        self.remote.after=lambda remote:remote.children.append(dict(id='late',identifier='ENG-999',state=dict(type='started')))
        with self.assertRaisesRegex(OrbitError,'ENG-999'): self.complete()

    def test_reopened_issue_is_not_closed_again_on_archive_retry(self):
        self.complete();self.remote.node['state']=dict(id='started',name='进行中',type='started')
        with self.assertRaisesRegex(OrbitError,'重新打开'): self.complete()
        self.assertEqual(self.remote.calls.count('update'),1)
        self.assertNotIn('archive_request',self.orbit.list_completions()['completions'][0])

    def test_cancel_after_update_does_not_allow_archival(self):
        original=self.remote.update
        def update(*args): original(*args);self.cancel.set()
        self.remote.update=update
        with self.assertRaises(InterruptedError): self.complete()
        self.cancel.clear()
        self.assertEqual(self.orbit.list_completions()['completions'][0]['state'],'prepared')
        self.assertEqual(self.complete()['state'],'issue_verified')
        self.assertEqual(self.remote.calls.count('update'),1)

    def test_archive_confirmation_requires_ticket_target_and_host_readback(self):
        with self.assertRaises(OrbitError):
            self.invoke('confirm_chat_archived',result_id=self.result['id'],archive_token='fake',thread_id=self.thread,host_verified=True)
        done=self.complete()
        for options in (dict(archive_token='wrong'),dict(thread_id='another'),dict(host_verified=False)):
            with self.assertRaises(OrbitError): self.confirm(done,**options)
        self.assertEqual(self.orbit.list_completions()['completions'][0]['state'],'issue_verified')
        self.assertEqual(self.confirm(done)['state'],'archived')

    def test_local_completion_lock_prevents_concurrent_network_operations(self):
        with guard(self.orbit):
            with self.assertRaisesRegex(OrbitError,'另一个'): self.complete()
        self.factory.assert_not_called()

    def test_historical_candidates_are_read_only_and_keep_exact_result(self):
        listed=self.invoke('list_completions')
        self.assertEqual(listed['candidates'][0]['result_id'],self.result['id'])
        self.assertEqual(listed['candidates'][0]['body_sha256'],self.result['body_sha256'])
        self.assertTrue(listed['candidates'][0]['requires_scope_review'])
        self.factory.assert_not_called()
        self.complete()
        self.assertEqual(self.invoke('list_completions')['candidates'],[])

    def test_candidate_discovery_does_not_use_an_older_verified_result(self):
        self.draft(self.task,status='blocked',completed='发现新问题',blockers='仍需修复')
        self.assertEqual(self.invoke('list_completions')['candidates'],[])
        self.factory.assert_not_called()


class CompletionTransportTests(unittest.TestCase):
    def test_all_child_pages_and_descendants_are_checked_and_only_state_is_written(self):
        original=dict(id='issue',identifier='ENG-1',state=dict(id='started',name='开始',type='started'),
            team=dict(id='team',autoCloseParentIssues=None,autoCloseChildIssues=None))
        calls=[]
        def request(query,variables):
            calls.append((query,variables))
            if 'OrbitCompletionIssue' in query:return dict(viewer=dict(id='person'),issue=deepcopy(original))
            if 'OrbitCompletionStates' in query:return dict(team=dict(states=dict(nodes=[dict(id='done',name='完成',type='completed')],pageInfo=dict(hasNextPage=False,endCursor=None))))
            if 'OrbitCompleteIssue' in query:return dict(issueUpdate=dict(success=True,issue=dict(id='issue')))
            issue_id=variables['id'];after=variables.get('after');children=[];more=False
            if issue_id=='issue':
                children=[dict(id='second' if after else 'first',identifier='ENG-2',state=dict(id='done',type='completed'))];more=not after
            elif issue_id=='first':children=[dict(id='grandchild',identifier='ENG-3',state=dict(id='started',type='started'))]
            return dict(issue=dict(children=dict(nodes=children,pageInfo=dict(hasNextPage=more,endCursor='next' if more else None))))
        client=CompletionLinear('test-key');client.request=Mock(side_effect=request)
        result=client.inspect('issue',threading.Event())
        self.assertEqual({row['id'] for row in result['descendants']},{'first','second','grandchild'})
        self.assertEqual(sum('OrbitCompletionChildren' in query for query,_ in calls),5)
        client.update('issue','done')
        self.assertEqual(calls[-1][1],dict(id='issue',input={'stateId':'done'}))

    def test_failed_mutation_and_unknown_automation_shape_are_not_accepted(self):
        client=CompletionLinear('test-key');client.request=Mock(return_value={'issueUpdate':{'success':False}})
        with self.assertRaises(LinearError):client.update('issue','done')
        client.request=Mock(return_value=dict(viewer=dict(id='person'),issue=dict(id='issue',team=dict(id='team'))))
        with self.assertRaises(LinearError):client.inspect('issue',threading.Event())


if __name__=='__main__':unittest.main()
