"""Installer scheduling contracts, calendar recovery and exact bound-chat collection."""
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/codex-orbit'))
from orbit_plugin.scheduler import Scheduler, ScheduleError, latest_boundary, local_boundary, clock, environment
from orbit_plugin.nightly import DailyCollector, shared_files, shared_records
from orbit_plugin.journal_codex import messages, CodexText
from orbit_plugin.journals import JournalLibrary
from evaluation.report_archive import write_json
from evaluation.protocol import validate_record
import test_codex_plugin_lan as fixtures


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.calls=[]
        self.scheduler=Scheduler(self.root/'中文 个人数据',python=sys.executable,script=ROOT/'plugins/codex-orbit/scripts/mcp.py',
            platform='linux',config_home=self.root/'config',execute=lambda args:self.calls.append(args) or 'active\n')
        self.now=datetime(2026,11,21,18).astimezone()

    def test_native_prepare_does_not_create_os_job_and_reuses_request(self):
        from orbit_plugin.codex_schedule import prepare
        first=prepare(self.scheduler, now=self.now)
        self.assertEqual(first,prepare(self.scheduler,now=self.now))
        self.assertEqual(self.calls,[])
        self.assertFalse(self.scheduler.registration_path.exists())
        self.assertEqual(first['config']['time'],'00:00')
        self.assertEqual(first['command'][-2:],['--data-dir',str(self.scheduler.directory)])

    def test_native_confirm_requires_receipt_and_disables_old_system_job_once(self):
        from orbit_plugin.codex_schedule import prepare,confirm
        self.scheduler.configure(now=self.now)
        request=prepare(self.scheduler,now=self.now)
        before=self.scheduler.registration()
        with self.assertRaises(ScheduleError):confirm(self.scheduler,request['token'],'native-id',False)
        with self.assertRaises(ScheduleError):confirm(self.scheduler,'wrong','native-id',True)
        self.assertEqual(self.scheduler.registration(),before)
        result=confirm(self.scheduler,request['token'],'native-id',True)
        self.assertEqual(result['registration']['backend'],'codex-automation')
        self.assertTrue(result['config']['enabled'])
        calls=list(self.calls)
        confirm(self.scheduler,request['token'],'native-id',True)
        self.assertEqual(self.calls,calls)
        self.assertEqual(prepare(self.scheduler,now=self.now)['automation_id'],'native-id')
        with self.assertRaises(ScheduleError):self.scheduler.configure(now=self.now)

    def test_native_failed_migration_keeps_old_job_and_same_request_is_retryable(self):
        from orbit_plugin.codex_schedule import prepare,confirm
        self.scheduler.configure(now=self.now)
        request=prepare(self.scheduler,now=self.now)
        old_config=self.scheduler.config();old_registration=self.scheduler.registration()
        execute=self.scheduler.execute
        self.scheduler.execute=Mock(side_effect=ScheduleError('unavailable'))
        with self.assertRaises(ScheduleError):confirm(self.scheduler,request['token'],'native-id',True)
        self.assertEqual(self.scheduler.config(),old_config)
        self.assertEqual(self.scheduler.registration(),old_registration)
        self.scheduler.execute=execute
        confirm(self.scheduler,request['token'],'native-id',True)
        self.assertEqual(self.scheduler.registration()['automation_id'],'native-id')

    def test_native_migration_preserves_initial_pause_request_but_host_owns_resume(self):
        from orbit_plugin.codex_schedule import prepare,confirm
        self.scheduler.configure(enabled=False,now=self.now)
        request=prepare(self.scheduler,now=self.now)
        self.assertFalse(request['config']['enabled'])
        result=confirm(self.scheduler,request['token'],'paused-native-id',True)
        self.assertTrue(result['config']['enabled'])

    def test_bootstrap_is_read_only_and_stops_after_confirmation(self):
        from orbit_plugin.codex_schedule import bootstrap_instructions, prepare, confirm
        self.assertIn('prepare_daily_automation', bootstrap_instructions(self.scheduler.directory))
        self.assertFalse(self.scheduler.root.exists())
        request=prepare(self.scheduler, now=self.now)
        self.assertIn('automation_update', bootstrap_instructions(self.scheduler.directory))
        confirm(self.scheduler, request['token'], 'native-id', True)
        self.assertEqual(bootstrap_instructions(self.scheduler.directory), '')

    def test_bootstrap_respects_opt_out_system_scheduler_and_unreadable_registration(self):
        from orbit_plugin.codex_schedule import bootstrap_instructions
        self.assertEqual(bootstrap_instructions(self.scheduler.directory, False), '')
        self.scheduler.configure(enabled=False, now=self.now)
        self.assertEqual(bootstrap_instructions(self.scheduler.directory), '')
        self.scheduler.registration_path.write_text('{broken')
        self.assertNotIn('prepare_daily_automation', bootstrap_instructions(self.scheduler.directory))
        self.assertIn('无法读取', bootstrap_instructions(self.scheduler.directory))

    def test_first_mcp_launch_preserves_installer_time_boundary_and_stable_command(self):
        from orbit_plugin.codex_schedule import prepare
        first=prepare(self.scheduler, '23:30', now=self.now)
        # The MCP instance may run from Codex's versioned cache, unlike the installer.
        runtime=Scheduler(self.scheduler.directory)
        result=prepare(runtime, now=self.now+timedelta(days=2), owner='session-a')
        self.assertEqual(result['config'], first['config'])
        self.assertEqual(result['command'], first['command'])

    def test_concurrent_bootstrap_claims_and_recovery_do_not_reuse_stale_tokens(self):
        from orbit_plugin.codex_schedule import prepare, confirm
        first=prepare(self.scheduler, now=self.now, owner='session-a')
        self.assertEqual(first, prepare(self.scheduler, now=self.now, owner='session-a'))
        other=prepare(self.scheduler, now=self.now, owner='session-b')
        self.assertEqual(other['state'], 'waiting')
        self.assertNotIn('token', other)
        later=prepare(self.scheduler, now=self.now+timedelta(minutes=16), owner='session-b')
        self.assertNotEqual(first['token'], later['token'])
        with self.assertRaises(ScheduleError):confirm(self.scheduler, first['token'], 'native-id', True)
        confirm(self.scheduler, later['token'], 'native-id', True)
        self.assertEqual(prepare(self.scheduler, now=self.now, owner='session-a')['automation_id'], 'native-id')

    def test_default_midnight_stable_job_update_and_disable(self):
        saved=self.scheduler.configure(now=self.now)
        self.assertEqual(saved['config']['time'],'00:00')
        self.assertEqual(saved['config']['first_boundary'],'2026-11-22')
        self.assertTrue(saved['config']['enabled'])
        units=self.root/'config/systemd/user'
        timer=next(units.glob('*.timer'));service=next(units.glob('*.service'))
        self.assertIn('OnCalendar=*-*-* 00:00:00',timer.read_text())
        self.assertIn('Persistent=true',timer.read_text())
        self.assertIn('--collect-daily',service.read_text())
        self.assertNotIn('LINEAR_API_KEY',service.read_text())
        self.scheduler.configure('23:30',now=self.now)
        self.assertEqual(len(list(units.glob('*.timer'))),1)
        self.assertIn('23:30:00',timer.read_text())
        self.scheduler.configure(enabled=False,now=self.now)
        self.assertIn(['systemctl','--user','disable','--now',timer.name],self.calls)
        self.scheduler.configure(now=self.now)
        self.assertFalse(self.scheduler.config()['enabled'])
        self.assertEqual(self.scheduler.config()['time'],'23:30')

    def test_failed_registration_restores_previous_files_and_preferences(self):
        self.scheduler.configure(now=self.now)
        original=self.scheduler.config_path.read_bytes()
        timer=next((self.root/'config/systemd/user').glob('*.timer'));old=timer.read_bytes()
        def fail(args):
            if 'is-active' in args: raise ScheduleError('offline')
            return ''
        self.scheduler.execute=fail
        with self.assertRaises(ScheduleError): self.scheduler.configure('21:30',now=self.now)
        self.assertEqual(self.scheduler.config_path.read_bytes(),original)
        self.assertEqual(timer.read_bytes(),old)

    def test_calendar_edges_and_real_systemd_unit_syntax(self):
        self.assertEqual(latest_boundary(datetime(2026,11,22,0,0).astimezone(),'00:00').date().isoformat(),'2026-11-22')
        self.assertEqual(latest_boundary(datetime(2026,11,21,23,59).astimezone(),'00:00').date().isoformat(),'2026-11-21')
        for invalid in ('24:00','12','0:00','00:60','00:00\nOnCalendar=bad'):
            with self.assertRaises(ScheduleError): clock(invalid)
        if sys.platform.startswith('linux'):
            self.scheduler.configure(now=self.now)
            unit=next((self.root/'config/systemd/user').glob('*.service'))
            result=subprocess.run(['systemd-analyze','--user','verify',str(unit)],capture_output=True,text=True,env=environment())
            self.assertEqual(result.returncode,0,result.stderr)

    def test_windows_task_xml_is_per_user_and_registered_idempotently(self):
        documents=[]
        def execute(args):
            self.calls.append(args)
            if args[0]=='whoami.exe':return '"work\\employee","S-1-5-21-123-456-1001"'
            if '/Create' in args:
                documents.append(Path(args[args.index('/XML')+1]).read_bytes());return ''
            if '/Query' in args:return documents[-1].decode('utf-16')
            return ''
        scheduler=Scheduler(self.root/'windows',python=sys.executable,script=ROOT/'plugins/codex-orbit/scripts/mcp.py',platform='win32',execute=execute)
        scheduler.configure(now=self.now)
        self.assertNotIn('/F',next(row for row in self.calls if '/Create' in row))
        task=ET.fromstring(documents[0]);ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
        self.assertEqual(task.find('.//t:LogonType',ns).text,'InteractiveToken')
        self.assertEqual(task.find('.//t:StartWhenAvailable',ns).text,'true')
        self.assertIn('T00:00:00',task.find('.//t:StartBoundary',ns).text)
        self.assertIn('--collect-daily',task.find('.//t:Arguments',ns).text)
        scheduler.configure('22:45',now=self.now)
        self.assertIn('/F',[row for row in self.calls if '/Create' in row][-1])
        self.assertEqual(len({row[row.index('/TN')+1] for row in self.calls if '/Create' in row}),1)
        self.assertFalse(list(scheduler.root.glob('orbit-schedule-*.xml')))
        scheduler.configure(enabled=False,now=self.now)
        self.assertIn(['/Change','/TN',scheduler.name,'/Disable'],[row[1:] for row in self.calls if '/Change' in row])

    def test_windows_initially_disabled_can_be_enabled_later(self):
        stored=[]
        def execute(args):
            if args[0]=='whoami.exe': return 'S-1-5-21-123-456-1001'
            if '/Create' in args: stored.append(Path(args[args.index('/XML')+1]).read_bytes());return ''
            if '/Query' in args: return stored[-1].decode('utf-16')
            return ''
        scheduler=Scheduler(self.root/'windows-disabled',python=sys.executable,script=ROOT/'plugins/codex-orbit/scripts/mcp.py',platform='win32',execute=execute)
        scheduler.configure(enabled=False,now=self.now)
        ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
        self.assertEqual(ET.fromstring(stored[-1]).find('t:Settings/t:Enabled',ns).text,'false')
        scheduler.configure(enabled=True,now=self.now)
        self.assertEqual(ET.fromstring(stored[-1]).find('t:Settings/t:Enabled',ns).text,'true')

    def test_windows_failed_readback_removes_new_task_and_restores_old_task(self):
        stored=[];fail=[True];calls=[]
        def execute(args):
            calls.append(args)
            if args[0]=='whoami.exe': return 'S-1-5-21-123-456-1001'
            if '/Create' in args: stored.append(Path(args[args.index('/XML')+1]).read_bytes());return ''
            if '/Query' in args:
                # Fail only after a task was written, so updates first read their previous definition.
                if fail[0] and calls[-2][1]=='/Create': raise ScheduleError('readback failed')
                return stored[-1].decode('utf-16')
            return ''
        scheduler=Scheduler(self.root/'windows-rollback',python=sys.executable,script=ROOT/'plugins/codex-orbit/scripts/mcp.py',platform='win32',execute=execute)
        with self.assertRaises(ScheduleError): scheduler.configure(now=self.now)
        self.assertIn(['/Delete','/TN',scheduler.name,'/F'],[row[1:] for row in calls])
        self.assertFalse(scheduler.config_path.exists())
        fail[0]=False;scheduler.configure(now=self.now)
        original=stored[-1];config=scheduler.config_path.read_bytes()
        fail[0]=True
        with self.assertRaises(ScheduleError): scheduler.configure('23:30',now=self.now)
        self.assertEqual(stored[-1],original)
        self.assertEqual(scheduler.config_path.read_bytes(),config)
        self.assertFalse(list(scheduler.root.glob('orbit-schedule-*.xml')))


