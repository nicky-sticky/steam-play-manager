import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from steam_play_manager.core import config
from steam_play_manager.core.manifests import load_manifest
from steam_play_manager.core.utils import read_json, write_json
from steam_play_manager.models import (
    IdleCheckResult,
    IdleManifest,
    Task,
    TaskFile,
    TaskStatus,
    UnlockCheckResult,
    UnlockManifest,
)
from steam_play_manager.tasks.operations import (
    run_check_manifest_ach,
    run_check_manifest_idle,
    run_idle_game,
    run_idle_manifest,
    run_lock_all_game_ach,
    run_lock_game_ach,
    run_lock_manifest_ach,
    run_reset_game_stat,
    run_sync_lib,
    run_toggle_game_ach,
    run_unlock_all_game_ach,
    run_unlock_game_ach,
    run_unlock_manifest_ach,
    run_update_game_stat,
)

logger = logging.getLogger(__name__)


def _cmd_arg(kwargs: dict[str, Any], pos_args: list[Any], key: str, index: int | None = None) -> Any:
    """Resolve a command argument from kwargs, falling back to positional args."""
    value = kwargs.get(key)
    if value:
        return value
    if index is not None and len(pos_args) > index:
        return pos_args[index]
    return None


# --- Command Handler Table ---


TASK_HANDLERS: dict[str, Callable[["TaskRunner", list[Any], dict[str, Any]], Any]] = {
    # 1. Library
    "sync_lib": lambda self, p, k: run_sync_lib(target_app_id=_cmd_arg(k, p, "app_id", 0)),
    # 2. Game Idler Operations
    "idle_game": lambda self, p, k: run_idle_game(_cmd_arg(k, p, "app_id", 0), _cmd_arg(k, p, "seconds", 1)),
    # 3. Game Achievement Operations
    "toggle_game_ach": lambda self, p, k: run_toggle_game_ach(
        _cmd_arg(k, p, "app_id", 0), _cmd_arg(k, p, "achievement_id", 1)
    ),
    "unlock_game_ach": lambda self, p, k: run_unlock_game_ach(
        _cmd_arg(k, p, "app_id", 0), _cmd_arg(k, p, "achievement_id", 1)
    ),
    "lock_game_ach": lambda self, p, k: run_lock_game_ach(
        _cmd_arg(k, p, "app_id", 0), _cmd_arg(k, p, "achievement_id", 1)
    ),
    "unlock_all_game_ach": lambda self, p, k: run_unlock_all_game_ach(_cmd_arg(k, p, "app_id", 0)),
    "lock_all_game_ach": lambda self, p, k: run_lock_all_game_ach(_cmd_arg(k, p, "app_id", 0)),
    # 4. Game Statistics Operations
    "update_game_stat": lambda self, p, k: run_update_game_stat(
        _cmd_arg(k, p, "app_id", 0), _cmd_arg(k, p, "stats_json", 1)
    ),
    "reset_game_stat": lambda self, p, k: run_reset_game_stat(_cmd_arg(k, p, "app_id", 0)),
    # 5. Manifest Operations
    "idle_manifest": lambda self, p, k: self._handle_idle_manifest(p, k),
    "check_manifest_idle": lambda self, p, k: self._handle_check_manifest_idle(p, k),
    "unlock_manifest_ach": lambda self, p, k: self._handle_unlock_manifest_ach(p, k),
    "lock_manifest_ach": lambda self, p, k: self._handle_lock_manifest_ach(p, k),
    "check_manifest_ach": lambda self, p, k: self._handle_check_manifest_ach(p, k),
}


