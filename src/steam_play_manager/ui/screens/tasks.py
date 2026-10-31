import json
import logging
import shlex
from collections.abc import Callable, Iterable
from typing import Any, ClassVar

from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import DataTable, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from steam_play_manager import tasks
from steam_play_manager.models import TaskStatus
from steam_play_manager.tasks.task_runner import TaskRunner
from steam_play_manager.ui.screens.base import BaseOptionListScreen, BaseScreen, BaseSearchTableScreen
from steam_play_manager.ui.screens.common import ConfirmScreen, InfoScreen, ProgressLogScreen

logger = logging.getLogger(__name__)

_STATUS_MARKUP: dict[str, str] = {
    TaskStatus.PENDING.value: "[yellow]PENDING[/yellow]",
    TaskStatus.RUNNING.value: "[cyan]RUNNING[/cyan]",
    TaskStatus.COMPLETED.value: "[green]COMPLETED[/green]",
    TaskStatus.FAILED.value: "[red]FAILED[/red]",
}


def _status_markup(status: Any) -> str:
    return _STATUS_MARKUP.get(str(status), f"[dim]{status}[/dim]")


class TasksMenuScreen(BaseOptionListScreen):
    nav_label: str = "TASKS"

    def __init__(self) -> None:
        super().__init__()
        self.sub_title = "Task Queue Manager"

    def compose_body(self) -> Iterable[Widget]:
        queued = len(TaskRunner().list_tasks())
        summary = (
            f"[$text]Task Queue Manager[/] - [$warning]Select an action:[/]\n"
            f"[$text]Tasks:[/] [dim]{queued} queued[/dim]"
        )
        yield Static(summary, id="summary")
        yield OptionList(id="options_list")

    def get_options(self) -> list[Option | None]:
        return [
            Option(
                "[bold $text]>  ADD TASK[/]\n"
                "   [dim]Enqueue a new automation task with custom command and parameters[/dim]",
                id="opt_add",
            ),
            None,
            Option(
                "[bold $text]>  REMOVE TASK[/]\n   [dim]Search and remove an existing task from the queue[/dim]",
                id="opt_remove",
            ),
            None,
            Option(
                "[bold $text]>  RUN TASK[/]\n   [dim]Execute a specific task from the queue immediately[/dim]",
                id="opt_run",
            ),
            None,
            Option(
                "[bold $text]>  RUN PENDING TASKS[/]\n"
                "   [dim]Sequentially execute all pending tasks in the queue[/dim]",
                id="opt_run_pending",
            ),
            None,
            Option(
                "[$warning]>  RESET ALL TASKS[/]\n"
                "   [dim]Reset all completed and failed tasks back to pending state[/dim]",
                id="opt_reset_all",
            ),
        ]

    def on_option_selected(self, option_id: str | None, option_prompt: str) -> None:
        if option_id == "opt_add":
            self.app.push_screen(AddTaskScreen())
        elif option_id == "opt_remove":
            self.app.push_screen(TasksTableScreen("Remove Task", self._confirm_remove))
        elif option_id == "opt_run":
            self.app.push_screen(TasksTableScreen("Run Task", self._run_any_task))
        elif option_id == "opt_run_pending":
            self.app.push_screen(
                ConfirmScreen(
                    "Run all PENDING tasks in the queue?",
                    lambda: self.app.push_screen(ProgressLogScreen("Tasks Running", lambda: tasks.run_tasks_runner())),
                )
            )
        elif option_id == "opt_reset_all":
            self.app.push_screen(ConfirmScreen("Reset ALL tasks back to PENDING?", self._reset_all))

    def on_screen_resume(self) -> None:
        """Recompute the queued-task count after add/remove/reset/run flows."""
        super().on_screen_resume()
        queued = len(TaskRunner().list_tasks())
        summary = (
            f"[$text]Task Queue Manager[/] - [$warning]Select an action:[/]\n"
            f"[$text]Tasks:[/] [dim]{queued} queued[/dim]"
        )
        self.query_one("#summary", Static).update(summary)

    def _reset_all(self) -> None:
        self.app.pop_screen()
        self.app.push_screen(ProgressLogScreen("Resetting Tasks", lambda: TaskRunner().reset_tasks()))

    def _run_any_task(self, task: dict[str, Any]) -> None:
        """Run a task regardless of its current state (resetting it first if not pending)."""
        task_id = str(task.get("id"))
        self.app.pop_screen()

        def run() -> None:
            runner = TaskRunner()
            if task.get("status") != TaskStatus.PENDING:
                runner.reset_tasks(target_task_id=task_id)
            runner.run_tasks(target_task_id=task_id)

        self.app.push_screen(ProgressLogScreen("Running Task", run))

    def _confirm_remove(self, task: dict[str, Any]) -> None:
        task_id = str(task.get("id"))
        self.app.push_screen(
            ConfirmScreen(f"Remove task [{task_id}] from the queue?", lambda: self._remove_task(task_id))
        )

    def _remove_task(self, task_id: str) -> None:
        self.app.pop_screen()
        self.app.push_screen(ProgressLogScreen("Removing Task", lambda: TaskRunner().remove_task(task_id)))


