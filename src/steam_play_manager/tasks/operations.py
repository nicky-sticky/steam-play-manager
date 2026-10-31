import logging
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any, ParamSpec

from steam_play_manager.core import config
from steam_play_manager.core.utils import read_json, seconds_to_dhms, ts_to_date, write_json
from steam_play_manager.models import (
    IdleCheckResult,
    IdleGame,
    LibraryFile,
    LibraryGame,
    LoginMode,
    OwnedGame,
    UnlockCheckResult,
    UnlockGame,
)
from steam_play_manager.services.achievement_unlocker import AchievementUnlocker
from steam_play_manager.services.game_idler import SteamGameIdler
from steam_play_manager.services.steam_client import SteamWebApiClient
from steam_play_manager.services.steam_utility import is_steam_ready

logger = logging.getLogger(__name__)

_P = ParamSpec("_P")


def require_steam(func: Callable[_P, Any]) -> Callable[_P, Any]:
    """Decorator to ensure Steam is running (client mode) or authenticated (agent mode) before running a task."""

    @wraps(func)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> Any:
        if not is_steam_ready():
            if config.settings.login_mode == LoginMode.CLIENT:
                logger.error("Steam does not appear to be running. Please launch Steam first.")
            else:
                logger.error("Steam Agent is not authenticated. Please log in first.")
            return False
        return func(*args, **kwargs)

    return wrapper


# --- Library Tasks ---


def _sync_library_to_disk(
    owned: dict[int, OwnedGame],
    target_app_id: int | None = None,
) -> None:
    """Transform owned games data from the Web API and write to games.json."""

    # When target_app_id is set, only that game is upserted; otherwise the full library is replaced.
    games_list: list[LibraryGame] = []
    for app_id, info in owned.items():
        if target_app_id and int(app_id) != int(target_app_id):
            continue
        rtime_last_played = info.get("rtime_last_played", 0)
        games_list.append(
            {
                "app_id": app_id,
                "app_name": info.get("name", f"App {app_id}"),
                "playtime_seconds": info.get("playtime_forever", 0) * 60,
                "last_played_ts": rtime_last_played,
                "last_played_date": ts_to_date(rtime_last_played) if rtime_last_played else "",
            }
        )

    now_iso = datetime.now(UTC).isoformat()

    if target_app_id:
        existing = read_json(config.settings.games_data_path) or {}
        existing_games: list[LibraryGame] = existing.get("games", [])
        existing_map = {g["app_id"]: i for i, g in enumerate(existing_games)}
        for new_entry in games_list:
            idx = existing_map.get(new_entry["app_id"])
            if idx is not None:
                existing_games[idx] = new_entry
            else:
                existing_games.append(new_entry)

        output_data: LibraryFile = {
            "last_synced_at": now_iso,
            "total_games": len(existing_games),
            "games": existing_games,
        }
    else:
        output_data = {
            "last_synced_at": now_iso,
            "total_games": len(games_list),
            "games": games_list,
        }

    write_json(config.settings.games_data_path, output_data)
    logger.info(f"Successfully synced {len(games_list)} games to {config.settings.games_data_path}")


def run_sync_lib(target_app_id: int | None = None) -> bool:
    """Synchronize Steam library to games.json database."""
    logger.info("Running Steam library sync...")
    client = SteamWebApiClient()
    if not client.steam_id or not client.api_key:
        logger.error("Cannot sync games: Missing STEAM_ID or STEAM_API_KEY.")
        return False
    owned = client.fetch_owned_games()
    if not owned:
        logger.error("Sync failed: Could not retrieve owned games.")
        return False
    _sync_library_to_disk(owned, target_app_id)
    return True


# --- Game Idler Operations ---


@require_steam
def run_idle_game(app_id: str | int, idle_seconds: int) -> bool:
    """Idle a single game for a specified duration in seconds."""
    logger.info(f"Running idler for App ID {app_id} ({idle_seconds}s)...")
    return SteamGameIdler().idle_game(app_id, idle_seconds)


# --- Game Achievement Operations ---


@require_steam
def run_toggle_game_ach(app_id: str | int, achievement_id: str) -> bool:
    """Toggle single achievement lock state."""
    logger.info(f"Running toggle achievement for App ID {app_id} (ach: {achievement_id})...")
    return AchievementUnlocker().toggle_game_ach(app_id, achievement_id)


