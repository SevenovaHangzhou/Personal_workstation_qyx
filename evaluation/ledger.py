"""Source-linked issue progress notes; no acceptance, scoring or remote writes."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading

from evaluation.insights import RevisionConflict
from evaluation.protocol import valid_uuid


def _text(value, label, maximum, required=False):
    if (not isinstance(value, str) or len(value) > maximum or '\x00' in value or
            required and not value.strip()):
        raise ValueError(label + '格式无效')
    return value.strip()


def _sources(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ValueError('请选择1至100条日志来源')
    result, seen = [], set()
    for row in value:
        if (not isinstance(row, dict) or set(row) != {'id', 'digest', 'employee_id'} or
                any(not isinstance(row[key], str) or not re.fullmatch(r'[0-9a-f]{64}', row[key])
                    for key in ('id', 'digest')) or not valid_uuid(row['employee_id'])):
            raise ValueError('日志来源标识、版本或员工身份无效')
        identity = (row['employee_id'], row['id'])
        if identity in seen:
            raise ValueError('同一日志来源不可重复或同时引用多个版本')
        seen.add(identity)
        result.append(copy.deepcopy(row))
    return sorted(result, key=lambda row: (row['employee_id'], row['id']))


class IssueLedger:
    """Keep current progress notes and append-only revisions in one transaction."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.folder / 'issues.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS ledger_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
          INSERT OR IGNORE INTO ledger_meta VALUES ('revision',0);
          CREATE TABLE IF NOT EXISTS ledger_current (issue_id TEXT PRIMARY KEY, content TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS ledger_history (
            revision INTEGER PRIMARY KEY, issue_id TEXT NOT NULL, content TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS ledger_history_issue ON ledger_history(issue_id,revision);
        ''')
        self.db.commit()

    def _revision(self):
        return self.db.execute("SELECT value FROM ledger_meta WHERE key='revision'").fetchone()[0]

    def save(self, issue_id, summary, blockers, next_step, editor, sources, expected_revision=None):
        issue_id = _text(issue_id, 'Issue ID', 200, required=True)
        summary = _text(summary, '进展摘要', 6000)
        blockers = _text(blockers, '阻碍', 6000)
        next_step = _text(next_step, '下一步', 6000)
        editor = _text(editor, '更新人员', 80, required=True)
        if not (summary or blockers or next_step):
            raise ValueError('请至少填写进展摘要、阻碍或下一步中的一项')
        sources = _sources(sources)
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise ValueError('更新版本无效')
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            revision = self._revision()
            if expected_revision is not None and revision != expected_revision:
                raise RevisionConflict('Issue 进展已更新，请刷新后重试')
            result = dict(issue_id=issue_id, revision=revision + 1, summary=summary, blockers=blockers,
                          next_step=next_step, editor=editor, sources=sources,
                          updated_at=datetime.now(timezone.utc).isoformat())
            serialized = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            self.db.execute('INSERT INTO ledger_history VALUES(?,?,?)', (result['revision'], issue_id, serialized))
            self.db.execute('INSERT OR REPLACE INTO ledger_current VALUES(?,?)', (issue_id, serialized))
            self.db.execute("UPDATE ledger_meta SET value=? WHERE key='revision'", (result['revision'],))
        return result

    def snapshot(self, allowed_issue_ids):
        if not isinstance(allowed_issue_ids, (list, tuple, set, frozenset)):
            raise ValueError('Issue 查看范围无效')
        allowed = sorted({_text(value, 'Issue ID', 200, required=True) for value in allowed_issue_ids})
        updates = []
        with self.lock:
            # One read transaction gives callers a matching revision and rows,
            # including when another process has opened the same local ledger.
            self.db.execute('BEGIN')
            try:
                revision = self._revision()
                for offset in range(0, len(allowed), 400):
                    subset = allowed[offset:offset + 400]
                    rows = self.db.execute('SELECT content FROM ledger_current WHERE issue_id IN (' +
                                           ','.join('?' for _ in subset) + ')', subset).fetchall()
                    updates.extend(json.loads(row[0]) for row in rows)
            finally:
                self.db.rollback()
        return dict(revision=revision, updates=sorted(updates, key=lambda row: row['revision'], reverse=True))

    def history(self, issue_id):
        issue_id = _text(issue_id, 'Issue ID', 200, required=True)
        with self.lock:
            rows = self.db.execute('SELECT content FROM ledger_history WHERE issue_id=? ORDER BY revision',
                                   (issue_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def close(self):
        with self.lock:
            self.db.close()
