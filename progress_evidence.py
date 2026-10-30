"""Manual progress records, kept separate from task state and model claims."""
import copy
from datetime import datetime, timezone
import uuid

KINDS = {'plan': '计划', 'execution': '执行结果', 'verification': '验证记录', 'acceptance': '人工验收'}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('进度记录时间无效')
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('进度记录时间无效') from exc
    if result.tzinfo is None:
        raise ValueError('进度记录时间必须带时区')
    return result


def validate_records(records):
    if not isinstance(records, list) or len(records) > 2000:
        raise ValueError('进度记录格式无效或超过 2000 条')
    seen = set()
    for row in records:
        if not isinstance(row, dict) or set(row) - {'id', 'kind', 'text', 'source', 'created_at', 'retracted_at'}:
            raise ValueError('进度记录格式无效')
        identifier = row.get('id')
        if not isinstance(identifier, str) or not identifier or len(identifier) > 100 or identifier in seen:
            raise ValueError('进度记录标识无效或重复')
        seen.add(identifier)
        if not isinstance(row.get('kind'), str) or row['kind'] not in KINDS:
            raise ValueError('请选择进度记录类型')
        for key, limit, required in [('text', 6000, True), ('source', 1000, False)]:
            value = row.get(key)
            if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
                raise ValueError('请填写记录内容（最多 6000 字）和可选来源（最多 1000 字）')
        created = timestamp(row.get('created_at'))
        if 'retracted_at' in row and timestamp(row['retracted_at']) < created:
            raise ValueError('撤回时间不能早于记录时间')


def new_record(kind, text, source=''):
    row = dict(id=str(uuid.uuid4()), kind=kind, text=text.strip() if isinstance(text, str) else text,
               source=source.strip() if isinstance(source, str) else source,
               created_at=datetime.now(timezone.utc).isoformat())
    validate_records([row])
    return row


def retract(records, identifier):
    result = copy.deepcopy(records)
    row = next((r for r in result if r['id'] == identifier), None)
    if row is None:
        raise ValueError('进度记录已不存在')
    if not row.get('retracted_at'):
        row['retracted_at'] = datetime.now(timezone.utc).isoformat()
    validate_records(result)
    return result


def period_records(tasks, start, end):
    """Freeze events as of cutoff, including later withdrawals of older entries."""
    result = []
    for task in tasks:
        for row in task.get('evidence', []):
            created = timestamp(row['created_at'])
            withdrawn = timestamp(row['retracted_at']) if row.get('retracted_at') else None
            if created >= end:
                continue
            if not (start <= created < end or withdrawn and start <= withdrawn < end):
                continue
            entry = copy.deepcopy(row)
            if withdrawn and withdrawn >= end:
                entry.pop('retracted_at')
            entry.update(task_id=task['id'], title=task['title'], category=task['category'])
            result.append(entry)
    return result


def report_lines(records):
    from codex_support import redact
    lines = ['## 进度记录', '', '以下为人工记录，未自动执行核验；仅保存在本地日报，不上传 Linear。', '']
    if not records:
        return lines + ['本周期没有新增或撤回的进度记录。', '']
    for row in records:
        lines += ['### ' + redact(row['title']).replace('\n', ' '),
                  KINDS[row['kind']] + ' · ' + row['created_at'] + (' · 已撤回：' + row['retracted_at'] if row.get('retracted_at') else ''), '']
        lines += ['> ' + line for line in redact(row['text']).splitlines()]
        if row['source']:
            lines += ['', '来源（人工填写）：'] + ['> ' + line for line in redact(row['source']).splitlines()]
        lines.append('')
    return lines