@require_steam
def run_unlock_game_ach(app_id: str | int, achievement_id: str) -> bool:
    """Unlock a single achievement by API name."""
    logger.info(f"Running unlock achievement for App ID {app_id} (ach: {achievement_id})...")
    return AchievementUnlocker().unlock_game_ach(app_id, achievement_id)


@require_steam
def run_lock_game_ach(app_id: str | int, achievement_id: str) -> bool:
    """Lock a single achievement by API name."""
    logger.info(f"Running lock achievement for App ID {app_id} (ach: {achievement_id})...")
    return AchievementUnlocker().lock_game_ach(app_id, achievement_id)


@require_steam
def run_unlock_all_game_ach(app_id: str | int) -> bool:
    """Instantly unlock all achievements for an App ID."""
    logger.info(f"Running instant unlock all for App ID {app_id}...")
    return AchievementUnlocker().unlock_all_game_ach(app_id)


@require_steam
def run_lock_all_game_ach(app_id: str | int) -> bool:
    """Instantly lock all achievements for an App ID."""
    logger.info(f"Running instant lock all for App ID {app_id}...")
    return AchievementUnlocker().lock_all_game_ach(app_id)


# --- Game Statistics Operations ---


@require_steam
def run_update_game_stat(app_id: str | int, stats_data: Any) -> bool:
    """Update statistics for a game."""
    logger.info(f"Running update stats for App ID {app_id}...")
    return AchievementUnlocker().update_game_stat(app_id, stats_data)


@require_steam
def run_reset_game_stat(app_id: str | int) -> bool:
    """Reset all statistics for a game."""
    logger.info(f"Running reset stats for App ID {app_id}...")
    return AchievementUnlocker().reset_game_stat(app_id)


# --- Manifest Operations ---


@require_steam
def run_idle_manifest(games: list[IdleGame], label: str, tolerance_seconds: int | None = None) -> bool:
    """Run idle routine for games list until target playtime is reached."""
    logger.info(f"Running idler for: {label} ({len(games)} games)...")
    try:
        idler = SteamGameIdler()
        idler.idle_manifest(games, tolerance_seconds=tolerance_seconds)
        logger.info("Idler routine finished!")
        return True
    except Exception as e:
        logger.exception(f"Idler failed for {label}")
        logger.error(f"Idler failed: {e}")
        return False


def run_check_manifest_idle(
    games: list[IdleGame], label: str, tolerance_seconds: int | None = None
) -> IdleCheckResult | None:
    """Check current playtime vs desired idle playtime in manifest."""
    logger.info(f"Running idle time check for: {label} ({len(games)} games)...")
    try:
        idler = SteamGameIdler()
        res = idler.check_manifest_idle(games, tolerance_seconds=tolerance_seconds)
        summary = res or {}
        logger.info(
            f"Idle Check Completed: Total Games={summary.get('total_games', 0)}, "
            f"Needing Idle={summary.get('games_needing_idle_count', 0)}, "
            f"Completed={summary.get('games_completed_count', 0)}"
        )
        for game in summary.get("games_needing_idle", []):
            logger.info(
                f" - {game['app_name']} (ID: {game['app_id']}): "
                f"{seconds_to_dhms(game['current_playtime_seconds'])} / "
                f"{seconds_to_dhms(game['desired_playtime_seconds'])} "
                f"({seconds_to_dhms(game['needed_seconds'])} remaining)"
            )
        if not summary.get("games_needing_idle"):
            logger.info("All games have reached their target playtimes!")
        return res
    except Exception as e:
        logger.exception(f"Idle time check failed for {label}")
        logger.error(f"Idle time check failed: {e}")
        return None


@require_steam
def run_unlock_manifest_ach(
    games: list[UnlockGame],
    label: str,
    target_apiname: str | None = None,
    ignore_timestamps: bool = False,
) -> bool:
    """Run manifest-scheduled achievement unlocking for games list."""
    logger.info(f"Running unlocker for: {label} ({len(games)} games)...")
    try:
        unlocker = AchievementUnlocker()
        if not unlocker.unlock_manifest_ach(games, target_apiname=target_apiname, ignore_timestamps=ignore_timestamps):
            logger.error(f"Unlocker aborted for {label}")
            return False
        logger.info("Unlocker routine finished!")
        return True
    except Exception as e:
        logger.exception(f"Unlocker failed for {label}")
        logger.error(f"Unlocker failed: {e}")
        return False


