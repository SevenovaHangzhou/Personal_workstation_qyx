"""Whitelist export of existing report ledgers and durable local replicas.

The exporter reads reports while holding their store locks. It never exposes the
SQLite database, raw chat groups, source paths, operations or Git snapshots.
"""
import copy
import json
import re
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from codex_support import redact
from evaluation.protocol import (MAX_BATCH, record_digest, record_id, timestamp,
                                 valid_uuid, validate_record)

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


def _safe_text(value):
    """Remove common credentials and local filesystem references from shared text."""
    text = redact(value)
    text = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|password|passwd|secret)\s*[:=]\s*)[^\s,;]+',
                  r'\1[凭据已隐藏]', text)
    text = re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@', r'\1[凭据已隐藏]@', text)
    text = re.sub(r'(?i)(?:file://)?(?:[a-z]:[\\/]|/(?:home|Users|root|tmp|var|etc|mnt|media|opt)/)[^\s<>"\x27]+',
                  '[本机路径已隐藏]', text)
    return text


def _threads(task):
    result = set()
    for binding in task.get('bindings', []):
        if isinstance(binding, dict) and binding.get('kind') == 'codex' and not binding.get('demo'):
            value = binding.get('url', '')
            if not isinstance(value, str):
                continue
            parsed = urlparse(value)
            if parsed.scheme == 'codex' and parsed.netloc == 'threads':
                result.add(parsed.path.strip('/'))
    return result


def _issue(task):
    issue = task.get('linear_issue', {})
    if not isinstance(issue, dict) or not valid_uuid(issue.get('id')):
        return None
    return issue


def _source_reports(store):
    """Read one report at a time without retaining every raw chat group in RAM."""
    after = ''
    while True:
        with store.lock:
            row = store.db.execute("SELECT day,content FROM reports WHERE day>? AND state IN ('done','pending') ORDER BY day LIMIT 1",
                                   (after,)).fetchone()
        if row is None:
            return
        after = row[0]
        yield row


