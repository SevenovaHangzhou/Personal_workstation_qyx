"""Local Codex app-server transport and transparent history matching.

Only explicit chat creation may write a thread. Never submit a turn or execute tools.
"""
import json
import os
import re
import select
import shutil
import subprocess
import time
from pathlib import Path
from typedefine.TypeDefine import APP_VERSION


class CodexError(Exception):
    pass


class CodexMethodUnavailable(CodexError):
    pass


def redact(text):
    text = text if isinstance(text, str) else ""
    text = re.sub(r"(?:lin_api_|sk-)[A-Za-z0-9_-]{8,}", "[密钥已隐藏]", text)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:Bearer\s+)?[^\s\"']+", r"\1[已隐藏]", text)
    return text


def thread_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise CodexError("Codex 返回的聊天 ID 无效")
    return value


class CodexRpc:
    METHODS = {"initialize", "thread/list", "thread/read", "thread/items/list", "thread/start", "thread/name/set", "config/read"}

    def __init__(self, cancel_event=None):
        self.cancel_event = cancel_event
        self.process = None
        self.buffer = bytearray()
        self.serial = 0

    def __enter__(self):
        executable = shutil.which("codex")
        if not executable:
            raise CodexError("未找到 Codex CLI。请安装并登录 Codex，或使用下方链接手动关联。")
        try:
            import os
            env = dict(os.environ)
            self.process = subprocess.Popen([executable, "app-server", "--listen", "stdio://"],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
            self.request("initialize", {"clientInfo": {"name": "momo_task_manager", "title": "Momo", "version": APP_VERSION}}, timeout=15)
            self.send({"method": "initialized"})
            return self
        except Exception:
            self.close()
            raise

    def check_cancel(self):
        if self.cancel_event and self.cancel_event.is_set():
            raise CodexError("已取消读取历史聊天")

    def send(self, payload):
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
            self.process.stdin.flush()
        except (OSError, ValueError, AttributeError):
            raise CodexError("Codex 连接已关闭，请重试") from None

    def read_message(self, deadline):
        while True:
            self.check_cancel()
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self.buffer[:newline]); del self.buffer[:newline + 1]
                try:
                    return json.loads(line)
                except (ValueError, UnicodeError):
                    raise CodexError("Codex 返回了无法解析的数据，请检查 CLI 版本") from None
            if time.monotonic() >= deadline:
                raise CodexError("读取 Codex 超时，请稍后刷新或手动关联")
            ready, _, _ = select.select([self.process.stdout], [], [], min(0.1, max(0, deadline-time.monotonic())))
            if ready:
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise CodexError("Codex app-server 已退出，请检查 CLI 是否可用")
                self.buffer.extend(chunk)
                if len(self.buffer) > 64 * 1024 * 1024:
                    raise CodexError("这条 Codex 历史过大，请使用链接直接关联")

    def request(self, method, params, timeout=10):
        if method not in self.METHODS:
            raise CodexError("此客户端不支持该 Codex 操作")
        self.check_cancel()
        self.serial += 1
        request_id = self.serial
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            message = self.read_message(deadline)
            if not isinstance(message, dict):
                raise CodexError("Codex 响应格式无效")
            if message.get("method"):
                if "id" in message:
                    self.send({"id": message["id"], "error": {"code": -32601, "message": "Momo does not handle interactive requests"}})
                continue
            if message.get("id") != request_id:
                continue
            if message.get("error"):
                error = message["error"]
                if isinstance(error, dict) and error.get("code") == -32601:
                    raise CodexMethodUnavailable("当前 Codex CLI 不支持此接口，请更新 CLI 或手动关联")
                raise CodexError("Codex 未能完成此操作，请刷新或检查 CLI 登录状态")
            result = message.get("result")
            if not isinstance(result, dict):
                raise CodexError("Codex 响应缺少有效结果")
            return result

    def list_threads(self, archived=False, query=""):
        rows, cursor, cursors = [], None, set()
        for _ in range(3):
            result = self.request("thread/list", {"limit": 100, "cursor": cursor, "sortKey": "updated_at", "sortDirection": "desc",
                                                  "archived": archived, "modelProviders": [], "searchTerm": query or None})
            batch = result.get("data")
            if not isinstance(batch, list):
                raise CodexError("Codex 聊天列表格式无效")
            for row in batch:
                if not isinstance(row, dict):
                    continue
                thread_id(row.get("id"))
                if row.get("parentThreadId") or isinstance(row.get("source"), dict):
                    continue
                rows.append(row)
            cursor = result.get("nextCursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in cursors:
                raise CodexError("Codex 分页信息异常，请重新刷新")
            cursors.add(cursor)
        return rows

    def history(self, identifier):
        identifier = thread_id(identifier)
        items, cursor, cursors = [], None, set()
        try:
            for _ in range(3):
                result = self.request("thread/items/list", {"threadId": identifier, "limit": 100, "sortDirection": "desc", "cursor": cursor})
                batch = result.get("data")
                if not isinstance(batch, list):
                    raise CodexError("Codex 历史条目格式无效")
                items.extend(entry.get("item", {}) for entry in batch if isinstance(entry, dict))
                cursor = result.get("nextCursor")
                if not cursor:
                    break
                if not isinstance(cursor, str) or cursor in cursors:
                    raise CodexError("Codex 历史分页信息异常")
                cursors.add(cursor)
        except CodexMethodUnavailable:
            result = self.request("thread/read", {"threadId": identifier, "includeTurns": True})
            items = [item for turn in result.get("thread", {}).get("turns", [])[-30:]
                     for item in turn.get("items", [])]
        return message_text(items)

    def create(self, title, cwd):
        result = self.request("thread/start", {"cwd": cwd, "ephemeral": False})
        identifier = thread_id(result.get("thread", {}).get("id"))
        # The app-server method is thread/name/set (generated protocol).
        self.request("thread/name/set", {"threadId": identifier, "name": title})
        return identifier

    def close(self):
        if not self.process:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=1)
        for stream in [self.process.stdin, self.process.stdout]:
            if stream:
                stream.close()
        self.process = None

    def __exit__(self, *args):
        self.close()


