"""Read a complete My Issues snapshot using the installed ORBIT credentials."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
SHARED = ROOT / 'shared'
sys.path[:0] = [str(ROOT), str(SHARED if SHARED.is_dir() else ROOT.parent.parent)]

from linear_support import LinearError, graphql
from orbit_plugin.core import Orbit, OrbitError, safe_text

SUMMARY = 'id identifier title url state { name type } assignee { id name }'
LIST_QUERY = '''query OrbitTriageIssues($after: String, $filter: IssueFilter) {
  issues(first: 50, after: $after, filter: $filter) {
    nodes { id identifier title url description priority priorityLabel dueDate updatedAt
      state { name type } assignee { id name } team { id name key }
      project { id name url targetDate } projectMilestone { id name targetDate }
      parent { SUMMARY }
    } pageInfo { hasNextPage endCursor }
  }
}'''.replace('SUMMARY', SUMMARY)
CONNECTIONS = {
    'children': SUMMARY,
    'relations': 'id type issue { SUMMARY } relatedIssue { SUMMARY }'.replace('SUMMARY', SUMMARY),
    'inverseRelations': 'id type issue { SUMMARY } relatedIssue { SUMMARY }'.replace('SUMMARY', SUMMARY),
    'comments': 'id body createdAt updatedAt user { name }',
}


def pages(request, query, variables, path):
    rows, ids, cursors, after = [], set(), set(), None
    for _ in range(200):
        connection = request(query, {**variables, 'after': after})
        try:
            for key in path:
                connection = connection[key]
            nodes, page = connection['nodes'], connection['pageInfo']
            if not isinstance(nodes, list) or type(page['hasNextPage']) is not bool:
                raise ValueError()
            for row in nodes:
                if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
                    raise ValueError()
                if row['id'] not in ids:
                    rows.append(row)
                    ids.add(row['id'])
            if not page['hasNextPage']:
                return rows
            after = page['endCursor']
            if not isinstance(after, str) or not after or after in cursors:
                raise ValueError()
            cursors.add(after)
        except (KeyError, TypeError, ValueError):
            raise LinearError('分页信息异常，无法确认完整清单；请重新查询。') from None
    raise LinearError('查询超过分页上限，未生成完整快照。')


def collect(request, identifiers=()):
    viewer = request('query { viewer { id name } }', {})['viewer']
    filt = {'assignee': {'id': {'eq': viewer['id']}},
            'state': {'type': {'nin': ['completed', 'canceled']}}}
    issues = pages(request, LIST_QUERY, {'filter': filt}, ['issues'])
    if any((i.get('assignee') or {}).get('id') != viewer['id'] or
           i['state']['type'] in ('completed', 'canceled') for i in issues):
        raise LinearError('查询期间负责人或状态发生变化，请重新拉取。')
    wanted = {identifier.upper() for identifier in identifiers}
    selected = [i for i in issues if not wanted or i['identifier'].upper() in wanted]

    def enrich(issue):
        for field, fields in CONNECTIONS.items():
            query = ('query OrbitTriageContext($id: String!, $after: String) { issue(id: $id) { '
                     + field + '(first: 50, after: $after) { nodes { ' + fields
                     + ' } pageInfo { hasNextPage endCursor } } } }')
            issue[field] = pages(request, query, {'id': issue['id']}, ['issue', field])
        return issue

    with ThreadPoolExecutor(max_workers=4) as pool:
        selected = list(pool.map(enrich, selected))
    if request('query { viewer { id } }', {})['viewer']['id'] != viewer['id']:
        raise LinearError('查询期间账号发生变化，请重新查询。')
    return {'as_of': datetime.now(timezone.utc).isoformat(), 'viewer': viewer,
            'scope': 'assigned to viewer; non-archived; state not completed/canceled',
            'total': len(issues), 'selected_count': len(selected),
            'missing_identifiers': sorted(wanted - {i['identifier'].upper() for i in selected}),
            'issues': selected, 'warnings': []}


def local_bindings(directory, snapshot):
    """Read only ORBIT's own store, never a Codex chat database."""
    path = Path(directory) / 'codex.sqlite3'
    if not path.is_file():
        return []
    issue_ids = {i['id'] for i in snapshot['issues']}
    db = None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        bindings = []
        for row in db.execute('SELECT id,issue_id,issue,work_dir,session_id,updated_at FROM tasks'):
            issue = json.loads(row['issue'])
            if row['issue_id'] in issue_ids and issue.get('viewer_id') == snapshot['viewer']['id']:
                bindings.append({k: row[k] for k in ('id', 'issue_id', 'work_dir', 'session_id', 'updated_at')})
        return bindings
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        snapshot['warnings'].append('本地 ORBIT 绑定未能完整读取；请用宿主清单核对聊天。')
        return []
    finally:
        if db is not None:
            db.close()


def settings():
    path = ROOT / 'settings.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if (type(value) is not dict or set(value) - {'data_dir', 'credentials_dir', 'auto_daily_automation'}
                or any(not isinstance(v, str) for k, v in value.items() if k != 'auto_daily_automation')
                or type(value.get('auto_daily_automation', True)) is not bool):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise OrbitError('invalid_settings', '插件 settings.json 无效，请使用本机设置修正。') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--issue', action='append', default=[], help='只补齐指定 My Issue 的上下文，可重复')
    parser.add_argument('--output', type=Path, help='新 JSON 文件；不覆盖已有文件')
    parser.add_argument('--data-dir', help='已知的插件数据目录')
    parser.add_argument('--credentials-dir', help='已配置的桌面凭据来源目录')
    args = parser.parse_args()
    try:
        if args.output and args.output.exists():
            raise OrbitError('output_exists', '输出文件已存在，请指定新文件名。')
        config = settings()
        orbit = Orbit(args.data_dir or os.environ.get('ORBIT_DATA_DIR') or config.get('data_dir'),
                      credential_directory=args.credentials_dir or os.environ.get('ORBIT_CREDENTIALS_DIR')
                      or config.get('credentials_dir'))
        credential = orbit.credential()
        result = collect(lambda query, variables: graphql(credential, variables, query), args.issue)
        result['local_bindings'] = local_bindings(orbit.directory, result)
        payload = safe_text(json.dumps(result, ensure_ascii=False, indent=2).replace(credential, '[已隐藏密钥]'))
        if args.output:
            with args.output.open('x', encoding='utf-8') as handle:
                handle.write(payload + '\n')
            print(json.dumps({'total': result['total'], 'selected_count': result['selected_count'],
                              'output': str(args.output.resolve()), 'warnings': result['warnings']}, ensure_ascii=False))
        else:
            print(payload)
        return 0
    except (LinearError, OrbitError) as exc:
        print(safe_text(str(exc)), file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError):
        print('无法生成盘点快照，请检查参数、输出路径或返回数据；未改动任务。', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
