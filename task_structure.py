"""Structural projections deliberately omit notes, status, links and learning data."""
import copy
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def projection(tasks):
    return [{key:copy.deepcopy(task.get(key, [] if key=='depends_on' else {} if key=='linear_target' else None))
             for key in ('id','category','parent_id','depends_on','linear_target')} for task in tasks]


def parent_sync(parent_id):
    now=datetime.now(ZoneInfo('Asia/Shanghai')); due=now.replace(hour=22,minute=0,second=0,microsecond=0)
    if now>=due: due+=timedelta(days=1)
    return dict(parent_id=parent_id,revision=str(uuid.uuid4()),due_at=due.isoformat())


def impact(tasks, task_id, target_id=None, mode='detach'):
    by_id={t['id']:t for t in tasks}; source=by_id[task_id]
    target=by_id.get(target_id)
    parent=target_id if mode=='child' else target.get('parent_id') if target else None
    subtree={task_id}
    for _ in range(len(tasks)):
        expanded=subtree|{t['id'] for t in tasks if t.get('parent_id') in subtree}
        if expanded==subtree: break
        subtree=expanded
    return dict(task_id=task_id,title=source['title'],subtree_count=len(subtree),
                from_title=by_id.get(source.get('parent_id'),{}).get('title','独立任务'),
                to_title=by_id.get(parent,{}).get('title','独立任务'),
                adds_dependency=target['title'] if target and mode=='after' else '',
                linear_sync=source['category']=='work' and source.get('parent_id')!=parent)


def impact_text(value):
    text=f"位置：{value['from_title']} → {value['to_title']}\n保留并随行：{value['subtree_count']} 项任务（含当前任务及全部子步骤）"
    if value['adds_dependency']: text+='\n新增前置：'+value['adds_dependency']
    if value['linear_sync']: text+='\nLinear issue 父级：下次 22:00 同步'
    return text
