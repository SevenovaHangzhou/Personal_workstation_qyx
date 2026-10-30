import copy
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QComboBox, QPushButton, QLabel
from PyQt5.QtTest import QTest
from runtime import MomoRuntime
from model import make_task
from learning_content import (BUILTIN_COURSE_ID, SUBJECT_MODE, combine_legacy_course,
                              legacy_catalog, ordered_lessons, validate_package)
from learning_runner import CourseRunner, ROOT
from learning_ui import LearningDialog, CourseReader
from main import Momo
from test_ui import APP


def package(job):
    chapters=legacy_catalog(ROOT/'assets/learning/zhixing.json')
    if job['input'].get('mode')==SUBJECT_MODE:
        p=combine_legacy_course(dict(list(chapters.items())[:2]))['package']
        first,second=p['lessons'];u1,u2=p['knowledge_units']
        u2['prerequisite_ids']=[u1['id']];first['depends_on']=[];second['depends_on']=[first['id']]
        p['coverage']=[{'id':'basics','title':'基础概念与映射','knowledge_unit_ids':[u1['id'],u2['id']],'gaps':['此为测试资料，未核实外部来源']}]
        p['learning_path']=[{'id':'foundation','title':'从基础出发','summary':'先建立速度映射的直觉','lesson_ids':[first['id']]},
                            {'id':'application','title':'连接力与应用','summary':'基于速度映射进入功率与力映射','lesson_ids':[second['id']]}]
    else:p=copy.deepcopy(next(iter(chapters.values()))['package'])
    p.pop('legacy_original',None);p['generation_id']=job['id'];p['input']=copy.deepcopy(job['input']);p['input']['topic_id']='test'
    p['search_queries']=['fixture 本地知识检索']
    for unit in p['knowledge_units']:unit['claims']=[{'statement':'测试结论','conditions':'测试条件','location':'本地测试资料','source_ids':unit['source_ids']}]
    return p


