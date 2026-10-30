"""Framework-owned monotonic focus clock. No dependency on Qt or its event loop."""
import math
import threading
import time
from framework import api
from msgs.YomkMsgs import TaskId, FocusStart, FocusMinutes, FocusCredit
from services.base import MomoService, ServiceRequestError, ok, require, request_data
from typedefine.TypeDefine import CONTEXT_GROUP, CTX_FOCUS, FOCUS_LOOP


class FocusService(MomoService):
    def __init__(self, server, auto_tick=True):
        super().__init__(server)
        self.set_name("/FocusService")
        self.auto_tick = auto_tick
        self.thread = None
        self.stop_event = threading.Event()
        self.tick_pending = threading.Event()
        self.loop_started = False
        self.task_id = None
        self.running = False
        self.duration = 1500
        self.remaining = 1500.0
        self.session_elapsed = 0.0
        self.pending_credit = {}
        self.last_elapsed = 0.0
        self.completion_serial = 0
        self.error = ""

    def init(self):
        snapshot = request_data("/TaskService/snapshot")
        self.duration = snapshot["settings"]["focus_minutes"] * 60
        self.remaining = float(self.duration)
        self.task_id = next((t["id"] for t in snapshot["tasks"] if t["status"] != "done"), None)
        for name, handler, message, description in [
            ("snapshot", self.snapshot, "None", "读取专注会话快照"),
            ("start", self.start, "FocusStart", "开始、继续或切换专注任务"),
            ("pause", self.pause, "None", "暂停并保存已专注时长"),
            ("reset", self.reset, "None", "结算后重置本段计时"),
            ("tick", self.tick, "None", "更新单调时钟与持久化检查点"),
            ("set_duration", self.set_duration, "FocusMinutes", "结算当前会话并保存时长偏好"),
            ("clear_selection", self.clear_selection, "None", "结算并清空当前专注任务"),
            ("delete_task", self.delete_task, "TaskId", "结算计时后删除本地任务"),
        ]:
            self.install("/" + name, handler, message, description)
        self.alive = True
        self.publish()
        return 0

    def state(self):
        return {"task_id": self.task_id, "running": self.running,
                "duration": self.duration, "remaining": max(0, math.ceil(self.remaining)),
                "session_elapsed": math.floor(self.session_elapsed),
                "completion_serial": self.completion_serial, "error": self.error}

    def publish(self):
        api.context_set(CTX_FOCUS, self.state(), CONTEXT_GROUP)

    def snapshot(self, pkg):
        require(pkg is None, "expected None")
        return ok(self.state())

    def observe(self):
        if not self.running:
            return
        elapsed = min(self.segment_budget, max(0.0, time.monotonic() - self.segment_start))
        delta = max(0.0, elapsed - self.last_elapsed)
        if self.task_id and delta:
            self.pending_credit[self.task_id] = self.pending_credit.get(self.task_id, 0.0) + delta
            self.session_elapsed += delta
        self.last_elapsed = elapsed
        self.remaining = max(0.0, self.segment_budget - elapsed)

    def flush(self):
        for task_id, pending in list(self.pending_credit.items()):
            seconds = math.floor(pending + 1e-9)
            if seconds:
                try:
                    request_data("/TaskService/credit_focus", FocusCredit(task_id, seconds))
                except ServiceRequestError as exc:
                    self.error = str(exc)
                    self.running = False
                    self.publish()
                    raise
                self.pending_credit[task_id] = max(0.0, pending - seconds)
        self.error = ""

    def pause_current(self):
        self.observe()
        self.running = False
        self.flush()
        self.publish()

    def start(self, pkg):
        require(isinstance(pkg, FocusStart), "expected FocusStart")
        require(pkg.task_id is None or isinstance(pkg.task_id, str), "task_id must be a string or None")
        task_id = pkg.task_id or self.task_id
        task = request_data("/TaskService/get", TaskId(task_id)) if task_id else None
        if not task or task["status"] == "done":
            require(pkg.task_id is None, "已完成或不存在的任务不能开始专注")
            task = next((t for t in request_data("/TaskService/list") if t["status"] != "done"), None)
        require(task is not None, "先创建一件未完成的小事吧")
        task_id = task["id"]
        if self.running and self.task_id == task_id:
            return ok(self.state())
        if self.task_id != task_id:
            self.pause_current()
            self.task_id = task_id
            self.remaining = float(self.duration)
            self.session_elapsed = 0.0
        else:
            self.flush()
        if self.remaining <= 0:
            self.remaining = float(self.duration)
            self.session_elapsed = 0.0
        self.segment_start = time.monotonic()
        self.segment_budget = self.remaining
        self.last_elapsed = 0.0
        self.running = True
        self.error = ""
        self.publish()
        return ok(self.state())

    def pause(self, pkg):
        require(pkg is None, "expected None")
        self.pause_current()
        return ok(self.state())

    def reset(self, pkg):
        require(pkg is None, "expected None")
        self.pause_current()
        self.remaining = float(self.duration)
        self.session_elapsed = 0.0
        self.publish()
        return ok(self.state())

    def clear_selection(self, pkg):
        require(pkg is None, "expected None")
        self.pause_current()
        self.task_id = None
        self.remaining = float(self.duration)
        self.session_elapsed = 0.0
        self.publish()
        return ok(self.state())

    def delete_task(self, pkg):
        require(isinstance(pkg, TaskId) and isinstance(pkg.task_id, str), "expected TaskId")
        require(request_data("/TaskService/get", pkg) is not None, "没有找到这个任务")
        if self.task_id == pkg.task_id:
            self.pause_current()
        elif math.floor(self.pending_credit.get(pkg.task_id, 0.0)):
            self.flush()
        request_data("/TaskService/delete", pkg)
        self.pending_credit.pop(pkg.task_id, None)
        if self.task_id == pkg.task_id:
            self.task_id = None
            self.remaining = float(self.duration)
            self.session_elapsed = 0.0
        self.publish()
        return ok()

    def set_duration(self, pkg):
        require(isinstance(pkg, FocusMinutes) and type(pkg.minutes) is int and pkg.minutes in [15, 25, 45, 60], "请选择有效的专注时长")
        self.pause_current()
        request_data("/TaskService/set_focus_minutes", pkg)
        self.duration = pkg.minutes * 60
        self.remaining = float(self.duration)
        self.session_elapsed = 0.0
        self.publish()
        return ok(self.state())

    def tick(self, pkg):
        require(pkg is None, "expected None")
        if self.running:
            task = request_data("/TaskService/get", TaskId(self.task_id))
            if not task:
                # Deletion is coordinated with pause by the UI; protect other callers too.
                self.running = False
                self.error = "专注任务已不存在，计时已暂停"
            else:
                self.observe()
                if self.remaining <= 0:
                    self.pause_current()
                    self.completion_serial += 1
                elif task["status"] == "done":
                    self.pause_current()
                elif sum(self.pending_credit.values()) >= 30:
                    self.flush()
        self.publish()
        return ok(self.state())

    def clock_event(self, pkg):
        try:
            # Go through the installed handler for locking, errors and liveness.
            return self.functions["/tick"](pkg)
        finally:
            self.tick_pending.clear()

    def start_clock(self):
        response = api.event_loop_start(FOCUS_LOOP, self.clock_event, "None")
        if response.status != api.ResStatus.eOk:
            raise RuntimeError(response.msg)
        self.loop_started = True
        if self.auto_tick:
            self.thread = threading.Thread(target=self.pulse, name="momo-focus-clock", daemon=True)
            self.thread.start()

    def pulse(self):
        while not self.stop_event.wait(0.25):
            if not self.tick_pending.is_set():
                self.tick_pending.set()
                response = api.event_loop_post(FOCUS_LOOP, None)
                if response.status != api.ResStatus.eOk:
                    self.tick_pending.clear()

    def stop_clock(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                raise RuntimeError("focus scheduler did not stop")

    def deinit(self):
        # No facade calls here: global shutdown may already have cleared its table.
        super().deinit()
        self.stop_clock()
