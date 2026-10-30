"""Yomk-owned course catalog, explicit task bindings, jobs and private answers."""
import copy
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from services.base import MomoService, ok, require
from learning_content import (BUILTIN_COURSE_ID, SUBJECT_MODE, combine_legacy_course,
                              legacy_catalog, ordered_lessons, validate_package)
from learning_runner import CourseRunner, ROOT
from learning_tutor import LearningTutor, course_context

ACTIVE = {'queued','running'}


def atomic_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temp = path.with_suffix('.tmp')
    with temp.open('w',encoding='utf-8') as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp,path)


class LearningService(MomoService):
    def __init__(self, server, folder, tasks, runner=None):
        super().__init__(server); self.set_name('/LearningService')
        self.folder = Path(folder); self.tasks = tasks; self.runner = runner or CourseRunner()
        self.workers = {}; self.stopping = False
        self.path = self.folder/'state.json'
        self.state = {'version':1,'bindings':{},'jobs':{},'answers':{},'binding_versions':{},'concurrency':1,'revision':0}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            require(isinstance(saved,dict) and saved.get('version')==1 and all(isinstance(saved.get(k),dict) for k in ('bindings','jobs','answers')), '学习记录无效，原文件已保留')
            require(type(saved.get('concurrency')) is int and 1<=saved['concurrency']<=32, '学习并发设置无效')
            self.state = saved
            self.state.setdefault('binding_versions',{})
        self.courses = legacy_catalog(ROOT/'assets/learning/zhixing.json')
        self.legacy_chapter_ids = set(self.courses)
        self.courses[BUILTIN_COURSE_ID] = combine_legacy_course(self.courses)
        for path in sorted((self.folder/'courses').glob('*.json')):
            course = json.loads(path.read_text(encoding='utf-8')); validate_package(course['package'])
            self.courses[course['id']] = course
        for job in self.state['jobs'].values():
            if job['status'] in ACTIVE:
                job.update(status='interrupted',message='上次应用退出，生成已中断；日志和草稿保留，可确认输入后重新创建。')
        self._save()
        self.tutor = LearningTutor(self.folder/'tutor', atomic_json)

    def _save(self):
        self.state['revision'] += 1
        atomic_json(self.path,self.state)

    def _mutate(self, operation):
        before = copy.deepcopy(self.state)
        try:
            result = operation(); self._save(); return result
        except Exception:
            self.state = before; raise

    def init(self):
        for endpoint in ('revision','snapshot','catalog','bind','start','cancel','course','answer',
                         'tutor_snapshot','tutor_ask','tutor_cancel'):
            self.install('/'+endpoint,getattr(self,endpoint),'dict','学习课程、后台生成与作答：'+endpoint)
        self.alive = True
        return 0

    def _task(self, task_id):
        task = self.tasks.get(task_id)
        require(task and task['category']=='learn', '只有学习任务可以绑定或创建课程')
        return task

    def revision(self, pkg):
        return ok({"revision":self.state["revision"],"busy":any(j["status"] in ACTIVE for j in self.state["jobs"].values()) or self.tutor.busy()})

    def snapshot(self, pkg):
        task_id = pkg.get('task_id') if isinstance(pkg,dict) else None
        jobs = [j for j in self.state['jobs'].values() if task_id is None or j['input']['task_id']==task_id]
        course_id = self.state['bindings'].get(task_id)
        course = self.courses.get(course_id)
        return ok({'revision':self.state['revision'],'bindings':self.state['bindings'],
            'course':{k:course[k] for k in ('id','title','origin')} if course else None,
            'jobs':jobs,'busy':any(j['status'] in ACTIVE for j in jobs),
            'concurrency':self.state['concurrency'],'folder':str(self.folder)})

    def catalog(self, pkg):
        return ok([{**{k:c[k] for k in ('id','title','origin')},
                    'lesson_count':len(c['package']['lessons']),
                    'question_count':sum(len(lesson['questions']) for lesson in c['package']['lessons'])}
                   for c in self.courses.values() if c['id'] not in self.legacy_chapter_ids])

    def bind(self, pkg):
        require(isinstance(pkg,dict) and set(pkg)=={'task_id','course_id'}, '无效课程绑定')
        self._task(pkg['task_id']); course_id = pkg['course_id']
        require(course_id is None or isinstance(course_id,str) and course_id in self.courses, '课程不存在')
        def write():
            versions=self.state['binding_versions']; versions[pkg['task_id']]=versions.get(pkg['task_id'],0)+1
            if course_id is None: self.state['bindings'].pop(pkg['task_id'],None)
            else: self.state['bindings'][pkg['task_id']] = course_id
        self._mutate(write)
        return self.snapshot({'task_id':pkg['task_id']})

    def start(self, pkg):
        require(isinstance(pkg,dict) and set(pkg)=={'task_id','subject','concurrency'}, '无效建课输入')
        task = self._task(pkg['task_id'])
        require(not self.stopping, '学习服务正在停止')
        require(not any(j['input']['task_id']==task['id'] and j['status'] in ACTIVE for j in self.state['jobs'].values()), '此任务已有课程正在生成')
        require(isinstance(pkg['subject'],str) and 1<=len(pkg['subject'].strip())<=6000, '请填写要学习的对象')
        require(type(pkg['concurrency']) is int and 1<=pkg['concurrency']<=32, '课程生成并发数为 1–32')
        identifier = str(uuid.uuid4())
        inputs = {'mode':SUBJECT_MODE,'subject':pkg['subject'].strip()}
        inputs.update(task_id=task['id'],title=task['title'],description=task['description'],note=task['note'],input_version=identifier)
        job = {'id':identifier,'input':inputs,'status':'queued','message':'等待后台生成课程',
               'created_at':datetime.now(timezone.utc).isoformat(),'thread_id':'',
               'binding_before':self.state['bindings'].get(task['id']), 'binding_version':self.state['binding_versions'].get(task['id'],0), 'course_id':None}
        def write():
            self.state['concurrency']=pkg['concurrency']; self.state['jobs'][identifier]=job
        self._mutate(write); self._pump()
        return ok(self.state['jobs'][identifier])

    def _pump(self):
        if self.stopping: return
        for job in self.state['jobs'].values():
            if len(self.workers)>=self.state['concurrency']: break
            if job['status']!='queued': continue
            job.update(status='running',message='正在启动系统 Codex CLI'); self._save()
            cancel = threading.Event()
            thread = threading.Thread(target=self._run,args=(copy.deepcopy(job),cancel),name='orbit-course-'+job['id'][:8],daemon=False)
            self.workers[job['id']] = (thread,cancel)
            try: thread.start()
            except Exception:
                self.workers.pop(job['id'],None); job.update(status='failed',message='后台线程未启动'); self._save(); raise

    def _run(self, job, cancel):
        identifier = job['id']; folder = self.folder/'generations'/identifier
        def progress(message,thread_id=''):
            with self.lock:
                current = self.state['jobs'][identifier]
                current['message'] = message[:500]
                if thread_id and not current['thread_id']:
                    # A generated chat is bound to its confirmed originating task only.
                    try:
                        self._task(job['input']['task_id'])
                        self.tasks.bind(job['input']['task_id'],'codex','codex://threads/'+thread_id)
                    except (ValueError,OSError): pass
                    current['thread_id'] = thread_id
                self._save()
        try:
            folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            atomic_json(folder/"input.json",job["input"])
            result = self.runner.run(job,folder,cancel,progress)
            atomic_json(folder/"result.json",result)
            (folder/"review.md").write_text(result["review"],encoding="utf-8")
            if cancel.is_set(): raise InterruptedError('已停止课程生成，日志和草稿已保留')
            if result['status']=='needs_input':
                require(bool(result['message'].strip()), 'Codex 未给出需补充的信息')
                with self.lock:
                    self.state['jobs'][identifier].update(status='needs_input',message=result['message']); self._save()
                return
            progress('正在检查知识覆盖、先修关系与学习路径')
            package = json.loads(result['course_json'])
            atomic_json(folder/'course-package.json',package)
            validate_package(package,job)
            package['lessons'] = ordered_lessons(package)
            if package['input'].get('mode') == SUBJECT_MODE:
                atomic_json(folder/'knowledge-base.json', {key:package[key] for key in
                    ('input','search_queries','sources','coverage','knowledge_units','unresolved')})
                atomic_json(folder/'learning-path.json', {
                    'stages':package['learning_path'],
                    'lessons':[{key:lesson[key] for key in ('id','title','knowledge_unit_ids','depends_on')}
                               for lesson in package['lessons']]})
            course_id = 'generated:'+identifier
            course = {'id':course_id,'title':job['input'].get('subject') or job['input']['title'],'origin':'此任务生成 · 待审阅',
                      'package':package,'review':result['review']}
            with self.lock:
                if cancel.is_set(): raise InterruptedError('已停止课程生成，草稿已保留')
                atomic_json(self.folder/'courses'/(identifier+'.json'),course)
                self.courses[course_id] = course
                task = self.tasks.get(job['input']['task_id'])
                # Preserve newer user choices and changed task context.
                same = task and task['category']=='learn' and all(task[k]==job['input'][k] for k in ('title','description','note'))
                if (same and self.state['bindings'].get(task['id'])==job['binding_before']
                        and self.state['binding_versions'].get(task['id'],0)==job.get('binding_version',0)):
                    self.state['bindings'][task['id']] = course_id
                    message = '课程草稿已生成并绑定，请阅读并核查教学内容'
                else: message = '课程草稿已保留；任务或绑定发生变化，请明确选择后绑定'
                self.state['jobs'][identifier].update(status='review',message=message,course_id=course_id)
                self._save()
        except Exception as exc:
            with self.lock:
                self.state['jobs'][identifier].update(status='cancelled' if isinstance(exc,InterruptedError) else 'failed',message=str(exc)[:1600])
                self._save()
        finally:
            with self.lock:
                self.workers.pop(identifier,None)
                self._pump()

    def cancel(self, pkg):
        require(isinstance(pkg,dict) and isinstance(pkg.get('job_id'),str), '无效生成记录')
        job = self.state['jobs'].get(pkg['job_id']); require(job, '没有找到生成记录')
        if job['status']=='queued':
            self._mutate(lambda:job.update(status='cancelled',message='已取消排队'))
        elif job['id'] in self.workers:
            self.workers[job['id']][1].set()
        return ok()

    def course(self, pkg):
        require(isinstance(pkg,dict) and isinstance(pkg.get('course_id'),str), '无效课程引用')
        course = self.courses.get(pkg['course_id']); require(course, '课程不存在')
        saved = self.state['answers'].get(pkg.get('task_id'),{})
        if course['id'] == BUILTIN_COURSE_ID or course['id'] in self.legacy_chapter_ids:
            # Old chapter bindings remain readable; all new answers share the whole course.
            answers = {}
            for chapter_id in self.legacy_chapter_ids: answers.update(saved.get(chapter_id,{}))
            answers.update(saved.get(BUILTIN_COURSE_ID,{}))
            keys = {json.dumps([lesson['id'],lesson['revision'],q['id']],ensure_ascii=False)
                    for lesson in course['package']['lessons'] for q in lesson['questions']}
            answers = {key:value for key,value in answers.items() if key in keys}
        else: answers = saved.get(course['id'],{})
        return ok({**course,'answers':answers})

    def answer(self, pkg):
        require(isinstance(pkg,dict) and set(pkg)=={'task_id','course_id','lesson_id','question_id','text'}, '无效作答')
        self._task(pkg['task_id']); course = self.courses.get(pkg['course_id']); require(course, '课程不存在')
        lesson = next((l for l in course['package']['lessons'] if l['id']==pkg['lesson_id']),None)
        require(lesson and any(q['id']==pkg['question_id'] for q in lesson['questions']), '题目不存在')
        require(isinstance(pkg['text'],str) and len(pkg['text'])<=20000, '答案过长')
        key = json.dumps([lesson['id'],lesson['revision'],pkg['question_id']],ensure_ascii=False)
        storage_id = BUILTIN_COURSE_ID if course['id'] in self.legacy_chapter_ids else course['id']
        def write():
            answers = self.state['answers'].setdefault(pkg['task_id'],{}).setdefault(storage_id,{})
            answers[key] = pkg['text']
        self._mutate(write); return ok()

    def _tutor_course(self, pkg):
        require(isinstance(pkg,dict), '无效学习问答请求')
        self._task(pkg.get('task_id'))
        course = self.courses.get(pkg.get('course_id')); require(course, '课程不存在')
        return course

    def tutor_snapshot(self, pkg):
        self._tutor_course(pkg)
        return ok(self.tutor.snapshot(pkg['task_id'],pkg['course_id']))

    def tutor_ask(self, pkg):
        require(isinstance(pkg,dict) and set(pkg)=={'task_id','course_id','lesson_id','question','excerpt'}, '无效学习问题')
        course = self._tutor_course(pkg)
        context = course_context(course,pkg['lesson_id'])
        return ok(self.tutor.ask(pkg['task_id'],context,pkg['question'],pkg['excerpt'],self.state['concurrency']))

    def tutor_cancel(self, pkg):
        self._tutor_course(pkg)
        return ok(self.tutor.cancel(pkg['task_id'],pkg['course_id']))

    def stop(self):
        self.tutor.stop()
        with self.lock:
            self.stopping = True
            workers = list(self.workers.values())
            for thread,cancel in workers: cancel.set()
            for job in self.state['jobs'].values():
                if job['status']=='queued': job.update(status='interrupted',message='应用退出，排队任务未启动；可重新创建')
            self._save()
        for thread,cancel in workers: thread.join()

    def deinit(self):
        self.stop(); super().deinit()
