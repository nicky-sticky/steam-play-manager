import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from steam_play_manager import tasks
from steam_play_manager.core import config
from steam_play_manager.core.auth import get_login_status, verify_steam_authentication
from steam_play_manager.core.logger import setup_logger
from steam_play_manager.core.manifests import get_category_games, load_manifest
from steam_play_manager.core.utils import enable_ansi_utf8
from steam_play_manager.models import IdleGame, IdleManifest, LoginMode, UnlockGame, UnlockManifest
from steam_play_manager.services.steam_utility import get_steam_utility_handler
from steam_play_manager.tasks.task_runner import TaskRunner

enable_ansi_utf8()

logger = logging.getLogger(__name__)


# --- Manifest Helpers ---


def _load_manifest(path: str | Path, model: type[Any]) -> Any:
    """Load a manifest, exiting with a failure code on error."""
    try:
        return load_manifest(path, model)
    except Exception as e:
        logger.error(f"Failed to load manifest: {e}")
        sys.exit(1)


def _find_unlock_game(app_id: str | int) -> UnlockGame | None:
    """Find a game in the unlock manifest by App ID."""
    for game in _load_manifest(config.settings.unlock_manifest_path, UnlockManifest).games:
        if game.app_id == int(app_id):
            return game
    return None


def _find_idle_game(app_id: str | int) -> IdleGame | None:
    """Find a game in the idle manifest by App ID."""
    for game in _load_manifest(config.settings.idle_manifest_path, IdleManifest).games:
        if game.app_id == int(app_id):
            return game
    return None


def _category_games(manifest_path: str | Path, model: type[Any], name: str) -> list[Any]:
    """Resolve the manifest games belonging to a category key."""
    manifest_data = _load_manifest(manifest_path, model)
    categories = manifest_data.categories
    if name not in categories:
        available = ", ".join(categories) or "none"
        logger.error(f"Category '{name}' not found in {Path(manifest_path).name}. Available categories: {available}")
        sys.exit(1)
    games = get_category_games(manifest_data, name)
    if not games:
        logger.error(f"Category '{name}' has no games in {Path(manifest_path).name}.")
        sys.exit(1)
    return games


# --- CLI Argument Parser Setup ---


