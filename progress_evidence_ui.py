"""Native progress journal; recording a claim never marks a task complete."""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QComboBox, QDialog, QFrame, QLineEdit, QListView, QListWidget,
                             QListWidgetItem, QMessageBox, QPlainTextEdit, QSplitter, QWidget)
from progress_evidence import KINDS, timestamp
from ui_helpers import box, label, button


def display_time(value):
    return timestamp(value).astimezone().strftime('%m-%d %H:%M')


class ProgressPanel(QFrame):
    def __init__(self, owner, task):
        super().__init__(); self.setObjectName('progressEvidencePanel')
        layout = box(self, spacing=7)
        head = box(horizontal=True); head.addWidget(label('进度与依据', 'section')); head.addStretch()
        head.addWidget(button('记录进展', 'linkButton', callback=lambda:self.open(owner, task['id'])))
        layout.addLayout(head)
        rows = [r for r in task.get('evidence', []) if not r.get('retracted_at')]
        if rows:
            layout.addWidget(label(' · '.join(f'{name} {sum(r["kind"] == kind for r in rows)}' for kind, name in KINDS.items()), 'muted', True))
            latest = rows[-1]
            layout.addWidget(label(KINDS[latest['kind']] + ' · ' + latest['text'][:100] + ('…' if len(latest['text']) > 100 else ''), wrap=True))
        else:
            layout.addWidget(label('留下一次尝试、验证结果或验收结论，方便下次接续。', 'muted', True))

    def open(self, owner, task_id):
        if not owner.flush_editors():
            return
        dialog = ProgressDialog(owner, owner, task_id)
        dialog.exec_()
        dialog.deleteLater()
        owner.refresh()


