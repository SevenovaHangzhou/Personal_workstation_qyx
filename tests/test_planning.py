"""Dependency integrity, atomic service writes, and native planning interaction."""
import copy
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from dag_layout import layout_dag, layers_for
from model import make_task, JsonTaskRepository
from task_plan import plan_rows
from runtime import MomoRuntime
from main import Momo, STYLE
from plan_ui import PlanDialog, TaskDag

APP = QApplication.instance() or QApplication([])
APP.setStyle("Fusion")
APP.setStyleSheet(STYLE)


def step(title, deps=()):
    return {"id": str(uuid.uuid4()), "title": title, "depends_on": list(deps)}


class LayoutTests(unittest.TestCase):
    def test_split_join_and_skip_level_remain_downward_without_overlap(self):
        edges = [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"), ("a", "d")]
        positions, layers = layout_dag("abcd", edges)
        self.assertEqual(layers, [["a"], ["b", "c"], ["d"]])
        for source, target in edges:
            self.assertGreater(positions[target][1], positions[source][1] + 96)
        self.assertGreaterEqual(abs(positions["b"][0] - positions["c"][0]), 208)
        self.assertEqual(layout_dag("abcd", edges), (positions, layers))

    def test_invalid_graph_is_rejected_instead_of_drawing_misleading_arrows(self):
        for ids, edges in [("ab", [("a", "b"), ("b", "a")]), ("a", [("a", "a")]),
                           ("ab", [("a", "missing")]), (["a", "a"], [])]:
            with self.subTest(ids=ids, edges=edges), self.assertRaises(ValueError):
                layers_for(ids, edges)


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "tasks.json"
        self.runtime = MomoRuntime(self.path, auto_tick=False).start()
        self.tasks = self.runtime.tasks
        self.tasks.clear_samples()
        self.parent = make_task("规划示例", "learn")
        self.tasks.add(self.parent)
        self.window = None

    def tearDown(self):
        if self.window:
            self.window.exiting = True
            self.window.close()
            self.window.deleteLater()
            APP.processEvents()
        self.runtime.close()
        self.temp.cleanup()

    def rows(self):
        return plan_rows(self.tasks.tasks, self.parent["id"])

    def save(self, rows, baseline=None):
        return self.tasks.apply_plan(self.parent["id"], rows, self.rows() if baseline is None else baseline)

    def open_dialog(self):
        if not self.window:
            self.window = Momo(self.runtime)
            self.window.show()
        dialog = PlanDialog(self.window, self.parent["id"])
        dialog.show()
        APP.processEvents()
        self.addCleanup(lambda: (dialog.reject(), dialog.deleteLater(), APP.processEvents()))
        return dialog

    def test_serial_parallel_join_round_trip_keeps_real_task_state_and_children(self):
        a, b = step("调研"), step("草图")
        c = step("实现", [a["id"], b["id"]])
        d = step("验证", [c["id"]])
        self.save([a, b, c, d])
        self.tasks.set_status(a["id"], "done")
        self.tasks.update(c["id"], note="保留笔记")
        nested = self.tasks.add_children(c["id"], ["内部检查"])[0]
        before = self.rows()
        changed = copy.deepcopy(before)
        changed[2]["title"] = "完成最小实现"
        self.save(changed, before)
        restored = JsonTaskRepository(self.path)
        self.assertEqual(restored.get(c["id"])["depends_on"], [a["id"], b["id"]])
        self.assertEqual(restored.get(c["id"])["note"], "保留笔记")
        self.assertEqual(restored.get(a["id"])["status"], "done")
        self.assertEqual(restored.get(nested["id"])["parent_id"], c["id"])
        self.assertEqual(restored.get(self.parent["id"])["status"], "todo")

    def test_old_v2_steps_do_not_acquire_inferred_dependencies(self):
        self.tasks.add_children(self.parent["id"], ["先调研", "再实现", "最后验证"])
        payload = json.loads(self.path.read_text())
        for task in payload["tasks"]:
            task.pop("depends_on", None)
        self.path.write_text(json.dumps(payload))
        restored = JsonTaskRepository(self.path)
        rows = plan_rows(restored.tasks, self.parent["id"])
        self.assertEqual(layers_for([r["id"] for r in rows], []), [[r["id"] for r in rows]])
        self.assertTrue(all(not r["depends_on"] for r in rows))

    def test_cycle_missing_duplicate_and_cross_parent_edges_cannot_change_disk(self):
        a, b = step("A"), step("B")
        self.save([a, b])
        original = self.path.read_bytes()
        original_revision = self.tasks.revision()["revision"]
        for deps_a, deps_b in [([a["id"]], []), ([b["id"]], [a["id"]]),
                                (["missing"], []), ([b["id"], b["id"]], [])]:
            candidate = copy.deepcopy(self.rows())
            candidate[0]["depends_on"], candidate[1]["depends_on"] = deps_a, deps_b
            with self.subTest(deps_a=deps_a), self.assertRaises(ValueError):
                self.save(candidate)
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(self.tasks.revision()["revision"], original_revision)
        external = make_task("另一个父项", "learn")
        self.tasks.add(external)
        foreign = make_task("跨层前置", "learn")
        foreign.update(parent_id=external["id"], depends_on=[a["id"]])
        with self.assertRaisesRegex(ValueError, "同一父任务"):
            self.tasks.add(foreign)
        self.assertIsNone(self.tasks.get(foreign["id"]))

    def test_write_failure_rolls_back_entire_plan_and_revision(self):
        self.save([step("原有步骤")])
        original = self.tasks.tasks
        disk = self.path.read_bytes()
        revision = self.tasks.revision()["revision"]
        candidate = self.rows() + [step("新增", [self.rows()[0]["id"]])]
        with patch.object(JsonTaskRepository, "save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.save(candidate)
        self.assertEqual(self.tasks.tasks, original)
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertEqual(self.tasks.revision()["revision"], revision)

    def test_stale_plan_does_not_overwrite_newer_changes_but_status_updates_are_preserved(self):
        self.save([step("原名")])
        baseline = self.rows()
        self.tasks.update(baseline[0]["id"], title="外部新名")
        with self.assertRaisesRegex(ValueError, "重新打开"):
            self.save(baseline + [step("草稿")], baseline)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["title"], "外部新名")
        baseline = self.rows()
        self.tasks.set_status(baseline[0]["id"], "done")
        self.save(baseline + [step("下一步")], baseline)
        self.assertEqual(self.tasks.get(baseline[0]["id"])["status"], "done")

    def test_delete_predecessor_requires_explicit_dependency_removal(self):
        a = step("前置")
        b = step("后续", [a["id"]])
        self.save([a, b])
        with self.assertRaisesRegex(ValueError, "解除依赖"):
            self.tasks.delete(a["id"])
        rows = self.rows()
        rows[1]["depends_on"] = []
        self.save(rows)
        self.tasks.delete(a["id"])
        self.assertIsNone(self.tasks.get(a["id"]))
        self.assertIsNotNone(self.tasks.get(b["id"]))

    def test_existing_task_cannot_disappear_or_be_stolen_via_plan(self):
        a = step("现有")
        self.save([a])
        with self.assertRaisesRegex(ValueError, "不会删除"):
            self.save([])
        foreign = make_task("其他任务", "learn")
        self.tasks.add(foreign)
        with self.assertRaisesRegex(ValueError, "已用于其他任务"):
            self.save(self.rows() + [{"id": foreign["id"], "title": "占用", "depends_on": []}])
        self.assertEqual(self.tasks.get(foreign["id"])["title"], "其他任务")

    def test_native_add_parallel_follow_join_and_save(self):
        dialog = self.open_dialog()
        dialog.quick_input.setPlainText("调研\n草图")
        dialog.add_parallel()
        a, b = dialog.rows
        dialog.quick_input.setPlainText("实现")
        dialog.add_following()
        c = dialog.rows[-1]
        self.assertEqual(c["depends_on"], [b["id"]])
        for index in range(dialog.dependencies.count()):
            item = dialog.dependencies.item(index)
            if item.data(Qt.UserRole) == a["id"]:
                item.setCheckState(Qt.Checked)
                break
        self.assertEqual(set(c["depends_on"]), {a["id"], b["id"]})
        self.assertGreater(dialog.graph.positions[c["id"]][1], dialog.graph.positions[a["id"]][1])
        # Exercise actual native scene hit testing and graph-to-editor selection.
        node = dialog.graph.nodes[a["id"]]
        point = dialog.graph.view.mapFromScene(node.sceneBoundingRect().center())
        QTest.mouseClick(dialog.graph.view.viewport(), Qt.LeftButton, pos=point)
        self.assertEqual(dialog.current_id, a["id"])
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Accepted)
        self.assertEqual(len(self.rows()), 3)
        self.window.refresh()
        self.assertTrue(self.window.findChildren(TaskDag))

    def test_native_cycle_is_rejected_without_losing_draft_and_cancel_is_read_only(self):
        dialog = self.open_dialog()
        dialog.quick_input.setPlainText("起点")
        dialog.add_parallel()
        a = dialog.rows[0]
        dialog.quick_input.setPlainText("后续")
        dialog.add_following()
        b = dialog.rows[1]
        dialog.select_id(a["id"])
        dialog.dependencies.item(0).setCheckState(Qt.Checked)
        self.assertIn("循环", dialog.error.text())
        self.assertEqual(a["depends_on"], [])
        self.assertEqual(b["depends_on"], [a["id"]])
        self.assertEqual(dialog.dependencies.item(0).checkState(), Qt.Unchecked)
        dialog.reject()
        self.assertEqual(self.rows(), [])

    def test_unadded_text_and_empty_name_stay_in_dialog(self):
        dialog = self.open_dialog()
        dialog.quick_input.setPlainText("尚未加入")
        dialog.accept()
        self.assertIn("未加入", dialog.error.text())
        self.assertEqual(self.rows(), [])
        dialog.add_parallel()
        dialog.rename("")
        dialog.accept()
        self.assertIn("名称", dialog.error.text())
        self.assertEqual(self.rows(), [])

    def test_graph_virtual_nodes_cannot_collide_with_saved_task_ids(self):
        row = {"id": "__momo_flow_start__", "title": "旧数据允许的 ID", "depends_on": []}
        self.save([row])
        dialog = self.open_dialog()
        self.assertIn(row["id"], dialog.graph.nodes)
        dialog.select_id(row["id"])
        self.assertEqual(dialog.current_id, row["id"])

    def test_local_work_plan_preserves_issue_policy_and_sample_guard(self):
        work = make_task("工作规划")
        work.update(is_sample=True, linear_target={"team_id": "selected-team"})
        self.tasks.add(work)
        rows = [step("方向一"), step("方向二")]
        self.tasks.apply_plan(work["id"], rows, [])
        for row in rows:
            child = self.tasks.get(row["id"])
            self.assertEqual(child["category"], "work")
            self.assertEqual(child["parent_id"], work["id"])
            self.assertTrue(child["is_sample"])
            self.assertEqual(child["bindings"], [])
        self.assertEqual(self.tasks.get(work["id"])["linear_target"], {"team_id": "selected-team"})


if __name__ == "__main__":
    unittest.main()
