"""Domain helpers and JSON repository, owned exclusively by TaskService."""
import copy
import json
import os
import re
import uuid
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlparse
from git_support import validate_repository
from task_plan import validate_dependencies, validate_plan, plan_rows

CATEGORIES = {"work": "工作任务", "learn": "学习任务", "demo": "有趣 Demo", "life": "日常任务"}
STATUSES = {"todo": "待开始", "doing": "进行中", "waiting": "等我处理", "done": "已完成"}


def has_linear_context(task):
    return bool(task.get("linear_issue") or any(b.get("kind") == "linear" for b in task.get("bindings", [])))


def has_work_issue(task):
    return bool(task.get("linear_issue") or any(b.get("kind") == "linear" and not b.get("demo") for b in task.get("bindings", [])))


def parse_binding(kind, raw):
    raw = raw.strip()
    parsed = urlparse(raw)
    if kind == "codex":
        match = re.fullmatch(r"/([A-Za-z0-9_-]+)", parsed.path)
        if parsed.scheme != "codex" or parsed.netloc != "threads" or not match or parsed.query or parsed.fragment:
            raise ValueError("请粘贴 Codex 聊天链接，格式为 codex://threads/聊天ID")
        return {"kind": kind, "url": raw, "label": "Codex 对话 · " + match.group(1)[:8], "demo": False}
    if kind == "linear":
        match = re.match(r"/[^/]+/issue/([A-Za-z][A-Za-z0-9]*-\d+)(?:/|$)", parsed.path)
        if parsed.scheme != "https" or parsed.netloc != "linear.app" or not match or parsed.query or parsed.fragment:
            raise ValueError("请粘贴 Linear issue 链接，例如 https://linear.app/team/issue/APP-123/title")
        return {"kind": kind, "url": raw, "label": match.group(1).upper(), "demo": False}
    raise ValueError("不支持的关联类型")


def make_task(title, category="work", day=None, next_step=""):
    return {"id": str(uuid.uuid4()), "title": title.strip(), "category": category,
            "status": "todo", "day": day or date.today().isoformat(), "next_step": next_step,
            "parent_id": None, "depends_on": [], "created_at": datetime.now(timezone.utc).isoformat(), "linear_target": {},
            "description": "", "steps": [], "bindings": [], "repositories": [], "note": "", "focus_seconds": 0}


def sample_tasks():
    def sample(title, category, status, desc, next_step, steps, links=()):
        task = make_task(title, category, next_step=next_step)
        task.update(status=status, description=desc, is_sample=True,
                    steps=[{"title": name, "done": done} for name, done in steps],
                    bindings=list(links))
        return task
    codex = {"kind": "codex", "label": "操作台交互打磨", "url": "", "demo": True}
    issue = {"kind": "linear", "label": "ARM-128", "url": "", "demo": True}
    return [
        sample("打磨机械臂操作台交互", "work", "doing", "让每一次操作，都有清晰、温柔的回应。", "完成任务卡片的状态反馈", [("梳理交互状态", True), ("确定间距与圆角", True), ("补齐加载与完成反馈", False), ("检查键盘操作", False)], [codex, issue]),
        sample("理解 Transformer 的注意力机制", "learn", "todo", "从一个小例子开始，理解 Q、K、V。", "读完图解的第一节，画一张笔记", [("读图解", False), ("写一个最小示例", False)], [{"kind": "codex", "label": "一起读懂 Attention", "url": "", "demo": True}]),
        sample("做一只会呼吸的桌面小水母", "demo", "todo", "一个没有截止日期的小实验。", "先画一个透明的小水母", [("画出小水母", False), ("加上呼吸动画", False)]),
        sample("确认 API 重试策略", "work", "waiting", "实现已完成，留一点时间检查边界情况。", "检查网络断开后是否正确恢复", [("实现退避重试", True), ("人工验收边界情况", False)], [{"kind": "codex", "label": "API 重试与错误恢复", "url": "", "demo": True}, {"kind": "linear", "label": "ARM-132", "url": "", "demo": True}]),
        sample("散步，顺便买一束花", "life", "todo", "给今天留一点与屏幕无关的时间。", "穿鞋出门就好", []),
        sample("整理本周的灵感碎片", "life", "done", "把散落的想法放回同一个地方。", "", [("收集便签", True), ("归档到笔记", True)]),
        sample("读完 Python 装饰器笔记", "learn", "done", "今天也弄懂了一小点。", "", [("读笔记", True), ("运行示例", True)])]