class TaskRunner:
    """Sequential file-based task queue runner for tasks.json."""

    def __init__(self, tasks_file: str | Path | None = None) -> None:
        self.tasks_path = Path(tasks_file).resolve() if tasks_file else config.settings.tasks_file_path

    # --- Task Queue Processing ---

    def list_tasks(self) -> list[Task]:
        """Return the current tasks from the queue file (empty on missing or invalid file)."""
        tasks_file = self.tasks_path
        if not tasks_file.exists():
            logger.error(f"No tasks queue file found at {self.tasks_path}")
            return []

        data = read_json(tasks_file)
        if not data or "tasks" not in data:
            logger.error(f"Invalid tasks JSON format in {tasks_file}")
            return []

        return data.get("tasks", [])

    def reset_tasks(self, target_task_id: str | None = None) -> bool:
        """
        Reset tasks back to PENDING status in tasks.json.
        Args:
            target_task_id: Target a specific task ID. Resets all if None.
        Returns:
            True if the reset operation completed.
        """
        tasks_file = self.tasks_path
        if not tasks_file.exists():
            logger.error(f"No tasks queue file found at {self.tasks_path}")
            return False

        data = read_json(tasks_file)
        if not data or "tasks" not in data:
            logger.error(f"Invalid tasks JSON format in {tasks_file}")
            return False

        count = 0
        for t in data.get("tasks", []):
            if target_task_id and str(t.get("id")) != str(target_task_id):
                continue
            t["status"] = TaskStatus.PENDING
            t.pop("executed_at", None)
            count += 1

        write_json(tasks_file, data)
        target_str = f"task [{target_task_id}]" if target_task_id else f"{count} tasks"
        logger.info(f"Reset {target_str} to PENDING in {tasks_file.name}.")
        return True

    def add_task(self, command: str, args: list[str] | None = None, kwargs: dict[str, Any] | None = None) -> str:
        """Add a new task to the tasks.json file."""
        tasks_file = self.tasks_path
        data: TaskFile = read_json(tasks_file) or {"tasks": []}

        task_id = str(uuid.uuid4())[:8]
        new_task: Task = {
            "id": task_id,
            "command": command,
            "status": TaskStatus.PENDING,
            "args": args or [],
            "kwargs": kwargs or {},
        }
        tasks = data.get("tasks")
        if tasks is None:
            tasks = []
            data["tasks"] = tasks
        tasks.append(new_task)
        write_json(tasks_file, data)
        logger.info(f"Added task [{task_id}]: {command} to {tasks_file.name}")
        return task_id

    def clear_tasks(self) -> bool:
        """Clear all tasks from the tasks.json file."""
        tasks_file = self.tasks_path
        write_json(tasks_file, {"tasks": []})
        logger.info(f"Cleared all tasks in {tasks_file.name}")
        return True

    def remove_task(self, target_task_id: str) -> bool:
        """Remove a task from the tasks.json file by ID."""
        tasks_file = self.tasks_path
        if not tasks_file.exists():
            logger.error(f"No tasks queue file found at {self.tasks_path}")
            return False

        data = read_json(tasks_file)
        if not data or "tasks" not in data:
            logger.error(f"Invalid tasks JSON format in {tasks_file}")
            return False

        original_len = len(data["tasks"])
        data["tasks"] = [t for t in data["tasks"] if str(t.get("id")) != str(target_task_id)]

        if len(data["tasks"]) == original_len:
            logger.error(f"Task [{target_task_id}] not found in {tasks_file.name}")
            return False

        write_json(tasks_file, data)
        logger.info(f"Removed task [{target_task_id}] from {tasks_file.name}")
        return True

    def run_tasks(self, target_task_id: str | None = None) -> bool:
        """
        Execute pending tasks sequentially in tasks.json.
        Args:
            target_task_id: Target a specific task ID. Runs all pending if None.
        Returns:
            True if task execution finished.
        """
        tasks_file = self.tasks_path
        if not tasks_file.exists():
            logger.error(f"No tasks queue file found at {self.tasks_path}")
            return False

        data = read_json(tasks_file)
        if not data or "tasks" not in data:
            logger.error(f"Invalid tasks JSON format in {tasks_file}")
            return False

        tasks_list = data.get("tasks", [])
        stale = [t for t in tasks_list if t.get("status") == TaskStatus.RUNNING]
        if stale:
            for t in stale:
                logger.warning(
                    f"Task [{t.get('id', 'unknown')}] was interrupted while running - re-queuing as PENDING."
                )
                t["status"] = TaskStatus.PENDING
            write_json(tasks_file, data)

        pending_tasks = [
            t
            for t in tasks_list
            if t.get("status") == TaskStatus.PENDING and (not target_task_id or str(t.get("id")) == str(target_task_id))
        ]
        if not pending_tasks:
            logger.info(f"No pending tasks to execute in {tasks_file.name}.")
            return True

        logger.info(f"Processing {len(pending_tasks)} pending tasks from {tasks_file.name}...")

        for t in tasks_list:
            if t.get("status") != TaskStatus.PENDING:
                continue
            if target_task_id and str(t.get("id")) != str(target_task_id):
                continue

            task_id = t.get("id", "unknown")
            cmd = t.get("command")
            pos_args = t.get("args") if isinstance(t.get("args"), list) else []
            kwargs = t.get("kwargs") if isinstance(t.get("kwargs"), dict) else {}

            logger.info(f"Running task [{task_id}]: {cmd} (args: {pos_args}, kwargs: {kwargs})")
            t["status"] = TaskStatus.RUNNING
            write_json(tasks_file, data)

            success = self.execute_task(cmd, pos_args, kwargs)
            t["status"] = TaskStatus.COMPLETED if success else TaskStatus.FAILED
            t["executed_at"] = datetime.now(UTC).isoformat()
            write_json(tasks_file, data)

        logger.info(f"Task queue processing finished for {tasks_file.name}.")
        return True

    def execute_task(self, cmd: str, pos_args: list[Any], kwargs: dict[str, Any]) -> bool:
        """
        Execute a single task command with arguments and kwargs.
        Args:
            cmd: Command name.
            pos_args: Positional arguments list.
            kwargs: Keyword arguments dict.
        Returns:
            True if task execution succeeded.
        """
        handler = TASK_HANDLERS.get(cmd)
        if not handler:
            logger.error(f"Unknown command '{cmd}'")
            return False

        try:
            return bool(handler(self, pos_args, kwargs))
        except Exception as e:
            logger.exception(f"Error executing command '{cmd}': {e}")
            return False

    # --- Task Command Handlers ---

    def _resolve_manifest_games(
        self, pos_args: list[Any], kwargs: dict[str, Any], manifest_path: str | Path, model: type
    ) -> tuple[list[Any], str]:
        """Resolve games and target category label from manifest."""
        m_data = load_manifest(manifest_path, model=model)
        categories = m_data.categories
        all_games = m_data.games

        target_cat = kwargs.get("config") or (pos_args[0] if pos_args and isinstance(pos_args[0], str) else None)
        if target_cat and target_cat in categories:
            cat_ids = {str(i) for i in categories[target_cat]}
            games = [g for g in all_games if str(g.app_id) in cat_ids]
        else:
            games = all_games

        app_id = kwargs.get("app_id") or (
            pos_args[0] if pos_args and isinstance(pos_args[0], (int, str)) and str(pos_args[0]).isdigit() else None
        )
        if app_id:
            games = [g for g in games if str(g.app_id) == str(app_id)]
        return games, target_cat or "manifest"

    def _handle_idle_manifest(self, pos_args: list[Any], kwargs: dict[str, Any]) -> bool:
        """Run the idle manifest task."""
        manifest_p = kwargs.get("manifest") or config.settings.idle_manifest_path
        games, label = self._resolve_manifest_games(pos_args, kwargs, manifest_p, IdleManifest)
        return run_idle_manifest(games, label, tolerance_seconds=kwargs.get("tolerance"))

    def _handle_check_manifest_idle(self, pos_args: list[Any], kwargs: dict[str, Any]) -> IdleCheckResult | None:
        """Run the idle manifest status check task."""
        manifest_p = kwargs.get("manifest") or config.settings.idle_manifest_path
        games, label = self._resolve_manifest_games(pos_args, kwargs, manifest_p, IdleManifest)
        return run_check_manifest_idle(games, label, tolerance_seconds=kwargs.get("tolerance"))

    def _handle_unlock_manifest_ach(self, pos_args: list[Any], kwargs: dict[str, Any]) -> bool:
        """Run the unlock manifest achievements task."""
        manifest_p = kwargs.get("manifest") or config.settings.unlock_manifest_path
        games, label = self._resolve_manifest_games(pos_args, kwargs, manifest_p, UnlockManifest)
        return run_unlock_manifest_ach(
            games,
            label,
            target_apiname=kwargs.get("achievement_apiname"),
            ignore_timestamps=kwargs.get("ignore_timestamps", False),
        )

    def _handle_lock_manifest_ach(self, pos_args: list[Any], kwargs: dict[str, Any]) -> bool:
        """Run the lock manifest achievements task."""
        manifest_p = kwargs.get("manifest") or config.settings.unlock_manifest_path
        games, label = self._resolve_manifest_games(pos_args, kwargs, manifest_p, UnlockManifest)
        return run_lock_manifest_ach(games, label, target_apiname=kwargs.get("achievement_apiname"))

    def _handle_check_manifest_ach(self, pos_args: list[Any], kwargs: dict[str, Any]) -> UnlockCheckResult | None:
        """Run the unlock manifest status check task."""
        manifest_p = kwargs.get("manifest") or config.settings.unlock_manifest_path
        games, label = self._resolve_manifest_games(pos_args, kwargs, manifest_p, UnlockManifest)
        return run_check_manifest_ach(
            games,
            label,
            tolerance_seconds=kwargs.get("tolerance"),
            target_apiname=kwargs.get("achievement_apiname"),
        )


def run_tasks_runner(tasks_file: str | Path | None = None, target_task_id: str | None = None) -> bool:
    """Run pending tasks from specified or default tasks file."""
    return TaskRunner(tasks_file=tasks_file).run_tasks(target_task_id=target_task_id)
