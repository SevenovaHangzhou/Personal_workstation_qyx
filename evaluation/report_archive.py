"""Explicitly granted, read-only complete reports, separate from issue evidence."""
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone

CHUNK = 256 * 1024
MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
MAX_FILES = 3000
DAY = r'\d{4}-\d{2}-\d{2}'
LOG = r'(today|week)-\d{8}-\d{6}-[0-9a-f]{8}'


def report_path(source):
    if isinstance(source, str) and re.fullmatch(DAY + r'\.md', source):
        return '日报/自动/' + source
    match = re.fullmatch(r'work-logs/(' + LOG + r')\.md', source or '')
    if match:
        return ('周报/' if match[2] == 'week' else '日报/手动/') + source.split('/')[-1]
    raise ValueError('仅允许已生成的日报、周报 Markdown 文件')


def source_path(name):
    if not isinstance(name, str):
        raise ValueError('报告文件名无效')
    source = name.rsplit('/', 1)[-1]
    if not name.startswith('日报/自动/'):
        source = 'work-logs/' + source
    if report_path(source) != name:
        raise ValueError('报告路径不在允许范围')
    return source


def safe_file(root, name):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('报告目录不能是链接')
    parts = name.split('/')
    if any(not part or part in ('.', '..') or '\\' in part or ':' in part for part in parts):
        raise ValueError('报告路径无效')
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('报告文件不能是链接')
    path.resolve().relative_to(root.resolve())
    return path


