import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QPushButton
from test_ui import APP
from test_learning import ControlledRunner, package
from runtime import MomoRuntime
from model import make_task
from learning_content import BUILTIN_COURSE_ID, validate_package
from learning_quality import teaching_audit, TEACHING_STANDARD
from learning_ui import LearningDialog, CourseReader
from learning_diagram_ui import LessonDiagram
from learning_runner import CourseRunner
from main import Momo


def diagram():
    return {'type':'diagram','title':'跟踪一次数据交换','caption':'从上而下看一次交接；为教学示意，不按时间比例绘制。',
        'nodes':[{'id':'master','title':'主站准备数据','explanation':'先确定要发送的字段和方向。'},
                 {'id':'slave','title':'从站取走目标','explanation':'只取约定的字段，不能把通信收到等同于动作完成。'},
                 {'id':'return','title':'返回反馈','explanation':'核对返回数据及其有效性。'}],
        'edges':[{'source':'master','target':'slave','label':'发送'}, {'source':'slave','target':'return','label':'回收'}]}


class RegenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start()
        self.tasks=self.runtime.tasks; self.tasks.clear_samples(); self.task=self.tasks.add(make_task('EtherCAT','learn'))
        self.client=self.runtime.learning; self.runner=ControlledRunner(); self.runtime.learning_service.runner=self.runner
        self.client.bind(self.task['id'],BUILTIN_COURSE_ID)

    def tearDown(self):
        self.runtime.close(); self.temp.cleanup()

    def wait_done(self):
        deadline=time.monotonic()+5
        while self.client.snapshot()['busy'] and time.monotonic()<deadline: time.sleep(.01)
        self.assertFalse(self.client.snapshot()['busy'])

    def regenerate(self,text='先建立整体直觉，再展开数据流，减少缩写堆叠。'):
        return self.client.regenerate(self.task['id'],BUILTIN_COURSE_ID,text)

    def test_snapshots_current_course_without_answers_and_preserves_old_version(self):
        old=self.client.course(BUILTIN_COURSE_ID,self.task['id']); lesson=old['package']['lessons'][0]; q=lesson['questions'][0]
        self.client.answer(task_id=self.task['id'],course_id=old['id'],lesson_id=lesson['id'],question_id=q['id'],text='私有作答不要进入上下文')
        job=self.regenerate(); self.assertTrue(self.runner.started.wait(2))
        self.assertEqual(self.client.snapshot(self.task['id'])['course']['id'],old['id'])
        snapshot=(self.root/'learning/generations'/job['id']/'previous-course.json').read_text()
        self.assertNotIn('私有作答',snapshot); self.assertNotIn('answers',json.loads(snapshot))
        self.assertEqual(json.loads(snapshot)['package'],old['package'])
        self.assertEqual(job['input']['feedback'],'先建立整体直觉，再展开数据流，减少缩写堆叠。')
        self.runner.release.set(); self.wait_done()
        current=self.client.snapshot(self.task['id'])['course']['id']; self.assertNotEqual(current,old['id'])
        self.assertEqual(self.client.course(current,self.task['id'])['supersedes'],old['id'])
        self.assertIn('私有作答',str(self.client.course(old['id'],self.task['id'])['answers']))
        self.assertEqual(self.client.course(current,self.task['id'])['answers'],{})
        audit=json.loads((self.root/'learning/generations'/job['id']/'teaching-audit.json').read_text())
        self.assertFalse(audit['errors']); self.assertIn('仅检查',audit['scope'])

    def test_invalid_empty_oversized_or_stale_requests_do_not_launch(self):
        for text in ['', '  ', '字'*6001, 123]:
            with self.assertRaises(ValueError): self.regenerate(text)
        self.client.bind(self.task['id'],None)
        with self.assertRaisesRegex(ValueError,'绑定已改变'): self.regenerate()
        self.assertEqual(self.runner.calls,[])

    def test_failure_cancel_or_later_binding_does_not_replace_old_course(self):
        self.runner.result='failed'; self.runner.release.set(); self.regenerate(); self.wait_done()
        self.assertEqual(self.client.snapshot(self.task['id'])['course']['id'],BUILTIN_COURSE_ID)
        self.runner=ControlledRunner(); self.runtime.learning_service.runner=self.runner
        job=self.regenerate(); self.assertTrue(self.runner.started.wait(2)); self.client.cancel(job['id']); self.wait_done()
        self.assertEqual(self.client.snapshot(self.task['id'])['course']['id'],BUILTIN_COURSE_ID)
        self.runner=ControlledRunner(); self.runtime.learning_service.runner=self.runner
        self.regenerate(); self.assertTrue(self.runner.started.wait(2)); self.client.bind(self.task['id'],None)
        self.runner.release.set(); self.wait_done(); self.assertIsNone(self.client.snapshot(self.task['id'])['course'])

    def test_teaching_failure_keeps_candidate_and_old_course(self):
        original=self.runner.run
        def thin(*args):
            result=original(*args); candidate=json.loads(result['course_json'])
            candidate['lessons'][0]['teaching']['visual']={'required':True,'reason':'流程是关键难点'}
            result['course_json']=json.dumps(candidate,ensure_ascii=False); return result
        self.runner.run=thin; self.runner.release.set(); job=self.regenerate(); self.wait_done()
        state=self.client.snapshot(self.task['id']); self.assertEqual(state['course']['id'],BUILTIN_COURSE_ID)
        self.assertEqual(state['jobs'][-1]['status'],'failed'); self.assertIn('图解',state['jobs'][-1]['message'])
        self.assertTrue((self.root/'learning/generations'/job['id']/'course-package.json').exists())
        self.assertFalse((self.root/'learning/courses'/(job['id']+'.json')).exists())

    def test_feedback_ui_nonmodal_and_old_reader_remains_usable_while_running(self):
        window=Momo(self.runtime); window.show(); APP.processEvents()
        try:
            window.open_learning(self.task['id']); dialog=window.learning_dialogs[self.task['id']]
            dialog.improve(); self.assertIs(dialog.tabs.currentWidget(),dialog.improve_page)
            self.assertFalse(dialog.regenerate_button.isEnabled())
            dialog.add_feedback('从零解释，并用一次数据交换贯穿。'); self.assertTrue(dialog.regenerate_button.isEnabled())
            self.assertTrue(dialog.save_feedback())
            QTest.mouseClick(dialog.regenerate_button,Qt.LeftButton); self.assertTrue(self.runner.started.wait(2))
            self.assertFalse(dialog.regenerate_button.isEnabled()); dialog.read()
            reader=dialog.readers[BUILTIN_COURSE_ID]; reader.lessons.setCurrentRow(1); APP.processEvents()
            self.assertEqual(reader.lessons.currentRow(),1)
            button=next(b for b in reader.findChildren(QPushButton) if b.text()=='改进课程')
            QTest.mouseClick(button,Qt.LeftButton); self.assertIs(dialog.tabs.currentWidget(),dialog.improve_page)
            self.assertEqual(dialog.windowModality(),Qt.NonModal)
        finally:
            self.runner.release.set(); self.wait_done(); window.exiting=True; window.close(); window.deleteLater(); APP.processEvents()

    def test_feedback_draft_is_private_per_task_course_and_survives_restart(self):
        self.client.save_feedback(self.task['id'],BUILTIN_COURSE_ID,'暂存意见，不立即生成')
        self.assertEqual(self.client.snapshot(self.task['id'])['feedback_draft'],'暂存意见，不立即生成')
        other=self.tasks.add(make_task('另一学习任务','learn')); self.client.bind(other['id'],BUILTIN_COURSE_ID)
        self.assertEqual(self.client.snapshot(other['id'])['feedback_draft'],'')
        self.runtime.close(); self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start()
        self.client=self.runtime.learning
        self.assertEqual(self.client.snapshot(self.task['id'])['feedback_draft'],'暂存意见，不立即生成')
        self.assertEqual(self.client.snapshot(self.task['id'])['jobs'],[])