def validate_task(task):
    required = {"id", "title", "category", "status", "day", "steps", "bindings", "note", "next_step", "description", "focus_seconds"}
    if not isinstance(task, dict) or not required.issubset(task):
        raise ValueError("任务数据不完整")
    if any(not isinstance(task[key], str) for key in ["id", "title", "category", "status", "day", "note", "next_step", "description"]):
        raise ValueError("任务文字格式有误")
    if not task["id"] or not task["title"].strip() or len(task["title"]) > 100 or task["category"] not in CATEGORIES or task["status"] not in STATUSES:
        raise ValueError("任务名称、分类或状态无效")
    date.fromisoformat(task["day"])
    if not isinstance(task["steps"], list) or not isinstance(task["bindings"], list):
        raise ValueError("任务列表格式有误")
    for step in task["steps"]:
        if not isinstance(step, dict) or not isinstance(step.get("title"), str) or not step["title"].strip() or not isinstance(step.get("done"), bool):
            raise ValueError("子任务格式有误")
    for binding in task["bindings"]:
        if not isinstance(binding, dict) or binding.get("kind") not in ("codex", "linear") or not isinstance(binding.get("label"), str) or not isinstance(binding.get("url"), str):
            raise ValueError("关联格式有误")
        if not binding.get("demo"):
            parse_binding(binding["kind"], binding["url"])
    if type(task["focus_seconds"]) is not int or task["focus_seconds"] < 0:
        raise ValueError("专注时长格式有误")
    repos = task.get("repositories", [])
    if not isinstance(repos, list):
        raise ValueError("仓库列表格式无效")
    for repo in repos:
        validate_repository(repo)
    if repos and task["category"] != "work":
        raise ValueError("关联了 Git 仓库的任务请保留在工作分类，或先移除仓库关联")
    created = task.get("created_at", "")
    if not isinstance(created, str) or created and datetime.fromisoformat(created).tzinfo is None:
        raise ValueError("任务创建时间必须带时区")
    if type(task.get("is_sample",False)) is not bool:
        raise ValueError("无效示例标志")
    parent = task.get("parent_id")
    if parent is not None and (not isinstance(parent, str) or not parent or parent == task["id"]):
        raise ValueError("无效父任务")
    deps = task.get("depends_on", [])
    if not isinstance(deps, list) or any(not isinstance(d, str) or not d for d in deps):
        raise ValueError("前置步骤格式无效")
    target = task.get("linear_target", {})
    if not isinstance(target, dict) or set(target) - {"team_id", "project_id"} or any(not isinstance(v, str) for v in target.values()):
        raise ValueError("Linear 归属格式无效")
    if "linear_parent_pending" in task:
        pending = task['linear_parent_pending']
        if (not isinstance(pending, dict) or set(pending) != {'parent_id','revision','due_at'}
                or pending['parent_id'] != task.get('parent_id') or not isinstance(pending['revision'],str) or not pending['revision']):
            raise ValueError("无效的待同步任务层级")
        if not isinstance(pending["due_at"],str) or datetime.fromisoformat(pending["due_at"]).tzinfo is None:
            raise ValueError("待同步时间必须带时区")
    if "linear_issue" in task:
        from linear_support import validate_issue
        validate_issue(task["linear_issue"])


