from model import parse_binding
from msgs.YomkMsgs import TaskId, BindingInput, BindingIndex, AppendBinding
from services.base import MomoService, ok, require, request_data


class BindingService(MomoService):
    """Codex and Linear link validation; OS navigation stays in the Qt adapter."""
    def __init__(self, server):
        super().__init__(server)
        self.set_name("/BindingService")

    def init(self):
        self.install("/bind", self.bind, "BindingInput", "校验并保存 Codex 或 Linear 关联")
        self.install("/remove", self.remove, "BindingIndex", "移除任务关联")
        self.install("/list", self.list_bindings, "TaskId", "获取任务关联副本")
        self.install("/resolve", self.resolve, "BindingIndex", "校验待打开的真实关联链接")
        self.alive = True
        return 0

    def bind(self, pkg):
        require(isinstance(pkg, BindingInput), "expected BindingInput")
        require(isinstance(pkg.kind, str) and isinstance(pkg.url, str), "关联类型和链接须为字符串")
        binding = parse_binding(pkg.kind, pkg.url)
        return ok(request_data("/TaskService/append_binding", AppendBinding(pkg.task_id, binding)))

    def remove(self, pkg):
        require(isinstance(pkg, BindingIndex), "expected BindingIndex")
        return ok(request_data("/TaskService/remove_binding", pkg))

    def list_bindings(self, pkg):
        require(isinstance(pkg, TaskId), "expected TaskId")
        task = request_data("/TaskService/get", pkg)
        require(task is not None, "没有找到这个任务")
        return ok(task["bindings"])

    def resolve(self, pkg):
        require(isinstance(pkg, BindingIndex) and type(pkg.index) is int, "expected BindingIndex")
        bindings = request_data("/BindingService/list", TaskId(pkg.task_id))
        require(0 <= pkg.index < len(bindings), "关联不存在")
        binding = bindings[pkg.index]
        require(not binding.get("demo"), "这是一条示例关联，请先替换为真实链接")
        parse_binding(binding["kind"], binding["url"])
        return ok(binding)
