import copy
import tempfile
import threading
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from PyQt5.QtCore import QPoint, QTimer
from PyQt5.QtWidgets import QPushButton
from model import JsonTaskRepository, make_task, parse_binding
from runtime import MomoRuntime
from main import Momo
from daily_engine import DailyEngine
from linear_support import LinearWriter
from test_ui import APP
from test_task_order import DragEnter, DragMove, Drop, TASK_MIME
from PyQt5.QtCore import QMimeData
from task_order_ui import TaskDropArea


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'tasks.json'
        self.runtime=MomoRuntime(self.path,auto_tick=False).start(); self.tasks=self.runtime.tasks; self.tasks.clear_samples()
        self.a=self.tasks.add(make_task('目标','work')); self.b=self.tasks.add(make_task('来源','work'))

    def tearDown(self): self.runtime.close(); self.temp.cleanup()

    def test_child_merge_preserves_subtree_notes_chat_and_issue_until_nightly(self):
        children=self.tasks.add_children(self.b['id'],['已有小步'])
        self.tasks.update(self.b['id'],note='继续的位置')
        self.tasks.bind(self.b['id'],'codex','codex://threads/old-chat')
        self.tasks.bind(self.b['id'],'linear','https://linear.app/test/issue/APP-2/b')
        before=self.tasks.get(self.b['id'])
        self.tasks.merge(self.b['id'],self.a['id'])
        result=self.tasks.get(self.b['id']); self.assertEqual(result['parent_id'],self.a['id'])
        self.assertEqual(result['note'],before['note']); self.assertEqual(result['bindings'],before['bindings'])
        self.assertEqual(result['steps'],before['steps']); self.assertEqual(self.tasks.get(children[0]['id'])['parent_id'],self.b['id'])
        self.assertEqual(result['linear_parent_pending']['parent_id'],self.a['id'])
        self.assertEqual(JsonTaskRepository(self.path).tasks,self.tasks.tasks)

    def test_root_follow_and_join_preserve_dependencies_and_block_cycles(self):
        c=self.tasks.add(make_task('另一方向'))
        self.tasks.merge(self.b['id'],self.a['id'],'after')
        self.tasks.merge(self.b['id'],c['id'],'after')
        self.assertIsNone(self.tasks.get(self.b['id'])['parent_id'])
        self.assertEqual(self.tasks.get(self.b['id'])['depends_on'],[self.a['id'],c['id']])
        before=self.path.read_bytes()
        with self.assertRaises(ValueError):self.tasks.merge(self.a['id'],self.b['id'],'after')
        self.assertEqual(self.path.read_bytes(),before)
        self.tasks.dependencies(self.b['id'],[]); self.tasks.delete(self.a['id'])

    def test_invalid_merge_and_save_failure_are_atomic(self):
        child=self.tasks.add_children(self.b['id'],['后代'])[0]
        other=self.tasks.add(make_task('学习','learn')); before=self.path.read_bytes(); revision=self.tasks.revision()
        for source,target,mode in [(self.b['id'],self.b['id'],'child'),(self.b['id'],child['id'],'after'),(other['id'],self.a['id'],'child')]:
            with self.assertRaises(ValueError): self.tasks.merge(source,target,mode)
        with patch.object(JsonTaskRepository,'save',side_effect=OSError('full')):
            with self.assertRaises(OSError):self.tasks.merge(self.b['id'],self.a['id'])
        self.assertEqual(self.path.read_bytes(),before); self.assertEqual(self.tasks.revision(),revision)
        self.assertIsNone(self.tasks.get(self.b['id'])['parent_id'])

    def test_move_with_existing_external_dependencies_requires_explicit_adjustment(self):
        self.tasks.merge(self.b['id'],self.a['id'],'after')
        c=self.tasks.add(make_task('父任务'))
        with self.assertRaisesRegex(ValueError,'先调整依赖'): self.tasks.merge(self.b['id'],c['id'],'child')

    def test_nightly_reparents_existing_issue_and_retains_newer_pending_revision(self):
        def issue(number,parent=''):
            return {'id':'issue-'+str(number),'identifier':'APP-'+str(number),'title':'工作',
                    'url':f'https://linear.app/test/issue/APP-{number}/task','state_type':'unstarted','state_name':'Todo',
                    'viewer_id':'me','team_id':'team','project_id':'','parent_issue_id':parent}
        parent,child=issue(1),issue(2)
        self.tasks.attach_issue(self.a['id'],parent); self.tasks.attach_issue(self.b['id'],child)
        self.tasks.merge(self.b['id'],self.a['id'])
        writer=Mock(); writer.issue.side_effect=lambda identifier: parent if identifier=='APP-1' else child
        writer.ensure_parent.return_value=issue(2,parent['id'])
        engine=DailyEngine(self.runtime.daily_service.store,self.tasks,lambda:writer,self.runtime.station)
        engine.sync_cutoff=datetime.now(timezone.utc)+timedelta(days=1)
        engine.synchronize(self.b['id'],{},threading.Event())
        self.assertNotIn('linear_parent_pending',self.tasks.get(self.b['id']))
        self.assertEqual(self.tasks.get(self.b['id'])['linear_issue']['parent_issue_id'],parent['id'])
        writer.ensure_issue.assert_not_called()
        pending=writer.ensure_parent.call_args.args[0]
        self.assertEqual(pending['payload'],{'issue_id':child['id'],'parent_id':parent['id']})
        # A newer local move cannot be acknowledged by an older in-flight sync.
        c=self.tasks.add(make_task('新父任务')); self.tasks.merge(self.b['id'],c['id'])
        newer=self.tasks.get(self.b['id'])['linear_parent_pending']
        self.assertFalse(self.tasks.acknowledge_parent(self.b['id'],{'parent_id':self.a['id'],'revision':'old'}))
        self.assertEqual(self.tasks.get(self.b['id'])['linear_parent_pending'],newer)

    def test_retry_of_older_report_does_not_publish_new_parent_before_next_cutoff(self):
        self.tasks.merge(self.b['id'],self.a['id'])
        self.tasks.bind(self.b['id'],'linear','https://linear.app/test/issue/APP-2/b')
        writer=Mock();writer.issue.return_value={'id':'issue-2','identifier':'APP-2','title':'B','url':'https://linear.app/test/issue/APP-2/b','state_type':'unstarted','state_name':'Todo','viewer_id':'me','team_id':'team','parent_issue_id':''}
        engine=DailyEngine(self.runtime.daily_service.store,self.tasks,lambda:writer,self.runtime.station)
        engine.sync_cutoff=datetime.now(timezone.utc)-timedelta(days=1)
        engine.synchronize(self.b['id'],{},threading.Event())
        writer.ensure_parent.assert_not_called();self.assertIn('linear_parent_pending',self.tasks.get(self.b['id']))

    def test_linear_lost_parent_response_reconciles_without_mutation(self):
        writer=LinearWriter('fixture')
        with patch.object(writer,'issue',return_value={'parent_issue_id':'parent','id':'issue'}), patch.object(writer,'request') as mutate:
            result=writer.ensure_parent({'payload':{'issue_id':'issue','parent_id':'parent'}})
            self.assertEqual(result['id'],'issue'); mutate.assert_not_called()


class MergeUiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'tasks.json'
        self.runtime=MomoRuntime(self.path,auto_tick=False).start(); self.tasks=self.runtime.tasks; self.tasks.clear_samples()
        self.a=self.tasks.add(make_task('A','demo')); self.b=self.tasks.add(make_task('B','demo'))
        self.tasks.set_status(self.a['id'],'waiting')
        self.window=Momo(self.runtime); self.window.navigate('all'); self.window.show(); APP.processEvents()

    def tearDown(self):
        self.window.exiting=True;self.window.close();self.window.deleteLater();APP.processEvents();self.runtime.close();self.temp.cleanup()

    def drop(self,area,source,target,mode):
        pos=target.geometry().center(); mime=QMimeData(); mime.setData(TASK_MIME,source.task_id.encode())
        enter=DragEnter(source,pos,mime);APP.sendEvent(area,enter);self.assertTrue(enter.isAccepted())
        move=DragMove(source,pos,mime);APP.sendEvent(area,move);self.assertTrue(area.hint.isVisible())
        def decide():
            dialog=APP.activeModalWidget()
            if mode: dialog.findChild(QPushButton,'merge_'+mode).click()
            else:dialog.reject()
        QTimer.singleShot(10,decide)
        self.window.task_drag_active=True
        event=Drop(source,pos,mime);APP.sendEvent(area,event)
        self.assertTrue(area.guide.isHidden())
        self.window.task_drag_active=False;self.window.finish_task_drag();APP.processEvents()
        return event.isAccepted()

    def test_cancel_is_read_only_and_child_confirmation_preserves_status(self):
        area=self.window.list_host; cards={c.task_id:c for c in area.cards()}; before=self.path.read_bytes()
        self.assertFalse(self.drop(area,cards[self.b['id']],cards[self.a['id']],None));self.assertEqual(self.path.read_bytes(),before)
        cards={c.task_id:c for c in area.cards()}
        self.assertTrue(self.drop(area,cards[self.b['id']],cards[self.a['id']],'child'))
        self.assertEqual(self.tasks.get(self.b['id'])['parent_id'],self.a['id'])
        self.assertEqual(self.tasks.get(self.b['id'])['status'],'todo')

    def test_cross_board_center_creates_follow_dependency_without_changing_status(self):
        self.window.change_mode('board');APP.processEvents()
        columns={c.status:c for c in self.window.list_host.findChildren(TaskDropArea)}
        self.assertTrue(self.drop(columns['waiting'],columns['todo'].cards()[0],columns['waiting'].cards()[0],'after'))
        self.assertEqual(self.tasks.get(self.b['id'])['depends_on'],[self.a['id']]);self.assertEqual(self.tasks.get(self.b['id'])['status'],'todo')
        self.window.select_task(self.b['id'])
        self.assertEqual({t['id'] for t in self.window.task_route(self.tasks.get(self.b['id']))},{self.a['id'],self.b['id']})
        self.assertIsNotNone(self.window.findChild(__import__('plan_ui').TaskDag,'rootRouteDag'))