class ControlledRunner:
    def __init__(self): self.started=threading.Event();self.release=threading.Event();self.calls=[];self.result='review'
    def run(self,job,folder,cancel,progress):
        self.calls.append(job); self.started.set(); progress('正在检索官方资料','generated-chat')
        while not self.release.wait(.01):
            if cancel.is_set(): raise InterruptedError('已停止')
        if self.result=='failed': raise RuntimeError('fixture failure')
        if self.result=='needs_input':return {'status':'needs_input','message':'请澄清学习对象的同名歧义','course_json':'','review':''}
        p=package(job)
        if self.result=='invalid':p['lessons'][0]['sections'][0]['blocks']=[{'type':'interactive','component':'arbitrary-code'}]
        return {'status':'review','message':'完成草稿','course_json':json.dumps(p,ensure_ascii=False),'review':'结构校验与教学核查分别记录；此为测试内容。'}


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start();self.tasks=self.runtime.tasks;self.tasks.clear_samples()
        self.task=self.tasks.add(make_task('雅可比：从关节到末端','learn'));self.client=self.runtime.learning
        self.runner=ControlledRunner();self.runtime.learning_service.runner=self.runner

    def tearDown(self):self.runtime.close();self.temp.cleanup()

    def start(self,task=None,concurrency=1):return self.client.start((task or self.task)['id'],(task or self.task)['title'],concurrency)

    def wait_done(self):
        deadline=time.monotonic()+5
        while self.client.snapshot()['busy'] and time.monotonic()<deadline:time.sleep(.01)
        self.assertFalse(self.client.snapshot()['busy'])

    def test_explicit_binding_preserves_ids_answers_and_survives_restart(self):
        self.assertEqual(len(self.client.catalog()),1)
        self.assertIsNone(self.client.snapshot(self.task['id'])['course'])  # Exact title still does not auto-bind.
        self.client.bind(self.task['id'],'zhixing:j-velocity')
        course=self.client.course('zhixing:j-velocity',self.task['id']);lesson=course['package']['lessons'][0];q=lesson['questions'][0]
        self.client.answer(task_id=self.task['id'],course_id=course['id'],lesson_id=lesson['id'],question_id=q['id'],text=q['options'][1]['id'])
        self.client.bind(self.task['id'],None);self.client.bind(self.task['id'],course['id'])
        self.runtime.close();self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start();self.client=self.runtime.learning
        restored=self.client.course(course['id'],self.task['id'])
        self.assertEqual(len(restored['answers']),1);self.assertEqual(restored['package']['lessons'][0]['id'],'j-velocity')
        self.assertEqual(self.tasks.get(self.task['id'])['status'],'todo')

    def test_background_generation_progress_chat_binding_and_validated_result(self):
        job=self.start();self.assertTrue(self.runner.started.wait(2));state=self.client.snapshot(self.task['id'])
        self.assertTrue(state['busy']);self.assertIsNone(state['course'])
        with self.assertRaisesRegex(ValueError,'已有课程'):self.start()
        self.runner.release.set();self.wait_done();state=self.client.snapshot(self.task['id'])
        self.assertEqual(state['jobs'][0]['status'],'review');self.assertEqual(state['course']['id'],'generated:'+job['id'])
        self.assertTrue(any(b['url']=='codex://threads/generated-chat' for b in self.tasks.get(self.task['id'])['bindings']))
        self.assertTrue((self.root/'learning/courses'/(job['id']+'.json')).is_file())
        self.assertEqual(self.tasks.get(self.task['id'])['status'],'todo')

    def test_changed_task_context_or_newer_explicit_binding_is_preserved(self):
        self.start();self.assertTrue(self.runner.started.wait(2));self.client.bind(self.task['id'],'zhixing:j-force')
        self.tasks.update(self.task['id'],note='增加了新的疑问')
        self.runner.release.set();self.wait_done()
        state=self.client.snapshot(self.task['id']);self.assertEqual(state['course']['id'],'zhixing:j-force')
        self.assertIn('已保留',state['jobs'][0]['message']);self.assertEqual(len(self.client.catalog()),2)

    def test_explicit_unbind_during_generation_is_not_overridden(self):
        self.start();self.assertTrue(self.runner.started.wait(2));self.client.bind(self.task['id'],None)
        self.runner.release.set();self.wait_done()
        self.assertIsNone(self.client.snapshot(self.task['id'])['course'])
        self.assertEqual(self.client.snapshot(self.task['id'])['jobs'][0]['status'],'review')

    def test_invalid_content_is_retained_as_failure_without_course_binding(self):
        self.runner.result='invalid';self.runner.release.set();self.start();self.wait_done()
        state=self.client.snapshot(self.task['id']);self.assertEqual(state['jobs'][0]['status'],'failed');self.assertIsNone(state['course'])
        self.assertIn('尚未支持',state['jobs'][0]['message'])

    def test_needs_input_does_not_claim_tutorial_exists(self):
        self.runner.result='needs_input';self.runner.release.set();self.start();self.wait_done()
        state=self.client.snapshot(self.task['id']);self.assertEqual(state['jobs'][0]['status'],'needs_input');self.assertIsNone(state['course'])

    def test_queue_limit_cancel_and_exit_stop_workers(self):
        first=self.start();self.assertTrue(self.runner.started.wait(2));other=self.tasks.add(make_task('另一课','learn'))
        queued=self.start(other);self.assertEqual(queued['status'],'queued');self.assertEqual(len(self.runner.calls),1)
        self.client.cancel(queued['id']);self.client.cancel(first['id']);self.wait_done()
        self.assertEqual(len(self.runner.calls),1)
        self.start();self.runtime.close();self.assertFalse(self.runtime.learning_service.workers)
        self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start();self.client=self.runtime.learning
        self.assertFalse(self.client.snapshot()['busy'])

    def test_only_learn_and_subject_are_required(self):
        work=self.tasks.add(make_task('工作'))
        with self.assertRaisesRegex(ValueError,'只有学习任务'):self.client.bind(work['id'],'zhixing:j-velocity')
        with self.assertRaisesRegex(ValueError,'只有学习任务'):self.start(work)
        with self.assertRaises(ValueError):self.client.start(self.task['id'],' ')

    def test_subject_only_builds_knowledge_base_path_and_survives_restart(self):
        self.runner.release.set();job=self.start();self.wait_done()
        self.assertEqual(job['input']['subject'],self.task['title'])
        self.assertEqual(job['input']['mode'],SUBJECT_MODE)
        self.assertFalse({'goal','background','scope'} & job['input'].keys())
        folder=self.root/'learning/generations'/job['id']
        knowledge=json.loads((folder/'knowledge-base.json').read_text())
        path=json.loads((folder/'learning-path.json').read_text())
        self.assertEqual(len(knowledge['knowledge_units']),2)
        self.assertTrue(knowledge['coverage'][0]['gaps'])
        self.assertEqual(path['lessons'][1]['depends_on'],[path['lessons'][0]['id']])
        self.assertEqual(path['stages'][0]['lesson_ids'],[path['lessons'][0]['id']])
        self.runtime.close();self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start();self.client=self.runtime.learning
        course=self.client.course('generated:'+job['id'],self.task['id'])
        self.assertEqual(course['package']['input']['subject'],self.task['title'])
        self.assertEqual(course['package']['learning_path'],path['stages'])

    def test_curriculum_rejects_uncovered_unplanned_and_misordered_prerequisites(self):
        job={'id':'fixture','input':{'task_id':'task','mode':SUBJECT_MODE,'subject':'雅可比','input_version':'v2'}}
        original=package(job);first,second=[lesson['id'] for lesson in original['lessons']]
        changes=[('personal_input',lambda p:p['input'].update(background='推测用户已掌握')),
                 ('no_coverage',lambda p:p.pop('coverage')),
                 ('uncovered_unit',lambda p:p['coverage'][0]['knowledge_unit_ids'].pop()),
                 ('unknown_unit',lambda p:p['coverage'][0]['knowledge_unit_ids'].append('missing')),
                 ('unplanned_lesson',lambda p:p['learning_path'].pop()),
                 ('duplicate_lesson',lambda p:p['learning_path'][1]['lesson_ids'].append(first)),
                 ('untaught_prerequisite',lambda p:p['lessons'][1].update(depends_on=[])),
                 ('later_stage_prerequisite',lambda p:p['lessons'][0].update(depends_on=[second]))]
        for name,change in changes:
            with self.subTest(name=name):
                p=copy.deepcopy(original);change(p)
                with self.assertRaises(ValueError):validate_package(p,job)
        p=copy.deepcopy(original);p['learning_path']=[dict(id='one',title='同一阶段',summary='测试循环依赖',lesson_ids=[first,second])]
        p['lessons'][0]['depends_on']=[second]
        with self.assertRaisesRegex(ValueError,'循环'):validate_package(p,job)
        p=copy.deepcopy(original);p['lessons'].reverse()
        p['learning_path']=[dict(id='one',title='同一阶段',summary='依据依赖排序',lesson_ids=[second,first])]
        validate_package(p,job);self.assertEqual([lesson['id'] for lesson in ordered_lessons(p)],[first,second])

    def test_legacy_generated_course_still_loads_without_new_curriculum_fields(self):
        old_job={'id':'old-fixture','input':{'task_id':self.task['id'],'goal':'旧目标','background':'旧基础','scope':'旧范围','input_version':'v1'}}
        old_course={'id':'generated:old-fixture','title':'历史课程','origin':'旧版生成','package':package(old_job),'review':'历史核查记录'}
        self.runtime.close();folder=self.root/'learning/courses';folder.mkdir(exist_ok=True)
        (folder/'old-fixture.json').write_text(json.dumps(old_course,ensure_ascii=False))
        path=self.root/'learning/state.json';state=json.loads(path.read_text())
        question='请填写你的目标和掌握程度'
        state['jobs'][old_job['id']]={**old_job,'status':'needs_input','message':question,'created_at':'2026-10-28T00:00:00+00:00','thread_id':''}
        path.write_text(json.dumps(state,ensure_ascii=False))
        self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start();self.client=self.runtime.learning
        self.client.bind(self.task['id'],old_course['id'])
        loaded=self.client.course(old_course['id'],self.task['id'])
        self.assertEqual(loaded['package'],old_course['package']);self.assertEqual(len(ordered_lessons(loaded['package'])),1)
        window=Momo(self.runtime)
        try:
            window.open_learning(self.task['id'],1);dialog=window.learning_dialogs[self.task['id']]
            self.assertNotIn(question,dialog.notice.text());self.assertTrue(dialog.create_button.isEnabled())
            self.assertEqual(dialog.subject.text(),self.task['title'])
        finally:window.exiting=True;window.close();window.deleteLater();APP.processEvents()

    def test_create_from_subject_without_questionnaire_and_open_path_chapter(self):
        window=Momo(self.runtime);window.show();APP.processEvents()
        try:
            window.open_learning(self.task['id'],1);dialog=window.learning_dialogs[self.task['id']]
            self.assertEqual(dialog.subject.text(),self.task['title'])
            self.assertFalse(any(hasattr(dialog,name) for name in ('goal','background','scope')))
            QTest.mouseClick(dialog.create_button,Qt.LeftButton);self.assertTrue(self.runner.started.wait(2))
            self.runner.release.set();self.wait_done();dialog.refresh();dialog.read()
            course_id=self.client.snapshot(self.task['id'])['course']['id'];reader=dialog.readers[course_id]
            self.assertIs(reader.tabs.currentWidget(),reader.path_page)
            second=reader.lesson_rows[1]
            action=next(b for b in reader.path_page.findChildren(QPushButton) if b.text()==second['title'])
            QTest.mouseClick(action,Qt.LeftButton)
            self.assertIs(reader.tabs.currentWidget(),reader.course_page);self.assertEqual(reader.lessons.currentRow(),1)
            self.assertIsNone(QApplication.activeModalWidget())
        finally:window.exiting=True;window.close();window.deleteLater();APP.processEvents()

    def test_validator_rejects_missing_refs_duplicate_questions_and_knowledge_cycle(self):
        job={'id':'fixture','input':{'task_id':'task','goal':'goal','background':'background','scope':'scope','input_version':'v1'}}
        original=package(job);validate_package(original,job)
        p=copy.deepcopy(original);p['lessons'][0]['source_ids']=['missing']
        with self.assertRaises(ValueError):validate_package(p,job)
        p=copy.deepcopy(original);p['lessons'][0]['questions'].append(copy.deepcopy(p['lessons'][0]['questions'][0]))
        with self.assertRaises(ValueError):validate_package(p,job)
        p=copy.deepcopy(original);p['knowledge_units'][0]['prerequisite_ids']=[p['knowledge_units'][0]['id']]
        with self.assertRaises(ValueError):validate_package(p,job)

    def test_card_creation_highlight_and_native_reader_restore_answers(self):
        window=Momo(self.runtime);window.show();window.navigate('learn');window.select_task(self.task['id']);APP.processEvents()
        try:
            self.assertTrue(any(b.text()=='创建课程' and b.objectName()=='coral' for b in window.list_host.findChildren(QPushButton)))
            dialog=LearningDialog(window,self.task['id'],2);dialog.show();APP.processEvents()
            try:
                for i in range(dialog.courses.count()):
                    if dialog.courses.item(i).data(256)==BUILTIN_COURSE_ID:dialog.courses.setCurrentRow(i);break
                self.assertEqual(dialog.courses.count(),1);self.assertIn('14 章',dialog.courses.item(0).text())
                dialog.bind();self.assertEqual(self.client.snapshot(self.task['id'])['course']['id'],BUILTIN_COURSE_ID)
                reader=CourseReader(dialog,self.client,self.task['id'],BUILTIN_COURSE_ID);reader.show();APP.processEvents()
                self.assertEqual(reader.lessons.count(),14)
                editor=reader.findChild(QComboBox,'answer_q18');editor.setCurrentIndex(2);QTest.qWait(550)
                reader.lessons.setCurrentRow(13);APP.processEvents()
                self.assertEqual(reader.lessons.currentRow(),13)
                self.assertTrue(any('从任务需求选择控制结构' in widget.text() for widget in reader.findChildren(QLabel)))
                reader.lessons.setCurrentRow(0);APP.processEvents()
                self.assertEqual(reader.findChild(QComboBox,'answer_q18').currentIndex(),2)
                reader.accept();reader.deleteLater();APP.processEvents()
                restored=CourseReader(dialog,self.client,self.task['id'],BUILTIN_COURSE_ID)
                self.assertEqual(restored.findChild(QComboBox,'answer_q18').currentIndex(),2)
                self.assertEqual(self.tasks.get(self.task['id'])['status'],'todo')
                restored.accept();restored.deleteLater()
            finally:dialog.accept();dialog.deleteLater();APP.processEvents()
        finally:window.exiting=True;window.close();window.deleteLater();APP.processEvents()

    def test_whole_course_preserves_all_chapters_questions_and_generated_courses(self):
        chapters=legacy_catalog(ROOT/'assets/learning/zhixing.json')
        catalog=self.client.catalog();self.assertEqual([c['id'] for c in catalog],[BUILTIN_COURSE_ID])
        self.assertEqual(catalog[0]['lesson_count'],14);self.assertEqual(catalog[0]['question_count'],48)
        course=self.client.course(BUILTIN_COURSE_ID,self.task['id']);validate_package(course['package'])
        self.assertEqual(course['package']['lessons'],[lesson for c in chapters.values() for lesson in c['package']['lessons']])
        self.assertEqual(course['package']['knowledge_units'],[unit for c in chapters.values() for unit in c['package']['knowledge_units']])
        self.assertEqual(sum(len(l['questions']) for l in course['package']['lessons']),48)
        # A newly generated course remains a separate course in the binding list.
        self.runner.release.set();job=self.start();self.wait_done()
        catalog=self.client.catalog();self.assertEqual({c['id'] for c in catalog},{BUILTIN_COURSE_ID,'generated:'+job['id']})
        self.assertEqual(next(c for c in catalog if c['id']==BUILTIN_COURSE_ID)['lesson_count'],14)

    def test_old_chapter_answers_are_visible_in_whole_course_without_rewriting_bindings(self):
        old_id='zhixing:j-velocity';another_id='zhixing:j-force'
        first=self.client.course(old_id,self.task['id'])['package']['lessons'][0]
        second=self.client.course(another_id,self.task['id'])['package']['lessons'][0]
        key=lambda lesson:json.dumps([lesson['id'],lesson['revision'],lesson['questions'][0]['id']],ensure_ascii=False)
        self.client.bind(self.task['id'],old_id);self.runtime.close()
        path=self.root/'learning/state.json';state=json.loads(path.read_text())
        state['answers'][self.task['id']]={old_id:{key(first):'0'},another_id:{key(second):'1'}}
        path.write_text(json.dumps(state,ensure_ascii=False))
        self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start();self.client=self.runtime.learning
        self.assertEqual(self.client.snapshot(self.task['id'])['course']['id'],old_id)
        self.assertEqual(self.client.course(old_id,self.task['id'])['answers'],{key(first):'0'})
        self.client.bind(self.task['id'],BUILTIN_COURSE_ID)
        self.assertEqual(self.client.course(BUILTIN_COURSE_ID,self.task['id'])['answers'],{key(first):'0',key(second):'1'})
        # Both old and whole-course readers write to the same record after the update.
        self.client.answer(task_id=self.task['id'],course_id=old_id,lesson_id=first['id'],question_id=first['questions'][0]['id'],text='1')
        self.assertEqual(self.client.course(BUILTIN_COURSE_ID,self.task['id'])['answers'][key(first)],'1')
        self.client.answer(task_id=self.task['id'],course_id=BUILTIN_COURSE_ID,lesson_id=second['id'],question_id=second['questions'][0]['id'],text='0')
        self.assertEqual(self.client.course(another_id,self.task['id'])['answers'][key(second)],'0')
        other=self.tasks.add(make_task('同一课程的另一项学习','learn'))
        self.assertEqual(self.client.course(BUILTIN_COURSE_ID,other['id'])['answers'],{})
        saved=json.loads(path.read_text())['answers'][self.task['id']]
        self.assertEqual(saved[old_id],{key(first):'0'});self.assertEqual(saved[another_id],{key(second):'1'})
        self.runtime.close();self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start();self.client=self.runtime.learning
        self.assertEqual(self.client.snapshot(self.task['id'])['course']['id'],BUILTIN_COURSE_ID)
        self.assertEqual(self.client.course(BUILTIN_COURSE_ID,self.task['id'])['answers'],{key(first):'1',key(second):'0'})

    def test_main_window_remains_usable_during_generation_and_panel_reopens(self):
        other=self.tasks.add(make_task('另一件日常任务','life'))
        window=Momo(self.runtime);window.show();window.navigate('learn');window.select_task(self.task['id']);APP.processEvents()
        try:
            with patch.object(LearningDialog,'exec_',side_effect=AssertionError('学习面板不得阻塞主窗口')):
                window.open_learning(self.task['id'],1)
            dialog=window.learning_dialogs[self.task['id']]
            self.assertEqual(dialog.windowModality(),Qt.NonModal);self.assertIsNone(QApplication.activeModalWidget())
            dialog.subject.setText('平面雅可比')
            # A later panel action must not overwrite edits made in the main window.
            window.note.setPlainText('开始建课前尚未保存的笔记')
            QTest.mouseClick(dialog.create_button,Qt.LeftButton)
            self.assertTrue(self.runner.started.wait(2));self.assertTrue(self.client.snapshot()['busy'])
            self.assertEqual(self.tasks.get(self.task['id'])['note'],'开始建课前尚未保存的笔记')
            self.assertEqual(self.runner.calls[0]['input']['note'],'开始建课前尚未保存的笔记')
            QTest.mouseClick(window.nav_buttons['life'],Qt.LeftButton);self.assertEqual(window.view,'life')
            window.select_task(other['id']);window.note.setPlainText('生成期间编辑另一任务');QTest.qWait(550)
            self.assertEqual(self.tasks.get(other['id'])['note'],'生成期间编辑另一任务')
            self.assertTrue(dialog.close());self.assertNotIn(self.task['id'],window.learning_dialogs)
            self.assertTrue(self.client.snapshot()['busy']);self.assertEqual(len(self.runner.calls),1)
            window.open_learning(self.task['id']);reopened=window.learning_dialogs[self.task['id']]
            self.assertFalse(reopened.create_button.isEnabled());self.assertTrue(reopened.progress.isVisible())
            self.assertEqual(len(self.runner.calls),1);self.assertEqual(window.selected,other['id'])
            self.runner.release.set();self.wait_done();window.tick();reopened.refresh()
            self.assertEqual(window.course_action(self.task['id'])[1],'开始学习')
            self.assertEqual(reopened.state['jobs'][0]['status'],'review')
            self.assertEqual(window.note.toPlainText(),'生成期间编辑另一任务')
        finally:window.exiting=True;window.close();window.deleteLater();APP.processEvents()

    def test_learning_windows_reuse_drafts_and_hide_to_tray_without_stopping_generation(self):
        other=self.tasks.add(make_task('另一项学习','learn'))
        window=Momo(self.runtime);window.show();APP.processEvents()
        try:
            window.open_learning(self.task['id'],1);first=window.learning_dialogs[self.task['id']]
            first.subject.setText('尚未提交的学习对象')
            window.open_learning(self.task['id'],2)
            self.assertIs(window.learning_dialogs[self.task['id']],first)
            self.assertEqual(first.subject.text(),'尚未提交的学习对象')
            window.open_learning(other['id'],1);second=window.learning_dialogs[other['id']]
            self.assertEqual(second.task_id,other['id']);self.assertEqual(len(window.learning_dialogs),2)
            self.client.bind(self.task['id'],'zhixing:j-velocity');first.refresh();first.read()
            reader=first.readers['zhixing:j-velocity']
            job=self.start();self.assertTrue(self.runner.started.wait(2))
            with patch.object(window.tray,'isVisible',return_value=True):
                self.assertFalse(window.close())
            self.assertFalse(window.isVisible());self.assertFalse(first.isVisible());self.assertFalse(second.isVisible());self.assertFalse(reader.isVisible())
            self.assertFalse(first.timer.isActive());self.assertFalse(second.timer.isActive())
            self.assertTrue(self.client.snapshot()['busy']);self.assertEqual(len(self.runner.calls),1)
            window.restore_window();window.open_learning(self.task['id'],1)
            self.assertIs(window.learning_dialogs[self.task['id']],first);self.assertTrue(first.timer.isActive())
            self.assertEqual(first.subject.text(),'尚未提交的学习对象')
            self.assertEqual(self.client.snapshot(self.task['id'])['jobs'][0]['id'],job['id'])
        finally:window.exiting=True;window.close();window.deleteLater();APP.processEvents()

    def test_reader_is_nonmodal_and_exit_flushes_answers_or_preserves_failed_edits(self):
        self.client.bind(self.task['id'],'zhixing:j-velocity')
        window=Momo(self.runtime);window.show();APP.processEvents()
        try:
            window.open_learning(self.task['id']);dialog=window.learning_dialogs[self.task['id']]
            with patch.object(CourseReader,'exec_',side_effect=AssertionError('阅读器不得阻塞主窗口')):
                dialog.read()
            reader=dialog.readers['zhixing:j-velocity'];dialog.read()
            self.assertIs(dialog.readers['zhixing:j-velocity'],reader);self.assertIsNone(QApplication.activeModalWidget())
            editor=reader.findChild(QComboBox,'answer_q18');editor.setCurrentIndex(2)
            self.assertTrue(reader.dirty)
            with patch.object(reader.client,'answer',side_effect=OSError('fixture 保存失败')):
                window.exiting=True;self.assertFalse(window.close())
                self.assertTrue(window.isVisible());self.assertTrue(reader.isVisible());self.assertTrue(reader.dirty)
                self.assertIn('保存失败',reader.notice.text())
            self.assertTrue(window.close());self.assertFalse(window.learning_dialogs)
            answers=self.client.course('zhixing:j-velocity',self.task['id'])['answers']
            self.assertEqual(len(answers),1)
            restored=CourseReader(window,self.client,self.task['id'],'zhixing:j-velocity')
            self.assertEqual(restored.findChild(QComboBox,'answer_q18').currentIndex(),2)
            restored.close();restored.deleteLater()
        finally:window.exiting=True;window.close();window.deleteLater();APP.processEvents()


