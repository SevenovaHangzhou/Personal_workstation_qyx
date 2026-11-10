import json
import tempfile
import threading
import unittest
import tomllib
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock,patch
from daily_summary import disabled_mcp_arguments,CodexSummarizer
from daily_store import DailyStore
from daily_engine import DailyEngine
from runtime import MomoRuntime
from model import make_task


class DailyRepairTests(unittest.TestCase):
    def test_inline_table_handles_server_names_and_does_not_copy_credentials(self):
        names=['normal','with.dot','with space','with"quote','中文']
        servers={name:{'command':'server','env':{'TOKEN':'private'},'args':['private']} for name in names}
        servers['http']={'url':'https://example.invalid/mcp','http_headers':{'Authorization':'private'}}
        args=disabled_mcp_arguments(servers)
        self.assertEqual(args[0],'-c');self.assertNotIn('private',args[1])
        parsed=tomllib.loads(args[1])['mcp_servers']
        self.assertEqual(set(parsed),set(servers))
        self.assertTrue(all(row['enabled'] is False for row in parsed.values()))
        self.assertEqual(parsed['http']['url'],servers['http']['url'])
        for invalid in (None,{'bad':{}},{'bad':'value'}):
            with self.assertRaises(RuntimeError):disabled_mcp_arguments(invalid)

    def test_stderr_is_shown_redacted_and_failed_output_is_not_cached(self):
        with tempfile.TemporaryDirectory() as temp:
            store=DailyStore(Path(temp)/'daily');binary=Path(temp)/'codex'
            binary.write_text('#!/bin/sh\necho \'invalid transport sk-secret123456789\' >&2\nexit 4\n');binary.chmod(0o700)
            rpc=Mock();rpc.return_value.__enter__=Mock(return_value=rpc);rpc.return_value.__exit__=Mock(return_value=False)
            rpc.request.return_value={'config':{'mcp_servers':{}}}
            try:
                with patch('daily_summary.find_codex',return_value=str(binary)),patch('daily_summary.CodexRpc',return_value=rpc.return_value):
                    rpc.return_value.request.return_value={'config':{'mcp_servers':{}}}
                    with self.assertRaisesRegex(RuntimeError,'退出码 4') as raised:
                        CodexSummarizer(store)._compress('聊天记录',threading.Event())
                self.assertIn('invalid transport',str(raised.exception));self.assertNotIn('secret123456789',str(raised.exception))
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM summaries').fetchone()[0],0)
            finally:store.close()

    def test_cross_team_parent_sync_preserves_issue_and_creates_children_in_parent_team(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime=MomoRuntime(Path(temp)/'tasks.json',auto_tick=False).start()
            try:
                runtime.tasks.clear_samples();parent=runtime.tasks.add(make_task('父任务'));child=runtime.tasks.add(make_task('跨团队子任务'))
                def issue(n,team,parent_id=''):
                    return dict(id='id-'+str(n),identifier='APP-'+str(n),title='工作',url=f'https://linear.app/test/issue/APP-{n}/task',
                        state_type='unstarted',state_name='Todo',viewer_id='me',team_id=team,project_id='',parent_issue_id=parent_id)
                a,b=issue(1,'team-a'),issue(2,'team-b')
                runtime.tasks.attach_issue(parent['id'],a);runtime.tasks.attach_issue(child['id'],b)
                runtime.tasks.merge(child['id'],parent['id'])
                grandchild=runtime.tasks.add_children(child['id'],['未建 issue 的孙步骤'])[0]
                writer=Mock();writer.issue.side_effect=lambda key:a if key==a['identifier'] else b
                writer.ensure_parent.return_value=issue(2,'team-b',a['id'])
                writer.choices.return_value={'viewer':{'id':'me'},'projects':[]}
                writer.ensure_issue.side_effect=lambda op:issue(3,'team-b',b['id'])
                engine=DailyEngine(runtime.daily_service.store,runtime.tasks,lambda:writer,runtime.station)
                engine.sync_cutoff=datetime.now(timezone.utc)+timedelta(days=1)
                updated=engine.synchronize(child['id'],{},threading.Event())
                self.assertEqual(updated['team_id'],'team-b');self.assertEqual(updated['identifier'],b['identifier'])
                self.assertNotIn('linear_parent_pending',runtime.tasks.get(child['id']))
                self.assertEqual(writer.ensure_parent.call_args.args[0]['payload'],{'issue_id':b['id'],'parent_id':a['id']})
                engine.synchronize(grandchild['id'],{},threading.Event())
                payload=writer.ensure_issue.call_args.args[0]['payload'];self.assertEqual(payload['teamId'],'team-b');self.assertEqual(payload['parentId'],b['id'])
            finally:runtime.close()
