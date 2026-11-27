"""Manager-facing facts from the already authorized local snapshot.

No name matching, model calls, network reads, or employee completion scores.
"""
from collections import defaultdict
from datetime import datetime, timezone

from evaluation.people import epoch, latest, source_key


STATES = {'completed': '已完成', 'started': '进行中', 'unstarted': '未开始',
          'backlog': '待排期', 'triage': '待分派', 'canceled': '已取消'}
STALE_DAYS = 7


def build_overview(progress, roster, updates, records, owners, now=None):
    """Keep ownership, contribution, log date and sync date as separate facts."""
    now = now or datetime.now(timezone.utc)
    people = {person['id']: person for person in roster['employees']}
    notes = {row['issue_id']: row for row in updates}
    by_issue = defaultdict(list)
    for record in records:
        by_issue[record['issue_id']].append(record)
    issues = []
    for issue in progress['issues']:
        update = notes.get(issue['id'], {})
        rows = by_issue[issue['id']]
        newest = latest(row['period_end'] for row in rows)
        reports = []
        # Keep every author at the latest reporting period, including ties.
        for row in sorted(rows, key=lambda row: (row['source_device'], row['id'])):
            if epoch(row['period_end']) != epoch(newest):
                continue
            person = people.get(owners.get(source_key(row)), {})
            reports.append(dict(id=row['id'], digest=row['digest'], date=row['period_end'],
                author=person.get('name') or '未归档来源 · '+row['name'],
                person_id=person.get('id', ''), summary=row['summary'],
                blockers=[item['text'] for item in row.get('evidence', [])
                          if item.get('kind', '').lower() in ('blocker', 'blockers', 'blocked')
                          and not item.get('retracted_at')]))
        manual_summary = update.get('summary', '').strip()
        notes_outdated = bool(update and newest and epoch(newest) > epoch(update.get('updated_at', '')))
        summary = manual_summary if not notes_outdated else ''
        summary_source = '人工整理' if summary else '员工日志'
        if not summary:
            summary = '\n'.join(row['author']+'：'+row['summary'] for row in reports if row['summary'].strip())
        state = issue.get('linear_state_type', '')
        current_states = {row.get('linear_state_type', state) for row in rows if epoch(row['period_end']) == epoch(newest)}
        if len(current_states) > 1:
            state = ''
        closed = state in ('completed', 'canceled')
        blockers = update.get('blockers', '').strip()
        blocker_source = '人工整理' if blockers else '员工日志'
        if not blockers:
            blockers = '\n'.join(report['author']+'：'+text for report in reports for text in report['blockers'])
        changed = update.get('source_versions_changed', False) or notes_outdated
        last_report = issue.get('last_report') or newest
        stale = bool(last_report and (now.timestamp() - epoch(last_report)) >= STALE_DAYS*86400)
        reasons = []
        if not closed:
            if blockers:
                reasons.append('阻塞待核对' if changed else '有阻塞')
            if changed:
                reasons.append('进展待核对')
            if stale:
                reasons.append('超过 7 天未更新')
            if not summary:
                reasons.append('待补充进展')
            if not issue.get('owner_person_id'):
                reasons.append('负责人待确认')
            if state not in STATES:
                reasons.append('状态待确认')
        issues.append(dict(id=issue['id'], identifier=issue['identifier'] or '关联父任务',
            title='父任务资料待共享' if issue.get('placeholder') else issue['title'],
            parent_id=issue.get('parent_id', ''), child_ids=issue.get('child_ids', []),
            owner_id=issue.get('owner_person_id', ''), owner_name=issue.get('owner_name', ''),
            owner_notice=issue.get('owner_notice', ''),
            contributor_ids=issue.get('contributor_person_ids', []),
            contributor_names=issue.get('contributor_names', []),
            state=state, state_label=STATES.get(state, '状态待确认'), closed=closed,
            percent=issue.get('percent'), summary=summary, summary_source=summary_source, reports=reports,
            manual_summary=manual_summary, notes_outdated=notes_outdated,
            blockers=blockers, blocker_source=blocker_source, next_step=update.get('next_step', ''),
            editor=update.get('editor', ''), updated_at=update.get('updated_at', ''),
            source_versions_changed=update.get('source_versions_changed', False),
            last_report=last_report, attention=reasons))
    # Explicit blockers first. Stable identifiers prevent refreshes from jumping.
    issues.sort(key=lambda row: (row['closed'], not bool(row['blockers']),
                                not bool(row['attention']), row['identifier'], row['id']))
    summaries = []
    for person in roster['summaries']:
        related = [row for row in issues if row['owner_id'] == person['id'] or person['id'] in row['contributor_ids']]
        active = [row for row in related if not row['closed']]
        summaries.append(dict(id=person['id'], name=person['name'], department=person['department'],
            active=person['active'], owned_count=sum(row['owner_id'] == person['id'] for row in active),
            contributed_count=sum(person['id'] in row['contributor_ids'] and row['owner_id'] != person['id'] for row in active),
            attention_count=sum(bool(row['attention']) for row in active),
            last_report=person.get('last_report', ''), last_synced=person.get('last_synced', ''),
            sync_errors=sum(bool(device.get('error')) for device in person['devices']),
            has_devices=bool(person['devices']), has_accounts=bool(person['accounts'])))
    summaries.sort(key=lambda row: (not row['active'], not bool(row['attention_count']), row['name'], row['id']))
    active = [row for row in issues if not row['closed']]
    return dict(issues=issues, people=summaries,
        counts=dict(people=sum(row['active'] for row in summaries), active=len(active),
                    attention=sum(bool(row['attention']) for row in active),
                    completed=sum(row['state'] == 'completed' for row in issues)),
        last_synced=latest(row.get('last_synced', '') for row in roster['connections']),
        sync_errors=sum(bool(row.get('error')) for row in roster['connections'] if row.get('connected')),
        unassigned_devices=roster['unassigned_device_count'])


def filter_issues(overview, person='', status='active', query=''):
    """Search respects the selected employee and status instead of resetting them."""
    words = query.casefold().split()
    result = []
    for row in overview['issues']:
        if person == '__unassigned__' and row['owner_id']:
            continue
        if person and person != '__unassigned__' and person != row['owner_id'] and person not in row['contributor_ids']:
            continue
        if status == 'active' and row['closed']:
            continue
        if status == 'attention' and not row['attention']:
            continue
        if status == 'completed' and row['state'] != 'completed':
            continue
        haystack = ' '.join([row['identifier'], row['title'], row['owner_name'],
                            *row['contributor_names'], row['summary'], row['blockers'], row['next_step']]).casefold()
        if all(word in haystack for word in words):
            result.append(row)
    return result
