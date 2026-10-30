"""Exercise real services and Git; replace only the external Linear transport."""
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients import call
from framework import api
from git_support import inspect_local, remote_repository
from linear_support import fetch_my_issues, graphql, LinearError, NoRedirect
from model import JsonTaskRepository, make_task
from msgs.YomkMsgs import ImportIssues, LinearCredentials
from runtime import MomoRuntime


def issue(number=1, state="started"):
    return {"id": f"issue-{number}", "identifier": f"MOM-{number}",
            "title": f"工作事项 {number}", "description": "来自 Linear 的说明",
            "url": f"https://linear.app/momo/issue/MOM-{number}/task",
            "state_type": state, "state_name": state, "team": "Momo",
            "due_date": "2026-12-01", "updated_at": "2026-10-27T10:00:00Z", "viewer_id": "viewer-1"}


def page(issues, has_next=False, cursor=None):
    nodes = [{"id": i["id"], "identifier": i["identifier"], "title": i["title"],
              "description": i["description"], "url": i["url"], "dueDate": i["due_date"],
              "updatedAt": i["updated_at"], "state": {"name": i["state_name"], "type": i["state_type"]},
              "team": {"name": i["team"]}} for i in issues]
    return {"viewer": {"id": "viewer-1", "name": "测试用户", "assignedIssues": {
        "nodes": nodes, "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}}}


class LinearTransportTests(unittest.TestCase):
    def test_pagination_current_user_filter_and_duplicates(self):
        with patch("linear_support.graphql", side_effect=[page([issue(1)], True, "next"), page([issue(1), issue(2), issue(3, "completed")])]) as transport:
            result = fetch_my_issues("test-key")
        self.assertEqual([i["id"] for i in result["issues"]], ["issue-1", "issue-2"])
        self.assertEqual(transport.call_args_list[1].args[1]["after"], "next")
        self.assertEqual(transport.call_args_list[0].args[1]["filter"]["state"]["type"]["nin"], ["completed", "canceled"])
        with patch("linear_support.graphql", return_value=page([issue(3, "completed")])) as transport:
            self.assertEqual(len(fetch_my_issues("test-key", True)["issues"]), 1)
            self.assertEqual(transport.call_args.args[1]["filter"], {})

    def test_bad_pagination_or_changed_identity_never_returns_partial_data(self):
        changed = page([issue(2)])
        changed["viewer"]["id"] = "different-user"
        for second in [page([issue(2)], True, "same"), changed, {"viewer": None}]:
            with self.subTest(second=second), patch("linear_support.graphql", side_effect=[page([issue(1)], True, "same"), second]):
                with self.assertRaises(LinearError): fetch_my_issues("test-key")

    def test_cancellation_before_and_during_request(self):
        cancel = threading.Event(); cancel.set()
        with patch("linear_support.graphql") as transport:
            with self.assertRaisesRegex(LinearError, "已取消"): fetch_my_issues("key", cancel_event=cancel)
            transport.assert_not_called()
        cancel.clear()
        def response(*args):
            cancel.set()
            return page([issue()])
        with patch("linear_support.graphql", side_effect=response):
            with self.assertRaisesRegex(LinearError, "已取消"): fetch_my_issues("key", cancel_event=cancel)

    def test_post_endpoint_personal_key_and_no_redirect(self):
        response = io.BytesIO(json.dumps({"data": page([])}).encode())
        opener = Mock(); opener.open.return_value = response
        with patch("linear_support.build_opener", return_value=opener):
            self.assertEqual(graphql("secret-for-test", {"after": None}), page([]))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.linear.app/graphql")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "secret-for-test")
        body = json.loads(request.data)
        self.assertIn("assignedIssues", body["query"])
        self.assertEqual(body["variables"], {"after": None})
        self.assertIsNone(NoRedirect().redirect_request(request, None, 302, "", {}, "https://elsewhere.test"))

    def test_http_network_and_graphql_errors_are_sanitized(self):
        for error, expected in [
            (HTTPError("url", 401, "secret-key", {}, None), "无效"),
            (HTTPError("url", 403, "secret-key", {}, None), "权限"),
            (HTTPError("url", 429, "secret-key", {}, None), "频繁"),
            (URLError("secret-key"), "网络"), (TimeoutError("secret-key"), "网络")]:
            opener = Mock(); opener.open.side_effect = error
            with self.subTest(error=error), patch("linear_support.build_opener", return_value=opener):
                with self.assertRaisesRegex(LinearError, expected) as context: graphql("secret-key", {})
                self.assertNotIn("secret-key", str(context.exception))
        for body in [b"invalid", b'{"errors":[{"message":"secret-key"}]}']:
            opener = Mock(); opener.open.return_value = io.BytesIO(body)
            with patch("linear_support.build_opener", return_value=opener):
                with self.assertRaises(LinearError) as context: graphql("secret-key", {})
                self.assertNotIn("secret-key", str(context.exception))


class IntegrationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "tasks.json"
        self.runtime = MomoRuntime(self.path, auto_tick=False).start()
        self.tasks = self.runtime.tasks
        self.linear = self.runtime.linear

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def import_rows(self, rows):
        return call("/TaskService/import_issues", ImportIssues(rows))

    def git(self, *args):
        return subprocess.run(["git", *map(str, args)], check=True, capture_output=True, text=True).stdout.strip()

    def test_delete_focused_task_settles_then_clears_session(self):
        task_id = next(t["id"] for t in self.tasks.tasks if not t["steps"])
        with patch("services.FocusService.time.monotonic", return_value=100): self.runtime.focus.start(task_id)
        with patch("services.FocusService.time.monotonic", return_value=107): self.tasks.delete(task_id)
        self.assertIsNone(self.tasks.get(task_id))
        self.assertIsNone(JsonTaskRepository(self.path).get(task_id))
        self.assertIsNone(self.runtime.focus.snapshot()["task_id"])
        self.assertFalse(self.runtime.focus.snapshot()["running"])
        self.assertNotIn(task_id, self.runtime.boot.focus_service.pending_credit)
        self.assertFalse(self.runtime.boot.focus_service.error)

    def test_delete_other_task_keeps_focus_and_failure_preserves_task(self):
        first, second = [t for t in self.tasks.tasks if not t['steps']][:2]
        self.runtime.focus.start(first["id"])
        self.tasks.delete(second["id"])
        self.assertTrue(self.runtime.focus.snapshot()["running"])
        self.assertEqual(self.runtime.focus.snapshot()["task_id"], first["id"])
        with patch.object(JsonTaskRepository, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.tasks.delete(first["id"])
        self.assertIsNotNone(self.tasks.get(first["id"]))
        self.assertFalse(self.runtime.focus.snapshot()["running"])
        self.runtime.focus.pause()

    def test_import_maps_status_dates_and_deduplicates(self):
        result = self.import_rows([issue(1), issue(2, "completed"), issue(3, "unstarted"), issue(1)])
        self.assertEqual(result["created"], 3)
        imported = [self.tasks.get(t) for t in result["task_ids"]]
        self.assertEqual([t["status"] for t in imported], ["doing", "done", "todo"])
        self.assertTrue(all(t["category"] == "work" and t["day"] == "2026-12-01" for t in imported))
        self.assertEqual(self.import_rows([issue(1)])["skipped"], 1)
        restored = JsonTaskRepository(self.path).get(result["task_ids"][0])
        self.assertEqual(restored["linear_issue"]["id"], "issue-1")

    def test_reimport_updates_remote_metadata_and_preserves_local_work(self):
        task_id = self.import_rows([issue()])["task_ids"][0]
        self.tasks.update(task_id, title="我的标题", note="续接笔记", next_step="开编辑器")
        self.tasks.add_step(task_id, "检查实现"); self.tasks.set_status(task_id, "waiting")
        self.tasks.bind_repository(task_id, "git@example.com:team/repo.git", "remote")
        updated = issue(state="completed"); updated["title"] = "远端新标题"
        updated["url"] = updated["url"].replace("/task", "/new-title")
        self.assertEqual(self.import_rows([updated])["updated"], 1)
        task = self.tasks.get(task_id)
        self.assertEqual((task["title"], task["note"], task["status"]), ("我的标题", "续接笔记", "waiting"))
        self.assertEqual(len(task["steps"]), 1); self.assertEqual(len(task["repositories"]), 1)
        self.assertEqual(task["linear_issue"]["state_type"], "completed")
        self.assertEqual(len(task["bindings"]), 1)
        self.tasks.remove_binding(task_id, 0)
        self.assertEqual(self.import_rows([updated])["updated"], 1)
        self.assertEqual(len(self.tasks.get(task_id)["bindings"]), 1)

    def test_import_matches_manual_issue_link_with_different_title_slug(self):
        task = make_task("已手动关联")
        from model import parse_binding
        task["bindings"] = [parse_binding("linear", "https://linear.app/momo/issue/MOM-1/old-slug")]
        self.tasks.add(task)
        result = self.import_rows([issue()])
        self.assertEqual((result["created"], result["updated"], result["task_ids"]), (0, 1, [task["id"]]))
        self.assertEqual(self.tasks.get(task["id"])["title"], "已手动关联")

    def test_import_validation_and_disk_failure_are_atomic(self):
        self.tasks.update(self.tasks.tasks[0]["id"], note="持久化基线")
        before = self.tasks.tasks
        disk_before = self.path.read_bytes()
        invalid = issue(2); invalid["url"] = "https://elsewhere.test/issue/MOM-2"
        with self.assertRaises(ValueError): self.import_rows([issue(), invalid])
        self.assertEqual(self.tasks.tasks, before)
        with patch.object(JsonTaskRepository, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.import_rows([issue()])
        self.assertEqual(self.tasks.tasks, before)
        self.assertEqual(self.path.read_bytes(), disk_before)

    def test_linear_session_cache_and_credential_lifetime(self):
        self.assertNotIn("test-secret", repr(LinearCredentials("test-secret")))
        self.linear.configure("test-secret")
        with patch("linear_support.graphql", return_value=page([issue()])):
            pulled = self.linear.pull()
        pulled["issues"][0]["title"] = "篡改返回值"
        result = self.linear.import_issues(["issue-1", "issue-1"])
        self.assertEqual(result["created"], 1)
        self.assertEqual(self.tasks.get(result["task_ids"][0])["title"], "工作事项 1")
        self.assertNotIn("test-secret", self.path.read_text())
        with self.assertRaises(ValueError): self.linear.import_issues(["unfetched"])
        self.linear.configure("different-test-secret")
        with self.assertRaises(ValueError): self.linear.import_issues(["issue-1"])
        with patch("linear_support.graphql", side_effect=LinearError("测试断网")):
            with self.assertRaisesRegex(OSError, "断网"): self.linear.pull()
        self.assertIsNone(self.linear.connection()["viewer"])

    def test_linear_env_fallback_and_missing_key(self):
        with patch.dict(os.environ, {"LINEAR_API_KEY": ""}):
            self.assertFalse(self.linear.connection()["configured"])
            with self.assertRaises(ValueError): self.linear.pull()
        with patch.dict(os.environ, {"LINEAR_API_KEY": "env-test-key"}), patch("linear_support.graphql", return_value=page([])) as transport:
            self.assertTrue(self.linear.connection()["configured"])
            self.linear.pull()
            self.assertEqual(transport.call_args.args[0], "env-test-key")

    def test_real_local_git_nested_path_worktree_bare_and_remote(self):
        repo = Path(self.temp.name) / "repo"
        self.git("init", repo)
        nested = repo / "sub"; nested.mkdir()
        self.assertEqual(inspect_local(str(nested))["location"], str(repo))
        self.git("-C", repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "fixture")
        worktree = Path(self.temp.name) / "linked-tree"
        self.git("-C", repo, "worktree", "add", "-b", "fixture-branch", worktree)
        self.assertEqual(inspect_local(str(worktree))["location"], str(worktree))
        bare = Path(self.temp.name) / "bare.git"; self.git("init", "--bare", bare)
        self.assertEqual(inspect_local(str(bare))["location"], str(bare))
        task_id = self.tasks.tasks[0]["id"]
        self.tasks.bind_repository(task_id, str(nested))
        with self.assertRaises(ValueError): self.tasks.bind_repository(task_id, str(repo))
        self.tasks.bind_repository(task_id, "https://example.com/team/repo.git", "remote")
        self.assertEqual(self.tasks.resolve_repository(task_id, 1)["web_url"], "https://example.com/team/repo")
        self.assertEqual(len(JsonTaskRepository(self.path).get(task_id)["repositories"]), 2)
        self.tasks.remove_repository(task_id, 0)
        self.assertEqual(len(self.tasks.get(task_id)["repositories"]), 1)

    def test_repository_validation_nonwork_missing_path_and_failed_save(self):
        for url in ["https://token@example.com/team/repo", "https://x:secret@example.com/t/r", "file:///tmp/repo", "https://example.com/team/../repo", "https://example.com/t/r?token=secret"]:
            with self.subTest(url=url), self.assertRaises(ValueError): remote_repository(url)
        self.assertEqual(remote_repository("ssh://git@example.com:2222/team/repo.git")["web_url"], "https://example.com/team/repo")
        work = self.tasks.tasks[0]; learn = self.tasks.tasks[1]
        with self.assertRaises(ValueError): self.tasks.bind_repository(learn["id"], "git@example.com:t/r", "remote")
        with self.assertRaises(ValueError): self.tasks.bind_repository(work["id"], self.temp.name)
        with patch.object(JsonTaskRepository, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.tasks.bind_repository(work["id"], "git@example.com:t/r", "remote")
        self.assertEqual(self.tasks.get(work["id"])["repositories"], [])
        repo = Path(self.temp.name) / "move-me"; self.git("init", repo)
        self.tasks.bind_repository(work["id"], str(repo))
        with self.assertRaises(ValueError): self.tasks.update(work["id"], category="learn")
        repo.rename(repo.with_name("moved"))
        with self.assertRaises(ValueError): self.tasks.resolve_repository(work["id"], 0)


if __name__ == "__main__": unittest.main()
