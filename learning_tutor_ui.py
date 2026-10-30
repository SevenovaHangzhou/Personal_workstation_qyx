"""A nonblocking native Q&A sidebar shared by every course reader."""
from html import escape
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QVariant
from PyQt5.QtGui import QKeySequence, QPainter
from PyQt5.QtWidgets import QFrame, QTextBrowser, QPlainTextEdit, QShortcut, QProgressBar, QLabel, QSizePolicy, QWidget
from ui_helpers import box, label, button
from learning_tutor import TUTOR_MODEL

STATUS = {'queued':'等待回答', 'running':'正在思考', 'done':'已回答', 'failed':'回答失败',
          'cancelled':'已停止', 'interrupted':'已中断'}


class TutorTranscript(QTextBrowser):
    def loadResource(self, kind, url):
        # Markdown stays text-only; model output cannot fetch embedded files.
        return QVariant()


class CompactLabel(QLabel):
    """Keep full status text available while one line fits a shared sidebar."""
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setObjectName('muted'); self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumHeight(self.fontMetrics().height()+2); self.setToolTip(text)

    def setText(self, text):
        super().setText(text); self.setToolTip(text)

    def paintEvent(self, event):
        painter=QPainter(self)
        text=self.fontMetrics().elidedText(self.text(),Qt.ElideRight,self.contentsRect().width())
        self.style().drawItemText(painter,self.contentsRect(),self.alignment(),self.palette(),self.isEnabled(),text,self.foregroundRole())


