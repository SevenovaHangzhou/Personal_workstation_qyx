import copy
from urllib.parse import urlparse
from framework import api
from model import JsonTaskRepository, STATUSES, validate_task, parse_binding, make_task
from msgs.YomkMsgs import (TaskId, CreateTask, UpdateTask, TaskStatus, StepInput,
                          StepState, BindingIndex, AppendBinding, FocusCredit, FocusMinutes)
from msgs.YomkMsgs import RepositoryInput, RepositoryIndex, ImportIssues, ChildTasks, TaskIssue, ApplyTaskPlan, TaskOrder, MergeTask
from git_support import validate_repository
from typedefine.TypeDefine import CONTEXT_GROUP, CTX_DATA_PATH, CTX_REVISION
from services.base import MomoService, ok, require


class TaskService(MomoService):
    """Single owner of task data; its lock serializes all reads and writes."""
    def __init__(self, server):
        super().__init__(server)
        self.set_name("/TaskService")
        self.repository = None
        self.revision = 0

    def init(self):
        path = api.context_get(CTX_DATA_PATH, None, CONTEXT_GROUP)
        require(isinstance(path, str), "missing task data path")
        self.repository = JsonTaskRepository(path)
        handlers = [
            ("revision", self.get_revision, "None", "轻量查询任务变更版本和未完成数量"),
            ("snapshot", self.snapshot, "None", "返回任务、设置与数据版本快照"),
            ("list", self.list_tasks, "None", "返回任务列表副本"),
            ("get", self.get_task, "TaskId", "读取单个任务副本"),
            ("create", self.create, "CreateTask", "创建并保存任务"),
            ("delete", self.delete, "TaskId", "删除本地任务；外部请用 FocusService/delete_task 协调计时"),
            ("reorder", self.reorder, "TaskOrder", "保存可见任务顺序，保持其余任务位置和步骤依赖"),
            ("check_merge", self.check_merge, "MergeTask", "检查合入子步骤的分类、父子循环与依赖"),
            ("acknowledge_parent", self.acknowledge_parent, "dict", "确认指定版本的远端父级同步"),
            ("dependencies", self.dependencies, "dict", "原子调整同层前置步骤"),
            ("merge", self.merge, "MergeTask", "将已有任务和子树原子合入目标任务"),
            ("check_detach", self.check_detach, "TaskId", "检查子步骤能否独立，保留依赖"),
            ("detach", self.detach, "TaskId", "将子步骤和子树原子移为独立任务"),
            ('undo_state', self.undo_state, 'None', '读取最近一次结构调整的安全撤销状态'),
            ('undo_structure', self.undo_structure, 'dict', '核对记录后只撤销结构，不覆盖任务内容'),
            ("update", self.update, "UpdateTask", "更新任务文字、分类与计划日期"),
            ("set_status", self.set_status, "TaskStatus", "更新任务状态"),
            ("add_children", self.add_children, "ChildTasks", "批量添加独立子任务，保留兼容端点"),
            ("apply_plan", self.apply_plan, "ApplyTaskPlan", "原子保存下一步规划和显式前置依赖"),
            ("attach_issue", self.attach_issue, "TaskIssue", "将已创建的唯一 issue 绑定回原任务"),
            ("add_step", self.add_step, "StepInput", "添加一个子步骤"),
            ("set_step", self.set_step, "StepState", "更新子步骤状态"),
            ("append_binding", self.append_binding, "AppendBinding", "原子添加已校验的关联"),
            ("remove_binding", self.remove_binding, "BindingIndex", "移除关联"),
            ("detach_linear", self.detach_linear, "TaskId", "解除全部本地 Linear 关联与导入元数据，不修改远端 issue"),
            ("credit_focus", self.credit_focus, "FocusCredit", "持久化专注秒数"),
            ("set_focus_minutes", self.set_focus_minutes, "FocusMinutes", "保存专注时长偏好"),
            ("clear", self.clear, "None", "清空当前全部任务"),
            ("append_repository", self.append_repository, "RepositoryInput", "原子关联工作任务仓库"),
            ("remove_repository", self.remove_repository, "RepositoryIndex", "移除仓库关联"),
            ("import_issues", self.import_issues, "ImportIssues", "原子导入去重的 Linear 工作任务"),
        ]
        for name, handler, message, description in handlers:
            self.install("/" + name, handler, message, description)
        self.alive = True
        return 0

    def changed(self):
        self.revision += 1
        api.context_set(CTX_REVISION, self.revision, CONTEXT_GROUP)

    def task(self, task_id):
        require(isinstance(task_id, str), "task_id must be a string")
        task = self.repository.get(task_id)
        require(task is not None, "没有找到这个任务")
        return task

    def get_revision(self, pkg):
        require(pkg is None, "expected None")
        return ok({"revision":self.revision, "unfinished":sum(t["status"]!="done" for t in self.repository.tasks)})

    def snapshot(self, pkg):
        require(pkg is None, "expected None")
        return ok({"tasks": self.repository.tasks, "settings": self.repository.settings,
                   "sample": self.repository.sample, "path": str(self.repository.path), "revision": self.revision})

    def list_tasks(self, pkg):
        require(pkg is None, "expected None")
        return ok(self.repository.tasks)

    def get_task(self, pkg):
        require(isinstance(pkg, TaskId), "expected TaskId")
        require(isinstance(pkg.task_id, str), "task_id must be a string")
        return ok(self.repository.get(pkg.task_id))

    def create(self, pkg):
        require(isinstance(pkg, CreateTask), "expected CreateTask")
        task = copy.deepcopy(pkg.task)
        validate_task(task)
        self.repository.add(task)
        self.changed()
        return ok(task)

    def delete(self, pkg):
        require(isinstance(pkg, TaskId), "expected TaskId")
        self.task(pkg.task_id)
        self.repository.delete(pkg.task_id)
        self.changed()
        return ok()

    def reorder(self, pkg):
        require(isinstance(pkg, TaskOrder), "expected TaskOrder")
        changed = self.repository.reorder(pkg.task_ids)
        if changed:
            self.changed()
        return ok(changed)

    def check_merge(self, pkg):
        require(isinstance(pkg, MergeTask), "expected MergeTask")
        task, parent = self.repository.check_merge(pkg.task_id, pkg.parent_id, pkg.mode)
        from task_structure import impact
        return ok({"task":task, "parent":parent,'impact':impact(self.repository.tasks,pkg.task_id,pkg.parent_id,pkg.mode)})

    def merge(self, pkg):
        require(isinstance(pkg, MergeTask), "expected MergeTask")
        self.repository.merge(pkg.task_id, pkg.parent_id, pkg.mode)
        self.changed()
        return ok(self.task(pkg.task_id))

    def check_detach(self, pkg):
        require(isinstance(pkg, TaskId), "expected TaskId")
        return ok(self.repository.check_detach(pkg.task_id))

    def detach(self, pkg):
        require(isinstance(pkg, TaskId), "expected TaskId")
        self.repository.detach(pkg.task_id)
        self.changed()
        return ok(self.task(pkg.task_id))

    def dependencies(self, pkg):
        require(isinstance(pkg, dict) and set(pkg) == {"task_id", "depends_on"}, "无效依赖调整")
        self.task(pkg["task_id"])
        self.repository.change_dependencies(pkg["task_id"],pkg['depends_on'])
        self.changed()
        return ok(self.task(pkg["task_id"]))

    def undo_state(self, pkg):
        require(pkg is None,'expected None')
        return ok(self.repository.undo_state())

    def undo_structure(self, pkg):
        require(isinstance(pkg,dict) and set(pkg)=={'id'} and isinstance(pkg['id'],str),'无效撤销请求')
        self.repository.undo_structure(pkg['id']); self.changed()
        return ok()

    def acknowledge_parent(self, pkg):
        require(isinstance(pkg, dict) and set(pkg) == {"task_id", "expected"}, "无效层级确认")
        changed = self.repository.acknowledge_parent(pkg["task_id"], pkg["expected"])
        if changed: self.changed()
        return ok(changed)

    def append_repository(self, pkg):
        require(isinstance(pkg, RepositoryInput), "expected RepositoryInput")
        validate_repository(pkg.repository)
        task = self.task(pkg.task_id)
        require(task["category"] == "work", "只有工作任务可以关联 Git 仓库")
        repos = copy.deepcopy(task.get("repositories", []))
        require(not any(r["kind"] == pkg.repository["kind"] and r["location"] == pkg.repository["location"] for r in repos), "这个仓库已经关联过了")
        repos.append(copy.deepcopy(pkg.repository))
        self.repository.update(pkg.task_id, repositories=repos)
        self.changed()
        return ok(repos[-1])

    def remove_repository(self, pkg):
        require(isinstance(pkg, RepositoryIndex) and type(pkg.index) is int, "expected RepositoryIndex")
        repos = copy.deepcopy(self.task(pkg.task_id).get("repositories", []))
        require(0 <= pkg.index < len(repos), "仓库关联不存在")
        repos.pop(pkg.index)
        self.repository.update(pkg.task_id, repositories=repos)
        self.changed()
        return ok(repos)

    def import_issues(self, pkg):
        from linear_support import validate_issue
        require(isinstance(pkg, ImportIssues) and isinstance(pkg.issues, list), "expected ImportIssues")
        require(len(pkg.issues) <= 10000, "单次导入数量过多")
        for issue in pkg.issues:
            validate_issue(issue)
        tasks = copy.deepcopy(self.repository.tasks)
        result = {"created": 0, "updated": 0, "skipped": 0, "task_ids": []}
        seen = set()
        for issue in pkg.issues:
            if issue["id"] in seen:
                continue
            seen.add(issue["id"])
            binding = parse_binding("linear", issue["url"])
            workspace = urlparse(issue["url"]).path.split("/")[1]
            def same_issue(item):
                return (item["kind"] == "linear" and not item.get("demo")
                        and parse_binding("linear", item["url"])["label"] == binding["label"]
                        and urlparse(item["url"]).path.split("/")[1] == workspace)
            task = next((t for t in tasks if t["category"] == "work" and (
                t.get("linear_issue", {}).get("id") == issue["id"] or
                (not any(b["kind"]=="linear" and not b.get("demo") for b in t["bindings"]) and t.get("unlinked_issue",{}).get("id")==issue["id"]) or
                any(same_issue(b) for b in t["bindings"]))), None)
            if task is None:
                task = make_task(issue["title"][:100], "work", day=issue.get("due_date") or None)
                task["description"] = issue.get("description", "")
                task["status"] = {"started": "doing", "completed": "done", "canceled": "done"}.get(issue["state_type"], "todo")
                task["bindings"] = [binding]
                tasks.insert(0, task)
                result["created"] += 1
            else:
                if task.get("linear_issue") == issue and any(b == binding for b in task["bindings"]):
                    result["skipped"] += 1
                else:
                    result["updated"] += 1
                    task["bindings"] = [b for b in task["bindings"] if not same_issue(b)] + [binding]
            self.repository.check_issue_mapping(task)
            task["linear_issue"] = copy.deepcopy(issue)
            task.pop("unlinked_issue", None)
            result["task_ids"].append(task["id"])
        if result["created"] or result["updated"]:
            self.repository.replace_tasks(tasks)
            self.changed()
        return ok(result)

    def update(self, pkg):
        require(isinstance(pkg, UpdateTask) and isinstance(pkg.changes, dict), "expected UpdateTask")
        require(set(pkg.changes).issubset({"title", "category", "day", "description", "next_step", "note", "issue_url", "detach_linear", "linear_target"}), "请使用对应端点修改状态、步骤、关联或计时")
        self.task(pkg.task_id)
        self.repository.update(pkg.task_id, **copy.deepcopy(pkg.changes))
        self.changed()
        return ok(self.task(pkg.task_id))

    def set_status(self, pkg):
        require(isinstance(pkg, TaskStatus), "expected TaskStatus")
        require(isinstance(pkg.status, str) and pkg.status in STATUSES, "无效的任务状态")
        self.task(pkg.task_id)
        self.repository.update(pkg.task_id, status=pkg.status)
        self.changed()
        return ok(self.task(pkg.task_id))

    def add_step(self, pkg):
        require(isinstance(pkg, StepInput), "expected StepInput")
        require(isinstance(pkg.title, str) and 0 < len(pkg.title.strip()) <= 70, "请填写 1–70 字的子步骤")
        self.repository.add_children(pkg.task_id, [pkg.title])
        self.changed()
        return ok(self.task(pkg.task_id))

    def add_children(self, pkg):
        require(isinstance(pkg, ChildTasks), "expected ChildTasks")
        result = self.repository.add_children(pkg.task_id, pkg.titles)
        self.changed()
        return ok(result)

    def attach_issue(self, pkg):
        from linear_support import validate_issue
        require(isinstance(pkg, TaskIssue), "expected TaskIssue")
        validate_issue(pkg.issue)
        task = self.task(pkg.task_id)
        require(task["category"] == "work", "只有工作任务可以关联 Linear issue")
        existing = [b for b in task["bindings"] if b["kind"] == "linear" and not b.get("demo")]
        if existing:
            require(parse_binding("linear", existing[0]["url"])["label"] == pkg.issue["identifier"], "任务的 issue 已变化，请人工核实同步记录")
        self.repository.update(pkg.task_id, linear_issue=copy.deepcopy(pkg.issue),
            bindings=[b for b in task["bindings"] if b["kind"] != "linear"] + [parse_binding("linear", pkg.issue["url"])])
        self.changed()
        return ok(self.task(pkg.task_id))

    def apply_plan(self, pkg):
        require(isinstance(pkg, ApplyTaskPlan) and isinstance(pkg.baseline, list), "expected ApplyTaskPlan")
        result = self.repository.apply_plan(pkg.task_id, copy.deepcopy(pkg.steps), copy.deepcopy(pkg.baseline))
        self.changed()
        return ok(result)

    def set_step(self, pkg):
        require(isinstance(pkg, StepState) and type(pkg.index) is int and type(pkg.done) is bool, "expected StepState")
        steps = self.task(pkg.task_id)["steps"]
        require(0 <= pkg.index < len(steps), "子任务不存在")
        self.repository.update(steps[pkg.index]["id"], status="done" if pkg.done else "todo")
        self.changed()
        return ok(self.task(pkg.task_id))

    def append_binding(self, pkg):
        require(isinstance(pkg, AppendBinding) and isinstance(pkg.binding, dict), "expected AppendBinding")
        raw = pkg.binding
        require(isinstance(raw.get("kind"), str) and isinstance(raw.get("url"), str), "无效关联")
        binding = parse_binding(raw["kind"], raw["url"])
        if binding["kind"] == "codex" and "title" in raw:
            require(isinstance(raw["title"], str) and 0 < len(raw["title"]) <= 300, "聊天标题格式无效")
            binding["label"] = raw["title"]
        task = self.task(pkg.task_id)
        require(binding["kind"] != "linear" or task["category"] == "work", "只有工作任务可以关联 Linear issue")
        require(not any(b["url"] == binding["url"] for b in task["bindings"]), "这条链接已经关联过了")
        if binding["kind"] == "linear":
            self.repository.check_issue_mapping({**task,"bindings":task["bindings"]+[binding]})
        self.repository.update(pkg.task_id, bindings=task["bindings"] + [binding])
        self.changed()
        return ok(binding)

    def remove_binding(self, pkg):
        require(isinstance(pkg, BindingIndex) and type(pkg.index) is int, "expected BindingIndex")
        bindings = copy.deepcopy(self.task(pkg.task_id)["bindings"])
        require(0 <= pkg.index < len(bindings), "关联不存在")
        bindings.pop(pkg.index)
        self.repository.update(pkg.task_id, bindings=bindings)
        self.changed()
        return ok(bindings)

    def detach_linear(self, pkg):
        require(isinstance(pkg, TaskId), "expected TaskId")
        self.task(pkg.task_id)
        self.repository.detach_linear(pkg.task_id)
        self.changed()
        return ok(self.task(pkg.task_id))

    def credit_focus(self, pkg):
        require(isinstance(pkg, FocusCredit) and type(pkg.seconds) is int and pkg.seconds >= 0, "expected nonnegative FocusCredit")
        task = self.task(pkg.task_id)
        self.repository.update(pkg.task_id, focus_seconds=task["focus_seconds"] + pkg.seconds)
        self.changed()
        return ok(self.task(pkg.task_id)["focus_seconds"])

    def set_focus_minutes(self, pkg):
        require(isinstance(pkg, FocusMinutes), "expected FocusMinutes")
        self.repository.set_focus_minutes(pkg.minutes)
        self.changed()
        return ok(self.repository.settings)

    def clear(self, pkg):
        require(pkg is None, "expected None")
        self.repository.clear_samples()
        self.changed()
        return ok()