class RunnerTests(unittest.TestCase):
    def test_real_fake_process_receives_standard_and_preserves_system_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fake=root/'fixture-codex'
            fake.write_text('#!/usr/bin/env python3\nimport json,sys,os\nfrom pathlib import Path\nprompt=sys.stdin.read()\nPath("received.txt").write_text(prompt)\nPath("env.json").write_text(json.dumps({k:os.environ.get(k) for k in ("CODEX_HOME","LINEAR_API_KEY")}))\nprint(json.dumps({"type":"thread.started","thread_id":"fixture-chat"}),flush=True)\nPath(sys.argv[sys.argv.index("-o")+1]).write_text(json.dumps({"status":"needs_input","message":"请澄清学习对象","course_json":"","review":""}))\n')
            fake.chmod(0o700);folder=root/'generation';events=[]
            # The fake CLI uses its process cwd like the real CLI -C option.
            original_popen=__import__('subprocess').Popen
            def launch(cmd,**kwargs):kwargs['cwd']=folder;return original_popen(cmd,**kwargs)
            with patch('learning_runner.shutil.which',return_value=str(fake)),patch('learning_runner.subprocess.Popen',side_effect=launch),patch.dict(os.environ,{'CODEX_HOME':'/fixture/system-config','LINEAR_API_KEY':'fixture-secret'}):
                result=CourseRunner().run({'id':'fixture','input':{'task_id':'task','mode':SUBJECT_MODE,'subject':'雅可比','input_version':'v2'}},folder,threading.Event(),lambda *values:events.append(values))
            self.assertEqual(result['status'],'needs_input');self.assertIn(('Codex 已开始检索和编写课程','fixture-chat'),events)
            prompt=(folder/'received.txt').read_text();self.assertIn('课程知识标准',prompt);self.assertIn('真实',prompt)
            env=json.loads((folder/'env.json').read_text());self.assertEqual(env['CODEX_HOME'],'/fixture/system-config');self.assertIsNone(env['LINEAR_API_KEY'])