def setup_parser() -> argparse.ArgumentParser:
    """Configure the subcommand structure: [SOURCE] [TOOL] [ACTION]"""
    parser = argparse.ArgumentParser(description="Steam Achievement, Idler & Stats Utility")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose (DEBUG) logging")

    sub = parser.add_subparsers(dest="source", required=True)

    # --- SOURCE: login ---
    sub.add_parser("login", help="Verify Steam auth status (client or agent mode)")

    # --- SOURCE: sync ---
    sync_p = sub.add_parser("sync", help="Sync Steam library to data/games.json")
    sync_p.add_argument("--app-id", type=int, help="Sync only a specific App ID")

    # --- SOURCE: tasks ---
    tasks_p = sub.add_parser("tasks", help="Manage the tasks.json queue")
    tasks_actions = tasks_p.add_subparsers(dest="action", required=True)

    tasks_actions.add_parser("list", help="List all tasks in the queue")
    tasks_actions.add_parser("clear", help="Clear all tasks from the queue")

    t_run = tasks_actions.add_parser("run", help="Execute pending tasks in tasks.json")
    t_run.add_argument("--task-id", type=str, help="Target a specific Task ID")

    t_reset = tasks_actions.add_parser("reset", help="Reset task status(es) back to PENDING")
    t_reset.add_argument("--task-id", type=str, help="Target a specific Task ID")

    t_queue = tasks_actions.add_parser("queue", help="Queue a task (unlock, idle, or sync)")
    t_queue.add_argument("mode", choices=["unlock", "idle", "sync"], help="Task type to queue")
    t_queue.add_argument(
        "app_id", type=int, nargs="?", help="Steam App ID (required for unlock/idle, optional for sync)"
    )
    t_queue.add_argument("--seconds", type=int, help="Idle duration in seconds (optional, defaults to manifest target)")
    t_queue.add_argument("-c", "--clear", action="store_true", help="Clear existing queue before adding")

    t_add = tasks_actions.add_parser("add", help="Add a raw custom task to the queue")
    t_add.add_argument("--command", required=True, help="Task command name (e.g. sync_lib, unlock_game_ach)")
    t_add.add_argument("--args", nargs="*", help="Positional arguments")
    t_add.add_argument("--kwargs", help="JSON string of keyword arguments")

    t_remove = tasks_actions.add_parser("remove", help="Remove a task from the queue by ID")
    t_remove.add_argument("task_id", help="Task ID to remove")

    # --- SOURCE: game ---
    game_p = sub.add_parser("game", help="Operations on a single game (--app-id required)")
    game_p.add_argument("--app-id", type=int, required=True, help="Steam App ID")

    g_tools = game_p.add_subparsers(dest="tool", required=True)

    # game -> unlocker
    g_unlocker = g_tools.add_parser("unlocker", help="Achievement unlock / lock / check operations")
    g_unlock_actions = g_unlocker.add_subparsers(dest="action", required=True)

    g_u_unlock = g_unlock_actions.add_parser("unlock", help="Unlock achievements")
    g_u_unlock.add_argument("--apiname", type=str, help="Unlock a single achievement by API name")
    g_u_unlock.add_argument("--all", action="store_true", help="Instantly unlock ALL achievements")

    g_u_lock = g_unlock_actions.add_parser("lock", help="Lock achievements")
    g_u_lock.add_argument("--apiname", type=str, help="Lock a single achievement by API name")
    g_u_lock.add_argument("--all", action="store_true", help="Instantly lock ALL achievements")

    g_u_toggle = g_unlock_actions.add_parser("toggle", help="Toggle a single achievement's lock state")
    g_u_toggle.add_argument("--apiname", required=True, help="Achievement API name")

    # game -> idler
    g_idler = g_tools.add_parser("idler", help="Playtime idling operations")
    g_idler_actions = g_idler.add_subparsers(dest="action", required=True)
    g_i_idle = g_idler_actions.add_parser("idle", help="Start idling")
    g_i_idle.add_argument("--seconds", type=int, help="Idle for a fixed duration")

    # game -> stats
    g_stats = g_tools.add_parser("stats", help="Game statistics operations")
    g_stats_actions = g_stats.add_subparsers(dest="action", required=True)
    g_stats_actions.add_parser("get", help="Get achievement & stat data")
    g_s_update = g_stats_actions.add_parser("update", help="Update game statistics")
    g_s_update.add_argument("--stats-json", required=True, help="JSON string or file path containing stats data")
    g_stats_actions.add_parser("reset", help="Reset all statistics for the game")

    # --- SOURCE: manifest ---
    man_p = sub.add_parser("manifest", help="Operations over games in manifest (all, category, or specific game)")
    man_target = man_p.add_mutually_exclusive_group()
    man_target.add_argument("-g", "--app-id", help="Target a specific game ID/name in manifest")
    man_target.add_argument("-c", "--category", help="Target games in a specific manifest category")
    man_target.add_argument("--all", action="store_true", help="Target all manifest games (default)")

    m_tools = man_p.add_subparsers(dest="tool", required=True)

    m_unlocker = m_tools.add_parser("unlocker", help="Achievement unlock (scheduled) & status check operations")
    m_unlock_actions = m_unlocker.add_subparsers(dest="action", required=True)
    m_u_unlock = m_unlock_actions.add_parser("unlock", help="Unlock achievements for target scope on a schedule")
    m_u_unlock.add_argument(
        "--ignore-timestamps",
        dest="ignore_timestamps",
        action="store_true",
        help="Ignore actual Steam unlock timestamps and run sequential manifest delays from zero",
    )
    m_unlock_actions.add_parser("status", help="Show achievement status (unlock delays vs actual Steam unlock times)")

    m_idler = m_tools.add_parser("idler", help="Playtime idling operations")
    m_idler_actions = m_idler.add_subparsers(dest="action", required=True)
    m_idler_actions.add_parser("idle", help="Start idling targeted games")
    m_idler_actions.add_parser("status", help="Show current vs desired playtime for targeted games")

    return parser


# --- Handlers & Execution ---


def _guard_code_prompt(message: str) -> str | None:
    """Interactive Steam Guard code entry for agent-mode login over the plain CLI."""
    code = input(f"{message} ").strip()
    return code if code else None


def _handle_login() -> None:
    """Show login status and verify the active authentication."""
    status = get_login_status()
    mode = status["mode"]
    print(f"Login Mode: {mode.upper()}")

    if mode == LoginMode.CLIENT:
        state_str = "RUNNING" if status["process_running"] else "NOT RUNNING"
        print(f"Local Steam Client Process: {state_str}")
    else:
        cred_str = "CONFIGURED" if status["has_agent_credentials"] else "MISSING"
        print(f"Agent Credentials (.env): {cred_str}")

    print("\nVerifying active authentication...")
    result = verify_steam_authentication(_guard_code_prompt)
    if result["success"]:
        print(f"SUCCESS: {result['message']}")
    else:
        print(f"FAILED: {result['message']}")
        sys.exit(1)


