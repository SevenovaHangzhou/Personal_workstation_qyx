"""Dragging a child out or to another parent preserves task identity and data."""
import copy
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from test_ui import APP
from PyQt5.QtCore import QPoint, QMimeData, QTimer
from PyQt5.QtWidgets import QMessageBox, QPushButton
from main import Momo
from model import JsonTaskRepository, make_task
from runtime import MomoRuntime
from daily_engine import DailyEngine
from clients import call
from test_task_order import DragEnter, Drop, TASK_MIME
from task_order_ui import TaskDropArea


class DetachTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'tasks.json'
        self.runtime=MomoRuntime(self.path,auto_tick=False).start(); self.tasks=self.runtime.tasks
        self.tasks.clear_samples(); self.window=None
        self.parent=self.tasks.add(make_task('父任务','work'))
        self.other=self.tasks.add(make_task('另一个父任务','work'))
        self.child,self.sibling=self.tasks.add_children(self.parent['id'],['子步骤','同级步骤'])
        self.nested=self.tasks.add_children(self.child['id'],['内部步骤'])[0]

    def tearDown(self):
        if self.window:
            self.window.exiting=True; self.window.close(); self.window.deleteLater(); APP.processEvents()
        self.runtime.close(); self.temp.cleanup()

    def open(self,board=False):
        self.window=Momo(self.runtime); self.window.navigate('all')
        self.window.expanded_tasks.add(self.parent['id']); self.window.refresh_list()
        if board: self.window.change_mode('board')
        self.window.show(); APP.processEvents()
        return self.window

    def source(self):
        return next(card for card in self.window.list_host.findChildren(__import__('main').DraggableTaskCard)
                    if card.task_id==self.child['id'])

    def drop(self,area,source,pos):
        mime=QMimeData(); mime.setData(TASK_MIME,source.task_id.encode())
        enter=DragEnter(source,pos,mime); APP.sendEvent(area,enter)
        if not enter.isAccepted(): return False
        event=Drop(source,pos,mime); APP.sendEvent(area,event); APP.processEvents()
        return event.isAccepted()

    def test_detach_keeps_identity_subtree_bindings_content_status_and_effective_destination(self):
        self.tasks.update(self.parent['id'],linear_target={'team_id':'team','project_id':'project'})
        self.tasks.update(self.child['id'],note='学习和工作笔记')
        self.tasks.set_status(self.child['id'],'doing')
        self.tasks.bind(self.child['id'],'codex','codex://threads/existing-child')
        before=self.tasks.get(self.child['id']); self.tasks.detach(self.child['id'])
        after=self.tasks.get(self.child['id'])
        for key in before:
            if key not in ('parent_id','linear_target'): self.assertEqual(after[key],before[key])
        self.assertIsNone(after['parent_id']); self.assertEqual(after['linear_target'],{'team_id':'team','project_id':'project'})
        self.assertIsNone(after['linear_parent_pending']['parent_id'])
        self.assertEqual(self.tasks.get(self.nested['id'])['parent_id'],self.child['id'])
        self.assertEqual([s['id'] for s in self.tasks.get(self.parent['id'])['steps']],[self.sibling['id']])
        self.assertEqual(JsonTaskRepository(self.path).tasks,self.tasks.tasks)

    def test_invalid_requests_and_failed_save_preserve_revision_disk_and_memory(self):
        before=self.tasks.tasks; revision=self.tasks.revision(); data=self.path.read_bytes()
        for tid in (self.parent['id'],'deleted'):
            with self.assertRaises(ValueError): self.tasks.detach(tid)
        with self.assertRaises(ValueError): call('/TaskService/detach',{'task_id':self.child['id']})
        with patch.object(JsonTaskRepository,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.tasks.detach(self.child['id'])
        self.assertEqual(self.tasks.tasks,before); self.assertEqual(self.tasks.revision(),revision)
        self.assertEqual(self.path.read_bytes(),data)

    def test_dependencies_are_never_silently_deleted(self):
        self.tasks.dependencies(self.child['id'],[self.sibling['id']])
        with self.assertRaisesRegex(ValueError,'先调整依赖'): self.tasks.detach(self.child['id'])
        self.tasks.dependencies(self.child['id'],[]); self.tasks.dependencies(self.sibling['id'],[self.child['id']])
        with self.assertRaisesRegex(ValueError,'先调整依赖'): self.tasks.detach(self.child['id'])

    def test_detach_nonwork_retains_learning_binding_and_has_no_linear_sync(self):
        parent=self.tasks.add(make_task('学习课程','learn')); child=self.tasks.add_children(parent['id'],['学习步骤'])[0]
        from learning_content import BUILTIN_COURSE_ID
        self.runtime.learning.bind(child['id'],BUILTIN_COURSE_ID)
        self.tasks.detach(child['id'])
        self.assertEqual(self.runtime.learning.snapshot(child['id'])['course']['id'],BUILTIN_COURSE_ID)
        self.assertNotIn('linear_parent_pending',self.tasks.get(child['id']))

    def test_nightly_clears_remote_parent_without_recreating_issue(self):
        issue=dict(id='issue',identifier='APP-2',title='子步骤',url='https://linear.app/test/issue/APP-2/child',
                   state_type='unstarted',state_name='Todo',viewer_id='me',team_id='team',project_id='',parent_issue_id='old')
        self.tasks.attach_issue(self.child['id'],issue); self.tasks.detach(self.child['id'])
        writer=Mock(); writer.issue.return_value=issue; writer.ensure_parent.return_value=dict(issue,parent_issue_id='')
        engine=DailyEngine(self.runtime.daily_service.store,self.tasks,lambda:writer,self.runtime.station)
        engine.sync_cutoff=datetime.now(timezone.utc)+timedelta(days=1)
        engine.synchronize(self.child['id'],{},threading.Event())
        self.assertEqual(writer.ensure_parent.call_args.args[0]['payload'],{'issue_id':'issue','parent_id':None})
        writer.ensure_issue.assert_not_called()
        self.assertNotIn('linear_parent_pending',self.tasks.get(self.child['id']))

    def test_drag_out_confirm_cancel_and_preserved_status_in_list_and_board(self):
        window=self.open(); before=self.path.read_bytes(); area=window.independent_drop
        self.assertTrue(area.isVisible())
        with patch('main.QMessageBox.question',return_value=QMessageBox.No):
            self.assertFalse(self.drop(area,self.source(),QPoint(15,15)))
        self.assertEqual(self.path.read_bytes(),before)
        window.change_mode('board'); APP.processEvents()
        with patch('main.QMessageBox.question',return_value=QMessageBox.Yes):
            self.assertTrue(self.drop(area,self.source(),QPoint(15,15)))
        self.assertIsNone(self.tasks.get(self.child['id'])['parent_id'])
        self.assertEqual(self.tasks.get(self.child['id'])['status'],'todo')
        self.assertEqual(window.selected,self.child['id'])

    def test_drag_child_directly_into_other_parent_and_reject_descendant_cycle(self):
        window=self.open(); area=window.list_host
        source=self.source(); target=next(c for c in area.cards() if c.task_id==self.other['id'])
        def confirm(): APP.activeModalWidget().findChild(QPushButton,'merge_child').click()
        QTimer.singleShot(10,confirm)
        self.assertTrue(self.drop(area,source,area.card_rect(target).center()))
        self.assertEqual(self.tasks.get(self.child['id'])['parent_id'],self.other['id'])
        self.assertEqual(self.tasks.get(self.nested['id'])['parent_id'],self.child['id'])
        self.assertEqual(self.tasks.get(self.parent['id'])['steps'][0]['id'],self.sibling['id'])
        with self.assertRaisesRegex(ValueError,'后代'): self.tasks.merge(self.child['id'],self.nested['id'])

    def test_root_and_external_drop_rejected_and_dependency_error_explained(self):
        window=self.open(); area=window.independent_drop
        root=next(c for c in window.list_host.cards() if c.task_id==self.parent['id'])
        self.assertFalse(self.drop(area,root,QPoint(15,15)))
        self.tasks.dependencies(self.child['id'],[self.sibling['id']])
        messages=[]
        def close_conflict():
            dialog=APP.activeModalWidget(); messages.append(dialog.text()); dialog.reject()
        QTimer.singleShot(10,close_conflict)
        self.assertFalse(self.drop(area,self.source(),QPoint(15,15)))
        self.assertIn('先调整依赖',messages[0])


if __name__=='__main__': unittest.main()
