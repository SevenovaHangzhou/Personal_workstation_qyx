import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from daily_store import DailyStore
from daily_history import HistoryIndex, cutoff, latest_due
from daily_engine import DailyEngine
from daily_git import daily_snapshot
from model import JsonTaskRepository, make_task, parse_binding
from runtime import MomoRuntime
from linear_support import LinearWriter, LinearError


def log(path,thread='chat-one'):
    path.parent.mkdir(parents=True,exist_ok=True)
    records=[{'timestamp':'2026-10-26T13:59:00Z','type':'session_meta','payload':{'id':thread,'source':'vscode'}}]
    for stamp,role,text in [('2026-10-26T14:00:00Z','user','周期开始'),('2026-10-27T13:59:59Z','assistant','已运行验证'),('2026-10-27T14:00:00Z','user','下期聊天')]:
        records.append({'timestamp':stamp,'type':'response_item','payload':{'type':'message','role':role,'content':[{'type':'input_text','text':text}]}})
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))


class DailyStoreHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=DailyStore(self.root/'daily');self.cancel=threading.Event()
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_22_hour_boundary_incremental_archive_and_partial_record(self):
        path=self.root/'codex/sessions/old/chat.jsonl';log(path);index=HistoryIndex(self.store,self.root/'codex')
        self.assertEqual(index.scan(self.cancel),[]);groups=index.between(cutoff('2026-10-26'),cutoff('2026-10-27'));self.assertEqual(len(groups['chat-one']),2)
        with path.open('a') as f:f.write('{"timestamp":')
        self.assertEqual(index.scan(self.cancel),[])
        archived=self.root/'codex/archived_sessions/copy.jsonl';archived.parent.mkdir(parents=True);shutil.copyfile(path,archived)
        self.assertEqual(index.scan(self.cancel),[]);self.assertEqual(index.between(cutoff('2026-10-26'),cutoff('2026-10-27')),groups)
        self.assertEqual(latest_due(datetime.fromisoformat('2026-10-27T21:59:59+08:00')),'2026-10-26')
        self.assertEqual(latest_due(datetime.fromisoformat('2026-10-27T22:00:00+08:00')),'2026-10-27')
    def test_operation_uuid_survives_restart_and_keeps_original_payload(self):
        op=self.store.operation('issue:one',{'title':'原题'});self.store.close();self.store=DailyStore(self.root/'daily')
        retry=self.store.operation('issue:one',{'title':'新题'});self.assertEqual(op,retry);self.assertEqual(retry['id'].split('-')[2][0],'4')
    def test_redaction_ignores_tool_output(self):
        path=self.root/'codex/sessions/test.jsonl';log(path)
        records=[{'timestamp':'2026-10-27T13:00:00Z','type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'lin_api_fake_secret_string_for_test'}]}},
                 {'timestamp':'2026-10-27T13:00:00Z','type':'response_item','payload':{'type':'function_call_output','output':'TOOL_SECRET'}}]
        with path.open('a') as f:f.write(''.join(json.dumps(r)+'\n' for r in records))
        index=HistoryIndex(self.store,self.root/'codex');index.scan(self.cancel);result=str(index.between(cutoff('2026-10-26'),cutoff('2026-10-27')))
        self.assertNotIn('fake_secret_string',result);self.assertNotIn('TOOL_SECRET',result)


class TaskTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'tasks.json';self.runtime=MomoRuntime(self.path,auto_tick=False).start();self.tasks=self.runtime.tasks;self.tasks.clear_samples()
    def tearDown(self):self.runtime.close();self.temp.cleanup()
    def test_draft_children_and_one_issue_per_task(self):
        root=make_task('工作父项');self.tasks.add(root);children=self.tasks.add_children(root['id'],['实现','验证'])
        self.assertEqual(len(children),2);self.assertTrue(all(c['parent_id']==root['id'] for c in children))
        url='https://linear.app/test/issue/APP-1/item';self.tasks.bind(root['id'],'linear',url)
        with self.assertRaisesRegex(ValueError,'一一对应'):self.tasks.bind(root['id'],'linear','https://linear.app/test/issue/APP-2/item')
        with self.assertRaisesRegex(ValueError,'已对应'):self.tasks.bind(children[0]['id'],'linear',url)
        self.tasks.set_step(root['id'],0,True);self.assertEqual(self.tasks.get(children[0]['id'])['status'],'done');self.assertEqual(self.tasks.get(root['id'])['status'],'todo')
        with self.assertRaisesRegex(ValueError,'先删除子任务'):self.tasks.delete(root['id'])
    def test_v1_migration_backup_and_idempotency(self):
        self.runtime.close();task=make_task('旧任务','learn',next_step='下一步');task['steps']=[{'title':'步骤','done':True}]
        original=json.dumps({'version':1,'sample':False,'tasks':[task]},ensure_ascii=False);self.path.write_text(original)
        repo=JsonTaskRepository(self.path);self.assertEqual(len(repo.tasks),3);self.assertEqual(self.path.with_name('tasks.json.v1-backup').read_text(),original)
        self.assertEqual(JsonTaskRepository(self.path).tasks,repo.tasks);self.assertEqual(repo.get(task['id'])['next_step'],'')
        self.runtime=MomoRuntime(self.path,auto_tick=False).start()


class GitSnapshotTests(unittest.TestCase):
    def test_real_snapshot_restores_index_worktree_untracked_without_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=root/'repo';repo.mkdir()
            def git(*args):return subprocess.check_output(['git','-C',str(repo),*args])
            git('init','-q');git('config','user.name','Test');git('config','user.email','test@example.invalid')
            (repo/'file.txt').write_text('base\n');git('add','.');git('commit','-qm','initial')
            (repo/'file.txt').write_text('staged\n');git('add','file.txt');(repo/'file.txt').write_text('working\n');(repo/'new.bin').write_bytes(b'\x00\x01test')
            (repo/'ignored.secret').write_text('ignored');(repo/'.git/info/exclude').write_text('ignored.secret\n')
            before=(git('symbolic-ref','HEAD'),git('rev-parse','HEAD'),git('diff','--cached'),git('status','--porcelain'))
            task=make_task('代码');task['bindings']=[parse_binding('linear','https://linear.app/test/issue/APP-1/code')]
            result=daily_snapshot(repo,root/'snapshots','2026-10-27',task,threading.Event());folder=Path(result['path'])
            self.assertEqual(before,(git('symbolic-ref','HEAD'),git('rev-parse','HEAD'),git('diff','--cached'),git('status','--porcelain')))
            self.assertEqual(git('rev-parse',result['branch']),before[1]);self.assertFalse(git('tag').strip())
            restore=root/'restore';subprocess.check_call(['git','clone','-q',str(repo),str(restore)])
            subprocess.check_call(['git','-C',str(restore),'apply','--index',str(folder/'staged.patch')]);subprocess.check_call(['git','-C',str(restore),'apply',str(folder/'working.patch')])
            with tarfile.open(folder/'untracked.tar') as archive:
                self.assertNotIn('ignored.secret',archive.getnames());archive.extractall(restore,filter='data')
            self.assertEqual((restore/'file.txt').read_text(),'working\n');self.assertEqual((restore/'new.bin').read_bytes(),b'\x00\x01test')
            self.assertEqual(daily_snapshot(repo,root/'snapshots','2026-10-27',task,threading.Event())['path'],result['path'])


class LinearIdempotencyTests(unittest.TestCase):
    def test_lost_comment_response_reconciles_without_second_mutation(self):
        writer=LinearWriter('fixture');op={'id':'saved-uuid','payload':{'issueId':'issue-1','body':'日报'}}
        with patch.object(writer,'request',return_value={'comments':{'nodes':[{'id':'saved-uuid','body':'日报','issue':{'id':'issue-1'}}]}}) as request:
            self.assertEqual(writer.ensure_comment(op),{'id':'saved-uuid'});self.assertEqual(request.call_count,1)
    def test_changed_remote_comment_is_preserved(self):
        writer=LinearWriter('fixture');op={'id':'saved-uuid','payload':{'issueId':'issue-1','body':'日报'}}
        with patch.object(writer,'request',return_value={'comments':{'nodes':[{'id':'saved-uuid','body':'手工编辑','issue':{'id':'issue-1'}}]}}):
            with self.assertRaisesRegex(LinearError,'人工核实'):writer.ensure_comment(op)


class DailyEngineTests(unittest.TestCase):
    def test_nonwork_unbound_stay_local_and_failed_comment_reuses_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);runtime=MomoRuntime(root/'tasks.json',auto_tick=False).start();store=runtime.daily_service.store
            try:
                runtime.tasks.clear_samples();work=make_task('工作');work['created_at']='2026-10-26T00:00:00+00:00';runtime.tasks.add(work)
                runtime.tasks.bind(work['id'],'codex','codex://threads/chat-one')
                learn=make_task('学习','learn');learn['created_at']=work['created_at'];runtime.tasks.add(learn);runtime.tasks.bind(learn['id'],'codex','codex://threads/chat-study')
                for name in ('chat-one','chat-study','chat-other'):log(root/('codex/sessions/'+name+'.jsonl'),name)
                writer=Mock();writer.choices.return_value={'viewer':{'id':'me'},'projects':[]}
                def ensure(op):return {'id':op['id'],'identifier':'APP-1','title':'工作','url':'https://linear.app/test/issue/APP-1/work','viewer_id':'me','state_type':'unstarted','state_name':'Todo','team_id':'team','project_id':'','parent_issue_id':''}
                writer.ensure_issue.side_effect=ensure;writer.issue.side_effect=lambda identifier:runtime.tasks.get(work['id'])['linear_issue'];writer.ensure_comment.side_effect=[RuntimeError('timeout'),{'id':'comment'}]
                summarizer=Mock();summarizer.summarize.return_value='已实现并验证'
                engine=DailyEngine(store,runtime.tasks,lambda:writer,runtime.station,HistoryIndex(store,root/'codex'),summarizer)
                first=engine.run('2026-10-27',{'target':{'team_id':'team'}},threading.Event());self.assertEqual(first['state'],'pending')
                second=engine.run('2026-10-27',{'target':{'team_id':'team'}},threading.Event());self.assertEqual(second['state'],'done')
                self.assertEqual(writer.ensure_issue.call_count,1);self.assertEqual(summarizer.summarize.call_count,3)
                self.assertEqual(writer.ensure_comment.call_args_list[0].args[0]['id'],writer.ensure_comment.call_args_list[1].args[0]['id'])
                body=writer.ensure_comment.call_args.args[0]['payload']['body'];self.assertIn('chat-one',body);self.assertNotIn('chat-study',body);self.assertNotIn('chat-other',body)
            finally:runtime.close()

