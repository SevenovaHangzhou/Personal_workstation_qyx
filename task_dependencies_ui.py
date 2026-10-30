"""Inspect both sides of a dependency before explicitly editing its owner."""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QFrame, QListWidget, QListWidgetItem
from ui_helpers import box, label, button


def related_tasks(tasks, task_id):
    by_id={task['id']:task for task in tasks}
    task=by_id.get(task_id)
    if not task: return [], []
    return ([by_id[tid] for tid in task.get('depends_on',[]) if tid in by_id],
            [row for row in tasks if task_id in row.get('depends_on',[])])


class DependencyPanel(QFrame):
    def __init__(self, owner, task, tasks):
        super().__init__(); self.setObjectName('dependencyPanel')
        layout=box(self,spacing=7)
        before,after=related_tasks(tasks,task['id'])
        head=box(horizontal=True); head.addWidget(label('步骤依赖','section')); head.addStretch()
        head.addWidget(button('管理依赖','linkButton',callback=lambda:owner.manage_dependencies(task['id'])))
        layout.addLayout(head)
        if not before and not after:
            layout.addWidget(label('暂无前置或后续依赖，可按需连接步骤。','muted',True))
        for title,rows in [('先完成',before),('随后可进行',after)]:
            if not rows: continue
            layout.addWidget(label(title+f' · {len(rows)}','muted'))
            for row in rows:
                entry=box(horizontal=True,spacing=6)
                entry.addWidget(label(('✓ ' if row['status']=='done' else '○ ')+row['title'],wrap=True),1)
                action=button('打开','linkButton',callback=lambda checked=False,tid=row['id']:owner.select_task(tid))
                action.setAccessibleName('打开依赖任务：'+row['title']); entry.addWidget(action); layout.addLayout(entry)
        if before or after:
            layout.addWidget(label('仅表示规划关系，不会自动启动或完成任务。','muted',True))


class DependencyDialog(QDialog):
    def __init__(self, parent, owner, task_id):
        super().__init__(parent); self.owner=owner; self.task_id=task_id
        self.setObjectName('dependencyDialog'); self.setWindowTitle('管理步骤依赖')
        self.resize(560,480); self.setMinimumSize(460,400)
        layout=box(self,margins=(24,20,24,20),spacing=12)
        layout.addWidget(label('把前后关系，理清楚','h2'))
        self.summary=label('','muted',True); layout.addWidget(self.summary)
        layout.addWidget(label('前置依赖保存在后续任务上。选择下方任务，再明确调整它的前置步骤。','muted',True))
        self.rows=QListWidget(); self.rows.setWordWrap(True); self.rows.setAccessibleName('当前任务与直接后续任务')
        layout.addWidget(self.rows,1)
        self.edit_button=button('调整所选任务的前置步骤','primary',callback=self.edit)
        layout.addWidget(self.edit_button)
        layout.addWidget(label('保存只修改你勾选的依赖；若要移动任务，请返回原操作重新确认。','muted',True))
        layout.addWidget(button('返回',callback=self.reject))
        self.rows.currentItemChanged.connect(self.update_actions); self.refresh()

    def refresh(self, selected=None):
        tasks=self.owner.tasks.tasks; by_id={task['id']:task for task in tasks}; task=by_id.get(self.task_id)
        self.rows.clear()
        if not task:
            self.summary.setText('任务已删除，请返回任务列表。'); self.update_actions(); return
        before,after=related_tasks(tasks,self.task_id)
        self.summary.setText(task['title']+f'\n前置 {len(before)} 项 · 直接后续 {len(after)} 项')
        for row in [task]+after:
            parents=[by_id[tid]['title'] for tid in row.get('depends_on',[]) if tid in by_id]
            role='当前任务' if row['id']==self.task_id else '依赖当前任务的后续步骤'
            text=role+' · '+row['title']+'\n前置：'+('、'.join(parents) or '无')
            item=QListWidgetItem(text); item.setData(Qt.UserRole,row['id']); item.setToolTip(text); self.rows.addItem(item)
            if row['id']==selected: self.rows.setCurrentItem(item)
        if not self.rows.currentItem() and self.rows.count(): self.rows.setCurrentRow(0)
        self.update_actions()

    def update_actions(self, *unused):
        self.edit_button.setEnabled(bool(self.rows.currentItem()))

    def edit(self):
        item=self.rows.currentItem()
        if not item: return
        task_id=item.data(Qt.UserRole)
        self.owner.edit_dependencies(task_id, parent=self)
        self.refresh(task_id)
