"""Local journal retrieval, delivery scopes and source-linked issue metadata.

Explicitly weighted child issues use their Linear snapshot states. Journal prose
does not award completion. This module uses no model or network and executes no source text.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evaluation.protocol import STATES, valid_uuid


LOCAL_ZONE = timezone(timedelta(hours=8))


class RevisionConflict(ValueError):
    """A delivery scope changed after the caller loaded it."""


def _text(value, name, maximum=500, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(f'{name}须为{maximum}字以内的有效文本')
    if '\x00' in value:
        raise ValueError(f'{name}包含无效字符')
    return value.strip()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _stamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=LOCAL_ZONE) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def _epoch(value):
    stamp = _stamp(value)
    return stamp.timestamp() if stamp else float('-inf')


def _record_key(record):
    return (str(record.get('employee_id', '')), str(record.get('source_device', '')),
            str(record.get('id') or record.get('source_id') or hashlib.sha256(_canonical(record).encode()).hexdigest()))


def _records(records):
    """Use one current copy per stable source record; explicit withdrawals win."""
    result = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        key = _record_key(record)
        previous = result.get(key)
        # Sync supplies current versions. When duplicate versions are supplied,
        # an explicit revision/update time, not filesystem order, takes precedence.
        rank = (_epoch(record.get('updated_at') or record.get('period_end')),
                int(bool(record.get('retracted_at'))), str(record.get('digest', '')))
        if previous is None or rank >= previous[0]:
            result[key] = (rank, copy.deepcopy(record))
    return [pair[1] for _, pair in sorted(result.items())]


def _evidence_key(record, evidence):
    identity = evidence.get('id')
    if not identity:
        identity = hashlib.sha256(_canonical({k: evidence.get(k, '') for k in
                                             ('kind', 'text', 'source', 'created_at')}).encode()).hexdigest()
    return (str(record.get('employee_id', '')), str(record.get('source_device', '')), str(identity))


def _evidence(records, lower=None, upper=None):
    entries = {}
    withdrawn = set()
    undated = 0
    for record in records:
        for evidence in record.get('evidence', []):
            if not isinstance(evidence, dict):
                continue
            key = _evidence_key(record, evidence)
            if evidence.get('retracted_at'):
                withdrawn.add(key)
                continue
            stamp = _stamp(evidence.get('created_at'))
            if stamp is not None:
                if lower and stamp < lower or upper and stamp >= upper:
                    continue
            elif lower or upper:
                undated += 1
            previous = entries.get(key)
            rank = (_epoch(evidence.get('created_at')), str(record.get('period_end', '')),
                    str(record.get('id', '')))
            if previous is None or rank > previous[0]:
                entries[key] = (rank, record, evidence)
    return {key: (value[1], value[2]) for key, value in entries.items() if key not in withdrawn}, withdrawn, undated


def _cancelled(state):
    return str(state or '').lower() in {'canceled', 'cancelled'}


class ProgressStore:
    """Neutral delivery scopes with optimistic, global integer revisions.

    configure() confirms the complete list of direct Linear child issue UUIDs and
    their relative weights. Only child Linear snapshots award completion values.
    snapshot() only exposes issues present in the supplied authorized records (and
    their parents), not every issue ever stored in this local ledger.
    """

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.folder / 'progress.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS progress_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
          INSERT OR IGNORE INTO progress_meta VALUES ('revision', 0);
          CREATE TABLE IF NOT EXISTS progress_scopes (issue_id TEXT PRIMARY KEY, items TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS progress_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL,
            issue_id TEXT NOT NULL, event TEXT NOT NULL);
        ''')
        self._remove_obsolete_fields()
        self.db.commit()

    @staticmethod
    def _neutral_items(items):
        return [{key: item.get(key, '') for key in ('id', 'title', 'weight', 'assignee_id')} |
                {'description': item.get('description', item.get('criteria', ''))} for item in items]

    def _remove_obsolete_fields(self):
        """Normalize pre-release databases to the current delivery-only schema."""
        with self.db:
            for issue_id, raw in self.db.execute('SELECT issue_id,items FROM progress_scopes').fetchall():
                normalized = _canonical(self._neutral_items(json.loads(raw)))
                if normalized != raw:
                    self.db.execute('UPDATE progress_scopes SET items=? WHERE issue_id=?', (normalized, issue_id))
            for sequence, raw in self.db.execute('SELECT sequence,event FROM progress_events').fetchall():
                event = json.loads(raw)
                if event.get('type') != 'scope_changed':
                    self.db.execute('DELETE FROM progress_events WHERE sequence=?', (sequence,))
                    continue
                event = {key: value for key, value in event.items() if key in ('type', 'revision', 'created_at')}
                previous = json.loads(raw)
                event['before'] = self._neutral_items(previous.get('before', []))
                event['after'] = self._neutral_items(previous.get('after', []))
                self.db.execute('UPDATE progress_events SET event=? WHERE sequence=?', (_canonical(event), sequence))

    def _revision(self):
        return self.db.execute("SELECT value FROM progress_meta WHERE key='revision'").fetchone()[0]

    def _check(self, expected):
        revision = self._revision()
        if expected is not None and (type(expected) is not int or expected != revision):
            raise RevisionConflict('交付范围已更新，请刷新后重试')
        return revision

    def _items(self, issue_id):
        row = self.db.execute('SELECT items FROM progress_scopes WHERE issue_id=?', (issue_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def _save(self, issue_id, items, event):
        revision = self._revision() + 1
        event = dict(event, revision=revision, created_at=datetime.now(timezone.utc).isoformat())
        self.db.execute('INSERT OR REPLACE INTO progress_scopes VALUES (?,?)', (issue_id, _canonical(items)))
        self.db.execute("UPDATE progress_meta SET value=? WHERE key='revision'", (revision,))
        self.db.execute('INSERT INTO progress_events (revision,issue_id,event) VALUES (?,?,?)',
                        (revision, issue_id, _canonical(event)))
        return self._state(issue_id)

    def _state(self, issue_id):
        events = [json.loads(row[0]) for row in self.db.execute(
            'SELECT event FROM progress_events WHERE issue_id=? ORDER BY sequence', (issue_id,))]
        return {'revision': self._revision(), 'issue_id': issue_id,
                'items': self._items(issue_id), 'events': events}

    def configure(self, issue_id, items, expected_revision=None):
        issue_id = _text(issue_id, 'Issue ID', 200)
        if not valid_uuid(issue_id):
            raise ValueError('父 Issue ID 须为真实 UUID')
        if not isinstance(items, list) or len(items) > 1000:
            raise ValueError('交付范围须为不超过1000项的列表')
        normalized = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('交付项格式无效')
            identity = _text(item.get('id'), '交付项 ID', 200)
            if not valid_uuid(identity) or identity == issue_id:
                raise ValueError('子 Issue ID 须为不同于父项的真实 UUID')
            if identity in seen:
                raise ValueError('交付项 ID 不可重复')
            seen.add(identity)
            weight = item.get('weight')
            if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0 or weight > 1e12:
                raise ValueError('每项权重须为有限正数，且不超过 10^12')
            normalized.append({'id': identity, 'title': _text(item.get('title'), '交付项标题'),
                               'weight': float(weight),
                               'assignee_id': _text(item.get('assignee_id', ''), '负责人 ID', 200, empty=True),
                               'description': _text(item.get('description', item.get('criteria', '')),
                                                    '交付描述', 8000, empty=True)})
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._check(expected_revision)
            old = self._items(issue_id)
            if old == normalized:
                return self._state(issue_id)
            return self._save(issue_id, normalized, {'type': 'scope_changed', 'before': old,
                                                    'after': normalized})

    def snapshot(self, records):
        current = [record for record in _records(records) if not record.get('retracted_at')]
        groups = defaultdict(list)
        for record in current:
            if record.get('issue_id'):
                groups[str(record['issue_id'])].append(record)
        with self.lock, self.db:
            self.db.execute('BEGIN')
            revision = self._revision()
            scopes = {identity: self._items(identity) for identity in groups}
            events = {identity: self._state(identity)['events'] for identity in groups}
        issues = {}
        for identity, rows in groups.items():
            rows.sort(key=lambda record: (_epoch(record.get('period_end')), str(record.get('id', ''))))
            latest = rows[-1]
            simultaneous = [row for row in rows if _epoch(row.get('period_end')) == _epoch(latest.get('period_end'))]
            metadata_errors = []
            parent_values = {str(row.get('parent_issue_id') or '') for row in simultaneous}
            state_values = {str(row.get('linear_state_type')) for row in simultaneous if row.get('linear_state_type')}
            if len(parent_values) > 1:
                metadata_errors.append(f'Issue {identity} 同周期资料中的父 issue 不一致，须核对最新关系')
            if len(state_values) > 1:
                metadata_errors.append(f'Issue {identity} 同周期资料中的 Linear 状态不一致，须核对最新状态')
            if any(row.get('parent_issue_id') and not isinstance(row.get('parent_issue_id'), str) for row in simultaneous):
                metadata_errors.append(f'Issue {identity} 父 issue ID 格式无效')
            evidence, _, _ = _evidence(rows)
            blockers = [item for _, item in evidence.values()
                        if str(item.get('kind', '')).lower() in {'blocker', 'blockers', 'blocked'}]
            issues[identity] = {
                'id': identity, 'identifier': latest.get('issue_identifier', ''),
                'title': latest.get('task_title') or latest.get('issue_identifier') or identity,
                'url': latest.get('issue_url', ''), 'parent_id': latest.get('parent_issue_id') or '',
                'employee_ids': sorted({str(row['employee_id']) for row in rows if row.get('employee_id')}),
                'linear_state_type': latest.get('linear_state_type', ''),
                'items': scopes.get(identity, []), 'events': events.get(identity, []),
                'last_report': latest.get('period_end', ''), 'linear_asof': latest.get('period_end', ''),
                'source_ids': sorted({str(row['id']) for row in rows if row.get('id')}),
                'unique_evidence': len(evidence), 'blocked': bool(blockers) or any(row.get('blocked') is True for row in rows),
                'blockers': blockers, 'errors': metadata_errors, 'placeholder': False,
            }
        # Missing parents are shown explicitly; their existence does not fabricate
        # direct progress or imply that the supplied subset covers all children.
        for parent in {str(issue['parent_id']) for issue in list(issues.values()) if issue['parent_id']}:
            if parent not in issues:
                issues[parent] = {'id': parent, 'identifier': '', 'title': parent,
                                  'url': '', 'parent_id': '', 'employee_ids': [], 'linear_state_type': '',
                                  'items': [], 'events': [], 'last_report': '', 'source_ids': [],
                                  'linear_asof': '', 'unique_evidence': 0, 'blocked': False, 'blockers': [],
                                  'errors': [], 'placeholder': True}
        children = defaultdict(list)
        for identity, issue in issues.items():
            if issue['parent_id']:
                children[str(issue['parent_id'])].append(identity)
        colors, cycle_errors, postorder = {}, defaultdict(list), []
        errors = [error for issue in issues.values() for error in issue['errors']]
        # Iterative post-order handles deeply nested imports and detects cycles
        # before evaluating any weights. Actual parent UUIDs define this graph.
        for start_id in sorted(issues):
            if colors.get(start_id) == 2:
                continue
            path, stack = [], [(start_id, False)]
            while stack:
                identity, exiting = stack.pop()
                if not exiting:
                    if colors.get(identity) == 2:
                        continue
                    if colors.get(identity) == 1:
                        cycle = path[path.index(identity):] + [identity]
                        message = 'Issue 父子关系存在环：' + ' → '.join(cycle)
                        errors.append(message)
                        for node in cycle:
                            cycle_errors[node].append(message)
                        continue
                    colors[identity] = 1
                    path.append(identity)
                    stack.append((identity, True))
                    stack.extend((child, False) for child in reversed(sorted(children[identity])))
                else:
                    colors[identity] = 2
                    postorder.append(identity)
                    path.pop()
        finished = {}
        for identity in postorder:
            issue = issues[identity]
            scope = issue['items']
            actual_children = set(children[identity])
            declared = {item['id'] for item in scope}
            result = {'leaves': {}, 'nodes': {identity}, 'cancelled': set(),
                      'missing': set(), 'wrong_parent': set(), 'invalid_state': set(),
                      'unconfigured': set(), 'invalid_metadata': set(),
                      'undeclared': actual_children - declared if scope else set(),
                      'errors': list(issue['errors']) + cycle_errors[identity], 'total': 0.0}
            if issue['placeholder']:
                result['missing'].add(identity)
            if issue['linear_state_type'] not in STATES - {''}:
                result['invalid_state'].add(identity)
            if _stamp(issue['linear_asof']) is None:
                result['invalid_metadata'].add(identity)
            if result['undeclared']:
                result['errors'].append('声明的完整子 Issue 清单遗漏了可见子项：' + ', '.join(sorted(result['undeclared'])))
            if any(not valid_uuid(item['id']) or item['id'] == identity for item in scope):
                result['errors'].append('声明的子 Issue 清单包含无效 UUID')
            # Structural errors remain visible even under a cancelled branch.
            for child in actual_children:
                result['errors'].extend(finished.get(child, {}).get('errors', cycle_errors[child]))
            cancelled = _cancelled(issue['linear_state_type'])
            if cancelled:
                result['cancelled'].add(identity)
            elif scope:
                for item in scope:
                    child_id = item['id']
                    child = issues.get(child_id)
                    if child is None or child['placeholder']:
                        result['missing'].add(child_id)
                        result['total'] += item['weight']
                        continue
                    if child['parent_id'] != identity:
                        result['wrong_parent'].add(child_id)
                        result['total'] += item['weight']
                        result['errors'].append('声明的子 Issue 不属于当前父项：' + child_id)
                        continue
                    sub = finished.get(child_id)
                    if sub is None:
                        result['errors'].extend(cycle_errors[child_id] or ['子 Issue 结构无法展开：' + child_id])
                        result['missing'].add(child_id)
                        result['total'] += item['weight']
                        continue
                    for key in ('nodes', 'cancelled', 'missing', 'wrong_parent', 'invalid_state',
                                'unconfigured', 'invalid_metadata', 'undeclared'):
                        result[key].update(sub[key])
                    result['errors'].extend(sub['errors'])
                    # A known all-cancelled subtree contributes neither completed
                    # weight nor denominator. Unknown subtrees are never dropped.
                    if not sub['leaves'] and sub['complete']:
                        continue
                    result['total'] += item['weight']
                    if sub['total']:
                        for leaf_id, leaf in sub['leaves'].items():
                            weighted = dict(leaf, weight=item['weight'] * (leaf['weight'] / sub['total']))
                            if not child['items']:
                                weighted['assignee_id'] = item['assignee_id']
                            if leaf_id in result['leaves']:
                                result['errors'].append('声明的子项重复计算同一叶 Issue：' + leaf_id)
                            result['leaves'][leaf_id] = weighted
            elif actual_children:
                # No explicit full child list means no guessed denominator.
                result['unconfigured'].add(identity)
            else:
                # A leaf can contribute its Linear state to an explicitly
                # configured parent, while its own percentage remains unset.
                result['total'] = 1.0
                result['leaves'][identity] = {'id': identity, 'weight': 1.0,
                    'linear_state_type': issue['linear_state_type'], 'assignee_id': '',
                    'asof': issue['linear_asof']}
            unknown = set().union(*(result[key] for key in ('missing', 'wrong_parent', 'invalid_state',
                                                           'unconfigured', 'invalid_metadata')))
            result['complete'] = not unknown and not result['errors']
            finished[identity] = result
            errors.extend(result['errors'])
        for identity, issue in issues.items():
            result = finished[identity]
            scope = issue['items']
            leaf_values = list(result['leaves'].values())
            total = result['total'] if scope else 0.0
            completed = sum(leaf['weight'] for leaf in leaf_values if leaf['linear_state_type'] == 'completed') if scope else 0.0
            complete = bool(scope) and result['complete']
            cancelled = _cancelled(issue['linear_state_type'])
            percent = round(100 * completed / total, 2) if complete and total and not cancelled else None
            unknown = set().union(*(result[key] for key in ('missing', 'wrong_parent', 'invalid_state',
                                                           'unconfigured', 'invalid_metadata')))
            source_nodes = [issues[node] for node in result['nodes'] if node in issues]
            asof = min((node['linear_asof'] for node in source_nodes if _stamp(node['linear_asof'])),
                       key=_epoch, default='')
            expected_leaves = set(result['leaves']) | result['missing'] | result['wrong_parent'] | result['unconfigured']
            issue.update({
                'percent': percent, 'partial_percent': percent, 'total_weight': total,
                'completed_weight': completed, 'basis': 'linear_subissues_weighted', 'asof': asof,
                'effective_leaves': sorted(leaf_values, key=lambda leaf: leaf['id']) if scope else [],
                'coverage': {
                    'estimated_leaf_count': sum(leaf['id'] not in unknown for leaf in leaf_values) if scope else 0,
                    'total_leaf_count': len(expected_leaves) if scope else 0,
                    'unestimated_issue_ids': sorted(unknown if scope else unknown | {identity}),
                    'complete': complete, 'scope': 'explicit_declared_subissues',
                    'parent_metadata_missing': issue['placeholder'], 'declared_subissues': bool(scope),
                    'declared_count': len(scope),
                    'visible_declared_count': sum(item['id'] in issues and not issues[item['id']]['placeholder'] for item in scope),
                    'missing_issue_ids': sorted(result['missing']),
                    'invalid_state_issue_ids': sorted(result['invalid_state']),
                    'invalid_metadata_issue_ids': sorted(result['invalid_metadata']),
                    'unconfigured_issue_ids': sorted(result['unconfigured']),
                    'wrong_parent_issue_ids': sorted(result['wrong_parent']),
                    'undeclared_issue_ids': sorted(result['undeclared']),
                    'active_scope_empty': complete and not total,
                },
                'leaf_issue_ids': sorted(result['leaves']), 'child_ids': sorted(children[identity]),
                'declared_issue_ids': [item['id'] for item in scope],
                'cancelled_issue_ids': sorted(result['cancelled']),
                'errors': sorted(set(result['errors'])), 'cancelled': cancelled,
                'progress_state': 'invalid' if result['errors'] else 'canceled' if cancelled else
                                  'estimated' if percent is not None else 'unestimated',
                'own_scope_ignored': False,
            })
            issue['employee_ids'] = sorted(set().union(*(set(node['employee_ids']) for node in source_nodes)))
            issue['source_ids'] = sorted(set().union(*(set(node['source_ids']) for node in source_nodes)))
            issue['last_report'] = max((node['last_report'] for node in source_nodes), key=_epoch, default='')
            issue['blocked'] = any(node['blocked'] for node in source_nodes)
        ordered = sorted(issues.values(), key=lambda issue: (issue['identifier'] or issue['id']))
        return {'issues': ordered, 'revision': revision, 'errors': sorted(set(errors)),
                'statistics': {'issues': len(groups), 'roots': sum(not issue['parent_id'] for issue in ordered),
                               'cancelled': sum(issue['cancelled'] for issue in ordered),
                               'unestimated': sum(issue['progress_state'] == 'unestimated' for issue in ordered)}}

    def close(self):
        with self.lock:
            self.db.close()


def _bound(value, name, end=False):
    if not value:
        return None
    stamp = _stamp(value)
    if stamp is None:
        raise ValueError(f'{name}须为 ISO 日期或时间')
    return stamp + timedelta(days=1) if end and len(value) == 10 else stamp


def _tokens(value):
    value = value.casefold()
    words = set(re.findall(r'[a-z0-9_][a-z0-9_-]+', value))
    for phrase in re.findall(r'[\u3400-\u9fff]+', value):
        words.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
        if len(phrase) == 1:
            words.add(phrase)
    return words


def retrieve(records, question, employee_id='', issue_id='', start='', end='', limit=12):
    """Count every matching record, then retrieve bounded lexical evidence.

    Date bounds include whole supplied end dates; timestamp bounds are exclusive.
    Report periods intersect the requested interval. Evidence with explicit times
    is additionally bounded; undated evidence is counted with an explicit warning.
    This is lexical retrieval, not semantic understanding or an exhaustive reading.
    """
    question = _text(question, '问题', 6000)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('检索条数须在 1 至 100 之间')
    lower, upper = _bound(start, '开始日期'), _bound(end, '结束日期', end=True)
    if lower and upper and lower >= upper:
        raise ValueError('结束时间须晚于开始时间')
    current = _records(records)
    candidates = []
    scoped = []
    invalid_dates = 0
    withdrawn = 0
    for record in current:
        if employee_id and str(record.get('employee_id', '')) != employee_id:
            continue
        if issue_id and str(record.get('issue_id', '')) != issue_id:
            continue
        scoped.append(record)
        if record.get('retracted_at'):
            withdrawn += 1
            continue
        beginning, finish = _stamp(record.get('period_start')), _stamp(record.get('period_end'))
        if lower or upper:
            if finish is None or beginning is None or finish < beginning:
                invalid_dates += 1
                continue
            if lower and finish < lower or upper and beginning >= upper:
                continue
            if finish == lower and beginning != finish:
                continue
        candidates.append(record)
    evidence, retractions, undated = _evidence(candidates, lower, upper)
    # A later withdrawal still retracts an older report inside a historical query.
    global_retractions = {_evidence_key(record, entry) for record in scoped
                          for entry in record.get('evidence', []) if isinstance(entry, dict) and entry.get('retracted_at')}
    retractions.update(global_retractions)
    evidence = {key: value for key, value in evidence.items() if key not in global_retractions}
    by_record = defaultdict(list)
    for record, entry in evidence.values():
        by_record[_record_key(record)].append(entry)
    tokens = _tokens(question)
    ranked = []
    for record in candidates:
        entries = by_record[_record_key(record)]
        summary = str(record.get('summary', ''))
        body = '\n'.join([summary] + [str(entry.get('text', '')) for entry in entries])
        corpus = ' '.join((str(record.get('name', '')), str(record.get('task_title', '')),
                           str(record.get('issue_identifier', '')), body)).casefold()
        title = str(record.get('task_title', '')).casefold()
        score = sum((2 if token in title else 1) for token in tokens if token in corpus)
        ranked.append((score, _epoch(record.get('period_end')), _record_key(record), record, entries, body))
    ranked.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    sources = []
    for index, (score, _, _, record, entries, body) in enumerate(ranked[:limit], 1):
        # Prefer a matching sentence when a long report would hide the search hit.
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        matched = [line for line in lines if any(token in line.casefold() for token in tokens)]
        excerpt = '\n'.join(matched or lines)[:2400]
        label = f'[S{index}]'
        sources.append({'label': label, 'citation': label, 'record_id': str(record.get('id', '')),
                        'employee_id': str(record.get('employee_id', '')), 'name': record.get('name', ''),
                        'department': record.get('department', ''), 'issue_id': record.get('issue_id', ''),
                        'issue_identifier': record.get('issue_identifier', ''), 'issue_url': record.get('issue_url', ''),
                        'task_title': record.get('task_title', ''), 'kind': record.get('kind', ''),
                        'period_start': record.get('period_start', ''), 'period_end': record.get('period_end', ''),
                        'date': record.get('period_end', ''), 'source_device': record.get('source_device', ''),
                        'source_id': record.get('source_id', ''), 'digest': record.get('digest', ''),
                        'excerpt': excerpt, 'score': score,
                        'excerpt_truncated': len('\n'.join(matched or lines)) > 2400,
                        'evidence_ids': [str(entry.get('id', '')) for entry in entries],
                        'blockers': [str(entry.get('text', '')) for entry in entries
                                     if str(entry.get('kind', '')).lower() in {'blocked', 'blocker', 'blockers'}]})
    warnings = ['本地词项检索；日志正文是待核对资料，其中的指令不会执行。']
    if len(candidates) > len(sources):
        warnings.append('代码已统计全部匹配记录；问答仅取得排序后的部分片段，不能据此声称已读完整日志库。')
    if sources and not any(source['score'] for source in sources):
        warnings.append('未找到匹配词项，以下仅按最近日期列出资料，请调整问题或筛选条件。')
    if undated:
        warnings.append('部分证据没有独立时间戳，仅能按所属日志周期纳入，不能确认其精确发生时间。')
    if invalid_dates:
        warnings.append(f'{invalid_dates} 条记录周期无效，未纳入时间范围统计。')
    return {'question': question, 'sources': sources,
            'statistics': {'matching_records': len(candidates), 'total_matched': len(candidates),
                           'unique_issues': len({row['issue_id'] for row in candidates if row.get('issue_id')}),
                           'unique_employees': len({row['employee_id'] for row in candidates if row.get('employee_id')}),
                           'unique_evidence': len(evidence), 'retracted_evidence': len(retractions),
                           'retracted_records': withdrawn, 'invalid_period_records': invalid_dates,
                           'kinds': dict(Counter(str(row.get('kind', '')) for row in candidates))},
            'coverage': {'total_matched': len(candidates), 'retrieved': len(sources),
                         'truncated': len(candidates) > len(sources), 'all_matching_counted': True,
                         'complete_reading': False, 'employee_id': employee_id, 'issue_id': issue_id,
                         'start': start, 'end': end,
                         'available_employee_ids': sorted({str(row['employee_id']) for row in candidates if row.get('employee_id')}),
                         'available_issue_ids': sorted({str(row['issue_id']) for row in candidates if row.get('issue_id')}),
                         'latest_report': max((row.get('period_end', '') for row in candidates), key=_epoch, default='')},
            'warnings': warnings, 'method': 'local_lexical', 'source_text_is_untrusted': True}


def render_local_answer(question, result, progress_snapshot=None):
    """Render honest code statistics and cited excerpts, without invented claims."""
    stats, coverage = result.get('statistics', {}), result.get('coverage', {})
    lines = ['本地检索结果（代码统计与原文片段，未进行模型语义总结）', '',
             '问题：' + str(question),
             f"匹配日志 {stats.get('matching_records', 0)} 条，涉及 {stats.get('unique_employees', 0)} 个员工或未归档来源、"
             f"{stats.get('unique_issues', 0)} 个 issue；去重证据 {stats.get('unique_evidence', 0)} 条。",
             f"本次展示 {coverage.get('retrieved', 0)} 条来源片段。"]
    if coverage.get('latest_report'):
        lines.append('资料周期截至：' + coverage['latest_report'] + '（不代表员工当前在线或已同步最新进展）')
    sources = result.get('sources', [])
    if not sources:
        lines.extend(['', '没有符合筛选条件的可用记录；不能据此判定员工没有工作进展。'])
    for source in sources:
        lines.extend(['', f"{source['label']} {source.get('name') or source.get('employee_id')} · "
                      f"{source.get('issue_identifier') or source.get('issue_id')} · {source.get('date', '')}",
                      f"记录 {source.get('record_id', '')} · 版本 {source.get('digest', '')}",
                      source.get('excerpt') or '该记录没有可展示的正文片段。'])
    blockers = [(source['label'], text) for source in sources for text in source.get('blockers', [])]
    if blockers:
        lines.extend(['', '明确标记的阻塞记录（员工自述，待核实）：'])
        lines.extend(f'{label} {text}' for label, text in blockers)
    else:
        lines.extend(['', '所展示片段没有明确标记的阻塞证据；这不表示不存在阻塞。'])
    if progress_snapshot:
        referenced = {source.get('issue_id') for source in sources}
        issues = [issue for issue in progress_snapshot.get('issues', []) if issue['id'] in referenced]
        if issues:
            lines.extend(['', f"Issue 范围与状态（版本 {progress_snapshot.get('revision', 0)}）："])
            for issue in issues:
                status = '已取消，不计作完成' if issue.get('cancelled') else \
                         '资料关系冲突，须先核对' if issue.get('errors') else \
                         '待估：完整子 Issue 清单或快照资料不足' if issue.get('percent') is None else \
                         f"{issue['percent']:g}%（Linear 已完成子项权重 {issue['completed_weight']:g}/{issue['total_weight']:g}）"
                lines.append(f"{issue.get('identifier') or issue['id']}：{status}")
                if issue.get('asof'):
                    lines.append('最早依据日志周期截至：' + issue['asof'] + '；不是 Linear 状态读取时间或实时查询结果。')
    lines.extend(['', '资料与统计边界：'] + ['- ' + warning for warning in result.get('warnings', [])])
    lines.append('- 日志叙述不会自动赋予完成百分比；尚未读取的附件不包含在本次回答内。')
    return '\n'.join(lines)