def _handle_game(args: argparse.Namespace) -> None:
    """Dispatch the game subcommand to its tool handler."""
    app_id = args.app_id
    if args.tool == "unlocker":
        _handle_game_unlocker(args, app_id)
    elif args.tool == "idler":
        _handle_game_idler(args, app_id)
    elif args.tool == "stats":
        _handle_game_stats(args, app_id)


def _handle_game_unlocker(args: argparse.Namespace, app_id: int) -> None:
    """Handle the game unlocker subcommand actions."""
    action = args.action
    if action in ("unlock", "lock"):
        target_ach = getattr(args, "apiname", None)
        game_config = _find_unlock_game(app_id)
        label = game_config.app_name if game_config else f"App {app_id}"

        if target_ach:
            if game_config:
                if action == "unlock":
                    tasks.run_unlock_manifest_ach([game_config], label, target_apiname=target_ach)
                else:
                    tasks.run_lock_manifest_ach([game_config], label, target_apiname=target_ach)
            else:
                task = tasks.run_unlock_game_ach if action == "unlock" else tasks.run_lock_game_ach
                task(app_id, target_ach)
            return

        if getattr(args, "all", False):
            if action == "unlock":
                tasks.run_unlock_all_game_ach(app_id)
            else:
                tasks.run_lock_all_game_ach(app_id)
            return

        if game_config:
            if action == "unlock":
                tasks.run_unlock_manifest_ach([game_config], label)
            else:
                tasks.run_lock_manifest_ach([game_config], label)
        else:
            logger.error(
                f"App ID {app_id} is not in {config.settings.unlock_manifest_path.name}. "
                "Use --all for an instant unlock, --apiname for a single achievement, "
                "or add the game to the manifest."
            )
            sys.exit(1)
    elif action == "toggle":
        tasks.run_toggle_game_ach(app_id, args.apiname)


def _handle_game_idler(args: argparse.Namespace, app_id: int) -> None:
    """Handle the game idler subcommand actions."""
    if args.action == "idle":
        if args.seconds:
            tasks.run_idle_game(app_id, args.seconds)
            return

        game_config = _find_idle_game(app_id)
        if game_config:
            tasks.run_idle_manifest([game_config], game_config.app_name or f"App {app_id}")
        else:
            logger.error(
                f"App ID {app_id} is not in {config.settings.idle_manifest_path.name}. "
                "Specify --seconds <duration> to idle for a fixed time, or add the game to the manifest."
            )
            sys.exit(1)


def _handle_game_stats(args: argparse.Namespace, app_id: int) -> None:
    """Handle the game stats subcommand actions."""
    if args.action == "get":
        achievements = get_steam_utility_handler().fetch_achievements(app_id)
        if achievements:
            for a in achievements:
                status = "UNLOCKED" if a.get("achieved") else "LOCKED"
                print(f" - {a.get('friendly_name')} ({a.get('name')}): {status}")
        else:
            print("No achievement data retrieved.")
    elif args.action == "update":
        tasks.run_update_game_stat(app_id, args.stats_json)
    elif args.action == "reset":
        tasks.run_reset_game_stat(app_id)


def _resolve_manifest_target_games(
    manifest_path: str | Path, model: type[Any], args: argparse.Namespace
) -> tuple[list[Any], str]:
    """Resolve target games list and scope label based on args (.app_id, .category, or --all)."""
    manifest_data = _load_manifest(manifest_path, model)
    games = manifest_data.games

    if getattr(args, "app_id", None):
        target = str(args.app_id).strip()
        filtered = [g for g in games if str(g.app_id) == target or str(g.app_name).lower() == target.lower()]
        if not filtered:
            logger.error(f"Game '{target}' not found in {Path(manifest_path).name}.")
            sys.exit(1)
        return filtered, f"Game {target}"
    elif getattr(args, "category", None):
        cat_name = args.category
        return _category_games(manifest_path, model, cat_name), f"Category '{cat_name}'"
    else:
        return games, "ALL MANIFEST GAMES"


def _handle_manifest(args: argparse.Namespace) -> None:
    """Dispatch the manifest subcommand to its tool handler."""
    if args.tool == "unlocker":
        games, label = _resolve_manifest_target_games(config.settings.unlock_manifest_path, UnlockManifest, args)
        if args.action == "unlock":
            ignore_ts = getattr(args, "ignore_timestamps", False)
            tasks.run_unlock_manifest_ach(games, label, ignore_timestamps=ignore_ts)
        elif args.action == "status":
            tasks.run_check_manifest_ach(games, label)
    elif args.tool == "idler":
        games, label = _resolve_manifest_target_games(config.settings.idle_manifest_path, IdleManifest, args)
        if args.action == "idle":
            tasks.run_idle_manifest(games, label)
        elif args.action == "status":
            tasks.run_check_manifest_idle(games, label)


