"""Main-thread lifecycle coordinator for application services and the clock."""
import threading
from pathlib import Path
from framework import api
from observation import Observation, caller
from boot.MomoBoot import MomoBoot
from clients import TaskClient, FocusClient, LinearClient, CodexClient, DailyClient, LearningClient
from clients import UpdateClient
from clients import CollaborationClient, JournalShareClient
from typedefine.TypeDefine import CONTEXT_GROUP, FOCUS_LOOP


class DataSpaceInUse(RuntimeError):
    pass


class MomoRuntime:
    _lock = threading.Lock()
    _active = False

    def __init__(self, path, auto_tick=True, graph_port=None, profile=None):
        self.path = Path(path)
        from app_profile import profile as resolve_profile
        self.profile = resolve_profile(profile)
        self.boot = MomoBoot(self.path, auto_tick, self.profile)
        self.tasks = TaskClient()
        self.focus = FocusClient() if self.profile.study else None
        self.linear = LinearClient() if self.profile.work else None
        self.codex = CodexClient() if self.profile.work else None
        self.daily = DailyClient() if self.profile.work else None
        self.learning = LearningClient() if self.profile.study else None
        self.updates = UpdateClient()
        self.collaboration = CollaborationClient() if self.profile.work else None
        self.journal_share = JournalShareClient() if self.profile.work else None
        self.started = False
        self.station = None
        self.daily_service = None
        self.learning_service = None
        self.update_service = None
        self.collaboration_service = None
        self.journal_share_service = None
        self._data_lock = None
        self.graph = Observation(api, graph_port) if graph_port is not None else None

    def start(self):
        with self._lock:
            if self._active:
                raise RuntimeError("同一进程只能运行一个应用空间")
            if api.init(2) is None:
                raise RuntimeError("YomkServerPy 已关闭；请在新进程中启动")
            type(self)._active = True
            self.started = True
        try:
            # Lock before loading/migrating tasks, not only before opening Station.
            from platform_support import lock_file
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._data_lock = (self.path.parent / "momo.lock").open("a+")
            try:
                lock_file(self._data_lock)
            except OSError:
                raise DataSpaceInUse("此"+self.profile.name+"数据空间已在运行，请打开已有窗口") from None
            if self.profile.key!='combined':
                import json
                marker=self.path.parent/'.application.json'
                if marker.exists() and json.loads(marker.read_text(encoding='utf-8')).get('app_id')!=self.profile.app_id:
                    raise ValueError('该数据空间属于另一个应用，未修改数据')
                split_marker=self.path.parent/'.product-split.json'
                if split_marker.exists() and json.loads(split_marker.read_text(encoding='utf-8')).get('product')!=self.profile.key:
                    raise ValueError('该数据空间属于另一个应用，未修改数据')
            if self.graph: self.graph.attach()
            with caller("@Boot"):
                if api.boot(self.boot) != 0:
                    raise RuntimeError(self.profile.name+"服务启动失败")
            if self.profile.key!='combined' and not marker.exists():
                from split_migration import write_json
                write_json(marker,{'app_id':self.profile.app_id,'product':self.profile.key})
            if self.profile.work:
                from station_backend import EmbeddedStation
                self.station = EmbeddedStation(self.path.parent / "station", self.tasks)
                from services.DailyService import DailyService
                self.daily_service = DailyService(api.server(), self.path.parent / "daily", self.tasks,
                    self.boot.linear_service, self.station, self.boot.auto_tick, bound_only=self.profile.key=="work")
                if api.add_service(self.daily_service, "/DailyService") != 0:
                    raise RuntimeError("日报服务启动失败")
                self.boot.registered.append("/DailyService")
                self.daily_service.start_clock()
                from evaluation.sharing import JournalShareService
                self.journal_share_service = JournalShareService(api.server(), self.path.parent/'evaluation', self.tasks, self.daily_service)
                if api.add_service(self.journal_share_service, '/JournalShareService') != 0:
                    raise RuntimeError('评估员日志共享服务启动失败')
                self.boot.registered.append('/JournalShareService')
                self.journal_share_service.start()
                from services.CollaborationService import CollaborationService
                from collaboration.reports import ReportFolder
                from services.base import request_data
                report_folder=ReportFolder(self.path.parent/'daily',lambda:request_data('/DailyService/report_files',{}))
                self.collaboration_service = CollaborationService(api.server(), self.path.parent/'collaboration', self.tasks,report_folder=report_folder)
                if api.add_service(self.collaboration_service, '/CollaborationService') != 0:
                    raise RuntimeError('同事协作服务启动失败')
                self.boot.registered.append('/CollaborationService')
                self.collaboration_service.start()
            if self.profile.study:
                from services.LearningService import LearningService
                self.learning_service = LearningService(api.server(), self.path.parent / "learning", self.tasks)
                if api.add_service(self.learning_service, "/LearningService") != 0:
                    raise RuntimeError("学习服务启动失败")
                self.boot.registered.append("/LearningService")
            from services.UpdateService import UpdateService
            self.update_service = UpdateService(api.server(), self.path.parent/'updates', self.boot.auto_tick)
            if api.add_service(self.update_service, '/UpdateService') != 0:
                raise RuntimeError('更新服务启动失败')
            self.boot.registered.append('/UpdateService')
            self.update_service.start_clock()
            if self.graph: self.graph.start_http()
        except Exception as startup_error:
            try:
                self.close(apply_update=False)
            except Exception as cleanup_error:
                startup_error.add_note("启动清理失败："+str(cleanup_error))
            raise
        return self

    def close(self, apply_update=True, final_shutdown=False):
        """Control-thread teardown. Attempt every drain even if one reports failure.

        final_shutdown drains the framework request pool as well, without clearing
        its service table; the existing main() path still owns api.shutdown().
        Normal test/context-manager closes can reuse the initialized framework.
        """
        if not self.started:
            return
        errors = []
        def attempt(label, function):
            try:
                function()
            except Exception as exc:
                errors.append(label+"："+str(exc))
        if self.graph:
            self.graph._control_thread()
            attempt("停止观测 HTTP", self.graph.stop_http)
        # Stop all producers before removing services or restoring request().
        for label, service, method in [
            ("评估员共享线程", self.journal_share_service, "stop"),
            ("协作线程", self.collaboration_service, "stop"),
            ("更新线程", self.update_service, "stop"),
            ("学习线程", self.learning_service, "stop"),
            ("日报线程", self.daily_service, "stop_clock"),
            ("工作站线程", self.station, "close"),
        ]:
            if service:
                attempt(label, getattr(service, method))
        focus = self.boot.focus_service
        if focus:
            attempt("专注时钟", focus.stop_clock)
            if focus.loop_started:
                def drain_focus():
                    result = api.event_loop_destroy(FOCUS_LOOP)
                    if result.status != api.ResStatus.eOk:
                        raise RuntimeError(result.msg)
                    focus.loop_started = False
                attempt("专注回调", drain_focus)
            with caller("@Control"):
                attempt("保存专注", self.focus.pause)
        if final_shutdown:
            # No producers remain. This waits for queued requests AND callbacks,
            # while the observer and all business services still exist.
            attempt("框架异步池", lambda: api.server().executor.shutdown(wait=True))
        live = []
        for service in (self.journal_share_service, self.collaboration_service, self.update_service, self.daily_service, self.learning_service, self.station, focus):
            if service is None:
                continue
            for name in ('worker', 'clock', 'thread', 'summary_worker', 'sync_worker'):
                thread = getattr(service, name, None)
                if isinstance(thread, threading.Thread) and thread.is_alive(): live.append(name)
            workers = getattr(service, 'workers', {})
            if any(thread.is_alive() for thread, _ in workers.values()): live.append('workers')
        if self.learning_service and any(t.is_alive() for t, _ in self.learning_service.tutor.workers.values()):
            live.append('tutor')
        if self.graph and self.graph.viewer:
            live.append('graph-http')
        if live:
            raise RuntimeError("星序仍有运行入口，保留服务与观测器以便重试清理："+", ".join(live)+"；"+"; ".join(errors))
        if self.graph:
            # Refuse to detach if an unowned synchronous request is still in flight.
            self.graph.detach()
        for name in reversed(self.boot.registered[:]):
            def remove(name=name):
                try:
                    if api.del_service(name) != 0:
                        raise RuntimeError(name)
                finally:
                    # Framework unregisters before deinit; a deinit exception must
                    # not make subsequent cleanup retry a now-absent service.
                    if name not in api.server().service_names():
                        self.boot.registered.remove(name)
            attempt("服务注销失败", remove)
        for key in reversed(self.boot.context_keys[:]):
            def destroy(key=key):
                response = api.context_destroy(key, CONTEXT_GROUP)
                if response.status != api.ResStatus.eOk:
                    raise RuntimeError(response.msg)
                self.boot.context_keys.remove(key)
            attempt("Context 清理失败", destroy)
        if self.boot.registered or self.boot.context_keys:
            raise RuntimeError("星序清理待重试："+"; ".join(errors))
        if apply_update and self.update_service and not errors:
            attempt("更新尚未切换", self.update_service.activate)
        if self._data_lock:
            self._data_lock.close()
            self._data_lock = None
        self.started = False
        self.station = None
        with self._lock:
            type(self)._active = False
        if errors:
            raise RuntimeError("星序退出清理失败："+"; ".join(errors))

    def __enter__(self): return self.start()
    def __exit__(self, exc_type, exc, tb): self.close()
