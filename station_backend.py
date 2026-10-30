"""Embedded Work Station composition; owns databases, services and local workers.

Only the pinned project/task repositories and Yomk runtime are composed. The
upstream distributed controller, HTTP server and provider router are never started.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def load_station(data_dir: Path) -> tuple:
    """Load the fixed dependency with desktop-scoped logs before domain imports."""
    sys.path.insert(0, str(ROOT / "vendor" / "work_station"))
    old = {key: os.environ.get(key) for key in ("LAN_MESH_LOG_FILE", "LAN_MESH_LOG_CONSOLE")}
    os.environ["LAN_MESH_LOG_FILE"] = str(data_dir / "station.log")
    os.environ["LAN_MESH_LOG_CONSOLE"] = "0"
    from lan_mesh.database import Database
    from lan_mesh.project import ProjectManager
    from lan_mesh.protocol import Task
    from lan_mesh.service_runtime import ServiceRuntime
    from lan_mesh.component_service import ComponentService
    logger = logging.getLogger("lan_mesh")
    logger.propagate = False
    from logging.handlers import RotatingFileHandler
    if not any(getattr(h, "baseFilename", "") == str(data_dir / "station.log") for h in logger.handlers):
        handler = RotatingFileHandler(data_dir / "station.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    for key, value in old.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    return Database, ProjectManager, Task, ServiceRuntime, ComponentService


def make_database(base: type, path: Path) -> Any:
    """Keep upstream schema/repositories; scope backups and connection ownership."""
    class DesktopDatabase(base):
        def __init__(self, location: Path) -> None:
            self.connections = []
            self.connection_lock = threading.Lock()
            super().__init__(str(location))

        def _get_conn(self) -> sqlite3.Connection:
            new = not hasattr(self._local, "conn")
            conn = super()._get_conn()
            if new:
                with self.connection_lock:
                    self.connections.append(conn)
            return conn

        def backup(self, keep: int = 3) -> str:
            folder = self.path.parent / "backups"
            folder.mkdir(exist_ok=True)
            target = folder / f"station-{time.time_ns()}.sqlite3"
            with sqlite3.connect(target) as dest:
                self._get_conn().backup(dest)
            for old in sorted(folder.glob("station-*.sqlite3"))[:-keep]:
                old.unlink()
            return str(target)

        def close(self) -> None:
            for conn in self.connections:
                conn.close()
            self.connections.clear()
    return DesktopDatabase(path)


class EmbeddedStation:
    """Synchronous desktop API routed through the pinned instance Yomk server."""

    def __init__(self, folder: Path, task_client: Any, executor: Any = None) -> None:
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self._lock_file = (self.folder / "desktop.lock").open("a+")
        import fcntl
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            raise RuntimeError("该 Momo 数据目录已在运行，请打开已有窗口")
        self.lock = threading.RLock()
        self.tasks = task_client
        self.executor = executor
        self.stopping = False
        self.maintenance_roots = set()
        self.workers: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self.concurrency = 0  # Explicit selection required before queue execution.
        self.closed = False
        try:
            self._compose()
        except Exception:
            self.close()
            raise

    def _compose(self) -> None:
        Database, ProjectManager, self.Task, Runtime, Component = load_station(self.folder)
        self.db = make_database(Database, self.folder / "station.sqlite3")
        self.projects = ProjectManager(self.db)
        self.services = Runtime()
        conn = self.db._get_conn()
        conn.execute("CREATE TABLE IF NOT EXISTS momo_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS momo_projects (momo_id TEXT PRIMARY KEY, project_id TEXT NOT NULL)")
        saved = conn.execute("SELECT value FROM momo_settings WHERE key='concurrency'").fetchone()
        if saved:
            self.concurrency = int(saved[0])
        conn.commit()
        class DesktopExecutionService(Component):
            pass
        service = DesktopExecutionService(self.services.server, self)
        methods = ("snapshot", "create_project", "link_project", "submit", "configure", "cancel", "review", "retry")
        self.services.register(service, {name: getattr(self, "_" + name) for name in methods})
        self.url = service.get_name()
        # Startup never repeats a model request whose outcome is unknown.
        for task in self.db.list_tasks(limit=1000000):
            if task.status in ("running", "pending"):
                task.status = "interrupted"
                task.output_data["error"] = "上次应用退出，执行结果待核实；检查工作目录后再重试。"
                self.db.save_task(task)
        from station_executor import CodexExecutor
        self.executor = self.executor or CodexExecutor()
        # Discard the former app override; use the system Codex environment.
        conn.execute("DELETE FROM momo_settings WHERE key='codex_home'")
        conn.commit()

    def call(self, operation: str, **arguments: Any) -> Any:
        """Dispatch all desktop operations through real YomkService functions."""
        with self.lock:
            if self.closed:
                raise RuntimeError("本机工作站已关闭")
            return self.services.request_sync(self.url + "/" + operation, **arguments)

    def _snapshot(self) -> dict:
        return {"concurrency": self.concurrency, "running": len(self.workers),
                "projects": [p.to_dict() for p in self.projects.list_projects()],
                "executions": [t.to_dict() for t in self.db.list_tasks(limit=1000000)],
                "codex": self.executor.status(),
                "links": dict(self.db._get_conn().execute("SELECT momo_id, project_id FROM momo_projects")),
                "services": self.services.describe()}

    def _create_project(self, name: str, description: str = "") -> dict:
        if not name.strip():
            raise ValueError("请填写项目名称")
        return self.projects.create_project(name.strip(), description,
                    allowed_models=["codex-cli"], routing_strategy="local-codex",
                    workspace_base=str(self.folder / "workspaces")).to_dict()

    def _link_project(self, momo_id: str, project_id: str) -> None:
        if not self.tasks.get(momo_id) or not self.projects.get_project(project_id):
            raise ValueError("任务或项目不存在")
        conn = self.db._get_conn()
        conn.execute("INSERT OR REPLACE INTO momo_projects VALUES (?, ?)", (momo_id, project_id))
        conn.commit()

    def _configure(self, concurrency: int) -> None:
        if type(concurrency) is not int or not 1 <= concurrency <= 32:
            raise ValueError("并发数应为 1 到 32 的整数")
        self.concurrency = concurrency
        conn = self.db._get_conn()
        conn.execute("INSERT OR REPLACE INTO momo_settings VALUES ('concurrency', ?)", (str(concurrency),))
        conn.commit()
        self._pump()

    def _validated_payload(self, momo_id: str, project_id: str, cwd: str,
                           goal: str, acceptance: str, sandbox: str) -> tuple:
        task = self.tasks.get(momo_id)
        if not task:
            raise ValueError("没有找到本机任务")
        if not self.projects.get_project(project_id):
            raise ValueError("请先选择本机项目")
        directory = Path(cwd).expanduser().resolve(strict=True)
        if not directory.is_dir():
            raise ValueError("工作目录不存在")
        if not goal.strip() or not acceptance.strip():
            raise ValueError("目标和验收条件都需要填写")
        if sandbox not in ("read-only", "workspace-write"):
            raise ValueError("请选择执行权限")
        return task, {"momo_id": momo_id, "cwd": str(directory), "goal": goal.strip(),
                      "acceptance": acceptance.strip(), "sandbox": sandbox,
                      "project_id": project_id}

    def _submit(self, request_id: str, momo_id: str, project_id: str, cwd: str,
                goal: str, acceptance: str, sandbox: str) -> dict:
        if not self.concurrency:
            raise ValueError("请先设置并发数")
        uuid.UUID(request_id)
        task, payload = self._validated_payload(momo_id, project_id, cwd, goal, acceptance, sandbox)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        existing = self.db.get_task(request_id)
        if existing:
            if existing.input_data.get("payload_hash") != digest:
                raise ValueError("同一提交标识对应不同内容")
            return existing.to_dict()
        if not self.executor.status()["available"]:
            raise ValueError(self.executor.status()["message"])
        if any(t.input_data.get("momo_id") == momo_id and t.status in ("pending", "running")
               for t in self.db.list_tasks(limit=1000000)):
            raise ValueError("该任务已有排队或运行中的执行，请先查看执行记录")
        payload["payload_hash"] = digest
        from station_executor import git_snapshot
        payload["git"] = git_snapshot(payload["cwd"])
        payload["lock_root"] = payload["git"].get("root", payload["cwd"])
        record = self.Task(task_id=request_id, name=task["title"], description=goal,
                           input_data=payload, project_id=project_id, created_by="momo-desktop")
        self.db.save_task(record)
        self._link_project(momo_id, project_id)
        self._pump()
        return self.db.get_task(request_id).to_dict()

    @staticmethod
    def _overlap(left: str, right: str) -> bool:
        a, b = Path(left), Path(right)
        return a == b or a in b.parents or b in a.parents

    def _pump(self) -> None:
        if self.stopping or not self.concurrency:
            return
        busy = [self.db.get_task(key).input_data.get("lock_root", self.db.get_task(key).input_data["cwd"]) for key in self.workers]
        busy.extend(self.maintenance_roots)
        for record in reversed(self.db.list_tasks(status="pending", limit=1000000)):
            if len(self.workers) >= self.concurrency:
                break
            cwd = record.input_data.get("lock_root", record.input_data["cwd"])
            if any(self._overlap(cwd, path) for path in busy):
                continue
            record.status = "running"
            self.db.save_task(record)
            cancel = threading.Event()
            worker = threading.Thread(target=self._run, args=(record.task_id, cancel),
                                      name="momo-codex-" + record.task_id[:8])
            self.workers[record.task_id] = (worker, cancel)
            busy.append(cwd)
            worker.start()

    def _run(self, task_id: str, cancel: threading.Event) -> None:
        record = self.db.get_task(task_id)
        try:
            result = self.executor.run(record.to_dict(), self.folder / "runs" / task_id, cancel)
            with self.lock:
                record.output_data = result
                record.status = "cancelled" if cancel.is_set() else result["status"]
        except Exception as exc:
            with self.lock:
                record.status = "cancelled" if cancel.is_set() else "failed"
                record.output_data = {"error": str(exc)[:3000]}
        finally:
            with self.lock:
                record.completed_at = time.time()
                self.db.save_task(record)
                self.workers.pop(task_id, None)
                self._pump()

    def _cancel(self, task_id: str) -> dict:
        record = self._record(task_id)
        if task_id in self.workers:
            self.workers[task_id][1].set()
            return {"pending": True}
        if record.status == "pending":
            record.status = "cancelled"
            self.db.save_task(record)
        return {"pending": False}

    def _record(self, task_id: str) -> Any:
        task = self.db.get_task(task_id)
        if not task:
            raise ValueError("执行记录不存在")
        return task

    def _review(self, task_id: str, expected_completed_at: float, accepted: bool,
                feedback: str = "") -> None:
        record = self._record(task_id)
        if record.status != "review" or record.completed_at != expected_completed_at:
            raise ValueError("交付状态已变化，请刷新后再处理")
        record.status = "accepted" if accepted else "rejected"
        record.output_data["feedback"] = feedback
        self.db.save_task(record)

    def _retry(self, task_id: str, request_id: str, reply: str) -> dict:
        record = self._record(task_id)
        if record.output_data.get("continued_as"):
            if record.output_data["continued_as"] == request_id:
                return self._record(request_id).to_dict()
            raise ValueError("这次结果已提交后续执行，请查看最新记录")
        if record.status not in ("needs_input", "failed", "interrupted", "cancelled", "rejected"):
            raise ValueError("当前执行不能重试或补充")
        if not reply.strip():
            raise ValueError("请补充答复或重试说明")
        p = record.input_data
        goal = p["goal"] + "\n\n上次结果：\n" + json.dumps(record.output_data, ensure_ascii=False)
        goal += "\n\n用户补充：\n" + reply
        result = self._submit(request_id, p["momo_id"], record.project_id, p["cwd"],
                              goal, p["acceptance"], p["sandbox"])
        record.output_data["continued_as"] = request_id
        self.db.save_task(record)
        return result

    def reserve_repository(self, root):
        from contextlib import contextmanager
        @contextmanager
        def guard():
            with self.lock:
                if self.closed or self.stopping:
                    raise RuntimeError("工作站正在退出")
                busy = [self.db.get_task(key).input_data.get("lock_root", self.db.get_task(key).input_data["cwd"]) for key in self.workers]
                if any(self._overlap(root, path) for path in busy) or any(self._overlap(root, path) for path in self.maintenance_roots):
                    raise RuntimeError("仓库正在执行任务，每日留存等待队列空闲后重试")
                self.maintenance_roots.add(root)
            try:
                yield
            finally:
                with self.lock:
                    self.maintenance_roots.discard(root)
                    if not self.closed: self._pump()
        return guard()

    def close(self) -> None:
        """Stop producers, terminate children, join workers, then release Yomk/SQLite."""
        with self.lock:
            if self.closed:
                return
            self.stopping = True
            workers = list(self.workers.values())
            for _, cancel in workers:
                cancel.set()
        for worker, _ in workers:
            worker.join()
        with self.lock:
            self.closed = True
            for owner in (getattr(self, "projects", None), self):
                if owner is None:
                    continue
                runtime = vars(owner).get("services")
                if runtime:
                    runtime.close()
                    runtime.wait_idle()
            if hasattr(self, "db"):
                self.db.close()
            self._lock_file.close()
        # File handlers belong to the embedded composition, not the user's station.
        logger = logging.getLogger("lan_mesh")
        for handler in list(logger.handlers):
            if getattr(handler, "baseFilename", "") == str(self.folder / "station.log"):
                logger.removeHandler(handler)
                handler.close()
