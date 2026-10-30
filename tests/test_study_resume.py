import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_ui import APP
from PyQt5.QtTest import QTest
from learning_ui import CourseReader
from learning_content import BUILTIN_COURSE_ID
from model import make_task
from runtime import MomoRuntime


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'tasks.json'
        self.runtime=MomoRuntime(self.path,auto_tick=False).start(); self.tasks=self.runtime.tasks; self.tasks.clear_samples()
        self.task=self.tasks.add(make_task('学习课程','learn')); self.client=self.runtime.learning; self.readers=[]
        self.client.bind(self.task['id'],BUILTIN_COURSE_ID)

    def tearDown(self):
        for reader in self.readers: reader.close(); reader.deleteLater()
        APP.processEvents(); self.runtime.close(); self.temp.cleanup()

    def open(self):
        reader=CourseReader(None,self.client,self.task['id'],BUILTIN_COURSE_ID); self.readers.append(reader)
        reader.show(); reader.tabs.setCurrentWidget(reader.course_page); QTest.qWait(60); return reader

    def value(self):
        lesson=self.client.course(BUILTIN_COURSE_ID,self.task['id'])['package']['lessons'][1]
        return dict(task_id=self.task['id'],course_id=BUILTIN_COURSE_ID,lesson_id=lesson['id'],lesson_revision=lesson['revision'],scroll_ratio=.5,question='尚未发送的问题')

    def test_service_restart_preserves_draft_without_changing_learning_revision_or_task(self):
        value=self.value(); before=self.client.revision(); task=self.tasks.get(self.task['id'])
        self.client.save_study(**value); self.assertEqual(self.client.revision(),before)
        self.runtime.close(); self.runtime=MomoRuntime(self.path,auto_tick=False).start(); self.client=self.runtime.learning
        saved=self.client.study_state(self.task['id'],BUILTIN_COURSE_ID)
        self.assertEqual(saved['question'],value['question']); self.assertEqual(saved['scroll_ratio'],.5)
        self.assertEqual(self.tasks.get(self.task['id']),task)
        self.assertEqual(self.client.tutor_snapshot(self.task['id'],BUILTIN_COURSE_ID)['turns'],[])

    def test_reader_reopens_chapter_scroll_and_unsent_question(self):
        reader=self.open(); reader.lessons.setCurrentRow(1); QTest.qWait(40)
        bar=reader.scroll.verticalScrollBar(); self.assertGreater(bar.maximum(),0)
        bar.setValue(round(bar.maximum()*.55)); reader.tutor.question.setPlainText('这里的力矩符号怎么理解？')
        self.assertTrue(reader.close()); reopened=self.open()
        self.assertEqual(reopened.lessons.currentRow(),1)
        bar=reopened.scroll.verticalScrollBar(); self.assertAlmostEqual(bar.value()/bar.maximum(),.55,delta=.03)
        self.assertEqual(reopened.tutor.question.toPlainText(),'这里的力矩符号怎么理解？')
        self.assertEqual(self.client.tutor_snapshot(self.task['id'],BUILTIN_COURSE_ID)['turns'],[])

    def test_invalid_positions_and_other_tasks_cannot_corrupt_saved_state(self):
        value=self.value(); self.client.save_study(**value); saved=self.client.study_state(self.task['id'],BUILTIN_COURSE_ID)
        for bad in (-.1,1.1,float('nan'),True):
            with self.assertRaises(ValueError): self.client.save_study(**{**value,'scroll_ratio':bad})
        other=self.tasks.add(make_task('另一个学习任务','learn'))
        self.assertEqual(self.client.study_state(other['id'],BUILTIN_COURSE_ID),{})
        work=self.tasks.add(make_task('工作任务','work'))
        with self.assertRaises(ValueError): self.client.save_study(**{**value,'task_id':work['id']})
        self.assertEqual(self.client.study_state(self.task['id'],BUILTIN_COURSE_ID),saved)

    def test_failed_save_keeps_draft_and_prevents_reader_close(self):
        reader=self.open(); reader.tutor.question.setPlainText('不能丢失的未发送问题')
        before=self.client.study_state(self.task['id'],BUILTIN_COURSE_ID)
        with patch('services.LearningService.atomic_json',side_effect=OSError('disk full')):
            self.assertFalse(reader.close())
        self.assertTrue(reader.isVisible()); self.assertIn('disk full',reader.notice.text())
        self.assertEqual(reader.tutor.question.toPlainText(),'不能丢失的未发送问题')
        self.assertEqual(self.client.study_state(self.task['id'],BUILTIN_COURSE_ID),before)


if __name__=='__main__':unittest.main()
