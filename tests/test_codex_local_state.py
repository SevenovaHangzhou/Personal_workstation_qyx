import sqlite3
import tempfile
import unittest
from pathlib import Path
from codex_support import local_thread_state


class LocalStateTests(unittest.TestCase):
    def test_missing_file_needs_all_empty_evidence_and_does_not_write_databases(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);rollout=home/'missing.jsonl'
            db=sqlite3.connect(home/'state_5.sqlite')
            db.execute('create table threads(id,rollout_path,cwd,has_user_event,tokens_used,first_user_message,preview)')
            db.execute('insert into threads values(?,?,?,?,?,?,?)',('old',str(rollout),temp,0,0,'',''));db.commit();db.close()
            state=local_thread_state('old',home)
            self.assertTrue(state['missing']);self.assertFalse(state['empty'])
            self.assertFalse((home/'thread_history_1.sqlite').exists())
            history=sqlite3.connect(home/'thread_history_1.sqlite')
            for table in ('thread_turns','thread_items','thread_realtime_items'):history.execute('create table '+table+'(thread_id)')
            history.commit();history.close()
            before={p:p.read_bytes() for p in home.iterdir()}
            self.assertTrue(local_thread_state('old',home)['empty'])
            self.assertIsNone(local_thread_state('unknown',home))
            self.assertEqual(before,{p:p.read_bytes() for p in home.iterdir()})
            history=sqlite3.connect(home/'thread_history_1.sqlite');history.execute("insert into thread_items values('old')");history.commit();history.close()
            self.assertFalse(local_thread_state('old',home)['empty'])
            rollout.write_text('session metadata')
            self.assertFalse(local_thread_state('old',home)['missing'])

    def test_unknown_home_does_not_create_state_or_claim_chat_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(local_thread_state('manual-chat',temp));self.assertEqual(list(Path(temp).iterdir()),[])


if __name__=='__main__':unittest.main()
