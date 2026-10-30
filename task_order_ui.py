"""Native task dragging with explicit insertion guides and edge scrolling."""
from PyQt5.QtCore import Qt, QMimeData, QTimer, QPoint, QRect
from PyQt5.QtGui import QDrag, QCursor
from PyQt5.QtWidgets import QApplication, QFrame, QWidget
from theme import PALETTE
from ui_helpers import label, box

TASK_MIME = "application/x-orbit-task-id"


class DraggableTaskCard(QFrame):
    def __init__(self, task, owner):
        super().__init__()
        self.task_id = task["id"]; self.task_status = task["status"]; self.owner = owner
        self._press_position = None
        self.setToolTip("拖到其他卡片重新连接 · 拖到间隙调整同级顺序 · 拖到上方独立区移出子步骤")

    def drag_handle(self):
        handle = label("⠿", "muted")
        handle.setFixedWidth(12); handle.setCursor(Qt.OpenHandCursor)
        handle.setToolTip("拖到其他卡片合入，拖到间隙排序，拖到上方独立区移出")
        return handle

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_position = event.pos(); event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press_position is not None:
            self._press_position = None
            self.owner.select_task(self.task_id); event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if not event.buttons() & Qt.LeftButton or self._press_position is None:
            return super().mouseMoveEvent(event)
        if (event.pos() - self._press_position).manhattanLength() < QApplication.startDragDistance():
            return
        press = self._press_position; self._press_position = None
        drag = QDrag(self); mime = QMimeData()
        mime.setData(TASK_MIME, self.task_id.encode("utf-8")); drag.setMimeData(mime)
        preview = self.grab()
        if preview.width() > 360:
            preview = preview.scaledToWidth(360, Qt.SmoothTransformation)
        drag.setPixmap(preview)
        drag.setHotSpot(press * (preview.width() / max(self.width(), 1)))
        self.owner.task_drag_active = True; self.owner.task_order_saved = False
        try:
            drag.exec_(Qt.MoveAction)
        finally:
            self.owner.task_drag_active = False
            QTimer.singleShot(0, self.owner.finish_task_drag)
            drag.deleteLater()


class IndependentTaskDropArea(QFrame):
    """A fixed, explicit target, available even in a long list or another column."""
    def __init__(self, owner):
        super().__init__(); self.owner = owner; self.setAcceptDrops(True)
        self.setObjectName('independentTaskDrop'); self.setAccessibleName('移为独立任务投放区')
        layout = box(self, margins=(12, 8, 12, 8))
        text = label('↗  拖到这里，移为独立任务', 'muted')
        text.setAttribute(Qt.WA_TransparentForMouseEvents); layout.addWidget(text)
        self.highlight(False)

    def highlight(self, active):
        self.setStyleSheet('QFrame#independentTaskDrop { border:1px dashed #92adc1; border-radius:8px; background:'
                          + ('#dce8f0' if active else '#edf2f7') + '; }')

    def source_for(self, event):
        source = event.source()
        if not isinstance(source, DraggableTaskCard) or source.owner is not self.owner:
            return None
        if not event.mimeData().hasFormat(TASK_MIME) or bytes(event.mimeData().data(TASK_MIME)) != source.task_id.encode('utf-8'):
            return None
        task = self.owner.tasks.get(source.task_id)
        return source if task and task.get('parent_id') else None

    def dragEnterEvent(self, event): self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        valid = self.source_for(event) is not None; self.highlight(valid)
        if valid: event.setDropAction(Qt.MoveAction); event.accept()
        else: event.ignore()

    def dragLeaveEvent(self, event): self.highlight(False); event.accept()

    def dropEvent(self, event):
        source = self.source_for(event); self.highlight(False)
        if source and self.owner.detach_task(source.task_id):
            event.setDropAction(Qt.MoveAction); event.accept()
        else: event.ignore()


