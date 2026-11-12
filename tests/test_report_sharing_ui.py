import csv
from pathlib import Path
import unittest
from unittest.mock import patch
from test_ui import APP
from main import Momo
from collaboration_ui import CollaborationDialog
from report_sharing_ui import open_report_sharing,counts
from mesh_fixture import MeshFixture

class ReportSharingUITests(MeshFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();self.window=Momo(self.runtime);self.window.show();open_report_sharing(self.window)
        self.dialog=self.window.collaboration_dialog;self.panel=self.dialog.report_panel;APP.processEvents()
    def tearDown(self):
        self.dialog.close();self.dialog.deleteLater();self.window.exiting=True;self.window.close();self.window.deleteLater();APP.processEvents();super().tearDown()
    def test_native_entry_and_small_window_controls_work_without_a_project(self):
        self.assertIs(self.dialog.tabs.currentWidget(),self.panel);self.assertTrue(self.panel.preview.isReadOnly())
        self.assertFalse(self.panel.enabled.isChecked());self.assertFalse(self.panel.collect.isChecked())
        self.dialog.resize(760,600);APP.processEvents();self.assertEqual(self.dialog.width(),760)
        self.assertGreater(self.panel.preview.height(),45);self.assertEqual(self.state(self.a)['projects'],{})
    def test_selecting_administrator_requires_confirmation_and_refresh_keeps_edits(self):
        self.pair_devices();self.dialog.refresh();ui=self.panel
        ui.enabled.setChecked(True);ui.admin.setCurrentIndex(ui.admin.findData(self.b.identity.id));self.dialog.refresh()
        self.assertTrue(ui.enabled.isChecked());self.assertEqual(ui.admin.currentData(),self.b.identity.id)
        with patch.object(self.dialog,'confirm',return_value=False):ui.configure()
        self.assertFalse(self.state(self.a)['report_sharing']['enabled'])
        with patch.object(self.dialog,'confirm',return_value=True):ui.configure()
        self.assertTrue(self.state(self.a)['report_sharing']['enabled']);self.assertEqual(self.state(self.a)['report_sharing']['admin_id'],self.b.identity.id)
    def test_admin_view_and_csv_index_use_per_device_files_and_escape_names(self):
        root=self.root/'collected';(root/'周报').mkdir(parents=True);(root/'周报/test.md').write_text('# 合成周报内容')
        peer=self.b.identity.id
        with self.a.lock:
            self.a.state['peers'][peer]=dict(id=peer,name='=HYPERLINK("bad")',fingerprint=self.b.identity.pin)
            self.a.state['report_inbox'][peer]=dict(root=str(root),version='test',files=[dict(path='周报/test.md',bytes=20,sha256='0'*64)],collected_at='2026-11-11')
            self.a.revision+=1
        self.dialog.refresh();ui=self.panel
        self.assertEqual(counts(self.state(self.a)['report_inbox'][peer]['files']),(0,1));self.assertIn('合成周报内容',ui.preview.toPlainText())
        dest=self.root/'index.csv'
        with patch('report_sharing_ui.QFileDialog.getSaveFileName',return_value=(str(dest),'CSV')):ui.export_index()
        with dest.open(encoding='utf-8-sig') as stream:rows=list(csv.reader(stream))
        self.assertEqual(len(rows),2);self.assertTrue(rows[1][0].startswith("'="));self.assertEqual(rows[1][2],'周报')

if __name__=='__main__':unittest.main()
