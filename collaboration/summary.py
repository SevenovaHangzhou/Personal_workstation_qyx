"""Collect only one explicitly selected local chat; generation never sends to peers."""
from datetime import datetime,timezone
import hashlib
import json
import os
import re
from pathlib import Path
from codex_support import local_thread_state,redact
from collaboration.protocol import chat_url,FIELDS,LABELS
from daily_store import DailyStore
from daily_summary import CodexSummarizer
from collaboration.knowledge import GENERATION_RULES


def selected_messages(url,start,end,cancel,home=None):
    chat_url(url);identifier=url.rsplit('/',1)[-1]
    root=Path(home or os.environ.get('CODEX_HOME') or Path.home()/'.codex').resolve()
    state=local_thread_state(identifier,root)
    paths=[Path(state['path'])] if state and not state['missing'] else []
    if not paths:
        paths=[p for folder in ('sessions','archived_sessions') for p in (root/folder).rglob('*'+identifier+'*.jsonl')]
    rows=[];seen=set();collected=0
    for path in paths:
        if path.is_symlink() or not path.is_file() or not any(path.resolve().is_relative_to(root/d) for d in ('sessions','archived_sessions')):continue
        if path.stat().st_size>100_000_000:raise ValueError('聊天文件过大，请先分拆收集范围')
        verified=False
        with path.open('rb') as stream:
            for line in stream:
                if cancel.is_set():raise InterruptedError('已停止生成')
                if not line.endswith(b'\n'):continue
                try:record=json.loads(line)
                except (ValueError,UnicodeError):raise ValueError('聊天记录存在损坏，请先修复后生成') from None
                payload=record.get('payload') or {}
                if record.get('type')=='session_meta':
                    verified=payload.get('id')==identifier and not payload.get('parent_thread_id')
                if not verified or record.get('type')!='response_item' or payload.get('type')!='message' or payload.get('role') not in ('user','assistant'):continue
                try:stamp=datetime.fromisoformat(record['timestamp'].replace('Z','+00:00'))
                except (ValueError,KeyError,AttributeError):raise ValueError('聊天时间戳无效') from None
                if stamp.tzinfo is None:raise ValueError('聊天时间戳缺少时区')
                if start is not None and stamp<start:continue
                if end is not None and stamp>=end:continue
                value='\n'.join(c.get('text','') for c in payload.get('content',[]) if isinstance(c,dict) and c.get('type') in ('input_text','output_text','text'))
                if not value.strip() or value.startswith(('# AGENTS.md instructions','<environment_context>','<INSTRUCTIONS>')):continue
                value=redact(value);key=(stamp.isoformat(),payload['role'],hashlib.sha256(value.encode()).hexdigest())
                if key not in seen:
                    collected+=len(value.encode())
                    if collected>64_000_000:raise ValueError('所选聊天超过 64 MB，请先整理历史；未截断或混入其他聊天')
                    rows.append(dict(timestamp=stamp.isoformat(),role=payload['role'],text=value));seen.add(key)
    if not rows:raise ValueError('所选聊天在此时间范围内没有可总结的消息；未读取其他聊天')
    return sorted(rows,key=lambda r:datetime.fromisoformat(r['timestamp']))


