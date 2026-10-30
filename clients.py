"""UI-facing request adapters. Returned task dictionaries are snapshots."""
from pathlib import Path
from framework import api
from msgs.YomkMsgs import (TaskId, CreateTask, UpdateTask, TaskStatus, StepInput,
                          ChildTasks, TaskIssue, StepState, BindingInput, BindingIndex, FocusStart, FocusMinutes)
from msgs.YomkMsgs import GitInput, RepositoryIndex, LinearCredentials, LinearPull, LinearImport
from msgs.YomkMsgs import CodexRecommend, CodexBind, CodexCreate, ApplyTaskPlan, TaskOrder, MergeTask


def call(url, payload=None):
    response = api.request(url, payload)
    if response.status == api.ResStatus.eOk:
        return response.data
    if response.status == api.ResStatus.eInvalid:
        raise ValueError(response.msg)
    raise OSError(response.msg)


class TaskClient:
    def revision(self): return call("/TaskService/revision")
    @property
    def tasks(self): return call("/TaskService/list")
    @property
    def settings(self): return call("/TaskService/snapshot")["settings"]
    @property
    def sample(self): return call("/TaskService/snapshot")["sample"]
    @property
    def path(self): return Path(call("/TaskService/snapshot")["path"])

    def get(self, task_id):
        return call("/TaskService/get", TaskId(task_id)) if task_id else None

    def add(self, task): return call("/TaskService/create", CreateTask(task))
    def delete(self, task_id): return call("/FocusService/delete_task", TaskId(task_id))
    def reorder(self, task_ids): return call("/TaskService/reorder", TaskOrder(task_ids))
    def check_merge(self, task_id, parent_id, mode="child"): return call("/TaskService/check_merge", MergeTask(task_id, parent_id, mode))
    def merge(self, task_id, parent_id, mode="child"): return call("/TaskService/merge", MergeTask(task_id, parent_id, mode))
    def check_detach(self, task_id): return call("/TaskService/check_detach", TaskId(task_id))
    def detach(self, task_id): return call("/TaskService/detach", TaskId(task_id))
    def undo_state(self): return call('/TaskService/undo_state')
    def undo_structure(self, identifier): return call('/TaskService/undo_structure',{'id':identifier})
    def acknowledge_parent(self, task_id, expected): return call("/TaskService/acknowledge_parent", {"task_id":task_id, "expected":expected})
    def dependencies(self, task_id, deps): return call("/TaskService/dependencies", {"task_id":task_id,"depends_on":deps})
    def detach_linear(self, task_id): return call("/TaskService/detach_linear", TaskId(task_id))
    def update(self, task_id, **changes): return call("/TaskService/update", UpdateTask(task_id, changes))
    def set_status(self, task_id, status): return call("/TaskService/set_status", TaskStatus(task_id, status))
    def add_children(self, task_id, titles): return call("/TaskService/add_children", ChildTasks(task_id, titles))
    def apply_plan(self, task_id, steps, baseline): return call("/TaskService/apply_plan", ApplyTaskPlan(task_id, steps, baseline))
    def attach_issue(self, task_id, issue): return call("/TaskService/attach_issue", TaskIssue(task_id, issue))
    def add_step(self, task_id, title): return call("/TaskService/add_step", StepInput(task_id, title))
    def set_step(self, task_id, index, done): return call("/TaskService/set_step", StepState(task_id, index, done))
    def clear_samples(self): return call("/TaskService/clear")
    def bind(self, task_id, kind, url): return call("/BindingService/bind", BindingInput(task_id, kind, url))
    def remove_binding(self, task_id, index): return call("/BindingService/remove", BindingIndex(task_id, index))
    def resolve_binding(self, task_id, index): return call("/BindingService/resolve", BindingIndex(task_id, index))
    def bind_repository(self, task_id, location, kind="local"): return call("/GitService/bind", GitInput(task_id, location, kind))
    def remove_repository(self, task_id, index): return call("/GitService/remove", RepositoryIndex(task_id, index))
    def resolve_repository(self, task_id, index): return call("/GitService/resolve", RepositoryIndex(task_id, index))


class LinearClient:
    def configure(self, api_key, remember=False): return call("/LinearService/configure", LinearCredentials(api_key, remember))
    def forget(self): return call("/LinearService/forget")
    def connection(self): return call("/LinearService/connection")
    def pull(self, include_closed=False, cancel_event=None): return call("/LinearService/pull", LinearPull(include_closed, cancel_event))
    def import_issues(self, issue_ids): return call("/LinearService/import", LinearImport(issue_ids))


class CodexClient:
    def recommend(self, task_id, query="", include_archived=False, cancel_event=None):
        return call("/CodexService/recommend", CodexRecommend(task_id, query, include_archived, cancel_event))
    def bind(self, task_id, thread_id): return call("/CodexService/bind", CodexBind(task_id, thread_id))
    def create(self, task_id, cwd): return call("/CodexService/create", CodexCreate(task_id, cwd))


class FocusClient:
    def snapshot(self): return call("/FocusService/snapshot")
    def start(self, task_id=None): return call("/FocusService/start", FocusStart(task_id))
    def pause(self): return call("/FocusService/pause")
    def reset(self): return call("/FocusService/reset")
    def tick(self): return call("/FocusService/tick")
    def set_duration(self, minutes): return call("/FocusService/set_duration", FocusMinutes(minutes))
    def clear_selection(self): return call("/FocusService/clear_selection")


class DailyClient:
    def report_text(self,day):return call('/DailyService/report_text',{'day':day})
    def retention_preview(self,cancel):return call('/DailyService/retention_preview',{'cancel':cancel})
    def retention_apply(self,identifier,cancel):return call('/DailyService/retention_apply',{'id':identifier,'cancel':cancel})
    def snapshot(self): return call("/DailyService/snapshot")
    def configure(self, **settings): return call("/DailyService/configure", settings)
    def retry(self): return call("/DailyService/retry")
    def choices(self, team_id=""): return call("/DailyService/choices", {"team_id":team_id})


class LearningClient:
    def revision(self): return call("/LearningService/revision", {})
    def snapshot(self, task_id=None): return call('/LearningService/snapshot', {'task_id':task_id})
    def catalog(self): return call('/LearningService/catalog', {})
    def bind(self, task_id, course_id): return call('/LearningService/bind', {'task_id':task_id,'course_id':course_id})
    def start(self, task_id, subject, concurrency=1):
        return call('/LearningService/start', dict(task_id=task_id,subject=subject,concurrency=concurrency))
    def cancel(self, job_id): return call('/LearningService/cancel', {'job_id':job_id})
    def course(self, course_id, task_id): return call('/LearningService/course', {'course_id':course_id,'task_id':task_id})
    def answer(self, **answer): return call('/LearningService/answer', answer)
    def study_state(self, task_id, course_id): return call('/LearningService/study_state',dict(task_id=task_id,course_id=course_id))
    def save_study(self, **value): return call('/LearningService/save_study',value)
    def tutor_snapshot(self, task_id, course_id):
        return call('/LearningService/tutor_snapshot', dict(task_id=task_id,course_id=course_id))
    def tutor_ask(self, task_id, course_id, lesson_id, question, excerpt=''):
        return call('/LearningService/tutor_ask', dict(task_id=task_id,course_id=course_id,
                    lesson_id=lesson_id,question=question,excerpt=excerpt))
    def tutor_cancel(self, task_id, course_id):
        return call('/LearningService/tutor_cancel', dict(task_id=task_id,course_id=course_id))
