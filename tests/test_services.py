"""Integration tests use the actual compiled YomkServerPy, not API doubles."""
import json
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from framework import api
from runtime import MomoRuntime
from clients import call
from model import JsonTaskRepository, make_task
from msgs.YomkMsgs import (TaskId, CreateTask, UpdateTask, StepInput, StepState,
                          BindingInput, BindingIndex, FocusStart, FocusMinutes)
from typedefine.TypeDefine import CONTEXT_GROUP, CTX_REVISION, CTX_FOCUS, FOCUS_LOOP


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "tasks.json"
        self.runtime = MomoRuntime(self.path, auto_tick=False).start()
        self.tasks = self.runtime.tasks
        self.focus = self.runtime.focus

    def tearDown(self):
        self.runtime.close()
        self.assertNotIn(FOCUS_LOOP, api.event_loop_info_loops().data)
        self.assertNotIn("/TaskService", api.server_info_services().data)
        self.temp.cleanup()

    def test_real_framework_registration_and_request_metadata(self):
        self.assertEqual(api.version(), "1.5.6")
        import YomkServerPy
        self.assertTrue(YomkServerPy.__file__.endswith((".so", "YomkServerPy.py")))
        names = api.server_info_services().data
        self.assertTrue({"/TaskService", "/FocusService", "/BindingService", "/GitService", "/LinearService"}.issubset(names))
        info = api.server_info_function("/TaskService/get")
        self.assertEqual(info.status, api.ResStatus.eOk)
        self.assertEqual(info.data.msg_name, "TaskId")

    def test_reference_messages_and_responses_cannot_mutate_service_state(self):
        task = make_task("独立消息", "demo")
        task["steps"] = [{"title": "第一步", "done": False}]
        created = call("/TaskService/create", CreateTask(task))
        task["steps"][0]["title"] = "篡改请求"
        created["steps"][0]["title"] = "篡改返回值"
        self.assertEqual(self.tasks.get(created["id"])["steps"][0]["title"], "第一步")
        snapshot = self.tasks.tasks
        snapshot.clear()
        self.assertGreater(len(self.tasks.tasks), 0)

    def test_invalid_messages_return_responses_without_changing_data(self):
        task = self.tasks.tasks[0]
        original = self.tasks.get(task["id"])
        for url, payload in [
            ("/TaskService/update", {"task_id": task["id"]}),
            ("/TaskService/update", UpdateTask(task["id"], {"id": "new-id"})),
            ("/TaskService/update", UpdateTask(task["id"], {"title": ""})),
            ("/TaskService/set_step", StepState(task["id"], -1, True)),
            ("/TaskService/set_step", StepState(task["id"], 0, "yes")),
            ("/BindingService/bind", BindingInput(task["id"], "linear", None)),
            ("/FocusService/set_duration", FocusMinutes(True)),
            ("/FocusService/start", FocusStart(123)),
        ]:
            with self.subTest(url=url, payload=payload):
                self.assertEqual(api.request(url, payload).status, api.ResStatus.eInvalid)
        self.assertEqual(self.tasks.get(task["id"]), original)
        self.assertEqual(api.context_get(CTX_REVISION, -1, CONTEXT_GROUP), 0)

    def test_concurrent_atomic_steps_and_duplicate_binding(self):
        task = make_task("并发请求", "demo")
        self.tasks.add(task)
        def add_step(index):
            return api.request("/TaskService/add_step", StepInput(task["id"], f"步骤 {index}"))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(add_step, range(24)))
        self.assertTrue(all(r.status == api.ResStatus.eOk for r in results))
        self.assertEqual(len(self.tasks.get(task["id"])["steps"]), 24)
        self.assertEqual(len(JsonTaskRepository(self.path).get(task["id"])["steps"]), 24)
        payload = BindingInput(task["id"], "codex", "codex://threads/same-thread")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: api.request("/BindingService/bind", payload), range(2)))
        self.assertEqual(sum(r.status == api.ResStatus.eOk for r in results), 1)
        self.assertEqual(len(self.tasks.get(task["id"])["bindings"]), 1)

    def test_binding_resolve_and_remove_roundtrip(self):
        task = self.tasks.tasks[0]
        self.tasks.bind(task["id"], "linear", "https://linear.app/team/issue/APP-122/title")
        with self.assertRaisesRegex(ValueError, "一一对应"):
            self.tasks.bind(task["id"], "linear", "https://linear.app/team/issue/APP-123/title")
        self.tasks.remove_binding(task["id"], len(self.tasks.get(task["id"])["bindings"])-1)
        self.tasks.bind(task["id"], "linear", "https://linear.app/team/issue/APP-123/title")
        bindings = self.tasks.get(task["id"])["bindings"]
        index = len(bindings) - 1
        self.assertEqual(self.tasks.resolve_binding(task["id"], index)["label"], "APP-123")
        self.assertEqual(api.request("/BindingService/resolve", BindingIndex(task["id"], 0)).status, api.ResStatus.eInvalid)
        self.tasks.remove_binding(task["id"], index)
        self.assertEqual(len(self.tasks.get(task["id"])["bindings"]), len(bindings) - 1)

    def test_event_loop_updates_focus_without_gui_and_completion_is_separate(self):
        task_id = self.tasks.tasks[0]["id"]
        with patch("services.FocusService.time.monotonic", return_value=1000):
            self.focus.start(task_id)
        with patch("services.FocusService.time.monotonic", return_value=2502):
            outer = api.event_loop_post_wait(FOCUS_LOOP, None)
        self.assertEqual(outer.status, api.ResStatus.eOk)
        self.assertEqual(outer.data.m_response.status, api.ResStatus.eOk)
        state = self.focus.snapshot()
        self.assertFalse(state["running"])
        self.assertEqual(state["remaining"], 0)
        self.assertEqual(state["completion_serial"], 1)
        self.assertEqual(self.tasks.get(task_id)["focus_seconds"], 1500)
        self.assertEqual(self.tasks.get(task_id)["status"], "doing")
        self.assertEqual(api.context_get(CTX_FOCUS, None, CONTEXT_GROUP), state)

    def test_focus_checkpoint_failure_retains_credit_and_retries_once(self):
        task_id = self.tasks.tasks[0]["id"]
        with patch("services.FocusService.time.monotonic", return_value=1000):
            self.focus.start(task_id)
        with patch("services.FocusService.time.monotonic", return_value=1032), patch.object(JsonTaskRepository, "save", side_effect=OSError("disk full")):
            response = api.request("/FocusService/tick", None)
        self.assertEqual(response.status, api.ResStatus.eNo)
        self.assertFalse(self.focus.snapshot()["running"])
        self.assertIn("disk full", self.focus.snapshot()["error"])
        self.assertEqual(self.tasks.get(task_id)["focus_seconds"], 0)
        self.focus.pause()
        self.focus.pause()
        self.assertEqual(JsonTaskRepository(self.path).get(task_id)["focus_seconds"], 32)
        self.assertEqual(self.focus.snapshot()["error"], "")

    def test_focus_fractional_time_and_preferences(self):
        first, second = self.tasks.tasks[:2]
        with patch("services.FocusService.time.monotonic", return_value=1000): self.focus.start(first["id"])
        with patch("services.FocusService.time.monotonic", return_value=1000.6): self.focus.start(second["id"])
        with patch("services.FocusService.time.monotonic", return_value=1000.8): self.focus.start(first["id"])
        with patch("services.FocusService.time.monotonic", return_value=1001.4): self.focus.pause()
        self.assertEqual(self.tasks.get(first["id"])["focus_seconds"], 1)
        self.focus.set_duration(45)
        self.assertEqual(self.focus.snapshot()["remaining"], 2700)
        self.assertEqual(JsonTaskRepository(self.path).settings["focus_minutes"], 45)

    def test_service_callbacks_are_inactive_after_unregister(self):
        retained = self.runtime.boot.focus_service.functions["/start"]
        self.runtime.close()
        self.assertEqual(retained(FocusStart()).status, api.ResStatus.eNo)
        self.assertFalse(any(t.name == "momo-focus-clock" for t in threading.enumerate()))


