"""Versioned, human-authored task catalog with no provider initialization."""

from collections.abc import Iterable
from importlib.resources import files

from .tasks import TaskDefinition

_CONTENT_FILES = ("weekend.json", "restaurant-order.json", "hotel-check-in.json")


class TaskCatalog:
    def __init__(self, tasks: Iterable[TaskDefinition] | None = None) -> None:
        if tasks is None:
            content = files("chatterpal").joinpath("learning", "content")
            tasks = (
                TaskDefinition.model_validate_json(
                    content.joinpath(filename).read_text(encoding="utf-8")
                )
                for filename in _CONTENT_FILES
            )
        self._tasks: dict[tuple[str, str], TaskDefinition] = {}
        self._current: dict[str, TaskDefinition] = {}
        for task in tasks:
            key = (task.task_id, task.content_version)
            if key in self._tasks:
                raise ValueError(f"Duplicate task version: {key}")
            self._tasks[key] = task
            # The caller's authored ordering selects the current version.
            self._current[task.task_id] = task

    def list_tasks(self) -> tuple[TaskDefinition, ...]:
        return tuple(self._current.values())

    def get_task(
        self, task_id: str, content_version: str | None = None
    ) -> TaskDefinition:
        if content_version is None:
            return self._current[task_id]
        return self._tasks[(task_id, content_version)]
