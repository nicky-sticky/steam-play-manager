from enum import StrEnum
from typing import Any, NotRequired, TypedDict


class TaskStatus(StrEnum):
    """Lifecycle states of a task in tasks.json."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Task(TypedDict):
    """A single entry in tasks.json."""

    id: str
    command: str
    args: list[str]
    kwargs: dict[str, Any]
    status: TaskStatus
    executed_at: NotRequired[str]


class TaskFile(TypedDict):
    """Top-level structure of tasks.json."""

    tasks: list[Task]
