import json
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from model import make_task
from daily_history import ZONE
from work_logs import WorkLogs,period
from runtime import MomoRuntime


def write_chat(root,thread,rows):
    path=root/'sessions'/('chat-'+thread+'.jsonl');path.parent.mkdir(parents=True,exist_ok=True)
    records=[{'type':'session_meta','payload':{'id':thread,'source':'cli'}}]
    for stamp,text in rows:
        records.append({'timestamp':stamp,'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':text}]}})
    path.write_text(''.join(json.dumps(r)+'\n' for r in records))


def wait(manager):
    deadline=time.monotonic()+4
    while manager.snapshot()['busy'] and time.monotonic()<deadline:time.sleep(.01)
    if manager.snapshot()['busy']:raise AssertionError('work log did not finish')


class WorkLogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.task=make_task('工作记录','work');self.task['bindings']=[{'kind':'codex','url':'codex://threads/work-chat','label':'工作'}]
        self.study=make_task('学习课程','learn');self.study['bindings']=[{'kind':'codex','url':'codex://threads/study-chat','label':'学习'}]
        self.tasks=SimpleNamespace(tasks=[self.task,self.study]);self.summary=Mock();self.summary.summarize.side_effect=lambda messages,cancel:'已核验：'+'、'.join(m['text'] for m in messages)
        self.manager=WorkLogs(self.root/'daily/work-logs',self.tasks,summarizer=self.summary)
        self.manager.history.root=self.root/'codex';self.now=datetime(2026,11,11,18,tzinfo=ZONE)
        write_chat(self.root/'codex','work-chat',[
            ('2026-11-08T15:59:59Z','周日前的旧内容'),('2026-11-08T16:00:00Z','周一零点验证'),
            ('2026-11-10T16:00:00Z','今日零点验证'),('2026-11-11T09:00:00Z','今日实验'),('2026-11-11T10:00:00Z','截止后的聊天')])
        write_chat(self.root/'codex','study-chat',[('2026-11-11T09:00:00Z','不能混入学习聊天')])
        write_chat(self.root/'codex','unbound-chat',[('2026-11-11T09:00:00Z','不能混入未绑定聊天')])
    def tearDown(self):self.manager.close();self.temp.cleanup()

    def test_day_week_boundaries_and_widening_reindexes_history(self):
        start,end=period('week',self.now);self.assertEqual(start.isoformat(),'2026-11-09T00:00:00+08:00');self.assertEqual(end,self.now)
        day=self.manager.start('today',self.now)['id'];wait(self.manager)
        text=self.manager.text(day);self.assertIn('今日零点验证',text);self.assertNotIn('周一零点验证',text)
        week=self.manager.start('week',self.now)['id'];wait(self.manager)
        text=self.manager.text(week);self.assertIn('周一零点验证',text);self.assertNotIn('周日前的旧内容',text)
        for excluded in ('不能混入学习聊天','不能混入未绑定聊天','截止后的聊天'):self.assertNotIn(excluded,text)
        self.assertEqual(set(self.manager.store.report(week)['content']['groups']),{'work-chat'})
        self.assertEqual(self.manager.snapshot()['rows'][0]['state'],'done')
        with self.assertRaises(ValueError):period('month',self.now)
        with self.assertRaises(ValueError):period('today',datetime(2026,11,11))

    def test_duplicate_clicks_share_one_job_cancel_and_retry_keep_cutoff(self):
        entered=threading.Event()
        def block(messages,cancel):entered.set();cancel.wait(3);raise InterruptedError()
        self.summary.summarize.side_effect=block
        identifier=self.manager.start('today',self.now)['id'];self.assertTrue(entered.wait(2))
        self.assertEqual(self.manager.start('today',self.now)['id'],identifier)
        self.assertTrue(self.manager.start('week',self.now)['existing'])
        self.manager.cancel_active();wait(self.manager)
        self.assertEqual(self.manager.snapshot()['rows'][0]['state'],'cancelled')
        self.summary.summarize.side_effect=None;self.summary.summarize.return_value='重试已完成'
        self.manager.retry(identifier);wait(self.manager)
        row=self.manager.snapshot()['rows'][0];self.assertEqual(row['id'],identifier);self.assertEqual(row['end'],self.now.isoformat())
        self.assertIn('重试已完成',self.manager.text(identifier));self.assertEqual(len(self.manager.snapshot()['rows']),1)

    def test_failed_summary_preserves_raw_records_and_can_retry(self):
        self.summary.summarize.side_effect=RuntimeError('临时不可用')
        identifier=self.manager.start('week',self.now)['id'];wait(self.manager)
        self.assertEqual(self.manager.snapshot()['rows'][0]['state'],'pending')
        report=self.manager.store.report(identifier);self.assertEqual(len(report['content']['groups']['work-chat']),3)
        self.assertIn('临时不可用',self.manager.text(identifier))
        self.summary.summarize.side_effect=None;self.summary.summarize.return_value='恢复后的周报'
        self.manager.retry(identifier);wait(self.manager)
        self.assertEqual(self.manager.snapshot()['rows'][0]['state'],'done');self.assertIn('恢复后的周报',self.manager.text(identifier))

    def test_final_save_failure_remains_visible_and_retryable(self):
        original=self.manager.store.save_report
        def fail_done(identifier,state,content,errors):
            if state=='done':raise OSError('disk full')
            return original(identifier,state,content,errors)
        with patch.object(self.manager.store,'save_report',side_effect=fail_done):
            identifier=self.manager.start('today',self.now)['id'];wait(self.manager)
        row=self.manager.snapshot()['rows'][0]
        self.assertEqual(row['state'],'pending');self.assertIn('保存',row['phase'])
        self.manager.retry(identifier);wait(self.manager)
        self.assertEqual(self.manager.snapshot()['rows'][0]['state'],'done')

    def test_empty_period_skips_model_and_waiting_job_can_stop(self):
        self.task['bindings']=[]
        identifier=self.manager.start('today',self.now)['id'];wait(self.manager)
        self.summary.summarize.assert_not_called();self.assertIn('没有找到',self.manager.text(identifier))
        lock=self.manager.execution_lock;lock.acquire()
        try:
            self.manager.start('week',self.now);self.manager.stop()
            self.assertFalse(self.manager.snapshot()['busy']);self.assertEqual(self.manager.snapshot()['rows'][0]['state'],'cancelled')
        finally:lock.release()

    def test_interrupted_state_recovers_without_automatic_model_call(self):
        identifier=self.manager.start('today',self.now)['id'];wait(self.manager)
        content=self.manager.store.report(identifier)['content'];content['state']='running';self.manager._save(content)
        self.manager.close();self.manager=WorkLogs(self.root/'daily/work-logs',self.tasks,summarizer=self.summary)
        self.assertEqual(self.manager.snapshot()['rows'][0]['state'],'cancelled')
        self.assertFalse(self.manager.snapshot()['busy']);self.assertIn('已核验',self.manager.text(identifier))


