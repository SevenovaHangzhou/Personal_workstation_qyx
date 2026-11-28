"""A compact manager view over authorized, attributed issue summaries."""
from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QHeaderView, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget)

from evaluation.overview import filter_issues
from evaluation_progress_ui import IssueProgress
from evaluation_ui import action, fill, note, selection, table


def local_time(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().strftime('%m-%d %H:%M')
    except (ValueError, AttributeError, TypeError):
        return '暂无'


def brief(value, limit=70):
    value = ' '.join(value.split())
    return value if len(value) <= limit else value[:limit]+'…'


class OverviewPanel(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.data = {'issues': [], 'people': []}
        self.people_key = None
        self.rows_key = None
        self.compact = False
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        self.title = note('团队工作概览')
        self.title.setObjectName('h1')
        heading.addWidget(self.title, 1)
        self.ask_button = action('进度提问', self.ask_progress)
        heading.addWidget(self.ask_button)
        self.sync = action('同步最新资料', self.primary_action)
        self.sync.setObjectName('primary')
        heading.addWidget(self.sync)
        layout.addLayout(heading)
        self.freshness = note('')
        self.freshness.setObjectName('muted')
        layout.addWidget(self.freshness)
        self.cards = QWidget()
        cards = QHBoxLayout(self.cards)
        cards.setContentsMargins(0, 0, 0, 0)
        self.metrics = {}
        for key, label in [('people', '名在册员工'), ('active', '项未完成'),
                           ('attention', '项需关注'), ('completed', '项已完成')]:
            card = QFrame()
            card.setObjectName('quiet')
            box = QHBoxLayout(card)
            value = note('0')
            value.setObjectName('h2')
            box.addWidget(value)
            box.addWidget(note(label))
            cards.addWidget(card, 1)
            self.metrics[key] = value
        layout.addWidget(self.cards)
        self.setup = QWidget()
        setup_row = QHBoxLayout(self.setup)
        setup_row.setContentsMargins(0, 0, 0, 0)
        self.setup_text = note('')
        setup_row.addWidget(self.setup_text, 1)
        self.setup_button = action('开始设置', self.open_setup)
        setup_row.addWidget(self.setup_button)
        layout.addWidget(self.setup)
        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 4, 0)
        side_layout.addWidget(note('按开发人员查看'))
        self.people = QListWidget()
        self.people.setMinimumWidth(170)
        self.people.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.people.setTextElideMode(Qt.ElideRight)
        self.people.currentItemChanged.connect(lambda *_: self.render())
        side_layout.addWidget(self.people, 1)
        side_layout.addWidget(action('管理人员', lambda: owner.open_settings('roster')))
        self.split.addWidget(side)
        main = QWidget()
        box = QVBoxLayout(main)
        box.setContentsMargins(4, 0, 0, 0)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索 issue、工作内容或姓名')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.render)
        self.state = QComboBox()
        for label, key in [('未完成', 'active'), ('需要关注', 'attention'), ('已完成', 'completed'), ('全部（含取消）', 'all')]:
            self.state.addItem(label, key)
        self.state.currentIndexChanged.connect(self.render)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.state)
        box.addLayout(filters)
        self.person_note = note('')
        self.person_note.setObjectName('muted')
        box.addWidget(self.person_note)
        self.content = QSplitter(Qt.Vertical)
        self.content.setChildrenCollapsible(False)
        self.issues = table(['Issue / 工作内容', '负责人', '当前状态', '完成进度', '最新进展'])
        self.issues.setShowGrid(False)
        self.issues.setAlternatingRowColors(True)
        self.issues.setStyleSheet('QTableWidget { background: white; alternate-background-color: #f5f7fa; '
            'border: 1px solid #dbe3ed; selection-background-color: #dce8f0; selection-color: #273d52; } '
            'QHeaderView::section { background: #edf2f7; border: 0; padding: 6px; }')
        self.issues.setWordWrap(False)
        self.issues.setTextElideMode(Qt.ElideRight)
        self.issues.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.issues.setColumnWidth(0, 240)
        self.issues.setColumnWidth(1, 100)
        self.issues.setColumnWidth(2, 130)
        self.issues.setColumnWidth(3, 110)
        self.issues.setMinimumHeight(100)
        self.issues.itemSelectionChanged.connect(self.show_issue)
        self.content.addWidget(self.issues)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(85)
        detail_area = QWidget()
        detail_layout = QVBoxLayout(detail_area)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.progress = IssueProgress()
        detail_layout.addWidget(self.progress)
        detail_layout.addWidget(self.detail, 1)
        self.content.addWidget(detail_area)
        self.content.setSizes([200, 220])
        box.addWidget(self.content, 1)
        self.result_note = note('')
        self.result_note.setObjectName('muted')
        box.addWidget(self.result_note)
        buttons = QHBoxLayout()
        self.logs = action('查看日志依据', self.open_logs)
        self.edit = action('记录进展', self.edit_issue)
        self.ledger = action('进度明细', self.open_ledger)
        self.ask_issue = action('询问此 issue', self.ask_issue_progress)
        for button in (self.ask_issue, self.logs, self.edit, self.ledger):
            buttons.addWidget(button)
        buttons.addStretch()
        box.addLayout(buttons)
        self.split.addWidget(main)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([220, 850])
        layout.addWidget(self.split, 1)

    def refresh(self, snapshot):
        self.snapshot = snapshot
        self.data = snapshot['overview']
        for key, widget in self.metrics.items():
            widget.setText(str(self.data['counts'][key]))
        self.freshness_text = ('仅统计已共享的 issue · 父项和子项分别列出 · 最近成功同步：'+local_time(self.data['last_synced'])+
            (' · 部分来源同步失败，保留上次资料' if self.data['sync_errors'] else '')+
            ' · 状态为同步快照')
        if not snapshot['config']['evaluator']:
            message, button, self.setup_target = '先开启接收，再连接员工并整理名册，即可查看团队进展。', '设置接收', 'share'
        elif not snapshot['config']['peers']:
            message, button, self.setup_target = '还没有员工资料。请先导入员工发来的共享授权。', '连接员工', 'peers'
        elif self.data['unassigned_devices']:
            message, button, self.setup_target = f"{self.data['unassigned_devices']} 个来源尚未归入员工名册，归档后即可按人查看。", '整理名册', 'roster'
        elif not self.data['issues']:
            message, button, self.setup_target = '尚未收到 issue 日志。员工共享工作任务并产生日志后，同步即可查看。', '查看连接', 'peers'
        else:
            message, button, self.setup_target = '', '', 'roster'
        self.setup_message = message
        self.setup_text.setText(message)
        self.setup_button.setText(button)
        self.update_density()
        entries = [('', '全部人员', '查看全部已共享 issue'),
                   ('__unassigned__', '负责人待确认', '查看尚未对应负责人的 issue')]
        for person in self.data['people']:
            label = person['name']+(' · '+person['department'] if person['department'] else '')
            if not person['active']:
                label += '（已停用）'
            counts = f"负责 {person['owned_count']} · 参与 {person['contributed_count']}"
            if person['attention_count']:
                counts += f" · 关注 {person['attention_count']}"
            entries.append((person['id'], label, counts))
        if entries != self.people_key:
            old = self.person_id()
            self.people.blockSignals(True)
            self.people.clear()
            selected = 0
            for index, (identity, label, caption) in enumerate(entries):
                item = QListWidgetItem(label+'\n'+caption)
                item.setData(Qt.UserRole, identity)
                item.setToolTip(label+'\n'+caption+('\n员工编号：'+identity if identity and identity != '__unassigned__' else ''))
                self.people.addItem(item)
                if identity == old:
                    selected = index
            self.people.setCurrentRow(selected)
            self.people.blockSignals(False)
            self.people_key = entries
        self.render()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.compact = self.height() < 650
        self.update_density()

    def update_density(self):
        if not hasattr(self, 'freshness_text'):
            return
        self.cards.setVisible(not self.compact)
        self.person_note.setVisible(not self.compact)
        if self.title.property('compact') != self.compact:
            self.title.setProperty('compact', self.compact)
            self.title.style().unpolish(self.title)
            self.title.style().polish(self.title)
        counts = self.data['counts']
        self.freshness.setText((f"{counts['people']} 人 · 未完成 {counts['active']} · 需关注 {counts['attention']} · 已完成 {counts['completed']} · 已共享资料"+
            (' · 同步异常' if self.data['sync_errors'] else '')) if self.compact else self.freshness_text)
        self.freshness.setToolTip(self.freshness_text+'\n'+self.setup_message)
        self.setup.setVisible(bool(self.setup_message) and not self.compact)
        setup_action = bool(self.compact and self.setup_message)
        self.sync.setEnabled(setup_action or self.snapshot['config']['evaluator'] and not self.snapshot['syncing'])
        self.sync.setText(self.setup_button.text() if setup_action else
            '正在同步…' if self.snapshot['syncing'] else '同步最新资料')
        self.sync.setToolTip(self.setup_message if setup_action else '读取员工已授权共享的最新日志')
        row_height = max(52, self.issues.fontMetrics().height()*2+12)
        for index in range(self.issues.rowCount()):
            self.issues.setRowHeight(index, row_height)
        self.issues.setMinimumHeight(max(100, row_height+self.issues.horizontalHeader().sizeHint().height()+4))

    def person_id(self):
        item = self.people.currentItem()
        return item.data(Qt.UserRole) if item else ''

    def render(self, *_):
        person = next((row for row in self.data['people'] if row['id'] == self.person_id()), None)
        if person:
            self.person_note.setText(person['name']+' · 最近日志 '+local_time(person['last_report'])+
                ' · 最近同步 '+local_time(person['last_synced'])+
                (' · 同步失败，当前显示历史资料' if person['sync_errors'] else '')+
                (' · 尚未绑定 Linear 账号' if not person['has_accounts'] else ''))
        else:
            self.person_note.setText('阻塞与需关注优先 · 人员数字为未完成数 · 参与表示日志贡献')
        visible = filter_issues(self.data, self.person_id(), self.state.currentData(), self.search.text())
        rows = []
        for issue in visible:
            state = issue['state_label']
            if issue['attention']:
                state += '\n'+issue['attention'][0]
            rows.append((issue['id'], [issue['identifier']+'  '+issue['title'],
                issue['owner_name'] or '待确认', state,
                f"{issue['percent']:g}%" if issue['percent'] is not None else '已取消' if issue['state'] == 'canceled' else '待估',
                brief(issue['summary']) or '待补充进展']))
        if rows != self.rows_key:
            old = selection(self.issues)
            self.issues.blockSignals(True)
            fill(self.issues, rows)
            identities = [row[0] for row in rows]
            if rows:
                self.issues.selectRow(identities.index(old) if old in identities else 0)
            else:
                self.issues.setCurrentCell(-1, -1)
            for index, issue in enumerate(visible):
                if issue['attention']:
                    self.issues.item(index, 2).setForeground(QColor('#99642b'))
                self.issues.setRowHeight(index, max(52, self.issues.fontMetrics().height()*2+12))
                self.issues.item(index, 3).setText('')
                progress = IssueProgress(self.issues)
                self.issues.setCellWidget(index, 3, progress)
                progress.refresh(issue)
                self.issues.item(index, 4).setToolTip(issue['summary'] or '待补充进展')
            self.issues.blockSignals(False)
            self.rows_key = rows
        self.result_note.setText(f'当前显示 {len(visible)} 项 · 仅含已共享资料')
        self.result_note.setToolTip('统计范围为已共享 issue 及其关联父项，未共享的其他任务不在此列。')
        self.show_issue()

    def current_issue(self):
        identity = selection(self.issues)
        return next((row for row in self.data['issues'] if row['id'] == identity), None)

    def show_issue(self):
        issue = self.current_issue()
        self.progress.setVisible(bool(issue))
        if issue:
            self.progress.refresh(issue)
        self.logs.setEnabled(bool(issue and self.snapshot.get('issue_sources', {}).get(issue['id'])))
        self.edit.setEnabled(self.logs.isEnabled())
        self.ledger.setEnabled(bool(issue))
        self.ask_issue.setEnabled(bool(issue))
        if not issue:
            text = ('尚无已共享的 issue。按上方提示连接员工并同步资料。' if not self.data['issues'] else
                    '没有符合条件的 issue。可切换为“全部”或清空搜索；没有记录不代表没有工作。')
        else:
            parts = [issue['identifier']+' · '+issue['title'],
                '负责人：'+(issue['owner_name'] or '待确认')+'　状态：'+issue['state_label']+'（同步快照）']
            if issue['source_versions_changed']:
                parts.append('引用日志已修订，人工整理记录需要重新核对。')
            if issue['notes_outdated']:
                parts.append('已收到更新的日志，之前整理的阻塞和下一步需要重新核对。')
            parts += ['进展（'+issue['summary_source']+'）：'+(issue['summary'] or '尚未收到进展摘要。'),
                '阻塞'+('（'+issue['blocker_source']+'）' if issue['blockers'] else '')+'：'+(issue['blockers'] or '暂无明确记录'),
                '下一步：'+(issue['next_step'] or '尚未记录，可查看日志后补充。')]
            parts += ['', '最近日志：'+local_time(issue['last_report'])+
                '　日志参与：'+('、'.join(issue['contributor_names']) or '尚未归档')]
            if not issue['owner_id'] and issue['owner_notice']:
                parts.append(issue['owner_notice'])
            if issue['parent_id']:
                parent = next((row for row in self.data['issues'] if row['id'] == issue['parent_id']), None)
                parts.append('所属任务：'+(parent['identifier']+' · '+parent['title'] if parent else '父任务资料尚未共享'))
            if issue['percent'] is not None:
                parts.append(f"子任务完成进度：{issue['percent']:g}%（按已核对子项权重计算）")
            elif issue['child_ids']:
                parts.append('子任务进度待估：请在“进度明细”核对范围和资料。')
            if issue['attention']:
                parts.append('需要关注：'+'；'.join(issue['attention']))
            if issue['editor']:
                parts += ['', '整理人：'+issue['editor']+' · '+local_time(issue['updated_at'])]
            if issue['notes_outdated'] and issue['manual_summary']:
                parts.append('之前整理的进展：'+issue['manual_summary'])
            if issue['reports']:
                parts += ['', '最新日志依据']
                parts += [row['author']+' · '+local_time(row['date'])+'\n'+row['summary'] for row in issue['reports']]
            text = '\n'.join(parts)
        if text != self.detail.toPlainText():
            scroll = self.detail.verticalScrollBar().value()
            same = getattr(self, 'detail_id', None) == (issue['id'] if issue else None)
            self.detail.setPlainText(text)
            if same:
                self.detail.verticalScrollBar().setValue(scroll)
        self.detail_id = issue['id'] if issue else None

    def open_setup(self):
        self.owner.open_settings(self.setup_target)

    def ask_progress(self):
        person = self.person_id()
        if person and person != '__unassigned__':
            self.owner.open_progress_question(person_id=person)
        else:
            self.owner.open_progress_question()

    def ask_issue_progress(self):
        issue = self.current_issue()
        if issue:
            self.owner.open_progress_question(issue_id=issue['id'])

    def primary_action(self):
        if self.compact and self.setup_message:
            self.open_setup()
        else:
            self.owner.perform(self.owner.client.sync)

    def open_logs(self):
        issue = self.current_issue()
        if issue:
            self.owner.open_issue_logs(issue['id'])

    def edit_issue(self):
        issue = self.current_issue()
        if issue:
            self.owner.select_ledger_issue(issue['id'])
            self.owner.edit_update()

    def open_ledger(self):
        issue = self.current_issue()
        if issue:
            self.owner.select_ledger_issue(issue['id'])
            self.owner.open_resource('进度明细')
