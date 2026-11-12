"""Explicit people, source devices and Linear-account identities for evaluation.

Names are labels, never identity keys. A source record's initial device owner is
fixed separately, so transferring a device cannot transfer an employee's history.
"""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from evaluation.insights import RevisionConflict
from evaluation.protocol import valid_uuid


def _text(value, label, maximum, required=False):
    if (not isinstance(value, str) or len(value) > maximum or
            any(ord(c) < 32 for c in value) or required and not value.strip()):
        raise ValueError(label + '格式无效')
    return value.strip()


def _uuid(value, label):
    if not valid_uuid(value):
        raise ValueError(label + '须为小写标准 UUID')
    return value


def _workspace(value):
    value = _text(value, 'Linear 工作空间', 100, True).lower()
    if (not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?', value) or
            value.startswith(('lin-api-', 'sk-', 'github-pat-'))):
        raise ValueError('请填写 Linear URL 中的工作空间 slug，不能填写 URL 或凭据')
    return value


def _record_keys(records):
    if not isinstance(records, list):
        raise ValueError('日志归属记录格式无效')
    keys = set()
    for record in records:
        if (not isinstance(record, dict) or not valid_uuid(record.get('source_device')) or
                not isinstance(record.get('id'), str) or not re.fullmatch(r'[0-9a-f]{64}', record['id'])):
            raise ValueError('日志来源设备或记录 ID 无效')
        keys.add((record['source_device'], record['id']))
    return sorted(keys)