def version(files):
    return hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def manifest(value):
    if not isinstance(value, dict) or set(value) != {'version', 'files'}:
        raise ValueError('完整报告清单格式无效')
    files = value['files']
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ValueError('完整报告文件数量超过限制')
    seen, total = set(), 0
    for row in files:
        if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256'}:
            raise ValueError('完整报告文件格式无效')
        source_path(row['path'])
        if row['path'] in seen or type(row['bytes']) is not int or not 0 <= row['bytes'] <= MAX_FILE:
            raise ValueError('完整报告重复或超过大小限制')
        if not isinstance(row['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', row['sha256']):
            raise ValueError('完整报告校验值无效')
        seen.add(row['path'])
        total += row['bytes']
    if total > MAX_TOTAL or files != sorted(files, key=lambda row: row['path']) or value['version'] != version(files):
        raise ValueError('完整报告总大小、顺序或清单校验无效')
    return value


def completed_reports(daily):
    """Use ended ledger rows, never a recursive directory scan or recent-60 UI list."""
    if getattr(daily, 'busy', False):
        raise ValueError('日报正在更新，请稍后收集；评估端保留上次副本')
    paths = []
    with daily.store.lock:
        rows = daily.store.db.execute("SELECT day FROM reports WHERE state IN ('done','pending')").fetchall()
    for row in rows:
        name = row[0] + '.md'
        if re.fullmatch(DAY + r'\.md', name):
            paths.append(name)
    logs = getattr(daily, 'work_logs', None)
    if logs:
        with logs.lock:
            active = logs.active
            with logs.store.lock:
                rows = logs.store.db.execute("SELECT day FROM reports WHERE state IN ('done','pending')").fetchall()
        for row in rows:
            if row[0] != active and re.fullmatch(LOG, row[0]):
                paths.append('work-logs/' + row[0] + '.md')
    return sorted(paths)


class ReportArchive:
    def __init__(self, daily):
        self.daily = daily
        self.root = daily.store.folder
        self.lock = threading.RLock()
        self.current = None

    def query(self, operation, payload):
        with self.lock:
            if operation == 'report_manifest':
                if payload:
                    raise ValueError('报告清单不接受其他参数')
                files = []
                total = 0
                for source in completed_reports(self.daily):
                    path = safe_file(self.root, source)
                    if not path.exists():
                        continue
                    if not path.is_file() or path.stat().st_size > MAX_FILE:
                        raise ValueError('报告文件超过限制')
                    raw = path.read_bytes()
                    total += len(raw)
                    if len(raw) > MAX_FILE or total > MAX_TOTAL or len(files) >= MAX_FILES:
                        raise ValueError('完整报告超过共享限制，未发送部分清单')
                    raw.decode('utf-8')
                    files.append(dict(path=report_path(source), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
                files.sort(key=lambda row: row['path'])
                self.current = manifest(dict(files=files, version=version(files)))
                return self.current
            if operation != 'report' or set(payload) != {'version', 'path', 'offset'}:
                raise ValueError('报告读取参数无效')
            name = payload['path']
            source = source_path(name)
            row = next((row for row in (self.current or {}).get('files', []) if row['path'] == name), None)
            if not row or payload['version'] != self.current['version'] or source not in completed_reports(self.daily):
                raise ValueError('报告清单已变化，请重新收集')
            offset = payload['offset']
            if type(offset) is not int or not 0 <= offset < row['bytes']:
                raise ValueError('报告读取位置无效')
            path = safe_file(self.root, source)
            if not path.is_file() or path.stat().st_size != row['bytes']:
                raise ValueError('报告已变化，请重新收集')
            with path.open('rb') as stream:
                stream.seek(offset)
                raw = stream.read(CHUNK)
            return dict(data=base64.b64encode(raw).decode('ascii'))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    pending = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        pending.chmod(0o600)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


class CollectedReports:
    """Verified immutable copies. Complete report text is never issue/AI evidence."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder / 'index.json'
        self.lock = threading.RLock()
        self.rows = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.rows))

    def suspend(self, employee, message):
        with self.lock:
            row = self.rows.setdefault(employee, {})
            row.update(available=False, notice=message)
            write_json(self.path, self.rows)

    def mark_error(self, employee, message):
        with self.lock:
            self.rows.setdefault(employee, dict(available=False))['notice'] = message
            write_json(self.path, self.rows)

    def pull(self, employee, transport, cancel, allowed):
        # Device UUID and hash are the only directory names not from a strict whitelist.
        if str(uuid.UUID(employee)) != employee:
            raise ValueError('员工设备标识无效')
        value = manifest(transport.report_manifest())
        with self.lock:
            old = dict(self.rows.get(employee, {}))
        target = self.folder / employee / value['version']
        pending = target.with_name('.' + value['version'] + '.' + uuid.uuid4().hex)
        pending.mkdir(parents=True, mode=0o700)
        try:
            for row in value['files']:
                if cancel.is_set() or not allowed():
                    raise InterruptedError('完整报告收集已停止')
                path = safe_file(pending, row['path'])
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                prior = next((r for r in old.get('files', []) if r == row), None)
                cached = safe_file(self.folder / employee / old['version'], row['path']) if prior else None
                if cached and cached.is_file() and cached.stat().st_size == row['bytes'] and hashlib.sha256(cached.read_bytes()).hexdigest() == row['sha256']:
                    shutil.copyfile(cached, path)
                else:
                    with path.open('xb') as stream:
                        offset = 0
                        while offset < row['bytes']:
                            if cancel.is_set() or not allowed():
                                raise InterruptedError('完整报告收集已停止')
                            result = transport.report(value['version'], row['path'], offset)
                            try:
                                raw = base64.b64decode(result['data'], validate=True)
                            except (ValueError, TypeError, binascii.Error):
                                raise ValueError('完整报告编码无效') from None
                            if not 0 < len(raw) <= min(CHUNK, row['bytes'] - offset):
                                raise ValueError('完整报告分块大小无效')
                            stream.write(raw)
                            offset += len(raw)
                path.chmod(0o600)
                if path.stat().st_size != row['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
                    raise ValueError('完整报告校验失败，保留上次成功副本')
                path.read_text(encoding='utf-8')
            if cancel.is_set() or not allowed():
                raise InterruptedError('完整报告收集已停止')
            # Every pull re-verifies all adopted files, including the cached version.
            with self.lock:
                if target.exists():
                    # Replace via a staging directory only after successful verification.
                    previous = target.with_name('.old-' + uuid.uuid4().hex)
                    target.rename(previous)
                    try:
                        pending.rename(target)
                    except BaseException:
                        previous.rename(target)
                        raise
                    shutil.rmtree(previous)
                else:
                    pending.rename(target)
                self.rows[employee] = dict(value, available=True, notice='完整报告已收集（单独授权，不参与 issue 统计）',
                    collected_at=datetime.now(timezone.utc).isoformat())
                write_json(self.path, self.rows)
        finally:
            shutil.rmtree(pending, ignore_errors=True)

    def text(self, employee, name):
        source_path(name)
        with self.lock:
            value = self.rows.get(employee, {})
            if not value.get('available'):
                raise ValueError('完整报告尚未授权、已撤销或未成功收集')
            row = next((row for row in value['files'] if row['path'] == name), None)
            if not row:
                raise ValueError('报告不在当前收集清单')
            path = safe_file(self.folder / employee / value['version'], name)
            if not path.is_file() or path.stat().st_size != row['bytes']:
                raise ValueError('本地报告副本不完整，请重新同步')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('本地报告校验失败，请重新同步')
            return raw.decode('utf-8')