class DailySchedulingTests(unittest.TestCase):
    def test_missed_dates_are_processed_and_cursor_survives_restart(self):
        from datetime import timezone
        from services import DailyService as module
        class Frozen(datetime):
            @classmethod
            def now(cls,tz=None):return datetime(2026,10,28,0,0,tzinfo=timezone.utc).astimezone(tz)
        with tempfile.TemporaryDirectory() as temp:
            runtime=MomoRuntime(Path(temp)/'tasks.json',auto_tick=False).start();service=runtime.daily_service
            service.store.put('config',{'enabled':True,'activated':True,'target':{'team_id':'team'},'next_day':'2026-10-26'})
            service.engine=Mock();done=threading.Event()
            def run(day,config,cancel):
                if day=='2026-10-27':done.set()
            service.engine.run.side_effect=run
            try:
                with patch.object(module,'datetime',Frozen):
                    service.auto_tick=True;service.start_clock();self.assertTrue(done.wait(3));service.stop_clock()
                self.assertEqual([c.args[0] for c in service.engine.run.call_args_list],['2026-10-26','2026-10-27'])
                self.assertEqual(service.store.get('config')['next_day'],'2026-10-28')
            finally:runtime.close()
    def test_child_issue_inherits_parent_team_project_and_assigns_viewer(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime=MomoRuntime(Path(temp)/'tasks.json',auto_tick=False).start()
            try:
                runtime.tasks.clear_samples();root=make_task('父任务');root['created_at']='2026-10-26T00:00:00+00:00'
                root['linear_target']={'team_id':'parent-team','project_id':'parent-project'};runtime.tasks.add(root)
                child=make_task('子任务');child['created_at']=root['created_at'];child['parent_id']=root['id'];runtime.tasks.add(child)
                writer=Mock();writer.choices.return_value={'viewer':{'id':'me'},'projects':[{'id':'parent-project'}]}
                serial=[]
                def issue(op):
                    serial.append(op)
                    n=len(serial)
                    return {'id':op['id'],'identifier':'APP-'+str(n),'title':'工作','url':'https://linear.app/test/issue/APP-'+str(n)+'/work','viewer_id':'me','state_type':'unstarted','state_name':'Todo','team_id':'parent-team','project_id':'parent-project','parent_issue_id':op['payload'].get('parentId','')}
                writer.ensure_issue.side_effect=issue
                engine=runtime.daily_service.engine;engine.writer_factory=lambda:writer
                first=engine.synchronize(root['id'],{'target':{'team_id':'default'}},threading.Event())
                second=engine.synchronize(child['id'],{'target':{'team_id':'default'}},threading.Event())
                self.assertEqual(second['parent_issue_id'],first['id']);self.assertEqual(serial[1]['payload']['assigneeId'],'me')
                self.assertEqual(serial[1]['payload']['projectId'],'parent-project');self.assertEqual(writer.choices.call_count,1)
            finally:runtime.close()
    def test_history_format_errors_persist_until_source_is_fixed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store=DailyStore(root/'daily');path=root/'codex/sessions/chat.jsonl';log(path)
            try:
                with path.open('a') as f:f.write('{bad-json}\n')
                index=HistoryIndex(store,root/'codex');self.assertTrue(index.scan(threading.Event()));self.assertTrue(index.scan(threading.Event()))
            finally:store.close()

class SummaryTransportTests(unittest.TestCase):
    def test_only_compression_uses_cli_ephemeral_and_cached_with_mcp_disabled(self):
        import sys
        from daily_summary import CodexSummarizer
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store=DailyStore(root/'daily');binary=root/'fake-codex'
            binary.write_text('#!'+sys.executable+'\nimport os,sys\nfrom pathlib import Path\nassert "--ephemeral" in sys.argv\nassert \'mcp_servers."test-server".enabled=false\' in sys.argv\nassert "LINEAR_API_KEY" not in os.environ\nassert os.environ["OPENAI_API_KEY"]=="existing-fixture-auth"\nsys.stdin.read()\nPath(sys.argv[sys.argv.index("-o")+1]).write_text("压缩摘要")\n')
            binary.chmod(0o755);rpc=MagicMock();rpc.return_value.__enter__.return_value.request.return_value={'config':{'mcp_servers':{'test-server':{'enabled':True}}}}
            try:
                with patch('daily_summary.shutil.which',return_value=str(binary)),patch('daily_summary.CodexRpc',rpc),patch.dict(os.environ,{'LINEAR_API_KEY':'fixture','OPENAI_API_KEY':'existing-fixture-auth'}):
                    messages=[{'timestamp':'2026-10-27T13:00:00Z','role':'user','text':'已完成实现'}]
                    summarizer=CodexSummarizer(store);self.assertEqual(summarizer.summarize(messages,threading.Event()),'压缩摘要')
                    self.assertEqual(summarizer.summarize(messages,threading.Event()),'压缩摘要');self.assertEqual(rpc.call_count,1)
            finally:store.close()


class DataDirectoryLockTests(unittest.TestCase):
    def test_second_process_is_rejected_before_v1_migration(self):
        import fcntl
        import sys
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"tasks.json";task=make_task("不能被另一实例迁移","learn")
            original=json.dumps({"version":1,"sample":False,"tasks":[task]});path.write_text(original)
            with (Path(temp)/"momo.lock").open("a+") as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                program="from runtime import MomoRuntime; import sys; MomoRuntime(sys.argv[1],auto_tick=False).start()"
                result=subprocess.run([sys.executable,"-c",program,str(path)],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=15)
                self.assertNotEqual(result.returncode,0);self.assertIn("已在运行",result.stderr)
                self.assertEqual(path.read_text(),original);self.assertFalse(path.with_name("tasks.json.v1-backup").exists())
