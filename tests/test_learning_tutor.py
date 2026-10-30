"""Tutor transport, course isolation, cancellation, persistence and native UI."""
import copy
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from learning_tutor import TUTOR_MODEL, TutorRunner, course_context, tutor_prompt
from learning_content import BUILTIN_COURSE_ID, combine_legacy_course, legacy_catalog
from learning_runner import ROOT
from model import make_task
from runtime import MomoRuntime
from test_ui import APP
from main import Momo
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QPushButton, QLabel
from PyQt5.QtTest import QTest


class TutorFixture:
    def __init__(self):
        self.started=threading.Event(); self.release=threading.Event(); self.calls=[]; self.failure=''
    def run(self, request, folder, cancel, progress):
        self.calls.append(copy.deepcopy(request)); self.started.set(); progress('正在分析本章问题')
        while not self.release.wait(.01):
            if cancel.is_set(): raise InterruptedError('已停止本次回答')
        if self.failure: raise RuntimeError(self.failure)
        return '先看输入与输出的映射，再对照本章公式。\n\n这是第 '+str(len(self.calls))+' 次回答。'


class TutorTransportTests(unittest.TestCase):
    def course(self):
        return combine_legacy_course(legacy_catalog(ROOT/'assets/learning/zhixing.json'))

    def test_context_uses_current_lesson_and_its_prerequisite_units_only(self):
        course=self.course(); lesson=course['package']['lessons'][0]
        context=course_context(course,lesson['id'])
        self.assertEqual(context['lesson']['id'],lesson['id'])
        self.assertTrue(set(lesson['knowledge_unit_ids']) <= {unit['id'] for unit in context['knowledge_units']})
        self.assertLess(len(context['knowledge_units']),len(course['package']['knowledge_units']))
        self.assertNotIn('answers',context); self.assertNotIn('note',context)
        with self.assertRaises(ValueError): course_context(course,'missing')

    def fake(self, root, mode='done'):
        binary=root/'fake-codex'
        binary.write_text('''#!/usr/bin/env python3
import json,sys,time
from pathlib import Path
args=sys.argv[1:];folder=Path(args[args.index('-C')+1]);prompt=sys.stdin.read()
(folder/'invocation.json').write_text(json.dumps({'args':args,'prompt':prompt}))
print(json.dumps({'type':'thread.started','thread_id':'fake'}),flush=True)
''' + ('''print(json.dumps({'type':'error','message':'unsupported model gpt-5.6-sol'}),flush=True)
sys.exit(2)
''' if mode=='failed' else '''time.sleep(30)
''' if mode=='wait' else '''Path(args[args.index('-o')+1]).write_text('用当前章节的映射关系来理解。')
'''))
        binary.chmod(0o700); return binary

    def request(self):
        course=self.course(); lesson=course['package']['lessons'][0]
        return dict(context=course_context(course,lesson['id']),history=[],question='这个公式如何理解？',excerpt='公式原文')

    def test_cli_uses_requested_model_current_auth_ephemeral_and_readonly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); binary=self.fake(root); folder=root/'run'
            with patch('learning_tutor.shutil.which',return_value=str(binary)), patch.dict(os.environ,{'CODEX_HOME':'/fixture/auth','LINEAR_API_KEY':'secret'}), patch('learning_tutor.subprocess.Popen',wraps=__import__('subprocess').Popen) as popen:
                answer=TutorRunner().run(self.request(),folder,threading.Event(),lambda text:None)
            self.assertIn('当前章节',answer)
            args=json.loads((folder/'invocation.json').read_text())['args']
            self.assertEqual(args[args.index('--model')+1],TUTOR_MODEL)
            self.assertEqual(args[args.index('-s')+1],'read-only'); self.assertIn('--ephemeral',args)
            env=popen.call_args.kwargs['env']
            self.assertEqual(env['CODEX_HOME'],'/fixture/auth'); self.assertNotIn('LINEAR_API_KEY',env)

    def test_model_error_is_returned_without_retry_or_model_substitution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); binary=self.fake(root,'failed')
            with patch('learning_tutor.shutil.which',return_value=str(binary)), patch('learning_tutor.subprocess.Popen',wraps=__import__('subprocess').Popen) as popen:
                with self.assertRaisesRegex(RuntimeError,'unsupported model gpt-5.6-sol'):
                    TutorRunner().run(self.request(),root/'run',threading.Event(),lambda text:None)
            self.assertEqual(popen.call_count,1)

    def test_cancel_terminates_cli_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); binary=self.fake(root,'wait'); cancel=threading.Event()
            timer=threading.Timer(.3,cancel.set); timer.start()
            try:
                with patch('learning_tutor.shutil.which',return_value=str(binary)):
                    start=time.monotonic()
                    with self.assertRaises(InterruptedError):
                        TutorRunner().run(self.request(),root/'run',cancel,lambda text:None)
                    self.assertLess(time.monotonic()-start,4)
            finally:timer.cancel();timer.join()


class TutorServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'tasks.json'
        self.runtime=MomoRuntime(self.path,auto_tick=False).start(); self.tasks=self.runtime.tasks; self.tasks.clear_samples()
        self.task=self.tasks.add(make_task('学习映射关系','learn')); self.client=self.runtime.learning
        self.client.bind(self.task['id'],BUILTIN_COURSE_ID)
        self.course=self.client.course(BUILTIN_COURSE_ID,self.task['id']); self.lessons=self.course['package']['lessons']
        self.runner=TutorFixture(); self.runtime.learning_service.tutor.runner=self.runner; self.window=None

    def tearDown(self):
        if self.window:
            self.window.exiting=True; self.window.close(); self.window.deleteLater(); APP.processEvents()
        self.runtime.close(); self.temp.cleanup()

    def ask(self,lesson=0,task=None,question='如何理解本章公式？'):
        return self.client.tutor_ask((task or self.task)['id'],BUILTIN_COURSE_ID,self.lessons[lesson]['id'],question)

    def snapshot(self,task=None):return self.client.tutor_snapshot((task or self.task)['id'],BUILTIN_COURSE_ID)

    def wait_done(self,task=None):
        deadline=time.monotonic()+4
        while self.snapshot(task)['busy'] and time.monotonic()<deadline:QTest.qWait(10)
        self.assertFalse(self.snapshot(task)['busy'])

    def open_reader(self):
        self.window=Momo(self.runtime); self.window.show(); self.window.navigate('learn'); APP.processEvents()
        action=next(b for b in self.window.list_host.findChildren(QPushButton) if b.text()=='开始学习')
        action.click(); APP.processEvents()
        return self.window.learning_dialogs[self.task['id']].readers[BUILTIN_COURSE_ID]

    def test_background_history_reuses_course_conversation_and_survives_restart(self):
        self.ask(); self.assertTrue(self.runner.started.wait(2))
        with self.assertRaisesRegex(ValueError,'正在回答'):self.ask()
        self.assertTrue(self.client.revision()['busy'])
        self.runner.release.set(); self.wait_done(); self.ask(1); self.wait_done()
        self.assertEqual(self.runner.calls[-1]['context']['lesson_id'],self.lessons[1]['id'])
        self.assertEqual(len(self.runner.calls[-1]['history']),1)
        before=self.snapshot()['turns']; self.runtime.close()
        self.runtime=MomoRuntime(self.path,auto_tick=False).start(); self.client=self.runtime.learning
        self.assertEqual(self.snapshot()['turns'],before)
        self.assertEqual(self.tasks.get(self.task['id'])['status'],'todo')
        self.assertEqual(self.client.course(BUILTIN_COURSE_ID,self.task['id'])['answers'],{})

    def test_task_course_validation_and_context_do_not_cross_sessions(self):
        other=self.tasks.add(make_task('另一个学习任务','learn')); work=self.tasks.add(make_task('工作任务','work'))
        with self.assertRaises(ValueError):self.ask(task=work)
        with self.assertRaises(ValueError):self.client.tutor_ask(self.task['id'],'missing',self.lessons[0]['id'],'问题')
        with self.assertRaises(ValueError):self.client.tutor_ask(self.task['id'],BUILTIN_COURSE_ID,'missing','问题')
        self.runner.release.set(); self.ask(); self.wait_done(); self.ask(task=other); self.wait_done(other)
        self.assertEqual(self.runner.calls[-1]['history'],[])
        self.assertEqual(len(self.snapshot(other)['turns']),1)

    def test_queue_and_cancel_follow_configured_learning_concurrency(self):
        other=self.tasks.add(make_task('另一门学习','learn'))
        self.ask(); self.assertTrue(self.runner.started.wait(2)); queued=self.ask(task=other)
        self.assertEqual(queued['turns'][-1]['status'],'queued')
        self.client.tutor_cancel(other['id'],BUILTIN_COURSE_ID)
        self.assertEqual(self.snapshot(other)['turns'][-1]['status'],'cancelled')
        self.client.tutor_cancel(self.task['id'],BUILTIN_COURSE_ID); self.wait_done()
        self.assertEqual(self.snapshot()['turns'][-1]['status'],'cancelled')
        self.assertEqual(len(self.runner.calls),1)

    def test_exit_stops_questions_and_keeps_interrupted_records(self):
        self.ask(); self.assertTrue(self.runner.started.wait(2)); self.runtime.close()
        self.assertFalse(self.runtime.learning_service.tutor.workers)
        self.runtime=MomoRuntime(self.path,auto_tick=False).start(); self.client=self.runtime.learning
        self.assertFalse(self.snapshot()['busy']); self.assertEqual(self.snapshot()['turns'][-1]['status'],'cancelled')

    def test_failed_save_does_not_launch_and_ui_keeps_question(self):
        reader=self.open_reader(); panel=reader.tutor; panel.question.setPlainText('请保留这段尚未发送的问题')
        with patch.object(self.runtime.learning_service.tutor,'writer',side_effect=OSError('disk full')):
            panel.send()
        self.assertEqual(self.runner.calls,[]); self.assertEqual(self.snapshot()['turns'],[])
        self.assertEqual(panel.question.toPlainText(),'请保留这段尚未发送的问题')
        self.assertIn('disk full',panel.notice.text())

    def test_scheduler_save_failure_does_not_leave_a_permanently_busy_question(self):
        tutor=self.runtime.learning_service.tutor; original=tutor.writer; writes=[]
        def fail_after_accept(path,data):
            writes.append(path)
            if len(writes)>1:raise OSError('disk full')
            return original(path,data)
        with patch.object(tutor,'writer',side_effect=fail_after_accept):
            state=self.ask()
        self.assertEqual(state['turns'][-1]['status'],'failed'); self.assertFalse(state['busy'])
        self.assertFalse(tutor.pending); self.assertFalse(tutor.workers); self.assertEqual(self.runner.calls,[])
        self.runner.release.set(); self.ask(); self.wait_done()

    def test_reader_sidebar_stays_responsive_and_new_questions_use_switched_chapter(self):
        reader=self.open_reader(); panel=reader.tutor
        self.assertIsNone(APP.activeModalWidget()); self.assertTrue(panel.isVisible())
        panel.question.setPlainText('解释第一章'); panel.send(); self.assertTrue(self.runner.started.wait(2))
        self.assertFalse(panel.send_button.isEnabled()); events=[]
        QTimer.singleShot(5,lambda:events.append('responsive')); QTest.qWait(30)
        self.assertEqual(events,['responsive']); reader.lessons.setCurrentRow(1); APP.processEvents()
        self.assertEqual(panel.lesson_id,self.lessons[1]['id'])
        self.assertEqual(self.runner.calls[0]['context']['lesson_id'],self.lessons[0]['id'])
        self.window.navigate('life'); self.assertEqual(self.window.view,'life')
        self.runner.release.set(); self.wait_done(); panel.refresh()
        panel.question.setPlainText('再解释第二章'); panel.send(); self.wait_done(); panel.refresh()
        self.assertEqual(self.runner.calls[-1]['context']['lesson_id'],self.lessons[1]['id'])
        self.assertIn('解释第一章',panel.transcript.toPlainText()); self.assertIn('再解释第二章',panel.transcript.toPlainText())
        reader.toggle_tutor(); self.assertTrue(panel.isHidden()); self.assertFalse(panel.timer.isActive())
        reader.toggle_tutor(); self.assertFalse(panel.isHidden()); self.assertTrue(panel.timer.isActive())

    def test_quote_tracks_its_chapter_and_reader_reopen_restores_saved_answers(self):
        reader=self.open_reader(); panel=reader.tutor
        text=next(widget for widget in reader.scroll.widget().findChildren(QLabel) if widget.text() and widget.textInteractionFlags() & Qt.TextSelectableByMouse)
        text.setSelection(0,min(5,len(text.text()))); reader.quote_selection()
        self.assertTrue(panel.excerpt); reader.lessons.setCurrentRow(1); self.assertEqual(panel.excerpt,'')
        self.runner.release.set(); panel.question.setPlainText('保存这个问题'); panel.send(); self.wait_done()
        reader.close(); APP.processEvents()
        dialog=self.window.learning_dialogs[self.task['id']]; dialog.read(); APP.processEvents()
        restored=dialog.readers[BUILTIN_COURSE_ID]
        self.assertIn('保存这个问题',restored.tutor.transcript.toPlainText())


if __name__=='__main__':unittest.main()