class TutorPanel(QFrame):
    quote_requested = pyqtSignal()

    def __init__(self, client, task_id, course_id, parent=None):
        super().__init__(parent)
        self.client=client; self.task_id=task_id; self.course_id=course_id
        self.lesson_id=None; self.lesson_title=''; self.excerpt=''; self._revision=None
        self.state={'turns':[],'busy':False}; self.setObjectName('tutorPanel'); self.setMinimumWidth(310)
        layout=box(self,margins=(12,8,12,8),spacing=5)
        head=box(horizontal=True); head.addWidget(label('随课答疑','h2')); head.addStretch()
        model=label('5.6 sol','badge'); model.setToolTip(TUTOR_MODEL); head.addWidget(model); layout.addLayout(head)
        self.context=CompactLabel(); layout.addWidget(self.context)
        self.transcript=TutorTranscript(); self.transcript.setObjectName('tutorTranscript')
        self.transcript.setMinimumHeight(50)
        self.transcript.document().setDefaultStyleSheet('p { margin-top: 6px; margin-bottom: 12px; } code { font-family: monospace; }')
        self.transcript.setOpenLinks(False); self.transcript.setOpenExternalLinks(False)
        self.transcript.setAccessibleName('本课程的学习问答'); layout.addWidget(self.transcript,1)
        self.quote_row=QWidget(); quotes=box(self.quote_row,horizontal=True,spacing=6)
        self.quote=CompactLabel(); quotes.addWidget(self.quote,1)
        self.clear_quote=button('移除引用','linkButton',callback=lambda:self.set_excerpt('')); quotes.addWidget(self.clear_quote)
        self.quote_row.hide(); layout.addWidget(self.quote_row)
        self.question=QPlainTextEdit(); self.question.setObjectName('tutorQuestion')
        self.question.setPlaceholderText('哪里没理解？可以问概念、公式或练习…\nCtrl + Enter 发送')
        self.question.setMinimumHeight(62); self.question.setMaximumHeight(100)
        self.question.textChanged.connect(self.update_send); layout.addWidget(self.question)
        actions=box(horizontal=True,spacing=6)
        quote_button=button('引用选文','linkButton',callback=self.quote_requested.emit)
        quote_button.setToolTip('引用本章正文中选中的文字'); actions.addWidget(quote_button)
        self.stop_button=button('停止回答','outline',callback=self.cancel); self.stop_button.setEnabled(False); actions.addWidget(self.stop_button)
        actions.addStretch(); self.send_button=button('发送问题','primary',callback=self.send); actions.addWidget(self.send_button); layout.addLayout(actions)
        self.progress=QProgressBar(); self.progress.setRange(0,0); self.progress.setTextVisible(False); self.progress.hide(); layout.addWidget(self.progress)
        self.notice=CompactLabel('回答会结合当前章节；本课程问答自动保存。'); layout.addWidget(self.notice)
        QShortcut(QKeySequence('Ctrl+Return'),self.question,activated=self.send)
        QShortcut(QKeySequence('Ctrl+Enter'),self.question,activated=self.send)
        self.timer=QTimer(self); self.timer.setInterval(450); self.timer.timeout.connect(self.refresh)
        self.refresh(); self.update_send()

    def set_lesson(self, lesson_id, title):
        if lesson_id != self.lesson_id:
            self.set_excerpt('')
        self.lesson_id=lesson_id; self.lesson_title=title
        self.context.setText('当前章节 · '+title); self.update_send()

    def set_excerpt(self, value):
        self.excerpt=value[:4000]
        self.quote.setText('引用：'+self.excerpt[:150]+('…' if len(self.excerpt)>150 else ''))
        self.quote.setToolTip(self.excerpt); self.quote_row.setVisible(bool(self.excerpt))

    def update_send(self):
        self.send_button.setEnabled(bool(self.lesson_id and self.question.toPlainText().strip()) and not self.state['busy'])

    def send(self):
        if self.state['busy'] or not self.lesson_id: return
        question=self.question.toPlainText().strip()
        if not question: return
        try:
            state=self.client.tutor_ask(self.task_id,self.course_id,self.lesson_id,question,self.excerpt)
        except (ValueError,OSError) as exc:
            self.notice.setText(str(exc)); return
        self.question.clear(); self.set_excerpt(''); self.apply_state(state)

    def cancel(self):
        try:
            self.apply_state(self.client.tutor_cancel(self.task_id,self.course_id))
            self.notice.setText('正在停止本次回答…' if self.state['busy'] else '本次回答已停止，可以继续提问。')
        except (ValueError,OSError) as exc: self.notice.setText(str(exc))

    def refresh(self):
        try: self.apply_state(self.client.tutor_snapshot(self.task_id,self.course_id))
        except (ValueError,OSError) as exc: self.notice.setText(str(exc))

    def apply_state(self,state):
        self.state=state
        self.progress.setVisible(state['busy']); self.stop_button.setEnabled(state['busy']); self.update_send()
        if state['revision']==self._revision: return
        self._revision=state['revision']
        bar=self.transcript.verticalScrollBar(); at_bottom=bar.value()>=bar.maximum()-24; position=bar.value()
        parts=[]
        for turn in state['turns']:
            parts.append('**你** · '+turn['lesson_title']+'\n\n'+escape(turn['question'])+'\n\n')
            if turn['excerpt']: parts.append('> 引用：'+escape(turn['excerpt']).replace('\n','\n> ')+'\n\n')
            parts.append('**随课答疑 · 5.6 sol**\n\n')
            parts.append((turn['answer'] if turn['status']=='done' else STATUS.get(turn['status'],turn['status'])+' · '+escape(turn['message']))+'\n\n---\n\n')
        self.transcript.setMarkdown(''.join(parts) if parts else '随时问一个问题。\n\n可以让老师解释概念、拆解公式，或换个例子帮助理解。')
        bar.setValue(bar.maximum() if at_bottom else position)
        active=next((turn for turn in reversed(state['turns']) if turn['status'] in ('queued','running')),None)
        self.notice.setText(active['message'] if active else ('本课程问答已保存，可继续追问。' if parts else '回答会结合当前章节；本课程问答自动保存。'))

    def showEvent(self,event):
        super().showEvent(event); self.refresh(); self.timer.start()

    def hideEvent(self,event):
        self.timer.stop(); super().hideEvent(event)