class TaskDropArea(QWidget):
    def __init__(self, owner, status=None):
        super().__init__()
        self.owner = owner; self.status = status; self._source = None
        self.setAcceptDrops(True)
        self.guide = QFrame(self); self.guide.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.guide.setStyleSheet("background:" + PALETTE["primary"] + "; border-radius:1px;")
        self.guide.hide()
        self.hint = label("松开后选择：子步骤 / 后续步骤", "badge")
        self.hint.setParent(self); self.hint.setAttribute(Qt.WA_TransparentForMouseEvents); self.hint.hide()
        self.scroll_timer = QTimer(self); self.scroll_timer.setInterval(40)
        self.scroll_timer.timeout.connect(self.auto_scroll)

    def cards(self):
        layout = self.layout()
        if not layout:
            return []
        result = []
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if isinstance(widget, DraggableTaskCard):
                result.append(widget)
            elif widget and widget.property('taskBranch'):
                result.extend(widget.findChildren(DraggableTaskCard))
        return result

    def card_rect(self, card):
        return QRect(card.mapTo(self, QPoint(0, 0)), card.size())

    def siblings(self, source):
        task = self.owner.tasks.get(source.task_id)
        if not task:
            return []
        by_id = {item['id']: item for item in self.owner.tasks.tasks}
        return [card for card in self.cards() if card.task_id in by_id
                and by_id[card.task_id].get('parent_id') == task.get('parent_id')]

    def source_for(self, event):
        source = event.source()
        if not isinstance(source, DraggableTaskCard) or source.owner is not self.owner:
            return None
        if self.owner.list_mode == "board":
            if self.status is None:
                return None
        elif self.status is not None:
            return None
        if (not event.mimeData().hasFormat(TASK_MIME)
                or bytes(event.mimeData().data(TASK_MIME)) != source.task_id.encode("utf-8")):
            return None
        return source

    def merge_target(self, pos, source):
        for card in self.cards():
            rect = self.card_rect(card); inset = max(12, rect.height() // 4)
            if card.task_id != source.task_id and rect.adjusted(0, inset, 0, -inset).contains(pos):
                return card
        return None

    def insertion(self, pos, source):
        if not any(card.task_id == source.task_id for card in self.cards()):
            return None  # Cross-column drops connect tasks; never change status or order.
        cards = [card for card in self.siblings(source) if card.task_id != source.task_id]
        if not cards:
            return None
        gap = max(self.layout().spacing() // 2, 2)
        for card in cards:
            rect = self.card_rect(card)
            if pos.y() < rect.center().y():
                return card.task_id, False, rect.top() - gap
        card = cards[-1]
        return card.task_id, True, self.card_rect(card).bottom() + gap

    def show_guide(self, pos):
        target = self.merge_target(pos, self._source)
        self.hint.hide()
        if target:
            self.guide.setStyleSheet("background:rgba(66,108,131,20); border:2px solid " + PALETTE["primary"] + ";border-radius:8px;")
            rect = self.card_rect(target)
            self.guide.setGeometry(rect); self.guide.show(); self.guide.raise_()
            self.hint.setWordWrap(True); self.hint.setFixedWidth(max(80,target.width()-16))
            self.hint.adjustSize(); self.hint.move(rect.x()+8, rect.y()+4)
            self.hint.show(); self.hint.raise_()
            return
        self.guide.setStyleSheet("background:" + PALETTE["primary"] + ";border-radius:1px;")
        insertion = self.insertion(pos, self._source)
        if insertion:
            y = max(0, min(insertion[2], self.height() - 2))
            self.guide.setGeometry(0, y, self.width(), 2)
            self.guide.show(); self.guide.raise_()
        else:
            self.guide.hide()

    def dragEnterEvent(self, event):
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        self._source = self.source_for(event)
        if self._source is None:
            self.clear_drag(); event.ignore(); return
        if not self.merge_target(event.pos(), self._source) and not self.insertion(event.pos(), self._source):
            self.clear_drag(); event.ignore(); return
        self.show_guide(event.pos())
        if not self.scroll_timer.isActive(): self.scroll_timer.start()
        event.setDropAction(Qt.MoveAction); event.accept()

    def clear_drag(self):
        self.guide.hide(); self.hint.hide(); self.scroll_timer.stop(); self._source = None

    def dragLeaveEvent(self, event):
        self.clear_drag(); event.accept()

    def dropEvent(self, event):
        source = self.source_for(event)
        target = self.merge_target(event.pos(), source) if source else None
        insertion = self.insertion(event.pos(), source) if source and not target else None
        scope = [card.task_id for card in self.siblings(source)] if source else []
        self.clear_drag()
        if target:
            accepted = self.owner.merge_task(source.task_id, target.task_id)
            event.setDropAction(Qt.MoveAction)
            event.accept() if accepted else event.ignore()
            return
        if insertion and self.owner.reorder_task(source.task_id, insertion[0], insertion[1], scope):
            event.setDropAction(Qt.MoveAction); event.accept()
        else:
            event.ignore()

    def auto_scroll(self):
        if self._source is None:
            return
        viewport = self.owner.scroll.viewport()
        cursor = QCursor.pos(); pos = viewport.mapFromGlobal(cursor)
        if not viewport.rect().contains(pos):
            return
        delta = -14 if pos.y() < 36 else (14 if pos.y() > viewport.height() - 36 else 0)
        if delta:
            bar = self.owner.scroll.verticalScrollBar(); bar.setValue(bar.value() + delta)
            self.show_guide(self.mapFromGlobal(cursor))
