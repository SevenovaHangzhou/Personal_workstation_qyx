"""Data-only courses. Generated content cannot install widgets or execute code."""
import copy
import json
from pathlib import Path
from dag_layout import layers_for

FORMAT = 'momo-learning-course-draft/v1'
SUBJECT_MODE = 'subject_curriculum'
BUILTIN_COURSE_ID = 'zhixing:robot-arm-control'
BLOCKS = {'paragraph', 'list', 'formula', 'table', 'code_example', 'self_check', 'callout'}


def need(condition, message):
    if not condition:
        raise ValueError('课程内容检查：'+message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def unique(rows, label):
    need(isinstance(rows, list) and all(isinstance(r, dict) and text(r.get('id')) for r in rows), label+'需要稳定 ID')
    ids = [r['id'] for r in rows]
    need(len(ids) == len(set(ids)), label+' ID 重复')
    return set(ids)


def references(row, key, ids):
    values = row.get(key)
    need(isinstance(values, list) and all(isinstance(v, str) and v in ids for v in values), key+' 引用了不存在的内容')


def validate_package(package, expected=None):
    need(isinstance(package, dict) and package.get('format') == FORMAT, '不支持的内容格式')
    need(text(package.get('generation_id')), '缺少生成 ID')
    inputs = package.get('input')
    need(isinstance(inputs, dict), '学习输入不完整')
    subject_mode = inputs.get('mode') == SUBJECT_MODE
    if subject_mode: need(not {'goal','background','scope'} & inputs.keys(), '按学习对象建课不应填入个人目标或掌握信息')
    input_keys = ('task_id','topic_id','subject','input_version') if subject_mode else ('task_id','topic_id','goal','background','scope','input_version')
    need(all(text(inputs.get(k)) for k in input_keys), '学习输入不完整')
    if expected:
        need(package['generation_id'] == expected['id'], '生成 ID 不匹配')
        need(all(inputs.get(k) == value for k,value in expected['input'].items()), '学习输入与本次请求不一致')
    sources = unique(package.get('sources'), '来源')
    need(bool(sources), '必须保留来源及访问状态')
    for source in package['sources']:
        need(all(isinstance(source.get(k), str) for k in ('title','author_or_institution','locator','version','section')), '来源字段不完整')
        need(source.get('access_status') in ('content_read','metadata_only','unavailable'), '来源访问状态无效')
    units = unique(package.get('knowledge_units'), '知识单元')
    need(bool(units), '缺少知识单元')
    if expected or subject_mode:
        need(isinstance(package.get('search_queries'),list) and all(text(q) for q in package['search_queries']), '缺少实际检索词记录')
    edges = []
    for unit in package['knowledge_units']:
        need(text(unit.get('title')) and text(unit.get('scope')), '知识边界不完整')
        references(unit, 'source_ids', sources); references(unit, 'prerequisite_ids', units)
        need(isinstance(unit.get('claims'), list), '结论记录格式错误')
        if expected or subject_mode:
            need(bool(unit['claims']), '知识单元缺少可追溯结论')
            for claim in unit['claims']:
                need(isinstance(claim,dict) and all(text(claim.get(k)) for k in ('statement','conditions','location')), '结论需包含表述、适用条件和证据定位')
                references(claim,'source_ids',sources)
        edges.extend((p,unit['id']) for p in unit['prerequisite_ids'])
    layers_for(units, edges)
    lessons = package.get('lessons'); unique(lessons, '课程')
    need(bool(lessons), '没有可阅读课程')
    question_ids = set()
    for lesson in lessons:
        need(text(lesson.get('title')) and text(lesson.get('revision')), '课程标题或修订版本缺失')
        need(all(text(lesson.get(k)) for k in ('prerequisites','goals','scope','pace')), '课程导读不完整')
        references(lesson, 'source_ids', sources); references(lesson, 'knowledge_unit_ids', units)
        sections = lesson.get('sections'); unique(sections, '小节')
        need(bool(sections), '课程正文为空')
        for section in sections:
            need(text(section.get('title')) and isinstance(section.get('blocks'), list) and section['blocks'], '小节正文为空')
            for block in section['blocks']:
                need(isinstance(block, dict) and block.get('type') in BLOCKS, '引用了尚未支持的图解或内容组件')
                kind = block['type']
                if kind == 'list':
                    need(isinstance(block.get('items'), list) and bool(block['items']) and all(text(v) for v in block['items']), '列表内容无效')
                elif kind == 'table':
                    headers, rows = block.get('headers'), block.get('rows')
                    need(isinstance(headers, list) and headers and all(text(v) for v in headers), '表头无效')
                    need(isinstance(rows, list) and all(isinstance(r, list) and len(r)==len(headers) and all(isinstance(v,str) for v in r) for r in rows), '表格行无效')
                elif kind == 'self_check':
                    need(text(block.get('prompt')) and text(block.get('answer')), '自测需要问题和参考解释')
                else:
                    need(text(block.get('text')), '正文块不能为空')
        questions = lesson.get('questions'); ids = unique(questions, '练习')
        need(bool(ids) and not ids & question_ids, '练习为空或跨课程 ID 重复'); question_ids |= ids
        for question in questions:
            need(question.get('kind') in ('choice','written') and all(text(question.get(k)) for k in ('prompt','reference_process','self_check')), '练习缺少题目、参考过程或自查点')
            references(question, 'source_ids', sources)
            if question['kind'] == 'choice':
                options = question.get('options'); opts = unique(options, '选项')
                need(len(opts)>=2 and all(text(v.get('text')) for v in options) and question.get('correct') in opts, '选择题答案或选项错误')
    need(isinstance(package.get('unresolved'), list) and all(isinstance(v,str) for v in package['unresolved']), '缺口格式错误')
    if subject_mode: validate_curriculum(package, units)
    return package


def validate_curriculum(package, unit_ids):
    """Check declared coverage and prerequisites; this cannot certify domain completeness."""
    coverage = package.get('coverage'); areas = unique(coverage, '知识覆盖范围')
    need(bool(areas), '缺少知识覆盖范围')
    covered = set()
    for area in coverage:
        need(text(area.get('title')), '知识覆盖范围缺少标题')
        references(area, 'knowledge_unit_ids', unit_ids)
        gaps = area.get('gaps')
        need(isinstance(gaps,list) and all(text(gap) for gap in gaps), '知识覆盖缺口格式错误')
        need(area['knowledge_unit_ids'] or gaps, '知识覆盖范围既无内容也未说明缺口')
        covered.update(area['knowledge_unit_ids'])
    need(covered == unit_ids, '有知识单元未纳入知识覆盖范围')
    lessons = {lesson['id']:lesson for lesson in package['lessons']}
    taught = {uid for lesson in lessons.values() for uid in lesson['knowledge_unit_ids']}
    need(taught == unit_ids, '有知识单元未安排到课程')
    stages = package.get('learning_path'); stage_ids = unique(stages, '学习路径阶段')
    need(bool(stage_ids), '缺少由浅入深的学习路径')
    positions = {}; sequence = []
    for index,stage in enumerate(stages):
        need(text(stage.get('title')) and text(stage.get('summary')), '学习阶段需要标题与说明')
        references(stage, 'lesson_ids', lessons)
        need(bool(stage['lesson_ids']), '学习阶段没有课程')
        sequence.extend(stage['lesson_ids'])
        for lid in stage['lesson_ids']: positions[lid] = index
    need(len(sequence) == len(set(sequence)) and set(sequence) == set(lessons), '学习路径必须覆盖每章且不重复')
    edges = []
    for lid,lesson in lessons.items():
        references(lesson, 'depends_on', lessons)
        need(len(lesson['depends_on']) == len(set(lesson['depends_on'])), '章节前置依赖重复')
        for parent in lesson['depends_on']:
            need(positions[parent] <= positions[lid], '前置章节不能放在后续学习阶段')
            edges.append((parent,lid))
    layers = layers_for(lessons, edges)
    units = {unit['id']:unit for unit in package['knowledge_units']}
    available = {}
    for layer in layers:
        for lid in layer:
            lesson = lessons[lid]; known = set(lesson['knowledge_unit_ids'])
            for parent in lesson['depends_on']: known.update(available[parent])
            for uid in lesson['knowledge_unit_ids']:
                need(set(units[uid]['prerequisite_ids']) <= known, '知识先修未安排到本章或前置章节')
            available[lid] = known


def ordered_lessons(package):
    """Order new curricula by stage and explicit prerequisites, preserving legacy order."""
    if package['input'].get('mode') != SUBJECT_MODE: return list(package['lessons'])
    lessons = {lesson['id']:lesson for lesson in package['lessons']}; ordered = []
    for stage in package['learning_path']:
        ids = stage['lesson_ids']; members = set(ids)
        edges = [(parent,lid) for lid in ids for parent in lessons[lid]['depends_on'] if parent in members]
        ordered.extend(lessons[lid] for layer in layers_for(ids,edges) for lid in layer)
    return ordered


def legacy_catalog(path):
    """Retain original paragraphs and stable IDs; never execute the old JavaScript."""
    original = json.loads(Path(path).read_text(encoding='utf-8'))
    courses = {}
    modules = {m['id']:m for m in original['modules']}
    for lesson in original['lessons']:
        resource_ids = lesson.get('resources', [])
        sources = [{'id':key, 'title':original['resources'][key]['title'],
                    'author_or_institution':original['resources'][key]['detail'],
                    'locator':original['resources'][key].get('url') or str(path),
                    'version':'知行原课程', 'section':original['resources'][key]['detail'],
                    'access_status':'metadata_only'} for key in resource_ids]
        if not sources:
            sources = [{'id':'local', 'title':'知行本地课程', 'author_or_institution':'原课程', 'locator':str(path), 'version':'1','section':lesson['id'],'access_status':'content_read'}]
            resource_ids = ['local']
        sections = [{'id':'intro', 'title':'本课问题', 'blocks':[{'type':'paragraph','text':lesson['intro']}]}]
        missing = []
        for i, section in enumerate(lesson['sections']):
            title, body, *extras = section; extra = extras[0] if extras else {}
            blocks = [{'type':'paragraph','text':body}]
            if extra.get('formula'): blocks.append({'type':'formula','text':extra['formula']})
            if extra.get('table'): blocks.append({'type':'table',**copy.deepcopy(extra['table'])})
            if extra.get('flow'): blocks.append({'type':'list','items':extra['flow']})
            if extra.get('check'): blocks.append({'type':'self_check',**copy.deepcopy(extra['check'])})
            if extra.get('interactive') or extra.get('figure') or extra.get('labTask'):
                message='原课程此处含图解或实验；当前显示完整文字与公式，该交互尚未迁移。'
                blocks.append({'type':'callout','text':message}); missing.append(title+'：原交互尚未迁移')
            sections.append({'id':lesson['id']+'-'+str(i),'title':title+(' · 选读' if extra.get('optional') else ''),'blocks':blocks})
        for field,title in [('formula','公式回顾'),('symbols','符号约定'),('pitfall','易错点'),('checkpoint','自查目标')]:
            if lesson.get(field): sections.append({'id':field,'title':title,'blocks':[{'type':'formula' if field=='formula' else 'paragraph','text':lesson[field]}]})
        questions = []
        for q in original['questions']:
            if q['lesson'] != lesson['id']: continue
            row = {'id':q['id'],'kind':q['kind'],'prompt':q.get('title','')+'\n'+q['prompt'],
                   'goals':q.get('level',''),'hint':q.get('hint',''),'reference_process':q['answer'],
                   'self_check':'对照参考过程检查条件、步骤与结果。','source_ids':resource_ids}
            if q['kind']=='choice':
                row.update(options=[{'id':str(i),'text':v} for i,v in enumerate(q['options'])],correct=str(q['correct']))
            questions.append(row)
        guide = lesson.get('guide', {})
        normalized = {'id':lesson['id'],'revision':'zhixing-1','title':lesson['title'],
                      'knowledge_unit_ids':[lesson['id']], 'source_ids':resource_ids, 'sections':sections,'questions':questions,
                      **{k:guide.get(k) or ('原课程未注明，请先确认。' if k!='pace' else str(lesson['time'])+' 分钟（原课程建议）') for k in ('prerequisites','goals','scope','pace')}}
        package = {'format':FORMAT,'generation_id':'zhixing-'+lesson['id'],
                   'input':{'task_id':'legacy','topic_id':lesson['module'],'goal':normalized['goals'],'background':normalized['prerequisites'],'scope':normalized['scope'],'input_version':'legacy-1'},
                   'sources':sources,'knowledge_units':[{'id':lesson['id'],'title':lesson['title'],'scope':normalized['scope'],'prerequisite_ids':[],'source_ids':resource_ids,'claims':[]}],
                   'lessons':[normalized], 'unresolved':missing, 'legacy_original':copy.deepcopy(lesson)}
        validate_package(package)
        courses['zhixing:'+lesson['id']] = {'id':'zhixing:'+lesson['id'],'title':lesson['title'],
              'origin':'知行 · '+modules[lesson['module']]['name'],'package':package,'review':'保留原正文和题目，来源访问状态未重新核验。旧浏览器作答未自动导入。'}
    return courses


def combine_legacy_course(chapters):
    """Present the imported curriculum as one course without rewriting its lessons."""
    lessons = []; units = []; sources = {}; unresolved = []; originals = []
    for chapter in chapters.values():
        package = chapter['package']
        lessons.extend(copy.deepcopy(package['lessons']))
        units.extend(copy.deepcopy(package['knowledge_units']))
        for source in package['sources']:
            previous = sources.get(source['id'])
            need(previous is None or previous == source, '原课程来源 ID 冲突')
            sources[source['id']] = copy.deepcopy(source)
        unresolved.extend(chapter['title']+'：'+message for message in package['unresolved'])
        originals.append(copy.deepcopy(package['legacy_original']))
    need(bool(lessons), '原课程没有章节')
    package = {'format':FORMAT, 'generation_id':'zhixing-robot-arm-control',
        'input':{'task_id':'legacy','topic_id':'robot-arm-control',
                 'goal':'；'.join(lesson['goals'] for lesson in lessons),
                 'background':lessons[0]['prerequisites'],
                 'scope':'；'.join(lesson['scope'] for lesson in lessons),'input_version':'legacy-1'},
        'sources':list(sources.values()),'knowledge_units':units,'lessons':lessons,
        'unresolved':unresolved,'legacy_original':originals}
    validate_package(package)
    return {'id':BUILTIN_COURSE_ID,'title':'机械臂控制','origin':'知行 · 完整课程',
            'package':package,'review':'保留原章节正文、顺序和题目，来源访问状态未重新核验。旧浏览器作答未自动导入。'}