class RosterStore:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.folder / 'roster.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS roster_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
          INSERT OR IGNORE INTO roster_meta VALUES ('revision',0);
          CREATE TABLE IF NOT EXISTS roster_employees (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, department TEXT NOT NULL, active INTEGER NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS roster_devices (
            device_id TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES roster_employees(id),
            label TEXT NOT NULL, bound_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS roster_accounts (
            id TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES roster_employees(id),
            workspace TEXT NOT NULL, user_id TEXT NOT NULL, display_name TEXT NOT NULL,
            UNIQUE(workspace,user_id));
          CREATE TABLE IF NOT EXISTS roster_claims (
            source_device TEXT NOT NULL, record_id TEXT NOT NULL,
            employee_id TEXT NOT NULL REFERENCES roster_employees(id), claimed_at TEXT NOT NULL,
            PRIMARY KEY(source_device,record_id));
          CREATE TABLE IF NOT EXISTS roster_history (revision INTEGER PRIMARY KEY, content TEXT NOT NULL);
        ''')
        self.db.commit()

    def _revision(self):
        return self.db.execute("SELECT value FROM roster_meta WHERE key='revision'").fetchone()[0]

    def _check(self, expected):
        current = self._revision()
        if expected is not None and (type(expected) is not int or expected < 0 or expected != current):
            raise RevisionConflict('员工名册已更新，请刷新后重试')
        return current

    def _employee(self, employee_id):
        row = self.db.execute('SELECT id,name,department,active,created_at,updated_at FROM roster_employees WHERE id=?',
                              (employee_id,)).fetchone()
        if row is None:
            raise ValueError('名册人员不存在，请先明确创建人员')
        result = dict(zip(('id', 'name', 'department', 'active', 'created_at', 'updated_at'), row))
        result['active'] = bool(result['active'])
        return result

    def _event(self, kind, before, after, stamp):
        revision = self._revision() + 1
        event = dict(revision=revision, type=kind, created_at=stamp,
                     before=copy.deepcopy(before), after=copy.deepcopy(after))
        self.db.execute('INSERT INTO roster_history VALUES(?,?)',
                        (revision, json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'))))
        self.db.execute("UPDATE roster_meta SET value=? WHERE key='revision'", (revision,))

    def snapshot(self):
        with self.lock, self.db:
            self.db.execute('BEGIN')
            result = self._snapshot()
        return result

    def _snapshot(self):
        employees = []
        for row in self.db.execute('SELECT id,name,department,active,created_at,updated_at FROM roster_employees ORDER BY name,id'):
            value = dict(zip(('id', 'name', 'department', 'active', 'created_at', 'updated_at'), row))
            value['active'] = bool(value['active'])
            employees.append(value)
        devices = [dict(zip(('device_id', 'employee_id', 'label', 'bound_at'), row)) for row in
                   self.db.execute('SELECT device_id,employee_id,label,bound_at FROM roster_devices ORDER BY device_id')]
        accounts = [dict(zip(('id', 'employee_id', 'workspace', 'user_id', 'display_name'), row)) for row in
                    self.db.execute('SELECT id,employee_id,workspace,user_id,display_name FROM roster_accounts ORDER BY workspace,user_id')]
        history = [json.loads(row[0]) for row in self.db.execute('SELECT content FROM roster_history ORDER BY revision')]
        return dict(revision=self._revision(), employees=employees, devices=devices, accounts=accounts, history=history)

    def save_employee(self, name, department='', active=True, employee_id='', expected_revision=None):
        name = _text(name, '姓名', 120, True)
        department = _text(department, '部门', 160)
        if type(active) is not bool:
            raise ValueError('人员启用状态须为布尔值')
        if employee_id:
            _uuid(employee_id, '人员 ID')
        elif employee_id != '':
            raise ValueError('人员 ID 无效')
        stamp = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._check(expected_revision)
            before = self._employee(employee_id) if employee_id else None
            identifier = employee_id or str(uuid.uuid4())
            after = dict(id=identifier, name=name, department=department, active=active,
                         created_at=before['created_at'] if before else stamp, updated_at=stamp)
            self.db.execute('''INSERT INTO roster_employees VALUES(?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET name=excluded.name,department=excluded.department,
                active=excluded.active,updated_at=excluded.updated_at''',
                            (identifier, name, department, int(active), after['created_at'], stamp))
            self._event('employee_updated' if before else 'employee_created', before, after, stamp)
            result = self._snapshot()
        return result

    def bind_device(self, device_id, employee_id, label='', expected_revision=None):
        _uuid(device_id, '设备 ID')
        _uuid(employee_id, '人员 ID')
        label = _text(label, '设备名称', 160)
        stamp = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._check(expected_revision)
            self._employee(employee_id)
            old = self.db.execute('SELECT device_id,employee_id,label,bound_at FROM roster_devices WHERE device_id=?',
                                  (device_id,)).fetchone()
            before = dict(zip(('device_id', 'employee_id', 'label', 'bound_at'), old)) if old else None
            after = dict(device_id=device_id, employee_id=employee_id, label=label, bound_at=stamp)
            self.db.execute('INSERT OR REPLACE INTO roster_devices VALUES(?,?,?,?)',
                            (device_id, employee_id, label, stamp))
            self._event('device_rebound' if before and before['employee_id'] != employee_id else 'device_bound',
                        before, after, stamp)
            result = self._snapshot()
        return result

    def unbind_device(self, device_id, expected_revision=None):
        _uuid(device_id, '设备 ID')
        stamp = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._check(expected_revision)
            old = self.db.execute('SELECT device_id,employee_id,label,bound_at FROM roster_devices WHERE device_id=?',
                                  (device_id,)).fetchone()
            if old is None:
                raise ValueError('设备尚未绑定名册人员')
            before = dict(zip(('device_id', 'employee_id', 'label', 'bound_at'), old))
            self.db.execute('DELETE FROM roster_devices WHERE device_id=?', (device_id,))
            self._event('device_unbound', before, None, stamp)
            result = self._snapshot()
        return result

    def save_account(self, employee_id, workspace, user_id, display_name='', account_id='', expected_revision=None):
        _uuid(employee_id, '人员 ID')
        _uuid(user_id, 'Linear 用户 ID')
        workspace = _workspace(workspace)
        display_name = _text(display_name, 'Linear 显示名称', 160)
        if account_id:
            _uuid(account_id, '账号 ID')
        elif account_id != '':
            raise ValueError('账号 ID 无效')
        stamp = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._check(expected_revision)
            self._employee(employee_id)
            old = self.db.execute('SELECT id,employee_id,workspace,user_id,display_name FROM roster_accounts WHERE id=?',
                                  (account_id,)).fetchone() if account_id else None
            if account_id and old is None:
                raise ValueError('Linear 账号记录不存在')
            before = dict(zip(('id', 'employee_id', 'workspace', 'user_id', 'display_name'), old)) if old else None
            identifier = account_id or str(uuid.uuid4())
            duplicate = self.db.execute('SELECT id,employee_id FROM roster_accounts WHERE workspace=? AND user_id=?',
                                        (workspace, user_id)).fetchone()
            if duplicate and duplicate[0] != identifier:
                raise ValueError('此 Linear 工作空间和用户 ID 已归属名册人员，不能重复或隐式转移')
            after = dict(id=identifier, employee_id=employee_id, workspace=workspace,
                         user_id=user_id, display_name=display_name)
            self.db.execute('''INSERT INTO roster_accounts VALUES(?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET employee_id=excluded.employee_id,workspace=excluded.workspace,
                user_id=excluded.user_id,display_name=excluded.display_name''',
                            (identifier, employee_id, workspace, user_id, display_name))
            self._event('account_updated' if before else 'account_created', before, after, stamp)
            result = self._snapshot()
        return result

    def remove_account(self, account_id, expected_revision=None):
        _uuid(account_id, '账号 ID')
        stamp = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._check(expected_revision)
            old = self.db.execute('SELECT id,employee_id,workspace,user_id,display_name FROM roster_accounts WHERE id=?',
                                  (account_id,)).fetchone()
            if old is None:
                raise ValueError('Linear 账号记录不存在')
            before = dict(zip(('id', 'employee_id', 'workspace', 'user_id', 'display_name'), old))
            self.db.execute('DELETE FROM roster_accounts WHERE id=?', (account_id,))
            self._event('account_removed', before, None, stamp)
            result = self._snapshot()
        return result

    def claim_records(self, records):
        keys = _record_keys(records)
        result = dict(claimed=0, unchanged=0, unbound=0)
        stamp = datetime.now(timezone.utc).isoformat()
        claimed = []
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            devices = dict(self.db.execute('SELECT device_id,employee_id FROM roster_devices'))
            for device, identifier in keys:
                owner = self.db.execute('SELECT employee_id FROM roster_claims WHERE source_device=? AND record_id=?',
                                        (device, identifier)).fetchone()
                if owner:
                    result['unchanged'] += 1
                elif device not in devices:
                    result['unbound'] += 1
                else:
                    employee = devices[device]
                    self.db.execute('INSERT INTO roster_claims VALUES(?,?,?,?)', (device, identifier, employee, stamp))
                    claimed.append(dict(source_device=device, id=identifier, employee_id=employee))
                    result['claimed'] += 1
            if claimed:
                self._event('records_claimed', None, claimed, stamp)
        return result

    def record_owners(self, records):
        keys = _record_keys(records)
        result = {}
        with self.lock, self.db:
            self.db.execute('BEGIN')
            for key in keys:
                owner = self.db.execute('SELECT employee_id FROM roster_claims WHERE source_device=? AND record_id=?', key).fetchone()
                if owner:
                    result[key] = owner[0]
        return result

    def close(self):
        with self.lock:
            self.db.close()
