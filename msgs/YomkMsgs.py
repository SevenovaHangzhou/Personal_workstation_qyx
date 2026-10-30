"""Explicit request messages. Services still validate all fields at runtime."""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class TaskId:
    task_id: str


@dataclass(frozen=True)
class CreateTask:
    task: dict


@dataclass(frozen=True)
class UpdateTask:
    task_id: str
    changes: dict


@dataclass(frozen=True)
class TaskStatus:
    task_id: str
    status: str


@dataclass(frozen=True)
class TaskOrder:
    task_ids: list


@dataclass(frozen=True)
class MergeTask:
    task_id: str
    parent_id: str
    mode: str = "child"


@dataclass(frozen=True)
class StepInput:
    task_id: str
    title: str


@dataclass(frozen=True)
class StepState:
    task_id: str
    index: int
    done: bool


@dataclass(frozen=True)
class BindingInput:
    task_id: str
    kind: str
    url: str


@dataclass(frozen=True)
class BindingIndex:
    task_id: str
    index: int


@dataclass(frozen=True)
class AppendBinding:
    task_id: str
    binding: dict


@dataclass(frozen=True)
class FocusStart:
    task_id: Optional[str] = None


@dataclass(frozen=True)
class FocusMinutes:
    minutes: int


@dataclass(frozen=True)
class FocusCredit:
    task_id: str
    seconds: int


@dataclass(frozen=True)
class GitInput:
    task_id: str
    location: str
    kind: str = "local"


@dataclass(frozen=True)
class RepositoryInput:
    task_id: str
    repository: dict


@dataclass(frozen=True)
class RepositoryIndex:
    task_id: str
    index: int


@dataclass(frozen=True)
class LinearCredentials:
    api_key: str = field(repr=False)
    remember: bool = False


@dataclass(frozen=True)
class LinearPull:
    include_closed: bool = False
    cancel_event: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class LinearImport:
    issue_ids: list


@dataclass(frozen=True)
class ImportIssues:
    issues: list


@dataclass(frozen=True)
class CodexRecommend:
    task_id: str
    query: str = ""
    include_archived: bool = False
    cancel_event: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class CodexBind:
    task_id: str
    thread_id: str


@dataclass(frozen=True)
class CodexCreate:
    task_id: str
    cwd: str


@dataclass(frozen=True)
class ChildTasks:
    task_id: str
    titles: list


@dataclass(frozen=True)
class ApplyTaskPlan:
    task_id: str
    steps: list
    baseline: list


@dataclass(frozen=True)
class TaskIssue:
    task_id: str
    issue: dict
