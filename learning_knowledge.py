"""Deterministic, chapter-local reference points from existing course content."""


def lesson_keypoints(package, lesson_id):
    lesson = next((row for row in package['lessons'] if row['id'] == lesson_id), None)
    if lesson is None:
        raise ValueError('找不到当前章节')
    formulas, recaps, claims = [], [], []
    for section in lesson['sections']:
        for block in section['blocks']:
            if block['type'] != 'formula' or not block['text'].strip():
                continue
            point = dict(kind='formula', title=section['title'], text=block['text'],
                         scope=lesson.get('scope', ''), conditions='',
                         location='本章正文 · ' + section['title'],
                         source_ids=list(lesson.get('source_ids', [])),
                         source_label='章节来源')
            # Prefer an explicitly labelled recap; do not infer a new summary.
            collection = recaps if section['title'].strip() in (
                '公式回顾', '公式汇总', '核心公式', '关键公式') else formulas
            collection.append(point)
    linked = set(lesson.get('knowledge_unit_ids', []))
    for unit in package['knowledge_units']:
        if unit['id'] not in linked:
            continue
        for claim in unit['claims']:
            text = claim if isinstance(claim, str) else claim.get('statement', '')
            if not text.strip():
                continue
            detail = {} if isinstance(claim, str) else claim
            claims.append(dict(kind='claim', title=unit['title'], text=text,
                               scope=unit.get('scope', ''), conditions=detail.get('conditions', ''),
                               location=detail.get('location', ''),
                               source_ids=list(detail.get('source_ids') or unit.get('source_ids', [])),
                               source_label='结论来源'))
    return recaps + formulas + claims


def keypoint_excerpt(point):
    lines = [('公式' if point['kind'] == 'formula' else '知识结论') + ' · ' + point['title'], point['text']]
    for key, title in [('scope', '适用范围'), ('conditions', '适用条件'), ('location', '内容定位')]:
        if point[key]:
            lines.append(title + '：' + point[key])
    if point['source_ids']:
        lines.append(point['source_label'] + '：' + '、'.join(point['source_ids']))
    return '\n\n'.join(lines)
