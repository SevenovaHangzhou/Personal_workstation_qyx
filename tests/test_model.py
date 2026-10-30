import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import JsonTaskRepository, make_task, parse_binding, progress


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"tasks.json"
        self.store=JsonTaskRepository(self.path)

    def tearDown(self): self.temp.cleanup()

    def test_task_and_multiple_bindings_roundtrip(self):
        self.store.clear_samples()
        task=make_task("写一个小实验","work",next_step="先画草图")
        task["bindings"]=[parse_binding("linear","https://linear.app/alfa/issue/ARM-128/title")]
        self.store.add(task)
        self.store.bind(task["id"],"codex","codex://threads/abc-123")
        self.store.bind(task["id"],"codex","codex://threads/def-456")
        with self.assertRaises(ValueError): self.store.bind(task["id"],"codex","codex://threads/abc-123")
        restored=JsonTaskRepository(self.path)
        self.assertEqual(restored.tasks,self.store.tasks)
        self.assertEqual(restored.tasks[0]["bindings"][0]["label"],"ARM-128")
        restored.remove_binding(task["id"],1)
        self.assertEqual(len(JsonTaskRepository(self.path).tasks[0]["bindings"]),2)
        self.assertFalse(restored.sample)

    def test_invalid_and_unexpected_links_rejected(self):
        for kind,url in [("codex","https://example.com"),("codex","codex://threads/abc?run=1"),("codex","codex://threads/../../bad"),("linear","http://linear.app/a/issue/APP-1/x"),("linear","https://linear.app.evil.com/a/issue/APP-1/x"),("linear","https://linear.app@evil.com/a/issue/APP-1/x"),("linear","file:///tmp/x")]:
            with self.subTest(url=url),self.assertRaises(ValueError): parse_binding(kind,url)

    def test_save_failure_rolls_back_task_mutations(self):
        task=self.store.tasks[0]; original=task["title"]; count=len(self.store.tasks)
        with patch.object(self.store,"save",side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.store.update(task["id"],title="丢失的修改")
            with self.assertRaises(OSError): self.store.add(make_task("新任务","demo"))
        self.assertEqual(task["title"],original); self.assertEqual(len(self.store.tasks),count)

    def test_corrupt_data_preserved(self):
        for content in ["{broken", "[]", json.dumps({"version":1,"tasks":[{}]}), json.dumps({"version":1,"tasks":[],"settings":None})]:
            self.path.write_text(content,encoding="utf-8")
            with self.assertRaises(ValueError): JsonTaskRepository(self.path)
            self.assertEqual(self.path.read_text(encoding="utf-8"),content)

    def test_preferences_and_progress(self):
        self.store.set_focus_minutes(45)
        self.assertEqual(JsonTaskRepository(self.path).settings["focus_minutes"],45)
        task=self.store.tasks[0]; self.assertEqual(progress(task),40)
        task["status"]="done"; self.assertEqual(progress(task),100)


if __name__=="__main__": unittest.main()