class JournalExporter:
    def __init__(self, daily_service, tasks, employee_id, name, department=''):
        if not valid_uuid(employee_id):
            raise ValueError('员工共享身份无效')
        self.daily_service = daily_service
        self.tasks = tasks
        self.employee_id = employee_id
        self.name = name
        self.department = department

    def records(self, shared_task_ids):
        """Project each source report onto one currently authorized work issue."""
        if getattr(self.daily_service, 'busy', False):
            raise ValueError('日报正在更新，请稍后同步，保留评估端上次副本')
        logs = getattr(self.daily_service, 'work_logs', None)
        def source_version():
            task_revision = getattr(self.tasks, 'revision', None)
            return (self.daily_service.store.get('report_revision', 0),
                    logs.store.get('report_revision', 0) if logs else None,
                    copy.deepcopy(task_revision()) if callable(task_revision) else None)
        original_version = source_version()
        if (not isinstance(shared_task_ids, (list, set, tuple)) or
                any(not isinstance(item, str) for item in shared_task_ids)):
            raise ValueError('共享任务范围无效')
        shared = set(shared_task_ids)
        # TaskClient.tasks already returns a service-owned snapshot. Test adapters
        # and repositories are copied so no mutable object escapes the exporter.
        current = copy.deepcopy(self.tasks.tasks)
        by_id = {t['id']: t for t in current if isinstance(t, dict) and isinstance(t.get('id'), str)}
        counts = Counter(i['id'] for t in current if isinstance(t, dict) and (i := _issue(t)))
        allowed = {tid: t for tid, t in by_id.items() if tid in shared and t.get('category') == 'work'
                   and not t.get('is_sample') and (issue := _issue(t)) and counts[issue['id']] == 1}
        if not allowed:
            return []
        stores = [('nightly', self.daily_service.store)]
        work_logs = getattr(self.daily_service, 'work_logs', None)
        if work_logs is not None:
            stores.append(('manual', work_logs.store))
        result = []
        total_bytes = 0
        for label, store in stores:
            # A locked row snapshot, rather than enumerating files or copying DBs.
            for source, raw in _source_reports(store):
                if label == 'manual' and source == getattr(work_logs, 'active', None):
                    continue
                content = json.loads(raw)
                if not isinstance(content, dict):
                    continue
                kind = content.get('kind') if label == 'manual' else 'nightly'
                if kind not in ('nightly', 'today', 'week'):
                    continue
                source_id = kind + ':' + source
                historical = {t['id']: t for t in content.get('tasks', [])
                              if isinstance(t, dict) and isinstance(t.get('id'), str)}
                mapping = content.get('mapping', {})
                summaries = content.get('summaries', {})
                if not isinstance(mapping, dict) or not isinstance(summaries, dict):
                    continue
                start, end = content.get('start'), content.get('end')
                try:
                    if timestamp(start) >= timestamp(end):
                        continue
                except ValueError:
                    continue
                for task_id, task in allowed.items():
                    old = historical.get(task_id)
                    if not old or old.get('category') != 'work' or old.get('is_sample'):
                        continue
                    issue = _issue(task)
                    old_issue = _issue(old)
                    if old_issue and old_issue['id'] != issue['id']:
                        continue
                    old_links = {b.get('url') for b in old.get('bindings', [])
                                 if isinstance(b, dict) and b.get('kind') == 'linear' and not b.get('demo')}
                    if old_links and old_links != {issue.get('url')}:
                        continue
                    text = []
                    gaps = []
                    relevant = [thread for thread, ids in mapping.items()
                                if isinstance(ids, list) and task_id in ids]
                    for thread in relevant:
                        # A mixed chat is never separable by authorized task list.
                        if any(not isinstance(tid, str) for tid in mapping[thread]):
                            gaps.append('聊天归属记录无效，已排除其摘要')
                            continue
                        assignments = set(mapping[thread])
                        assignments.update(t['id'] for t in current if thread in _threads(t))
                        assignments.update(t['id'] for t in historical.values() if thread in _threads(t))
                        if assignments != {task_id}:
                            gaps.append('共享聊天涉及多个任务，已排除其摘要')
                            continue
                        if thread not in _threads(task):
                            gaps.append('聊天已解除任务关联，未共享其摘要')
                            continue
                        summary = summaries.get(thread)
                        if not isinstance(summary, str) or not summary.strip():
                            gaps.append('源日志的聊天摘要尚未生成')
                            continue
                        text.append(_safe_text(summary))
                    evidence = []
                    seen = set()
                    for row in content.get('evidence', []):
                        if (not isinstance(row, dict) or row.get('task_id') != task_id or
                                row.get('category') != 'work' or row.get('id') in seen):
                            continue
                        item = {key: _safe_text(row.get(key, '')) for key in
                                ('id', 'kind', 'text', 'source', 'created_at')}
                        if row.get('retracted_at'):
                            item['retracted_at'] = row['retracted_at']
                        seen.add(row.get('id'))
                        evidence.append(item)
                    if not relevant and not evidence:
                        continue
                    if gaps:
                        text.append('摘要缺口：' + '；'.join(dict.fromkeys(gaps)) + '。')
                    if not text:
                        text = ['摘要缺口：本记录仅包含人工进度证据，未提供聊天摘要。']
                    parent = by_id.get(task.get('parent_id'), {})
                    parent_issue = _issue(parent)
                    record = dict(
                        id=record_id(source_id, task_id), employee_id=self.employee_id,
                        name=_safe_text(self.name), department=_safe_text(self.department),
                        source_device=self.employee_id, source_id=source_id, kind=kind,
                        period_start=start, period_end=end, task_id=task_id,
                        task_title=_safe_text(task['title']), issue_id=issue['id'],
                        issue_identifier=issue.get('identifier', ''), issue_url=issue.get('url', ''),
                        parent_issue_id=parent_issue['id'] if parent_issue and parent.get('id') in allowed else '',
                        linear_state_type=issue.get('state_type', ''), summary='\n\n'.join(text),
                        evidence=evidence)
                    # Only Linear's explicit, current assignee is an ownership
                    # signal. The journal author and API viewer are not one.
                    if valid_uuid(issue.get('assignee_id')):
                        record['linear_assignee_id'] = issue['assignee_id']
                    record['digest'] = record_digest(record)
                    # A malformed source record is not a reason to export its raw
                    # replacement or bypass the common receiving-side validator.
                    try:
                        validate_record(record)
                    except ValueError:
                        continue
                    total_bytes += len(json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
                    if total_bytes > MAX_SNAPSHOT_BYTES:
                        raise ValueError('授权日志超过 32 MiB，请先缩小共享范围；未发送不完整清单')
                    result.append(record)
                    if len(result) > MAX_BATCH:
                        # A truncated manifest would wrongly revoke omitted logs.
                        raise ValueError('授权日志超过 10000 条，请先缩小共享范围；未发送不完整清单')
        if getattr(self.daily_service, 'busy', False) or source_version() != original_version:
            raise ValueError('日志或任务在导出期间更新，请重新同步；未发送部分清单')
        return sorted(result, key=lambda row: (timestamp(row['period_end']), row['id']), reverse=True)


class ReplicaStore:
    """The evaluator's latest records and immutable revision history."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.folder / 'replicas.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS employees (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, department TEXT NOT NULL,
            last_synced TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS records (
            employee_id TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL,
            period_end TEXT NOT NULL, content TEXT NOT NULL, received_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(employee_id,id));
          CREATE TABLE IF NOT EXISTS revisions (
            employee_id TEXT NOT NULL, record_id TEXT NOT NULL, digest TEXT NOT NULL,
            content TEXT NOT NULL, received_at TEXT NOT NULL,
            PRIMARY KEY(employee_id,record_id,digest));
        ''')
        if 'active' not in {row[1] for row in self.db.execute('PRAGMA table_info(records)')}:
            self.db.execute('ALTER TABLE records ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
        self.db.commit()

    def _validated_batch(self, employee_id, records):
        if not valid_uuid(employee_id) or not isinstance(records, list) or len(records) > MAX_BATCH:
            raise ValueError('共享日志批次无效')
        # Detach caller mutations and validate the entire batch before any write.
        detached = []
        total_bytes = 0
        seen = set()
        identity = None
        for record in records:
            record = copy.deepcopy(record)
            validate_record(record)
            total_bytes += len(json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            if total_bytes > MAX_SNAPSHOT_BYTES:
                raise ValueError('日志同步批次超过 32 MiB，请缩小共享范围')
            if record['employee_id'] != employee_id or record['id'] in seen:
                raise ValueError('共享日志身份不一致或同批记录重复')
            seen.add(record['id'])
            employee = (record['name'], record['department'])
            if identity is not None and identity != employee:
                raise ValueError('同批员工资料不一致')
            identity = employee
            detached.append(record)
        return detached, identity

    def _ingest(self, employee_id, records, identity, now):
        result = dict(created=0, updated=0, unchanged=0)
        if identity:
            self.db.execute('''INSERT INTO employees(id,name,department,last_synced,error) VALUES(?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET name=excluded.name,department=excluded.department''',
                            (employee_id, *identity, '', ''))
        for record in records:
            old = self.db.execute('SELECT digest FROM records WHERE employee_id=? AND id=?',
                                  (employee_id, record['id'])).fetchone()
            if old and old[0] == record['digest']:
                self.db.execute('UPDATE records SET active=1 WHERE employee_id=? AND id=?',
                                (employee_id, record['id']))
                result['unchanged'] += 1
                continue
            content = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            self.db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?,?)',
                            (employee_id, record['id'], record['digest'], content, now))
            self.db.execute('''INSERT OR REPLACE INTO records
              (employee_id,id,digest,period_end,content,received_at,active) VALUES(?,?,?,?,?,?,1)''',
                            (employee_id, record['id'], record['digest'], record['period_end'], content, now))
            result['updated' if old else 'created'] += 1
        return result

    def ingest(self, employee_id, records):
        records, identity = self._validated_batch(employee_id, records)
        with self.lock, self.db:
            return self._ingest(employee_id, records, identity, datetime.now(timezone.utc).isoformat())

    def records(self, employee_id=None):
        if employee_id is not None and not valid_uuid(employee_id):
            raise ValueError('员工共享身份无效')
        with self.lock:
            rows = self.db.execute('SELECT content FROM records WHERE active=1' + (' AND employee_id=?' if employee_id else ''),
                                   (employee_id,) if employee_id else ()).fetchall()
        result = [json.loads(row[0]) for row in rows]
        return sorted(result, key=lambda row: (timestamp(row['period_end']), row['id']), reverse=True)

    def employees(self):
        with self.lock:
            rows = self.db.execute('''SELECT e.id,e.name,e.department,e.last_synced,e.error,COUNT(r.id)
              FROM employees e LEFT JOIN records r ON r.employee_id=e.id AND r.active=1
              GROUP BY e.id ORDER BY e.name,e.id''').fetchall()
        return [dict(id=i, name=n, department=d, record_count=c, last_synced=s, error=e)
                for i, n, d, s, e, c in rows]

    def _validated_manifest(self, employee_id, entries):
        if not valid_uuid(employee_id) or not isinstance(entries, list) or len(entries) > MAX_BATCH:
            raise ValueError('共享日志清单无效')
        desired = {}
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != {'id', 'digest'} or
                    any(not isinstance(entry.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', entry[key])
                        for key in ('id', 'digest')) or entry['id'] in desired):
                raise ValueError('共享日志清单重复或校验字段无效')
            desired[entry['id']] = entry['digest']
        return desired

    def _apply_manifest(self, employee_id, desired):
        for identifier, digest in desired.items():
            row = self.db.execute('SELECT digest FROM records WHERE employee_id=? AND id=?',
                                  (employee_id, identifier)).fetchone()
            if row is None or row[0] != digest:
                raise ValueError('共享日志清单尚未完整下载，保持现有可见范围')
        self.db.execute('UPDATE records SET active=0 WHERE employee_id=?', (employee_id,))
        self.db.executemany('UPDATE records SET active=1 WHERE employee_id=? AND id=? AND digest=?',
                            [(employee_id, identifier, digest) for identifier, digest in desired.items()])

    def apply_manifest(self, employee_id, entries):
        """Hide revoked records after a complete, successfully fetched manifest.

        A missing payload rejects the entire operation. An offline/error response
        must never be passed as an empty manifest. This method deletes no history.
        """
        desired = self._validated_manifest(employee_id, entries)
        with self.lock, self.db:
            self._apply_manifest(employee_id, desired)

    def _validate_employee(self, employee_id, name, department, error=''):
        if not valid_uuid(employee_id):
            raise ValueError('员工共享身份无效')
        if (not isinstance(name, str) or not name.strip() or len(name) > 120 or
                not isinstance(department, str) or len(department) > 160 or
                not isinstance(error, str) or len(error) > 2000):
            raise ValueError('员工同步信息无效')
    def _mark_sync(self, employee_id, name, department, error, now):
        self.db.execute('''INSERT INTO employees(id,name,department,last_synced,error) VALUES(?,?,?,?,?)
          ON CONFLICT(id) DO UPDATE SET name=excluded.name,department=excluded.department,
            last_synced=CASE WHEN excluded.last_synced='' THEN employees.last_synced ELSE excluded.last_synced END,
            error=excluded.error''', (employee_id, _safe_text(name), _safe_text(department), now, _safe_text(error)))

    def mark_sync(self, employee_id, name, department, error=''):
        self._validate_employee(employee_id, name, department, error)
        now = datetime.now(timezone.utc).isoformat() if not error else ''
        with self.lock, self.db:
            self._mark_sync(employee_id, name, department, error, now)

    def sync(self, employee_id, records, manifest, name, department):
        """Atomically publish a complete sync, its authorized scope and timestamp."""
        records, identity = self._validated_batch(employee_id, records)
        desired = self._validated_manifest(employee_id, manifest)
        self._validate_employee(employee_id, name, department)
        if any(desired.get(row['id']) != row['digest'] for row in records):
            raise ValueError('下载记录不在当前完整共享清单中')
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            result = self._ingest(employee_id, records, identity, now)
            self._apply_manifest(employee_id, desired)
            self._mark_sync(employee_id, name, department, '', now)
        return result

    def suspend(self, employee_id, error):
        """Hide cached records on explicit authentication refusal, retaining history."""
        if (not valid_uuid(employee_id) or not isinstance(error, str) or
                not error.strip() or len(error) > 2000):
            raise ValueError('员工授权暂停信息无效')
        with self.lock, self.db:
            self.db.execute('UPDATE records SET active=0 WHERE employee_id=?', (employee_id,))
            self.db.execute('UPDATE employees SET error=? WHERE id=?', (_safe_text(error), employee_id))

    def close(self):
        with self.lock:
            self.db.close()