class TeachingTests(unittest.TestCase):
    def candidate(self):
        return package({'id':'test','input':{'mode':'subject_curriculum','subject':'测试','task_id':'task','input_version':'v'},'teaching_standard':TEACHING_STANDARD})

    def test_audit_requires_process_examples_levels_and_visual_delivery(self):
        p=self.candidate(); self.assertFalse(teaching_audit(p,True)['errors'])
        for change,needle in [
            (lambda l:l['teaching'].pop('bridge'),'先修'),
            (lambda l:l['teaching'].update(explanation_section_id='missing'),'推理'),
            (lambda l:l['questions'][0].update(level='unknown'),'三个层次'),
            (lambda l:l['teaching'].update(visual={'required':True,'reason':'流程难点'}),'图解')]:
            candidate=copy.deepcopy(p); change(candidate['lessons'][0]); self.assertIn(needle,str(teaching_audit(candidate,True)['errors']))
        candidate=copy.deepcopy(p)
        for s in candidate['lessons'][0]['sections']:s['blocks']=[b for b in s['blocks'] if b['type']!='worked_example']
        self.assertIn('分步案例',str(teaching_audit(candidate,True)['errors']))
        p.pop('teaching_standard'); self.assertFalse(teaching_audit(p)['errors'])
        self.assertIn('版本',str(teaching_audit(p,True)['errors']))

    def test_diagram_validation_and_native_step_controls(self):
        p=self.candidate(); b=diagram(); p['lessons'][0]['sections'][0]['blocks'].append(b)
        validate_package(p); view=LessonDiagram(b); view.resize(320,600); view.show(); APP.processEvents()
        try:
            self.assertIn('先确定',view.explanation.text()); self.assertFalse(view.previous.isEnabled())
            QTest.mouseClick(view.next,Qt.LeftButton); self.assertIn('只取',view.explanation.text())
            view.selector.setCurrentIndex(2); self.assertFalse(view.next.isEnabled())
            self.assertGreaterEqual(view.view.transform().m11(),.85)
        finally:view.close(); view.deleteLater(); APP.processEvents()
        for bad in ['cycle','unknown','empty']:
            p=self.candidate(); b=diagram()
            if bad=='cycle':b['edges'].append({'source':'return','target':'master','label':'loop'})
            elif bad=='unknown':b['edges'][0]['target']='missing'
            else:b['nodes']=[]
            p['lessons'][0]['sections'][0]['blocks'].append(b)
            with self.assertRaises(ValueError): validate_package(p)

    def test_regeneration_without_snapshot_fails_before_launching_cli(self):
        with tempfile.TemporaryDirectory() as temp, patch('learning_runner.find_codex',return_value='/test/codex'), patch('learning_runner.subprocess.Popen') as process:
            import threading
            with self.assertRaisesRegex(ValueError,'快照缺失'):
                CourseRunner().run({'id':'fixture','input':{'base_course_id':'course','feedback':'解释跳步'}},Path(temp),threading.Event(),lambda *args:None)
            process.assert_not_called()

    def test_long_diagram_titles_stay_inside_nodes(self):
        b=diagram(); b['nodes'][0]['title']='长标题'*18
        view=LessonDiagram(b); view.resize(320,600); view.show(); APP.processEvents()
        try:
            for key,text in view.titles.items():
                rect=view.rects[key].rect()
                self.assertGreaterEqual(rect.height(),text.boundingRect().height()+10)
        finally:view.close(); view.deleteLater(); APP.processEvents()
