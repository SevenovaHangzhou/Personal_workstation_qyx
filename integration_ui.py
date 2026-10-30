"""Native dialogs for read-only Linear import and Git repository associations."""
import threading
from PyQt5.QtCore import Qt, QThread, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QDialog, QLineEdit, QCheckBox, QScrollArea, QWidget,
                            QFileDialog, QMessageBox, QComboBox)
from ui_helpers import label, button, box, clear


class PullWorker(QThread):
    def __init__(self, client, include_closed, parent):
        super().__init__(parent)
        self.client = client
        self.include_closed = include_closed
        self.cancel_event = threading.Event()
        self.result = None
        self.error = ""

    def run(self):
        try:
            self.result = self.client.pull(self.include_closed, self.cancel_event)
        except (ValueError, OSError) as exc:
            self.error = str(exc)
        except Exception:
            self.error = "拉取未完成，请重试或检查应用日志"


class LinearDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.client = owner.runtime.linear
        self.worker = None
        self.busy = False
        self.closing = False
        self.issues = []
        self.checks = []
        self.setWindowTitle("Linear · My Issues")
        self.resize(620, 650)
        self.setMinimumWidth(500)
        layout = box(self, margins=(25, 23, 25, 23), spacing=13)
        layout.addWidget(label("把工作，接回自己的节奏。", "h2"))
        layout.addWidget(label("拉取指派给我的 issues，勾选后导入工作任务。", "muted", True))
        credentials = box(horizontal=True, spacing=8)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("Linear 个人 API Key")
        self.key.setMaxLength(4096)
        credentials.addWidget(self.key, 1)
        self.connect_button = button("连接", "outline", callback=self.configure)
        credentials.addWidget(self.connect_button)
        layout.addLayout(credentials)
        remember_row = box(horizontal=True)
        self.remember = QCheckBox("在本机记住密钥")
        self.remember.setChecked(True)
        self.remember.setToolTip("优先使用系统密钥环；不可用时使用仅当前用户可读的独立凭据文件")
        remember_row.addWidget(self.remember)
        remember_row.addStretch()
        self.forget_button = button("忘记密钥", "linkButton", callback=self.forget)
        remember_row.addWidget(self.forget_button)
        layout.addLayout(remember_row)
        self.connection = label("", "muted", True)
        layout.addWidget(self.connection)
        setup = button("创建 / 管理个人 API Key  ↗", "linkButton", callback=self.open_settings)
        layout.addWidget(setup)
        actions = box(horizontal=True)
        self.closed = QCheckBox("包含已完成 / 已取消")
        actions.addWidget(self.closed)
        actions.addStretch()
        self.pull_button = button("拉取 My Issues", "primary", "linear", color="#ffffff", callback=self.pull)
        actions.addWidget(self.pull_button)
        layout.addLayout(actions)
        self.status = label("连接后拉取；相同 issue 会去重，不会覆盖已有的笔记与子步骤。", "muted", True)
        layout.addWidget(self.status)
        options = box(horizontal=True)
        self.select_all = QCheckBox("全选")
        self.select_all.toggled.connect(self.toggle_all)
        options.addWidget(self.select_all)
        options.addStretch()
        layout.addLayout(options)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        host = QWidget()
        self.rows = box(host, spacing=9)
        self.rows.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(host)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(label("此处只读导入；自动创建 issue 和日报评论在「日报与同步」配置。", "muted", True))
        footer = box(horizontal=True)
        self.close_button = button("关闭", callback=self.reject)
        footer.addWidget(self.close_button)
        footer.addStretch()
        self.import_button = button("导入所选为工作任务", "coral", "plus", callback=self.import_selected)
        self.import_button.setEnabled(False)
        footer.addWidget(self.import_button)
        layout.addLayout(footer)
        self.update_connection()

    def open_settings(self):
        QDesktopServices.openUrl(QUrl("https://linear.app/settings/api"))

    def update_connection(self):
        connection = self.client.connection()
        name = (connection.get("viewer") or {}).get("name")
        self.connection.setText(("已连接 · " + name) if name else ("已配置密钥，可以拉取" if connection["configured"] else "尚未连接 · 也可通过 LINEAR_API_KEY 环境变量提供密钥"))
        if connection.get("remembered"):
            self.connection.setText(self.connection.text() + " · 本机已记住")
        if connection.get("notice"):
            self.connection.setText(connection["notice"])
        self.forget_button.setEnabled(connection.get("remembered", False))

    def configure(self):
        try:
            self.client.configure(self.key.text(), self.remember.isChecked())
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return False
        self.key.clear()
        self.issues = []
        self.render_issues()
        self.update_connection()
        return True

    def forget(self):
        try:
            self.client.forget()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc)); return
        self.key.clear(); self.issues = []; self.render_issues(); self.update_connection()
        self.status.setText("本机已保存的密钥已移除。")

    def set_busy(self, value):
        self.busy = value
        for widget in [self.key, self.connect_button, self.closed, self.pull_button, self.select_all, self.remember, self.forget_button]:
            widget.setEnabled(not value)
        self.update_selection()
        if not value:
            self.forget_button.setEnabled(self.client.connection().get("remembered", False))
        self.close_button.setText("取消拉取" if value else "关闭")

    def pull(self):
        if self.busy:
            return
        if self.key.text().strip() and not self.configure():
            return
        self.issues = []
        self.render_issues()
        self.status.setText("正在从 Linear 拉取 My Issues…")
        self.set_busy(True)
        self.worker = PullWorker(self.client, self.closed.isChecked(), self)
        self.worker.finished.connect(self.pull_finished)
        self.worker.start()

    def pull_finished(self):
        worker = self.worker
        self.worker = None
        self.set_busy(False)
        if self.closing:
            worker.deleteLater()
            super().reject()
            return
        if worker.error:
            self.status.setText(worker.error)
        else:
            self.issues = worker.result["issues"]
            self.render_issues()
            self.status.setText(f"{worker.result['viewer']['name']} · 拉取到 {len(self.issues)} 个 issues" if self.issues else "My Issues 为空，可以勾选包含已完成事项后重新拉取。")
            self.update_connection()
        worker.deleteLater()

    def render_issues(self):
        clear(self.rows)
        self.checks = []
        self.select_all.blockSignals(True)
        self.select_all.setChecked(False)
        self.select_all.blockSignals(False)
        for issue in self.issues:
            row = QWidget()
            col = box(row, margins=(5, 7, 5, 7), spacing=4)
            check = QCheckBox(issue["identifier"])
            check.setToolTip(issue["title"])
            title_row = box(horizontal=True, spacing=8)
            title_row.addWidget(check)
            title_row.addWidget(label(issue["title"], wrap=True), 1)
            col.addLayout(title_row)
            check.toggled.connect(self.update_selection)
            col.addWidget(label(issue["team"] + "  ·  " + issue["state_name"] + ("  ·  " + issue["due_date"] if issue["due_date"] else ""), "muted", True))
            self.rows.addWidget(row)
            self.checks.append((issue["id"], check))
        self.update_selection()

    def update_selection(self):
        count = sum(check.isChecked() for _, check in self.checks)
        self.import_button.setEnabled(count > 0 and not self.busy)
        self.import_button.setText(f"导入所选 {count} 项" if count else "导入所选为工作任务")

    def toggle_all(self, value):
        for _, check in self.checks:
            check.setChecked(value)

    def import_selected(self):
        issue_ids = [issue_id for issue_id, check in self.checks if check.isChecked()]
        try:
            result = self.client.import_issues(issue_ids)
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return
        self.status.setText(f"已新增 {result['created']} 项，更新关联信息 {result['updated']} 项，跳过重复 {result['skipped']} 项。")
        self.owner.view = "work"
        self.owner.status_filter = "all"
        self.owner.search.clear()
        if result["task_ids"]:
            self.owner.selected = result["task_ids"][0]
        self.owner.refresh()

    def reject(self):
        if self.busy:
            self.closing = True
            self.worker.cancel_event.set()
            self.status.setText("正在取消拉取，等待当前请求结束…")
            self.close_button.setEnabled(False)
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