class ProgressDialog(QDialog):
    def __init__(self, parent, owner, task_id):
        super().__init__(parent); self.owner = owner; self.task_id = task_id; self.records = {}
        self.setObjectName('progressEvidenceDialog'); self.setWindowTitle('记录进展 · 星序')
        self.resize(820, 710); self.setMinimumSize(640, 600)
        layout = box(self, margins=(24, 20, 24, 20), spacing=10)
        task = owner.tasks.get(task_id)
        layout.addWidget(label(task['title'] if task else '任务已删除', 'h2', True))
        layout.addWidget(label('记录做过什么，以及如何确认。记录不会自动完成任务；仅进入本地日报。', 'muted', True))
        row = box(horizontal=True)
        row.addWidget(label('这次记录', 'section'))
        self.kind = QComboBox(); self.kind.setAccessibleName('进度记录类型')
        for key, name in KINDS.items():
            self.kind.addItem(name, key)
        row.addWidget(self.kind); row.addStretch()
        self.note_button = button('带入任务笔记', 'linkButton', callback=self.use_note)
        row.addWidget(self.note_button); layout.addLayout(row)
        self.text = QPlainTextEdit(); self.text.setAccessibleName('进度记录内容'); self.text.setFixedHeight(96)
        self.text.setPlaceholderText('例如：运行边界测试，12 项通过；未覆盖断网恢复。')
        layout.addWidget(self.text)
        self.source = QLineEdit(); self.source.setMaxLength(1000); self.source.setAccessibleName('进度依据来源')
        self.source.setPlaceholderText('依据（可选）：文件路径、测试报告、聊天链接…')
        layout.addWidget(self.source)
        row = box(horizontal=True)
        self.notice = label('', 'muted', True); row.addWidget(self.notice, 1)
        self.save_button = button('保存记录', 'primary', callback=self.save_record); row.addWidget(self.save_button)
        layout.addLayout(row)
        layout.addWidget(label('记录历史 · 撤回仍保留原文', 'section'))
        split = QSplitter(Qt.Horizontal); split.setChildrenCollapsible(False)
        self.rows = QListWidget(); self.rows.setWordWrap(True); self.rows.setMinimumWidth(180); self.rows.setAccessibleName('进度历史')
        self.rows.setResizeMode(QListView.Adjust); self.rows.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        split.addWidget(self.rows)
        details = QWidget(); detail_layout = box(details, spacing=7)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); self.details.setAccessibleName('进度记录详情')
        detail_layout.addWidget(self.details, 1)
        self.retract_button = button('撤回所选记录', 'outline', callback=self.retract_record)
        detail_layout.addWidget(self.retract_button); split.addWidget(details)
        split.setSizes([290, 460]); layout.addWidget(split, 1)
        layout.addWidget(label('来源由你填写，不会读取文件或自动执行核验。模型回答请核实后再记录。', 'muted', True))
        layout.addWidget(button('返回任务', callback=self.accept))
        self.rows.currentItemChanged.connect(self.show_record)
        self.refresh()

    def refresh(self, selected=None):
        task = self.owner.tasks.get(self.task_id)
        self.records = {r['id']:r for r in (task or {}).get('evidence', [])}
        self.rows.clear()
        self.save_button.setEnabled(bool(task)); self.note_button.setEnabled(bool(task and task['note'].strip()))
        if not task:
            self.notice.setText('任务已删除，未保存的内容仍保留在输入框中。')
        elif not self.records:
            self.notice.setText('尚无记录，可直接写下本次进展。')
        for record in reversed(list(self.records.values())):
            title = ('已撤回 · ' if record.get('retracted_at') else '') + KINDS[record['kind']]
            text = title + ' · ' + display_time(record['created_at']) + '\n' + record['text'][:70]
            item = QListWidgetItem(text); item.setData(Qt.UserRole, record['id']); self.rows.addItem(item)
            if record['id'] == selected:
                self.rows.setCurrentItem(item)
        if self.rows.currentRow() < 0 and self.rows.count():
            self.rows.setCurrentRow(0)
        self.show_record()

    def done(self, result):
        if self.text.toPlainText().strip() or self.source.text().strip():
            if QMessageBox.question(self, '放弃未保存的记录？', '当前记录尚未保存。是否放弃草稿并返回任务？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        super().done(result)

    def show_record(self, *unused):
        item = self.rows.currentItem(); record = self.records.get(item.data(Qt.UserRole)) if item else None
        self.retract_button.setEnabled(bool(record and not record.get('retracted_at')))
        if not record:
            self.details.setPlainText('选择一条记录，查看内容和依据。'); return
        text = KINDS[record['kind']] + ' · ' + display_time(record['created_at']) + '\n\n' + record['text']
        text += '\n\n依据（人工填写）：\n' + (record['source'] or '未填写')
        if record.get('retracted_at'):
            text += '\n\n已撤回 · ' + display_time(record['retracted_at'])
        self.details.setPlainText(text)

    def use_note(self):
        task = self.owner.tasks.get(self.task_id)
        if not task:
            self.refresh(); return
        if self.text.toPlainText().strip() and QMessageBox.question(self, '替换草稿？', '使用任务笔记替换当前记录草稿？') != QMessageBox.Yes:
            return
        self.text.setPlainText(task['note'])

    def save_record(self):
        result = []
        if not self.owner.write(lambda:result.append(self.owner.tasks.record_progress(self.task_id, self.kind.currentData(), self.text.toPlainText(), self.source.text()))):
            return
        self.text.clear(); self.source.clear(); self.notice.setText('已保存；任务状态保持原样。')
        self.refresh(result[0]['id'])

    def retract_record(self):
        item = self.rows.currentItem()
        if not item:
            return
        identifier = item.data(Qt.UserRole)
        if QMessageBox.question(self, '撤回这条记录？', '原文和撤回时间仍会保留。已生成的历史日报保持原样。') != QMessageBox.Yes:
            return
        if self.owner.write(lambda:self.owner.tasks.retract_progress(self.task_id, identifier)):
            self.notice.setText('已撤回，原文已保留。'); self.refresh(identifier)
