"""A native reference area that stays outside the scrolling course body."""
from html import escape
from PyQt5.QtCore import Qt, QVariant, pyqtSignal
from PyQt5.QtWidgets import QFrame, QComboBox, QTextBrowser
from learning_knowledge import lesson_keypoints, keypoint_excerpt
from ui_helpers import box, label, button


class KnowledgeText(QTextBrowser):
    def loadResource(self, kind, url):
        return QVariant()


class KnowledgePanel(QFrame):
    quote_requested = pyqtSignal(str)

    def __init__(self, package, parent=None):
        super().__init__(parent)
        self.package = package; self.points = []; self.lesson_id = None
        self.setObjectName('knowledgePanel'); self.setMinimumWidth(310); self.setMinimumHeight(170)
        layout = box(self, margins=(12, 10, 12, 10), spacing=6)
        heading = box(horizontal=True, spacing=6)
        self.heading = label('核心知识', 'h2'); heading.addWidget(self.heading)
        self.count = label('', 'badge'); heading.addWidget(self.count); heading.addStretch()
        self.quote_button = button('引用到答疑', 'linkButton', callback=self.quote_current)
        heading.addWidget(self.quote_button); layout.addLayout(heading)
        self.selector = QComboBox(); self.selector.setAccessibleName('本章核心知识点')
        self.selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.selector.setMinimumContentsLength(12)
        self.selector.currentIndexChanged.connect(self.show_point); layout.addWidget(self.selector)
        self.content = KnowledgeText(); self.content.setObjectName('knowledgeText')
        self.content.setAccessibleName('固定显示的公式与知识结论')
        self.content.setOpenLinks(False); self.content.setOpenExternalLinks(False)
        self.content.setMinimumHeight(32); layout.addWidget(self.content, 1)
        self.sources = {source['id']: source for source in package['sources']}

    def set_lesson(self, lesson):
        self.lesson_id = lesson['id']; self.points = lesson_keypoints(self.package, self.lesson_id)
        self.heading.setToolTip('当前章节：' + lesson['title'] + '\n正文滚动时保持可见；可拖动下方分隔条调整高度。')
        self.count.setText(str(len(self.points)) + ' 项')
        self.selector.blockSignals(True); self.selector.clear()
        for index, point in enumerate(self.points):
            title = ('公式' if point['kind'] == 'formula' else '结论') + ' · ' + point['title']
            self.selector.addItem(title)
            self.selector.setItemData(index, title, Qt.ToolTipRole)
        if not self.points:
            self.selector.addItem('本章暂无独立知识点')
        self.selector.setEnabled(bool(self.points)); self.quote_button.setEnabled(bool(self.points))
        self.selector.blockSignals(False); self.show_point(0)

    def show_point(self, index):
        if not 0 <= index < len(self.points):
            self.content.setPlainText('本章尚未收录独立公式或知识结论。可继续阅读正文，或在「知识库与来源」查阅相关内容。')
            return
        point = self.points[index]
        if point['kind'] == 'formula':
            body = '<pre style="font-family:monospace; font-size:13px">' + escape(point['text']) + '</pre>'
        else:
            body = '<p>' + escape(point['text']).replace('\n', '<br>') + '</p>'
        for key, title in [('scope', '适用范围'), ('conditions', '适用条件'), ('location', '内容定位')]:
            if point[key]:
                body += '<p style="color:#5f7488"><b>' + title + '</b><br>' + escape(point[key]).replace('\n', '<br>') + '</p>'
        if point['source_ids']:
            references = [source_id + ' · ' + self.sources.get(source_id, {}).get('title', '来源记录未提供标题')
                          for source_id in point['source_ids']]
            body += '<p style="color:#5f7488"><b>' + point['source_label'] + '</b><br>' + '<br>'.join(escape(value) for value in references) + '</p>'
        self.content.setHtml(body)
        self.content.verticalScrollBar().setValue(0); self.content.horizontalScrollBar().setValue(0)
        self.selector.setToolTip(self.selector.itemText(index))

    def quote_current(self):
        index = self.selector.currentIndex()
        if 0 <= index < len(self.points):
            self.quote_requested.emit(keypoint_excerpt(self.points[index]))
