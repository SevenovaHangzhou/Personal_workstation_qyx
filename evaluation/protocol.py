"""Bounded, canonical wire records for employee-approved journal sharing."""
import hashlib
import json
import re
import uuid
from datetime import datetime
from urllib.parse import urlparse

PROTOCOL = 'orbit-evaluator/v1'
DISCOVERY_PORT = 45874
MAX_RECORD_BYTES = 200 * 1024
MAX_BATCH = 10000
FIELDS = frozenset(('id', 'employee_id', 'name', 'department', 'source_device',
                    'source_id', 'kind', 'period_start', 'period_end', 'task_id',
                    'task_title', 'issue_id', 'issue_identifier', 'issue_url',
                    'parent_issue_id', 'linear_state_type', 'summary', 'evidence', 'digest'))
OPTIONAL_FIELDS = frozenset(('linear_assignee_id',))
STATES = {'', 'backlog', 'unstarted', 'started', 'completed', 'canceled', 'triage'}


def canonical(record):
    """Return the exact UTF-8 payload covered by a record's SHA-256 digest."""
    if not isinstance(record, dict):
        raise ValueError('共享日志必须为对象')
    return json.dumps({k: v for k, v in record.items() if k != 'digest'},
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def record_digest(record):
    return hashlib.sha256(canonical(record)).hexdigest()


def record_id(source_id, task_id):
    return hashlib.sha256((source_id + '\0' + task_id).encode('utf-8')).hexdigest()


def valid_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _text(value, name, limit, required=False):
    if (not isinstance(value, str) or len(value) > limit or
            required and not value.strip() or any(ord(c) < 32 and c not in '\n\t' for c in value)):
        raise ValueError('共享日志字段无效：' + name)


def timestamp(value):
    _text(value, '时间', 64, True)
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('共享日志时间无效') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('共享日志时间必须带时区')
    return result


def validate_record(record):
    """Validate all fields, identities and digest; never accept an opaque extra field."""
    if (not isinstance(record, dict) or not FIELDS <= set(record) or
            set(record) - FIELDS - OPTIONAL_FIELDS):
        raise ValueError('共享日志字段不完整或包含未授权字段')
    for field in ('employee_id', 'source_device', 'issue_id'):
        if not valid_uuid(record[field]):
            raise ValueError('共享日志 UUID 无效：' + field)
    if record['source_device'] != record['employee_id']:
        raise ValueError('日志设备与员工身份不一致')
    if 'linear_assignee_id' in record and not valid_uuid(record['linear_assignee_id']):
        raise ValueError('Linear 负责人 UUID 无效')
    if record['parent_issue_id'] and not valid_uuid(record['parent_issue_id']):
        raise ValueError('父 issue UUID 无效')
    if record['parent_issue_id'] == record['issue_id']:
        raise ValueError('issue 不能作为自己的父项')
    for key, limit, required in (
            ('id', 64, True), ('name', 120, True), ('department', 160, False),
            ('source_id', 240, True), ('task_id', 128, True), ('task_title', 500, True),
            ('issue_identifier', 80, True), ('issue_url', 1000, True),
            ('linear_state_type', 24, False), ('summary', 100000, False), ('digest', 64, True)):
        _text(record[key], key, limit, required)
    if record['kind'] not in ('nightly', 'today', 'week'):
        raise ValueError('共享日志种类无效')
    if record['linear_state_type'] not in STATES:
        raise ValueError('Linear 状态种类无效')
    if timestamp(record['period_start']) >= timestamp(record['period_end']):
        raise ValueError('共享日志周期无效')
    parsed = urlparse(record['issue_url'])
    segments = parsed.path.strip('/').split('/')
    if (parsed.scheme != 'https' or parsed.netloc != 'linear.app' or parsed.query or parsed.fragment or
            len(segments) < 3 or segments[1] != 'issue' or segments[2] != record['issue_identifier'] or
            not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*-[0-9]+', record['issue_identifier'])):
        raise ValueError('Linear issue 编号与链接不一致')
    if record['id'] != record_id(record['source_id'], record['task_id']):
        raise ValueError('共享日志稳定 ID 无效')
    evidence = record['evidence']
    if not isinstance(evidence, list) or len(evidence) > 2000:
        raise ValueError('共享证据数量无效')
    seen = set()
    for item in evidence:
        if (not isinstance(item, dict) or not {'id', 'kind', 'text', 'source', 'created_at'} <= set(item)
                or set(item) - {'id', 'kind', 'text', 'source', 'created_at', 'retracted_at'}):
            raise ValueError('共享证据字段无效')
        for key, limit, required in (('id', 100, True), ('kind', 30, True),
                                     ('text', 6000, True), ('source', 1000, False)):
            _text(item[key], 'evidence.' + key, limit, required)
        if item['id'] in seen or item['kind'] not in ('plan', 'execution', 'verification', 'acceptance'):
            raise ValueError('共享证据重复或类型无效')
        seen.add(item['id'])
        created = timestamp(item['created_at'])
        if 'retracted_at' in item and timestamp(item['retracted_at']) < created:
            raise ValueError('共享证据撤回时间无效')
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_RECORD_BYTES:
        raise ValueError('单条共享日志超过 200 KiB')
    if not re.fullmatch(r'[0-9a-f]{64}', record['digest']) or record_digest(record) != record['digest']:
        raise ValueError('共享日志摘要校验失败')
    return record
