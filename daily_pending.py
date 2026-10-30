"""Local-only preview; never queries Linear or publishes report content."""
from model import has_work_issue


def pending_work(tasks, config):
    by_id={task['id']:task for task in tasks}; rows=[]
    for task in tasks:
        if task['category']!='work' or task.get('is_sample'): continue
        pending=task.get('linear_parent_pending'); create=not has_work_issue(task)
        if not pending and not create: continue
        parent=by_id.get(task.get('parent_id'))
        target=task.get('linear_target') or config.get('target',{})
        reason='随父任务继承团队与项目' if parent else ('尚未设置默认团队或本任务归属' if not target.get('team_id') else '负责人为当前 Linear 用户')
        rows.append(dict(task_id=task['id'],title=task['title'],
                         action='创建 issue'+('并设置父级' if parent else '') if create else '调整 issue 父级',
                         parent=parent['title'] if parent else '独立 issue',
                         due_at=pending['due_at'] if pending else config['next_day']+' 22:00 · Asia/Shanghai',
                         reason=reason,enabled=config.get('enabled',False)))
    return rows
