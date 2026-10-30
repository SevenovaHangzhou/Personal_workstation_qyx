"""Exercise real pinned services/SQLite and controlled child processes; no model calls."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import MomoRuntime
from model import make_task, parse_binding
from station_executor import CodexExecutor


class ControlledExecutor:
    def __init__(self):
        self.started = []
        self.release = threading.Event()
        self.result = {"status": "review", "summary": "完成", "question": "", "tests": [], "artifacts": []}

    def status(self):
        return {"available": True, "message": "测试传输"}

    def run(self, record, folder, cancel):
        self.started.append(record["task_id"])
        while not self.release.wait(0.01):
            if cancel.is_set():
                return {"status": "cancelled"}
        return dict(self.result)


class StationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = MomoRuntime(self.root / "tasks.json", auto_tick=False).start()
        self.station = self.runtime.station
        self.executor = ControlledExecutor()
        self.station.executor = self.executor
        self.station.call("configure", concurrency=2)
        self.project = self.station.call("create_project", name="本机项目")["project_id"]
        self.ids = []
        for title in ("甲", "乙", "丙"):
            task = make_task(title, "demo")
            self.runtime.tasks.add(task)
            self.ids.append(task["id"])
        self.a, self.b = self.root / "a", self.root / "b"
        self.a.mkdir(); self.b.mkdir()

    def tearDown(self):
        self.executor.release.set()
        self.runtime.close()
        self.temp.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(predicate())

    def submit(self, index=0, cwd=None, request_id=None):
        return self.station.call("submit", request_id=request_id or str(uuid.uuid4()),
            momo_id=self.ids[index], project_id=self.project, cwd=str(cwd or self.a),
            goal="实现目标", acceptance="验证目标", sandbox="read-only")

    def test_repository_maintenance_reservation_blocks_new_execution(self):
        with self.station.reserve_repository(str(self.a)):
            record=self.submit()
            self.assertEqual(self.station.db.get_task(record["task_id"]).status,"pending")
            self.assertFalse(self.executor.started)
        self.wait(lambda:record["task_id"] in self.executor.started)
        with self.assertRaisesRegex(RuntimeError,"正在执行"):
            with self.station.reserve_repository(str(self.a)): pass

    def test_real_service_project_and_idempotent_execution(self):
        request = str(uuid.uuid4())
        first = self.submit(request_id=request)
        self.assertEqual(self.submit(request_id=request)["task_id"], first["task_id"])
        self.wait(lambda: len(self.executor.started) == 1)
        with self.assertRaisesRegex(ValueError, "不同内容"):
            self.station.call("submit", request_id=request, momo_id=self.ids[0], project_id=self.project,
                cwd=str(self.a), goal="另一个目标", acceptance="验证目标", sandbox="read-only")
        self.assertIn("/DesktopExecutionService", [s["service"] for s in self.station.call("snapshot")["services"]])
        self.assertIn("/ProjectLifecycleService", [s["service"] for s in self.station.projects.services.describe()])
        self.assertIsNot(self.station.services.server, self.station.projects.services.server)
        self.executor.release.set()
        self.wait(lambda: self.station.db.get_task(request).status == "review")
        record = self.station.db.get_task(request)
        with self.assertRaisesRegex(ValueError, "状态已变化"):
            self.station.call("review", task_id=request, expected_completed_at=0, accepted=True)
        self.station.call("review", task_id=request, expected_completed_at=record.completed_at, accepted=True)
        self.assertEqual(self.station.db.get_task(request).status, "accepted")
        self.assertNotEqual(self.runtime.tasks.get(self.ids[0])["status"], "done")

    def test_concurrency_directory_exclusion_and_cancel_acknowledgement(self):
        first = self.submit()
        second = self.submit(1)
        third = self.submit(2, self.b)
        self.wait(lambda: len(self.executor.started) == 2)
        self.assertEqual(self.station.db.get_task(second["task_id"]).status, "pending")
        self.assertIn(third["task_id"], self.executor.started)
        self.station.call("cancel", task_id=first["task_id"])
        self.wait(lambda: self.station.db.get_task(first["task_id"]).status == "cancelled")
        self.wait(lambda: second["task_id"] in self.executor.started)
        self.assertLessEqual(len(self.station.workers), 2)

    def test_issue_guard_nonwork_execution_and_restart_does_not_resubmit(self):
        work = next(t for t in self.runtime.tasks.tasks if t["category"] == "work")
        # Work drafts can execute locally while awaiting the nightly issue mapping.
        self.assertFalse(work.get("linear_issue"))
        first = self.submit()
        second = self.submit(1)
        self.station.close()
        self.assertFalse(self.station.workers)
        from station_backend import EmbeddedStation
        restored = EmbeddedStation(self.root / "station", self.runtime.tasks, executor=ControlledExecutor())
        self.runtime.station = restored
        self.station = restored
        self.assertEqual(restored.db.get_task(second["task_id"]).status, "interrupted")
        self.assertEqual(restored.concurrency, 2)
        self.assertFalse(restored.workers)
        self.assertTrue(list((restored.folder / "backups").glob("*.sqlite3")))

    def test_clarification_followup_keeps_original_result(self):
        self.executor.result.update(status="needs_input", question="选择哪种格式？")
        first = self.submit()
        self.executor.release.set()
        self.wait(lambda: self.station.db.get_task(first["task_id"]).status == "needs_input")
        request = str(uuid.uuid4())
        self.station.call("retry", task_id=first["task_id"], request_id=request, reply="使用 JSON")
        self.assertEqual(self.station.db.get_task(first["task_id"]).output_data["question"], "选择哪种格式？")
        self.assertIn("使用 JSON", self.station.db.get_task(request).input_data["goal"])
        with self.assertRaisesRegex(ValueError, "后续执行"):
            self.station.call("retry", task_id=first["task_id"], request_id=str(uuid.uuid4()), reply="再次提交")

    def test_same_git_root_is_serialized_across_different_subdirectories(self):
        import subprocess
        subprocess.run(["git", "init", "-q", str(self.a)], check=True)
        left, right = self.a / "left", self.a / "right"
        left.mkdir(); right.mkdir()
        self.submit(0, left)
        second = self.submit(1, right)
        self.wait(lambda: len(self.executor.started) == 1)
        self.assertEqual(self.station.db.get_task(second["task_id"]).status, "pending")
        self.executor.release.set()
        self.wait(lambda: second["task_id"] in self.executor.started)

    def test_project_link_is_independent_of_model_execution(self):
        self.station.call("link_project", momo_id=self.ids[0], project_id=self.project)
        self.assertEqual(self.station.call("snapshot")["links"][self.ids[0]], self.project)
        self.assertFalse(self.executor.started)
        self.assertEqual(self.station.db.list_tasks(), [])

    def test_old_login_directory_setting_no_longer_overrides_system_codex(self):
        conn = self.station.db._get_conn()
        conn.execute("INSERT OR REPLACE INTO momo_settings VALUES ('codex_home', '/old/missing/auth')")
        conn.commit()
        self.station.close()
        from station_backend import EmbeddedStation
        restored = EmbeddedStation(self.root / "station", self.runtime.tasks)
        self.runtime.station = restored
        self.station = restored
        self.assertIsNone(restored.db._get_conn().execute("SELECT value FROM momo_settings WHERE key='codex_home'").fetchone())
        with patch("station_executor.shutil.which", return_value="codex"), patch.dict(os.environ, {"CODEX_HOME": "/system/current/auth"}):
            self.assertTrue(restored.call("snapshot")["codex"]["available"])
            self.assertEqual(restored.executor.environment()["CODEX_HOME"], "/system/current/auth")

    def test_second_instance_cannot_recover_or_mutate_live_database(self):
        from station_backend import EmbeddedStation
        self.submit()
        with self.assertRaisesRegex(RuntimeError, "已在运行"):
            EmbeddedStation(self.root / "station", self.runtime.tasks)
        self.assertEqual(self.station.db.list_tasks(status="running")[0].status, "running")


class ExecutorTests(unittest.TestCase):
    def test_actual_child_protocol_auth_environment_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "codex"
            binary.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
assert sys.argv[1] == 'exec', 'must not inspect or change login'
assert 'LINEAR_API_KEY' not in os.environ
assert os.environ['CODEX_HOME'].endswith('auth')
assert 'workspace-write' in sys.argv
assert not any(arg.startswith('model_provider=') for arg in sys.argv)
assert os.environ['OPENAI_API_KEY'] == 'existing-test-config'
assert os.environ['CUSTOM_CODEX_AUTH'] == 'existing-auth'
prompt = sys.stdin.read()
assert '验收条件' in prompt
cwd = pathlib.Path(sys.argv[sys.argv.index('-C')+1])
(cwd/'result.txt').write_text('result')
report = pathlib.Path(sys.argv[sys.argv.index('-o')+1])
report.write_text(json.dumps({'status':'review','summary':'已完成','question':'','tests':['实际子进程检查'],'artifacts':['result.txt','/etc/passwd']}))
print(json.dumps({'type':'thread.started','thread_id':'test-thread'}))
''')
            binary.chmod(0o755)
            (root / "auth").mkdir()
            executor = CodexExecutor()
            with patch("station_executor.shutil.which", return_value=str(binary)), patch.dict(os.environ, {"LINEAR_API_KEY": "never-pass", "CODEX_HOME": str(root / "auth"), "OPENAI_API_KEY": "existing-test-config", "CUSTOM_CODEX_AUTH": "existing-auth"}):
                result = executor.run({"input_data": {"cwd": str(root), "goal": "生成文件", "acceptance": "文件存在", "sandbox": "workspace-write"}}, root / "logs", threading.Event())
            self.assertEqual(result["status"], "review")
            self.assertEqual(result["codex_thread_id"], "test-thread")
            self.assertEqual(result["artifacts"], [str(root / "result.txt")])

    def test_system_cli_is_available_without_authentication_probe(self):
        executor = CodexExecutor()
        with patch("station_executor.shutil.which", return_value="codex"), patch("station_executor.subprocess.run") as probe, patch("station_executor.subprocess.Popen") as child:
            self.assertTrue(executor.status()["available"])
            probe.assert_not_called()
            child.assert_not_called()

    def test_missing_cli_rejects_without_starting_execution(self):
        with patch("station_executor.shutil.which", return_value=None), patch("station_executor.subprocess.Popen") as child:
            executor = CodexExecutor()
            self.assertFalse(executor.status()["available"])
            with self.assertRaisesRegex(RuntimeError, "未找到"):
                executor.run({}, Path("unused"), threading.Event())
            child.assert_not_called()

    def test_cancel_terminates_actual_child_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "codex"
            binary.write_text("#!/usr/bin/env python3\nimport sys,time\nassert sys.argv[1]=='exec'\nsys.stdin.read()\ntime.sleep(60)\n")
            binary.chmod(0o755)
            cancel = threading.Event()
            timer = threading.Timer(0.2, cancel.set)
            timer.start()
            start = time.monotonic()
            with patch("station_executor.shutil.which", return_value=str(binary)):
                result = CodexExecutor().run({"input_data":{"cwd":str(root),"goal":"检查","acceptance":"返回","sandbox":"read-only"}}, root/"logs", cancel)
            timer.join()
            self.assertEqual(result["status"], "cancelled")
            self.assertLess(time.monotonic()-start, 4)


if __name__ == "__main__": unittest.main()
