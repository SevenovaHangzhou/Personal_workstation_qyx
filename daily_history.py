"""Incrementally index timestamped local Codex messages; never index tools/reasoning."""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from codex_support import redact

ZONE = ZoneInfo('Asia/Shanghai')


def cutoff(day):
    return datetime.fromisoformat(day).replace(hour=22, tzinfo=ZONE)


def latest_due(now):
    local = now.astimezone(ZONE)
    return (local.date() if local.hour >= 22 else local.date()-timedelta(days=1)).isoformat()


def utcstamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('缺少时区的 Codex 时间戳')
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')


class HistoryIndex:
    def __init__(self, store, root=None):
        self.store = store
        self.root = Path(root) if root else Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))

    def scan(self, cancel, earliest=None):
        errors = []
        threshold = utcstamp(earliest.isoformat()) if earliest else None
        # rglob includes old threads that continued today and threads moved to archives.
        files = sorted(set(p for directory in ('sessions', 'archived_sessions')
                           for p in (self.root/directory).rglob('*.jsonl')))
        if not (self.root/'sessions').exists():
            errors.append('未找到本机 Codex sessions 目录；无法确认聊天记录完整性')
        for path in files:
            if cancel.is_set():
                raise InterruptedError('已停止日报收集')
            if path.is_symlink():
                continue
            info = path.stat(); inode = str(info.st_dev)+':'+str(info.st_ino)
            with self.store.lock:
                row = self.store.db.execute('SELECT inode,offset,meta FROM sources WHERE path=?', (str(path),)).fetchone()
            offset, meta = (row[1], json.loads(row[2])) if row and row[0] == inode and row[1] <= info.st_size else (0, {})
            records = []; reset = offset == 0
            file_errors = [] if reset else self.store.get('history_warning:'+str(path), [])
            try:
                with path.open('rb') as stream:
                    stream.seek(offset)
                    while True:
                        if cancel.is_set():
                            raise InterruptedError('已停止日报收集')
                        pos = stream.tell(); line = stream.readline()
                        if not line or not line.endswith(b'\n'):
                            break  # Writer may not yet have finished this record.
                        offset = stream.tell()
                        try:
                            record = json.loads(line)
                            payload = record.get('payload') or {}
                            if record.get('type') == 'session_meta':
                                meta = {'id': payload.get('id', ''), 'cwd': payload.get('cwd', ''),
                                        'skip': bool(payload.get('parent_thread_id') or isinstance(payload.get('source'), dict))}
                            if meta.get('skip') or not meta.get('id'):
                                continue
                            if record.get('type') != 'response_item' or payload.get('type') != 'message':
                                continue
                            role = payload.get('role')
                            if role not in ('user', 'assistant'):
                                continue
                            content = payload.get('content', [])
                            text = '\n'.join(c.get('text', '') for c in content
                                             if isinstance(c, dict) and c.get('type') in ('input_text', 'output_text', 'text'))
                            if not text.strip() or text.startswith(('# AGENTS.md instructions', '<environment_context>', '<INSTRUCTIONS>')):
                                continue
                            stamp = utcstamp(record['timestamp'])
                            if threshold is None or stamp >= threshold:
                                records.append((str(path), pos, meta['id'], stamp, role, redact(text)))
                        except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
                            file_errors.append('Codex 记录格式需核实：'+path.name+' @'+str(pos))
                with self.store.lock, self.store.db:
                    if reset:
                        self.store.db.execute('DELETE FROM facts WHERE source=?', (str(path),))
                    self.store.db.executemany('INSERT OR REPLACE INTO facts VALUES (?,?,?,?,?,?)', records)
                    self.store.db.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?)',
                                          (str(path), inode, offset, json.dumps(meta)))
                self.store.put('history_warning:'+str(path), list(dict.fromkeys(file_errors)))
                errors.extend(file_errors)
            except OSError:
                errors.append('无法读取 Codex 记录：'+path.name)
        return errors

    def between(self, start, end):
        with self.store.lock:
            rows = self.store.db.execute('SELECT thread,timestamp,role,text FROM facts WHERE timestamp>=? AND timestamp<? ORDER BY timestamp,source,pos',
                                        (utcstamp(start.isoformat()), utcstamp(end.isoformat()))).fetchall()
        groups = {}; seen = set()
        for thread, timestamp, role, text in rows:
            identity = (thread, timestamp, role, hashlib.sha256(text.encode()).hexdigest())
            if identity in seen:
                continue
            seen.add(identity)
            groups.setdefault(thread, []).append({'timestamp': timestamp, 'role': role, 'text': text})
        return groups
