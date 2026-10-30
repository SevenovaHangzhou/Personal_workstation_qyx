"""Choose task connections across collapsed groups and current view filters."""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QScrollArea
from task_outline import TaskOutline
from ui_helpers import box, label, button


class TaskMoveDialog(QDialog):
    def __init__(self, owner, task_id):
        super().__init__(owner)
        self.owner=owner; self.client=owner.tasks; self.task_id=task_id; self.target_id=None; self.mode=None
        self.setObjectName('taskMoveDialog'); self.setWindowTitle('移动与连接任务')
        self.resize(560,580); self.setMinimumSize(460,480)
        task=self.client.get(task_id); outline=TaskOutline(self.client.tasks)
        layout=box(self,margins=(24,20,24,20),spacing=12)
        layout.addWidget(label('给这一步，换一个位置','h2'))
        layout.addWidget(label('当前任务：'+task['title'],'muted',True))
        layout.addWidget(label('在同类任务中选择目标，包含其他日期和已收起的步骤。','muted',True))
        self.search=QLineEdit(); self.search.setPlaceholderText('搜索任务名称或所属路径'); self.search.setObjectName('moveTaskSearch')
        self.search.setClearButtonEnabled(True); layout.addWidget(self.search)
        self.targets=QListWidget(); self.targets.setWordWrap(True); self.targets.setObjectName('moveTaskTargets')
        self.targets.setAccessibleName('可连接的目标任务'); layout.addWidget(self.targets,1)
        for target in outline.tasks:
            ancestors=outline.ancestors(target['id'])
            if target['category']!=task['category'] or target['id']==task_id or any(a['id']==task_id for a in ancestors):
                continue
            path=' / '.join(a['title'] for a in ancestors) or '独立任务'
            text=target['title']+'\n'+path+' · '+target['day']
            item=QListWidgetItem(text); item.setData(Qt.UserRole,target['id']); item.setToolTip(text)
            self.targets.addItem(item)
        self.reason=label('先选择目标任务。','muted',True)
        self.preview=QScrollArea(); self.preview.setWidgetResizable(True); self.preview.setFrameShape(QScrollArea.NoFrame)
        self.preview.setMinimumHeight(90); self.preview.setMaximumHeight(130); self.preview.setWidget(self.reason)
        self.preview.setAccessibleName('任务移动影响预览'); layout.addWidget(self.preview)
        layout.addWidget(button('管理相关依赖','linkButton',callback=self.manage_dependencies))
        actions=box(horizontal=True)
        self.child_button=button('作为子步骤合入','primary',callback=lambda:self.choose('child'))
        self.after_button=button('接在此任务之后','outline',callback=lambda:self.choose('after'))
        actions.addWidget(self.child_button); actions.addWidget(self.after_button); layout.addLayout(actions)
        layout.addWidget(label('保留笔记、聊天、课程和完整子树。工作任务的 issue 父级在 22:00 同步。','muted',True))
        layout.addWidget(button('取消',callback=self.reject))
        self.targets.currentItemChanged.connect(self.update_actions); self.search.textChanged.connect(self.filter_targets)
        self.update_actions(); self.search.setFocus()

    def filter_targets(self, text):
        text=text.strip().casefold()
        for index in range(self.targets.count()):
            item=self.targets.item(index); item.setHidden(text not in item.text().casefold())
        self.update_actions()

    def update_actions(self, *unused):
        item=self.targets.currentItem()
        self.target_id=item.data(Qt.UserRole) if item and not item.isHidden() else None
        reasons=[]
        previews=[]
        for mode,action in [('child',self.child_button),('after',self.after_button)]:
            error='先选择目标任务。'
            if self.target_id:
                try:
                    result=self.client.check_merge(self.task_id,self.target_id,mode); error=''
                    from task_structure import impact_text
                    previews.append(('合入：' if mode=='child' else '后续：')+impact_text(result['impact']))
                except (ValueError,OSError) as exc: error=str(exc)
            action.setEnabled(bool(self.target_id) and not error); action.setToolTip(error)
            if error and error not in reasons: reasons.append(error)
        visible=sum(not self.targets.item(i).isHidden() for i in range(self.targets.count()))
        self.reason.setText(('没有匹配的同类任务，可调整搜索或先创建目标任务。' if not visible else
                             '\n'.join(reasons) if reasons else
                             '合入会成为目标的一部分；接在之后会建立同层前置依赖。'))
        if self.target_id and previews: self.reason.setText(self.reason.text()+'\n'+('\n\n'.join(previews)))

    def manage_dependencies(self):
        self.owner.manage_dependencies(self.task_id,self); self.update_actions()

    def choose(self, mode):
        self.update_actions()
        action=self.child_button if mode=='child' else self.after_button
        if self.target_id and action.isEnabled():
            self.mode=mode; self.accept()
