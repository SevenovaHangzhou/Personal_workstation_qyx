"""Chapter-local reference extraction and the fixed native study area."""
import copy
import unittest
from PyQt5.QtCore import QPoint
from PyQt5.QtTest import QTest
from learning_content import combine_legacy_course, legacy_catalog
from learning_runner import ROOT
from learning_knowledge import lesson_keypoints, keypoint_excerpt
from learning_ui import CourseReader
from test_ui import APP


def reference_course():
    course = combine_legacy_course(legacy_catalog(ROOT/'assets/learning/zhixing.json'))
    course['answers'] = {}
    unit = course['package']['knowledge_units'][0]
    unit['claims'] = [dict(statement='速度映射只描述当前位形的瞬时关系。',
                          conditions='在一致坐标系下计算，不用常量 J 替代有限运动。',
                          location='测试资料 · 速度章节', source_ids=unit['source_ids']),
                      '先写出关节量与末端量的定义。']
    return course


class ReferenceClient:
    def __init__(self, course):
        self.data = course; self.asked = []
    def course(self, *args): return copy.deepcopy(self.data)
    def tutor_snapshot(self, *args): return dict(revision=1, busy=False, turns=[])
    def tutor_ask(self, *args): self.asked.append(args); return self.tutor_snapshot()


class ReferenceContentTests(unittest.TestCase):
    def test_formula_recap_is_first_and_content_is_not_rewritten(self):
        course = reference_course(); package = course['package']; before = copy.deepcopy(package)
        lesson = package['lessons'][0]; points = lesson_keypoints(package, lesson['id'])
        self.assertEqual(points[0]['title'], '公式回顾')
        formula = next(block['text'] for section in lesson['sections'] if section['title']=='公式回顾'
                       for block in section['blocks'] if block['type']=='formula')
        self.assertEqual(points[0]['text'], formula)
        self.assertEqual(package, before)
        self.assertEqual(points[0]['scope'], lesson['scope'])
        self.assertEqual(points[0]['source_label'], '章节来源')

    def test_claims_keep_conditions_locations_sources_and_legacy_strings(self):
        package = reference_course()['package']; points = lesson_keypoints(package, package['lessons'][0]['id'])
        claim, legacy = [point for point in points if point['kind']=='claim']
        excerpt = keypoint_excerpt(claim)
        self.assertIn('在一致坐标系下计算', excerpt); self.assertIn('测试资料 · 速度章节', excerpt)
        self.assertEqual(claim['source_ids'], package['knowledge_units'][0]['source_ids'])
        self.assertEqual(legacy['text'], '先写出关节量与末端量的定义。')
        self.assertEqual(legacy['conditions'], '')
        self.assertIn('结论来源', keypoint_excerpt(legacy))

    def test_only_explicitly_linked_knowledge_and_formula_blocks_are_included(self):
        package = reference_course()['package']; lesson = package['lessons'][0]
        lesson['sections'][0]['blocks'].extend([
            dict(type='paragraph', text='普通段落秘密'),
            dict(type='self_check', prompt='思考问题', answer='不能提前公开的自测答案')])
        package['knowledge_units'][1]['claims'] = ['另一个章节的结论']
        lesson['questions'][0]['reference_process'] = '不能提前公开的练习答案'
        text = '\n'.join(keypoint_excerpt(point) for point in lesson_keypoints(package, lesson['id']))
        for secret in ['普通段落秘密', '不能提前公开的自测答案', '不能提前公开的练习答案', '另一个章节的结论']:
            self.assertNotIn(secret, text)
        with self.assertRaises(ValueError): lesson_keypoints(package, 'missing')

    def test_chapter_without_formulas_still_has_claims_or_an_explicit_empty_state(self):
        package = reference_course()['package']; lesson = package['lessons'][0]
        lesson['sections'] = []
        self.assertEqual([point['kind'] for point in lesson_keypoints(package, lesson['id'])], ['claim', 'claim'])
        package['knowledge_units'][0]['claims'] = []
        self.assertEqual(lesson_keypoints(package, lesson['id']), [])


