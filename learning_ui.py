"""Native course binding, background progress and data-only reading/answers."""
import json
from pathlib import Path
from PyQt5.QtCore import Qt, QEvent, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QDialog, QWidget, QScrollArea, QSplitter, QListWidget,
    QListWidgetItem, QTextEdit, QPlainTextEdit, QTabWidget, QLineEdit, QSpinBox,
    QComboBox, QGroupBox, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QProgressBar)
from ui_helpers import box, label, button, clear, line
from learning_content import SUBJECT_MODE, ordered_lessons
from learning_tutor_ui import TutorPanel
from learning_knowledge_ui import KnowledgePanel
from learning_diagram_ui import LessonDiagram
from learning_progress import progress_view

STATUS = {'queued':'排队中','running':'生成中','review':'待审阅','failed':'生成失败',
          'cancelled':'已停止','interrupted':'已中断','needs_input':'需要补充信息'}


def scroll_body():
    scroll = QScrollArea(); scroll.setWidgetResizable(True)
    host = QWidget(); layout = box(host,margins=(20,16,20,16),spacing=14)
    layout.setAlignment(Qt.AlignTop); scroll.setWidget(host)
    return scroll, layout


class LearningDialog(QDialog):
    def __init__(self, parent, task_id, tab=0):
        super().__init__(parent); self.owner=parent; self.client=parent.runtime.learning; self.task_id=task_id; self.readers={}
        self.setWindowModality(Qt.NonModal)
        self.setWindowTitle('学习课程'); self.resize(850,730); self.setMinimumSize(680,580)
        task=parent.tasks.get(task_id); layout=box(self,margins=(24,20,24,20),spacing=14)
        layout.addWidget(label(task['title'],'h2',True)); self.tabs=QTabWidget(); layout.addWidget(self.tabs,1)
        self.course_page=QWidget(); page=box(self.course_page,margins=(16,16,16,16),spacing=12)
        self.course_status=label('','section',True); page.addWidget(self.course_status)
        self.read_button=button('开始学习','primary',callback=self.read); page.addWidget(self.read_button)
        page.addWidget(button('提出意见 / 重新生成','outline',callback=self.improve))
        self.progress=QProgressBar(); self.progress.setObjectName('courseProgress'); self.progress.setTextVisible(False)
        self.progress.setStyleSheet('QProgressBar#courseProgress {min-height:12px; max-height:12px; border-radius:6px;} QProgressBar::chunk {border-radius:6px;}')
        self.progress.setToolTip('按实际阶段前进；不是预计耗时百分比。模型未报告阶段时保持原阶段。')
        page.addWidget(self.progress)
        self.progress_text=label('','section',True); page.addWidget(self.progress_text)
        self.jobs=QListWidget(); self.jobs.currentItemChanged.connect(self.show_job); page.addWidget(self.jobs,1)
        self.job_detail=QPlainTextEdit(); self.job_detail.setReadOnly(True); self.job_detail.setMaximumHeight(160); page.addWidget(self.job_detail)
        actions=box(horizontal=True); self.stop_button=button('停止生成','outline',callback=self.cancel); actions.addWidget(self.stop_button)
        actions.addWidget(button('查看生成文件','linkButton',callback=self.open_folder)); actions.addStretch(); page.addLayout(actions)
        self.tabs.addTab(self.course_page,'课程与进度')
        form_page=QWidget(); form_layout=box(form_page,spacing=10)
        form, content=scroll_body(); form_layout.addWidget(form,1); self.tabs.addTab(form_page,'创建课程')
        content.addWidget(label('从一个学习对象，展开完整知识体系','h2'))
        content.addWidget(label('先检索资料、建立知识库，再按先修关系组织从基础到进阶的课程与练习。','muted',True))
        content.addWidget(label('学习对象','section'))
        self.subject=QLineEdit(task['title']); self.subject.setPlaceholderText('例如：机械臂力矩控制、Python、线性代数'); content.addWidget(self.subject)
        content.addWidget(label('无需填写掌握程度或学习目标。必要基础会纳入前置课程；已有知识可在阅读时跳过。','muted',True))
        for title,description in [('建立知识库','梳理基础概念、核心方法、应用、适用边界与进阶方向，保留可靠来源和资料缺口。'),('串起学习路径','把所需先修知识放在前面，逐步连接核心内容与进阶实践。'),('编写完整课程','每章提供分步讲解、完整案例、自测与练习，而不只给提纲。')]:
            content.addWidget(label(title,'section')); content.addWidget(label(description,'muted',True))
        row=box(horizontal=True); row.addWidget(label('课程生成并发数','muted')); self.concurrency=QSpinBox(); self.concurrency.setRange(1,32)
        self.concurrency.setValue(self.client.snapshot()['concurrency']); row.addWidget(self.concurrency); row.addStretch(); form_layout.addLayout(row)
        self.create_button=button('创建知识库与课程','primary',callback=self.create); form_layout.addWidget(self.create_button)
        form_layout.addWidget(label('沿用系统 Codex 配置与登录。收起到托盘后继续；退出应用会停止生成并保留记录。','muted',True))
        bind_page=QWidget(); catalog=box(bind_page,margins=(16,16,16,16),spacing=12)
        catalog.addWidget(label('选择整门课程进行绑定，章节在阅读器内切换；不按任务标题自动匹配。','muted',True))
        self.search=QLineEdit(); self.search.setPlaceholderText('筛选已有课程'); self.search.textChanged.connect(self.filter_courses); catalog.addWidget(self.search)
        self.courses=QListWidget(); catalog.addWidget(self.courses,1)
        for course in self.client.catalog():
            self.add_course(course)
        row=box(horizontal=True); row.addWidget(button('绑定所选课程','primary',callback=self.bind)); row.addWidget(button('解除课程绑定','outline',callback=self.unbind)); catalog.addLayout(row)
        self.tabs.addTab(bind_page,'绑定已有课程')
        self.improve_page=QWidget(); improve_shell=box(self.improve_page,spacing=10)
        improve_scroll,improve_layout=scroll_body(); improve_shell.addWidget(improve_scroll,1)
        self.tabs.addTab(self.improve_page,'改进课程')
        improve_layout.addWidget(label('让课程更好懂，也更扎实','h2',True))
        self.base_course=label('','section',True); improve_layout.addWidget(self.base_course)
        improve_layout.addWidget(label('写下哪里跳步、哪里不直观，或希望增加的讲解与案例。系统会参考当前课程，重建知识链路并生成新版本。','muted',True))
        self.feedback=QPlainTextEdit(); self.feedback.setAccessibleName('课程重新生成的改进意见')
        self.feedback.setPlaceholderText('例如：先用一次控制周期建立整体直觉，再逐层解释 PDO、SM 与 FMMU。减少缩写堆叠，加入数据流图和分步算例。')
        self.feedback.setMinimumHeight(130); self.feedback.setMaximumHeight(230); improve_layout.addWidget(self.feedback)
        self.feedback_notice=label('','muted',True); improve_layout.addWidget(self.feedback_notice)
        suggestions=box(horizontal=True,spacing=8)
        for title,text in [('从零搭体系','从零介绍必要基础，先建立全景，再按真实先修关系逐层深入；不要把知识清单当成讲解。'),
                           ('讲透关键步骤','用一个贯穿案例逐步解释关键过程，写清每步为什么、条件和结果，补充反例与迁移练习。'),
                           ('增加直观图解','对数据流、空间关系或时序难点加入有教学作用的原生图解，说明观察顺序，并用文字解释图中关系。')]:
            suggestions.addWidget(button(title,'outline',callback=lambda checked=False,value=text:self.add_feedback(value)))
        improve_layout.addLayout(suggestions)
        improve_layout.addWidget(label('旧课程与作答保留；新草稿通过结构和教学组织检查后接入。生成期间仍可学习旧课程，切换绑定不会被新结果覆盖。','muted',True))
        self.regenerate_button=button('按意见重新生成课程','primary',callback=self.regenerate); improve_shell.addWidget(self.regenerate_button)
        self._feedback_course_id=None; self._saved_feedback=''
        self.feedback_timer=QTimer(self); self.feedback_timer.setSingleShot(True); self.feedback_timer.setInterval(700)
        self.feedback_timer.timeout.connect(self.save_feedback)
        self.feedback.textChanged.connect(self.feedback_changed)
        self.tabs.setCurrentIndex(tab)
        self.notice=label('','muted',True); layout.addWidget(self.notice)
        layout.addWidget(label('生成期间可切换其他任务；关闭此窗口也会继续生成，可从任务卡片查看进度。','muted',True))
        layout.addWidget(button('关闭',callback=self.accept))
        self.timer=QTimer(self); self.timer.setInterval(650); self.timer.timeout.connect(self.refresh); self.timer.start()
        self._revision=None; self.refresh()
        if self.state['jobs']:
            previous=self.state['jobs'][-1]
            self.subject.setText(previous['input'].get('subject') or previous['input'].get('title') or task['title'])
            if previous['status']=='needs_input':
                self.notice.setText(previous['message'] if previous['input'].get('mode')==SUBJECT_MODE else '已改为按学习对象建立知识库，可直接重新创建。')

    def add_course(self,course):
        text=course['title']+'\n'+course['origin']+f" · {course['lesson_count']} 章 · {course['question_count']} 道练习"
        item=QListWidgetItem(text); item.setData(Qt.UserRole,course['id']); self.courses.addItem(item)

    def refresh(self):
        try: state=self.client.snapshot(self.task_id)
        except (ValueError,OSError) as exc: self.notice.setText(str(exc)); return
        self.update_generation_progress(state)
        if state['revision']==self._revision: return
        feedback_course_id=(state['course'] or {}).get('id')
        if feedback_course_id!=self._feedback_course_id:
            if not self.save_feedback(): return
            self._feedback_course_id=feedback_course_id; self._saved_feedback=state.get('feedback_draft','')
            self.feedback.blockSignals(True); self.feedback.setPlainText(self._saved_feedback); self.feedback.blockSignals(False)
        self._revision=state['revision']; self.state=state
        existing={self.courses.item(i).data(Qt.UserRole) for i in range(self.courses.count())}
        for course in self.client.catalog():
            if course['id'] not in existing:
                self.add_course(course)
        self.filter_courses(self.search.text())
        self.course_status.setText('已绑定教程 · '+state['course']['title'] if state['course'] else '尚未绑定教程')
        self.base_course.setText('改进对象：'+state['course']['title'] if state['course'] else '请先绑定需要改进的课程')
        self.feedback_changed()
        self.read_button.setEnabled(bool(state['course'])); self.create_button.setEnabled(not state['busy'])
        current=self.jobs.currentItem(); selected=current.data(Qt.UserRole) if current else None
        self.jobs.clear()
        for job in reversed(state['jobs']):
            item=QListWidgetItem(STATUS.get(job['status'],job['status'])+' · '+job['created_at'][:16].replace('T',' ')+' UTC')
            item.setData(Qt.UserRole,job['id']); self.jobs.addItem(item)
            if job['id']==selected: self.jobs.setCurrentItem(item)
        if not self.jobs.currentItem() and self.jobs.count(): self.jobs.setCurrentRow(0)
        self.show_job()

    def show_job(self, *unused):
        item=self.jobs.currentItem(); selected=item.data(Qt.UserRole) if item else None
        job=next((j for j in getattr(self,'state',{}).get('jobs',[]) if j['id']==selected),None)
        self.stop_button.setEnabled(bool(job and job['status'] in ('queued','running')))
        self.job_detail.setPlainText(job['message']+('\nCodex 聊天：'+job['thread_id'] if job.get('thread_id') else '') if job else '点击「创建课程」开始，或明确绑定已有课程。')
        if job and job['input'].get('feedback'):
            self.job_detail.appendPlainText('\n本次改进意见：\n'+job['input']['feedback'])
        if getattr(self,'state',None): self.update_generation_progress(self.state)

    def update_generation_progress(self,state):
        jobs=state['jobs']; selected=self.jobs.currentItem()
        identifier=selected.data(Qt.UserRole) if selected else None
        job=next((j for j in reversed(jobs) if j['status'] in ('queued','running')),None)
        if job is None: job=next((j for j in jobs if j['id']==identifier),jobs[-1] if jobs else None)
        self.progress.setVisible(bool(job)); self.progress_text.setVisible(bool(job))
        if not job: return
        view=progress_view(job); self.progress.setRange(0,view['maximum']); self.progress.setValue(view['value'])
        self.progress_text.setText(view['title']+(' · 已用 '+view['elapsed'] if view['elapsed'] else ''))

    def create(self):
        if not self.owner.flush_editors(): return
        try: self.client.start(self.task_id,self.subject.text(),self.concurrency.value())
        except (ValueError,OSError) as exc: self.notice.setText(str(exc)); return
        self.notice.setText('已在后台开始；可随时关闭此面板查看其他任务。'); self.tabs.setCurrentIndex(0); self.refresh(); self.refresh_owner()

    def improve(self, course_id=None):
        self.refresh()
        if course_id and (not self.state['course'] or self.state['course']['id']!=course_id):
            self.notice.setText('此阅读器已不是当前绑定课程，请先切回需要改进的课程。'); return
        self.tabs.setCurrentWidget(self.improve_page); self.show(); self.raise_(); self.feedback.setFocus()

    def add_feedback(self,text):
        current=self.feedback.toPlainText().strip()
        self.feedback.setPlainText(current+'\n'+text if current else text)

    def feedback_changed(self):
        size=len(self.feedback.toPlainText().strip()); state=getattr(self,'state',{})
        self.feedback_notice.setText(f'{size} / 6000 字'+(' · 请精简意见后再生成' if size>6000 else ''))
        self.regenerate_button.setEnabled(bool(state.get('course')) and not state.get('busy',False) and 1<=size<=6000)
        self.feedback_timer.start()

    def save_feedback(self):
        self.feedback_timer.stop(); text=self.feedback.toPlainText()
        if not self._feedback_course_id or text==self._saved_feedback: return True
        try: self.client.save_feedback(self.task_id,self._feedback_course_id,text)
        except (ValueError,OSError) as exc: self.notice.setText('改进意见尚未保存：'+str(exc)); return False
        self._saved_feedback=text; return True

    def regenerate(self):
        if not self.owner.flush_editors(): return
        course=self.state.get('course')
        if not course: self.notice.setText('请先绑定需要改进的课程。'); return
        try: self.client.regenerate(self.task_id,course['id'],self.feedback.toPlainText(),self.concurrency.value())
        except (ValueError,OSError) as exc: self.notice.setText(str(exc)); return
        self.notice.setText('已按意见在后台生成新版本；当前课程仍可继续学习。')
        self.tabs.setCurrentIndex(0); self.refresh(); self.refresh_owner()

    def refresh_owner(self):
        # The main window remains editable while this panel is open.
        if self.owner.flush_editors(): self.owner.refresh()

    def cancel(self):
        item=self.jobs.currentItem()
        if item:
            try: self.client.cancel(item.data(Qt.UserRole)); self.refresh()
            except (ValueError,OSError) as exc: self.notice.setText(str(exc))

    def filter_courses(self, value):
        for index in range(self.courses.count()):
            item=self.courses.item(index); item.setHidden(value.lower() not in item.text().lower())

    def bind(self):
        item=self.courses.currentItem()
        if not item or item.isHidden(): self.notice.setText('请先选择一门课程。'); return
        try: self.client.bind(self.task_id,item.data(Qt.UserRole))
        except (ValueError,OSError) as exc: self.notice.setText(str(exc)); return
        self.notice.setText('已绑定所选课程。'); self.refresh(); self.refresh_owner(); self.tabs.setCurrentIndex(0)

    def unbind(self):
        try: self.client.bind(self.task_id,None)
        except (ValueError,OSError) as exc: self.notice.setText(str(exc)); return
        self.refresh(); self.refresh_owner(); self.notice.setText('已解除绑定；课程与已有作答保留。')

    def read(self):
        if not self.state['course']: return
        course_id=self.state['course']['id']; reader=self.readers.get(course_id)
        if reader is None:
            reader=CourseReader(self,self.client,self.task_id,course_id)
            reader.setAttribute(Qt.WA_DeleteOnClose)
            self.readers[course_id]=reader
            reader.finished.connect(lambda code,cid=course_id:self.readers.pop(cid,None))
        reader.show(); reader.raise_(); reader.activateWindow()

    def open_folder(self):
        item=self.jobs.currentItem(); folder=Path(self.state['folder'])
        if item: folder=folder/'generations'/item.data(Qt.UserRole)
        if folder.is_dir(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def done(self, code):
        if not self.save_feedback(): return
        for reader in list(self.readers.values()):
            if not reader.close(): return
        self.timer.stop(); super().done(code)

    def showEvent(self,event):
        super().showEvent(event); self.refresh(); self.timer.start()

    def hideEvent(self,event):
        self.save_feedback()
        self.timer.stop()
        for reader in self.readers.values(): reader.hide()
        super().hideEvent(event)


class CourseReader(QDialog):
    def __init__(self,parent,client,task_id,course_id):
        super().__init__(parent); self.client=client; self.task_id=task_id; self.course_id=course_id
        self.setWindowModality(Qt.NonModal)
        self.course=client.course(course_id,task_id); self.package=self.course['package']; self.drafts={}; self.dirty=set()
        self.study_record=client.study_state(task_id,course_id) if hasattr(client,'study_state') else {}
        self.study_positions={}; self.active_lesson=None; self.restoring_study=False; self._saved_study=None
        self.lesson_rows=ordered_lessons(self.package)
        self.setWindowTitle(self.course['title']+' · 开始学习'); self.resize(1400,860); self.setMinimumSize(800,580)
        layout=box(self,margins=(20,20,20,20),spacing=12)
        heading=box(horizontal=True); heading.addWidget(label(self.course['title'],'h2',True),1)
        self.reference_toggle=button('核心知识与答疑','outline',callback=self.open_reference); heading.addWidget(self.reference_toggle); self.reference_toggle.hide()
        self.tutor_toggle=button('收起答疑','soft',callback=self.toggle_tutor); heading.addWidget(self.tutor_toggle); layout.addLayout(heading)
        if hasattr(parent,'improve'):
            heading.insertWidget(1,button('改进课程','outline',callback=lambda:parent.improve(self.course_id)))
        layout.addWidget(label('课程草稿 · 结构检查不代表教学质量验收；阅读或查看答案不会自动完成任务。','muted',True))
        self.chapter_choice=QComboBox(); self.chapter_choice.setAccessibleName('当前章节'); self.chapter_choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon); self.chapter_choice.setMinimumContentsLength(12)
        for lesson in self.lesson_rows: self.chapter_choice.addItem(lesson['title'])
        layout.addWidget(self.chapter_choice); self.chapter_choice.hide()
        self.study_split=QSplitter(Qt.Horizontal); self.study_split.setChildrenCollapsible(False); layout.addWidget(self.study_split,1)
        self.tabs=QTabWidget(); tabs=self.tabs; tabs.setMinimumWidth(360); self.study_split.addWidget(tabs)
        self.reference_split=QSplitter(Qt.Vertical); self.reference_split.setChildrenCollapsible(False)
        self.reference_split.setHandleWidth(8); self.study_split.addWidget(self.reference_split)
        self.knowledge=KnowledgePanel(self.package); self.reference_split.addWidget(self.knowledge)
        self.tutor=TutorPanel(client,task_id,course_id); self.reference_split.addWidget(self.tutor)
        self.reference_split.setStretchFactor(0,0); self.reference_split.setStretchFactor(1,1)
        self.reference_split.setSizes([250,430]); self._reference_sizes=None
        self.study_split.setStretchFactor(0,1); self.study_split.setStretchFactor(1,0); self.study_split.setSizes([950,370])
        self.tutor.quote_requested.connect(self.quote_selection)
        self.knowledge.quote_requested.connect(self.quote_keypoint)
        self.tutor_toggle.setToolTip('收起答疑后，核心知识仍保持显示。')
        self.path_page=None
        if self.package['input'].get('mode') == SUBJECT_MODE:
            self.path_page,path_content=scroll_body(); tabs.addTab(self.path_page,'学习路径')
            titles={lesson['id']:lesson['title'] for lesson in self.lesson_rows}
            lesson_map={lesson['id']:lesson for lesson in self.lesson_rows}
            path_content.addWidget(label('按先修关系，从基础走向进阶','h2'))
            path_content.addWidget(label('必要基础已安排进课程；可从任意章节阅读，路径不会限制手动跳转。','muted',True))
            for index,stage in enumerate(self.package['learning_path'],1):
                path_content.addWidget(line()); path_content.addWidget(label(f"阶段 {index} · "+stage['title'],'h2',True))
                path_content.addWidget(label(stage['summary'],wrap=True))
                for lesson in self.lesson_rows:
                    if lesson['id'] not in stage['lesson_ids']: continue
                    path_content.addWidget(button(lesson['title'],'outline',callback=lambda checked=False,lid=lesson['id']:self.open_lesson(lid)))
                    parents=lesson_map[lesson['id']].get('depends_on',[])
                    path_content.addWidget(label('先学：'+'、'.join(titles[p] for p in parents) if parents else '可从这里开始','muted',True))
        self.course_page=QSplitter(); split=self.course_page; tabs.addTab(split,'课程与练习')
        chapter_nav=QWidget(); self.chapter_nav=chapter_nav; chapter_nav.setMinimumWidth(210); chapter_nav.setMaximumWidth(280)
        chapters=box(chapter_nav,spacing=10); chapters.addWidget(label(f"章节 · {len(self.package['lessons'])}",'section'))
        self.lessons=QListWidget(); self.lessons.setWordWrap(True); self.lessons.setAccessibleName('课程章节'); chapters.addWidget(self.lessons,1); split.addWidget(chapter_nav)
        self.scroll,self.body=scroll_body(); split.addWidget(self.scroll); split.setStretchFactor(1,1)
        for index,lesson in enumerate(self.lesson_rows,1): self.lessons.addItem(f"{index:02d} · "+lesson['title'])
        knowledge,content=scroll_body(); tabs.addTab(knowledge,'知识库与来源')
        unit_titles={unit['id']:unit['title'] for unit in self.package['knowledge_units']}
        if self.package['input'].get('mode') == SUBJECT_MODE:
            content.addWidget(label('知识覆盖范围','h2'))
            for area in self.package['coverage']:
                content.addWidget(label(area['title'],'section',True))
                if area['knowledge_unit_ids']: content.addWidget(label('已纳入：'+'、'.join(unit_titles[uid] for uid in area['knowledge_unit_ids']),wrap=True))
                for gap in area['gaps']: content.addWidget(label('待补充：'+gap,'muted',True))
            content.addWidget(line())
        for unit in self.package['knowledge_units']:
            content.addWidget(label(unit['title'],'h2',True)); content.addWidget(label(unit['scope'],wrap=True))
            if unit['prerequisite_ids']: content.addWidget(label('先修单元：'+'、'.join(unit['prerequisite_ids']),'muted',True))
            for claim in unit['claims']:
                value=claim if isinstance(claim,str) else (claim.get('statement','')+'\n适用条件：'+claim.get('conditions','')+'\n证据定位：'+claim.get('location',''))
                content.addWidget(label(value,wrap=True))
            content.addWidget(label('来源：'+'、'.join(unit['source_ids']),'muted',True))
        for source in self.package['sources']:
            content.addWidget(line()); content.addWidget(label(source['title'],'section',True))
            content.addWidget(label('\n'.join(source[k] for k in ('author_or_institution','locator','version','section'))+'\n访问状态：'+source['access_status'],wrap=True))
        review,content=scroll_body(); tabs.addTab(review,'核查与缺口')
        for message in self.package['unresolved']: content.addWidget(label('• '+message,wrap=True))
        content.addWidget(label(self.course['review'],wrap=True))
        footer=box(horizontal=True)
        self.notice=label('作答自动保存；书面题请对照参考过程自查。','muted',True); footer.addWidget(self.notice,1)
        footer.addWidget(button('关闭课程','outline',callback=self.accept)); layout.addLayout(footer)
        self.debounce=QTimer(self); self.debounce.setSingleShot(True); self.debounce.setInterval(500); self.debounce.timeout.connect(self.save_answers)
        self.study_timer=QTimer(self); self.study_timer.setSingleShot(True); self.study_timer.setInterval(900); self.study_timer.timeout.connect(self.save_study)
        self.restore_timer=QTimer(self); self.restore_timer.setSingleShot(True); self.restore_timer.timeout.connect(self.restore_position)
        self.scroll.verticalScrollBar().valueChanged.connect(self.mark_study)
        self.tutor.question.textChanged.connect(self.mark_study)
        self.lessons.currentRowChanged.connect(self.show_lesson)
        previous=next((i for i,row in enumerate(self.lesson_rows) if row['id']==self.study_record.get('lesson_id')),0)
        if self.study_record:
            self.study_positions[self.study_record['lesson_id']]=self.study_record.get('scroll_ratio',0)
            self.tutor.question.setPlainText(self.study_record.get('question',''))
            self.tabs.setCurrentWidget(self.course_page)
        self.lessons.setCurrentRow(previous)
        self.chapter_choice.currentIndexChanged.connect(self.choose_chapter)
        self.layout_timer=QTimer(self); self.layout_timer.setSingleShot(True); self.layout_timer.timeout.connect(self.adapt_reader)
        self.reference_dialog=QDialog(self); self.reference_dialog.setWindowTitle('核心知识与随课答疑'); self.reference_dialog.setWindowModality(Qt.NonModal); box(self.reference_dialog)
        self.adapt_reader()
        available=self.screen().availableGeometry(); self.resize(min(self.width(),available.width()),min(self.height(),available.height()))

    def choose_chapter(self, index):
        self.lessons.setCurrentRow(index); self.tabs.setCurrentWidget(self.course_page)

    def open_reference(self):
        if self.tutor.isHidden(): self.toggle_tutor()
        available=self.screen().availableGeometry()
        self.reference_dialog.resize(min(490,available.width()),min(800,available.height()))
        self.reference_dialog.show(); self.reference_split.show(); self.reference_dialog.raise_()

    def adapt_reader(self):
        if not hasattr(self,'reference_dialog'): return
        font_size=self.font().pixelSize()
        if font_size<1: font_size=round(self.font().pointSizeF()*self.logicalDpiY()/72)
        narrow=self.width()<round(960*max(1,font_size/13))
        self.reference_toggle.setVisible(narrow); self.tutor_toggle.setVisible(not narrow)
        self.chapter_nav.setVisible(self.width()>=round(1250*max(1,font_size/13)))
        self.chapter_choice.setVisible(self.chapter_nav.isHidden())
        if narrow:
            if self.reference_split.parentWidget()!=self.reference_dialog:
                self.reference_split.setParent(self.reference_dialog); self.reference_dialog.layout().addWidget(self.reference_split)
                self.reference_split.show()
        else:
            self.reference_dialog.hide()
            if self.reference_split.parentWidget()!=self.study_split:
                self.study_split.addWidget(self.reference_split); self.reference_split.show()
                self.study_split.setSizes([max(400,self.width()-430),390])

    def resizeEvent(self, event):
        super().resizeEvent(event); self.adapt_reader()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange,QEvent.StyleChange) and hasattr(self,'reference_dialog'):
            self.layout_timer.start(0)

    def selectable(self, text, name=None):
        widget=label(text,name,True); widget.setTextInteractionFlags(Qt.TextSelectableByMouse|Qt.TextSelectableByKeyboard)
        return widget

    def toggle_tutor(self):
        visible=self.tutor.isHidden()
        if not visible: self._reference_sizes=self.reference_split.sizes()
        self.tutor.setVisible(visible); self.tutor_toggle.setText('收起答疑' if visible else '展开答疑')
        if visible: self.reference_split.setSizes(self._reference_sizes or [250,430])

    def quote_keypoint(self, text):
        if self.tutor.isHidden(): self.toggle_tutor()
        self.tutor.set_excerpt(text); self.tutor.question.setFocus()

    def quote_selection(self):
        from PyQt5.QtWidgets import QLabel
        for widget in self.scroll.widget().findChildren(QWidget):
            if isinstance(widget,QLabel) and widget.hasSelectedText():
                self.tutor.set_excerpt(widget.selectedText()); return
            if isinstance(widget,QPlainTextEdit) and widget.isReadOnly() and widget.textCursor().hasSelection():
                self.tutor.set_excerpt(widget.textCursor().selectedText().replace('\u2029','\n')); return
        self.tutor.notice.setText('先在本章正文中选中需要解释的文字，再引用。')

    def open_lesson(self,lesson_id):
        if not self.save_answers(): return
        index=next(i for i,lesson in enumerate(self.lesson_rows) if lesson['id']==lesson_id)
        self.lessons.setCurrentRow(index); self.tabs.setCurrentWidget(self.course_page)

    def show_lesson(self,index):
        if index<0 or not self.save_answers(): return
        if self.active_lesson:
            self.capture_study_position(); self.save_study()
        self.restoring_study=True
        clear(self.body); lesson=self.lesson_rows[index]
        self.active_lesson=lesson
        self.chapter_choice.blockSignals(True); self.chapter_choice.setCurrentIndex(index); self.chapter_choice.blockSignals(False)
        self.tutor.set_lesson(lesson['id'],lesson['title'])
        self.knowledge.set_lesson(lesson)
        self.body.addWidget(self.selectable(lesson['title'],'h2'))
        guide=lesson.get('teaching',{})
        if isinstance(guide,dict):
            for key,title in [('question','这一章解决什么'),('bridge','从已知走向新知'),('takeaway','先记住这一点')]:
                if guide.get(key): self.body.addWidget(self.selectable(title+'\n'+guide[key]))
        for key,title in [('prerequisites','先修基础'),('goals','本课目标'),('scope','内容边界'),('pace','建议节奏')]:
            self.body.addWidget(self.selectable(title+'：'+lesson[key],'muted'))
        for section in lesson['sections']:
            self.body.addWidget(line()); self.body.addWidget(self.selectable(section['title'],'h2'))
            for block in section['blocks']: self.add_block(block)
        self.body.addWidget(line()); self.body.addWidget(label('独立练习','h2'))
        for question in lesson['questions']: self.add_question(lesson,question)
        self.restore_timer.start(0)

    def restore_position(self):
        if not self.active_lesson: return
        bar=self.scroll.verticalScrollBar(); bar.setValue(round(bar.maximum()*self.study_positions.get(self.active_lesson['id'],0)))
        self.restoring_study=False; self.mark_study()

    def capture_study_position(self):
        if self.active_lesson and not self.restoring_study:
            bar=self.scroll.verticalScrollBar()
            self.study_positions[self.active_lesson['id']]=bar.value()/bar.maximum() if bar.maximum() else 0

    def mark_study(self, *unused):
        if not self.restoring_study: self.study_timer.start()

    def save_study(self):
        self.study_timer.stop()
        if not self.active_lesson or not hasattr(self.client,'save_study'): return True
        self.capture_study_position(); lesson=self.active_lesson
        value=dict(task_id=self.task_id,course_id=self.course_id,lesson_id=lesson['id'],
                   lesson_revision=lesson['revision'],scroll_ratio=self.study_positions.get(lesson['id'],0),question=self.tutor.question.toPlainText())
        if value==self._saved_study: return True
        try:
            self.client.save_study(**value)
        except (ValueError,OSError) as exc: self.notice.setText('续读记录尚未保存：'+str(exc)); return False
        self._saved_study=value
        return True

    def add_block(self,block):
        kind=block['type']
        if kind=='diagram':
            self.body.addWidget(LessonDiagram(block))
        elif kind=='worked_example':
            self.body.addWidget(self.selectable(block['title'],'h2'))
            self.body.addWidget(self.selectable('已知条件与问题\n'+block['givens']))
            for index,step in enumerate(block['steps'],1): self.body.addWidget(self.selectable(f'{index:02d} · '+step))
            self.body.addWidget(self.selectable('回到问题\n'+block['result']))
            self.body.addWidget(self.selectable('核对结果\n'+block['check'],'muted'))
        elif kind in ('formula','code_example'):
            editor=QPlainTextEdit(block['text']); editor.setReadOnly(True); editor.setLineWrapMode(QPlainTextEdit.NoWrap)
            editor.setStyleSheet('font-family: monospace; background:#edf2f7; border-radius:8px; padding:8px;')
            editor.setFixedHeight(min(220,max(70,24*len(block['text'].splitlines())+25))); self.body.addWidget(editor)
        elif kind=='table':
            table=QTableWidget(len(block['rows']),len(block['headers'])); table.setHorizontalHeaderLabels(block['headers'])
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            for y,row in enumerate(block['rows']):
                for x,value in enumerate(row): table.setItem(y,x,QTableWidgetItem(value))
            table.resizeColumnsToContents(); table.horizontalHeader().setStretchLastSection(True)
            table.setMaximumHeight(min(280,60+36*len(block['rows']))); self.body.addWidget(table)
        elif kind=='self_check':
            self.body.addWidget(self.selectable('停下来想一想：'+block['prompt']))
            answer=self.selectable(block['answer'],'muted'); answer.hide()
            self.body.addWidget(button('展开 / 收起参考思路','linkButton',callback=lambda checked=False,w=answer:w.setVisible(w.isHidden()))); self.body.addWidget(answer)
        else:
            value='\n'.join('• '+v for v in block['items']) if kind=='list' else block['text']
            self.body.addWidget(self.selectable(value,'muted' if kind=='callout' else None))

    def add_question(self,lesson,q):
        group=QGroupBox('练习 · '+q['id']); content=box(group,margins=(14,16,14,14),spacing=10)
        level={'recognition':'起步 · 识别概念','application':'跟随 · 应用方法','transfer':'迁移 · 解释与辨析'}.get(q.get('level'))
        if level: content.addWidget(label(level,'section'))
        content.addWidget(self.selectable(q['prompt']))
        key=json.dumps([lesson['id'],lesson['revision'],q['id']],ensure_ascii=False)
        saved=self.course['answers'].get(key,'')
        if q['kind']=='choice':
            editor=QComboBox(); editor.addItem('请选择你的答案',None)
            editor.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon); editor.setMinimumContentsLength(12)
            for option in q['options']: editor.addItem(option['text'],option['id'])
            found=editor.findData(saved); editor.setCurrentIndex(found if found>=0 else 0)
            read=lambda:editor.currentData() or ''
            editor.currentIndexChanged.connect(lambda: self.mark(key,lesson,q,read()))
        else:
            editor=QTextEdit(); editor.setFixedHeight(100); editor.setPlainText(saved); read=editor.toPlainText
            editor.textChanged.connect(lambda: self.mark(key,lesson,q,read()))
        editor.setObjectName('answer_'+q['id']); content.addWidget(editor)
        if q.get('hint'):
            hint=self.selectable('提示：'+q['hint'],'muted'); hint.hide()
            content.addWidget(button('提示','linkButton',callback=lambda checked=False,w=hint:w.setVisible(w.isHidden()))); content.addWidget(hint)
        result=self.selectable('参考过程：\n'+q['reference_process']+'\n自查：'+q['self_check'],'muted'); result.hide()
        feedback=label('','muted',True); content.addWidget(feedback)
        def check():
            if q['kind']=='choice':
                value=read()
                if not value: feedback.setText('先选择一个答案，再核对。'); return
                feedback.setText('选项一致，请对照参考过程。' if value==q['correct'] else '选项不同，请检查条件与推理。')
            result.setVisible(result.isHidden())
        content.addWidget(button('核对选项与过程' if q['kind']=='choice' else '对照参考过程（自行检查）','outline',callback=check)); content.addWidget(result)
        self.body.addWidget(group)

    def mark(self,key,lesson,q,value):
        self.drafts[key]=dict(task_id=self.task_id,course_id=self.course_id,lesson_id=lesson['id'],question_id=q['id'],text=value)
        self.dirty.add(key); self.debounce.start()

    def save_answers(self):
        self.debounce.stop()
        try:
            for key in list(self.dirty):
                self.client.answer(**self.drafts[key]); self.course['answers'][key]=self.drafts[key]['text']; self.dirty.remove(key)
        except (ValueError,OSError) as exc: self.notice.setText('作答尚未保存：'+str(exc)); return False
        return True

    def done(self,code):
        if self.save_answers() and self.save_study(): super().done(code)

    def hideEvent(self,event):
        self.save_study()
        if hasattr(self,'reference_dialog'): self.reference_dialog.hide()
        super().hideEvent(event)
