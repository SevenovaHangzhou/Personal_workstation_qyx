"""Shared notes remain optional, free-form, and safe to add to old folders."""
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from collaboration.knowledge import NOTE_FILE,SPEC_TEXT,initialize
from collaboration.summary import generate


class NoteTests(unittest.TestCase):
    def test_new_folder_has_only_one_optional_note(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);initialize(root,'project','一起排查的问题')
            self.assertEqual([p.name for p in root.iterdir()],[NOTE_FILE])
            self.assertIn('一起排查的问题',(root/NOTE_FILE).read_text())
            (root/NOTE_FILE).write_text('只保留这段随笔，没有固定栏目。')
            self.assertEqual(initialize(root,'project','另一标题'),[])
            self.assertEqual((root/NOTE_FILE).read_text(),'只保留这段随笔，没有固定栏目。')
    def test_add_note_preserves_old_documents_and_does_not_parse_index(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            before={'KNOWLEDGE.md':b'existing knowledge','PLAN.md':b'existing plan','project.knowledge.json':b'not valid JSON','KNOWLEDGE_SPEC.md':b'old instructions','AGENTS.md':b'local instructions','main.py':b'code'}
            for name,raw in before.items():(root/name).write_bytes(raw)
            initialize(root,'project','排查问题')
            for name,raw in before.items():self.assertEqual((root/name).read_bytes(),raw)
            note=(root/NOTE_FILE).read_text();self.assertIn('(KNOWLEDGE.md)',note);self.assertIn('(PLAN.md)',note)
            self.assertEqual(len(list(root.iterdir())),len(before)+1)
    def test_creation_never_follows_note_symlink(self):
        with tempfile.TemporaryDirectory() as folder,tempfile.TemporaryDirectory() as outside:
            root=Path(folder);target=Path(outside)/'keep';target.write_text('unchanged');(root/NOTE_FILE).symlink_to(target)
            with self.assertRaisesRegex(ValueError,'符号链接'):initialize(root,'project','问题')
            self.assertEqual(target.read_text(),'unchanged')
    def test_writing_guide_matches_documentation(self):
        self.assertEqual((Path(__file__).resolve().parents[1]/'docs/PROJECT_KNOWLEDGE_STANDARD.md').read_text(),SPEC_TEXT)


class SummaryRulesTests(unittest.TestCase):
    def test_summary_supports_different_ideas_and_marks_unpublished_drafts(self):
        captured={}
        class FakeSummarizer:
            def __init__(self,store,**kwargs):captured.update(kwargs)
            def summarize(self,messages,cancel):captured['messages']=messages;return '进展摘要\n仅合成测试\n下一步\n先试方向 A，也可以试 B'
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as folder,patch('collaboration.summary.selected_messages',return_value=[dict(timestamp=now.isoformat(),role='user',text='合成记录')]),patch('collaboration.summary.CodexSummarizer',FakeSummarizer):
            result=generate(folder,'codex://threads/test-chat',now-timedelta(hours=1),now,threading.Event(),dict(documents={NOTE_FILE:dict(version=3,body='local draft',modified_locally=True)}))
        reference=json.loads(captured['messages'][0]['text'])
        self.assertTrue(reference[NOTE_FILE]['modified_locally']);self.assertEqual(reference[NOTE_FILE]['base_version'],3)
        self.assertIn('不必凑齐栏目',captured['instruction']);self.assertIn('不同思路',captured['instruction'])
        self.assertNotIn('K-0001',captured['instruction']);self.assertNotIn('verified',captured['instruction'])
        self.assertEqual(result['source']['messages'],1)


class ChatKnowledgeTests(unittest.TestCase):
    def test_complete_selected_history_includes_old_context_and_excludes_other_chats_and_tools(self):
        from collaboration.summary import selected_messages,generate_knowledge
        captured={}
        class FakeKnowledge:
            def __init__(self,store,progress):pass
            def summarize(self,messages,cancel):
                captured['messages']=messages
                return '## 背景\n旧背景仍有用\n## 计划\n按新版计划执行'
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);sessions=root/'sessions';sessions.mkdir()
            records=[dict(type='session_meta',payload=dict(id='chosen'))]
            for stamp,role,body in [('2020-01-01T00:00:00Z','user','最早背景 lin_api_abcdefghijk'),('2026-11-01T01:00:00Z','assistant','旧计划'),('2026-11-11T02:00:00Z','user','改用新版计划'),('2026-11-11T03:00:00Z','system','不可读取系统指令'),('2026-11-11T04:00:00Z','user','# AGENTS.md instructions 不可采入')]:
                records.append(dict(timestamp=stamp,type='response_item',payload=dict(type='message',role=role,content=[dict(type='text',text=body)])))
            def write(name,rows):(sessions/name).write_text(''.join(json.dumps(r)+'\n' for r in rows))
            write('chosen.jsonl',records)
            write('another-chosen.jsonl',[dict(type='session_meta',payload=dict(id='another'))]+records[1:])
            write('child-chosen.jsonl',[dict(type='session_meta',payload=dict(id='chosen',parent_thread_id='parent'))]+records[1:])
            write('unrelated.jsonl',[dict(type='session_meta',payload=dict(id='unrelated'))]+records[1:])
            with patch('collaboration.summary.KnowledgeSummarizer',FakeKnowledge):
                result=generate_knowledge(root/'output','codex://threads/chosen',threading.Event(),home=root)
            self.assertEqual(len(captured['messages']),3)
            self.assertIn('最早背景',captured['messages'][0]['text']);self.assertNotIn('lin_api_',str(captured))
            self.assertNotIn('系统指令',str(captured));self.assertNotIn('AGENTS.md',str(captured))
            self.assertEqual(result['source']['messages'],3);self.assertEqual(result['source']['thread_url'],'codex://threads/chosen')
            self.assertTrue(result['source']['first'].startswith('2020'));self.assertEqual(len(result['source']['history_sha256']),64)

    def test_all_history_chunks_are_reduced_without_losing_the_last_chunk(self):
        from collaboration.summary import KnowledgeSummarizer,KNOWLEDGE_INSTRUCTION
        seen=[];progress=[]
        class Capture(KnowledgeSummarizer):
            def _compress(self,text,cancel):seen.append(text);return '压缩后的上下文 '+('末尾标记' if '末尾标记' in text else '早期背景')
        result=Capture(None,lambda *args:progress.append(args)).summarize([dict(timestamp='2020',role='user',text='早'*40000+'末尾标记')],threading.Event())
        self.assertIn('末尾标记',result);self.assertGreater(len(seen),3);self.assertTrue(progress)
        self.assertIn('完整历史',KNOWLEDGE_INSTRUCTION);self.assertIn('开发计划',KNOWLEDGE_INSTRUCTION);self.assertIn('较新的决定',KNOWLEDGE_INSTRUCTION)
        cancel=threading.Event();cancel.set()
        with self.assertRaises(InterruptedError):Capture(None,lambda *a:None).summarize([dict(timestamp='a',role='user',text='test')],cancel)


if __name__=='__main__':unittest.main()
