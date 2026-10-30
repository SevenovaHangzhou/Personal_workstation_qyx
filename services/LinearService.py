import os
import threading
from pathlib import Path
from credentials import CredentialStore
from typedefine.TypeDefine import CONTEXT_GROUP, CTX_DATA_PATH
from framework import api
from linear_support import fetch_my_issues, LinearError
from msgs.YomkMsgs import LinearCredentials, LinearPull, LinearImport, ImportIssues
from services.base import MomoService, ok, require, request_data


class LinearService(MomoService):
    """Device-local Linear credentials and UI issue import."""
    def __init__(self, server):
        super().__init__(server)
        self.set_name("/LinearService")
        self.api_key = ""
        self.cache = {}
        self.viewer = None
        self.saved_key = ""
        self.credential_source = ""
        self.credential_notice = ""

    def init(self):
        self.credentials = CredentialStore(Path(api.context_get(CTX_DATA_PATH, "", CONTEXT_GROUP)).parent)
        try:
            self.saved_key, self.credential_source = self.credentials.load()
        except OSError:
            self.credential_notice = "本机凭据暂时无法读取，请重新配置密钥"
        self.install("/configure", self.configure, "LinearCredentials", "设置本次运行的 Linear API Key")
        self.install("/connection", self.connection, "None", "查看连接配置，不返回密钥")
        self.install("/pull", self.pull, "LinearPull", "分页拉取当前用户被指派的 issues")
        self.install("/import", self.import_selected, "LinearImport", "将所选已拉取 issues 导入工作任务")
        self.install("/forget", self.forget, "None", "移除已保存的 Linear 凭据")
        self.alive = True
        return 0

    def writer(self):
        from linear_support import LinearWriter
        with self.lock:
            key = self.api_key or os.environ.get("LINEAR_API_KEY", "").strip() or self.saved_key
            require(self.alive, "Linear service is stopped")
        return LinearWriter(key)

    def configure(self, pkg):
        require(isinstance(pkg, LinearCredentials) and isinstance(pkg.api_key, str) and type(pkg.remember) is bool, "expected LinearCredentials")
        key = pkg.api_key.strip()
        require(len(key) <= 4096 and not any(c.isspace() for c in key), "请输入不含空格的个人 API Key")
        if pkg.remember:
            require(key, "请填写要保存的密钥")
            self.credential_source = self.credentials.save(key)
            self.saved_key = key
        self.api_key = key
        self.credential_notice = ""
        self.cache = {}
        self.viewer = None
        return self.connection(None)

    def connection(self, pkg):
        require(pkg is None, "expected None")
        return ok({"configured": bool(self.api_key or os.environ.get("LINEAR_API_KEY") or self.saved_key), "viewer": self.viewer,
                   "remembered": bool(self.saved_key), "storage": self.credential_source, "notice": self.credential_notice})

    def forget(self, pkg):
        require(pkg is None, "expected None")
        self.credentials.clear()
        self.api_key = self.saved_key = self.credential_source = self.credential_notice = ""
        self.cache.clear()
        self.viewer = None
        return self.connection(None)

    def pull(self, pkg):
        require(isinstance(pkg, LinearPull) and type(pkg.include_closed) is bool, "expected LinearPull")
        require(pkg.cancel_event is None or isinstance(pkg.cancel_event, threading.Event), "invalid cancellation event")
        key = self.api_key or os.environ.get("LINEAR_API_KEY", "").strip() or self.saved_key
        require(key and len(key) <= 4096 and not any(c.isspace() for c in key), "请先填写 Linear 个人 API Key，或设置 LINEAR_API_KEY")
        self.cache = {}
        self.viewer = None
        try:
            result = fetch_my_issues(key, pkg.include_closed, pkg.cancel_event)
        except LinearError as exc:
            return api.YomkResponse(api.ResStatus.eNo, str(exc))
        self.cache = {issue["id"]: issue for issue in result["issues"]}
        self.viewer = result["viewer"]
        return ok(result)

    def import_selected(self, pkg):
        require(isinstance(pkg, LinearImport) and isinstance(pkg.issue_ids, list) and pkg.issue_ids, "请先勾选要导入的 issues")
        require(all(isinstance(i, str) and i in self.cache for i in pkg.issue_ids), "请重新拉取 issues 后选择导入")
        issues = [self.cache[i] for i in dict.fromkeys(pkg.issue_ids)]
        return ok(request_data("/TaskService/import_issues", ImportIssues(issues)))

    def deinit(self):
        with self.lock:
            self.alive = False
            self.api_key = ""
            self.saved_key = ""
            self.cache.clear()
            self.viewer = None
