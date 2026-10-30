"""Shared hierarchy cues for native list and board task cards."""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QSizePolicy, QFrame
from ui_helpers import label, button, box


class RelationLabel(QLabel):
    def __init__(self, text, name):
        super().__init__()
        self.full_text = text
        self.setObjectName(name)
        self.setTextFormat(Qt.PlainText)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    def resizeEvent(self, event):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.ElideRight, self.contentsRect().width()))
        super().resizeEvent(event)


def build_context_card(card, entry, owner, compact=False):
    """Ancestors excluded by a filter are slim navigation paths, not full results."""
    layout = box(card, horizontal=not compact, margins=(12, 8, 12, 8), spacing=6)
    text = box(spacing=3)
    text.addWidget(label('父级路径 · 当前筛选外', 'contextBadge'))
    title = label(entry.task['title'], 'contextTitle', True)
    text.addWidget(title)
    layout.addLayout(text, 1)
    action = '收起' if entry.expanded else '展开'
    arrow = '▾' if entry.expanded else '▸'
    toggle = button(f'{arrow} {action}路径', 'stepToggle',
                    callback=lambda checked=False: owner.toggle_task_children(entry.task['id']))
    toggle.setAccessibleName(f'{action}「{entry.task["title"]}」的子步骤')
    layout.addWidget(toggle)
    card.setToolTip('父任务不符合当前筛选，仅显示所属路径；点击查看父任务详情。')


def add_relations(layout, task, owner, entry):
    parents = owner.task_outline.ancestors(task['id'])
    if parents:
        badge = RelationLabel('子步骤 · ' + parents[-1]['title'], 'substepBadge')
        badge.setToolTip('所属路径：' + ' / '.join(parent['title'] for parent in parents))
        layout.addWidget(badge)
    predecessors = [owner.task_outline.by_id[tid]['title'] for tid in task.get('depends_on', [])
                    if tid in owner.task_outline.by_id]
    if predecessors:
        layout.addWidget(RelationLabel('前置 · ' + '、'.join(predecessors), 'dependencyHint'))


def add_step_toggle(layout, task, owner, entry, compact=False):
    children = owner.task_outline.children[task['id']]
    if not children:
        return
    total = len(children)
    done = sum(child['status'] == 'done' for child in children)
    if not entry or not entry.available_children:
        summary = label(f'{total} 个子步骤 · 完成 {done}/{total}', 'muted', True)
        summary.setToolTip('当前筛选中没有子步骤；在任务详情中查看全部步骤。')
        layout.addWidget(summary)
        return
    action = '收起' if entry.expanded else '展开'
    arrow = '▾' if entry.expanded else '▸'
    text = f'{arrow} {action} {total} 个子步骤' + ('\n' if compact else '  ·  ') + f'已完成 {done}/{total}'
    toggle = button(text, 'stepToggle', callback=lambda checked=False: owner.toggle_task_children(task['id']))
    toggle.setProperty('expanded', entry.expanded)
    toggle.setAccessibleName(f'{action}「{task["title"]}」的子步骤')
    toggle.setToolTip('展开当前筛选中的子步骤；完整步骤和路线保留在任务详情中。')
    layout.addWidget(toggle)


def add_outline_card(layout, card, entry, compact=False):
    if entry.depth == 0:
        layout.addWidget(card)
        return
    branch = QFrame()
    branch.setObjectName('stepBranch')
    branch.setProperty('taskBranch', True)
    indent = min(entry.depth, 3) * (5 if compact else 16)
    row = box(branch, True, (indent, 0, 0, 0), 7 if compact else 10)
    rail = QFrame()
    rail.setObjectName('stepRail')
    rail.setFixedWidth(2)
    row.addWidget(rail)
    row.addWidget(card, 1)
    layout.addWidget(branch)