@require_steam
def run_lock_manifest_ach(games: list[UnlockGame], label: str, target_apiname: str | None = None) -> bool:
    """Run manifest-scheduled achievement relocking for games list."""
    logger.info(f"Running locker for: {label} ({len(games)} games)...")
    try:
        unlocker = AchievementUnlocker()
        if not unlocker.lock_manifest_ach(games, target_apiname=target_apiname):
            logger.error(f"Locker aborted for {label}")
            return False
        logger.info("Locker routine finished!")
        return True
    except Exception as e:
        logger.exception(f"Locker failed for {label}")
        logger.error(f"Locker failed: {e}")
        return False


def run_check_manifest_ach(
    games: list[UnlockGame], label: str, tolerance_seconds: int | None = None, target_apiname: str | None = None
) -> UnlockCheckResult | None:
    """Check manifest unlock delays vs actual Steam unlock times."""
    logger.info(f"Running unlock time check for: {label} ({len(games)} games)...")
    try:
        unlocker = AchievementUnlocker()
        res = unlocker.check_manifest_ach(games, tolerance_seconds=tolerance_seconds, target_apiname=target_apiname)

        summary = res["summary"]
        logger.info(
            f"Check Completed: Total Games={summary['total_games']}, "
            f"Differing Games={summary['differing_games_count']}, "
            f"Matching Games={summary['matching_games_count']}, "
            f"Skipped/Error Games={summary['skipped_games_count']}"
        )

        differing_games = res["games_with_differences"]
        if differing_games:
            logger.info("1) Games with unlock delay differences:")
            for g in differing_games:
                logger.info(
                    f" - {g['app_name']} (ID: {g['app_id']}): "
                    f"{g['differing_count']} of {g['total_manifest']} manifest achievements "
                    "differ from actual Steam unlock times."
                )

            logger.info("2) Specific achievements with differences:")
            for g in differing_games:
                logger.info(f"[{g['app_name']} ({g['app_id']})]")
                for ach in g["differing_achievements"]:
                    diff_sign = (
                        f"+{ach['step_delay_diff']}"
                        if ach["step_delay_diff"] is not None and ach["step_delay_diff"] > 0
                        else f"{ach['step_delay_diff']}"
                    )
                    logger.info(f"  * Achievement: '{ach['name']}' ({ach['apiname']})")
                    logger.info(
                        f"    - Manifest Step Delay : {seconds_to_dhms(ach['manifest_step_delay'])} "
                        f"({ach['manifest_step_delay']}s)"
                    )
                    logger.info(
                        f"    - Actual Step Delay   : {seconds_to_dhms(ach['actual_step_delay'] or 0)} "
                        f"({ach['actual_step_delay']}s)"
                    )
                    logger.info(f"    - Step Delay Diff     : {diff_sign}s")
                    logger.info(
                        f"    - Actual Unlock Date  : {ach['unlocktime_date']} (Timestamp: {ach['unlocktime_ts']})"
                    )
        else:
            logger.info("No games found with unlock delay differences!")

        return res
    except Exception as e:
        logger.exception(f"Unlock time check failed for {label}")
        logger.error(f"Unlock time check failed: {e}")
        return None


# --- Task Registry Mapping ---


TASK_REGISTRY = {
    # 1. Library
    "sync_lib": run_sync_lib,
    # 2. Game Idler Operations
    "idle_game": run_idle_game,
    # 3. Game Achievement Operations
    "toggle_game_ach": run_toggle_game_ach,
    "unlock_game_ach": run_unlock_game_ach,
    "lock_game_ach": run_lock_game_ach,
    "unlock_all_game_ach": run_unlock_all_game_ach,
    "lock_all_game_ach": run_lock_all_game_ach,
    # 4. Game Statistics Operations
    "update_game_stat": run_update_game_stat,
    "reset_game_stat": run_reset_game_stat,
    # 5. Manifest Operations
    "idle_manifest": run_idle_manifest,
    "check_manifest_idle": run_check_manifest_idle,
    "unlock_manifest_ach": run_unlock_manifest_ach,
    "lock_manifest_ach": run_lock_manifest_ach,
    "check_manifest_ach": run_check_manifest_ach,
}
