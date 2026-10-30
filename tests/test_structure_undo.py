"""Undo uses structural compare-and-swap, preserving all later task content."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from model import JsonTaskRepository,make_task
from task_structure import impact,projection


class UndoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'tasks.json'
        self.repo=JsonTaskRepository(self.path); self.repo.clear_samples()
        self.a=make_task('父任务','work'); self.b=make_task('待移动','work')
        self.repo.add(self.a); self.repo.add(self.b)

    def tearDown(self): self.temp.cleanup()

    def undo(self): self.repo.undo_structure(self.repo.undo_state()['id'])

    def test_merge_undo_keeps_new_notes_status_links_and_new_sync_revision(self):
        self.repo.merge(self.b['id'],self.a['id']); pending=copy.deepcopy(self.repo.get(self.b['id'])['linear_parent_pending'])
        self.repo.update(self.b['id'],note='合入后写的笔记',status='doing')
        self.repo.bind(self.b['id'],'codex','codex://threads/new-chat')
        self.undo(); result=self.repo.get(self.b['id'])
        self.assertIsNone(result['parent_id']); self.assertEqual(result['note'],'合入后写的笔记')
        self.assertEqual(result['status'],'doing'); self.assertEqual(result['bindings'][0]['url'],'codex://threads/new-chat')
        self.assertNotEqual(result['linear_parent_pending']['revision'],pending['revision'])
        self.assertIsNone(result['linear_parent_pending']['parent_id']); self.assertFalse(self.repo.undo_state()['available'])

    def test_undo_survives_restart_and_does_not_restore_acknowledged_remote_operation(self):
        self.repo.merge(self.b['id'],self.a['id']); pending=copy.deepcopy(self.repo.get(self.b['id'])['linear_parent_pending'])
        self.repo.acknowledge_parent(self.b['id'],pending)
        self.repo=JsonTaskRepository(self.path); self.assertTrue(self.repo.undo_state()['available'])
        self.undo(); self.assertIsNone(self.repo.get(self.b['id'])['parent_id'])
        self.assertNotEqual(self.repo.get(self.b['id'])['linear_parent_pending']['revision'],pending['revision'])

    def test_detach_undo_preserves_subtree_and_inherited_target_is_restored(self):
        self.repo.merge(self.b['id'],self.a['id']); self.repo.add_children(self.b['id'],['内部步骤'])
        self.repo.update(self.a['id'],linear_target={'team_id':'team'})
        before=projection(self.repo.tasks); self.repo.detach(self.b['id']); self.undo()
        self.assertEqual(projection(self.repo.tasks),before)
        self.assertEqual(self.repo.get(self.a['id'])['steps'][0]['id'],self.b['id'])

    def test_dependencies_order_and_latest_only(self):
        self.repo.change_dependencies(self.b['id'],[self.a['id']]); self.undo()
        self.assertEqual(self.repo.get(self.b['id'])['depends_on'],[])
        before=projection(self.repo.tasks); self.repo.reorder(list(reversed([t['id'] for t in self.repo.tasks])))
        self.undo(); self.assertEqual(projection(self.repo.tasks),before)

    def test_new_task_or_stale_token_blocks_without_touching_disk(self):
        self.repo.merge(self.b['id'],self.a['id']); token=self.repo.undo_state()['id']; data=self.path.read_bytes()
        with self.assertRaises(ValueError): self.repo.undo_structure('old-token')
        self.assertEqual(self.path.read_bytes(),data)
        self.repo.add(make_task('后来新建','work')); data=self.path.read_bytes()
        self.assertFalse(self.repo.undo_state()['available'])
        with self.assertRaises(ValueError): self.repo.undo_structure(token)
        self.assertEqual(self.path.read_bytes(),data)

    def test_save_failure_rolls_back_both_history_and_tasks(self):
        self.repo.merge(self.b['id'],self.a['id']); data=self.path.read_bytes(); before=copy.deepcopy(self.repo.tasks)
        history=copy.deepcopy(self.repo.structure_undo)
        with patch.object(self.repo,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.undo()
        self.assertEqual(self.repo.tasks,before); self.assertEqual(self.repo.structure_undo,history)
        self.assertEqual(self.path.read_bytes(),data)
        with patch.object(self.repo,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.repo.detach(self.b['id'])
        self.assertEqual(self.repo.tasks,before); self.assertEqual(self.repo.structure_undo,history)

    def test_preview_counts_entire_subtree_without_mutation(self):
        self.repo.add_children(self.b['id'],['内部步骤']); before=self.path.read_bytes()
        result=impact(self.repo.tasks,self.b['id'],self.a['id'],'child')
        self.assertEqual(result['subtree_count'],2); self.assertTrue(result['linear_sync'])
        self.assertEqual(result['to_title'],'父任务'); self.assertEqual(self.path.read_bytes(),before)


if __name__=='__main__':unittest.main()