def _handle_tasks(args: argparse.Namespace) -> None:
    """Dispatch the tasks subcommand to handle queue operations."""
    runner = TaskRunner()

    if args.action == "list":
        tasks_list = runner.list_tasks()
        if not tasks_list:
            print("Tasks queue is empty (0 tasks in tasks.json).")
            return
        print(f"{'ID':<12} {'STATUS':<12} {'COMMAND':<24} {'TARGET / PARAMS'}")
        print("-" * 75)
        for t in tasks_list:
            tid = str(t.get("id", "unknown"))
            st = str(t.get("status", "UNKNOWN"))
            cmd = str(t.get("command", "unknown"))
            kwargs = t.get("kwargs", {})
            args_list = t.get("args", [])
            target = ""
            if "app_id" in kwargs:
                target = f"App {kwargs['app_id']}"
                if "seconds" in kwargs:
                    target += f" ({kwargs['seconds']}s)"
            elif kwargs:
                target = str(kwargs)
            elif args_list:
                target = " ".join(str(a) for a in args_list)
            print(f"{tid:<12} {st:<12} {cmd:<24} {target}")

    elif args.action == "clear":
        runner.clear_tasks()
        print("[OK] Tasks queue cleared.")

    elif args.action == "reset":
        runner.reset_tasks(target_task_id=getattr(args, "task_id", None))

    elif args.action == "run":
        runner.run_tasks(target_task_id=getattr(args, "task_id", None))

    elif args.action == "queue":
        if getattr(args, "clear", False):
            runner.clear_tasks()

        mode = args.mode
        app_id = getattr(args, "app_id", None)

        if mode == "unlock":
            if not app_id:
                logger.error("App ID is required for queue unlock (e.g. tasks queue unlock 782330)")
                sys.exit(1)
            tid = runner.add_task("unlock_manifest_ach", kwargs={"app_id": app_id})
            print(f"[OK] Queued unlock task [{tid}] for App ID {app_id}.")

        elif mode == "idle":
            if not app_id:
                logger.error("App ID is required for queue idle (e.g. tasks queue idle 782330)")
                sys.exit(1)
            kw: dict[str, Any] = {"app_id": app_id}
            if getattr(args, "seconds", None):
                kw["seconds"] = args.seconds
                tid = runner.add_task("idle_game", kwargs=kw)
            else:
                tid = runner.add_task("idle_manifest", kwargs=kw)
            print(f"[OK] Queued idle task [{tid}] for App ID {app_id}.")

        elif mode == "sync":
            kw = {"app_id": app_id} if app_id else {}
            tid = runner.add_task("sync_lib", kwargs=kw)
            print(f"[OK] Queued library sync task [{tid}].")

    elif args.action == "add":
        kw = {}
        if args.kwargs:
            try:
                kw = json.loads(args.kwargs)
            except Exception as e:
                logger.error(f"Failed to parse --kwargs as JSON: {e}")
                sys.exit(1)
        runner.add_task(args.command, args=args.args, kwargs=kw)

    elif args.action == "remove":
        runner.remove_task(args.task_id)


def main() -> None:
    """Parse CLI arguments and dispatch to the requested task."""
    parser = setup_parser()
    args = parser.parse_args()
    setup_logger(verbose=args.verbose)

    # Ensure authenticated if in agent mode and token exists
    if config.settings.login_mode == LoginMode.AGENT:
        handler = get_steam_utility_handler()
        if handler.has_saved_token() and not handler.is_authenticated():
            verify_steam_authentication()

    # Verify Steam connection/authentication for commands requiring it
    requires_connection = False
    if args.source in ("sync", "game", "manifest") or (args.source == "tasks" and args.action == "run"):
        requires_connection = True

    if requires_connection:
        res = verify_steam_authentication()
        if not res.get("success"):
            logger.error(f"Steam connection verification failed: {res.get('message', 'Unknown error')}")
            sys.exit(1)

    try:
        if args.source == "login":
            _handle_login()
        elif args.source == "sync":
            tasks.run_sync_lib(target_app_id=args.app_id)
        elif args.source == "tasks":
            _handle_tasks(args)
        elif args.source == "manifest":
            _handle_manifest(args)
        elif args.source == "game":
            _handle_game(args)
    except KeyboardInterrupt:
        logger.info("Interrupted.")
        sys.exit(130)
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        if args.verbose:
            logger.exception("Detailed traceback:")
        sys.exit(1)


if __name__ == "__main__":
    main()
