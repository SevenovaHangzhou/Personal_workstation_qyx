from git_support import inspect_local, remote_repository, validate_repository
from msgs.YomkMsgs import GitInput, RepositoryInput, RepositoryIndex, TaskId
from services.base import MomoService, ok, require, request_data


class GitService(MomoService):
    def __init__(self, server):
        super().__init__(server)
        self.set_name("/GitService")

    def init(self):
        self.install("/bind", self.bind, "GitInput", "关联本地或远程 Git 仓库")
        self.install("/remove", self.remove, "RepositoryIndex", "移除任务仓库关联")
        self.install("/resolve", self.resolve, "RepositoryIndex", "校验仓库后返回打开目标")
        self.alive = True
        return 0

    def bind(self, pkg):
        require(isinstance(pkg, GitInput) and pkg.kind in ("local", "remote"), "expected GitInput")
        task = request_data("/TaskService/get", TaskId(pkg.task_id))
        require(task is not None and task["category"] == "work", "只有工作任务可以关联 Git 仓库")
        repo = inspect_local(pkg.location) if pkg.kind == "local" else remote_repository(pkg.location)
        return ok(request_data("/TaskService/append_repository", RepositoryInput(pkg.task_id, repo)))

    def remove(self, pkg):
        require(isinstance(pkg, RepositoryIndex), "expected RepositoryIndex")
        return ok(request_data("/TaskService/remove_repository", pkg))

    def resolve(self, pkg):
        require(isinstance(pkg, RepositoryIndex) and type(pkg.index) is int, "expected RepositoryIndex")
        task = request_data("/TaskService/get", TaskId(pkg.task_id))
        require(task is not None, "没有找到这个任务")
        repos = task.get("repositories", [])
        require(0 <= pkg.index < len(repos), "仓库关联不存在")
        repo = repos[pkg.index]
        validate_repository(repo)
        if repo["kind"] == "local":
            inspect_local(repo["location"])
        return ok(repo)
