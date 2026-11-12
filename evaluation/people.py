"""Read-only projections of an explicit roster over authorized journal copies.

Source device UUIDs and record digests are never rewritten into person IDs.
Linear ownership requires a known assignee and an explicitly mapped account.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import time
from urllib.parse import urlparse

from evaluation.insights import _bound


def source_key(record):
    return record['source_device'], record['id']


def epoch(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, AttributeError, TypeError):
        return float('-inf')


def latest(values):
    return max((value for value in values if value), key=epoch, default='')


def account_candidates(records):
    candidates = {}
    for row in records:
        user = row.get('linear_assignee_id')
        if not user:
            continue
        workspace = urlparse(row['issue_url']).path.strip('/').split('/')[0].lower()
        candidates[(workspace, user)] = dict(workspace=workspace, user_id=user,
                                             issue_identifier=row['issue_identifier'])
    return [candidates[key] for key in sorted(candidates)]


def project_roster(roster, records, owners, peers, sync_states, discovered, progress, updates):
    """Attach person ownership without altering source data or inferred totals."""
    people = {row['id']: row for row in roster['employees']}
    devices = {row['device_id']: row for row in roster['devices']}
    accounts = {(row['workspace'], row['user_id']): row['employee_id'] for row in roster['accounts']}
    paired = {row['employee_id']: row for row in peers}
    synced = {row['id']: row for row in sync_states}
    connections = []
    for device_id in sorted(set(paired) | set(devices)):
        peer, binding, state = paired.get(device_id, {}), devices.get(device_id, {}), synced.get(device_id, {})
        found = next((row for row in discovered if row['employee_id'] == device_id and
                      row['fingerprint'] == peer.get('fingerprint')), None)
        seen = ''
        if found:
            elapsed = max(0, time.monotonic() - found['seen'])
            seen = (datetime.now(timezone.utc) - timedelta(seconds=elapsed)).isoformat()
        connections.append(dict(device_id=device_id, name=peer.get('name', ''),
            department=peer.get('department', ''), url=peer.get('url', ''),
            fingerprint=peer.get('fingerprint', ''), last_synced=state.get('last_synced', ''),
            error=state.get('error', ''), connected=bool(peer), online=bool(found), last_seen=seen,
            employee_id=binding.get('employee_id', ''), label=binding.get('label', '')))

    records_by_issue, records_by_person = defaultdict(list), defaultdict(list)
    for record in records:
        records_by_issue[record['issue_id']].append(record)
        person = owners.get(source_key(record))
        if person in people:
            records_by_person[person].append(record)
    issues = {row['id']: row for row in progress['issues']}
    for issue in issues.values():
        rows = records_by_issue[issue['id']]
        contributors = {owners[source_key(row)] for row in rows if owners.get(source_key(row)) in people}
        unassigned = {row['source_device'] for row in rows if owners.get(source_key(row)) not in people}
        issue.update(contributor_person_ids=sorted(contributors), unassigned_device_ids=sorted(unassigned),
                     owner_person_id='', owner_notice='负责人待核对：来源未提供 Linear 负责人',
                     linear_workspace='', linear_assignee_id='')
        if not rows:
            continue
        end = max(epoch(row['period_end']) for row in rows)
        current = [row for row in rows if epoch(row['period_end']) == end]
        identities = {(urlparse(row['issue_url']).path.strip('/').split('/')[0].lower(),
                       row.get('linear_assignee_id', '')) for row in current}
        if len(identities) != 1:
            issue['owner_notice'] = '负责人待核对：同周期来源中的负责人不一致'
            continue
        workspace, user = next(iter(identities))
        issue.update(linear_workspace=workspace, linear_assignee_id=user)
        if user:
            person = accounts.get((workspace, user))
            issue['owner_person_id'] = person or ''
            issue['owner_notice'] = '已按 Linear 账号对应' if person else '负责人待归档：Linear 账号尚未绑定员工'
    # Parent issues include contributors to their child issues, but never inherit
    # a child's assignee. Each issue's actual ownership is resolved separately.
    changed = True
    while changed:
        changed = False
        for child in issues.values():
            parent = issues.get(child['parent_id'])
            if parent is None:
                continue
            for key in ('contributor_person_ids', 'unassigned_device_ids'):
                combined = sorted(set(parent[key]) | set(child[key]))
                if parent[key] != combined:
                    parent[key], changed = combined, True
    for issue in issues.values():
        issue['owner_name'] = people.get(issue['owner_person_id'], {}).get('name', '')
        issue['contributor_names'] = [people[identity]['name'] for identity in issue['contributor_person_ids']]
    summaries = []
    for person in people.values():
        identity = person['id']
        authored = records_by_person[identity]
        owned = {issue['id'] for issue in issues.values() if issue['owner_person_id'] == identity}
        contributed = {issue['id'] for issue in issues.values() if identity in issue['contributor_person_ids']}
        related = owned | contributed
        bound = [row for row in connections if row['employee_id'] == identity]
        source_devices = {row['source_device'] for row in authored} | {row['device_id'] for row in bound}
        issue_rows = []
        for issue in issues.values():
            if issue['id'] not in related:
                continue
            relation = ('负责人' if issue['id'] in owned else '') + (' · ' if issue['id'] in owned & contributed else '') + ('日志贡献' if issue['id'] in contributed else '')
            issue_rows.append({key: issue.get(key, '') for key in
                ('id', 'identifier', 'title', 'percent', 'linear_state_type', 'owner_notice', 'last_report')} |
                {'relation': relation})
        blockers = [dict(issue_id=row['issue_id'], issue_identifier=issues[row['issue_id']]['identifier'], text=row['blockers'],
                         source_versions_changed=row.get('source_versions_changed', False))
                    for row in updates if row['issue_id'] in related and row['blockers']]
        summaries.append(dict(person, devices=bound,
            accounts=[row for row in roster['accounts'] if row['employee_id'] == identity],
            authored_record_count=len(authored), contributed_issue_ids=sorted(contributed),
            assigned_issue_ids=sorted(owned), last_report=latest(row['period_end'] for row in authored),
            last_synced=latest(synced.get(device, {}).get('last_synced', '') for device in source_devices),
            blockers=blockers, issues=issue_rows))
    return dict(roster, summaries=summaries, connections=connections,
                unassigned_device_count=sum(not row['employee_id'] for row in connections if row['connected']),
                account_candidates=account_candidates(records))


def matching_records(records, result):
    """Use the retrieval interval for derived statistics and model metadata."""
    candidates = set(result['coverage']['available_employee_ids'])
    issues = set(result['coverage']['available_issue_ids'])
    lower = _bound(result['coverage'].get('start'), '开始时间')
    upper = _bound(result['coverage'].get('end'), '结束时间', end=True)
    return [row for row in records if row['employee_id'] in candidates and row['issue_id'] in issues
               and not row.get('retracted_at')
               and (lower is None or epoch(row['period_end']) > lower.timestamp())
               and (upper is None or epoch(row['period_start']) < upper.timestamp())]


def attribute_result(result, records, owners, roster):
    """Attribute retrieved excerpts and counts; keep original source IDs intact."""
    people = {row['id']: row for row in roster['employees']}
    matched = matching_records(records, result)
    identities = {('person', owners[source_key(row)]) if owners.get(source_key(row)) in people
                  else ('device', row['source_device']) for row in matched}
    result['statistics']['unique_employees'] = len(identities)
    result['statistics']['unique_devices'] = len({row['source_device'] for row in matched})
    for source in result['sources']:
        person = owners.get((source['source_device'], source['record_id']), '')
        source['person_id'] = person
        source['person_name'] = people.get(person, {}).get('name', '')
        if person in people:
            source['name'] = people[person]['name']
