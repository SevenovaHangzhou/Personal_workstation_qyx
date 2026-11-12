#!/usr/bin/env python3
"""ORBIT / 星序 — a native Qt desktop attention and task manager."""
import argparse
import os
import sys
if __name__=="__main__": os.environ.setdefault("ORBIT_APP_PROFILE","work")
from app_profile import PROFILE

# PyInstaller's Windows interpreter ignores PYTHONUTF8 at bootstrap. Console
# pipes can otherwise default to CP1252 and fail on the Chinese app name/logs.
if os.name=='nt':
    for stream in (sys.stdout,sys.stderr):
        if stream is not None and hasattr(stream,'reconfigure'):
            stream.reconfigure(encoding='utf-8',errors='backslashreplace')
from datetime import date
from pathlib import Path

from PyQt5.QtCore import Qt, QSize, QDate, QTimer, QThread, QEventLoop, QUrl, QStandardPaths
from PyQt5.QtGui import QColor, QFont, QKeySequence, QDesktopServices
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel,
    QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QTextEdit,
    QScrollArea, QComboBox, QCheckBox, QDialog, QSizeGrip,
    QMessageBox, QDateEdit, QShortcut, QProgressBar, QSystemTrayIcon, QMenu)
from qt_runtime import configure_qt_plugins

configure_qt_plugins()

from model import CATEGORIES, STATUSES, make_task, progress, parse_binding, has_work_issue, has_linear_context
CATEGORIES = {k:v for k,v in CATEGORIES.items() if k in PROFILE.categories}
from art import icon, OrbitMark
from framework import api
from runtime import MomoRuntime, DataSpaceInUse
from display_settings import DisplayController
from window_activation import WindowActivation, reveal_window
from typedefine.TypeDefine import APP_VERSION

from theme import APP_NAME, APP_WORDMARK, APP_DISPLAY_NAME, TAGLINE, CATEGORY_COLORS, STYLE, PALETTE

COLORS = CATEGORY_COLORS
ICONS = {"work": "work", "learn": "book", "demo": "flask", "life": "cup"}




from ui_helpers import label, button, box, line, clear
from integration_ui import LinearDialog, RepositoryDialog
from codex_ui import CodexBindingDialog, CodexCreateDialog
from plan_ui import PlanDialog, TaskDag
from task_order_ui import DraggableTaskCard, TaskDropArea, IndependentTaskDropArea
from task_outline import TaskOutline
from task_outline_ui import add_relations, add_step_toggle, add_outline_card, build_context_card


class TitleBar(QFrame):
    """Use the compositor's move operation (required on Wayland)."""
    def __init__(self, window):
        super().__init__(); self.window=window; self.drag=None
        self.setObjectName("titlebar"); self.setFixedHeight(40)
        self.setToolTip("拖动标题栏移动窗口 · 双击最大化或还原")
        row=box(self,True,(16,0,10,0),8)
        badge=QLabel(); badge.setPixmap(icon("orbit",PALETTE["primary"],18).pixmap(18,18)); row.addWidget(badge)
        name=label(APP_DISPLAY_NAME,"muted"); row.addWidget(name); row.addStretch()
        for child in (badge,name): child.setAttribute(Qt.WA_TransparentForMouseEvents)
        for glyph,tip,fn in [("minimize","最小化",window.showMinimized),("maximize","最大化 / 还原",self.maximize),("close","收起到托盘",window.close)]:
            control=button(name="iconButton",glyph=glyph,callback=fn)
            control.setFixedSize(30,28); control.setToolTip(tip); control.setAccessibleName(tip); row.addWidget(control)

    def maximize(self):
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()

    def start_system_move(self):
        handle=self.window.windowHandle()
        try: return bool(handle and handle.startSystemMove())
        except (AttributeError,RuntimeError): return False

    def mousePressEvent(self,event):
        if event.button()!=Qt.LeftButton: return super().mousePressEvent(event)
        self.drag=None
        if not self.start_system_move():
            if self.window.isMaximized():
                ratio=event.pos().x()/max(self.width(),1)
                self.window.showNormal()
                self.window.move(event.globalPos().x()-round(self.window.width()*ratio),event.globalPos().y()-event.pos().y())
            self.drag=event.globalPos()-self.window.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self,event):
        if self.drag is not None and event.buttons() & Qt.LeftButton:
            self.window.move(event.globalPos()-self.drag); event.accept()
        else: super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        self.drag=None; super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.LeftButton:
            self.drag=None; self.maximize(); event.accept()
        else: super().mouseDoubleClickEvent(event)


class DetailSection(QFrame):
    """Keep optional tools available without filling the task reading area."""
    def __init__(self,owner,key,title,content):
        super().__init__(); self.setObjectName('detailSection')
        self.owner=owner; self.key=key; self.title=title
        layout=box(self,spacing=8)
        self.toggle=button(name='sectionToggle',callback=self.flip)
        self.toggle.setCheckable(True); self.toggle.setAccessibleName(title)
        self.content=content; layout.addWidget(self.toggle); layout.addWidget(content)
        self.apply_state(key in owner.open_sections)

    def apply_state(self,expanded):
        self.toggle.setChecked(expanded); self.toggle.setText(('▾  ' if expanded else '▸  ')+self.title)
        self.content.setVisible(expanded)

    def flip(self):
        expanded=self.toggle.isChecked()
        if expanded: self.owner.open_sections.add(self.key)
        else: self.owner.open_sections.discard(self.key)
        self.apply_state(expanded)


class TaskRow(DraggableTaskCard):
    def __init__(self, task, selected, owner, entry=None):
        super().__init__(task,owner); self.setObjectName("taskRow")
        self.setProperty("selected", selected); self.setProperty("complete",task["status"]=="done")
        self.setProperty('substep',bool(task.get('parent_id')))
        self.setProperty('context',bool(entry and entry.context))
        self.setCursor(Qt.PointingHandCursor)
        if entry and entry.context:
            build_context_card(self,entry,owner); return
        row=box(self,True,(15,13,15,13),12)
        row.addWidget(self.drag_handle())
        state=button(name="statusDot",callback=lambda: owner.toggle_done(task["id"]))
        state.setFixedSize(19,19); state.setProperty("status",task["status"])
        state.setToolTip("重新打开任务" if task["status"]=="done" else "标记为完成")
        state.setAccessibleName(state.toolTip())
        if task["status"]=="done": state.setIcon(icon("check","#ffffff",12)); state.setIconSize(QSize(12,12))
        elif task["status"]=="doing": state.setText("·")
        elif task["status"]=="waiting": state.setText("·")
        row.addWidget(state)
        content=box(spacing=6)
        add_relations(content,task,owner,entry)
        title=label(task["title"],wrap=True); title.setProperty("textRole","taskTitle"); title.setStyleSheet(("color: #7d8b9b; text-decoration: line-through;" if task["status"]=="done" else "font-weight: 500;"))
        content.addWidget(title)
        meta=box(horizontal=True,spacing=8)
        category=label(CATEGORIES[task["category"]]); category.setProperty("textRole","small"); category.setStyleSheet("color: "+COLORS[task["category"]]+";")
        meta.addWidget(category)
        for kind in (["codex","linear"] if owner.runtime.profile.work else []):
            bindings=[b for b in task["bindings"] if b["kind"]==kind]
            if bindings:
                glyph=QLabel(); glyph.setPixmap(icon(kind,"#5f7488",13).pixmap(13,13)); meta.addWidget(glyph)
                text="Codex" if kind=="codex" else bindings[0]["label"]
                if len(bindings)>1: text+=" +"+str(len(bindings)-1)
                meta.addWidget(label(text,"muted"))
        meta.addStretch(); content.addLayout(meta)
        add_step_toggle(content,task,owner,entry)
        row.addLayout(content,1)
        right=box(spacing=6)
        status=label(STATUSES[task["status"]]); status.setAlignment(Qt.AlignRight)
        color={"todo":"#7d8b9b","doing":"#426c83","waiting":"#ad884e","done":"#7d8b9b"}[task["status"]]
        status.setProperty("textRole","small"); status.setStyleSheet("color: "+color+";"); right.addWidget(status)
        if task['category']=='learn':
            text,action,accent=owner.course_action(task['id'])
            right.addWidget(label(text,'muted'))
            right.addWidget(button(action,'coral' if accent else 'outline',callback=lambda checked=False:owner.activate_learning(task['id'],accent)))
        row.addLayout(right)

class BoardCard(DraggableTaskCard):
    def __init__(self, task, selected, owner, entry=None):
        super().__init__(task,owner); self.setObjectName("taskRow"); self.setProperty("selected",selected)
        self.setProperty('substep',bool(task.get('parent_id')))
        self.setProperty('context',bool(entry and entry.context))
        self.setProperty('complete',task['status']=='done')
        self.setCursor(Qt.PointingHandCursor)
        if entry and entry.context:
            build_context_card(self,entry,owner,compact=True); return
        col=box(self,margins=(12,12,12,12),spacing=10)
        add_relations(col,task,owner,entry)
        title=label(task["title"],wrap=True); title.setStyleSheet("font-weight:500;"); col.addWidget(title)
        category=label(CATEGORIES[task["category"]]); category.setProperty("textRole","small"); category.setStyleSheet("color:"+COLORS[task["category"]]+";"); col.addWidget(category)
        if task["steps"]:
            p=QProgressBar(); p.setTextVisible(False); p.setValue(progress(task)); col.addWidget(p)
        add_step_toggle(col,task,owner,entry,compact=True)
        if task['category']=='learn':
            text,action,accent=owner.course_action(task['id'])
            col.addWidget(label(text,'muted',True))
            col.addWidget(button(action,'coral' if accent else 'outline',callback=lambda checked=False:owner.activate_learning(task['id'],accent)))
        links=box(horizontal=True,spacing=5)
        for kind in ["codex","linear"]:
            linked=[b for b in task["bindings"] if b["kind"]==kind]
            if linked:
                glyph=QLabel(); glyph.setPixmap(icon(kind,"#5f7488",14).pixmap(14,14)); links.addWidget(glyph)
        links.addStretch(); links.addWidget(label(f"{progress(task)}%","muted")); links.addWidget(self.drag_handle()); col.addLayout(links)