class NightlyTests(unittest.TestCase):
    setUpBase=fixtures.PluginLanTests.setUp
    draft=fixtures.PluginLanTests.draft

    def setUp(self):
        self.setUpBase()
        self.thread=str(uuid.uuid4())
        self.home=self.root/'codex-home';self.home.mkdir()
        self.start=local_boundary(datetime(2026,11,21).date(),'00:00');self.end=local_boundary(datetime(2026,11,22).date(),'00:00')
        stamp=(self.start+timedelta(hours=12)).isoformat()
        with self.orbit.db() as db:
            db.execute('UPDATE tasks SET session_id=?,created_at=?',(self.thread,(self.start-timedelta(days=1)).isoformat()))
            db.execute('UPDATE results SET created_at=?',(stamp,))
        scheduler=Scheduler(self.orbit.directory)
        write_json(scheduler.config_path,dict(version=1,time='00:00',enabled=True,first_boundary='2026-11-22',source='bound_codex'))
        write_json(scheduler.registration_path,dict(codex_home=str(self.home)))
        self.path=self.rollout(self.thread,[self.item(stamp,'user','计划拆分模块'),self.item(stamp,'assistant','已改接口，单测通过，实机尚未验证')])

    @staticmethod
    def item(stamp,role,text):
        return dict(type='response_item',timestamp=stamp,payload=dict(type='message',role=role,content=[dict(type='input_text' if role=='user' else 'output_text',text=text)]))

    def rollout(self,thread,items):
        folder=self.home/'sessions/2026/11/21';folder.mkdir(parents=True,exist_ok=True)
        path=folder/('rollout-2026-11-21T12-00-00-'+thread+'.jsonl')
        meta=dict(type='session_meta',payload=dict(id=thread,source='cli'))
        path.write_text('\n'.join(json.dumps(row,ensure_ascii=False) for row in [meta,*items])+'\n',encoding='utf-8')
        return path

    def test_only_bound_chat_window_is_collected_and_shared_explicitly(self):
        other=self.rollout(str(uuid.uuid4()),[self.item((self.start+timedelta(hours=1)).isoformat(),'user','PRIVATE-UNBOUND')])
        summarizer=Mock(return_value='接口已拆分；单测通过，实机未验证。')
        collector=DailyCollector(self.orbit,summarize=summarizer)
        first=collector.collect(self.end)
        self.assertEqual(first['periods'],1);self.assertEqual(first['pending'],0)
        transcript=summarizer.call_args.args[0]
        self.assertEqual(len(transcript),2)
        self.assertNotIn('PRIVATE-UNBOUND',json.dumps(transcript))
        file=next((self.orbit.directory/'daily/reports').rglob('*.json'))
        report=json.loads(file.read_text());self.assertEqual(report['day'],'2026-11-21')
        self.assertEqual(report['message_count'],2);self.assertEqual(len(report['results']),1)
        self.assertEqual(collector.collect(self.end)['periods'],0)
        self.assertEqual(summarizer.call_count,1)
        files=shared_files(self.orbit,[self.task['id']]);self.assertEqual(len(files),2)
        self.assertNotIn('计划拆分模块',json.dumps(list(files)))
        records=shared_records(self.orbit,self.config.read());self.assertEqual(len(records),1);validate_record(records[0])
        self.assertEqual(len(JournalLibrary(self.orbit,self.config.read).files()),5)
        self.config.save(self.orbit,'张工','电气',True,[])
        self.assertEqual(list(JournalLibrary(self.orbit,self.config.read).files()),['日志统计标准.md'])

    def test_cutoff_is_left_closed_right_open_and_never_reads_unbound_contents(self):
        self.path.unlink()
        items=[self.item((self.start-timedelta(microseconds=1)).isoformat(),'user','before'),
               self.item(self.start.isoformat(),'user','included-start'),self.item(self.end.isoformat(),'assistant','next-day')]
        self.rollout(self.thread,items)
        result=messages(self.home,self.thread,self.start,self.end)
        self.assertEqual([row['text'] for row in result],['included-start'])
        with self.assertRaises(ValueError):messages(self.home,'../../auth',self.start,self.end)

    def test_failed_summary_retries_without_duplicate_successful_days(self):
        summarize=Mock(side_effect=[ValueError('Codex unavailable'),'已补全摘要'])
        collector=DailyCollector(self.orbit,summarize=summarize)
        self.assertEqual(collector.collect(self.end)['pending'],1)
        report=json.loads(next((self.orbit.directory/'daily/reports').rglob('*.json')).read_text())
        self.assertEqual(report['status'],'pending');self.assertEqual(report['message_count'],2)
        result=collector.collect(self.end+timedelta(hours=2))
        self.assertEqual(result['pending'],0)
        self.assertEqual(len(list((self.orbit.directory/'daily/reports').rglob('*.json'))),1)

    def test_missed_days_are_backfilled_disabled_schedule_does_not_read(self):
        collector=DailyCollector(self.orbit,summarize=Mock(return_value='摘要'))
        self.assertEqual(collector.collect(self.end+timedelta(days=2))['periods'],3)
        self.assertEqual(len(list((self.orbit.directory/'daily/reports').rglob('*.json'))),3)
        scheduler=Scheduler(self.orbit.directory);config=scheduler.config();config['enabled']=False;write_json(scheduler.config_path,config)
        reader=Mock(side_effect=AssertionError('must not read'))
        self.assertEqual(DailyCollector(self.orbit,read_messages=reader).collect(self.end+timedelta(days=3))['state'],'disabled')
        reader.assert_not_called()

    def test_missing_transcript_and_partial_json_are_visible_pending(self):
        self.path.unlink()
        collector=DailyCollector(self.orbit,summarize=Mock())
        self.assertEqual(collector.collect(self.end)['pending'],1)
        self.path=self.rollout(self.thread,[])
        with self.path.open('ab') as stream:stream.write(b'{"unfinished":')
        self.assertEqual(collector.collect(self.end)['pending'],1)
        self.path=self.rollout(self.thread,[])
        self.assertEqual(collector.collect(self.end)['pending'],0)

    def test_changing_time_keeps_a_continuous_window_without_overwriting(self):
        reader=Mock(return_value=[])
        collector=DailyCollector(self.orbit,read_messages=reader,summarize=Mock())
        collector.collect(self.end)
        scheduler=Scheduler(self.orbit.directory)
        config=scheduler.config();config['time']='23:30';write_json(scheduler.config_path,config)
        next_end=local_boundary(self.end.date(),'23:30')
        self.assertEqual(collector.collect(next_end)['periods'],1)
        self.assertEqual(reader.call_args.args[2],self.end)
        self.assertEqual(reader.call_args.args[3],next_end)
        self.assertEqual(len(list((self.orbit.directory/'daily/reports').rglob('*.json'))),2)

    def test_summary_cache_and_command_disable_tools_without_changing_auth(self):
        worker=CodexText(self.orbit.directory,dict(codex_home=str(self.home)),threading.Event())
        with patch.dict(os.environ,dict(LINEAR_API_KEY='never-pass-this')):
            self.assertNotIn('LINEAR_API_KEY',worker.environment())
            self.assertEqual(worker.environment()['CODEX_HOME'],str(self.home))
        content='A'*50000
        with patch.object(worker,'compress',return_value='摘要') as compress:
            output=worker.summarize([dict(timestamp=self.start.isoformat(),role='user',text=content)])
            self.assertEqual(compress.call_count,4)
            joined=''.join(call.args[0] for call in compress.call_args_list)
            self.assertTrue(joined.endswith(content));self.assertEqual(output,'摘要\n\n摘要\n\n摘要\n\n摘要')


