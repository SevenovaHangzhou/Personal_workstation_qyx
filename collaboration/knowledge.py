"""One optional shared note for solving a problem together; no content schema."""
from pathlib import Path

NOTE_FILE='COLLABORATION.md'
DOCUMENTS=[NOTE_FILE,'KNOWLEDGE.md','PLAN.md','knowledge/DECISIONS.md','knowledge/EVIDENCE.md','knowledge/HANDOFF.md']

GENERATION_RULES=('以共同解决当前问题为目的，优先参考 COLLABORATION.md，并按需参考已有知识和计划。'
    '围绕背景、目标、已有资料与知识、思路、计划、当前进展整理有用信息；只写这次有变化的内容，不必凑齐栏目。'
    '保留双方不同思路、已尝试的方法及结果，区分猜想和已确认内容，不猜测缺失信息，不把建议写成已完成。'
    '资料尽量给链接或文件位置。内容可以自由增删、改标题、拆成文件，不要求编号、JSON 索引或固定状态。'
    '本机尚未共享的内容仍是草稿；仅生成本机建议，预览确认后再共享。')

SPEC_TEXT='''# 项目知识库：轻量创建标准

通常一份 COLLABORATION.md 就够了，下面六个部分只是写作参考，有内容再写，可以改标题、合并或删掉。

## 背景
发生了什么？现在为什么要一起处理？补充必要的环境与现象。

## 目标
这次想解决什么，做到什么程度就够了？

## 已有资料与知识
手头有哪些文档、代码、链接、数据和已知结论？简要说明与问题的关系。

## 思路
有哪些解释或解决方向，各自有什么考虑？猜想和不同意见都可以保留。

## 计划
接下来准备试什么，谁方便做哪部分？可以写下一小步，也可以列几个并行方向。

## 当前进展
已经做了什么、结果如何、遇到什么阻碍、下一步接着做什么？

日常更新只补充发生变化的部分，不必重复背景或写完整报告。交接前把进展和下一步说清楚即可。
资料多了再自行拆文件并在笔记中放链接；已有 KNOWLEDGE.md、PLAN.md 等文档可以继续用。
无需维护 JSON、条目编号或固定状态。笔记为空、缺栏目、改标题或不用这份笔记，都不会阻止知识库同步。
协作时先选择同事，再明确选择本机 Codex 聊天；不要求预先绑定任务。
双方确认后，从各自所选聊天的完整历史上下文整理知识库，包含项目开发计划；生成期间可继续使用应用。
生成结果先作为本机待核对草稿，核对后保存；若原笔记已改变，保留原笔记并手动合并。
星序负责 LAN Mesh 双方配对、协作确认、共享预览和版本留存。
双方各自编辑自己的知识库，对方已共享的版本同步为单独本地副本；互不覆盖，不同步代码工作区。
'''


def safe_file(root,name):
    from collaboration.files import allowed_path
    allowed_path(name)
    path=Path(root)
    for part in Path(name).parts:
        path=path/part
        if path.is_symlink():raise ValueError('共享文档不能含符号链接：'+name)
    return path


def initialize(root,project,title):
    """Only create the optional note. Preserve all existing files, including v1."""
    root=Path(root).resolve()
    if not root.is_dir():raise ValueError('请选择现有项目目录')
    path=safe_file(root,NOTE_FILE)
    if path.exists():
        if not path.is_file():raise ValueError('协作笔记路径被目录占用：'+NOTE_FILE)
        return []
    links=[]
    for name in ('KNOWLEDGE.md','PLAN.md'):
        if safe_file(root,name).is_file():links.append('['+name+']('+name+')')
    title=str(title).replace('\n',' ').replace('\r',' ').strip() or '一起解决的问题'
    note=('# '+title+'\n\n## 背景\n\n\n## 目标\n\n\n## 已有资料与知识\n\n'
          +('已有文档：'+' · '.join(links)+'\n' if links else '')
          +'\n## 思路\n\n\n## 计划\n\n\n## 当前进展\n\n')
    try:
        with path.open('x',encoding='utf-8',newline='\n') as stream:stream.write(note)
        return [NOTE_FILE]
    except FileExistsError:return []
