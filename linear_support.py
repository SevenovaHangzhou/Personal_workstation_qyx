"""Linear GraphQL transport; mutations use durable caller-owned UUID v4 IDs."""
import json
import socket
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from model import parse_binding

QUERY = """query MomoMyIssues($after: String, $filter: IssueFilter) {
  viewer {
    id name
    assignedIssues(first: 50, after: $after, filter: $filter, orderBy: updatedAt) {
      nodes { id identifier title description url dueDate updatedAt
        state { name type } team { name } }
      pageInfo { hasNextPage endCursor }
    }
  }
}"""


class LinearError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def graphql(api_key, variables, query=QUERY):
    request = Request("https://api.linear.app/graphql",
                      data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Authorization": api_key}, method="POST")
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise LinearError("Linear 返回数据过大，请稍后重试")
        payload = json.loads(data)
    except HTTPError as exc:
        messages = {401: "Linear API Key 无效或已失效", 403: "Linear API Key 没有执行此操作的权限", 429: "Linear 请求过于频繁，请稍后重试"}
        raise LinearError(messages.get(exc.code, f"Linear 请求失败（HTTP {exc.code}）")) from None
    except (URLError, socket.timeout, TimeoutError, OSError):
        raise LinearError("无法连接 Linear，请检查网络后重试") from None
    except (ValueError, UnicodeError):
        raise LinearError("Linear 返回了无法解析的数据") from None
    if not isinstance(payload, dict) or payload.get("errors"):
        raise LinearError("Linear 操作未成功，请确认权限或在同步详情中重试")
    return payload.get("data")


def validate_issue(issue):
    if not isinstance(issue, dict) or any(not isinstance(issue.get(k), str) or not issue[k] for k in ["id", "identifier", "title", "url", "state_type", "state_name", "viewer_id"]):
        raise ValueError("Linear issue 数据不完整")
    if any(not isinstance(issue.get(k, ""), str) for k in ["description", "due_date", "updated_at", "team"]):
        raise ValueError("Linear issue 字段格式不正确")
    binding = parse_binding("linear", issue["url"])
    if binding["label"] != issue["identifier"]:
        raise ValueError("Linear issue 编号与链接不一致")
    if issue.get("due_date"):
        date.fromisoformat(issue["due_date"])


def fetch_my_issues(api_key, include_closed=False, cancel_event=None):
    after = None
    cursors = set()
    seen = set()
    issues = []
    identity = None
    for _ in range(200):
        if cancel_event is not None and cancel_event.is_set():
            raise LinearError("已取消拉取，本次未导入任务")
        variables = {"after": after, "filter": {} if include_closed else {"state": {"type": {"nin": ["completed", "canceled"]}}}}
        data = graphql(api_key, variables)
        if cancel_event is not None and cancel_event.is_set():
            raise LinearError("已取消拉取，本次未导入任务")
        try:
            viewer = data["viewer"]
            if not isinstance(viewer["id"], str) or not viewer["id"] or not isinstance(viewer["name"], str):
                raise ValueError()
            if identity and identity["id"] != viewer["id"]:
                raise ValueError()
            identity = {"id": viewer["id"], "name": viewer["name"]}
            connection = viewer["assignedIssues"]
            nodes = connection["nodes"]
            if not isinstance(nodes, list):
                raise ValueError()
            for node in nodes:
                issue = {"id": node["id"], "identifier": node["identifier"], "title": node["title"],
                         "description": node.get("description") or "", "url": node["url"],
                         "state_type": node["state"]["type"], "state_name": node["state"]["name"],
                         "team": node["team"]["name"], "due_date": node.get("dueDate") or "",
                         "updated_at": node.get("updatedAt") or "", "viewer_id": viewer["id"]}
                validate_issue(issue)
                if issue["id"] not in seen and (include_closed or issue["state_type"] not in ("completed", "canceled")):
                    issues.append(issue)
                    seen.add(issue["id"])
            page = connection["pageInfo"]
            if type(page["hasNextPage"]) is not bool:
                raise ValueError()
            if not page["hasNextPage"]:
                return {"viewer": identity, "issues": issues}
            after = page["endCursor"]
            if not isinstance(after, str) or not after or after in cursors:
                raise ValueError()
            cursors.add(after)
        except (TypeError, ValueError, KeyError, AttributeError):
            raise LinearError("Linear 返回的 issue 或分页信息不完整，本次未导入任务") from None
    raise LinearError("My Issues 超过单次拉取上限，请减少范围后重试")


ISSUE_FIELDS = '''id identifier title description url dueDate updatedAt
  state { name type } team { id name } project { id name } parent { id } assignee { id }'''


def normalize_issue(node, viewer_id):
    issue = {'id': node['id'], 'identifier': node['identifier'], 'title': node['title'], 'url': node['url'],
             'state_type': node['state']['type'], 'state_name': node['state']['name'], 'viewer_id': viewer_id,
             'description': node.get('description') or '', 'due_date': node.get('dueDate') or '',
             'updated_at': node.get('updatedAt') or '', 'team': node['team']['name'], 'team_id': node['team']['id'],
             'project_id': (node.get('project') or {}).get('id', ''),
             'parent_issue_id': (node.get('parent') or {}).get('id', ''),
             'assignee_id': (node.get('assignee') or {}).get('id', '')}
    validate_issue(issue)
    return issue


class LinearWriter:
    """API shapes verified against official @linear/sdk 99.0.0 (2026-10-27)."""
    def __init__(self, key):
        if not key:
            raise LinearError('请先配置 Linear API Key')
        self._key = key

    def request(self, query, variables=None):
        data = graphql(self._key, variables or {}, query)
        if not isinstance(data, dict):
            raise LinearError('Linear 响应缺少 data')
        return data

    def _pages(self, query, field, variables=None, nested=None):
        rows, cursor, seen = [], None, set()
        for _ in range(200):
            data = self.request(query, {**(variables or {}), 'after': cursor})
            connection = data[nested][field] if nested else data[field]
            rows.extend(connection['nodes'])
            page = connection['pageInfo']
            if not page['hasNextPage']:
                return rows
            cursor = page['endCursor']
            if not cursor or cursor in seen:
                raise LinearError('Linear 分页游标无效')
            seen.add(cursor)
        raise LinearError('Linear 归属选项超过分页上限')

    def choices(self, team_id=''):
        viewer = self.request('query MomoViewer { viewer { id name } }')['viewer']
        teams = self._pages('''query MomoTeams($after: String) { teams(first: 50, after: $after) {
          nodes { id name } pageInfo { hasNextPage endCursor } } }''', 'teams')
        projects = []
        if team_id:
            if team_id not in {t['id'] for t in teams}:
                raise LinearError('所选团队已不可访问，请重新选择')
            projects = self._pages('''query MomoTeamProjects($id: String!, $after: String) {
              team(id: $id) { projects(first: 50, after: $after) { nodes { id name }
              pageInfo { hasNextPage endCursor } } } }''', 'projects', {'id': team_id}, 'team')
        return {'viewer': viewer, 'teams': teams, 'projects': projects}

    def issue(self, identifier):
        data = self.request('query MomoIssue($id: String!) { viewer { id } issue(id: $id) { '+ISSUE_FIELDS+' } }', {'id': identifier})
        return normalize_issue(data['issue'], data['viewer']['id'])

    def ensure_issue(self, operation):
        identifier = operation['id']
        data = self.request('query MomoIssueById($filter: IssueFilter!) { viewer { id } issues(first: 1, filter: $filter) { nodes { '+ISSUE_FIELDS+' } } }',
                            {'filter': {'id': {'eq': identifier}}})
        rows = data['issues']['nodes']
        if rows:
            issue = normalize_issue(rows[0], data['viewer']['id'])
        else:
            result = self.request('mutation MomoCreateIssue($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { '+ISSUE_FIELDS+' } } }',
                                  {'input': {'id': identifier, **operation['payload']}})['issueCreate']
            if not result.get('success'):
                raise LinearError('Linear 未确认 issue 创建成功；下次将核对相同 ID')
            issue = normalize_issue(result['issue'], data['viewer']['id'])
        expected = operation['payload']
        if issue['id'] != identifier or issue['team_id'] != expected['teamId'] or issue['parent_issue_id'] != expected.get('parentId', ''):
            raise LinearError('远端 issue 与待同步记录不一致，请人工核实')
        return issue

    def ensure_parent(self, operation):
        payload = operation['payload']
        issue = self.issue(payload['issue_id'])
        expected = payload.get('parent_id') or ''
        if issue.get('parent_issue_id', '') == expected:
            return issue
        result = self.request('mutation OrbitIssueParent($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) { success issue { '+ISSUE_FIELDS+' } } }',
            {'id':payload['issue_id'], 'input':{'parentId':expected or None}})['issueUpdate']
        if not result.get('success'):
            raise LinearError('Linear 未确认父级修改；下次将核对远端关系')
        updated = normalize_issue(result['issue'], issue['viewer_id'])
        if updated['id'] != issue['id'] or updated.get('parent_issue_id', '') != expected:
            raise LinearError('远端 issue 父级与目标不一致，保留待同步记录')
        return updated

    def ensure_comment(self, operation):
        data = self.request('''query MomoCommentById($filter: CommentFilter!) {
          comments(first: 1, filter: $filter) { nodes { id body issue { id } } } }''',
          {'filter': {'id': {'eq': operation['id']}}})
        rows = data['comments']['nodes']
        if rows:
            row = rows[0]
            if row['body'] != operation['payload']['body'] or row['issue']['id'] != operation['payload']['issueId']:
                raise LinearError('已存在的日报评论内容或归属已改变；保留远端内容，请人工核实')
            return {'id': row['id']}
        result = self.request('''mutation MomoCreateDailyComment($input: CommentCreateInput!) {
          commentCreate(input: $input) { success comment { id } } }''',
          {'input': {'id': operation['id'], **operation['payload']}})['commentCreate']
        if not result.get('success') or result['comment']['id'] != operation['id']:
            raise LinearError('Linear 未确认日报评论成功；下次将核对相同 ID')
        return {'id': result['comment']['id']}
