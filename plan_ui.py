"""Native Qt step planning and code-generated, interactive DAG rendering."""
import copy
import uuid

from PyQt5.QtCore import Qt, QRectF, QPointF, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF, QKeySequence
from PyQt5.QtWidgets import (QWidget, QFrame, QDialog, QGraphicsObject, QGraphicsScene,
    QGraphicsView, QLineEdit, QPlainTextEdit, QListWidget, QListWidgetItem, QSplitter,
    QAbstractItemView, QShortcut)

from dag_layout import layout_dag, layers_for
from model import STATUSES
from task_plan import plan_rows, validate_plan
from ui_helpers import box, button, label


class StepNode(QGraphicsObject):
    clicked = pyqtSignal(str)
    activated = pyqtSignal(str)

    def __init__(self, task, virtual=False):
        super().__init__()
        self.task = task
        self.virtual = virtual
        self.chosen = False
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor if not virtual else Qt.ArrowCursor)
        self.setToolTip(task["title"] + ("\n点击选中 · 双击打开任务" if not virtual else ""))

    def boundingRect(self):
        return QRectF(30, 28, 148, 40) if self.virtual else QRectF(0, 0, 208, 96)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        if self.virtual:
            painter.setPen(QPen(QColor("#cdd9e3"), 1))
            painter.setBrush(QColor("#e9f0f4"))
            painter.drawRoundedRect(QRectF(30, 28, 148, 40), 20, 20)
            painter.setPen(QColor("#5f7488"))
            painter.drawText(QRectF(32, 28, 144, 40), Qt.AlignCenter, self.task["title"])
            return
        state = self.task.get("status", "todo")
        tint = {"todo": "#ffffff", "doing": "#e7eff5", "waiting": "#f4ead9", "done": "#edf2f7"}[state]
        painter.setBrush(QColor(tint))
        painter.setPen(QPen(QColor("#92adc1" if self.chosen else "#cdd9e3"), 2 if self.chosen else 1))
        painter.drawRoundedRect(QRectF(1, 1, 206, 94), 14, 14)
        painter.save()
        painter.setClipRect(QRectF(14, 10, 180, 52))
        painter.setFont(QFont("Noto Sans CJK SC", 10))
        painter.setPen(QColor("#7d8b9b" if state == "done" else "#273d52"))
        painter.drawText(QRectF(14, 10, 180, 52), Qt.AlignTop | Qt.TextWordWrap, self.task["title"])
        painter.restore()
        painter.setFont(QFont("Noto Sans CJK SC", 8))
        painter.setPen(QColor("#7d8b9b"))
        suffix = " · 未保存" if self.task.get("draft") else ""
        children = len(self.task.get("steps", []))
        if children:
            suffix += f" · {children} 个小步"
        painter.drawText(QRectF(14, 69, 180, 18), Qt.AlignLeft | Qt.AlignVCenter, STATUSES[state] + suffix)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.virtual:
            self.clicked.emit(self.task["id"])
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and not self.virtual:
            self.activated.emit(self.task["id"])
        super().mouseDoubleClickEvent(event)


