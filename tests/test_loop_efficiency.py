import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_ui import APP
from PyQt5.QtWidgets import QPushButton
from PyQt5.QtTest import QTest
from model import make_task,parse_binding,JsonTaskRepository
from task_outline import TaskOutline
from daily_pending import pending_work
from plan_ui import TaskDag
from main import Momo
from runtime import MomoRuntime


class EfficiencyTests(unittest.TestCase):
    def test_deep_tree_projection_has_no_python_recursion_limit(self):
        tasks=[]
        for i in range(1200):
            task=make_task(str(i),'demo'); task['id']=str(i);task['parent_id']=str(i-1) if i else None; tasks.append(task)
        rows=TaskOutline(tasks).rows(tasks,expanded=[t['id'] for t in tasks])
        self.assertEqual(len(rows),1200);self.assertEqual(rows[-1].depth,1199)

    def test_unchanged_dag_keeps_nodes_zoom_and_selection(self):
        tasks=[make_task('方向一','demo'),make_task('方向二','demo')];graph=TaskDag()
        try:
            graph.set_tasks(tasks); graph.choose(tasks[0]['id']);graph.view.zoom(.5)
            node=graph.nodes[tasks[0]['id']]; scale=graph.view.transform().m11()
            graph.set_tasks(tasks)
            self.assertIs(graph.nodes[tasks[0]['id']],node);self.assertTrue(node.chosen)
            self.assertEqual(graph.view.transform().m11(),scale)
            tasks[0]['title']='新标题';graph.set_tasks(tasks)
            self.assertEqual(graph.nodes[tasks[0]['id']].task['title'],'新标题')
        finally:graph.deleteLater();APP.processEvents()

    def test_pending_sync_excludes_learning_and_examples_and_describes_parent_change(self):
        work=make_task('工作','work');learn=make_task('学习','learn');sample=make_task('示例','work');sample['is_sample']=True
        child=make_task('子步骤','work');child['parent_id']=work['id']
        rows=pending_work([work,learn,sample,child],dict(target={},next_day='2026-10-30',enabled=False))
        self.assertEqual({row['task_id'] for row in rows},{work['id'],child['id']})
        self.assertIn('尚未设置',rows[0]['reason']);self.assertEqual(rows[1]['parent'],'工作')
        work['bindings']=[parse_binding('linear','https://linear.app/test/issue/APP-1/task')]
        self.assertEqual(len(pending_work([work],dict(target={},next_day='2026-10-30',enabled=True))),0)
        work['linear_parent_pending']={'parent_id':None,'due_at':'2026-10-30T22:00:00+08:00'}
        self.assertEqual(pending_work([work],dict(target={},next_day='2026-10-30',enabled=True))[0]['action'],'调整 issue 父级')

    def test_large_list_loads_in_batches_and_searches_unloaded_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            tasks=[make_task(f'方向 {i:03d}','demo') for i in range(185)]
            path=Path(temp)/'tasks.json'
            repository=JsonTaskRepository(path);repository.clear_samples();repository.replace_tasks(tasks)
            runtime=MomoRuntime(path,auto_tick=False).start()
            window=Momo(runtime);window.navigate('all');window.show();QTest.qWait(20)
            try:
                self.assertEqual(len(window.list_host.cards()),80)
                window.findChild(QPushButton,'showMoreTasks').click();QTest.qWait(20)
                self.assertEqual(len(window.list_host.cards()),160)
                window.search.setText('方向 184');QTest.qWait(20)
                self.assertEqual([card.task_id for card in window.list_host.cards()],[tasks[-1]['id']])
                window.search.clear();window.select_task(tasks[-1]['id']);QTest.qWait(20)
                self.assertIn(tasks[-1]['id'],[card.task_id for card in window.list_host.cards()])
            finally:
                window.exiting=True;window.close();window.deleteLater();APP.processEvents();runtime.close()


if __name__=='__main__':unittest.main()