def message_text(items):
    texts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "userMessage":
            for part in item.get("content", []):
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(redact(part.get("text")))
        elif item.get("type") == "agentMessage":
            texts.append(redact(item.get("text")))
    return "\n".join(texts)[:60000]


STOP = {"任务", "工作", "这个", "一个", "一下", "可以", "帮我", "进行", "实现", "功能", "the", "and", "for", "with", "task", "please"}


def tokens(text):
    text = redact(text).lower()
    parts = set(re.findall(r"[a-z][a-z0-9_.-]{2,}", text))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        parts.update(run[i:i+2] for i in range(len(run)-1))
    return parts - STOP


def metadata(row):
    title = redact(row.get("name") or row.get("preview") or "未命名聊天").splitlines()[0][:180]
    return {"id": thread_id(row.get("id")), "title": title, "preview": redact(row.get("preview"))[:1000],
            "cwd": row.get("cwd") if isinstance(row.get("cwd"), str) else "", "updated_at": row.get("updatedAt") or 0,
            "git_url": (row.get("gitInfo") or {}).get("repositoryUrl", ""), "history": "", "history_read": False}


def rank(task, rows, query=""):
    query_text = query.strip() or " ".join([task["title"], task.get("description", "")[:2000], task.get("next_step", ""), " ".join(c["title"] for c in task.get("steps", []))])
    terms = tokens(query_text)
    title_terms = tokens(query.strip() or task["title"])
    issues = {b["label"].upper() for b in task.get("bindings", []) if b.get("kind") == "linear" and not b.get("demo")}
    linked = {b["url"] for b in task.get("bindings", []) if b.get("kind") == "codex"}
    results = []
    for row in rows:
        url = "codex://threads/" + row["id"]
        if url in linked:
            continue
        title_text = row["title"] + " " + row["preview"]
        body = row.get("history", "")
        combined = (title_text + " " + body).lower()
        title_match = terms & tokens(title_text)
        body_match = terms & tokens(body)
        weight = lambda term: 1.5 if re.search(r"[a-z]", term) else 1
        score = min(28, sum(4*weight(t) for t in title_match)) + min(24, sum(3*weight(t) for t in body_match))
        phrase = (query.strip() or task["title"]).lower()
        if len(phrase) >= 3 and phrase in combined:
            score += 12
        reasons = []
        for identifier in issues:
            if re.search(r"(?<![a-z0-9])" + re.escape(identifier.lower()) + r"(?![a-z0-9])", combined):
                score += 65; reasons.append("提到同一个 issue：" + identifier)
        for repo in task.get("repositories", []):
            if repo["kind"] == "local" and row["cwd"]:
                root, cwd = Path(repo["location"]), Path(row["cwd"])
                if root == cwd or root in cwd.parents:
                    score += 55; reasons.append("聊天位于已关联仓库")
            elif repo["kind"] == "remote" and repo["location"] == row.get("git_url"):
                score += 55; reasons.append("聊天使用同一个远程仓库")
        if title_match & title_terms:
            reasons.append("标题 / 首条消息与任务相近")
        if body_match:
            reasons.append("历史对话内容与任务相近")
        matches = sorted(body_match or title_match, key=lambda x: (-len(x), x))
        excerpt_source = body or row["preview"]
        position = next((excerpt_source.lower().find(t) for t in matches if t in excerpt_source.lower()), 0)
        snippet = re.sub(r"\s+", " ", excerpt_source[max(0, position-40):max(0, position-40)+200])
        results.append({**row, "url": url, "score": score, "reasons": reasons or ["最近的聊天，可手动检查"], "snippet": snippet})
    results.sort(key=lambda r: (-r["score"], -r["updated_at"]))
    return results


