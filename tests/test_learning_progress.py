import copy
from datetime import datetime,timezone,timedelta
import unittest
import json
import tempfile
import threading
import subprocess
from pathlib import Path
from unittest.mock import patch
from PyQt5.QtTest import QTest
from test_learning import ControlledRunner
from learning_runner import CourseRunner
from test_ui import APP
from learning_progress import advance_progress,model_stage,progress_view,STAGES
from main import Momo
import test_learning_regeneration as regeneration


class ProgressTests(unittest.TestCase):
    def test_only_actual_agent_stage_messages_are_accepted(self):
        valid={'type':'item.completed','item':{'type':'agent_message','text':'[ORBIT_STAGE:writing]'}}
        self.assertEqual(model_stage(valid),'writing')
        for e in [dict(valid,type='item.started'),dict(valid,item={'type':'command_execution','text':'[ORBIT_STAGE:writing]'}),
                  dict(valid,item={'type':'agent_message','text':'文中引用 [ORBIT_STAGE:writing]'}),
                  dict(valid,item={'type':'agent_message','text':'[ORBIT_STAGE:unknown]'})]:
            self.assertIsNone(model_stage(e))
        self.assertEqual(model_stage({'type':'item.started','item':{'type':'web_search'}}),'research')

    def test_real_subprocess_stage_events_reach_progress_callback(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); binary=root/'codex'; folder=root/'generation'; reported=[]
            binary.write_text('''#!/usr/bin/env python3
import json,sys
from pathlib import Path
prompt=sys.stdin.read()
Path('received.txt').write_text(prompt)
for stage in ('research','outline','writing','review'):
 print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'[ORBIT_STAGE:'+stage+']'}}),flush=True)
Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps({'status':'needs_input','message':'对象歧义','course_json':'','review':''}))
''');binary.chmod(0o700)
            original=subprocess.Popen
            def launch(cmd,**kwargs):return original(cmd,cwd=folder,**kwargs)
            with patch('learning_runner.find_codex',return_value=str(binary)),patch('learning_runner.subprocess.Popen',side_effect=launch):
                result=CourseRunner().run({'id':'fixture','input':{}},folder,threading.Event(),lambda message,**kw:reported.append(kw.get('stage')))
            self.assertEqual(reported,['research','outline','writing','review'])
            self.assertIn('[ORBIT_STAGE:writing]',(folder/'received.txt').read_text())
            self.assertEqual(result['status'],'needs_input')

    def test_stage_never_moves_back_or_completes_due_to_elapsed_time(self):
        started=datetime(2026,11,7,tzinfo=timezone.utc)
        job={'status':'running','started_at':started.isoformat()}
        advance_progress(job,'writing'); before=copy.deepcopy(job['progress'])
        advance_progress(job,'research'); self.assertEqual(job['progress'],before)
        view=progress_view(job,started+timedelta(hours=2)); self.assertEqual(view['elapsed'],'120:00')
        self.assertEqual(view['value'],3); self.assertLess(view['value'],view['maximum'])
        job.update(status='failed',finished_at=(started+timedelta(seconds=80)).isoformat())
        failed=progress_view(job,started+timedelta(hours=3)); self.assertEqual(failed['elapsed'],'01:20')
        self.assertIn('失败',failed['title']); self.assertLess(failed['value'],len(STAGES))
        job.update(status='review',course_id='generated:course'); self.assertEqual(progress_view(job)['value'],len(STAGES))

    def test_legacy_running_job_is_explicitly_unknown_not_finished(self):
        view=progress_view({'status':'running','created_at':'2026-10-28T00:00:00+00:00'})
        self.assertEqual(view['value'],0); self.assertIn('生成中',view['title']); self.assertEqual(view['elapsed'],'')


class GenerationProgressTests(unittest.TestCase):
    setUp=regeneration.RegenerationTests.setUp
    tearDown=regeneration.RegenerationTests.tearDown
    wait_done=regeneration.RegenerationTests.wait_done

    def test_service_persists_reported_stage_and_ui_updates_clock_without_revision_change(self):
        original=self.runner.run
        def run(job,folder,cancel,progress):
            progress('正在编写分步案例',stage='writing')
            return original(job,folder,cancel,progress)
        self.runner.run=run
        window=Momo(self.runtime);window.show();window.open_learning(self.task['id']);APP.processEvents()
        try:
            dialog=window.learning_dialogs[self.task['id']]
            job=self.client.regenerate(self.task['id'],self.client.snapshot(self.task['id'])['course']['id'],'分步讲透')
            self.assertTrue(self.runner.started.wait(2));dialog.refresh()
            state=self.client.snapshot(self.task['id']); current=state['jobs'][-1]
            self.assertEqual(current['progress']['stage'],'writing'); self.assertIn('started_at',current)
            self.assertEqual(dialog.progress.value(),3); self.assertIn('编写',dialog.progress_text.text())
            before=dialog.progress_text.text(); QTest.qWait(1150);dialog.refresh()
            self.assertNotEqual(dialog.progress_text.text(),before)
            self.runner.release.set();self.wait_done();dialog.refresh()
            self.assertEqual(dialog.progress.value(),dialog.progress.maximum());self.assertIn('待审阅',dialog.progress_text.text())
        finally:
            self.runner.release.set();self.wait_done();window.exiting=True;window.close();window.deleteLater();APP.processEvents()
