"""Dependency guidance edits only user-selected links and keeps movement explicit."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_ui import APP
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QPushButton, QCheckBox, QLabel
from PyQt5.QtTest import QTest
from main import Momo
from model import make_task
from runtime import MomoRuntime
from task_dependencies_ui import related_tasks, DependencyPanel, DependencyDialog
from task_move_ui import TaskMoveDialog


class DependencyUiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'tasks.json'
        self.runtime=MomoRuntime(self.path,auto_tick=False).start(); self.tasks=self.runtime.tasks; self.tasks.clear_samples()
        self.root=self.tasks.add(make_task('项目方向','demo'))
        self.a,self.b,self.c=self.tasks.add_children(self.root['id'],['通信准备','时序验证','实验记录'])
        self.other=self.tasks.add(make_task('其他方向','demo'))
        self.window=Momo(self.runtime); self.window.navigate('demo'); self.window.show(); APP.processEvents()

    def tearDown(self):
        self.window.exiting=True; self.window.close(); self.window.deleteLater(); APP.processEvents()
        self.runtime.close(); self.temp.cleanup()

    def select(self,dialog,task_id):
        for i in range(dialog.rows.count()):
            if dialog.rows.item(i).data(Qt.UserRole)==task_id: dialog.rows.setCurrentRow(i); return
        self.fail('missing related task')

    def save_checks(self, wanted):
        dialog=APP.activeModalWidget()
        for check in dialog.findChildren(QCheckBox): check.setChecked(check.text() in wanted)
        next(b for b in dialog.findChildren(QPushButton) if b.text()=='保存前置步骤').click()

    def test_projection_distinguishes_incoming_outgoing_and_excludes_containment(self):
        self.tasks.dependencies(self.b['id'],[self.a['id']]); self.tasks.dependencies(self.c['id'],[self.b['id']])
        before,after=related_tasks(self.tasks.tasks,self.b['id'])
        self.assertEqual([t['id'] for t in before],[self.a['id']]); self.assertEqual([t['id'] for t in after],[self.c['id']])
        self.assertEqual(related_tasks(self.tasks.tasks,self.root['id']),([],[]))
        self.assertEqual(related_tasks(self.tasks.tasks,'deleted'),([],[]))

    def test_detail_displays_both_sides_and_opens_successor(self):
        self.tasks.dependencies(self.b['id'],[self.a['id']]); self.tasks.dependencies(self.c['id'],[self.b['id']])
        self.window.select_task(self.b['id']); QTest.qWait(10)
        panel=self.window.findChild(DependencyPanel,'dependencyPanel')
        texts=[w.text() for w in panel.findChildren(QLabel)]
        self.assertIn('先完成 · 1',texts); self.assertIn('随后可进行 · 1',texts)
        next(b for b in panel.findChildren(QPushButton) if b.accessibleName()=='打开依赖任务：实验记录').click()
        self.assertEqual(self.window.selected,self.c['id'])

    def test_manager_can_create_first_dependency_and_cancel_is_readonly(self):
        dialog=DependencyDialog(self.window,self.window,self.b['id']); before=self.path.read_bytes()
        try:
            self.assertEqual(dialog.rows.count(),1)
            dialog.reject(); self.assertEqual(self.path.read_bytes(),before)
            QTimer.singleShot(10,lambda:self.save_checks({'通信准备'})); dialog.edit()
            self.assertEqual(self.tasks.get(self.b['id'])['depends_on'],[self.a['id']])
            self.assertIn('前置 1 项',dialog.summary.text())
        finally: dialog.deleteLater(); APP.processEvents()

    def test_edit_successor_removes_only_chosen_dependency_without_moving_tasks(self):
        self.tasks.dependencies(self.c['id'],[self.a['id'],self.b['id']])
        dialog=DependencyDialog(self.window,self.window,self.a['id'])
        try:
            self.select(dialog,self.c['id'])
            QTimer.singleShot(10,lambda:self.save_checks({'时序验证'})); dialog.edit()
            self.assertEqual(self.tasks.get(self.c['id'])['depends_on'],[self.b['id']])
            self.assertEqual(self.tasks.get(self.a['id'])['parent_id'],self.root['id'])
            self.assertEqual(dialog.rows.count(),1)
        finally: dialog.deleteLater(); APP.processEvents()

    def test_move_picker_rechecks_after_managing_dependencies_without_automatic_move(self):
        self.tasks.dependencies(self.b['id'],[self.a['id']]); dialog=TaskMoveDialog(self.window,self.a['id'])
        try:
            for i in range(dialog.targets.count()):
                if dialog.targets.item(i).data(Qt.UserRole)==self.other['id']:dialog.targets.setCurrentRow(i)
            self.assertFalse(dialog.child_button.isEnabled())
            def adjust(): self.tasks.dependencies(self.b['id'],[])
            with patch.object(self.window,'manage_dependencies',side_effect=lambda *args:adjust()): dialog.manage_dependencies()
            self.assertTrue(dialog.child_button.isEnabled()); self.assertIsNone(dialog.mode)
            self.assertEqual(self.tasks.get(self.a['id'])['parent_id'],self.root['id'])
        finally: dialog.deleteLater(); APP.processEvents()

    def test_cycle_is_rejected_and_cancel_preserves_existing_dependencies(self):
        self.tasks.dependencies(self.b['id'],[self.a['id']]); before=self.path.read_bytes(); errors=[]
        def create_cycle():
            dialog=APP.activeModalWidget()
            next(cb for cb in dialog.findChildren(QCheckBox) if cb.text()=='时序验证').setChecked(True)
            next(b for b in dialog.findChildren(QPushButton) if b.text()=='保存前置步骤').click()
            errors.extend(w.text() for w in dialog.findChildren(QLabel) if '环' in w.text())
            dialog.reject()
        QTimer.singleShot(10,create_cycle); self.window.edit_dependencies(self.a['id'])
        self.assertTrue(errors); self.assertEqual(self.path.read_bytes(),before)

    def test_drag_merge_dialog_reenables_action_after_explicit_dependency_edit(self):
        self.tasks.dependencies(self.b['id'],[self.a['id']])
        states=[]
        def choose():
            dialog=APP.activeModalWidget(); action=dialog.findChild(QPushButton,'merge_child')
            states.append(action.isEnabled())
            next(b for b in dialog.findChildren(QPushButton) if b.text()=='管理相关依赖').click()
            states.append(action.isEnabled()); action.click()
        with patch.object(self.window,'manage_dependencies',side_effect=lambda *args:self.tasks.dependencies(self.b['id'],[])):
            QTimer.singleShot(10,choose)
            self.assertTrue(self.window.merge_task(self.a['id'],self.other['id']))
        self.assertEqual(states,[False,True]); self.assertEqual(self.tasks.get(self.a['id'])['parent_id'],self.other['id'])

    def test_detach_conflict_offers_manager_without_detaching_or_removing_links(self):
        self.tasks.dependencies(self.b['id'],[self.a['id']]); before=self.path.read_bytes()
        def manage():
            message=APP.activeModalWidget()
            next(b for b in message.buttons() if b.text()=='管理相关依赖').click()
        with patch.object(self.window,'manage_dependencies') as manager:
            QTimer.singleShot(10,manage)
            self.assertFalse(self.window.detach_task(self.a['id']))
        manager.assert_called_once_with(self.a['id']); self.assertEqual(self.path.read_bytes(),before)


if __name__=='__main__':unittest.main()
