import threading
from pathlib import Path
from framework import api
from codex_support import recommend, CodexRpc, CodexError, redact
from msgs.YomkMsgs import TaskId, CodexRecommend, CodexBind, CodexCreate, AppendBinding
from model import parse_binding
from services.base import MomoService, ok, require, request_data


class CodexService(MomoService):
    def __init__(self, server):
        super().__init__(server)
        self.set_name("/CodexService")
        self.history_cache = {}
        self.candidates = {}
        self.created = {}

    def init(self):
        self.install("/recommend", self.recommend, "CodexRecommend", "按任务、仓库和 issue 匹配历史聊天")
        self.install("/bind", self.bind, "CodexBind", "绑定已推荐的历史聊天")
        self.install("/create", self.create, "CodexCreate", "仅按用户选择新建空聊天并绑定，不发送消息")
        self.alive = True
        return 0

    def task(self, identifier):
        task = request_data("/TaskService/get", TaskId(identifier))
        require(task is not None, "没有找到这个任务")
        return task

    def recommend(self, pkg):
        require(isinstance(pkg, CodexRecommend) and isinstance(pkg.query, str) and len(pkg.query) <= 2000 and type(pkg.include_archived) is bool, "expected CodexRecommend")
        require(pkg.cancel_event is None or isinstance(pkg.cancel_event, threading.Event), "invalid cancellation event")
        task = self.task(pkg.task_id)
        self.candidates.pop(pkg.task_id, None)
        try:
            result = recommend(task, pkg.query, pkg.include_archived, pkg.cancel_event, self.history_cache)
        except CodexError as exc:
            return api.YomkResponse(api.ResStatus.eNo, str(exc))
        self.candidates[pkg.task_id] = {r["id"]: {"title": r["title"], "url": r["url"]} for r in result["candidates"]}
        return ok(result)

    def bind(self, pkg):
        require(isinstance(pkg, CodexBind) and isinstance(pkg.thread_id, str), "expected CodexBind")
        self.task(pkg.task_id)
        candidate = self.candidates.get(pkg.task_id, {}).get(pkg.thread_id)
        require(candidate is not None, "请刷新推荐后再选择聊天")
        binding = parse_binding("codex", candidate["url"])
        binding["title"] = candidate["title"]
        return ok(request_data("/TaskService/append_binding", AppendBinding(pkg.task_id, binding)))

    def create(self, pkg):
        require(isinstance(pkg, CodexCreate) and isinstance(pkg.cwd, str), "expected CodexCreate")
        task = self.task(pkg.task_id)
        path = Path(pkg.cwd).expanduser()
        require(path.is_absolute() and path.is_dir(), "请选择存在的绝对路径作为聊天工作目录")
        # Retain a successfully created chat if local binding failed, so retry won't create another.
        identifier = self.created.get(pkg.task_id)
        if not identifier:
            try:
                with CodexRpc() as rpc:
                    identifier = rpc.create(redact(task["title"]), str(path.resolve()))
            except CodexError as exc:
                return api.YomkResponse(api.ResStatus.eNo, str(exc))
            self.created[pkg.task_id] = identifier
        binding = parse_binding("codex", "codex://threads/" + identifier)
        binding["title"] = redact(task["title"])
        result = request_data("/TaskService/append_binding", AppendBinding(pkg.task_id, binding))
        self.created.pop(pkg.task_id, None)
        return ok(result)

    def deinit(self):
        with self.lock:
            self.alive = False
            self.history_cache.clear(); self.candidates.clear(); self.created.clear()
