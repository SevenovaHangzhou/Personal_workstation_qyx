"""Show verified issue percentages without turning missing evidence into zero."""
from PyQt5.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget


class IssueProgress(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(3)
        self.label = QLabel()
        self.bar = QProgressBar()
        self.bar.setRange(0, 10000)
        self.bar.setTextVisible(False)
        self.bar.setAccessibleName('Issue 完成进度')
        layout.addWidget(self.label)
        layout.addWidget(self.bar)

    def refresh(self, issue):
        percent = issue.get('percent')
        canceled = issue.get('state') == 'canceled'
        known = percent is not None and not canceled
        text = f'{percent:g}%' if known else '已取消' if canceled else '待估'
        self.label.setText(text)
        self.bar.setVisible(known)
        self.bar.setValue(round(percent * 100) if known else 0)
        self.bar.setAccessibleDescription(text)
        self.setToolTip('按已核对子任务权重和完成状态计算，依据最近同步资料。' if known else
            '已取消，不计入完成进度。' if canceled else
            '暂无可核实的完成百分比。可在进度明细核对子任务范围、权重和共享资料；当前状态请看状态列。')
