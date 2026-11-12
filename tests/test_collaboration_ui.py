import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import threading
from pathlib import Path
from collaboration.knowledge import NOTE_FILE
import unittest
from unittest.mock import patch
from test_ui import APP
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QPushButton,QPlainTextEdit
from main import Momo
from collaboration_ui import CollaborationDialog,TaskChatPicker
from collaboration.protocol import FIELDS
from mesh_fixture import MeshFixture

class CollaborationUITests(MeshFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();self.window=Momo(self.runtime);self.window.show();self.dialog=CollaborationDialog(self.window,self.task['id']);self.dialog.show();APP.processEvents()
    def tearDown(self):
        self.dialog.loaded_note=self.dialog.note_view.toPlainText();self.dialog.loaded_draft=dict(content=self.dialog.draft_content());self.dialog.message_input.clear()
        self.dialog.close();self.dialog.deleteLater();self.window.exiting=True;self.window.close();self.window.deleteLater();APP.processEvents();super().tearDown()
    def pair(self):
        project=self.project();self.dialog.refresh();APP.processEvents();return project
    def test_work_only_native_entry_no_server_configuration_and_network_opt_in(self):
        self.assertTrue(any(b.text()=='同事协作' for b in self.window.findChildren(QPushButton)))
        self.assertFalse(self.runtime.collaboration.snapshot()['config']['enabled'])
        self.assertFalse(hasattr(self.dialog,'url'));self.assertFalse(hasattr(self.dialog,'join_code'))
        picker=TaskChatPicker(self.dialog);picker.show()
        for _ in range(100):
            APP.processEvents();QTest.qWait(10)
            if picker.worker is None:break
        self.assertIsNone(picker.worker);self.assertEqual(picker.rows.count(),2);self.assertFalse(picker.submit.isEnabled())
        picker.rows.setCurrentRow(1);self.assertTrue(picker.submit.isEnabled());self.assertEqual(picker.result_payload()['thread_url'],'codex://threads/peer-chat')
        self.assertNotIn('task_id',picker.result_payload());picker.close();picker.deleteLater()
    def test_independent_knowledge_panels_and_typing_are_not_reset_by_refresh(self):
        project=self.pair();ui=self.dialog;ui.tabs.setCurrentIndex(1);ui.config_host.hide();ui.refresh()
        self.assertEqual(ui.project_tabs.tabText(0),'我的知识库');self.assertEqual(ui.project_tabs.tabText(1),'对方知识库')
        self.assertTrue(ui.peer_note_view.isReadOnly());self.assertFalse(ui.note_view.isReadOnly());self.assertIn('## 背景',ui.peer_note_view.toPlainText())
        cursor=ui.note_view.textCursor();cursor.setPosition(8);ui.note_view.setTextCursor(cursor);self.runtime.collaboration.refresh();ui.refresh();self.assertEqual(ui.note_view.textCursor().position(),8)
        ui.note_view.insertPlainText('未保存的本机编辑');typed=ui.note_view.toPlainText();ui.previous_revision=-1;ui.refresh();self.assertEqual(ui.note_view.toPlainText(),typed)
        self.assertNotIn('未保存的本机编辑',ui.peer_note_view.toPlainText())
        ui.resize(760,600);APP.processEvents();self.assertEqual(ui.size().width(),760);self.assertTrue(ui.share_button.isVisible());self.assertGreater(ui.note_view.height(),80)
    def test_preview_cancellation_never_sends_a_message(self):
        self.pair();ui=self.dialog;ui.message_input.setPlainText('仅编辑，尚未确认')
        with patch.object(ui,'preview',return_value=False):ui.send_message()
        self.assertEqual(self.state(self.b)['entries'],{});self.assertEqual(ui.message_input.toPlainText(),'仅编辑，尚未确认')
        with patch.object(ui,'preview',return_value=True):ui.send_message()
        self.until(lambda:bool(self.state(self.b)['entries']));self.assertEqual(ui.message_input.toPlainText(),'')
    def test_background_summary_can_change_tabs_without_auto_send(self):
        project=self.pair();started=threading.Event();release=threading.Event()
        def generate(*args):started.set();release.wait(2);return dict(content={k:('本机摘要' if k=='summary' else '') for k in FIELDS},source={})
        self.a.generator=generate;self.runtime.collaboration.generate(project,24);self.until(started.is_set)
        self.dialog.tabs.setCurrentIndex(0);self.dialog.project_tabs.setCurrentIndex(2);APP.processEvents();self.assertTrue(self.state(self.a)['busy'])
        release.set();self.until(lambda:not self.state(self.a)['busy']);self.dialog.refresh();self.assertEqual(self.dialog.editors['summary'].toPlainText(),'本机摘要');self.assertEqual(self.state(self.b)['entries'],{})
    def test_close_and_main_quit_respect_unsaved_notes(self):
        self.pair();ui=self.dialog;ui.note_view.setPlainText('尚未保存')
        with patch.object(ui,'confirm',return_value=False):self.assertFalse(ui.close());self.assertTrue(ui.isVisible())
        with patch.object(self.window,'collaboration_dialog',ui,create=True),patch.object(ui,'confirm',return_value=False):
            self.window.exiting=True;self.assertFalse(self.window.close());self.assertTrue(self.window.isVisible())
    def test_knowledge_review_is_local_and_keeps_unsaved_edits(self):
        project=self.pair();self.runtime.collaboration.knowledge_generate(project)
        self.until(lambda:self.state(self.a)['knowledge_jobs'][project]['status']=='ready');ui=self.dialog;ui.refresh()
        ui.note_view.insertPlainText('未保存的手工内容');before=ui.note_view.toPlainText()
        with patch.object(ui,'preview',return_value=True):ui.review_knowledge()
        self.assertEqual(ui.note_view.toPlainText(),before);self.assertEqual(self.state(self.a)['knowledge_jobs'][project]['status'],'ready')
        self.assertEqual(self.state(self.b)['entries'],{})

    def test_local_notes_save_and_copy_context_do_not_send(self):
        project=self.pair();ui=self.dialog;ui.note_view.setPlainText('## 思路\n本机更新');ui.save_note()
        self.assertEqual(ui.loaded_note,'## 思路\n本机更新');self.assertEqual(self.state(self.b)['entries'],{})
        ui.copy_context();text=APP.clipboard().text();self.assertIn('同事知识库（同步副本）',text);self.assertIn('不必凑齐栏目',text);self.assertNotIn('服务器',text)

    def test_crlf_notes_save_repeatedly_and_external_edits_still_conflict(self):
        project=self.pair();ui=self.dialog
        note=Path(self.state(self.a)['workspaces'][project]['root'])/NOTE_FILE
        original='## 思路\r\n来自 Windows 编辑器\r\n'.encode('utf-8')
        note.write_bytes(original);ui.render_project()
        self.assertEqual(ui.loaded_note,'## 思路\n来自 Windows 编辑器\n')
        ui.note_view.setPlainText('## 思路\n本机更新');ui.save_note()
        self.assertEqual(note.read_bytes(),'## 思路\n本机更新'.encode('utf-8'))
        ui.note_view.setPlainText('## 思路\n再次保存');ui.save_note()
        self.assertEqual(note.read_bytes(),'## 思路\n再次保存'.encode('utf-8'))
        ui.note_view.setPlainText('尚未保存的编辑');external='外部改动\r\n'.encode('utf-8')
        note.write_bytes(external);ui.save_note()
        self.assertEqual(note.read_bytes(),external);self.assertIn('已被 Codex',ui.notice.text())
        self.assertEqual(self.state(self.b)['entries'],{})

if __name__=='__main__':unittest.main()
