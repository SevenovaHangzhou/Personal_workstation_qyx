"""Separate native dialogs for creating a chat and binding existing chats."""
import threading
from datetime import datetime
from pathlib import Path
from PyQt5.QtCore import Qt, QThread, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QApplication, QDialog, QWidget, QFrame, QLineEdit,
                            QComboBox, QCheckBox, QScrollArea, QFileDialog, QMessageBox)
from codex_support import redact
from model import has_linear_context
from ui_helpers import label, button, box, clear


class HistoryWorker(QThread):
    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.cancel_event = threading.Event()
        self.result = None
        self.error = ""

    def run(self):
        try:
            self.result = self.operation(self.cancel_event)
        except (ValueError, OSError) as exc:
            self.error = str(exc)
        except Exception:
            self.error = "Codex 操作未完成，请重试或检查本机 Codex 状态"


class CodexCreateDialog(QDialog):
    def __init__(self, owner, task):
        super().__init__(owner)
        self.owner = owner; self.task = task
        self.worker = None; self.busy = False; self.closing = False
        self.binding = None; self.brief = ""
        self.setWindowTitle("新建聊天")
        self.setMinimumWidth(500)
        layout = box(self, margins=(25, 22, 25, 22), spacing=13)
        layout.addWidget(label("新建聊天", "h2"))
        layout.addWidget(label(task["title"], "muted", True))
        layout.addWidget(label("选择工作目录，创建后自动绑定到这件任务。", "muted", True))
        layout.addWidget(label("聊天工作目录", "section"))
        row = box(horizontal=True, spacing=8)
        self.directory = QLineEdit()
        self.directory.setPlaceholderText("选择或输入一个本机目录…")
        self.directory.setMaxLength(4096)
        local = next((r["location"] for r in task.get("repositories", []) if r["kind"] == "local"), "")
        self.directory.setText(local)
        row.addWidget(self.directory, 1)
        self.choose_button = button("选择目录", "outline", callback=self.choose_directory)
        row.addWidget(self.choose_button); layout.addLayout(row)
        self.status = label("创建空聊天后会打开 Codex，并把任务说明复制到剪贴板，方便你粘贴。", "muted", True)
        layout.addWidget(self.status)
        footer = box(horizontal=True)
        self.close_button = button("取消", callback=self.reject)
        footer.addWidget(self.close_button); footer.addStretch()
        self.create_button = button("创建并绑定", "primary", callback=self.create_chat)
        footer.addWidget(self.create_button); layout.addLayout(footer)

    def choose_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "选择新聊天的工作目录", self.directory.text())
        if directory:
            self.directory.setText(directory)

    def set_busy(self, value):
        self.busy = value
        self.directory.setEnabled(not value and self.binding is None)
        self.choose_button.setEnabled(not value and self.binding is None)
        self.create_button.setEnabled(not value)

    def create_chat(self):
        if self.busy or self.closing:
            return
        if self.binding:
            self.open_chat(); return
        task = self.owner.tasks.get(self.task["id"])
        if not task:
            self.status.setText("这件任务已不存在，请关闭窗口后重新选择。")
            return
        path = Path(self.directory.text().strip()).expanduser()
        if not path.is_absolute() or not path.is_dir():
            self.status.setText("请选择存在的绝对路径作为聊天工作目录。")
            return
        self.brief = redact("\n\n".join(part for part in
                            [task["title"], task["description"],
                             "下一步：" + task["next_step"] if task["next_step"] else ""] if part))
        self.set_busy(True)
        self.status.setText("正在新建空聊天并绑定…")
        self.worker = HistoryWorker(lambda cancel: self.owner.runtime.codex.create(task["id"], str(path)), self)
        self.worker.finished.connect(self.finished_operation)
        self.worker.start()

    def finished_operation(self):
        worker = self.worker; self.worker = None
        if worker.result:
            self.binding = worker.result
            self.owner.refresh()
        self.set_busy(False)
        worker.deleteLater()
        if self.closing:
            super().reject(); return
        if worker.error:
            self.status.setText(worker.error)
            return
        QApplication.clipboard().setText(self.brief)
        self.create_button.setText("打开聊天")
        self.close_button.setText("完成")
        self.open_chat()

    def open_chat(self):
        if QDesktopServices.openUrl(QUrl(self.binding["url"])):
            self.status.setText("已新建聊天并绑定；任务说明已复制，可在 Codex 中粘贴。")
        else:
            self.status.setText("已新建并绑定，暂时无法打开 Codex。可重试打开，或复制链接：\n" + self.binding["url"])

    def reject(self):
        if self.busy:
            self.closing = True
            self.status.setText("正在完成创建，结束后关闭窗口…")
            self.close_button.setEnabled(False)
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self.reject(); event.ignore()
        else:
            super().closeEvent(event)