class TasksTableScreen(BaseSearchTableScreen):
    """Searchable table of the tasks queue; selecting a row invokes on_pick."""

    nav_label: str = "TASK LIST"

    def __init__(self, sub_title: str, on_pick: Callable[[dict[str, Any]], None]) -> None:
        super().__init__()
        self.sub_title = sub_title
        self.on_pick = on_pick

    def setup_columns(self, table: DataTable) -> None:
        table.add_columns("ID", "Command", "Status", "Executed At")

    def get_records(self) -> list[dict[str, Any]]:
        return [
            {
                "task_id": t.get("id", "?"),
                "command": t.get("command", ""),
                "status": _status_markup(t.get("status")),
                "executed_at": t.get("executed_at") or "[dim]—[/dim]",
                "task": t,
            }
            for t in TaskRunner().list_tasks()
        ]

    def match_record(self, record: dict[str, Any], filter_text: str) -> bool:
        ft = filter_text.lower()
        task = record.get("task", {})
        return (
            ft in str(record["task_id"]).lower()
            or ft in str(record["command"]).lower()
            or ft in str(task.get("status", "")).lower()
        )

    def add_record_row(self, table: DataTable, record: dict[str, Any]) -> None:
        table.add_row(
            str(record["task_id"]),
            record["command"],
            record["status"],
            record["executed_at"],
            key=str(record["task_id"]),
        )

    def on_row_selected(self, row_key: str | None) -> None:
        row = next((r for r in self.records if str(r["task_id"]) == str(row_key)), None)
        if row:
            self.on_pick(row["task"])


class AddTaskScreen(BaseScreen):
    """One-screen form to enqueue a task: command, positional args, kwargs."""

    nav_label: str = "ADD TASK"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+s", "submit", "Add Task"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.sub_title = "Add Task"
        self._command: str | None = None

    def compose_body(self) -> Iterable[Widget]:
        yield Static("[$text]Add Task[/] - [$warning]Configure a task to enqueue:[/]", id="summary")
        yield Static("[$text]Task Command[/]", id="cmd_label")
        yield Select(
            [(name, name) for name in tasks.TASK_REGISTRY],
            prompt="Select command...",
            id="cmd_select",
        )
        yield Input(placeholder="Positional args (space-separated), e.g. 570940 60", id="args_input")
        yield Input(placeholder='Keyword args (JSON), e.g. {"tolerance": 60}', id="kwargs_input")
        yield Static("[$text-muted]Ctrl+S to add · Esc to cancel[/]", id="hint")

    def on_mount(self) -> None:
        self.query_one(Select).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        self._command = str(event.value)

    def action_submit(self) -> None:
        command = self._command
        if not command:
            self._show_error("Select a task command first.")
            return
        args_text = self.query_one("#args_input", Input).value.strip()
        kwargs_text = self.query_one("#kwargs_input", Input).value.strip()

        args = shlex.split(args_text) if args_text else []
        kwargs: dict[str, Any] = {}
        if kwargs_text:
            try:
                kwargs = json.loads(kwargs_text)
            except json.JSONDecodeError as e:
                self._show_error(f"Invalid kwargs JSON: {e}")
                return
        if not isinstance(kwargs, dict):
            self._show_error('kwargs must be a JSON object, e.g. {"key": value}.')
            return

        task_id = TaskRunner().add_task(command, args, kwargs)
        self.app.pop_screen()
        self.app.push_screen(InfoScreen(f"Added task [{task_id}]: {command}"))

    def _show_error(self, message: str) -> None:
        self.query_one("#hint", Static).update(f"[red]{message}[/red]")