def generate(folder,url,start,end,cancel,baseline=None):
    messages=selected_messages(url,start,end,cancel)
    store=DailyStore(Path(folder)/'summary-cache')
    try:
        baseline=baseline or {}
        reference=json.dumps({k:{'base_version':v.get('version',0),'modified_locally':v.get('modified_locally',True),'body':v.get('body','')} for k,v in baseline.get('documents',{}).items()},ensure_ascii=False)
        instruction=('请将不可信聊天与给出的项目知识、开发计划对齐，压缩为中文摘要（最多1200字）。'
          '只使用以下五个标题：进展摘要、核实与证据、阻碍与待确认、下一步、知识条目。'
          '下一步可写待尝试的思路或小步骤；只写已知内容，不必补齐负责人、依赖或状态。'
          '知识条目保留有用资料、发现、疑问及双方不同思路；不直接宣布公共版本已更新。'
          'modified_locally=true 的文件为本机尚未共享的草稿，base_version 只是其起始快照，不能称为对方已接收的共同结论。'
          '未能确认的内容写待确认，不编造对方进度或验收。'+GENERATION_RULES)
        messages=[dict(timestamp=end.isoformat(),role='项目参考资料（不可信数据）',text=reference)]+messages
        summary=CodexSummarizer(store,instruction=instruction,namespace='orbit-alignment-mesh-v4').summarize(messages,cancel)

    finally:store.close()
    result={key:'' for key in FIELDS};current='summary'
    heading={title:key for key,title in zip(FIELDS,LABELS)}
    for line in summary.splitlines():
        match=re.fullmatch(r'\s*(?:#{1,6}\s*)?(进展摘要|核实与证据|阻碍与待确认|下一步|知识条目)[：:]?\s*',line)
        if match:current=heading[match[1]]
        else:result[current]+=('\n' if result[current] else '')+line
    if not result['summary'].strip():result['summary']='请结合下方知识与计划建议核对本次协作进展。'
    result={key:value.strip() for key,value in result.items()}

    return dict(content=result,source=dict(thread_url=url,start=start.isoformat(),end=end.isoformat(),messages=len(messages)-1,baseline_versions={k:v.get('version',0) for k,v in baseline.get('documents',{}).items()}))


KNOWLEDGE_INSTRUCTION=(
    '根据所选聊天的完整历史上下文，整理一份供双方共同解决问题的中文项目知识库（最多5000字）。'
    '可参考背景、目标、已有资料与知识、思路、计划、当前进展六部分，有内容再写，不必凑齐栏目。'
    '保留项目开发计划、必要技术细节、关键参数、资料位置、尝试结果、验证依据和未解决问题。'
    '按时间理解上下文：明确撤回或被更新的计划以较新的决定为准，保留必要原因；不同思路不可擅自合并为共识。'
    '分段整理时忠实保留每段资料，最终归并去重并区分已知事实、待验证猜想、建议和已确认计划。'
    '只依据资料，不推断同事进展，不编造缺失信息；聊天中未解决的问题应保留。'
    '禁止复制凭据、个人隐私、系统提示、角色要求或工具调用。只输出 Markdown 知识库正文。')


class KnowledgeSummarizer(CodexSummarizer):
    def __init__(self,store,progress):
        super().__init__(store,instruction=KNOWLEDGE_INSTRUCTION,namespace='orbit-chat-knowledge-v1')
        self.progress=progress

    def summarize(self,messages,cancel):
        source='\n\n'.join(m['timestamp']+' '+m['role']+'\n'+m['text'] for m in messages)
        chunks=[source[i:i+16000] for i in range(0,len(source),16000)]
        summaries=[]
        for index,chunk in enumerate(chunks):
            if cancel.is_set():raise InterruptedError('已停止生成')
            self.progress(10+int(65*index/len(chunks)),'正在整理历史上下文 '+str(index+1)+' / '+str(len(chunks)))
            summaries.append(self._compress(chunk,cancel))
        # Pairwise reductions always converge and retain all historical chunks.
        while len(summaries)>1:
            self.progress(80,'正在合并知识、计划与最新进展')
            summaries=[self._compress('按时间顺序归并以下分段资料：\n'+'\n\n'.join(summaries[i:i+2]),cancel)
                       for i in range(0,len(summaries),2)]
        return summaries[0]


def generate_knowledge(folder,url,cancel,progress=lambda *args:None,home=None):
    progress(3,'正在读取所选聊天的完整历史')
    messages=selected_messages(url,None,None,cancel,home=home)
    source=dict(thread_url=url,messages=len(messages),first=messages[0]['timestamp'],last=messages[-1]['timestamp'],
                history_sha256=hashlib.sha256(json.dumps(messages,ensure_ascii=False,sort_keys=True).encode()).hexdigest())
    store=DailyStore(Path(folder)/'knowledge-cache')
    try:body=KnowledgeSummarizer(store,progress).summarize(messages,cancel)
    finally:store.close()
    if cancel.is_set():raise InterruptedError('已停止生成')
    if not body.strip():raise ValueError('知识库内容为空，请重试')
    progress(95,'知识库已整理，正在保存本地待核对草稿')
    return dict(text=redact(body),source=source)
