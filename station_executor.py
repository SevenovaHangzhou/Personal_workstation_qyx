"""Cancellable transport through the system Codex CLI and its existing configuration."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path
from process_environment import system_environment

SCHEMA = {"type": "object", "properties": {
    "status": {"type": "string", "enum": ["review", "needs_input"]},
    "summary": {"type": "string"}, "question": {"type": "string"},
    "tests": {"type": "array", "items": {"type": "string"}},
    "artifacts": {"type": "array", "items": {"type": "string"}}},
    "required": ["status", "summary", "question", "tests", "artifacts"],
    "additionalProperties": False}


def git_snapshot(cwd: str) -> dict:
    """Inspect the selected worktree without modifying it or fetching remotes."""
    values = {}
    for key, args in (("commit", ["rev-parse", "HEAD"]),
                      ("changes", ["status", "--short"]),
                      ("root", ["rev-parse", "--show-toplevel"])):
        try:
            result = subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                                    text=True, timeout=5, env=system_environment())
            if result.returncode == 0:
                values[key] = result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            break
    return values


class CodexExecutor:
    """Reuse Codex auth in place; choose no provider, model or alternate CLI."""

    def environment(self) -> dict:
        # Codex may use provider credentials from its existing environment.
        env = {k: v for k, v in system_environment().items()
               if not k.upper().startswith(("LINEAR_", "LIN_API"))}
        return env

    def status(self) -> dict:
        """Check CLI presence without inspecting or changing authentication."""
        binary = shutil.which("codex")
        if not binary:
            return {"available": False, "message": "未找到本机 Codex CLI"}
        return {"available": True, "message": "使用系统 Codex · 沿用现有配置与认证"}

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        except ProcessLookupError:
            pass

    def run(self, record: dict, folder: Path, cancel: threading.Event) -> dict:
        binary = shutil.which("codex")
        if not binary:
            raise RuntimeError("未找到本机 Codex CLI")
        folder.mkdir(parents=True, exist_ok=True)
        schema, report = folder / "schema.json", folder / "report.json"
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        p = record["input_data"]
        prompt = ("你在 Momo 本机工作站执行用户已确认的任务。先拆解步骤，再在指定目录内执行并核实结果。"
                  "不确定的产品行为、需求、权限应返回 needs_input 和具体问题；完成后返回 review 供人验收。"
                  "不要访问远端执行节点，不要提交、推送、部署或向其他人发送消息。"
                  "不要调用其他模型 API 或其他 agent CLI。用户未请求的破坏性操作需要澄清。\n"
                  f"目标：\n{p['goal']}\n验收条件：\n{p['acceptance']}\n"
                  "artifacts 只列实际产生的文件；tests 只列实际执行的验证。")
        command = [binary, "exec", "-C", p["cwd"], "--skip-git-repo-check", "--json",
                   "-s", p["sandbox"], "-c", 'approval_policy="never"',
                   "--output-schema", str(schema), "-o", str(report), "-"]
        # Keep Codex authentication environment; exclude Linear credentials.
        env = self.environment()
        with (folder / "events.jsonl").open("wb") as out, (folder / "stderr.log").open("wb") as err:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out,
                                       stderr=err, env=env, start_new_session=True)
            try:
                process.stdin.write(prompt.encode("utf-8"))
                process.stdin.close()
                while process.poll() is None:
                    if cancel.wait(0.15):
                        self._stop(process)
                        return {"status": "cancelled", "summary": "用户已停止本次执行"}
            finally:
                if process.poll() is None:
                    self._stop(process)
        if process.returncode != 0:
            raise RuntimeError(f"Codex 退出码 {process.returncode}；请查看本次执行日志")
        result = json.loads(report.read_text(encoding="utf-8"))
        if (not isinstance(result, dict) or result.get("status") not in ("review", "needs_input")
                or not isinstance(result.get("summary"), str)
                or not isinstance(result.get("question"), str)
                or any(not isinstance(result.get(k), list) or
                       any(not isinstance(v, str) for v in result[k]) for k in ("tests", "artifacts"))):
            raise ValueError("Codex 返回格式无效，请检查 report.json")
        if result["status"] == "needs_input" and not result["question"].strip():
            raise ValueError("Codex 未给出需要补充的问题")
        # Keep only paths inside the confirmed working directory.
        root = Path(p["cwd"]).resolve()
        artifacts = []
        for item in result["artifacts"]:
            candidate = (root / item).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                artifacts.append(str(candidate))
        result["artifacts"] = artifacts
        result["git_after"] = git_snapshot(p["cwd"])
        result["log_dir"] = str(folder)
        with (folder / "events.jsonl").open(encoding="utf-8", errors="replace") as events:
            for line in events:
                try:
                    event = json.loads(line)
                    if event.get("type") == "thread.started":
                        result["codex_thread_id"] = event.get("thread_id", "")
                except ValueError:
                    continue
        return result
