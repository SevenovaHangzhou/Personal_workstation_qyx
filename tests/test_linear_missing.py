"""Incremental Linear imports only create missing tasks; remote calls are faked."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from clients import call
from model import make_task, parse_binding
from msgs.YomkMsgs import ImportIssues
from runtime import MomoRuntime
from test_integrations import issue, page


class MissingIssueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'tasks.json'
        self.runtime=MomoRuntime(self.path,profile='work',auto_tick=False).start()
        self.tasks=self.runtime.tasks;self.tasks.clear_samples();self.linear=self.runtime.linear
        self.linear.configure('fixture-key')

    def tearDown(self):
        self.runtime.close();self.temp.cleanup()

    def import_rows(self,rows,only_new=False):
        return call('/TaskService/import_issues',ImportIssues(rows,only_new))

    def pull(self,rows):
        with patch('linear_support.graphql',return_value=page(rows)):
            return self.linear.pull()

    def test_pull_excludes_done_manual_child_and_unlinked_matches(self):
        done=self.import_rows([issue(1)])['task_ids'][0];self.tasks.set_status(done,'done')
        parent=self.tasks.add(make_task('本地父任务','work'))
        child=self.tasks.add_children(parent['id'],['手动关联的子步骤'])[0]
        self.tasks.bind(child['id'],'linear',issue(2)['url'].replace('/task','/old-title'))
        removed=self.import_rows([issue(3)])['task_ids'][0];self.tasks.remove_binding(removed,0)
        before=self.path.read_bytes()
        result=self.pull([issue(1),issue(2),issue(3),issue(4)])
        self.assertEqual([row['id'] for row in result['issues']],['issue-4'])
        self.assertEqual((result['total'],result['existing']),(4,3))
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.linear.import_issues(['issue-4'])['created'],1)
        self.assertEqual(self.pull([issue(1),issue(2),issue(3),issue(4)])['issues'],[])

    def test_same_identifier_in_other_workspace_and_demo_are_not_matches(self):
        task=make_task('另一个工作区的编号','work')
        task['bindings']=[parse_binding('linear',issue(1)['url'].replace('/momo/','/elsewhere/'))]
        self.tasks.add(task)
        demo=make_task('示意关联','work');demo['bindings']=[{'kind':'linear','url':'','label':'MOM-2','demo':True}]
        self.tasks.add(demo)
        self.assertEqual([row['id'] for row in self.pull([issue(1),issue(2)])['issues']],['issue-1','issue-2'])

    def test_import_rechecks_latest_tasks_without_overwriting_local_work(self):
        self.pull([issue()])
        task_id=self.import_rows([issue()])['task_ids'][0]
        self.tasks.update(task_id,title='保留我的标题',note='保留笔记')
        self.tasks.add_children(task_id,['保留子步骤']);self.tasks.set_status(task_id,'waiting')
        before=self.path.read_bytes();revision=self.tasks.revision()
        result=self.linear.import_issues(['issue-1'])
        self.assertEqual(result,{'created':0,'updated':0,'skipped':1,'task_ids':[]})
        self.assertEqual(self.path.read_bytes(),before);self.assertEqual(self.tasks.revision(),revision)

    def test_duplicate_link_with_different_id_cannot_create_two_tasks(self):
        duplicate=copy.deepcopy(issue());duplicate['id']='alias-id'
        result=self.import_rows([issue(),duplicate],only_new=True)
        self.assertEqual((result['created'],result['skipped']),(1,1))
        self.assertEqual(len(self.tasks.tasks),1)
        with self.assertRaises(ValueError):self.import_rows([issue()],only_new=1)


if __name__=='__main__':unittest.main()
