"""Deterministic log exports from immutable result drafts, never raw chat scraping."""
import base64
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import threading

from evaluation.protocol import record_digest, record_id, validate_record, valid_uuid
from evaluation.report_archive import CHUNK, manifest, version, write_json
from evaluation.shared_store import file_manifest
from .core import safe_text

STANDARD = Path(__file__).resolve().parents[1] / '日志统计标准.md'
TITLES = [('已完成', 'completed'), ('代码变动', 'changes'), ('测试与证据', 'tests'),
          ('尚未完成', 'remaining'), ('阻碍', 'blockers'), ('下一步', 'next_step')]


def log_text(value):
    value = safe_text(value)
    value = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|password|passwd|secret)\s*[:=]\s*)[^\s,;]+',
                   r'\1[凭据已隐藏]', value)
    value = re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@', r'\1[凭据已隐藏]@', value)
    return re.sub(r'(?i)(?:file://)?(?:[a-z]:[\\/]|/(?:home|Users|root|tmp|var|etc|mnt|media|opt)/)[^\s<>"\x27]+',
                  '[本机路径已隐藏]', value)


def result_log(row):
    snapshot = json.loads(row['snapshot'])
    task, result = snapshot['task'], snapshot['result']
    issue = task['issue']
    return dict(format='orbit-work-log/v1', result_id=row['id'], task_id=task['id'],
                issue_id=task['issue_id'], issue_identifier=safe_text(issue['identifier']),
                issue_title=log_text(issue['title']), issue_url=issue['url'], created_at=row['created_at'],
                result={key: log_text(result[key]) for key in ('status', *(key for _, key in TITLES))},
                publication=dict(state=row['state'], comment_id=row['comment_id'], published_at=row['published_at']))


def markdown(log):
    lines = [f"# {log['issue_identifier']} · {log['issue_title']}", '',
             '时间：' + log['created_at'], '结果 ID：' + log['result_id'], '任务 ID：' + log['task_id'],
             'Issue：' + log['issue_url'], '开发状态：' + log['result']['status'],
             '发布状态：' + log['publication']['state'], '', '开发状态是个人记录，不代表 Linear 完成或系统验收。', '']
    for title, key in TITLES:
        lines += ['## ' + title, log['result'][key] or '无', '']
    return '\n'.join(lines)


def rows(orbit, result_id=None):
    with orbit.db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM results WHERE id=?', (result_id,))] if result_id else [
            dict(r) for r in db.execute('SELECT * FROM results ORDER BY created_at,id')]


def export_local(orbit, result_id=None):
    """Rebuildable files. SQLite remains the canonical source if an export fails."""
    count = 0
    for row in rows(orbit, result_id):
        log = result_log(row)
        day = datetime.fromisoformat(log['created_at']).astimezone().date().isoformat()
        folder = orbit.directory / 'journals' / day
        for suffix, raw in (('.json', json.dumps(log, ensure_ascii=False, indent=2) + '\n'), ('.md', markdown(log))):
            path = folder / (log['result_id'] + suffix)
            # UUID and ISO date are application-owned, never remote path input.
            if path.is_symlink() or folder.is_symlink() or folder.parent.is_symlink():
                raise ValueError('日志导出路径不能是链接')
            if suffix == '.json':
                write_json(path, log)
            else:
                from evaluation.mesh import _private_write
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _private_write(path, raw.encode('utf-8'))
        count += 1
    return dict(count=count, directory=str(orbit.directory / 'journals'))


class JournalLibrary:
    def __init__(self, orbit, config):
        self.orbit, self.config = orbit, config
        self.lock = threading.RLock()

    def selected(self):
        settings = self.config()
        if not settings['enabled']:
            return settings, []
        chosen = set(settings['task_ids'])
        return settings, [row for row in rows(self.orbit) if json.loads(row['snapshot'])['task']['id'] in chosen]

    def files(self):
        settings, selected = self.selected()
        files = {'日志统计标准.md': STANDARD.read_bytes()}
        for row in selected:
            log = result_log(row)
            day = datetime.fromisoformat(log['created_at']).astimezone().date().isoformat()
            stem = f"日志/{day}/{log['result_id']}"
            files[stem + '.md'] = markdown(log).encode('utf-8')
            files[stem + '.json'] = (json.dumps(log, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if settings['enabled']:
            from .nightly import shared_files
            files.update(shared_files(self.orbit,settings['task_ids']))
        file_manifest(files)  # Fail closed on limits; do not silently truncate logs.
        return files

    def shared(self, operation, payload):
        with self.lock:
            files = self.files()
            current = file_manifest(files)
            if operation == 'shared_manifest' and payload == {}:
                return current
            if operation != 'shared_file' or set(payload) != {'version', 'path'}:
                raise ValueError('共享文件参数无效')
            if payload['version'] != current['version'] or payload['path'] not in files:
                raise ValueError('共享文件已变化或不在授权范围')
            return files[payload['path']]

    def reports(self, operation, payload):
        # Existing evaluator compatibility: complete reports remain separate evidence.
        files = self.files()
        days = {}
        for path, raw in sorted(files.items()):
            if path.startswith(('日志/', '日报/')) and path.endswith('.md'):
                days.setdefault(path.split('/')[1][:10], []).append(raw)
        reports = {'日报/自动/' + day + '.md': b'\n\n---\n\n'.join(parts) for day, parts in days.items()}
        import hashlib
        listing = [dict(path=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()) for name, raw in sorted(reports.items())]
        current = manifest(dict(files=listing, version=version(listing)))
        if operation == 'report_manifest' and payload == {}:
            return current
        if operation != 'report' or set(payload) != {'version', 'path', 'offset'}:
            raise ValueError('报告查询参数无效')
        offset = payload['offset']
        if (payload['version'] != current['version'] or payload['path'] not in reports or
                type(offset) is not int or not 0 <= offset < len(reports[payload['path']])):
            raise ValueError('报告版本或读取位置无效')
        return dict(data=base64.b64encode(reports[payload['path']][offset:offset + CHUNK]).decode('ascii'))

    def records(self):
        settings, selected = self.selected()
        records = []
        for row in selected:
            log, task = result_log(row), json.loads(row['snapshot'])['task']
            issue = task['issue']
            created = datetime.fromisoformat(log['created_at'])
            source_id = 'codex-result:' + row['id']
            summary = '\n\n'.join(title + '：' + (log['result'][key] or '无') for title, key in TITLES)
            evidence = [dict(id=row['id'] + ':' + key, kind='verification' if key == 'tests' else 'execution',
                             text=value, source=issue['url'], created_at=log['created_at'])
                        for _, key in TITLES if (value := log['result'][key])]
            record = dict(id=record_id(source_id, task['id']), employee_id=settings['employee_id'], name=settings['name'],
                department=settings['department'], source_device=settings['employee_id'], source_id=source_id, kind='today',
                period_start=created.isoformat(), period_end=(created + timedelta(microseconds=1)).isoformat(),
                task_id=task['id'], task_title=log_text(issue['title']), issue_id=task['issue_id'],
                issue_identifier=issue['identifier'], issue_url=issue['url'], parent_issue_id=issue.get('parent_id', '') or '',
                linear_state_type=issue.get('state_type', ''), summary=summary, evidence=evidence)
            if valid_uuid(issue.get('assignee_id')):
                record['linear_assignee_id'] = issue['assignee_id']
            record['digest'] = record_digest(record)
            records.append(validate_record(record))
        if settings['enabled']:
            from .nightly import shared_records
            records.extend(shared_records(self.orbit,settings))
        return records
