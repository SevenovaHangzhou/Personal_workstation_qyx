"""Administrator's separately authorized complete reports. Never AI evidence."""
from datetime import datetime
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QComboBox, QLabel, QPlainTextEdit, QTreeWidget,
                            QTreeWidgetItem, QVBoxLayout, QWidget)


class CompleteReportsPanel(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.rows = {}
        self.signature = None
        self.text_signature = None
        layout = QVBoxLayout(self)
        note = QLabel('员工另行授权的完整日报、周报。与工作 issue 证据分开保存，不参与进度计算或 Codex 问答。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.sources = QTreeWidget()
        self.sources.setRootIsDecorated(False)
        self.sources.setHeaderLabels(['员工设备', '日报', '周报', '最后收集', '收集状态'])
        for index, width in enumerate((170, 55, 55, 130)):
            self.sources.setColumnWidth(index, width)
        self.sources.setMinimumHeight(80)
        self.sources.setMaximumHeight(155)
        self.sources.currentItemChanged.connect(self.choose_source)
        layout.addWidget(self.sources)
        self.files = QComboBox()
        self.files.setMinimumContentsLength(15)
        self.files.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.files.currentIndexChanged.connect(self.read_report)
        layout.addWidget(self.files)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText('员工在星序「连接评估员」创建包含完整报告的授权；管理员导入并同步后可在这里查看。')
        layout.addWidget(self.preview, 1)
        self.notice = QLabel('尚未收集完整报告。')
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)

    def current_peer(self):
        item = self.sources.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def refresh(self, snapshot):
        rows = snapshot.get('complete_reports', {})
        if rows == self.rows:
            return
        peer = self.current_peer()
        self.rows = rows
        self.sources.blockSignals(True)
        self.sources.clear()
        for employee, row in rows.items():
            files = row.get('files', []) if row.get('available') else []
            timestamp = row.get('collected_at', '')
            try:
                collected = datetime.fromisoformat(timestamp).astimezone().strftime('%m-%d %H:%M')
            except ValueError:
                collected = '尚未成功收集'
            item = QTreeWidgetItem([row['name']+' · '+employee[:8], str(sum(r['path'].startswith('日报/') for r in files)),
                str(sum(r['path'].startswith('周报/') for r in files)), collected, row.get('notice', '等待同步')])
            item.setData(0, Qt.UserRole, employee)
            item.setToolTip(0, row['name'] + ' · ' + employee)
            item.setToolTip(3, timestamp)
            item.setToolTip(4, row.get('notice', '等待同步'))
            self.sources.addTopLevelItem(item)
            if employee == peer:
                self.sources.setCurrentItem(item)
        if not self.sources.currentItem() and self.sources.topLevelItemCount():
            self.sources.setCurrentItem(self.sources.topLevelItem(0))
        self.sources.blockSignals(False)
        self.choose_source()

    def choose_source(self, *args):
        peer = self.current_peer()
        row = self.rows.get(peer, {})
        self.notice.setText(row.get('notice', '未导入包含完整报告的授权。'))
        prior = self.files.currentData() if self.signature and self.signature[0] == peer else None
        signature = (peer, row.get('version'), row.get('available'))
        if signature != self.signature or self.text_signature is None:
            self.signature = signature
            self.files.blockSignals(True)
            self.files.clear()
            if row.get('available'):
                for item in row.get('files', []):
                    self.files.addItem(item['path'], item['path'])
            self.files.setCurrentIndex(max(0, self.files.findData(prior)))
            self.files.blockSignals(False)
            self.read_report()

    def read_report(self, *args):
        peer, path = self.current_peer(), self.files.currentData()
        row = self.rows.get(peer, {})
        signature = (peer, row.get('version'), path, row.get('available'))
        if signature == self.text_signature:
            return
        self.text_signature = signature
        self.preview.clear()
        if peer and path and row.get('available'):
            try:
                self.preview.setPlainText(self.owner.client.complete_report_text(peer, path))
            except (ValueError, OSError) as exc:
                self.text_signature = None
                self.notice.setText(str(exc))