class WorkLogServiceTests(unittest.TestCase):
    def test_manual_service_keeps_nightly_settings_and_remote_state_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            runtime=MomoRuntime(Path(td)/'tasks.json',profile='work',auto_tick=False).start()
            try:
                runtime.tasks.clear_samples();task=make_task('带聊天的工作','work');runtime.tasks.add(task)
                runtime.tasks.bind(task['id'],'codex','codex://threads/service-chat')
                manager=runtime.daily_service.work_logs;manager.history.root=Path(td)/'codex'
                write_chat(manager.history.root,'service-chat',[(datetime.now(ZONE).isoformat(),'服务调用测试')])
                config=runtime.daily.snapshot()['config'];task_data=(Path(td)/'tasks.json').read_bytes()
                with patch.object(manager.summarizer,'summarize',return_value='本地总结'),patch.object(runtime.daily_service.engine,'run') as nightly,patch('linear_support.LinearWriter.ensure_comment') as remote:
                    identifier=runtime.daily.worklog_start('today')['id'];wait(manager)
                    nightly.assert_not_called();remote.assert_not_called()
                self.assertIn('本地总结',runtime.daily.worklog_text(identifier))
                self.assertEqual(runtime.daily.snapshot()['config'],config);self.assertEqual(runtime.daily.snapshot()['reports'],[])
                self.assertEqual((Path(td)/'tasks.json').read_bytes(),task_data)
                with self.assertRaises(ValueError):runtime.daily.worklog_start('bad')
                with self.assertRaises(ValueError):runtime.daily.worklog_text('../tasks')
            finally:runtime.close()

if __name__=='__main__':unittest.main()