if __name__=='__main__':unittest.main()


class ScheduleUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_runtime import configure_qt_plugins
        configure_qt_plugins()
        from PyQt5.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_native_card_defaults_to_midnight_and_does_not_require_api_key(self):
        from orbit_plugin.core import Orbit
        from orbit_plugin.schedule_ui import ScheduleCard
        with tempfile.TemporaryDirectory() as temporary:
            card=ScheduleCard(Orbit(Path(temporary)/'data'))
            self.addCleanup(card.close)
            card.show();self.app.processEvents()
            self.assertEqual(card.at.time().toString('HH:mm'),'00:00')
            self.assertTrue(card.enabled.isChecked())
            self.assertIn('尚未注册',card.status.text())
            self.assertFalse((Path(temporary)/'data').exists())
            card.close()

    def test_collection_runs_in_worker_and_close_requests_cancellation(self):
        from orbit_plugin.core import Orbit
        from orbit_plugin.schedule_ui import ScheduleCard
        from PyQt5.QtTest import QTest
        with tempfile.TemporaryDirectory() as temporary:
            card=ScheduleCard(Orbit(Path(temporary)/'data'));card.show()
            started=threading.Event()
            def collect(*args,**kwargs):
                started.set()
                kwargs['cancel'].wait(3)
                return dict(periods=0,pending=0)
            with patch('orbit_plugin.nightly.DailyCollector.collect',side_effect=collect):
                card.collect()
                self.assertTrue(started.wait(1))
                self.assertFalse(card.run_button.isEnabled())
                self.assertTrue(card.isVisible())
                card.close();self.assertTrue(card.cancel.is_set())
                for _ in range(100):
                    self.app.processEvents();QTest.qWait(5)
                    if not card.job.isRunning():break
                self.assertFalse(card.job.isRunning())
                card.close()
