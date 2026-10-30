"""Project filtered tasks into a collapsible outline without changing task data."""
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class OutlineRow:
    task: dict
    depth: int
    context: bool
    expanded: bool
    available_children: int


class TaskOutline:
    def __init__(self, tasks):
        self.tasks = tasks
        self.by_id = {task['id']: task for task in tasks}
        self.children = defaultdict(list)
        for task in tasks:
            self.children[task.get('parent_id')].append(task)

    def ancestors(self, task_id):
        result = []
        seen = {task_id}
        task = self.by_id.get(task_id)
        while task and task.get('parent_id') in self.by_id:
            parent_id = task['parent_id']
            if parent_id in seen:
                break
            seen.add(parent_id)
            task = self.by_id[parent_id]
            result.append(task)
        return list(reversed(result))

    def rows(self, matches, expanded=(), collapsed=(), reveal_matches=False):
        matched = {task['id'] for task in matches}
        included = set(matched)
        automatic = set()
        for task_id in matched:
            ancestors = self.ancestors(task_id)
            included.update(task['id'] for task in ancestors)
            if reveal_matches:
                automatic.update(task['id'] for task in ancestors)
            else:
                # A parent excluded by date/status/link filters provides context,
                # and must open far enough to expose a matching descendant.
                for index, task in enumerate(ancestors):
                    if task['id'] not in matched:
                        automatic.update(parent['id'] for parent in ancestors[:index + 1])
        opened = (set(expanded) | automatic) - set(collapsed)
        result = []

        def visit(task, depth):
            children = [child for child in self.children[task['id']] if child['id'] in included]
            is_open = bool(children) and task['id'] in opened
            result.append(OutlineRow(task, depth, task['id'] not in matched, is_open, len(children)))
            if is_open:
                for child in children:
                    visit(child, depth + 1)

        for task in self.tasks:
            if task['id'] in included and task.get('parent_id') not in included:
                visit(task, 0)
        return result
