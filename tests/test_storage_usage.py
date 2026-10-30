import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from test_ui import APP
from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QWidget
from storage_usage import scan_usage
from storage_ui import StorageDialog


class StorageTests(unittest.TestCase):
    def test_metadata_counts_age_and_protects_course_answers_and_databases(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);now=200*86400
            files={'daily/git/old.patch':(30,100),'learning/generations/run/events.jsonl':(20,40),
                   'learning/courses/course.json':(10,100),'learning/reading.json':(8,100),
                   'daily/daily.sqlite3':(5,100),'tasks.json':(9,100)}
            for name,(size,age) in files.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'x'*size);os.utime(path,(now-age*86400,now-age*86400))
            before={name:(root/name).read_bytes() for name in files}
            with patch.object(Path,'open',side_effect=AssertionError('contents must not be read')):
                data=scan_usage(root,now=now)
            by={row['path']:row for row in data['rows']}
            self.assertEqual(sum(row['bytes'] for row in data['rows']),82)
            self.assertEqual(by['daily/git']['older90'],30)
            self.assertEqual(by['learning/generations']['older30'],20)
            self.assertEqual(by['learning/generations']['older90'],0)
            for name in ('learning/courses','learning','daily',''):
                self.assertEqual(by[name]['older30'],0)
            self.assertEqual(before,{name:(root/name).read_bytes() for name in files})

    def test_links_outside_root_and_cancel_do_not_leak_descriptors(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'data';root.mkdir();outside=Path(temp)/'private';outside.mkdir();(outside/'secret').write_bytes(b'x'*50)
            (root/'link').symlink_to(outside,target_is_directory=True);(root/'loop').symlink_to(root,target_is_directory=True)
            before=len(os.listdir('/proc/self/fd'))
            for _ in range(20):
                data=scan_usage(root);self.assertEqual(data['skipped_links'],2)
                self.assertEqual(sum(row['bytes'] for row in data['rows']),0)
                self.assertTrue(scan_usage(root,lambda:True)['cancelled'])
            self.assertEqual(len(os.listdir('/proc/self/fd')),before)
            self.assertEqual(scan_usage(root/'link')['errors'],1)

    def test_worker_is_responsive_and_close_waits_for_cancellation(self):
        with tempfile.TemporaryDirectory() as temp:
            owner=QWidget();owner.runtime=SimpleNamespace(path=Path(temp)/'tasks.json')
            entered=threading.Event();release=threading.Event()
            def slow(root,cancel):
                entered.set();release.wait(2);return scan_usage(root,cancel)
            with patch('storage_ui.scan_usage',side_effect=slow):
                dialog=StorageDialog(owner);dialog.show();self.assertTrue(entered.wait(1))
                events=[];QTimer.singleShot(5,lambda:events.append(True));QTest.qWait(20);self.assertEqual(events,[True])
                self.assertFalse(dialog.close());self.assertTrue(dialog.closing);self.assertTrue(dialog.worker.stop.is_set())
                release.set()
                for _ in range(100):
                    if dialog.worker is None:break
                    QTest.qWait(10)
                self.assertIsNone(dialog.worker);self.assertFalse(dialog.isVisible())
                dialog.deleteLater();owner.deleteLater();APP.processEvents()


if __name__=='__main__':unittest.main()
