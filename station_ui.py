"""Native attention review; project management and execution forms were removed."""
from pathlib import Path
from PyQt5.QtCore import Qt, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
                            QMessageBox, QListWidget, QSplitter, QInputDialog)
from art import OrbitMark
from theme import APP_NAME
from ui_helpers import label, button

STATES = {"review": "待验收", "needs_input": "待补充", "rejected": "已退回",
          "failed": "失败", "interrupted": "状态待核实"}
ATTENTION = set(STATES)


class AttentionDialog(QDialog):
    """Review existing records without submitting or retrying executions."""
    def __init__(self, parent, station):
        super().__init__(parent)
        self.station = station
        self.records = {}
        self.setWindowTitle(APP_NAME+" · 需要我处理")
        self.resize(980, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        heading = QHBoxLayout()
        heading.addWidget(OrbitMark(compact=True))
        heading.addWidget(label("需要你看一眼的小事。", "h2"))
        heading.addStretch()
        layout.addLayout(heading)
        self.status = label("", "muted", True)
        layout.addWidget(self.status)
        splitter = QSplitter()
        self.items = QListWidget()
        self.items.setAccessibleName("待处理记录")
        self.items.currentRowChanged.connect(self.show_selected)
        splitter.addWidget(self.items)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        splitter.addWidget(self.detail)
        splitter.setSizes([360, 560])
        layout.addWidget(splitter, 1)
        actions = QHBoxLayout()
        self.review_buttons = []
        for text, callback in (("验收通过", lambda: self.review(True)), ("退回交付", lambda: self.review(False))):
            control = button(text, "outline", callback=callback)
            self.review_buttons.append(control)
            actions.addWidget(control)
        self.logs_button = button("打开记录日志", "outline", callback=self.open_logs)
        self.artifact_button = button("打开产物", "outline", callback=self.open_artifact)
        actions.addWidget(self.logs_button)
        actions.addWidget(self.artifact_button)
        actions.addStretch()
        actions.addWidget(button("关闭", callback=self.close))
        layout.addLayout(actions)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)
        self.refresh()
        self.timer.start()

    def refresh(self):
        snapshot = self.station.call("snapshot")
        records = [r for r in snapshot["executions"] if r["status"] in ATTENTION
                   and not r["output_data"].get("continued_as")]
        self.status.setText(f"{len(records)} 项待处理 · 查看结果、核实问题或验收已有交付。")
        selected = self.items.currentItem().data(Qt.UserRole) if self.items.currentItem() else None
        self.records = {r["task_id"]: r for r in records}
        self.items.blockSignals(True)
        self.items.clear()
        for record in records:
            self.items.addItem(f"{STATES[record['status']]}  ·  {record['name']}")
            item = self.items.item(self.items.count() - 1)
            item.setData(Qt.UserRole, record["task_id"])
            if record["task_id"] == selected:
                self.items.setCurrentItem(item)
        if self.items.count() and not self.items.currentItem():
            self.items.setCurrentRow(0)
        self.items.blockSignals(False)
        self.show_selected()

    def selected(self):
        item = self.items.currentItem()
        return self.records.get(item.data(Qt.UserRole)) if item else None

    def show_selected(self, *args):
        record = self.selected()
        for control in self.review_buttons:
            control.setEnabled(bool(record and record["status"] == "review"))
        self.logs_button.setEnabled(bool(record))
        self.artifact_button.setEnabled(bool(record and record["output_data"].get("artifacts")))
        if not record:
            self.detail.setPlainText("暂时没有需要处理的记录。\n\n把注意力留给眼前的一小步就好。")
            return
        source, result = record["input_data"], record["output_data"]
        text = (f"{record['name']} · {STATES[record['status']]}\n"
                f"目录：{source['cwd']}\n\n目标\n{source['goal']}\n\n验收条件\n{source['acceptance']}\n\n")
        text += result.get("summary", result.get("error", "等待核实已有记录"))
        for key, heading in (("question", "需要补充"), ("tests", "执行器报告的检查"),
                             ("artifacts", "产物"), ("codex_thread_id", "Codex 聊天"), ("feedback", "验收意见")):
            if result.get(key):
                value = result[key]
                text += "\n\n" + heading + "\n" + ("\n".join(value) if isinstance(value, list) else str(value))
        self.detail.setPlainText(text)

    def safely(self, operation):
        try:
            operation()
            self.refresh()
        except (ValueError, RuntimeError, OSError) as exc:
            QMessageBox.warning(self, "暂时无法操作", str(exc))

    def review(self, accepted):
        def apply():
            record = self.selected()
            if not record or record["status"] != "review":
                raise ValueError("请先选择一条待验收记录")
            feedback, ok = QInputDialog.getMultiLineText(self, "验收通过" if accepted else "退回交付", "验收意见")
            if ok:
                self.station.call("review", task_id=record["task_id"],
                                  expected_completed_at=record["completed_at"], accepted=accepted, feedback=feedback)
        self.safely(apply)

    def open_logs(self):
        def apply():
            record = self.selected()
            if record:
                folder = self.station.folder / "runs" / record["task_id"]
                if folder.is_dir():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        self.safely(apply)

    def open_artifact(self):
        def apply():
            record = self.selected()
            if not record:
                return
            artifacts = record["output_data"].get("artifacts", [])
            if not artifacts:
                raise ValueError("这条记录没有可打开的产物")
            path, ok = QInputDialog.getItem(self, "打开产物", "文件", artifacts, editable=False)
            if ok and Path(path).is_file() and Path(path).resolve().is_relative_to(Path(record["input_data"]["cwd"]).resolve()):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        self.safely(apply)

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)
