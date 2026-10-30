"""Protect empty-chat creation and targeted replacement without sending turns."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock,MagicMock
from codex_support import CodexRpc,CodexError
from runtime import MomoRuntime
from model import JsonTaskRepository,make_task


class PersistenceTests(unittest.TestCase):
    def test_create_requests_legacy_and_checks_materialized_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'rollout.jsonl';rpc=CodexRpc();calls=[]
            def request(method,params):
                calls.append((method,params))
                if method=='thread/start':return {'thread':{'id':'new','path':str(path)}}
                path.write_text('session metadata\n');return {}
            with patch.object(rpc,'request',side_effect=request):self.assertEqual(rpc.create('title',temp),'new')
            self.assertEqual(calls[0][1]['historyMode'],'legacy')
            self.assertFalse(any(method=='turn/start' for method,_ in calls))
            path.unlink()
            with patch.object(rpc,'request',side_effect=[{'thread':{'id':'broken','path':str(path)}},{}]):
                with self.assertRaisesRegex(CodexError,'未绑定'):rpc.create('title',temp)


if __name__=='__main__':unittest.main()
