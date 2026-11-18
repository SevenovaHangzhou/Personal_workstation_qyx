"""Reconcile host-owned chats and persist a local, user-selected creation plan."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from .core import OrbitError, absolute_directory


def plan_path(directory, plan_id):
    try:
        value = str(uuid.UUID(plan_id))
    except (ValueError, TypeError, AttributeError):
        raise OrbitError('invalid_plan', '卡片计划编号无效。') from None
    return Path(directory) / 'chat-plans' / (value + '.json')


def save_plan(directory, plan):
    path = plan_path(directory, plan['plan_id'])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(plan, stream, ensure_ascii=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_plan(directory, plan_id):
    try:
        return json.loads(plan_path(directory, plan_id).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise OrbitError('invalid_plan', '卡片计划不存在或无法读取，请重新拉取。') from None


def select_plan(directory, plan_id, selection):
    plan = read_plan(directory, plan_id)
    if plan['state'] != 'waiting':
        raise OrbitError('plan_closed', '此卡片已处理，请返回 Codex 继续。')
    missing = {r['identifier'] for r in plan['missing']}
    projects = {p['projectId']: p for p in plan['projects']}
    if len({r['identifier'] for r in selection}) != len(selection):
        raise OrbitError('invalid_selection', '任务重复。')
    for row in selection:
        if row['identifier'] not in missing or row['project_id'] not in projects:
            raise OrbitError('invalid_selection', '任务或项目不在此卡片中。')
        absolute_directory(projects[row['project_id']]['path'])
    plan.update(state='selected' if selection else 'cancelled', selection=selection)
    save_plan(directory, plan)
    return plan


def reconcile(orbit, cancel, threads, projects):
    payload = orbit.fetch(orbit.credential(), cancel_event=cancel)
    orbit.check_cancel(cancel)
    with orbit.db() as db:
        local = {r['issue_id']: orbit.task_row(r) for r in db.execute('SELECT * FROM tasks')}
    plan = dict(plan_id=str(uuid.uuid4()), state='waiting', bound=[], missing=[], conflicts=[], selection=[], projects=[])
    for p in projects:
        if p.get('projectKind') == 'local' and p.get('hostId') == 'local':
            try:
                absolute_directory(p['path'])
                plan['projects'].append(p)
            except OrbitError:
                pass
    chats = {t['id']: t for t in threads if t.get('kind') == 'codex' and t.get('hostId') == 'local'}
    for issue in payload['issues']:
        orbit.check_cancel(cancel)
        row = {k: issue.get(k, '') for k in ('identifier', 'title', 'url')}
        previous = local.get(issue['id'])
        if previous and previous['session_id']:
            plan['bound'].append({**row, 'thread_id': previous['session_id']})
            continue
        candidates = [t for t in chats.values() if t['title'].strip() in
                      (issue['title'].strip(), issue['identifier'] + ' · ' + issue['title'])]
        if len(candidates) == 1:
            t = candidates[0]
            try:
                task = orbit.start_task(cancel, issue['identifier'], t['cwd'], t['id'])['task']
                plan['bound'].append({**row, 'thread_id': task['session_id']})
            except OrbitError as exc:
                plan['conflicts'].append({**row, 'reason': str(exc), 'candidates': [t]})
        elif candidates:
            plan['conflicts'].append({**row, 'reason': '多个同名聊天，请选择后绑定。', 'candidates': candidates})
        else:
            plan['missing'].append(row)
    save_plan(orbit.directory, plan)
    return plan


def open_card(orbit, plan_id):
    read_plan(orbit.directory, plan_id)
    from .settings import desktop_environment
    env = desktop_environment()
    if env.get('QT_QPA_PLATFORM') in ('offscreen', 'minimal') or (sys.platform.startswith('linux') and not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY'))):
        return {'state': 'ui_unavailable', 'plan_id': plan_id, 'message': '当前会话没有可见桌面。'}
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/mcp.py'),
               '--chat-card', plan_id, '--data-dir', str(orbit.directory)]
    options = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, **options)
    return {'state': 'opening', 'plan_id': plan_id}