def recommend(task, query="", include_archived=False, cancel_event=None, cache=None):
    cache = cache if cache is not None else {}
    deadline = time.monotonic() + 75
    with CodexRpc(cancel_event) as rpc:
        raw = rpc.list_threads()
        if include_archived:
            raw += rpc.list_threads(True)
        if query.strip():
            raw += rpc.list_threads(False, query.strip())
        rows = list({row["id"]: metadata(row) for row in raw}.values())
        ranked = rank(task, rows, query)
        # Search content even when titles differ: read recent conversations as well.
        candidates = list(dict.fromkeys([r["id"] for r in ranked[:35]] + [r["id"] for r in sorted(rows, key=lambda r: -r["updated_at"])[:25]]))[:60]
        by_id = {r["id"]: r for r in rows}
        read_count, failures = 0, 0
        for identifier in candidates:
            rpc.check_cancel()
            if time.monotonic() > deadline:
                break
            row = by_id[identifier]
            cached = cache.get(identifier)
            if cached and cached[0] == row["updated_at"]:
                row["history"] = cached[1]; row["history_read"] = True
                read_count += 1; continue
            try:
                row["history"] = rpc.history(identifier)
                row["history_read"] = True
                cache[identifier] = (row["updated_at"], row["history"])
                read_count += 1
            except CodexError:
                rpc.check_cancel()
                failures += 1
        ranked = rank(task, rows, query)
        relevant = [r for r in ranked if r["score"] >= 8]
        shown = relevant[:12] if relevant else ranked[:8]
        for row in shown:
            row.pop("history", None)
            row.pop("preview", None)
            row.pop("git_url", None)
        # Bound memory to this scan and avoid saving history to disk.
        for identifier in list(cache):
            if identifier not in by_id:
                del cache[identifier]
        return {"candidates": shown, "scanned": len(rows), "history_read": read_count,
                "failures": failures, "matched": bool(relevant)}
