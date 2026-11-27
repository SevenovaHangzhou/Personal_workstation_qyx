"""A modeless progress question window using the existing evidence query service."""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QApplication, QComboBox, QDialog, QFormLayout,
    QHBoxLayout, QMessageBox, QPlainTextEdit, QTabWidget, QVBoxLayout)

from evaluation_ui import action, note


class ProgressQuestionDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle('进度提问 · 星序管理员')
        self.resize(840, 720)
        self.setMinimumSize(680, 540)
        self.request_id = None
        self.request_scope = None
        self.request_label = ''
        self.error = ''
        self.snapshot = {}
        self.available_people = {''}
        self.available_issues = {''}
        layout = QVBoxLayout(self)
        self.title = note('了解人员与任务的进展')
        self.title.setObjectName('h2')
        layout.addWidget(self.title)
        form = QFormLayout()
        self.person, self.issue = QComboBox(), QComboBox()
        for combo in (self.person, self.issue):
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.currentIndexChanged.connect(self.edited)
        form.addRow('人员', self.person)
        form.addRow('Issue', self.issue)
        layout.addLayout(form)
        self.scope_hint = '人员按已归档的日志归属筛选；issue 包含子任务。两项都选时，只查询该人员在此 issue 下的日志。'
        self.hint = note(self.scope_hint)
        self.hint.setObjectName('muted')
        self.hint.setToolTip(self.scope_hint)
        layout.addWidget(self.hint)
        shortcuts = QHBoxLayout()
        self.shortcuts = []
        for label, question in [
            ('当前进展', '目前进展如何？请按 issue 列出已完成和正在推进的工作，并引用日志依据。'),
            ('阻塞与协调', '目前有哪些明确的阻塞、待核实事项，需要管理者协调什么？请引用日志依据。'),
            ('下一步', '下一步需要完成什么？请区分日志中明确的计划、建议和缺少的资料，并引用依据。'),
        ]:
            button = action(label, lambda text=question: self.question.setPlainText(text))
            self.shortcuts.append(button)
            shortcuts.addWidget(button)
        shortcuts.addStretch()
        layout.addLayout(shortcuts)
        self.question = QPlainTextEdit()
        self.question.setPlaceholderText('输入问题，例如：最近推进了哪些工作？还有什么需要我协调？')
        self.question.setMaximumHeight(85)
        self.question.setMinimumHeight(55)
        self.question.setPlainText('目前进展如何？请说明已完成、正在推进、阻塞和下一步，并引用日志依据。')
        self.question.textChanged.connect(self.edited)
        layout.addWidget(self.question)
        buttons = QHBoxLayout()
        self.ask_button = action('AI 回答', lambda: self.submit(True))
        self.ask_button.setObjectName('primary')
        self.local_button = action('查看日志', lambda: self.submit(False))
        self.stop_button = action('停止', lambda: owner.perform(owner.client.cancel))
        for button in (self.ask_button, self.local_button, self.stop_button):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.result_label = note('')
        self.result_label.setObjectName('muted')
        self.result_label.setWordWrap(False)
        self.result_label.setMinimumWidth(0)
        layout.addWidget(self.result_label)
        self.result_tabs = QTabWidget()
        self.answer, self.sources = QPlainTextEdit(), QPlainTextEdit()
        for editor in (self.answer, self.sources):
            editor.setReadOnly(True)
        self.answer.setPlaceholderText('选择范围并输入问题。“AI 回答”会依据共享日志总结；“查看日志”直接展示相关原文。')
        self.result_tabs.addTab(self.answer, '回答')
        self.result_tabs.addTab(self.sources, '日志依据')
        layout.addWidget(self.result_tabs, 1)
        self.notice = note('')
        layout.addWidget(self.notice)
        footer = QHBoxLayout()
        self.copy_button = action('复制回答', lambda: QApplication.clipboard().setText(self.answer.toPlainText()))
        footer.addWidget(self.copy_button)
        footer.addStretch()
        footer.addWidget(action('关闭', self.close))
        layout.addLayout(footer)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.height() < 680
        self.title.setVisible(not compact)
        self.hint.setText('两项都选时，只查询该人员在此 issue 下的日志。' if compact else self.scope_hint)
        self.render_result()

    @staticmethod
    def update_choices(combo, values):
        old, label = combo.currentData(), combo.currentText()
        entries = list(values)
        if old and old not in {value for _, value in entries}:
            entries.append((label.removesuffix('（已不可用）')+'（已不可用）', old))
        if [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())] == entries:
            return
        combo.blockSignals(True)
        combo.clear()
        for title, value in entries:
            combo.addItem(title, value)
        combo.setCurrentIndex(max(0, combo.findData(old)))
        combo.blockSignals(False)

    def refresh(self, snapshot):
        self.snapshot = snapshot
        people = snapshot['roster']['employees']
        issues = snapshot['progress']['issues']
        self.available_people = {person['id'] for person in people} | {''}
        self.available_issues = {issue['id'] for issue in issues} | {''}
        self.update_choices(self.person, [('全部人员', '')]+[
            (person['name']+' · '+(person['department'] or '未填部门')+' · '+person['id'][:8]+
             ('（已停用）' if not person['active'] else ''), person['id']) for person in people])
        self.update_choices(self.issue, [('全部已共享 issue', '')]+[
            ((issue['identifier']+' · '+issue['title']) if issue['identifier'] else '关联父任务 · 资料待共享', issue['id'])
            for issue in issues])
        self.render_result()

    def open_scope(self, person_id='', issue_id=''):
        self.refresh(self.owner.snapshot_data)
        for combo, identity in ((self.person, person_id), (self.issue, issue_id)):
            index = combo.findData(identity)
            if index < 0:
                combo.addItem('所选范围已不可用，请重新选择', identity)
                index = combo.count()-1
            combo.setCurrentIndex(index)
        self.render_result()
        self.show()
        self.raise_()
        self.activateWindow()
        self.question.setFocus(Qt.OtherFocusReason)

    def scope(self):
        return self.person.currentData() or '', self.issue.currentData() or ''

    def edited(self, *_):
        self.error = ''
        self.render_result()

    def submit(self, model):
        person, issue = self.scope()
        if not self.ask_button.isEnabled():
            return
        if model and QMessageBox.question(self, '依据共享日志回答',
                '将当前范围的共享日志片段发送给本机配置的 Codex 模型，以生成回答。继续？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.error = ''
        try:
            self.owner.client.ask(question=self.question.toPlainText().strip(), model=model,
                person_id=person, employee_id='', issue_id=issue, start='', end='')
            snapshot = self.owner.client.snapshot()
        except (ValueError, OSError, TypeError, KeyError) as exc:
            self.error = str(exc)
            self.render_result()
            return
        self.request_id = snapshot['query']['started_at']
        self.request_scope = (person, issue)
        self.request_label = self.person.currentText()+' / '+self.issue.currentText()
        self.result_tabs.setCurrentIndex(0)
        self.owner.refresh()

    def render_result(self, *_):
        if not self.snapshot or not hasattr(self, 'answer'):
            return
        query = self.snapshot['query']
        person, issue = self.scope()
        valid = person in self.available_people and issue in self.available_issues
        enabled = self.snapshot['config']['evaluator'] and valid and not query['busy'] and bool(self.question.toPlainText().strip())
        self.ask_button.setEnabled(enabled)
        self.local_button.setEnabled(enabled)
        own = bool(self.request_id and self.request_id == query.get('started_at'))
        self.stop_button.setEnabled(own and query['busy'])
        same_question = self.question.toPlainText().strip() == query['question']
        matching = own and valid and self.request_scope == (person, issue) and same_question
        answer = query['answer'] if matching else ''
        sources = '\n\n'.join(source.get('label', '')+' '+source.get('name', '')+' · '+source.get('issue_identifier', '')+
            ' · '+source.get('date', '')+'\n'+source.get('excerpt', '')+'\n记录：'+source.get('record_id', '')+
            '\n版本：'+source.get('digest', '') for source in query['sources']) if matching else ''
        for editor, value in ((self.answer, answer), (self.sources, sources)):
            if editor.toPlainText() != value:
                editor.setPlainText(value)
        self.copy_button.setEnabled(bool(answer))
        label = '回答范围：'+self.request_label if matching else '回答仅对应提交时选择的人员和 issue。'
        self.result_label.setText(self.result_label.fontMetrics().elidedText(label, Qt.ElideRight, max(150, self.width()-28)))
        self.result_label.setToolTip((self.request_label+'\n'+query['question']) if matching else '')
        if not self.snapshot['config']['evaluator']:
            notice = '请先在“人员与连接”开启接收员工共享资料。'
        elif not valid:
            notice = '所选人员或 issue 已不可用，请重新选择范围。'
        elif self.error:
            notice = self.error
        elif matching:
            notice = ('正在回答 · ' if query['busy'] else '')+query['notice']
        elif query['busy']:
            notice = '已有问询正在进行，请等待完成或回到原范围停止。'
        elif self.request_scope and self.request_scope != (person, issue):
            notice = '范围已切换，请重新提问；上一范围的回答已隐藏。'
        elif own and not same_question:
            notice = '问题已修改，请重新提交；上一问题的回答已隐藏。'
        elif self.request_id:
            notice = '其他日志查询已更新结果，请重新提问。'
        else:
            notice = '仅使用已授权的共享日志。没有资料时会明确说明缺口。'
        self.notice.setText(notice)
