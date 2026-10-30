"""Main-thread lifecycle coordinator for application services and the clock."""
import threading
from pathlib import Path
from framework import api
from boot.MomoBoot import MomoBoot
from clients import TaskClient, FocusClient, LinearClient, CodexClient, DailyClient, LearningClient
from typedefine.TypeDefine import CONTEXT_GROUP, FOCUS_LOOP


class MomoRuntime:
    _lock = threading.Lock()
    _active = False

    def __init__(self, path, auto_tick=True):
        self.path = Path(path)
        self.boot = MomoBoot(self.path, auto_tick)
        self.tasks = TaskClient()
        self.focus = FocusClient()
        self.linear = LinearClient()
        self.codex = CodexClient()
        self.daily = DailyClient()
        self.learning = LearningClient()
        self.started = False
        self.station = None
        self.daily_service = None
        self.learning_service = None
        self._data_lock = None

    def start(self):
        with self._lock:
            if self._active:
                raise RuntimeError("同一进程只能运行一个 Momo 空间")
            if api.init(2) is None:
                raise RuntimeError("YomkServerPy 已关闭；请在新进程中启动")
            type(self)._active = True
            self.started = True
        try:
            # Lock before loading/migrating tasks, not only before opening Station.
            import fcntl
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._data_lock = (self.path.parent / "momo.lock").open("a+")
            try:
                fcntl.flock(self._data_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError("此 Momo 数据空间已在运行，请打开已有窗口") from None
            if api.boot(self.boot) != 0:
                raise RuntimeError("Momo Boot failed")
            from station_backend import EmbeddedStation
            self.station = EmbeddedStation(self.path.parent / "station", self.tasks)
            from services.DailyService import DailyService
            self.daily_service = DailyService(api.server(), self.path.parent / "daily", self.tasks,
                self.boot.linear_service, self.station, self.boot.auto_tick)
            if api.add_service(self.daily_service, "/DailyService") != 0:
                raise RuntimeError("日报服务启动失败")
            self.boot.registered.append("/DailyService")
            self.daily_service.start_clock()
            from services.LearningService import LearningService
            self.learning_service = LearningService(api.server(), self.path.parent / "learning", self.tasks)
            if api.add_service(self.learning_service, "/LearningService") != 0:
                raise RuntimeError("学习服务启动失败")
            self.boot.registered.append("/LearningService")
        except Exception:
            self.close()
            raise
        return self

    def close(self):
        if not self.started:
            return
        errors = []
        if self.learning_service:
            self.learning_service.stop()
        if self.daily_service:
            self.daily_service.stop_clock()
        if self.station:
            self.station.close()
            self.station = None
        focus = self.boot.focus_service
        if focus:
            try:
                focus.stop_clock()
            except Exception as exc:
                errors.append(str(exc))
            try:
                self.focus.pause()
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
            if focus.loop_started:
                result = api.event_loop_destroy(FOCUS_LOOP)
                if result.status != api.ResStatus.eOk:
                    errors.append(result.msg)
        for name in reversed(self.boot.registered):
            if api.del_service(name) != 0:
                errors.append("服务注销失败：" + name)
        self.boot.registered.clear()
        for key in reversed(self.boot.context_keys):
            api.context_destroy(key, CONTEXT_GROUP)
        self.boot.context_keys.clear()
        if self._data_lock:
            self._data_lock.close()
            self._data_lock = None
        self.started = False
        with self._lock:
            type(self)._active = False
        if errors:
            raise RuntimeError("Momo 退出清理失败：" + "; ".join(errors))

    def __enter__(self): return self.start()
    def __exit__(self, exc_type, exc, tb): self.close()
