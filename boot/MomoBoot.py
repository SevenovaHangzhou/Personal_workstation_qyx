"""Boot follows the framework project template's before/start/after contract."""
from framework import api
from services.TaskService import TaskService
from services.BindingService import BindingService
from services.FocusService import FocusService
from services.GitService import GitService
from services.LinearService import LinearService
from services.CodexService import CodexService
from typedefine.TypeDefine import (APP_VERSION, CONTEXT_GROUP, CTX_DATA_PATH,
                                   CTX_REVISION, CTX_FOCUS)


class MomoBoot(api.YomkBoot):
    def __init__(self, path, auto_tick=True):
        super().__init__()
        self.path = str(path)
        self.auto_tick = auto_tick
        self.registered = []
        self.context_keys = []
        self.focus_service = None
        self.linear_service = None

    def before(self):
        for key, value in [(CTX_DATA_PATH, self.path), (CTX_REVISION, 0), (CTX_FOCUS, {})]:
            response = api.context_create(key, value, CONTEXT_GROUP)
            if response.status != api.ResStatus.eOk:
                raise RuntimeError("无法创建 Momo Context：" + response.msg)
            self.context_keys.append(key)
        return 0

    def start(self):
        creators = {
            "/TaskService": lambda: TaskService(api.server()),
            "/BindingService": lambda: BindingService(api.server()),
            "/GitService": lambda: GitService(api.server()),
            "/LinearService": lambda: LinearService(api.server()),
            "/CodexService": lambda: CodexService(api.server()),
            "/FocusService": lambda: FocusService(api.server(), auto_tick=self.auto_tick),
        }
        for name, creator in creators.items():
            service = creator()
            if api.add_service(service, name) != 0:
                raise RuntimeError("Momo 服务启动失败：" + name)
            self.registered.append(name)
            if name == "/LinearService":
                self.linear_service = service
            if name == "/FocusService":
                self.focus_service = service
        return 0

    def after(self):
        response = api.request("/TaskService/snapshot", None)
        if response.status != api.ResStatus.eOk:
            raise RuntimeError(response.msg)
        self.focus_service.start_clock()
        api.console_log_info_tag("MomoBoot", "Momo", APP_VERSION, "YomkServerPy", api.version(), "ready")
        return 0