class RuntimeTests(unittest.TestCase):
    def test_original_v1_data_is_kept_and_corrupt_boot_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tasks.json"
            task = make_task("旧版数据不丢失", "learn")
            task.pop("repositories")  # JSON written before repository associations existed.
            payload = {"version": 1, "sample": False, "tasks": [task]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            with MomoRuntime(path, auto_tick=False) as runtime:
                self.assertEqual(runtime.tasks.get(task["id"])["title"], "旧版数据不丢失")
                self.assertEqual(runtime.tasks.settings["focus_minutes"], 25)
            path.write_text("{invalid", encoding="utf-8")
            with self.assertRaises(RuntimeError): MomoRuntime(path, auto_tick=False).start()
            self.assertEqual(path.read_text(), "{invalid")
            self.assertNotIn("/TaskService", api.server_info_services().data)
            with MomoRuntime(Path(temp) / "fresh.json", auto_tick=False) as runtime:
                self.assertEqual(len([t for t in runtime.tasks.tasks if not t.get("parent_id")]), 7)

    def test_scheduler_advances_without_qt_events_and_stops(self):
        with tempfile.TemporaryDirectory() as temp:
            with MomoRuntime(Path(temp) / "tasks.json") as runtime:
                runtime.focus.start()
                deadline = time.monotonic() + 2
                while runtime.focus.snapshot()["remaining"] == 1500 and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertLess(runtime.focus.snapshot()["remaining"], 1500)
                task_id = runtime.focus.snapshot()["task_id"]
                runtime.focus.pause()
                self.assertGreaterEqual(runtime.tasks.get(task_id)["focus_seconds"], 1)
            self.assertFalse(any(t.name == "momo-focus-clock" for t in threading.enumerate()))


if __name__ == "__main__": unittest.main()