class CodexBindingDialog(QDialog):
    def __init__(self, owner, task, initial_kind="codex"):
        super().__init__(owner)
        self.owner = owner; self.task = task
        self.worker = None; self.busy = False; self.closing = False
        self.started = False; self.result = None
        self.resize(680, 760); self.setMinimumWidth(560)
        layout = box(self, margins=(25, 22, 25, 22), spacing=11)
        self.heading = label("", "h2"); layout.addWidget(self.heading)
        layout.addWidget(label(task["title"], "muted", True))
        self.kind = QComboBox(); self.kind.addItem("Codex 聊天", "codex")
        if task["category"] == "work":
            self.kind.addItem("Linear issue", "linear")
        initial_index = self.kind.findData(initial_kind)
        if initial_index < 0:
            raise ValueError("此任务不支持该关联类型")
        self.kind.setCurrentIndex(initial_index)
        layout.addWidget(self.kind)
        self.history_host = QWidget(); history = box(self.history_host, spacing=10)
        search_row = box(horizontal=True, spacing=8)
        self.query = QLineEdit(); self.query.setPlaceholderText("按任务内容查找，也可输入聊天关键词…"); self.query.setMaxLength(2000)
        self.query.returnPressed.connect(self.refresh_recommendations)
        search_row.addWidget(self.query, 1)
        self.refresh_button = button("搜索聊天", "soft", callback=self.refresh_recommendations)
        search_row.addWidget(self.refresh_button); history.addLayout(search_row)
        self.archived = QCheckBox("也查找归档聊天"); history.addWidget(self.archived)
        self.status = label("打开后自动读取本机 Codex 历史，按内容、issue 与仓库匹配。", "muted", True)
        history.addWidget(self.status)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        self.candidate_host = QWidget(); self.rows = box(self.candidate_host, spacing=9); self.rows.setAlignment(Qt.AlignTop)
        scroll.setWidget(self.candidate_host); history.addWidget(scroll, 1)
        layout.addWidget(self.history_host, 1)
        layout.addWidget(label("已有的关联", "section"))
        linked_scroll = QScrollArea(); linked_scroll.setWidgetResizable(True); linked_scroll.setMaximumHeight(115)
        self.linked_host = QWidget(); self.list = box(self.linked_host, spacing=5)
        linked_scroll.setWidget(self.linked_host); layout.addWidget(linked_scroll)
        manual = box(horizontal=True, spacing=8)
        self.input = QLineEdit(); self.input.setPlaceholderText("也可粘贴 codex://threads/聊天ID 手动关联"); self.input.setMaxLength(2000)
        manual.addWidget(self.input, 1)
        self.bind_button = button("关联链接", "outline", callback=self.bind); manual.addWidget(self.bind_button)
        layout.addLayout(manual)
        self.error = label("历史内容仅在本机匹配，不调用模型；点击候选后才会绑定。", "muted", True)
        layout.addWidget(self.error)
        footer = box(horizontal=True)
        footer.addStretch()
        self.close_button = button("完成", "primary", callback=self.reject); footer.addWidget(self.close_button)
        layout.addLayout(footer)
        self.kind.currentIndexChanged.connect(self.change_kind)
        self.change_kind()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.started:
            self.started = True
            if self.kind.currentData() == "codex":
                QTimer.singleShot(0, self.refresh_recommendations)

    def change_kind(self):
        codex = self.kind.currentData() == "codex"
        self.setWindowTitle("绑定已有聊天" if codex else "关联 Linear issue")
        self.heading.setText("选择已有聊天，接回任务上下文。" if codex else "关联已有 issue，对齐工作进度。")
        self.history_host.setVisible(codex)
        self.input.setPlaceholderText("也可粘贴 codex://threads/聊天ID 手动关联" if codex else "https://linear.app/team/issue/APP-123/title")
        self.bind_button.setText("关联链接" if codex else "关联 issue")
        self.error.setText("历史内容仅在本机匹配，不调用模型；点击候选后才会绑定。" if codex else "粘贴 Linear issue 链接，将它关联到当前工作任务。")
        self.setMinimumHeight(620 if codex else 360)
        self.resize(self.width(), 760 if codex else 420)
        self.refresh_list()
        if codex and self.started and not self.result:
            self.refresh_recommendations()

    def refresh_list(self):
        clear(self.list)
        self.task = self.owner.tasks.get(self.task["id"])
        visible_bindings = 0
        for index, binding in enumerate(self.task["bindings"]):
            if binding["kind"] != self.kind.currentData():
                continue
            visible_bindings += 1
            row = box(horizontal=True, spacing=8)
            row.addWidget(label(("示例 · " if binding.get("demo") else "") + binding["label"], "muted", True), 1)
            row.addWidget(button("移除", callback=lambda checked=False, i=index: self.remove(i)))
            self.list.addLayout(row)
        if not visible_bindings:
            self.list.addWidget(label("还没有关联，可选择已有聊天或粘贴链接。" if self.kind.currentData() == "codex" else "还没有关联 issue，请粘贴已有 issue 链接。", "muted"))
        if has_linear_context(self.task) and self.task["category"] != "work":
            self.list.addWidget(button("解除全部 Linear 关联", "linkButton", callback=self.detach_linear))

    def detach_linear(self):
        text = "将解除此任务的全部 Linear 链接与导入信息，保留本地任务、笔记和步骤。\n不会修改远端 issue。"
        if QMessageBox.question(self, "解除 Linear 关联？", text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if self.owner.write(lambda: self.owner.tasks.detach_linear(self.task["id"])):
            self.refresh_list(); self.owner.refresh()

    def set_busy(self, value):
        self.busy = value
        for widget in [self.kind, self.query, self.refresh_button, self.archived,
                       self.input, self.bind_button, self.candidate_host, self.linked_host]:
            widget.setEnabled(not value)
        self.close_button.setText("取消查找" if value else "完成")

    def refresh_recommendations(self):
        if self.busy or self.closing or self.kind.currentData() != "codex":
            return
        task_id = self.task["id"]; query = self.query.text(); archived = self.archived.isChecked()
        self.status.setText("正在本机查找相关历史聊天…")
        self.start_worker(lambda cancel: self.owner.runtime.codex.recommend(task_id, query, archived, cancel))

    def start_worker(self, operation):
        self.set_busy(True)
        self.worker = HistoryWorker(operation, self)
        self.worker.finished.connect(self.finished_operation)
        self.worker.start()

    def finished_operation(self):
        worker = self.worker; self.worker = None; self.set_busy(False)
        if self.closing:
            worker.deleteLater(); super().reject(); return
        if worker.error:
            self.status.setText(worker.error)
            self.error.setText("可以重新搜索，或粘贴已有聊天链接进行绑定。")
        else:
            self.result = worker.result
            self.render_candidates()
            self.status.setText(f"已检索 {self.result['scanned']} 条聊天，读取 {self.result['history_read']} 条近期历史。" +
                                (" 找到相关候选，先查看再绑定。" if self.result["matched"] else " 暂无明显匹配，下面列出最近聊天。") +
                                (f" {self.result['failures']} 条历史暂时无法读取。" if self.result.get("failures") else ""))
        worker.deleteLater()

    def render_candidates(self):
        clear(self.rows)
        for candidate in self.result["candidates"]:
            card = QFrame(); card.setObjectName("binding")
            col = box(card, margins=(13, 11, 13, 11), spacing=6)
            col.addWidget(label(candidate["title"], wrap=True))
            col.addWidget(label(" · ".join(candidate["reasons"]), "muted", True))
            if candidate.get("snippet"):
                col.addWidget(label(candidate["snippet"], "muted", True))
            if candidate.get("cwd"):
                location = label(candidate["cwd"], "muted", True); col.addWidget(location)
            row = box(horizontal=True)
            try:
                updated = datetime.fromtimestamp(candidate["updated_at"]).strftime("%m-%d %H:%M")
            except (ValueError, OSError, OverflowError):
                updated = ""
            row.addWidget(label(updated, "muted")); row.addStretch()
            row.addWidget(button("打开查看 ↗", "linkButton", callback=lambda checked=False, r=candidate: QDesktopServices.openUrl(QUrl(r["url"]))))
            row.addWidget(button("绑定此聊天", "outline", callback=lambda checked=False, r=candidate: self.bind_candidate(r)))
            col.addLayout(row); self.rows.addWidget(card)
        if not self.result["candidates"]:
            self.rows.addWidget(label("没有找到可绑定的聊天。\n可以调整关键词、包含归档，或粘贴已有聊天链接。", "muted", True))

    def bind_candidate(self, candidate):
        try:
            self.owner.runtime.codex.bind(self.task["id"], candidate["id"])
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc)); return
        self.error.setText("已绑定「" + candidate["title"] + "」。")
        self.refresh_list(); self.owner.refresh()
        self.result["candidates"] = [r for r in self.result["candidates"] if r["id"] != candidate["id"]]
        self.render_candidates()

    def bind(self):
        try:
            self.owner.tasks.bind(self.task["id"], self.kind.currentData(), self.input.text())
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc)); return
        self.input.clear(); self.error.setText("已关联，任务里的上下文入口已更新。")
        self.refresh_list(); self.owner.refresh()

    def remove(self, index):
        if self.owner.write(lambda: self.owner.tasks.remove_binding(self.task["id"], index)):
            self.refresh_list(); self.owner.refresh()

    def reject(self):
        if self.busy:
            self.closing = True
            self.worker.cancel_event.set()
            self.status.setText("正在取消历史读取…")
            self.close_button.setEnabled(False)
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self.reject(); event.ignore()
        else:
            super().closeEvent(event)
