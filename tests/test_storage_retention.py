import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from storage_retention import Retention
from runtime import MomoRuntime
from test_ui import APP
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QMessageBox
from main import Momo
from storage_ui import StorageDialog


def old_file(root,name,text='history',days=40):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    old=time.time()-days*86400;os.utime(path,(old,old))
    directory=path.parent
    while directory!=root:
        os.utime(directory,(old,old));directory=directory.parent
    return path


class RetentionTests(unittest.TestCase):
    def test_preview_never_changes_files_skips_links_and_recent_members(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old_file(root,'daily/git/old/a.patch');old_file(root,'daily/git/recent/old.patch')
            old_file(root,'daily/git/recent/new.patch',days=1)
            (root/'link').symlink_to(root/'daily/git/old',target_is_directory=True)
            manager=Retention(root);plan=manager.preview(['daily/git/old','daily/git/recent','link','../other'])
            self.assertEqual([row['path'] for row in plan['rows']],['daily/git/old'])
            self.assertTrue((root/'daily/git/old/a.patch').exists())

    def test_trash_uses_reviewed_one_time_plan_and_preserves_core_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'data';root.mkdir();trash=Path(temp)/'trash';trash.mkdir()
            source=old_file(root,'daily/git/old/a.patch');old_file(root,'tasks.json','core')
            manager=Retention(root);plan=manager.preview(['daily/git/old'])
            def move(command,**kwargs):
                self.assertEqual(command[:3],['gio','trash','--'])
                path=Path(command[3]);path.rename(trash/path.name);return SimpleNamespace(returncode=0)
            with patch('storage_retention.shutil.which',return_value='/usr/bin/gio'),patch('storage_retention.subprocess.run',side_effect=move):
                result=manager.apply(plan['id'])
                with self.assertRaises(ValueError):manager.apply(plan['id'])
            self.assertEqual(result['trashed'],['daily/git/old']);self.assertFalse(source.exists())
            self.assertEqual((trash/'0/a.patch').read_text(),'history');self.assertEqual((root/'tasks.json').read_text(),'core')

    def test_changed_plan_is_rejected_before_any_move_and_trash_failure_restores(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=old_file(root,'daily/git/old/a.patch');manager=Retention(root)
            plan=manager.preview(['daily/git/old']);source.write_text('new work')
            with patch('storage_retention.shutil.which',return_value='gio'),patch('storage_retention.subprocess.run') as command:
                with self.assertRaises(ValueError):manager.apply(plan['id'])
                command.assert_not_called()
            source=old_file(root,'daily/git/old/a.patch');plan=manager.preview(['daily/git/old'])
            with patch('storage_retention.shutil.which',return_value='gio'),patch('storage_retention.subprocess.run',return_value=SimpleNamespace(returncode=1)):
                result=manager.apply(plan['id'])
            self.assertTrue(result['errors']);self.assertEqual(source.read_text(),'history')

    def test_service_only_previews_terminal_artifacts_and_done_reports_and_ui_cancel_never_applies(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);runtime=MomoRuntime(root/'tasks.json',auto_tick=False).start();window=Momo(runtime)
            try:
                learning=runtime.learning_service
                learning.state['jobs'].update({'old':{'id':'old','status':'failed'},'live':{'id':'live','status':'running'}})
                old_file(root,'learning/generations/old/events.jsonl');old_file(root,'learning/generations/live/events.jsonl')
                runtime.daily_service.store.save_report('2020-01-01','done',{'markdown':'completed'},[])
                runtime.daily_service.store.save_report('2020-01-02','pending',{'markdown':'pending'},[])
                old_file(root,'daily/2020-01-01.md');old_file(root,'daily/2020-01-02.md')
                plan=runtime.daily.retention_preview(threading.Event())
                self.assertEqual({row['path'] for row in plan['rows']},{'learning/generations/old','daily/2020-01-01.md'})
                dialog=StorageDialog(window)
                while dialog.worker:QTest.qWait(10)
                with patch('storage_ui.QMessageBox.exec_',return_value=QMessageBox.No),patch.object(runtime.daily,'retention_apply') as apply:
                    dialog.preview_retention()
                    while dialog.worker:QTest.qWait(10)
                    apply.assert_not_called()
                self.assertTrue((root/'learning/generations/old/events.jsonl').exists())
                (root/'daily/2020-01-01.md').unlink()
                self.assertEqual(runtime.daily.report_text('2020-01-01'),'completed')
                with patch('storage_ui.QMessageBox.exec_',return_value=QMessageBox.Yes),patch.object(runtime.daily,'retention_apply',return_value={'trashed':[],'errors':[],'cancelled':False}) as apply:
                    dialog.preview_retention()
                    for _ in range(100):
                        QTest.qWait(10)
                        if dialog.worker is None:break
                    apply.assert_called_once()
                dialog.close();dialog.deleteLater();APP.processEvents()
            finally:
                learning.state['jobs'].pop('live',None);window.exiting=True;window.close();window.deleteLater();APP.processEvents();runtime.close()


if __name__=='__main__':unittest.main()
