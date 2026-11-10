"""Check observable teaching structure, without claiming to grade pedagogy."""
TEACHING_STANDARD = 'orbit-teaching/v2'


def teaching_audit(package, required=False):
    errors=[]; lessons=[]
    active=required or package.get('teaching_standard')==TEACHING_STANDARD
    if required and package.get('teaching_standard')!=TEACHING_STANDARD:
        errors.append('缺少本次要求的教学标准版本')
    if active:
        for lesson in package['lessons']:
            issues=[]; guide=lesson.get('teaching',{})
            if not isinstance(guide,dict): guide={}
            for key,title in [('question','本章问题'),('bridge','先修衔接'),('takeaway','核心结论')]:
                if not isinstance(guide.get(key),str) or not guide[key].strip(): issues.append('缺少'+title)
            sections={s['id']:s for s in lesson['sections']}
            explanation=sections.get(guide.get('explanation_section_id'))
            if not explanation or not any(b['type']=='paragraph' for b in explanation['blocks']):
                issues.append('缺少有正文讲解的推理小节')
            blocks=[b for s in lesson['sections'] for b in s['blocks']]
            if not any(b['type']=='worked_example' for b in blocks): issues.append('缺少完整分步案例')
            if sum(b['type']=='self_check' for b in blocks)<2: issues.append('缺少两处折叠自测')
            levels={q.get('level') for q in lesson['questions']}
            if len(lesson['questions'])<3 or levels!={'recognition','application','transfer'}:
                issues.append('练习需覆盖起步、跟随和迁移三个层次')
            visual=guide.get('visual',{})
            if (not isinstance(visual,dict) or type(visual.get('required')) is not bool
                    or not isinstance(visual.get('reason'),str) or not visual['reason'].strip()):
                issues.append('缺少图解必要性说明')
            elif visual['required'] and not any(b['type']=='diagram' for b in blocks):
                issues.append('声明需要图解但尚未提供')
            lessons.append({'id':lesson['id'],'title':lesson['title'],'errors':issues})
            errors.extend(lesson['title']+'：'+issue for issue in issues)
    return {'standard':TEACHING_STANDARD if active else 'legacy','errors':errors,'lessons':lessons,
            'scope':'仅检查教学结构、示例步骤和练习层次；事实、解释质量、来源与答案仍需审阅。'}
