"""Evidence-based coarse stages; never estimate model time or percentages."""
from datetime import datetime,timezone
import re

STAGES=[('starting','启动 Codex'),('research','检索与建立知识库'),('outline','组织学习路径'),
        ('writing','编写课程与练习'),('review','复核教学内容'),('validate','程序检查'),('save','保存并接入')]
STAGE_IDS=[key for key,_ in STAGES]
MODEL_STAGES={'research','outline','writing','review'}


def now_text():
    return datetime.now(timezone.utc).isoformat()


def advance_progress(job,stage):
    if stage not in STAGE_IDS: return
    previous=job.get('progress',{}).get('stage')
    if previous in STAGE_IDS and STAGE_IDS.index(stage)<=STAGE_IDS.index(previous): return
    job['progress']={'stage':stage,'changed_at':now_text(),'reported_by':'model' if stage in MODEL_STAGES else 'application'}


def model_stage(event):
    if event.get('type') not in ('item.started','item.completed'): return None
    item=event.get('item',{})
    if isinstance(item,dict) and item.get('type')=='web_search': return 'research'
    if event.get('type')!='item.completed': return None
    if not isinstance(item,dict) or item.get('type')!='agent_message': return None
    for line in str(item.get('text','')).splitlines():
        match=re.fullmatch(r'\[ORBIT_STAGE:(research|outline|writing|review)\]',line.strip())
        if match: return match[1]
    return None


def progress_view(job,now=None):
    status=job['status']; stage=job.get('progress',{}).get('stage')
    index=STAGE_IDS.index(stage) if stage in STAGE_IDS else None
    complete=status=='review' and bool(job.get('course_id'))
    if complete: value=len(STAGES); title='课程草稿已就绪 · 待审阅'
    elif status=='queued': value=0; title='等待生成'
    elif index is None: value=0; title='生成中 · 正在检索或编写' if status=='running' else '生成已结束'
    else:
        value=index; title=f'阶段 {index+1}/{len(STAGES)} · '+STAGES[index][1]
        if stage=='starting' and job.get('thread_id'): title='生成中 · 等待阶段进度'
    if status in ('failed','cancelled','interrupted','needs_input'):
        title={'failed':'生成失败','cancelled':'已停止','interrupted':'已中断','needs_input':'需要澄清对象'}[status]+(' · 停在'+STAGES[index][1] if index is not None else '')
    start=job.get('started_at')
    end=job.get('finished_at')
    elapsed=''
    if start:
        try:
            stop=datetime.fromisoformat(end) if end else (now or datetime.now(timezone.utc))
            seconds=max(0,int((stop-datetime.fromisoformat(start)).total_seconds()))
            elapsed=f'{seconds//60:02d}:{seconds%60:02d}'
        except (ValueError,TypeError): pass
    return {'value':value,'maximum':len(STAGES),'title':title,'elapsed':elapsed,
            'source':job.get('progress',{}).get('reported_by','unknown')}