class JsonTaskRepository:
    def __init__(self, path):
        self.path = Path(path)
        self.sample = True
        self.settings = {"focus_minutes": 25}
        self.tasks = []
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or payload.get("version") not in (1, 2) or not isinstance(payload.get("tasks"), list):
                    raise ValueError("不支持的数据格式")
                seen = set()
                for task in payload["tasks"]:
                    validate_task(task)
                    if task["id"] in seen:
                        raise ValueError("任务数据无效")
                    seen.add(task["id"])
                self.tasks = payload["tasks"]
                self.sample = bool(payload.get("sample", False))
                settings = payload.get("settings", {})
                if not isinstance(settings, dict):
                    raise ValueError("设置格式有误")
                minutes = settings.get("focus_minutes", 25)
                if type(minutes) is not int or minutes not in [15, 25, 45, 60]:
                    raise ValueError("专注时间设置有误")
                self.settings["focus_minutes"] = minutes
            except (ValueError, TypeError, KeyError, OSError) as exc:
                raise ValueError("无法读取本地数据；原文件已保留。" + str(exc)) from exc
        else:
            self.tasks = sample_tasks()
        if not self.path.exists() or payload.get("version") == 1:
            if self.path.exists():
                # Never overwrite the first migration backup.
                backup = self.path.with_name(self.path.name + ".v1-backup")
                if not backup.exists():
                    with backup.open("xb") as stream:
                        stream.write(self.path.read_bytes())
            self._migrate_children()
            if self.path.exists():
                self.save()
        self._project_children()
        self._validate_tree()

    def _migrate_children(self):
        children = []
        for task in self.tasks:
            task.setdefault("parent_id", None)
            task.setdefault("created_at", "")
            task.setdefault("linear_target", {})
            task.setdefault("is_sample", bool(self.sample and any(b.get("demo") for b in task["bindings"]) and not any(not b.get("demo") for b in task["bindings"])))
            steps = list(task["steps"])
            title = task.get("next_step", "").strip()
            if title and not any(step["title"].strip() == title for step in steps):
                steps.insert(0, {"title": title, "done": False})
            for step in steps:
                child = make_task(step["title"][:100], task["category"], task["day"])
                child.update(parent_id=task["id"], status="done" if step["done"] else "todo", is_sample=task.get("is_sample",False))
                # Retain long legacy text without truncating its content.
                if len(step["title"]) > 100:
                    child["description"] = step["title"]
                children.append(child)
            task["next_step"] = ""
            task["steps"] = []
        self.tasks.extend(children)
        self._project_children()

    def _project_children(self):
        children = {}
        for child in self.tasks:
            if child.get("parent_id"):
                children.setdefault(child["parent_id"], []).append({"id":child["id"], "title":child["title"], "done":child["status"]=="done"})
        for task in self.tasks:
            task["steps"] = children.get(task["id"], [])

    def _validate_tree(self):
        tasks = {t["id"]: t for t in self.tasks}
        if len(tasks) != len(self.tasks):
            raise ValueError("任务 ID 重复")
        for task in self.tasks:
            parent = task.get("parent_id")
            seen = {task["id"]}
            while parent:
                if parent not in tasks or parent in seen:
                    raise ValueError("子任务父级不存在或存在循环，原始数据已保留")
                seen.add(parent)
                if tasks[parent]["category"] != task["category"]:
                    raise ValueError("子任务分类必须与父任务一致")
                parent = tasks[parent].get("parent_id")
        validate_dependencies(self.tasks)

    def check_issue_mapping(self, task):
        real = [b for b in task["bindings"] if b["kind"] == "linear" and not b.get("demo")]
        if len(real) > 1 or len(issue_keys(task)) > 1:
            raise ValueError("工作任务与 issue 必须一一对应，请先移除多余关联")
        keys = issue_keys(task)
        for other in self.tasks:
            if other["id"] != task["id"] and keys & issue_keys(other):
                raise ValueError("这个 issue 已对应另一项工作任务，请在原任务中创建子任务")

    def save(self):
        self._project_children()
        self._validate_tree()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as stream:
            json.dump({"version":2, "sample":self.sample, "settings":self.settings, "tasks":self.tasks}, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, self.path)

    def get(self, task_id):
        return next((task for task in self.tasks if task["id"] == task_id), None)

    def replace_tasks(self, tasks):
        for task in tasks:
            validate_task(task)
        previous = self.tasks
        self.tasks = tasks
        try:
            self.save()
        except (OSError, ValueError):
            self.tasks = previous
            raise

    def delete(self, task_id):
        if not self.get(task_id):
            raise ValueError("没有找到这个任务")
        if any(t.get("parent_id") == task_id for t in self.tasks):
            raise ValueError("请先删除子任务；删除父任务不会自动删除子任务或远端 issue")
        if any(task_id in t.get("depends_on", []) for t in self.tasks):
            raise ValueError("其他步骤仍以此为前置，请在「规划下一步」中解除依赖后再删除")
        self.replace_tasks([task for task in self.tasks if task["id"] != task_id])

    def reorder(self, task_ids):
        """Reorder a visible subset in its existing slots; keep hidden tasks in place."""
        if (not isinstance(task_ids, list) or not task_ids
                or any(not isinstance(task_id, str) or not task_id for task_id in task_ids)
                or len(set(task_ids)) != len(task_ids)):
            raise ValueError("排序需要一组不重复的任务 ID")
        by_id = {task["id"]: task for task in self.tasks}
        if any(task_id not in by_id for task_id in task_ids):
            raise ValueError("任务列表已变化，请刷新后重新排序")
        scope = set(task_ids)
        if [task["id"] for task in self.tasks if task["id"] in scope] == task_ids:
            return False
        ordered = iter(by_id[task_id] for task_id in task_ids)
        tasks = [next(ordered) if task["id"] in scope else task for task in self.tasks]
        self.replace_tasks(copy.deepcopy(tasks))
        return True

    def check_merge(self, task_id, parent_id, mode="child"):
        if not isinstance(task_id, str) or not isinstance(parent_id, str) or mode not in ("child", "after"):
            raise ValueError("无效的任务合入请求")
        task = self.get(task_id); target = self.get(parent_id)
        if task is None or target is None:
            raise ValueError("任务列表已变化，请重新选择")
        if task_id == parent_id:
            raise ValueError("任务不能合入自身")
        if task["category"] != target["category"]:
            raise ValueError("步骤须与目标任务属于同一分类，请先明确调整分类")
        ancestor = target
        while ancestor:
            if ancestor["id"] == task_id:
                raise ValueError("不能合入自己的后代任务，以免形成循环")
            ancestor = self.get(ancestor.get("parent_id"))
        new_parent = target["id"] if mode == "child" else target.get("parent_id")
        if mode == "child" and task.get("parent_id") == new_parent:
            raise ValueError("这件任务已经是目标的子步骤")
        if mode == "after" and parent_id in task.get("depends_on", []) and task.get("parent_id") == new_parent:
            raise ValueError("这件任务已经接在目标之后")
        if task.get("parent_id") != new_parent and (task.get("depends_on") or any(task_id in t.get("depends_on", []) for t in self.tasks)):
            raise ValueError("移动层级会影响已有前置或后续步骤，请先调整依赖后再合入")
        candidates = copy.deepcopy(self.tasks)
        candidate = next(t for t in candidates if t["id"] == task_id)
        candidate["parent_id"] = new_parent
        if mode == "after":
            candidate["depends_on"] = list(dict.fromkeys(candidate.get("depends_on", []) + [parent_id]))
        validate_dependencies(candidates)
        return task, target

    def merge(self, task_id, parent_id, mode="child"):
        self.check_merge(task_id, parent_id, mode)
        tasks = copy.deepcopy(self.tasks)
        by_id = {task["id"]: task for task in tasks}
        source, target = by_id[task_id], by_id[parent_id]
        new_parent = parent_id if mode == "child" else target.get("parent_id")
        if source.get("parent_id") != new_parent and source["category"] == "work":
            now = datetime.now(ZoneInfo('Asia/Shanghai'))
            due = now.replace(hour=22,minute=0,second=0,microsecond=0)
            if now >= due: due += timedelta(days=1)
            source["linear_parent_pending"] = {"parent_id":new_parent, "revision":str(uuid.uuid4()), "due_at":due.isoformat()}
        source["parent_id"] = new_parent
        if mode == "after":
            source["depends_on"] = list(dict.fromkeys(source.get("depends_on", []) + [parent_id]))
        # Move the whole existing subtree without recreating any task.
        moved = {task_id}
        while True:
            expanded = moved | {task["id"] for task in tasks if task.get("parent_id") in moved}
            if expanded == moved: break
            moved = expanded
        remaining = [task for task in tasks if task["id"] not in moved]
        target_tree = {parent_id}
        while True:
            expanded = target_tree | {task["id"] for task in remaining if task.get("parent_id") in target_tree}
            if expanded == target_tree: break
            target_tree = expanded
        position = max(i for i, task in enumerate(remaining) if task["id"] in target_tree) + 1
        remaining[position:position] = [task for task in tasks if task["id"] in moved]
        self.replace_tasks(remaining)

    def check_detach(self, task_id):
        task = self.get(task_id)
        if not task or not task.get('parent_id'):
            raise ValueError('请选择一个子步骤拖出；这件任务可能已独立或已删除')
        if task.get('depends_on') or any(task_id in t.get('depends_on', []) for t in self.tasks):
            raise ValueError('移出会影响已有前置或后续步骤，请先调整依赖后再移出')
        return task

    def detach(self, task_id):
        self.check_detach(task_id)
        tasks = copy.deepcopy(self.tasks)
        source = next(t for t in tasks if t['id'] == task_id)
        # Keep inherited issue destination when a pending work item becomes a root.
        if source['category'] == 'work':
            ancestor = self.get(source['parent_id'])
            while ancestor:
                issue = ancestor.get('linear_issue')
                target = ({'team_id':issue['team_id'], 'project_id':issue.get('project_id','')}
                          if issue else ancestor.get('linear_target'))
                if target:
                    source['linear_target'] = copy.deepcopy(target); break
                ancestor = self.get(ancestor.get('parent_id'))
            now = datetime.now(ZoneInfo('Asia/Shanghai'))
            due = now.replace(hour=22, minute=0, second=0, microsecond=0)
            if now >= due: due += timedelta(days=1)
            source['linear_parent_pending'] = dict(parent_id=None, revision=str(uuid.uuid4()), due_at=due.isoformat())
        source['parent_id'] = None
        self.replace_tasks(tasks)

    def acknowledge_parent(self, task_id, expected):
        task = self.get(task_id)
        if not isinstance(expected,dict) or not expected or not task or task.get("linear_parent_pending") != expected:
            return False
        candidate = copy.deepcopy(task)
        candidate.pop("linear_parent_pending", None)
        self.replace_tasks([candidate if t["id"] == task_id else t for t in self.tasks])
        return True

    def set_focus_minutes(self, minutes):
        if type(minutes) is not int or minutes not in [15, 25, 45, 60]:
            raise ValueError("请选择有效的专注时间")
        previous = self.settings["focus_minutes"]
        self.settings["focus_minutes"] = minutes
        try:
            self.save()
        except OSError:
            self.settings["focus_minutes"] = previous
            raise

    def update(self, task_id, **changes):
        task = self.get(task_id)
        if not task:
            raise ValueError("没有找到这个任务")
        previous = copy.deepcopy(task)
        issue_url = changes.pop("issue_url", None)
        detach = changes.pop("detach_linear", False)
        if type(detach) is not bool:
            raise ValueError("解除关联标志必须为布尔值")
        candidate = copy.deepcopy({**task, **changes})
        if detach:
            candidate["bindings"] = [b for b in candidate["bindings"] if b["kind"] != "linear"]
            candidate.pop("linear_issue", None)
        if issue_url is not None:
            if not isinstance(issue_url, str):
                raise ValueError("issue 地址必须是文本")
            if candidate["category"] != "work":
                raise ValueError("只有工作任务可以关联 Linear issue")
            binding = parse_binding("linear", issue_url)
            linear = next((i for i, b in enumerate(candidate["bindings"]) if b["kind"] == "linear"), None)
            if linear is None:
                candidate["bindings"].append(binding)
            else:
                old = candidate["bindings"][linear]
                candidate["bindings"][linear] = binding
                if old["url"] != binding["url"]:
                    candidate.pop("linear_issue", None)
        active_keys = issue_keys({"bindings":candidate["bindings"]})
        metadata_keys = issue_keys({"bindings":[], "linear_issue":candidate["linear_issue"]}) if candidate.get("linear_issue") else set()
        if not active_keys or metadata_keys and not active_keys & metadata_keys:
            old_issue = candidate.pop("linear_issue", None)
            if old_issue:
                candidate["unlinked_issue"] = old_issue
        if candidate["category"] != "work":
            candidate.pop("linear_parent_pending", None)
            if candidate["category"] != task["category"] and has_linear_context(candidate):
                raise ValueError("请先解除 Linear issue 关联，再移出工作分类")
            old_links = {b["url"] for b in task["bindings"] if b["kind"] == "linear"}
            new_links = {b["url"] for b in candidate["bindings"] if b["kind"] == "linear"}
            if new_links - old_links or ("linear_issue" in changes and changes["linear_issue"] != task.get("linear_issue")):
                raise ValueError("只有工作任务可以关联 Linear issue")
        if candidate["category"] != task["category"] and (task.get("parent_id") or any(t.get("parent_id") == task_id for t in self.tasks)):
            raise ValueError("含父子关系的任务需保持相同分类")
        if issue_keys(candidate) - issue_keys(task) or candidate.get("linear_issue") != task.get("linear_issue") and candidate.get("linear_issue"):
            self.check_issue_mapping(candidate)
        validate_task(candidate)
        task.clear(); task.update(candidate)
        try:
            self.save()
        except (OSError, ValueError):
            task.clear()
            task.update(previous)
            self._project_children()
            raise

    def add(self, task):
        validate_task(task)
        if task["category"] != "work" and has_linear_context(task):
            raise ValueError("只有工作任务可以关联 Linear issue")
        self.check_issue_mapping(task)
        if self.get(task["id"]):
            raise ValueError("任务 ID 已存在")
        before = copy.deepcopy(self.tasks)
        initial = list(task["steps"])
        if task.get("next_step", "").strip():
            initial.insert(0, {"title": task["next_step"].strip(), "done": False})
        if len(initial) > 100 or any(not 1 <= len(step["title"].strip()) <= 100 for step in initial):
            raise ValueError("一次最多创建 100 个子任务，每项 1–100 字")
        task["steps"] = []; task["next_step"] = ""
        self.tasks.insert(0, task)
        for step in initial:
            child = make_task(step["title"][:100], task["category"], task["day"])
            child.update(parent_id=task["id"], status="done" if step["done"] else "todo", is_sample=task.get("is_sample",False))
            self.tasks.append(child)
        try:
            self.save()
        except (OSError, ValueError):
            self.tasks = before
            raise

    def add_children(self, task_id, titles):
        parent = self.get(task_id)
        if not parent:
            raise ValueError("没有找到父任务")
        if not isinstance(titles, list) or not 1 <= len(titles) <= 100 or any(not isinstance(t, str) or not 1 <= len(t.strip()) <= 100 for t in titles):
            raise ValueError("每行一项子任务，每项 1–100 字，一次最多 100 项")
        tasks = copy.deepcopy(self.tasks)
        children = []
        for title in titles:
            child = make_task(title, parent["category"], parent["day"])
            child["parent_id"] = task_id
            child["is_sample"] = parent.get("is_sample",False)
            tasks.append(child); children.append(child)
        self.replace_tasks(tasks)
        return children

    def apply_plan(self, task_id, rows, baseline):
        parent = self.get(task_id)
        if not parent:
            raise ValueError("没有找到父任务")
        if baseline != plan_rows(self.tasks, task_id):
            raise ValueError("步骤已在其他位置修改，请重新打开规划后再保存；本次输入仍保留")
        validate_plan(rows)
        existing = {r["id"] for r in baseline}
        if not existing.issubset({r["id"] for r in rows}):
            raise ValueError("请从任务详情删除已有步骤，规划窗口不会删除任务")
        all_ids = {t["id"] for t in self.tasks}
        if len([r for r in rows if r["id"] not in existing]) > 100:
            raise ValueError("一次最多新增 100 个步骤")
        tasks = copy.deepcopy(self.tasks)
        by_id = {t["id"]: t for t in tasks}
        for row in rows:
            if row["id"] not in existing:
                if row["id"] in all_ids:
                    raise ValueError("步骤 ID 已用于其他任务，请重新添加")
                child = make_task(row["title"], parent["category"], parent["day"])
                child.update(id=row["id"], parent_id=task_id, is_sample=parent.get("is_sample", False))
                tasks.append(child)
                by_id[row["id"]] = child
            by_id[row["id"]].update(title=row["title"].strip(), depends_on=list(row["depends_on"]))
        self.replace_tasks(tasks)
        return plan_rows(self.tasks, task_id)

    def bind(self, task_id, kind, raw):
        binding = parse_binding(kind, raw)
        task = self.get(task_id)
        if not task:
            raise ValueError("没有找到这个任务")
        if kind == "linear" and task["category"] != "work":
            raise ValueError("只有工作任务可以关联 Linear issue")
        if any(item["url"] == binding["url"] for item in task["bindings"]):
            raise ValueError("这条链接已经关联过了")
        if kind == "linear":
            self.check_issue_mapping({**task,"bindings":task["bindings"]+[binding]})
        self.update(task_id, bindings=task["bindings"] + [binding])
        return binding

    def detach_linear(self, task_id):
        task = self.get(task_id)
        if not task:
            raise ValueError("没有找到这个任务")
        replacement = copy.deepcopy(task)
        replacement["bindings"] = [b for b in replacement["bindings"] if b["kind"] != "linear"]
        replacement.pop("linear_issue", None)
        self.replace_tasks([replacement if t["id"] == task_id else t for t in self.tasks])

    def remove_binding(self, task_id, index):
        task = self.get(task_id)
        bindings = list(task["bindings"])
        bindings.pop(index)
        self.update(task_id, bindings=bindings)

    def clear_samples(self):
        previous, old_sample = self.tasks, self.sample
        self.tasks, self.sample = [], False
        try:
            self.save()
        except OSError:
            self.tasks, self.sample = previous, old_sample
            raise


def issue_keys(task):
    keys = set()
    bindings = list(task.get("bindings", []))
    if task.get("linear_issue"):
        bindings.append({"kind":"linear", "url":task["linear_issue"]["url"]})
    for binding in bindings:
        if binding["kind"] == "linear" and not binding.get("demo"):
            parsed = urlparse(binding["url"])
            keys.add((parsed.path.split("/")[1], parse_binding("linear", binding["url"])["label"]))
    return keys


def progress(task):
    if task["status"] == "done":
        return 100
    return round(100 * sum(bool(s["done"]) for s in task["steps"]) / len(task["steps"])) if task["steps"] else 0