class DagView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setBackgroundBrush(QColor("#f5f7fa"))
        self.setFrameShape(QFrame.NoFrame)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.auto_fit = True
        self.setAccessibleName("从上到下的步骤依赖图，可拖动，Ctrl 加滚轮缩放")

    def fit_graph(self):
        self.auto_fit = True
        rect = self.scene().itemsBoundingRect().adjusted(-28, -20, 28, 20)
        if not rect.isEmpty():
            self.fitInView(rect, Qt.KeepAspectRatio)
            if self.transform().m11() > 1.1:
                self.resetTransform()

    def zoom(self, factor):
        scale = self.transform().m11() * factor
        if .05 <= scale <= 3:
            self.auto_fit = False
            self.scale(factor, factor)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.zoom(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.auto_fit:
            self.fit_graph()


class TaskDag(QWidget):
    selected = pyqtSignal(str)
    activated = pyqtSignal(str)

    def __init__(self, parent=None, compact=False):
        super().__init__(parent)
        self.compact = compact
        self.nodes = {}
        self.positions = {}
        self.edges = []
        self.current_id = None
        layout = box(self, spacing=7)
        if not compact:
            tools = box(horizontal=True, spacing=4)
            tools.addWidget(label("步骤图 · 从上到下", "section"))
            tools.addStretch()
            tools.addWidget(button("−", "soft", callback=lambda: self.view.zoom(1 / 1.2)))
            tools.addWidget(button("＋", "soft", callback=lambda: self.view.zoom(1.2)))
            tools.addWidget(button("适应画布", "soft", callback=lambda: self.view.fit_graph()))
            layout.addLayout(tools)
        self.view = DagView(self)
        self.view.setMinimumHeight(155 if compact else 260)
        layout.addWidget(self.view, 1)
        self.caption = label("", "muted", True)
        layout.addWidget(self.caption)

    def choose(self, task_id):
        self.current_id = task_id
        for key, node in self.nodes.items():
            node.chosen = key == task_id
            node.update()

    def set_tasks(self, tasks):
        scene = self.view.scene()
        scene.clear()
        self.nodes = {}
        ids = [t["id"] for t in tasks]
        real_edges = [(source, t["id"]) for t in tasks for source in t.get("depends_on", [])]
        try:
            layers_for(ids, real_edges)
        except ValueError as exc:
            self.caption.setText(str(exc))
            self.positions, self.edges = {}, []
            return
        if not ids:
            scene.addText("从一个小步开始，连接接下来的方向。", QFont("Noto Sans CJK SC", 10)).setDefaultTextColor(QColor("#7d8b9b"))
            self.positions, self.edges = {}, []
            self.caption.setText("没有前置依赖的步骤会并排展示。")
            self.view.fit_graph()
            return
        start, end = object(), object()
        sources = {source for source, _ in real_edges}
        targets = {target for _, target in real_edges}
        self.edges = ([(start, key) for key in ids if key not in targets] + real_edges
                      + [(key, end) for key in ids if key not in sources])
        all_tasks = [{"id": start, "title": "开始"}] + list(tasks) + [{"id": end, "title": "本层步骤汇合"}]
        self.positions, layers = layout_dag([t["id"] for t in all_tasks], self.edges, height=96, y_gap=40)
        # Virtual start/end pills need less vertical room than real task cards.
        self.positions = {key: (x, y - (80 if key == end else 0 if key == start else 40))
                          for key, (x, y) in self.positions.items()}
        rank = {key: index for index, layer in enumerate(layers) for key in layer}
        long_index = 0
        for source, target in self.edges:
            sx, sy = self.positions[source]
            tx, ty = self.positions[target]
            x1, y1 = sx + 104, sy + (68 if source == start else 96)
            x2, y2 = tx + 104, ty + (28 if target == end else 0)
            path = QPainterPath(QPointF(x1, y1))
            if rank[target] - rank[source] > 1:
                # Route skip-level dependencies outside nodes to keep every card legible.
                lane = -26 - 14 * long_index
                long_index += 1
                path.lineTo(x1, y1 + 20)
                path.lineTo(lane, y1 + 20)
                path.lineTo(lane, y2 - 22)
                path.lineTo(x2, y2 - 22)
            else:
                middle = (y1 + y2) / 2
                path.lineTo(x1, middle)
                path.lineTo(x2, middle)
            path.lineTo(x2, y2 - 5)
            scene.addPath(path, QPen(QColor("#b5c7d5"), 1.5))
            scene.addPolygon(QPolygonF([QPointF(x2, y2), QPointF(x2 - 4, y2 - 7), QPointF(x2 + 4, y2 - 7)]),
                             QPen(Qt.NoPen), QColor("#b5c7d5"))
        for task in all_tasks:
            node = StepNode(task, task["id"] in (start, end))
            scene.addItem(node)
            node.setPos(*self.positions[task["id"]])
            if task["id"] not in (start, end):
                self.nodes[task["id"]] = node
                node.clicked.connect(self.selected)
                node.activated.connect(self.activated)
        self.choose(self.current_id)
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-28, -20, 28, 20))
        roots = sum(t["id"] not in targets for t in tasks)
        joins = sum(len(t.get("depends_on", [])) > 1 for t in tasks)
        self.caption.setText(f"{len(ids)} 个步骤 · {roots} 个起点 · {joins} 处汇合" +
                             ("" if self.compact else "\n箭头表示前置依赖；仅用于规划，执行仍由你手动发起。"))
        if self.view.auto_fit:
            self.view.fit_graph()


