import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_support import (CodexRpc, CodexError, metadata, rank, recommend, message_text, redact)
from credentials import CredentialStore
from model import make_task, JsonTaskRepository
from runtime import MomoRuntime


def row(identifier="history-1", title="讨论一个方案", history="", updated=100):
    result = metadata({"id": identifier, "name": title, "preview": "先梳理一下", "updatedAt": updated, "cwd": "/tmp/project"})
    result["history"] = history
    return result


def recommendations():
    candidate = row(title="机械臂操作台的反馈")
    candidate.update(url="codex://threads/history-1", score=30, reasons=["历史对话内容与任务相近"], snippet="补齐任务卡片反馈")
    candidate.pop("history"); candidate.pop("preview")
    return {"candidates": [candidate], "scanned": 4, "history_read": 3, "failures": 0, "matched": True}


class HistoryTests(unittest.TestCase):
    def test_history_content_matches_when_title_does_not(self):
        task = make_task("确认 API 重试策略")
        rows = [row("a", "今天的闲聊", "我们已实现 API retry 退避，接下来确认重试策略。", 1),
                row("b", "散步买花", "出门去买花", 200)]
        result = rank(task, rows)
        self.assertEqual(result[0]["id"], "a")
        self.assertIn("历史对话内容与任务相近", result[0]["reasons"])
        self.assertGreater(result[0]["score"], result[1]["score"])

    def test_issue_repository_and_existing_binding(self):
        task = make_task("检查反馈")
        task["bindings"] = [{"kind": "linear", "label": "ARM-123", "url": "https://linear.app/momo/issue/ARM-123/task"},
                            {"kind": "codex", "label": "旧关联", "url": "codex://threads/already"}]
        task["repositories"] = [{"kind": "local", "location": "/tmp/project", "name": "project"}]
        result = rank(task, [row("issue", history="检查 ARM-123 的完成反馈"), row("other", history="ARM-1234"), row("already")])
        self.assertEqual(result[0]["id"], "issue")
        self.assertTrue(any("ARM-123" in reason for reason in result[0]["reasons"]))
        self.assertIn("聊天位于已关联仓库", result[0]["reasons"])
        self.assertNotIn("already", [r["id"] for r in result])
        self.assertFalse(any("issue：" in reason for reason in result[1]["reasons"]))

    def test_extraction_ignores_tools_reasoning_and_redacts_keys(self):
        items = [{"type": "userMessage", "content": [{"type": "text", "text": "重试 lin_api_private_test_credential"}]},
                 {"type": "agentMessage", "text": "检查 API 重试"},
                 {"type": "commandExecution", "aggregatedOutput": "DO NOT INDEX THIS"},
                 {"type": "reasoning", "summary": ["DO NOT INDEX THIS"]}]
        text = message_text(items)
        self.assertNotIn("private_test", text); self.assertNotIn("DO NOT INDEX", text)
        self.assertIn("密钥已隐藏", text); self.assertIn("检查 API", text)

    def test_recommend_reads_content_caches_and_returns_no_full_history(self):
        rpc = Mock()
        rpc.list_threads.return_value = [{"id": "thread1", "name": "代码讨论", "preview": "你好", "updatedAt": 1, "cwd": "/tmp"}]
        rpc.history.return_value = "Python 装饰器和闭包的例子"
        rpc_class = MagicMock(); rpc_class.return_value.__enter__.return_value = rpc
        cache = {}
        with patch("codex_support.CodexRpc", rpc_class):
            result = recommend(make_task("理解 Python 装饰器"), cache=cache)
            self.assertTrue(result["matched"])
            self.assertNotIn("history", result["candidates"][0])
            self.assertNotIn("preview", result["candidates"][0])
            recommend(make_task("理解 Python 装饰器"), cache=cache)
        self.assertEqual(rpc.history.call_count, 1)

    def test_stdio_transport_interleaved_notification_and_process_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "fake-codex"
            executable.write_text('''#!/usr/bin/env python3
import json,sys
for line in sys.stdin:
    message=json.loads(line)
    if "id" not in message or "method" not in message: continue
    method=message["method"]
    print(json.dumps({"method":"test/notification","params":{}}),flush=True)
    if method=="initialize": result={"version":"fixture"}
    elif method=="thread/list": result={"data":[{"id":"fixture-chat"}],"nextCursor":None}
    elif method=="thread/items/list": result={"data":[{"item":{"type":"agentMessage","text":"历史内容"}}]}
    else: result={}
    print(json.dumps({"id":message["id"],"result":result}),flush=True)
''')
            executable.chmod(0o755)
            with patch("codex_support.shutil.which", return_value=str(executable)):
                with CodexRpc() as rpc:
                    process = rpc.process
                    self.assertEqual(rpc.list_threads()[0]["id"], "fixture-chat")
                    self.assertEqual(rpc.history("fixture-chat"), "历史内容")
                    with self.assertRaises(CodexError): rpc.request("turn/start", {})
                self.assertIsNotNone(process.poll())

    def test_missing_cli_and_cancel_are_actionable(self):
        with patch("codex_support.shutil.which", return_value=None):
            with self.assertRaisesRegex(CodexError, "手动关联"):
                with CodexRpc(): pass
        cancel = threading.Event(); cancel.set()
        with self.assertRaisesRegex(CodexError, "已取消"): CodexRpc(cancel).check_cancel()


