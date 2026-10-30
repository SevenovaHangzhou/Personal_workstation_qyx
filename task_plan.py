"""Explicit dependencies belong to sibling steps, separate from containment."""
from dag_layout import layers_for


def plan_rows(tasks, parent_id):
    return [{"id": t["id"], "title": t["title"], "depends_on": list(t.get("depends_on", []))}
            for t in tasks if t.get("parent_id") == parent_id]


def validate_dependencies(tasks):
    by_id = {t["id"]: t for t in tasks}
    edges = []
    for task in tasks:
        deps = task.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) or not d for d in deps):
            raise ValueError("前置步骤格式无效")
        if len(deps) != len(set(deps)):
            raise ValueError("前置步骤不能重复")
        for source in deps:
            if source not in by_id:
                raise ValueError("前置步骤不存在，请先调整依赖后再删除")
            if (by_id[source].get("parent_id") != task.get("parent_id")
                    or by_id[source].get("category") != task.get("category")):
                raise ValueError("前置步骤需属于同一父任务；父子关系不代表先后顺序")
            edges.append((source, task["id"]))
    layers_for(by_id, edges)


def validate_plan(rows):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("一个规划最多包含 1000 个步骤")
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"id", "title", "depends_on"}
                or not isinstance(row["id"], str) or not row["id"]
                or not isinstance(row["title"], str) or not 1 <= len(row["title"].strip()) <= 100):
            raise ValueError("每个步骤需要 1–100 字的名称")
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("步骤 ID 重复")
    validate_dependencies([{**r, "parent_id": "__plan__"} for r in rows])