class ReferenceUiTests(unittest.TestCase):
    def setUp(self):
        self.client = ReferenceClient(reference_course())
        self.reader = CourseReader(None, self.client, 'reference-task', self.client.data['id'])
        self.reader.show(); self.reader.tabs.setCurrentWidget(self.reader.course_page); QTest.qWait(40)

    def tearDown(self):
        self.reader.close(); self.reader.deleteLater(); APP.processEvents()

    def test_reference_does_not_move_or_scroll_with_body_and_updates_on_chapter_change(self):
        panel = self.reader.knowledge; start = panel.mapTo(self.reader, QPoint(0, 0))
        panel.content.verticalScrollBar().setValue(10)
        reference_position = panel.content.verticalScrollBar().value()
        self.reader.scroll.verticalScrollBar().setValue(self.reader.scroll.verticalScrollBar().maximum())
        APP.processEvents()
        self.assertEqual(panel.mapTo(self.reader, QPoint(0, 0)), start)
        self.assertEqual(panel.content.verticalScrollBar().value(), reference_position)
        self.assertEqual(panel.points[0]['title'], '公式回顾')
        self.reader.lessons.setCurrentRow(1); APP.processEvents()
        self.assertEqual(panel.lesson_id, self.reader.lesson_rows[1]['id'])
        self.assertIn('τₑₓₜ', panel.content.toPlainText())
        self.assertNotIn('先写出关节量', panel.content.toPlainText())

    def test_claim_detail_and_quote_keep_qualifiers_without_sending_a_question(self):
        panel = self.reader.knowledge
        panel.selector.setCurrentIndex(next(i for i, point in enumerate(panel.points) if point['kind']=='claim'))
        self.assertIn('在一致坐标系下计算', panel.content.toPlainText())
        self.assertIn('Modern Robotics', panel.content.toPlainText())
        self.reader.toggle_tutor(); APP.processEvents()
        self.assertTrue(self.reader.tutor.isHidden()); self.assertTrue(panel.isVisible())
        panel.quote_button.click(); APP.processEvents()
        self.assertTrue(self.reader.tutor.isVisible()); self.assertIn('适用条件', self.reader.tutor.excerpt)
        self.assertEqual(self.client.asked, []); self.assertFalse(self.reader.tutor.question.toPlainText())
        self.reader.lessons.setCurrentRow(1)
        self.assertEqual(self.reader.tutor.excerpt, '')

    def test_minimum_window_keeps_reference_question_and_actions_accessible_even_when_busy(self):
        reader = self.reader; reader.resize(1040, 640)
        reader.tutor.set_excerpt('需要解释的公式')
        reader.tutor.apply_state(dict(revision=2, busy=True, turns=[]))
        QTest.qWait(40)
        self.assertEqual((reader.width(), reader.height()), (1040, 640))
        self.assertGreaterEqual(reader.knowledge.height(), 170)
        self.assertGreaterEqual(reader.knowledge.content.height(), 65)
        self.assertGreaterEqual(reader.tutor.question.height(), 62)
        self.assertGreaterEqual(reader.tutor.transcript.height(), 50)
        for widget in [reader.knowledge.selector, reader.knowledge.content, reader.tutor.question,
                       reader.tutor.send_button, reader.tutor.stop_button]:
            self.assertTrue(widget.isVisible())
            origin = widget.mapTo(reader, QPoint(0, 0))
            self.assertTrue(reader.rect().contains(origin))
            self.assertTrue(reader.rect().contains(origin + QPoint(widget.width()-1, widget.height()-1)))
        reader.toggle_tutor(); APP.processEvents()
        self.assertTrue(reader.knowledge.isVisible()); self.assertFalse(reader.tutor.timer.isActive())
        reader.toggle_tutor(); APP.processEvents()
        self.assertTrue(reader.tutor.timer.isActive())

    def test_plain_course_text_is_escaped_and_empty_chapter_explains_why(self):
        lesson = self.reader.lesson_rows[0]
        lesson['sections'] = [dict(id='literal', title='公式回顾', blocks=[
            dict(type='formula', text='<script>literal</script>\nq < 0  &  x > 0')])]
        self.reader.knowledge.set_lesson(lesson)
        self.assertIn('<script>literal</script>', self.reader.knowledge.content.toPlainText())
        lesson['sections'] = []; lesson['knowledge_unit_ids'] = []
        self.reader.knowledge.set_lesson(lesson)
        self.assertIn('尚未收录', self.reader.knowledge.content.toPlainText())
        self.assertFalse(self.reader.knowledge.quote_button.isEnabled())


if __name__ == '__main__': unittest.main()