class CredentialTests(unittest.TestCase):
    def test_private_file_roundtrip_permissions_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(CredentialStore, "keyring", return_value=None):
            store = CredentialStore(temp)
            self.assertEqual(store.save("test-device-secret"), "file")
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(store.directory.stat().st_mode), 0o700)
            self.assertEqual(store.load(), ("test-device-secret", "file"))
            store.path.chmod(0o644)
            with self.assertRaises(OSError): store.load()
            store.path.unlink(); store.path.symlink_to(Path(temp) / "other")
            with self.assertRaises(OSError): store.save("new-secret")
            store.clear(); self.assertEqual(store.load(), ("", ""))

    def test_system_keyring_roundtrip_without_file(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = Mock(); backend.get_password.return_value = "system-secret"
            with patch.object(CredentialStore, "keyring", return_value=backend):
                store = CredentialStore(temp)
                self.assertEqual(store.save("system-secret"), "keyring")
                self.assertFalse(store.path.exists())
                self.assertEqual(store.load(), ("system-secret", "keyring"))
                store.clear(); backend.delete_password.assert_called_once()


class CodexServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "tasks.json"
        self.runtime = MomoRuntime(self.path, auto_tick=False).start()
        self.task_id = self.runtime.tasks.tasks[0]["id"]

    def tearDown(self):
        self.runtime.close(); self.temp.cleanup()

    def test_recommend_then_explicit_bind_preserves_real_title(self):
        before = self.runtime.tasks.tasks
        with patch("services.CodexService.recommend", return_value=recommendations()):
            result = self.runtime.codex.recommend(self.task_id)
        self.assertEqual(self.runtime.tasks.tasks, before)
        result["candidates"][0]["title"] = "篡改返回值"
        binding = self.runtime.codex.bind(self.task_id, "history-1")
        self.assertEqual(binding["label"], "机械臂操作台的反馈")
        self.assertEqual(JsonTaskRepository(self.path).get(self.task_id)["bindings"][-1]["label"], binding["label"])
        with self.assertRaises(ValueError): self.runtime.codex.bind(self.task_id, "unfetched")
        with self.assertRaises(ValueError): self.runtime.codex.bind(self.task_id, "history-1")

    def test_failed_recommend_is_visible_and_new_chat_is_never_automatic(self):
        with patch("services.CodexService.recommend", side_effect=CodexError("未找到 CLI")), patch("services.CodexService.CodexRpc") as rpc:
            with self.assertRaisesRegex(OSError, "CLI"): self.runtime.codex.recommend(self.task_id)
            rpc.assert_not_called()

    def test_create_empty_chat_only_on_explicit_endpoint_and_retry_binding(self):
        rpc = Mock(); rpc.create.return_value = "created-chat"
        factory = MagicMock(); factory.return_value.__enter__.return_value = rpc
        with patch("services.CodexService.CodexRpc", factory):
            with patch.object(JsonTaskRepository, "save", side_effect=OSError("disk full")):
                with self.assertRaises(OSError): self.runtime.codex.create(self.task_id, self.temp.name)
            result = self.runtime.codex.create(self.task_id, self.temp.name)
        self.assertEqual(result["url"], "codex://threads/created-chat")
        rpc.create.assert_called_once()

    def test_remember_linear_key_restart_and_forget(self):
        with patch.object(CredentialStore, "keyring", return_value=None):
            self.runtime.linear.configure("remember-test-key", remember=True)
            self.assertTrue(self.runtime.linear.connection()["remembered"])
            self.runtime.close()
            self.runtime = MomoRuntime(self.path, auto_tick=False).start()
            self.assertTrue(self.runtime.linear.connection()["configured"])
            self.assertNotIn("remember-test-key", str(self.runtime.linear.connection()))
            self.runtime.linear.forget()
            self.assertFalse(self.runtime.linear.connection()["remembered"])
            self.assertFalse(CredentialStore(self.path.parent).path.exists())


if __name__ == "__main__": unittest.main()