class TaskDialog(QDialog):
    def __init__(self,parent,task=None):
        super().__init__(parent); self.setWindowTitle("编辑任务" if task else "种下一件小事"); self.setMinimumWidth(450)
        self.task=task; layout=box(self,margins=(26,24,26,24),spacing=13)
        layout.addWidget(label("把事情变小一点。","h2"))
        layout.addWidget(label("一句话就够了，剩下的慢慢来。","muted"))
        self.title_input=QLineEdit(task["title"] if task else ""); self.title_input.setPlaceholderText("想做的事情…"); self.title_input.setMaxLength(100)
        layout.addWidget(label("任务名称","section")); layout.addWidget(self.title_input)
        row=box(horizontal=True)
        self.category=QComboBox()
        for key,value in CATEGORIES.items(): self.category.addItem(value,key)
        if task: self.category.setCurrentIndex(list(CATEGORIES).index(task["category"]))
        row.addWidget(self.category)
        self.day=QDateEdit(); self.day.setCalendarPopup(True); self.day.setDisplayFormat("yyyy / MM / dd")
        self.day.setDate(QDate.fromString(task["day"],"yyyy-MM-dd") if task else QDate.currentDate()); row.addWidget(self.day)
        layout.addWidget(label("分类与计划日期","section")); layout.addLayout(row)
        self.issue_label=label("工作 issue 链接（可选；未绑定则晚间创建）","section")
        self.issue_url=QLineEdit()
        self.issue_url.setPlaceholderText("https://linear.app/team/issue/APP-123/title")
        if task:
            current=next((b["url"] for b in task["bindings"] if b["kind"]=="linear" and not b.get("demo")),task.get("linear_issue",{}).get("url",""))
            self.issue_url.setText(current)
        layout.addWidget(self.issue_label); layout.addWidget(self.issue_url)
        self.detach_issue=False
        self.category.currentIndexChanged.connect(self.refresh_issue_field)
        self.refresh_issue_field()
        self.child_titles=QTextEdit(); self.child_titles.setFixedHeight(80); self.child_titles.setPlaceholderText("每行一件小事：可并行的方向，或接下来的一小步")
        if not task:
            layout.addWidget(label("先记下几个方向（可选）","section")); layout.addWidget(self.child_titles)
            layout.addWidget(label("先分别记下；创建后在「规划下一步」里连接前置步骤。","muted",True))
        else:
            self.child_titles.hide()
        if task and task.get("parent_id"): self.category.setEnabled(False)
        layout.addWidget(label("说明","section")); self.description=QTextEdit(); self.description.setPlainText(task["description"] if task else ""); self.description.setFixedHeight(86); layout.addWidget(self.description)
        self.error=label(""); self.error.setStyleSheet("color: #af766e;"); layout.addWidget(self.error)
        row=box(horizontal=True); row.addStretch(); row.addWidget(button("取消",callback=self.reject)); row.addWidget(button("保存任务" if task else "种下小事","primary",callback=self.submit)); layout.addLayout(row)
        self.title_input.setFocus()

    def submit(self):
        if not self.title_input.text().strip(): self.error.setText("给这件事起一个名字吧。"); return
        if self.category.currentData()=="work" and self.issue_url.text().strip():
            try: parse_binding("linear",self.issue_url.text())
            except ValueError as exc: self.error.setText(str(exc)); return
        elif self.category.currentData()!="work" and self.task and has_linear_context(self.task):
            if QMessageBox.question(self,"更改分类并解除 issue？","此分类不使用 Linear issue。更改后将解除本地 issue 关联，保留任务、笔记和步骤。",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
            self.detach_issue=True
        self.accept()

    def refresh_issue_field(self):
        work=self.category.currentData()=="work"
        self.issue_label.setVisible(work); self.issue_url.setVisible(work)

    def values(self):
        values=dict(title=self.title_input.text().strip(),category=self.category.currentData(),day=self.day.date().toString("yyyy-MM-dd"),description=self.description.toPlainText().strip())
        if self.category.currentData()=="work" and self.issue_url.text().strip(): values["issue_url"]=self.issue_url.text().strip()
        elif self.detach_issue: values["detach_linear"]=True
        if not self.task: values["steps"]=[{"title":t.strip(),"done":False} for t in self.child_titles.toPlainText().splitlines() if t.strip()]
        return values


class BindDialog(CodexBindingDialog):
    pass


class Momo(QMainWindow):
    def __init__(self,runtime,display=None):
        super().__init__(); self.display=display or DisplayController(QApplication.instance()); self.runtime=runtime; self.tasks=runtime.tasks; self.focus=runtime.focus
        tasks=self.tasks.tasks; first=next((task for task in tasks if not task.get('parent_id')),tasks[0] if tasks else None)
        self.selected=first['id'] if first else None
        self.view="today"; self.status_filter="all"; self.list_mode="list"
        self.expanded_tasks=set(); self.collapsed_tasks=set(); self.outline_filter=None
        self.task_drag_active=False; self.task_order_saved=False
        self.open_sections=set(); self.worklog_dialog=None; self.evaluation_dialog=None
        self.mini=None; self.linear_dialog=None; self.binding_dialog=None; self.nav_buttons={}; self.note_task_id=None
        self.station_dialog=None; self.learning_dialogs={}; self.exiting=False
        self.seen_completion=self.focus.snapshot()["completion_serial"] if self.focus else 0; self.shown_focus_error=""
        self.setWindowTitle(APP_DISPLAY_NAME+(" · 学习与专注" if PROFILE.key=="study" else " · 工作与进展")); self.setWindowIcon(icon("orbit",PALETTE["primary"],64)); self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.resize(1440,960); self.setMinimumSize(960,600); self.compact_layout=None
        self.note_debounce=QTimer(self); self.note_debounce.setSingleShot(True); self.note_debounce.setInterval(500); self.note_debounce.timeout.connect(self.save_note)
        shell=QFrame(); shell.setObjectName("shell"); self.setCentralWidget(shell); outer=box(shell,spacing=0); outer.addWidget(TitleBar(self))
        body=box(horizontal=True,spacing=0); self.body_layout=body; outer.addLayout(body,1)
        self.build_sidebar(body); self.build_main(body); self.build_details(body)
        self.display.changed.connect(self.adapt_layout)
        if display is not None: self.display.fit_window(self,restore=True)
        self.adapt_layout()
        self.tick_timer=QTimer(self); self.tick_timer.setInterval(250); self.tick_timer.timeout.connect(self.tick)
        self.resize_grip=QSizeGrip(shell); self.resize_grip.setFixedSize(16,16); self.resize_grip.move(self.width()-17,self.height()-17); self.resize_grip.raise_()
        QShortcut(QKeySequence("Ctrl+N"),self,activated=self.new_task)
        QShortcut(QKeySequence("Ctrl+K"),self,activated=self.search.setFocus)
        if self.focus: QShortcut(QKeySequence("Ctrl+Shift+F"),self,activated=self.toggle_focus)
        self.refresh()
        # This timer refreshes the view only. FocusService owns the actual clock.
        self.tick_timer.start()
        self.tray=QSystemTrayIcon(self.windowIcon(),self)
        self.tray.setToolTip(APP_DISPLAY_NAME+(" · 学习与专注" if PROFILE.key=="study" else " · 工作与进展"))
        menu=QMenu(self)
        menu.addAction("打开"+APP_NAME,self.restore_window)
        if self.runtime.graph and self.runtime.graph.url:
            menu.addAction("查看运行图谱",lambda:QDesktopServices.openUrl(QUrl(self.runtime.graph.url)))
        if self.runtime.profile.work:
            menu.addAction("需要我处理",self.open_attention)
            menu.addAction("日报与同步",self.open_daily)
            menu.addAction("日志评估",self.open_evaluation)
        menu.addAction('应用更新',self.open_updates)
        menu.addSeparator()
        menu.addAction("停止执行并退出",self.exit_application)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason:self.restore_window() if reason==QSystemTrayIcon.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable(): self.tray.show()

    def write(self,operation):
        try: operation(); return True
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,"暂时没有保存成功",str(exc)); return False

    def build_sidebar(self,body):
        self.sidebar_scroll=QFrame(); self.sidebar_scroll.setObjectName("sidebar"); self.sidebar_scroll.setFixedWidth(202); body.addWidget(self.sidebar_scroll)
        side_layout=box(self.sidebar_scroll,spacing=0)
        side=QWidget(); self.sidebar=side
        navigation=QScrollArea(); navigation.setWidgetResizable(True); navigation.setWidget(side); navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); side_layout.addWidget(navigation,1)
        col=box(side,margins=(17,23,17,18),spacing=7)
        brand=box(horizontal=True,spacing=8); brand.addWidget(OrbitMark(compact=True)); names=box(spacing=1); names.addWidget(label(APP_NAME,"brand")); names.addWidget(label(APP_WORDMARK,"eyebrow")); brand.addLayout(names); brand.addStretch(); col.addLayout(brand); col.addSpacing(12)
        self.add_nav(col,"today","今天","sun"); self.add_nav(col,"all","全部任务","tasks"); self.add_nav(col,"linked","关联空间","link") if self.runtime.profile.key=="combined" else None
        col.addSpacing(17); col.addWidget(label("任务方向","section")); col.addSpacing(3)
        for key,title in CATEGORIES.items(): self.add_nav(col,key,title,ICONS[key])
        col.addSpacing(14); self.add_nav(col,"done","已完成","check")
        if self.runtime.profile.work:
            col.addSpacing(14); col.addWidget(label("进展与记录","section"))
            col.addWidget(button("需要我处理","nav",callback=self.open_attention))
            col.addWidget(button("日报与同步","nav",glyph="book",callback=self.open_daily))
            self.end_day_button=button('下班','nav',callback=lambda:self.open_worklog('today'))
            self.end_day_button.setAccessibleName('下班')
            self.end_day_button.setToolTip('后台总结今天 00:00 至当前的工作记录；仅本地保存，应用继续运行')
            col.addWidget(self.end_day_button)
            self.weeklog_button=button('总结周报','nav',callback=lambda:self.open_worklog('week'))
            self.weeklog_button.setToolTip('统计本周一 00:00 至当前；仅本地保存')
            col.addWidget(self.weeklog_button)
            col.addWidget(button('日志评估','nav',callback=self.open_evaluation))
        col.addStretch()
        quiet=QFrame(); quiet.setObjectName("quiet"); quiet.setMinimumHeight(54); q=box(quiet,margins=(13,12,13,12),spacing=8)
        self.sidebar_done=label("","muted",True); q.addWidget(self.sidebar_done)
        self.day_progress=QProgressBar(); self.day_progress.setTextVisible(False); q.addWidget(self.day_progress); col.addWidget(quiet); quiet.setVisible(self.runtime.profile.key!="work")
        footer_widget=QWidget(); side_layout.addWidget(footer_widget)
        col=box(footer_widget,margins=(17,8,17,12),spacing=7)
        col.addWidget(button("偏好设置","nav",glyph="settings",callback=self.settings))
        if self.runtime.profile.key=="work":
            more=button("更多","nav"); more.setAccessibleName("更多应用操作"); menu=QMenu(more)
            menu.addAction("已关联的任务",lambda:self.navigate('linked'))
            menu.addAction("应用更新 · v"+APP_VERSION,self.open_updates)
            menu.addSeparator(); menu.addAction("退出星序",self.exit_application)
            more.setMenu(menu); col.addWidget(more)
        else: col.addWidget(button("停止执行并退出","linkButton",callback=self.exit_application))
        foot=box(horizontal=True); foot.addWidget(label("●  本地保存","eyebrow")); foot.addStretch(); foot.addWidget(button('v'+APP_VERSION,'linkButton',callback=self.open_updates)); col.addLayout(foot)
        if self.runtime.profile.key=="work":
            for i in range(foot.count()):
                if foot.itemAt(i).widget(): foot.itemAt(i).widget().hide()

    def add_nav(self,layout,key,text,glyph):
        b=button(text,"nav",glyph=glyph,callback=lambda checked=False,k=key:self.navigate(k)); b.setCheckable(True); layout.addWidget(b); self.nav_buttons[key]=b

    def build_main(self,body):
        main=QWidget(); self.main_panel=main; body.addWidget(main,1); col=box(main,margins=(29,23,29,18),spacing=17); self.main_layout=col
        crumb=box(horizontal=True); self.breadcrumb=label("我的空间  /  今天","muted"); crumb.addWidget(self.breadcrumb)
        self.details_toggle=button("任务详情","outline",callback=self.open_details); self.details_toggle.setToolTip("在独立面板查看和编辑当前任务"); crumb.addWidget(self.details_toggle)
        crumb.addStretch()
        self.search=QLineEdit(); self.search.setObjectName("search"); self.search.setPlaceholderText("搜索任务    Ctrl K"); self.search.setFixedWidth(174); self.search.addAction(icon("search","#5f7488",16),QLineEdit.LeadingPosition); self.search.textChanged.connect(self.refresh_list); crumb.addWidget(self.search); col.addLayout(crumb); self.crumb_layout=crumb
        heading=box(horizontal=True); self.heading_layout=heading; words=box(spacing=6); self.heading=label("把今天，连成方向。","h1",True); words.addWidget(self.heading)
        self.subtitle=label("聚焦眼前一步，让想法沿着清晰的路径前进。","muted",True); words.addWidget(self.subtitle); heading.addLayout(words); heading.addStretch()
        self.task_actions=QWidget(); actions=box(self.task_actions,horizontal=True,spacing=8)
        self.my_issues_button=None
        if self.runtime.profile.work:
            self.my_issues_button=button('拉取 My Issues','outline',glyph='linear',callback=self.pull_my_issues)
            self.my_issues_button.setToolTip('从 Linear 拉取指派给我、且本地尚未创建任务的 issue；已有任务自动跳过')
            actions.addWidget(self.my_issues_button)
        self.new_task_button=button('新建任务','coral','plus',color='#514738',callback=self.new_task)
        actions.addWidget(self.new_task_button); heading.addWidget(self.task_actions); col.addLayout(heading)
        self.task_actions_bar=QWidget(); self.task_actions_layout=box(self.task_actions_bar,horizontal=True)
        self.task_actions_layout.addStretch(); col.addWidget(self.task_actions_bar); self.task_actions_bar.hide()
        if self.runtime.profile.key=="work":
            crumb.removeWidget(self.search); heading.insertWidget(2,self.search)
            self.search.setMinimumWidth(100); self.search.setMaximumWidth(220)
            self.search.setSizePolicy(self.search.sizePolicy().Expanding,self.search.sizePolicy().Fixed)
            for widget in (self.breadcrumb,self.subtitle): widget.hide()
            # Compact mode keeps this button available next to the search field.
            crumb.removeWidget(self.details_toggle); heading.insertWidget(3,self.details_toggle)
            crumb.setContentsMargins(0,0,0,0)

        self.focus_card=QFrame(); self.focus_card.setObjectName("focus"); focus=box(self.focus_card,True,(19,14,19,14),17)
        text=box(spacing=9); self.focus_status=label("●  留一段专注时间","eyebrow"); text.addWidget(self.focus_status)
        self.focus_title=label("","h2",True); text.addWidget(self.focus_title)
        self.focus_step=label("","muted",True); text.addWidget(self.focus_step); text.addStretch()
        self.focus_caption=QWidget(); minirow=box(self.focus_caption,horizontal=True); minirow.addWidget(label("一次，向前一步","eyebrow")); minirow.addStretch(); minirow.addWidget(button("小窗","linkButton",glyph="external",callback=self.show_mini)); text.addWidget(self.focus_caption); focus.addLayout(text,1)
        self.mascot=OrbitMark(); focus.addWidget(self.mascot)
        divider=QFrame(); self.focus_divider=divider; divider.setObjectName("focusDivider"); divider.setFixedWidth(1); divider.setFixedHeight(108); focus.addWidget(divider)
        timer=box(spacing=5); self.focus_timer_layout=timer; timer.setAlignment(Qt.AlignCenter); self.timer_label=label("25:00","timer"); self.timer_label.setAlignment(Qt.AlignCenter); timer.addWidget(self.timer_label)
        buttons=box(horizontal=True,spacing=4); self.focus_button=button("开始专注","primary","play",color="#ffffff",callback=self.toggle_focus); buttons.addWidget(self.focus_button)
        reset=button(name="iconButton",glyph="reset",callback=self.reset_focus); reset.setToolTip("重置当前计时"); reset.setAccessibleName("重置当前计时"); buttons.addWidget(reset); timer.addLayout(buttons); focus.addLayout(timer)
        col.addWidget(self.focus_card)
        self.focus_card.setVisible(bool(self.focus))
        listhead=box(horizontal=True,spacing=7); self.list_title=label("我的任务","h2"); listhead.addWidget(self.list_title); self.list_count=label("5","badge"); listhead.addWidget(self.list_count); listhead.addStretch()
        self.undo_structure_button=button('撤销结构调整','linkButton',callback=self.undo_task_structure); listhead.addWidget(self.undo_structure_button)
        self.list_btn=button(name="iconButton",glyph="list",callback=lambda:self.change_mode("list")); self.list_btn.setToolTip("列表视图"); self.list_btn.setAccessibleName("列表视图")
        self.board_btn=button(name="iconButton",glyph="grid",callback=lambda:self.change_mode("board")); self.board_btn.setToolTip("看板视图"); self.board_btn.setAccessibleName("看板视图"); listhead.addWidget(self.list_btn); listhead.addWidget(self.board_btn); col.addLayout(listhead)
        filters=box(horizontal=True,spacing=3); self.filter_buttons={}
        for key,title in [("all","全部"),("todo","待开始"),("doing","进行中"),("waiting","等我处理")]:
            b=button(title,"filter",callback=lambda checked=False,k=key:self.set_filter(k)); b.setCheckable(True); filters.addWidget(b); self.filter_buttons[key]=b
        filters.addStretch(); self.date_label=label("","eyebrow"); filters.addWidget(self.date_label); col.addLayout(filters)
        if self.runtime.profile.key=="work":
            self.list_title.hide(); self.date_label.hide()
            for index,control in enumerate(self.filter_buttons.values()):
                filters.removeWidget(control); listhead.insertWidget(index,control)
            self.list_count.hide()
            self.list_btn.hide(); self.board_btn.hide(); self.undo_structure_button.hide()
            menu_button=button("视图与操作","soft"); menu_button.setAccessibleName("任务视图与操作")
            menu=QMenu(menu_button); menu.aboutToShow.connect(self.sync_task_menu)
            self.view_actions={}
            for mode,title in [('list','列表视图'),('board','看板视图')]:
                action=menu.addAction(title,lambda checked=False,m=mode:self.change_mode(m)); action.setCheckable(True); self.view_actions[mode]=action
            menu.addSeparator(); self.undo_action=menu.addAction('撤销结构调整',self.undo_task_structure)
            menu_button.setMenu(menu); listhead.addWidget(menu_button)

        self.outline_hint=label('', 'muted', True); col.addWidget(self.outline_hint)
        self.independent_drop=IndependentTaskDropArea(self); col.addWidget(self.independent_drop)
        self.scroll=QScrollArea(); self.scroll.setWidgetResizable(True); self.list_host=TaskDropArea(self); self.task_list=box(self.list_host,spacing=9); self.task_list.setAlignment(Qt.AlignTop); self.scroll.setWidget(self.list_host); col.addWidget(self.scroll,1)
        footer=box(horizontal=True); self.complete_summary=label("","muted"); footer.addWidget(self.complete_summary); footer.addStretch(); self.footer_shortcuts=label("Ctrl N  新建  ·  Ctrl Shift F  专注" if self.focus else "Ctrl N  新建  ·  Ctrl K  搜索","eyebrow"); footer.addWidget(self.footer_shortcuts); col.addLayout(footer)
        if self.runtime.profile.key=="work":
            self.complete_summary.hide(); self.footer_shortcuts.hide()

    def sync_task_menu(self):
        for mode,action in self.view_actions.items(): action.setChecked(mode==self.list_mode)
        undo=self.tasks.undo_state(); self.undo_action.setEnabled(undo['available'])
        self.undo_action.setText('撤销：'+undo['label'] if undo['available'] else '撤销结构调整')

    def add_detail_section(self,title,content,key):
        if self.runtime.profile.key=="work":
            self.detail_content.addWidget(DetailSection(self,(self.selected,key),title,content))
        else: self.detail_content.addWidget(content)

    def build_details(self,body):
        self.details=QFrame(); self.details.setObjectName("details"); self.details.setFixedWidth(334); body.addWidget(self.details)
        self.details_dialog=QDialog(self); self.details_dialog.setWindowTitle("当前任务"); self.details_dialog.setWindowModality(Qt.NonModal); box(self.details_dialog)
        self.detail_outer=box(self.details,margins=(22,22,22,18),spacing=15)
        head=box(horizontal=True); head.addWidget(label("当前任务","section")); head.addStretch()
        self.edit_button=button("编辑","linkButton",callback=self.edit_task); head.addWidget(self.edit_button)
        self.delete_button=button("删除","deleteButton",callback=self.delete_task); self.delete_button.setAccessibleName("删除当前任务"); head.addWidget(self.delete_button); self.detail_outer.addLayout(head)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); content=QWidget(); self.detail_content=box(content,spacing=13); self.detail_content.setAlignment(Qt.AlignTop); scroll.setWidget(content); self.detail_outer.addWidget(scroll,1)
        self.detail_focus=button("专注这件事","outline","orbit",callback=self.focus_selected); self.detail_outer.addWidget(self.detail_focus); self.detail_focus.setVisible(bool(self.focus))

    def navigate(self,view):
        if not self.flush_editors(): return
        self.view=view; self.status_filter="all"; self.refresh()

    def visible_tasks(self,apply_status=True):
        tasks=self.tasks.tasks
        if self.view=="today": tasks=[t for t in tasks if t["day"]<=date.today().isoformat() and t["status"]!="done"]
        elif self.view=="done": tasks=[t for t in tasks if t["status"]=="done"]
        elif self.view=="linked": tasks=[t for t in tasks if t["bindings"] and t["status"]!="done"]
        elif self.view in CATEGORIES: tasks=[t for t in tasks if t["category"]==self.view and t["status"]!="done"]
        if apply_status and self.status_filter!="all": tasks=[t for t in tasks if t["status"]==self.status_filter]
        query=self.search.text().strip().lower()
        if query: tasks=[t for t in tasks if query in (t["title"]+t["description"]+" ".join(b["label"] for b in t["bindings"])).lower()]
        return tasks

    def refresh(self):
        if self.task_drag_active: return
        self.seen_revision=self.tasks.revision()["revision"]
        self.learning_state=self.runtime.learning.snapshot() if self.runtime.learning else {"revision":0,"bindings":{},"jobs":[]}
        self.seen_learning_revision=self.learning_state["revision"]
        if self.my_issues_button:
            self.my_issues_button.setVisible(self.runtime.profile.key=='work' or self.view=='work')
        undo=self.tasks.undo_state(); self.undo_structure_button.setEnabled(undo['available'])
        self.undo_structure_button.setToolTip(('撤销：'+undo['label']) if undo['available'] else undo['reason'])
        today=[t for t in self.tasks.tasks if t["day"]==date.today().isoformat()]; done=sum(t["status"]=="done" for t in today)
        self.sidebar_done.setText(f"今天完成 {done} 项任务"); self.day_progress.setValue(round(done/len(today)*100) if today else 0)
        self.complete_summary.setText(f"已完成 {done} 项，留下今天的进展。" if done else "从眼前的一步开始。")
        titles={"today":"把今天，连成方向。","all":"让每件事，各就其位。","linked":"把上下文，接起来。","done":"每一步，都有回响。",**{k:v for k,v in CATEGORIES.items()}}
        self.heading.setText(({"today":"今天","all":"全部任务","linked":"已关联","done":"已完成",**CATEGORIES}[self.view]) if self.runtime.profile.key=="work" else titles[self.view]); self.breadcrumb.setText("我的空间  /  "+({"today":"今天","all":"全部任务","linked":"关联空间","done":"已完成",**CATEGORIES}[self.view]))
        for key,b in self.nav_buttons.items():
            b.setChecked(key==self.view)
            if key in CATEGORIES: count=sum(t["category"]==key and t["status"]!="done" for t in self.tasks.tasks)
            elif key=="today": count=sum(t["day"]<=date.today().isoformat() and t["status"]!="done" for t in self.tasks.tasks)
            elif key=="done": count=sum(t["status"]=="done" for t in self.tasks.tasks)
            elif key=="linked": count=sum(bool(t["bindings"]) and t["status"]!="done" for t in self.tasks.tasks)
            else: count=len(self.tasks.tasks)
            title={"today":"今天","all":"全部任务","linked":"关联空间","done":"已完成",**CATEGORIES}[key]
            b.setText(f"{title}     {count}")
        self.date_label.setText(QDate.currentDate().toString("MM / dd")+"  ·  "+["周一","周二","周三","周四","周五","周六","周日"][date.today().weekday()])
        self.refresh_list(); self.refresh_detail(); self.refresh_timer()

    def refresh_list(self):
        if self.task_drag_active: return
        self.learning_state=self.runtime.learning.snapshot() if self.runtime.learning else {"revision":0,"bindings":{},"jobs":[]}
        clear(self.task_list)
        for key,b in self.filter_buttons.items(): b.setChecked(key==self.status_filter)
        tasks=self.visible_tasks()
        self.independent_drop.setVisible(self.runtime.profile.key!="work" and any(task.get('parent_id') for task in tasks))
        self.task_outline=TaskOutline(self.tasks.tasks)
        filter_key=(self.view,self.status_filter,self.search.text().strip().lower())
        if filter_key!=self.outline_filter:
            self.collapsed_tasks.clear(); self.outline_filter=filter_key
            self.task_page_limit=80
        reveal=bool(filter_key[2]) or self.status_filter!='all' or self.view in ('done','linked')
        entries=self.task_outline.rows(tasks,self.expanded_tasks,self.collapsed_tasks,reveal)
        self.outline_entries={entry.task['id']:entry for entry in entries}
        roots=sum(not task.get('parent_id') for task in tasks); steps=len(tasks)-roots
        self.list_count.setText(f'{roots} 项' + (f' · {steps} 步' if steps else ''))
        self.list_count.setToolTip('当前筛选中的独立任务与子步骤数量；子步骤收起后仍计入。')
        hidden=len(tasks)-sum(not entry.context for entry in entries)
        self.outline_hint.setText(f'已收起 {hidden} 个子步骤 · 点父任务上的「展开」查看。' if hidden else ('保留父级路径，方便定位筛选中的子步骤。' if any(entry.context for entry in entries) else '子步骤收在父任务内；展开后显示层级与所属任务。'))
        selected_index=next((i for i,entry in enumerate(entries) if entry.task['id']==self.selected),-1)
        self.task_page_limit=max(getattr(self,'task_page_limit',80),((selected_index//80)+1)*80)
        remaining=max(0,len(entries)-self.task_page_limit); entries=entries[:self.task_page_limit]
        self.outline_hint.setVisible(self.runtime.profile.key!="work" or bool(remaining))
        if remaining:self.outline_hint.setText(self.outline_hint.text()+f' · 另有 {remaining} 项，可继续显示或搜索定位。')
        self.list_btn.setStyleSheet("background:#e7eff5;" if self.list_mode=="list" else ""); self.board_btn.setStyleSheet("background:#e7eff5;" if self.list_mode=="board" else "")
        if not tasks:
            self.task_list.addSpacing(45); empty=label("这里安安静静的。\n新建一件小事，或换一个筛选看看。","muted",True); empty.setAlignment(Qt.AlignCenter); self.task_list.addWidget(empty); return
        if self.list_mode=="list":
            for entry in entries:
                task=entry.task
                add_outline_card(self.task_list,TaskRow(task,task['id']==self.selected,self,entry),entry)
        else:
            board=box(horizontal=not self.compact_layout,spacing=10)
            for status,name in STATUSES.items():
                group=[entry for entry in entries if entry.task['status']==status]
                if status=="done" and not group: continue
                column=TaskDropArea(self,status); stack=box(column,spacing=10); stack.setAlignment(Qt.AlignTop)
                stack.addWidget(label(name+"  ·  "+str(sum(not entry.context for entry in group)),"section"))
                for entry in group:
                    task=entry.task
                    add_outline_card(stack,BoardCard(task,task['id']==self.selected,self,entry),entry,compact=True)
                board.addWidget(column,1)
            self.task_list.addLayout(board)
        if remaining:
            more=button(f'继续显示 · 还有 {remaining} 项','outline',callback=self.show_more_tasks)
            more.setObjectName('showMoreTasks'); self.task_list.addWidget(more)

    def show_more_tasks(self):
        position=self.scroll.verticalScrollBar().value(); self.task_page_limit+=80; self.refresh_list()
        QTimer.singleShot(0,lambda:self.scroll.verticalScrollBar().setValue(position))

    def course_action(self,task_id):
        active=next((j for j in reversed(self.learning_state['jobs']) if j['input']['task_id']==task_id and j['status'] in ('queued','running')),None)
        if active: return ('课程生成中' if active['status']=='running' else '课程等待生成','查看进度',False)
        if self.learning_state['bindings'].get(task_id): return ('已绑定教程','开始学习',False)
        latest=next((j for j in reversed(self.learning_state['jobs']) if j['input']['task_id']==task_id),None)
        status='建课待补充信息' if latest and latest['status']=='needs_input' else '尚未绑定教程'
        return (status,'创建课程',True)

    def open_learning(self,task_id=None,tab=0):
        from learning_ui import LearningDialog
        task_id=task_id or self.selected
        if not self.flush_editors(): return
        task=self.tasks.get(task_id)
        if not task or task['category']!='learn': return
        dialog=self.learning_dialogs.get(task_id)
        if dialog is None:
            dialog=LearningDialog(self,task_id,tab)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            self.learning_dialogs[task_id]=dialog
            dialog.finished.connect(lambda code,tid=task_id:self.learning_dialogs.pop(tid,None))
        else:
            dialog.tabs.setCurrentIndex(tab)
        dialog.show(); dialog.raise_(); dialog.activateWindow()
        return dialog

    def activate_learning(self,task_id,create=False):
        dialog=self.open_learning(task_id,1 if create else 0)
        if dialog and not create and self.course_action(task_id)[1]=='开始学习':
            dialog.refresh(); dialog.read()

    def set_filter(self,value): self.status_filter=value; self.refresh_list()
    def change_mode(self,value): self.list_mode=value; self.refresh_list()

    def toggle_task_children(self,task_id):
        entry=self.outline_entries.get(task_id)
        if not entry: return
        if entry.expanded:
            self.expanded_tasks.discard(task_id); self.collapsed_tasks.add(task_id)
        else:
            self.collapsed_tasks.discard(task_id); self.expanded_tasks.add(task_id)
        self.refresh_list()

    def reveal_task(self,task_id):
        for ancestor in TaskOutline(self.tasks.tasks).ancestors(task_id):
            self.expanded_tasks.add(ancestor['id']); self.collapsed_tasks.discard(ancestor['id'])

    def reorder_task(self,task_id,anchor_id,after,scope):
        if task_id not in scope or anchor_id not in scope or task_id==anchor_id: return False
        task=self.tasks.get(task_id); anchor=self.tasks.get(anchor_id)
        if not task or not anchor or task.get('parent_id')!=anchor.get('parent_id'): return False
        # Insertion changes sibling order only. Cross-level connections use the
        # card-centre drop and its explicit confirmation dialog.
        scope=[tid for tid in scope if self.tasks.get(tid).get('parent_id')==task.get('parent_id')]
        if not self.flush_editors(): return False
        order=[task for task in scope if task!=task_id]
        order.insert(order.index(anchor_id)+(1 if after else 0),task_id)
        if not self.write(lambda:self.tasks.reorder(order)): return False
        self.task_order_saved=True
        if not self.task_drag_active: self.refresh()
        return True

    def detach_task(self,task_id):
        if not self.flush_editors(): return False
        try: task=self.tasks.check_detach(task_id)
        except (ValueError,OSError) as exc:
            from task_dependencies_ui import related_tasks
            before,after=related_tasks(self.tasks.tasks,task_id)
            if before or after:
                from task_dependencies_ui import DependencyConflictDialog
                message=DependencyConflictDialog(self,str(exc),len(before),len(after))
                chosen=message.exec_()==QDialog.Accepted; message.deleteLater()
                if chosen: self.manage_dependencies(task_id)
            else: QMessageBox.warning(self,'无法移出',str(exc))
            return False
        message='将「'+task['title']+'」移出父任务，成为独立任务？\n笔记、聊天、课程和已有子步骤都会保留。'
        from task_structure import impact, impact_text
        message+='\n\n'+impact_text(impact(self.tasks.tasks,task_id))
        if task['category']=='work': message+='\nLinear issue 父级将在 22:00 同步。'
        if QMessageBox.question(self,'移为独立任务',message,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return False
        if not self.write(lambda:self.tasks.detach(task_id)): return False
        self.selected=task_id; self.task_order_saved=True
        if not self.task_drag_active: self.refresh()
        return True

    def undo_task_structure(self):
        if not self.flush_editors(): return
        state=self.tasks.undo_state()
        if not state['available']: QMessageBox.information(self,'无法撤销',state['reason']); return
        text='撤销「'+state['label']+'」？\n仅恢复任务层级、依赖和顺序，保留当前笔记、状态、聊天与课程记录。\n工作任务的父级变化将在下次 22:00 同步。'
        if QMessageBox.question(self,'撤销最近的结构调整',text,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
        if self.write(lambda:self.tasks.undo_structure(state['id'])): self.refresh()

    def merge_task(self,task_id,target_id):
        if not self.flush_editors(): return False
        task=self.tasks.get(task_id); target=self.tasks.get(target_id)
        if not task or not target: return False
        dialog=QDialog(self); dialog.setWindowTitle("连接任务步骤"); dialog.setMinimumWidth(480)
        layout=box(dialog,margins=(24,24,24,24),spacing=14)
        layout.addWidget(label("如何连接这两件事？","h2"))
        layout.addWidget(label("将「"+task['title']+"」连接到「"+target['title']+"」。","muted",True))
        layout.addWidget(label("原任务的笔记、聊天与已有子树都会保留。","muted",True))
        dialog.merge_mode=None
        def choose(mode): dialog.merge_mode=mode; dialog.accept()
        choices=[]
        for mode,title,description in [("child","作为子步骤合入","成为目标任务的一部分，合入后收起在父卡片内；不代表先后顺序。"),("after","接在此任务之后","保留同层独立卡片，并标注目标为前置任务；仅用于规划，不限制手动执行。")]:
            action=button(title,"outline",callback=lambda checked=False,m=mode:choose(m)); action.setObjectName("merge_"+mode)
            explanation=label(description,'muted',True); choices.append((mode,action,explanation,description))
            layout.addWidget(action); layout.addWidget(explanation)
        def update_choices():
            for mode,action,explanation,description in choices:
                try:
                    result=self.tasks.check_merge(task_id,target_id,mode); error=''
                    from task_structure import impact_text
                    description+='\n'+impact_text(result['impact'])
                except (ValueError,OSError) as exc: error=str(exc)
                action.setEnabled(not error); explanation.setText(error or description)
        def manage(): self.manage_dependencies(task_id,dialog); update_choices()
        update_choices(); layout.addWidget(button('管理相关依赖','linkButton',callback=manage))
        if task['category']=="work": layout.addWidget(label("本地立即调整；Linear issue 父级在 22:00 同步。","muted",True))
        layout.addWidget(button("取消",callback=dialog.reject))
        accepted=dialog.exec_()==QDialog.Accepted
        mode=dialog.merge_mode; dialog.deleteLater()
        if not accepted or not mode: return False
        return self.apply_task_connection(task_id,target_id,mode)

    def move_task(self,task_id):
        from task_move_ui import TaskMoveDialog
        if not self.flush_editors() or not self.tasks.get(task_id): return False
        dialog=TaskMoveDialog(self,task_id)
        accepted=dialog.exec_()==QDialog.Accepted
        target_id,mode=dialog.target_id,dialog.mode; dialog.deleteLater()
        if not accepted or not target_id or not mode: return False
        if not self.apply_task_connection(task_id,target_id,mode): return False
        self.select_task(target_id if mode=='child' else task_id)
        return True

    def apply_task_connection(self,task_id,target_id,mode):
        if not self.flush_editors(): return False
        if not self.write(lambda:self.tasks.merge(task_id,target_id,mode)): return False
        if mode=='child':
            self.selected=target_id; self.reveal_task(target_id)
            self.expanded_tasks.discard(target_id); self.collapsed_tasks.add(target_id)
        self.task_order_saved=True
        if not self.task_drag_active: self.refresh()
        return True

    def manage_dependencies(self,task_id,parent=None):
        from task_dependencies_ui import DependencyDialog
        if not self.flush_editors() or not self.tasks.get(task_id): return
        dialog=DependencyDialog(parent or self,self,task_id); dialog.exec_(); dialog.deleteLater()

    def edit_dependencies(self,task_id,parent=None):
        task=self.tasks.get(task_id)
        if not task or not self.flush_editors(): return
        dialog=QDialog(parent or self); dialog.setWindowTitle("调整前置步骤"); dialog.setMinimumWidth(420)
        layout=box(dialog,margins=(24,24,24,24),spacing=12)
        layout.addWidget(label("哪些步骤需要先完成？","h2")); checks=[]
        layout.addWidget(label('正在调整：'+task['title'],'muted',True))
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setMinimumHeight(180); scroll.setMaximumHeight(430)
        host=QWidget(); choices=box(host,spacing=10); choices.setAlignment(Qt.AlignTop); scroll.setWidget(host); layout.addWidget(scroll)
        for sibling in self.tasks.tasks:
            if sibling['id']==task_id or sibling.get('parent_id')!=task.get('parent_id') or sibling['category']!=task['category']: continue
            cb=QCheckBox(sibling['title']); cb.setChecked(sibling['id'] in task.get('depends_on',[])); checks.append((sibling['id'],cb)); choices.addWidget(cb)
        if not checks: choices.addWidget(label('没有可选的同层任务。可先创建并行方向，再连接先后关系。','muted',True))
        error=label("","muted",True); layout.addWidget(error)
        def save():
            try: self.tasks.dependencies(task_id,[tid for tid,cb in checks if cb.isChecked()])
            except (ValueError,OSError) as exc: error.setText(str(exc)); return
            dialog.accept()
        row=box(horizontal=True); row.addWidget(button("取消",callback=dialog.reject)); row.addWidget(button("保存前置步骤","primary",callback=save)); layout.addLayout(row)
        if dialog.exec_()==QDialog.Accepted: self.refresh()
        dialog.deleteLater()

    def task_route(self,task):
        siblings=[t for t in self.tasks.tasks if t.get('parent_id')==task.get('parent_id') and t['category']==task['category']]
        connected={task['id']}
        while True:
            expanded=connected | {t['id'] for t in siblings if connected.intersection(t.get('depends_on',[]))}
            expanded |= {dep for t in siblings if t['id'] in connected for dep in t.get('depends_on',[])}
            if expanded==connected: break
            connected=expanded
        return [t for t in siblings if t['id'] in connected] if len(connected)>1 else []

    def show_task_route(self,task_id):
        task=self.tasks.get(task_id)
        if not task: return
        dialog=QDialog(self); dialog.setWindowTitle('任务路线'); dialog.resize(850,650)
        layout=box(dialog,margins=(20,20,20,20),spacing=12); layout.addWidget(label('从上到下，连接串行与并行步骤','h2'))
        dag=TaskDag(); dag.set_tasks(self.task_route(task)); dag.selected.connect(dag.choose)
        def open_task(tid): dialog.accept(); self.select_task(tid)
        dag.activated.connect(open_task); layout.addWidget(dag,1)
        layout.addWidget(label('箭头表示显式前置关系；规划顺序不限制手动执行。','muted',True))
        layout.addWidget(button('关闭',callback=dialog.accept)); dialog.exec_(); dialog.deleteLater()

    def finish_task_drag(self):
        # Native QDrag must release its source before rebuilding card widgets.
        if self.task_order_saved: self.refresh()
        else: self.refresh_list()
        self.task_order_saved=False

    def select_task(self,task_id):
        if not self.flush_editors(): return
        if not self.tasks.get(task_id): return
        matches={task['id'] for task in self.visible_tasks()}
        context_ids=set(getattr(self,'outline_entries',{}))
        outside=task_id not in matches and task_id not in context_ids
        if outside:
            self.search.blockSignals(True); self.search.clear(); self.search.blockSignals(False)
            self.view='all'; self.status_filter='all'
        self.selected=task_id; self.reveal_task(task_id)
        if outside: self.refresh()
        else: self.refresh_list(); self.refresh_detail()
        QTimer.singleShot(0,self.scroll_to_selected_task)

    def scroll_to_selected_task(self):
        for card in self.list_host.findChildren(DraggableTaskCard):
            if card.task_id==self.selected and card.isVisible():
                self.scroll.ensureWidgetVisible(card,0,12); break

    def refresh_detail(self):
        clear(self.detail_content)
        task=self.tasks.get(self.selected); self.note_task_id=None
        self.edit_button.setEnabled(bool(task)); self.delete_button.setEnabled(bool(task))
        self.detail_focus.setEnabled(bool(task) and task["status"]!="done" if task else False)
        if not task:
            self.detail_content.addWidget(label("点一件小事，\n把注意力留给它。","muted",True)); return
        badges=box(horizontal=True,spacing=8); category=label(CATEGORIES[task["category"]],"badge"); category.setStyleSheet("color: "+COLORS[task["category"]]+"; background:#edf2f7;"); badges.addWidget(category); badges.addStretch()
        status=QComboBox()
        for key,value in STATUSES.items(): status.addItem(value,key)
        status.setCurrentIndex(list(STATUSES).index(task["status"])); status.setFixedWidth(109); status.currentIndexChanged.connect(lambda:self.set_status(task["id"],status.currentData())); badges.addWidget(status); self.detail_content.addLayout(badges)
        self.detail_content.addWidget(label(task["title"],"detailTitle",True))
        location=QFrame(); location.setObjectName('binding'); location_box=box(location,margins=(12,10,12,10),spacing=7)
        location_box.addWidget(label('任务位置','section'))
        ancestors=TaskOutline(self.tasks.tasks).ancestors(task['id'])
        location_box.addWidget(label(' / '.join(a['title'] for a in ancestors) if ancestors else '独立任务','muted',True))
        if ancestors:
            parent_id=ancestors[-1]['id']
            location_box.addWidget(button('↖ 打开父任务','linkButton',callback=lambda checked=False,tid=parent_id:self.select_task(tid)))
        location_box.addWidget(button('移动到其他任务','outline',callback=lambda checked=False:self.move_task(task['id'])))
        if ancestors:
            location_box.addWidget(button('移为独立任务','linkButton',callback=lambda checked=False:self.detach_task(task['id'])))
        advanced=QFrame(); advanced_layout=box(advanced,spacing=12)
        settings_layout=advanced_layout if self.runtime.profile.key=="work" else self.detail_content
        settings_layout.addWidget(location)
        if task["description"]: self.detail_content.addWidget(label(task["description"],"muted",True))
        if task['category']=='learn':
            course=QFrame(); course.setObjectName('binding'); content=box(course,margins=(12,12,12,12),spacing=10)
            text,action,accent=self.course_action(task['id'])
            content.addWidget(label('学习教程 · '+text,'section',True))
            content.addWidget(button(action,'coral' if accent else 'primary',callback=lambda checked=False:self.activate_learning(task['id'],accent)))
            content.addWidget(button('绑定已有课程','outline',callback=lambda checked=False:self.open_learning(task['id'],2)))
            self.detail_content.addWidget(course)
        self.detail_content.addWidget(line())
        props=box(horizontal=True); props.addWidget(label("计划日期","muted")); props.addStretch(); props.addWidget(label("今天" if task["day"]==date.today().isoformat() else task["day"],"muted")); self.detail_content.addLayout(props)
        if self.runtime.profile.key=="work": self.build_task_links(task)
        from progress_evidence_ui import ProgressPanel
        settings_layout.addWidget(ProgressPanel(self, task))
        from task_dependencies_ui import DependencyPanel
        settings_layout.addWidget(DependencyPanel(self,task,self.tasks.tasks))
        if not task.get('parent_id'):
            route=self.task_route(task)
            if route:
                settings_layout.addWidget(label('所在路线 · 串行与并行','section'))
                graph=TaskDag(compact=True); graph.setObjectName('rootRouteDag'); graph.setFixedHeight(220); graph.set_tasks(route)
                graph.selected.connect(graph.choose); graph.activated.connect(self.select_task); settings_layout.addWidget(graph)
                settings_layout.addWidget(button('放大任务路线','linkButton',callback=lambda:self.show_task_route(task['id'])))
        stephead_widget=QWidget(); stephead=box(stephead_widget,horizontal=True); stephead.addWidget(label("方向与小步","section")); stephead.addStretch(); stephead.addWidget(label(f"{sum(s['done'] for s in task['steps'])} / {len(task['steps'])}","muted")); self.detail_content.addWidget(stephead_widget); stephead_widget.setVisible(self.runtime.profile.key!="work" or bool(task["steps"]))
        children=[t for t in self.tasks.tasks if t.get('parent_id')==task['id']]
        if children:
            dag=TaskDag(compact=True); dag.setObjectName('detailDag'); dag.setFixedHeight(220); dag.set_tasks(children)
            dag.selected.connect(dag.choose); dag.activated.connect(self.select_task)
            self.add_detail_section('步骤图',dag,'dag')
            if self.runtime.profile.key!="work": self.detail_content.addWidget(button("放大步骤图与调整路线","linkButton",callback=lambda:self.create_children(task['id'])))
        for index,step in enumerate(task["steps"]):
            row=box(horizontal=True)
            cb=QCheckBox(); cb.setAccessibleName(step["title"]); cb.setChecked(step["done"]); cb.setToolTip(step["title"]); cb.stateChanged.connect(lambda state,i=index:self.check_step(task["id"],i,state==Qt.Checked)); row.addWidget(cb)
            row.addWidget(label(step["title"],"muted" if step["done"] else None,True),1)
            row.addWidget(button("打开","linkButton",callback=lambda checked=False,cid=step["id"]:self.select_task(cid))); self.detail_content.addLayout(row)
        self.detail_content.addWidget(button("＋ 规划下一步","outline",callback=lambda:self.create_children(task["id"])))
        p=QProgressBar(); p.setTextVisible(False); p.setValue(progress(task)); p.setVisible(bool(task["steps"])); self.detail_content.addWidget(p)
        self.detail_content.addSpacing(2); self.detail_content.addWidget(line())
        if self.runtime.profile.key!="work": self.build_task_links(task)
        if task["category"]=="work" and not task.get("parent_id"):
            settings_layout.addWidget(button("选择本任务 Linear 归属","linkButton",callback=self.select_task_target))
        if task["category"]=="work":
            repohead=box(horizontal=True); repohead.addWidget(label("Git 仓库","section")); repohead.addStretch(); repohead.addWidget(button("关联仓库","linkButton",callback=self.manage_repositories)); settings_layout.addLayout(repohead)
            repos=task.get("repositories",[])
            if not repos and self.runtime.profile.key!="work": settings_layout.addWidget(label("给这件工作，连上它的代码。","muted",True))
            for index,repo in enumerate(repos):
                row=box(horizontal=True,spacing=7)
                name=label(("本地 · " if repo["kind"]=="local" else "远程 · ")+repo["name"],"muted",True); name.setToolTip(repo["location"]); row.addWidget(name,1)
                row.addWidget(button("打开 ↗","linkButton",callback=lambda checked=False,i=index:self.open_repository(i))); settings_layout.addLayout(row)
        if self.runtime.profile.key=="work": self.add_detail_section('更多任务设置',advanced,'settings')
        else: advanced.deleteLater()
        self.detail_content.addWidget(label("笔记" if self.runtime.profile.key=="work" else "留一句给下次的自己","section"))
        self.note=QTextEdit(); self.note.setObjectName("note"); self.note.setPlaceholderText("下次打开时，从这里继续…"); self.note.setFixedHeight(70); self.note.setPlainText(task["note"]); self.note_task_id=task["id"]; self.note.textChanged.connect(self.note_debounce.start); self.detail_content.addWidget(self.note)

    def build_task_links(self,task):
        if self.runtime.profile.key=="combined":
            linkshead=box(horizontal=True); linkshead.addWidget(label("关联空间","section")); linkshead.addStretch(); linkshead.addWidget(button("管理关联","linkButton",callback=lambda checked=False:self.bind_task("codex"))); self.detail_content.addLayout(linkshead)
        for kind,name in [("codex","Codex 聊天"),("linear","Linear issue")]:
            if not self.runtime.profile.work or kind=="linear" and task["category"]!="work": continue
            linked=[b for b in task["bindings"] if b["kind"]==kind]
            task_chat=kind=="codex" and any(not b.get("demo") for b in linked)
            card=QFrame(); card.setObjectName("binding"); layout=box(card,margins=(12,10,12,9),spacing=5)
            top=box(horizontal=True,spacing=7); pic=QLabel(); pic.setPixmap(icon(kind,"#5f7488" if kind=="codex" else "#8676a5",17).pixmap(17,17)); top.addWidget(pic)
            if task_chat: top.addWidget(button("打开任务 Codex","linkButton",callback=lambda checked=False:self.open_binding("codex")))
            else: top.addWidget(label(name))
            top.addStretch()
            if linked and linked[0].get("demo"): top.addWidget(label("示例","badge"))
            layout.addLayout(top)
            if linked:
                layout.addWidget(label(linked[0]["label"]+(f"  +{len(linked)-1}" if len(linked)>1 else ""),"muted",True))
                if not task_chat: layout.addWidget(button("打开对话  ↗" if kind=="codex" else "打开 issue  ↗","linkButton",callback=lambda checked=False,k=kind:self.open_binding(k)))
            elif kind=="linear": layout.addWidget(button("＋  关联一个 issue","linkButton",callback=lambda checked=False:self.bind_task("linear")))
            if kind=="codex":
                actions=box(horizontal=True,spacing=8)
                if task_chat and self.runtime.profile.key=="work":
                    more=button('聊天设置','linkButton'); menu=QMenu(more)
                    menu.addAction('新建聊天',self.new_chat); menu.addAction('绑定已有聊天',lambda:self.bind_task('codex'))
                    more.setMenu(menu); actions.addWidget(more); actions.addStretch()
                else:
                    actions.addWidget(button("新建聊天","outline",callback=self.new_chat),1)
                    actions.addWidget(button("绑定已有聊天","outline",callback=lambda checked=False:self.bind_task("codex")),1)
                layout.addLayout(actions)
            self.detail_content.addWidget(card)
        if task["category"]=="work" and task.get("linear_issue"):
            issue=task["linear_issue"]
            self.detail_content.addWidget(label(issue["identifier"]+" · Linear："+issue["state_name"]+"\n"+issue.get("team", ""),"muted",True))
        if task.get("linear_parent_pending"):
            self.detail_content.addWidget(label("步骤层级已调整 · Linear 父级待 22:00 同步","muted",True))
        if task["category"]=="work" and not has_work_issue(task):
            self.detail_content.addWidget(label("待同步：22:00 自动为此工作任务创建 issue；也可手动绑定。","muted",True))

    def flush_editors(self):
        self.note_debounce.stop()
        if not self.save_note(): return False
        return True

    def save_note(self):
        if self.note_task_id and hasattr(self,"note"):
            task=self.tasks.get(self.note_task_id)
            if task and task["note"]!=self.note.toPlainText(): return self.write(lambda:self.tasks.update(task["id"],note=self.note.toPlainText()))
        return True

    def check_step(self,task_id,index,checked):
        if not self.flush_editors(): return
        if self.write(lambda:self.tasks.set_step(task_id,index,checked)): self.refresh()

    def create_children(self,task_id):
        if not self.flush_editors(): return
        if not self.tasks.get(task_id): return
        dialog=PlanDialog(self,task_id)
        if dialog.exec_()==QDialog.Accepted: self.refresh()
        dialog.deleteLater()

    def set_status(self,task_id,status):
        if not self.flush_editors(): return
        if status=="done" and task_id==self.focus_task_id and not self.pause_focus(): return
        if self.write(lambda:self.tasks.set_status(task_id,status)):
            self.refresh()

    def toggle_done(self,task_id):
        task=self.tasks.get(task_id); self.set_status(task_id,"todo" if task["status"]=="done" else "done")

    def new_task(self):
        if not self.flush_editors(): return
        dialog=TaskDialog(self)
        if self.view in CATEGORIES: dialog.category.setCurrentIndex(list(CATEGORIES).index(self.view))
        if dialog.exec_()==QDialog.Accepted:
            values=dialog.values(); issue_url=values.pop("issue_url",None)
            task=make_task(values.pop("title"),values.pop("category")); task.update(values)
            if issue_url: task["bindings"]=[parse_binding("linear",issue_url)]
            if self.write(lambda:self.tasks.add(task)):
                self.selected=task["id"]; self.status_filter="all"; self.search.clear()
                if self.view in ("done","linked"): self.view="today"
                self.refresh()

    def edit_task(self):
        if not self.flush_editors(): return
        task=self.tasks.get(self.selected)
        if not task: return
        dialog=TaskDialog(self,task)
        if dialog.exec_()==QDialog.Accepted and self.write(lambda:self.tasks.update(task["id"],**dialog.values())): self.refresh()

    def bind_task(self, kind="codex"):
        if not self.flush_editors(): return
        task=self.tasks.get(self.selected)
        if task:
            self.binding_dialog=BindDialog(self,task,kind)
            self.binding_dialog.exec_()
            self.binding_dialog.deleteLater(); self.binding_dialog=None

    def new_chat(self):
        if not self.flush_editors(): return
        task=self.tasks.get(self.selected)
        if task:
            self.binding_dialog=CodexCreateDialog(self,task)
            self.binding_dialog.exec_()
            self.binding_dialog.deleteLater(); self.binding_dialog=None

    def delete_task(self):
        if not self.flush_editors(): return
        task=self.tasks.get(self.selected)
        if not task: return
        if task["steps"]:
            QMessageBox.information(self,"这件事还有子任务","请先打开并处理子任务。父任务删除不会连带删除子任务和远端 issue。")
            return
        if any(task['id'] in t.get('depends_on',[]) for t in self.tasks.tasks):
            QMessageBox.information(self,"还有步骤需要它先完成","请在父任务的「规划下一步」中解除相关前置依赖，再删除此任务。")
            return
        removal=f"「{task['title']}」将从本地任务中删除。\n笔记和本地关联会一同移除，已有后台记录会保留。"
        if self.runtime.profile.work: removal+="\nLinear issue 与 Git 仓库会保留。"
        confirm=QMessageBox.question(self,"删除这件任务？",removal,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if confirm!=QMessageBox.Yes: return
        if self.write(lambda:self.tasks.delete(task["id"])):
            self.note_task_id=None
            visible=self.visible_tasks()
            self.selected=visible[0]["id"] if visible else None
            self.refresh()

    def pull_my_issues(self):
        if not self.flush_editors(): return
        self.linear_dialog=LinearDialog(self)
        if self.runtime.linear.connection()['configured']:
            QTimer.singleShot(0,self.linear_dialog.pull)
        self.linear_dialog.exec_()
        self.linear_dialog.deleteLater(); self.linear_dialog=None

    def manage_repositories(self):
        if not self.flush_editors(): return
        task=self.tasks.get(self.selected)
        if task and task["category"]=="work": RepositoryDialog(self,task["id"]).exec_()

    def open_repository(self,index):
        try: repo=self.tasks.resolve_repository(self.selected,index)
        except (ValueError,OSError) as exc: QMessageBox.warning(self,"暂时无法打开仓库",str(exc)); return
        url=QUrl.fromLocalFile(repo["location"]) if repo["kind"]=="local" else QUrl(repo["web_url"])
        if not QDesktopServices.openUrl(url): QMessageBox.information(self,"暂时无法打开","系统未找到可打开此仓库的程序。\n"+repo["location"])

    def open_binding(self,kind):
        task=self.tasks.get(self.selected); links=[b for b in task["bindings"] if b["kind"]==kind]
        if len(links)>1:
            dialog=QDialog(self); dialog.setWindowTitle("选择关联"); layout=box(dialog,margins=(22,22,22,22))
            for link in links: layout.addWidget(button(link["label"]+(" · 示例" if link.get("demo") else ""),"outline",callback=lambda checked=False,b=link:self.launch_link(b)))
            dialog.exec_()
        elif links: self.launch_link(links[0])

    def launch_link(self,binding):
        if binding.get("demo"):
            QMessageBox.information(self,"这是一条示例关联","在「管理关联」里粘贴自己的真实聊天或 issue 链接，就可以从这里打开。\n示例没有连接你的 Codex 或 Linear 账号。"); return
        try:
            task=self.tasks.get(self.selected)
            index=next(i for i,b in enumerate(task["bindings"]) if b["url"]==binding["url"])
            binding=self.tasks.resolve_binding(task["id"],index)
        except (ValueError,OSError,StopIteration) as exc: QMessageBox.warning(self,"链接需要修正",str(exc)); return
        if binding['kind']=='codex':
            from codex_support import local_thread_state
            state=local_thread_state(binding['url'].rsplit('/',1)[-1])
            if state and state['missing']:
                QMessageBox.warning(self,'聊天会话文件缺失','这个旧聊天的本机会话文件不存在，暂时无法继续。\n已保留任务和关联；重新建立可用聊天并替换此旧关联后再打开。');return
        if not QDesktopServices.openUrl(QUrl(binding["url"])):
            QMessageBox.information(self,"暂时无法打开","系统没有找到处理这个链接的应用。\n请检查 Codex 是否安装，或复制链接手动打开。\n\n"+binding["url"])

    def focus_selected(self):
        if not self.focus: return
        task=self.tasks.get(self.selected)
        if not task or task["status"]=="done": return
        if self.write(lambda:self.focus.start(task["id"])): self.refresh_timer()

    def toggle_focus(self):
        if not self.focus: return
        action=self.focus.pause if self.running else self.focus.start
        if self.write(action): self.refresh_timer()

    def tick(self):
        revision=self.tasks.revision()
        learning_revision=self.runtime.learning.revision()["revision"] if self.runtime.learning else 0
        if (revision["revision"]!=getattr(self,"seen_revision",-1) or learning_revision!=getattr(self,"seen_learning_revision",-1)) and not QApplication.activeModalWidget():
            current=self.tasks.get(self.note_task_id)
            dirty=bool(current and hasattr(self,"note") and self.note.toPlainText()!=current["note"])
            if not dirty:
                self.refresh()
        if not self.focus: return
        state=self.focus.snapshot(); self.refresh_timer(state,revision["unfinished"])
        if state["error"] and state["error"]!=self.shown_focus_error:
            self.shown_focus_error=state["error"]; QMessageBox.warning(self,"专注计时已暂停",state["error"])
        elif not state["error"]: self.shown_focus_error=""
        if state["completion_serial"]>self.seen_completion:
            self.seen_completion=state["completion_serial"]
            QMessageBox.information(self,"这一小段，完成了","辛苦啦，起来喝口水吧。\n专注结束不会自动完成任务。")

    def pause_focus(self):
        if not self.focus: return True
        success=self.write(self.focus.pause)
        self.refresh_timer()
        return success

    def reset_focus(self):
        if self.write(self.focus.reset): self.refresh_timer()

    @property
    def focus_task_id(self): return self.focus.snapshot()["task_id"] if self.focus else None
    @property
    def focus_duration(self): return self.focus.snapshot()["duration"] if self.focus else 0
    @property
    def remaining(self): return self.focus.snapshot()["remaining"]
    @property
    def running(self): return self.focus.snapshot()["running"]

    def refresh_timer(self,state=None,unfinished=None):
        if not self.focus: return
        state=state or self.focus.snapshot(); remaining=state["remaining"]; running=state["running"]
        task=self.tasks.get(state["task_id"])
        self.focus_title.setText(task["title"] if task else "从一件小事开始吧")
        self.focus_step.setText("子任务 · "+next((c["title"] for c in task["steps"] if not c["done"]), "给这件事留一点时间") if task else "选中一个任务，点击「专注这件事」。")
        self.focus_status.setText("●  正在专注，其他事可以等等" if running else "●  留一段专注时间")
        text=f"{remaining//60:02}:{remaining%60:02}"; self.timer_label.setText(text)
        self.focus_button.setText("暂停一下" if running else ("继续专注" if remaining<state["duration"] and remaining>0 else "开始专注")); self.focus_button.setIcon(icon("pause" if running else "play","#ffffff",16))
        self.focus_button.setEnabled(bool(self.tasks.revision()["unfinished"] if unfinished is None else unfinished))
        if self.mini:
            self.mini_time.setText(text); self.mini_title.setText(task["title"] if task else "先选一件小事"); self.mini_button.setText("暂停一下" if running else "开始专注")

    def show_mini(self):
        if not self.focus: return
        if self.mini: self.mini.show(); self.mini.raise_(); return
        self.mini=QDialog(self,Qt.Window | Qt.WindowStaysOnTopHint); self.mini.setWindowTitle(APP_NAME+" · 专注小窗"); self.mini.setFixedSize(330,365)
        layout=box(self.mini,margins=(30,24,30,24),spacing=15); layout.addWidget(label("收拢注意力，沿这一小步前进。","muted"),alignment=Qt.AlignCenter)
        layout.addWidget(OrbitMark(compact=True),alignment=Qt.AlignCenter); self.mini_title=label("","h2",True); self.mini_title.setAlignment(Qt.AlignCenter); layout.addWidget(self.mini_title)
        self.mini_time=label("","miniTimer"); self.mini_time.setAlignment(Qt.AlignCenter); layout.addWidget(self.mini_time)
        self.mini_button=button("开始专注","primary",callback=self.toggle_focus); layout.addWidget(self.mini_button); layout.addWidget(label("任务进度留在主窗口，专注留在这里。","muted",True),alignment=Qt.AlignCenter)
        self.refresh_timer(); self.mini.show()

    def settings(self):
        from preferences_ui import PreferencesDialog
        dialog=PreferencesDialog(self); dialog.exec_(); dialog.deleteLater()

    def open_details(self):
        if not self.compact_layout: return
        screen=self.screen().availableGeometry()
        self.details_dialog.resize(min(460,screen.width()),min(760,screen.height()))
        self.details_dialog.show(); self.details_dialog.raise_(); self.details_dialog.activateWindow()

    def adapt_layout(self):
        if not hasattr(self,'details_dialog') or not self.runtime.started: return
        size=self.display.current.font_size; ratio=size/13
        side_width=max(190,round(202*min(ratio,1.15)))
        detail_width=round(334*ratio)
        compact=self.width()<side_width+detail_width+round(640*max(1,ratio*.85))
        changed=compact!=self.compact_layout; self.compact_layout=compact
        self.sidebar_scroll.setFixedWidth(side_width)
        self.details_toggle.setVisible(compact); self.breadcrumb.setVisible(not compact and self.runtime.profile.key!="work")
        separate_actions=compact and self.runtime.profile.key=='work'
        target=self.task_actions_layout if separate_actions else self.heading_layout
        if target.indexOf(self.task_actions)<0:
            self.heading_layout.removeWidget(self.task_actions); self.task_actions_layout.removeWidget(self.task_actions)
            target.addWidget(self.task_actions); self.task_actions.show()
        self.task_actions_bar.setVisible(separate_actions)
        self.mascot.setVisible(not compact)
        self.subtitle.setVisible(not compact and self.runtime.profile.key!="work")
        self.focus_title.setVisible(not compact or self.width()-side_width>=650)
        self.focus_status.setVisible(not compact); self.focus_step.setVisible(not compact)
        self.focus_caption.setVisible(not compact); self.focus_divider.setVisible(not compact)
        self.focus_timer_layout.setDirection(0 if compact else 2)
        self.focus_card.setMinimumHeight(80 if compact else 190)
        self.focus_title.setMaximumHeight(round(52*ratio) if compact else 16777215)
        self.footer_shortcuts.setVisible(not compact and self.runtime.profile.key!="work")
        self.main_layout.setSpacing(10 if compact else 17)
        self.outline_hint.setVisible(self.runtime.profile.key!="work" and (not compact or self.height()>=760))
        if changed:
            for widget in (self.timer_label,self.heading):
                widget.setProperty('compact',compact); widget.style().unpolish(widget); widget.style().polish(widget)
        self.main_layout.setContentsMargins(18 if compact else 29,16,18 if compact else 29,14)
        self.undo_structure_button.setText('撤销' if compact else '撤销结构调整')
        self.date_label.setVisible(not compact and self.runtime.profile.key!="work")
        if compact:
            if self.details.parentWidget()!=self.details_dialog:
                self.details.setParent(self.details_dialog); self.details_dialog.layout().addWidget(self.details)
            self.details.setMinimumWidth(0); self.details.setMaximumWidth(16777215)
        else:
            self.details_dialog.hide()
            if self.details.parentWidget()!=self.centralWidget():
                self.details.setParent(self.centralWidget()); self.body_layout.addWidget(self.details)
            self.details.setFixedWidth(detail_width); self.details.show()
        if changed and self.list_mode=='board': self.refresh_list()

    def open_updates(self):
        from update_ui import UpdateDialog
        dialog=UpdateDialog(self,self.runtime.updates);dialog.exec_();dialog.deleteLater()

    def open_daily(self):
        from daily_ui import DailyDialog
        if self.flush_editors(): DailyDialog(self).exec_()

    def open_worklog(self,kind=None):
        if not self.runtime.daily or not self.flush_editors():return
        identifier=None
        if kind:
            result=[]
            if not self.write(lambda:result.append(self.runtime.daily.worklog_start(kind))):return
            identifier=result[0]['id']
        from work_logs_ui import WorkLogsDialog
        if self.worklog_dialog is None:self.worklog_dialog=WorkLogsDialog(self)
        if identifier:self.worklog_dialog.choose(identifier)
        self.worklog_dialog.show();self.worklog_dialog.raise_();self.worklog_dialog.activateWindow()

    def open_evaluation(self):
        if not self.runtime.evaluation or not self.flush_editors(): return
        from evaluation_ui import EvaluationDialog
        if self.evaluation_dialog is None: self.evaluation_dialog=EvaluationDialog(self)
        self.evaluation_dialog.show(); self.evaluation_dialog.raise_(); self.evaluation_dialog.activateWindow()

    def select_task_target(self):
        from daily_ui import TargetDialog
        task=self.tasks.get(self.selected)
        if not task or task.get("parent_id"): return
        if has_work_issue(task):
            QMessageBox.information(self,"已有 issue","此任务已绑定 issue，归属以 Linear 为准。此入口只用于尚未同步的根任务。")
            return
        dialog=TargetDialog(self,self.runtime.daily,task.get("linear_target") or self.runtime.daily.snapshot()["config"]["target"])
        if dialog.exec_()==QDialog.Accepted:
            self.write(lambda:self.tasks.update(task["id"],linear_target=dialog.value()))

    def open_attention(self):
        from station_ui import AttentionDialog
        if not self.flush_editors(): return
        if self.station_dialog: self.station_dialog.close(); self.station_dialog.deleteLater()
        self.station_dialog=AttentionDialog(self,self.runtime.station)
        self.station_dialog.show()

    def drain_ui_workers(self):
        """Called on the Qt control thread before domain services are stopped."""
        for timer in self.findChildren(QTimer):
            timer.stop()
        workers = self.findChildren(QThread)
        for worker in workers:
            worker.requestInterruption()
            for name in ("cancel_event", "stop"):
                event = getattr(worker,name,None)
                if hasattr(event,"set"): event.set()
        for worker in workers:
            worker.wait()
        # Deliver already queued finished/result callbacks while services and
        # observation still exist. New UI input is excluded during teardown.
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
        for timer in self.findChildren(QTimer):
            timer.stop()
        if any(worker.isRunning() for worker in self.findChildren(QThread)):
            raise RuntimeError("界面回调仍启动了新线程，尚不能关闭服务")

    def restore_window(self):
        reveal_window(self)

    def background_busy(self):
        evaluation=self.runtime.evaluation.snapshot() if self.runtime.evaluation else {}
        return bool(evaluation.get('syncing') or evaluation.get('query',{}).get('busy') or evaluation.get('reports',{}).get('busy') or self.runtime.station and self.runtime.station.workers or self.runtime.daily and (self.runtime.daily.snapshot()["busy"] or self.runtime.daily.worklog_snapshot()["busy"]) or self.runtime.learning and self.runtime.learning.revision()["busy"])

    def exit_application(self):
        running=self.background_busy()
        work="正在执行的 Codex 任务和日报处理会停止，已有文件与记录会保留。" if self.runtime.profile.key=="work" else "课程生成和学习问答会停止，已有文件与记录会保留。"
        if running and QMessageBox.question(self,"停止执行并退出？",work,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
        self.exiting=True
        if self.close(): QApplication.instance().quit()
        else: self.exiting=False

    def closeEvent(self,event):
        self.display.remember_window(self)
        storage=getattr(self,'storage_dialog',None)
        if storage and storage.worker:
            storage.reject(); event.ignore(); return
        if self.binding_dialog and self.binding_dialog.busy:
            self.binding_dialog.reject(); event.ignore(); return
        if self.linear_dialog and self.linear_dialog.busy:
            self.linear_dialog.reject(); event.ignore(); return
        if not self.flush_editors(): event.ignore(); return
        if not self.exiting and self.tray.isVisible():
            if self.station_dialog: self.station_dialog.hide()
            if self.worklog_dialog: self.worklog_dialog.hide()
            if self.evaluation_dialog: self.evaluation_dialog.hide()
            if self.mini: self.mini.hide()
            for dialog in self.learning_dialogs.values(): dialog.hide()
            self.details_dialog.hide()
            self.hide(); event.ignore(); return
        if not self.exiting and self.background_busy():
            QMessageBox.information(self,"系统托盘不可用","当前桌面未提供系统托盘，已保留窗口继续执行。可以使用最小化；退出请在菜单中选择停止执行并退出。")
            self.showMinimized(); event.ignore(); return
        for dialog in list(self.learning_dialogs.values()):
            if not dialog.close(): event.ignore(); return
        if not self.pause_focus(): event.ignore(); return
        self.tick_timer.stop()
        if self.mini: self.mini.close()
        if self.station_dialog: self.station_dialog.close()
        if self.evaluation_dialog: self.evaluation_dialog.close()
        self.tray.hide()
        event.accept()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.adapt_layout()
        if hasattr(self,"resize_grip"): self.resize_grip.move(self.width()-17,self.height()-17)


def main():
    parser=argparse.ArgumentParser(description=APP_DISPLAY_NAME+" · 原生桌面任务与专注工具")
    parser.add_argument('--version', action='version', version=APP_DISPLAY_NAME+' '+APP_VERSION)
    parser.add_argument('--self-test', action='store_true', help='使用临时数据离线检查服务、学习资源和系统 Git，不调用模型')
    parser.add_argument("--data-dir",type=Path,help="指定本地数据目录")
    parser.add_argument("--graph",action="store_true",help="启用本机只读 YomkGraph 运行图谱（默认关闭）")
    parser.add_argument("--graph-port",type=int,default=18767,help="图谱端口，默认 18767；仅监听 127.0.0.1")
    parser.add_argument("--screenshot",type=Path,help="保存主窗口截图后退出（支持 QT_QPA_PLATFORM=offscreen）")
    parser.add_argument("--screenshot-view",choices=["today","board","mini","attention","station"],default="today")
    args=parser.parse_args()
    if not 0 <= args.graph_port <= 65535:
        parser.error("--graph-port 必须为 0–65535；0 表示随机空闲端口")
    if args.self_test and (args.data_dir or args.screenshot):
        parser.error('--self-test 始终使用临时数据，不能与 --data-dir 或 --screenshot 合用')
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling,True); QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps,True)
    app=QApplication(sys.argv[:1])
    # Keep the desktop/window identity compatible; data_paths owns storage naming.
    app.setApplicationName("Luma" if PROFILE.key=="study" else "Momo"); app.setOrganizationName("Sevenova"); app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setWindowIcon(icon("orbit",PALETTE["primary"],64)); app.setStyle("Fusion"); app.setFont(QFont("Noto Sans CJK SC",10)); app.setStyleSheet(STYLE)
    from PyQt5.QtCore import QSettings
    display=DisplayController(app,None if args.self_test or args.screenshot else QSettings("Sevenova",PROFILE.wordmark.title())); display.apply(display.saved)
    if QSystemTrayIcon.isSystemTrayAvailable(): app.setQuitOnLastWindowClosed(False)
    if args.self_test:
        from split_release_check import run_checks
        try:
            return run_checks(app, Momo, graph_port=args.graph_port if args.graph else None)
        except Exception:
            import traceback
            traceback.print_exc()
            return 1
        finally:
            api.shutdown()
    try:
        from split_migration import default_split_directory as default_data_directory
        directory=args.data_dir or default_data_directory(Path((os.environ.get('LOCALAPPDATA') if os.name=='nt' else None) or QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)))
    except (ValueError,OSError,RuntimeError) as exc:
        QMessageBox.critical(None,APP_NAME+' 数据目录未就绪',str(exc)); return 1
    activation=None if args.screenshot else WindowActivation(directory,app)
    if activation and activation.request():
        return 0
    runtime=MomoRuntime(directory/"tasks.json",profile=PROFILE,graph_port=args.graph_port if args.graph else None)
    window=None
    apply_update=False
    try:
        runtime.start()
        if runtime.graph: print("星序 YomkGraph: "+runtime.graph.url,flush=True)
        if activation: activation.listen()
        window=Momo(runtime,display)
        if activation: activation.requested.connect(lambda startup_id: reveal_window(window,startup_id))
        window.show()
        if os.environ.get('ORBIT_EVALUATOR_BUILD')=='1':
            window.setWindowTitle('星序 · 日志评估开发版')
            QTimer.singleShot(0,window.open_evaluation)
        apply_update=True
        QTimer.singleShot(0,runtime.update_service.mark_started)
        if args.screenshot:
            if args.screenshot_view=="board": window.change_mode("board")
            if args.screenshot_view=="mini": window.show_mini()
            if args.screenshot_view in ("attention","station"): window.open_attention()
            def capture():
                args.screenshot.parent.mkdir(parents=True,exist_ok=True)
                target=window.mini if args.screenshot_view=="mini" else window.station_dialog if args.screenshot_view in ("attention","station") else window
                app.exit(0 if target.grab().save(str(args.screenshot)) else 1)
            QTimer.singleShot(450,capture)
        return app.exec_()
    except DataSpaceInUse as exc:
        if activation and activation.request(timeout_ms=4000,retry=True):
            return 0
        QMessageBox.information(None,APP_NAME+" 已在运行",str(exc)+"。\n若已有窗口仍未出现，请从托盘打开星序；旧版本需完整退出后重新启动一次。")
        return 75
    except (ValueError,OSError,RuntimeError) as exc:
        apply_update=False
        QMessageBox.critical(None,APP_NAME+" 启动失败",str(exc)); return 1
    finally:
        if activation: activation.close()
        try:
            if runtime.graph: runtime.graph.stop_http()
            if window: window.drain_ui_workers()
        finally:
            ui_alive = bool(window and any(worker.isRunning() for worker in window.findChildren(QThread)))
            if not ui_alive:
                try: runtime.close(apply_update=apply_update,final_shutdown=True)
                finally:
                    # Do not clear services underneath a producer that failed to drain.
                    if not runtime.started: api.shutdown()


if __name__=="__main__": sys.exit(main())
