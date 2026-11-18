"""Native task-chat card, styled consistently with connection onboarding."""
import sys
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QScrollArea, QWidget, QCheckBox, QComboBox
from .settings_ui import STYLE, label
from .chat_card import read_plan, select_plan, plan_path
from .core import OrbitError


class ChatCard(QDialog):
    def __init__(self, directory, plan_id):
        super().__init__()
        self.directory, self.plan_id = directory, plan_id
        self.plan = read_plan(directory, plan_id)
        self.rows = []
        self.setWindowTitle('星序 · 任务聊天')
        self.resize(720, 620)
        self.setStyleSheet(STYLE + "QScrollArea, QScrollArea > QWidget > QWidget { background: white; border-radius: 12px; } QCheckBox { color: #23304a; font-size: 14px; padding: 6px 0; } QComboBox { background: #f9fbfe; color: #344760; border: 1px solid #cbd6e5; border-radius: 7px; padding: 8px; font-size: 13px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(14)
        layout.addWidget(label('✧  七耀星 · ORBIT', 'brand'))
        layout.addWidget(label('给任务一个聊天', 'title'))
        layout.addWidget(label('已有同名聊天自动绑定，缺少的在这里补建。', 'muted', True))
        layout.addWidget(label(f"已绑定 {len(self.plan['bound'])} 项 · 待创建 {len(self.plan['missing'])} 项 · 待核对 {len(self.plan['conflicts'])} 项", 'status', True))
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        rows = QVBoxLayout(body)
        for issue in self.plan['missing']:
            check = QCheckBox(issue['identifier'] + ' · ' + issue['title'])
            check.setChecked(True)
            check.setToolTip(issue['title'])
            rows.addWidget(check)
            combo = QComboBox()
            combo.addItem('选择所属项目', '')
            for project in self.plan['projects']:
                combo.addItem(project.get('label') or project['path'], project['projectId'])
                combo.setItemData(combo.count() - 1, project['path'], Qt.ToolTipRole)
            rows.addWidget(combo)
            self.rows.append((issue, check, combo))
        for issue in self.plan['conflicts']:
            rows.addWidget(label(issue['identifier'] + ' · ' + issue['title'] + '\n' + issue['reason'], 'muted', True))
        if not self.rows:
            rows.addWidget(label('没有需要补建的聊天。', 'muted'))
        rows.addStretch()
        area.setWidget(body)
        layout.addWidget(area, 1)
        self.notice = label('选择任务及项目后，返回 Codex 完成创建。新聊天先接收上下文，等待你的下一步指示。', 'muted', True)
        layout.addWidget(self.notice)
        actions = QHBoxLayout()
        later = QPushButton('稍后处理')
        later.clicked.connect(self.close)
        actions.addWidget(later)
        self.submit = QPushButton('创建所选任务聊天')
        self.submit.setObjectName('primary')
        self.submit.setEnabled(bool(self.rows) and self.plan['state'] == 'waiting')
        self.submit.clicked.connect(self.confirm)
        actions.addWidget(self.submit)
        layout.addLayout(actions)

    def confirm(self):
        selection = []
        for issue, check, combo in self.rows:
            if check.isChecked():
                if not combo.currentData():
                    self.notice.setText('请为 ' + issue['identifier'] + ' 选择所属项目。')
                    return
                selection.append({'identifier': issue['identifier'], 'project_id': combo.currentData()})
        try:
            select_plan(self.directory, self.plan_id, selection)
        except OrbitError as exc:
            self.notice.setText(str(exc))
            return
        self.submit.setEnabled(False)
        self.notice.setText('选择已保存，请返回 Codex 继续创建。' if selection else '未选择任务，不创建聊天。')

    def closeEvent(self, event):
        if read_plan(self.directory, self.plan_id)['state'] == 'waiting':
            select_plan(self.directory, self.plan_id, [])
        event.accept()


def run_card(directory, plan_id):
    from qt_runtime import configure_qt_plugins
    from platform_support import lock_file
    configure_qt_plugins()
    app = QApplication(sys.argv[:1])
    guard = plan_path(directory, plan_id).with_suffix('.lock').open('a+')
    try:
        lock_file(guard)
    except OSError:
        guard.close()
        return 0
    card = ChatCard(directory, plan_id)
    card.show()
    result = app.exec_()
    guard.close()
    return result