class RepositoryDialog(QDialog):
    def __init__(self, owner, task_id):
        super().__init__(owner)
        self.owner = owner
        self.task_id = task_id
        self.setWindowTitle("工作任务 · Git 仓库")
        self.resize(560, 420)
        layout = box(self, margins=(25, 23, 25, 23), spacing=13)
        layout.addWidget(label("让代码，也有一个入口。", "h2"))
        self.kind = QComboBox()
        self.kind.addItem("本地 Git 仓库", "local")
        self.kind.addItem("远程 Git 地址", "remote")
        layout.addWidget(self.kind)
        row = box(horizontal=True)
        self.location = QLineEdit()
        self.location.setPlaceholderText("选择仓库文件夹，支持 Git worktree")
        row.addWidget(self.location, 1)
        self.browse = button("选择目录", "outline", callback=self.choose_directory)
        row.addWidget(self.browse)
        layout.addLayout(row)
        self.kind.currentIndexChanged.connect(self.change_kind)
        self.status = label("关联只保存入口，不会修改仓库内容。", "muted", True)
        layout.addWidget(self.status)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        self.rows = box(host, spacing=8)
        self.rows.setAlignment(Qt.AlignTop)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        footer = box(horizontal=True)
        footer.addWidget(button("完成", callback=self.accept))
        footer.addStretch()
        footer.addWidget(button("关联仓库", "primary", "link", color="#ffffff", callback=self.bind))
        layout.addLayout(footer)
        self.refresh_list()

    def change_kind(self):
        local = self.kind.currentData() == "local"
        self.browse.setVisible(local)
        self.location.clear()
        self.location.setPlaceholderText("选择仓库文件夹，支持 Git worktree" if local else "https://host/owner/repo.git 或 git@host:owner/repo.git")

    def choose_directory(self):
        path = QFileDialog.getExistingDirectory(self, "选择本地 Git 仓库")
        if path:
            self.location.setText(path)

    def refresh_list(self):
        clear(self.rows)
        task = self.owner.tasks.get(self.task_id)
        for index, repo in enumerate(task.get("repositories", [])):
            row = box(horizontal=True)
            info = label(("本地 · " if repo["kind"] == "local" else "远程 · ") + repo["name"], wrap=True)
            info.setToolTip(repo["location"])
            row.addWidget(info, 1)
            row.addWidget(button("移除", callback=lambda checked=False, i=index: self.remove(i)))
            self.rows.addLayout(row)

    def bind(self):
        try:
            self.owner.tasks.bind_repository(self.task_id, self.location.text(), self.kind.currentData())
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return
        self.location.clear()
        self.status.setText("已关联，可以从任务详情打开仓库。")
        self.refresh_list()
        self.owner.refresh()

    def remove(self, index):
        if self.owner.write(lambda: self.owner.tasks.remove_repository(self.task_id, index)):
            self.refresh_list()
            self.owner.refresh()