class PlanDialog(QDialog):
    """Draft changes locally, preview immediately, then save in one service call."""
    def __init__(self, owner, task_id):
        super().__init__(owner)
        self.owner, self.task_id = owner, task_id
        self.task = owner.tasks.get(task_id)
        self.task_snapshot = {t["id"]: t for t in owner.tasks.tasks if t.get("parent_id") == task_id}
        self.baseline = plan_rows(self.task_snapshot.values(), task_id)
        self.rows = copy.deepcopy(self.baseline)
        self.original_ids = {r["id"] for r in self.rows}
        self.current_id = None
        self.setWindowTitle("规划下一步 · " + self.task["title"])
        self.resize(1160, 820)
        self.setMinimumSize(940, 720)
        layout = box(self, margins=(24, 22, 24, 22), spacing=12)
        layout.addWidget(label("规划下一步", "h2"))
        layout.addWidget(label(self.task["title"], "muted", True))
        layout.addWidget(label("列出可以并行的方向，或接下来的一小步。勾选前置步骤，就能连成路线。", "muted", True))
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)
        editor = QWidget()
        left = box(editor, margins=(0, 0, 14, 0), spacing=7)
        left.addWidget(label("想到什么，先写下来", "section"))
        self.quick_input = QPlainTextEdit()
        self.quick_input.setPlaceholderText("每行一件小事…\n例如：梳理使用场景")
        self.quick_input.setFixedHeight(70)
        self.quick_input.setAccessibleName("批量录入方向或小步，每行一项")
        left.addWidget(self.quick_input)
        actions = box(horizontal=True, spacing=6)
        actions.addWidget(button("加入并行方向", "soft", callback=self.add_parallel))
        self.follow_button = button("接在选中步骤后", "soft", callback=self.add_following)
        actions.addWidget(self.follow_button)
        left.addLayout(actions)
        self.follow_button.setToolTip("输入的每一行都以当前选中步骤为前置")
        self.step_list = QListWidget()
        self.step_list.setMinimumHeight(155)
        self.step_list.setStyleSheet("QListWidget::item { padding:7px 9px; }")
        self.step_list.setWordWrap(True)
        self.step_list.setAccessibleName("规划中的步骤")
        self.step_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.step_list.currentItemChanged.connect(self.select_item)
        left.addWidget(self.step_list, 1)
        self.title_input = QLineEdit()
        self.title_input.setMaxLength(100)
        self.title_input.setPlaceholderText("选中步骤后，调整名称")
        self.title_input.setAccessibleName("步骤名称")
        self.title_input.textEdited.connect(self.rename)
        left.addWidget(self.title_input)
        left.addWidget(label("前置步骤 · 可以勾选多项，完成后在这里汇合", "section", True))
        self.dependencies = QListWidget()
        self.dependencies.setMinimumHeight(100)
        self.dependencies.setMaximumHeight(130)
        self.dependencies.setStyleSheet("QListWidget::item { padding:7px 9px; }")
        self.dependencies.setAccessibleName("选择当前步骤的前置步骤")
        self.dependencies.itemChanged.connect(self.change_dependency)
        left.addWidget(self.dependencies)
        self.remove_button = button("移除未保存的小步", "linkButton", callback=self.remove_draft)
        left.addWidget(self.remove_button)
        splitter.addWidget(editor)
        self.graph = TaskDag()
        self.graph.selected.connect(self.select_id)
        self.graph.activated.connect(self.select_id)
        splitter.addWidget(self.graph)
        splitter.setSizes([390, 690])
        self.error = label("", None, True)
        self.error.setStyleSheet("color:#af766e;")
        layout.addWidget(self.error)
        if self.task["category"] == "work":
            layout.addWidget(label("保存后，每个新增小步都是独立工作任务；晚间创建子 issue，并继承父任务归属。", "muted", True))
        foot = box(horizontal=True)
        foot.addWidget(label("只规划眼前够用的一小段，也很好。", "muted"))
        foot.addStretch()
        foot.addWidget(button("取消", callback=self.reject))
        foot.addWidget(button("保存规划", "primary", callback=self.accept))
        layout.addLayout(foot)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(120)
        self.preview_timer.timeout.connect(self.refresh_graph)
        QShortcut(QKeySequence("Ctrl+Return"), self.quick_input, activated=self.add_parallel)
        self.refresh_rows(self.rows[0]["id"] if self.rows else None)
        QTimer.singleShot(0, self.graph.view.fit_graph)

    def current_row(self):
        return next((r for r in self.rows if r["id"] == self.current_id), None)

    def select_item(self, item, previous=None):
        self.current_id = item.data(Qt.UserRole) if item else None
        row = self.current_row()
        self.title_input.setEnabled(bool(row))
        self.title_input.setText(row["title"] if row else "")
        self.follow_button.setEnabled(bool(row))
        self.remove_button.setVisible(bool(row) and row["id"] not in self.original_ids)
        self.dependencies.blockSignals(True)
        self.dependencies.clear()
        if row:
            for other in self.rows:
                if other["id"] == row["id"]:
                    continue
                item = QListWidgetItem(other["title"])
                item.setData(Qt.UserRole, other["id"])
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if other["id"] in row["depends_on"] else Qt.Unchecked)
                self.dependencies.addItem(item)
        self.dependencies.blockSignals(False)
        self.graph.choose(self.current_id)

    def select_id(self, task_id):
        for index in range(self.step_list.count()):
            item = self.step_list.item(index)
            if item.data(Qt.UserRole) == task_id:
                self.step_list.setCurrentItem(item)
                self.step_list.scrollToItem(item)
                return

    def refresh_rows(self, selected=None):
        self.step_list.blockSignals(True)
        self.step_list.clear()
        for row in self.rows:
            detail = "可先开始" if not row["depends_on"] else f"接在 {len(row['depends_on'])} 项之后"
            if row["id"] not in self.original_ids:
                detail += " · 未保存"
            item = QListWidgetItem(row["title"])
            item.setToolTip(detail)
            item.setData(Qt.UserRole, row["id"])
            self.step_list.addItem(item)
            if row["id"] == selected:
                self.step_list.setCurrentItem(item)
        self.step_list.blockSignals(False)
        self.select_item(self.step_list.currentItem())
        self.refresh_graph()

    def refresh_graph(self):
        tasks = [{**self.task_snapshot.get(r["id"], {}), **r, "draft": r["id"] not in self.original_ids} for r in self.rows]
        self.graph.set_tasks(tasks)
        self.graph.choose(self.current_id)

    def add_parallel(self):
        self.add_rows([])

    def add_following(self):
        if self.current_row():
            self.add_rows([self.current_id])

    def add_rows(self, dependencies):
        titles = [s.strip() for s in self.quick_input.toPlainText().splitlines() if s.strip()]
        if not titles:
            self.error.setText("先写下一件小事吧，每行一项。")
            self.quick_input.setFocus()
            return
        rows = self.rows + [{"id": str(uuid.uuid4()), "title": title, "depends_on": list(dependencies)} for title in titles]
        try:
            validate_plan(rows)
            if sum(r["id"] not in self.original_ids for r in rows) > 100:
                raise ValueError("一次最多新增 100 个小步")
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.rows = rows
        self.quick_input.clear()
        self.error.clear()
        self.refresh_rows(rows[-1]["id"])

    def rename(self, text):
        row = self.current_row()
        if row:
            row["title"] = text
            item = self.step_list.currentItem()
            if item:
                item.setText(text)
            self.preview_timer.start()

    def change_dependency(self, item):
        row = self.current_row()
        if not row:
            return
        old = list(row["depends_on"])
        row["depends_on"] = [self.dependencies.item(i).data(Qt.UserRole) for i in range(self.dependencies.count())
                             if self.dependencies.item(i).checkState() == Qt.Checked]
        try:
            validate_plan(self.rows)
        except ValueError as exc:
            row["depends_on"] = old
            self.error.setText(str(exc))
            self.select_item(self.step_list.currentItem())
            return
        self.error.clear()
        self.refresh_rows(self.current_id)

    def remove_draft(self):
        row = self.current_row()
        if not row or row["id"] in self.original_ids:
            return
        if any(row["id"] in r["depends_on"] for r in self.rows):
            self.error.setText("其他小步还以它为前置，请先取消相关勾选。")
            return
        self.rows.remove(row)
        self.error.clear()
        self.refresh_rows(self.rows[-1]["id"] if self.rows else None)

    def accept(self):
        if self.quick_input.toPlainText().strip():
            self.error.setText("输入框还有未加入的内容，请先选择「加入并行方向」或「接在选中步骤后」。")
            self.quick_input.setFocus()
            return
        try:
            validate_plan(self.rows)
            self.owner.tasks.apply_plan(self.task_id, self.rows, self.baseline)
        except (OSError, ValueError) as exc:
            self.error.setText(str(exc))
            return
        super().accept()
