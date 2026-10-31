import json
import logging
import time
from pathlib import Path
from typing import Any

from steam_play_manager.core import config
from steam_play_manager.core.utils import seconds_to_dhms, ts_to_date, wait_with_update
from steam_play_manager.models import (
    AchievementCheckEntry,
    AchievementStatus,
    GameCheckResult,
    GameUnlockStatus,
    GameUnlockSummary,
    UnlockCheckResult,
    UnlockGame,
)
from steam_play_manager.services.steam_client import SteamWebApiClient
from steam_play_manager.services.steam_utility import SteamUtilityHandler, get_steam_utility_handler

logger = logging.getLogger(__name__)


class AchievementUnlocker:
    """Manages unlocking, locking, stats, and unlock time verification for Steam achievements."""

    def __init__(
        self,
        steam_id: str | None = None,
        steam_api_key: str | None = None,
        steam_api_base_url: str | None = None,
        steam_utility_path: str | Path | None = None,
        tolerance_seconds: int | None = None,
        steam_utility_handler: SteamUtilityHandler | None = None,
        steam_api_client: SteamWebApiClient | None = None,
    ) -> None:
        s = config.settings
        self.steam_id = s.steam_id if steam_id is None else steam_id
        self.steam_api_key = s.steam_api_key if steam_api_key is None else steam_api_key
        self.steam_api_base_url = s.steam_api_base_url if steam_api_base_url is None else steam_api_base_url
        self.steam_utility_path = s.steam_utility_path if steam_utility_path is None else steam_utility_path
        self.tolerance_seconds = (
            s.unlock_delay_tolerance_seconds if tolerance_seconds is None else int(tolerance_seconds)
        )
        self._steam_utility_handler = steam_utility_handler
        self.steam_api_client = steam_api_client or SteamWebApiClient(
            steam_id=self.steam_id, api_key=self.steam_api_key, api_base_url=self.steam_api_base_url
        )
        self.owned_games = {}

    @property
    def steam_utility_handler(self) -> SteamUtilityHandler:
        """Resolve the handler lazily so test patches on get_steam_utility_handler take effect."""
        if self._steam_utility_handler is not None:
            return self._steam_utility_handler
        return get_steam_utility_handler()

    # --- Game Achievement Operations ---

    def toggle_game_ach(self, app_id: str | int, achievement_id: str) -> bool:
        """Toggle lock/unlock state of a single achievement."""
        logger.info(f"Toggling achievement {achievement_id} for App ID {app_id}...")
        res = self.steam_utility_handler.execute("toggle_achievement", str(app_id), str(achievement_id))
        if res["success"]:
            logger.info(f"Successfully toggled achievement {achievement_id} for App ID {app_id}.")
            return True
        err_msg = res.get("stderr") or res.get("output")
        logger.error(f"Failed to toggle achievement {achievement_id} for App ID {app_id}: {err_msg}")
        return False

    def unlock_game_ach(self, app_id: str | int, achievement_id: str) -> bool:
        """Unlock a single Steam achievement."""
        logger.info(f"Unlocking achievement {achievement_id} for App ID {app_id}...")
        return self._unlock_achievement(app_id, achievement_id)

    def lock_game_ach(self, app_id: str | int, achievement_id: str) -> bool:
        """Lock a single Steam achievement."""
        logger.info(f"Locking achievement {achievement_id} for App ID {app_id}...")
        return self._lock_achievement(app_id, achievement_id)

    def unlock_all_game_ach(self, app_id: str | int) -> bool:
        """Instantly unlock all achievements for a game."""
        logger.info(f"Unlocking all achievements for App ID {app_id}...")
        res = self.steam_utility_handler.execute("unlock_all_achievements", str(app_id))
        if res["success"]:
            logger.info(f"Successfully unlocked all achievements for App ID {app_id}.")
            return True
        logger.error(f"Failed to unlock all achievements for App ID {app_id}: {res.get('stderr') or res.get('output')}")
        return False

    def lock_all_game_ach(self, app_id: str | int) -> bool:
        """Instantly lock all achievements for a game."""
        logger.info(f"Locking all achievements for App ID {app_id}...")
        res = self.steam_utility_handler.execute("lock_all_achievements", str(app_id))
        if res["success"]:
            logger.info(f"Successfully locked all achievements for App ID {app_id}.")
            return True
        logger.error(f"Failed to lock all achievements for App ID {app_id}: {res.get('stderr') or res.get('output')}")
        return False

    # --- Game Statistics Operations ---

    def update_game_stat(self, app_id: str | int, stats: Any) -> bool:
        """Update stats for a game."""
        logger.info(f"Updating stats for App ID {app_id}...")
        stats_str = json.dumps(stats) if isinstance(stats, (dict, list)) else str(stats)

        res = self.steam_utility_handler.execute("update_stats", str(app_id), stats_str)
        if res["success"]:
            logger.info(f"Successfully updated stats for App ID {app_id}.")
            return True
        logger.error(f"Failed to update stats for App ID {app_id}: {res.get('stderr') or res.get('output')}")
        return False

    def reset_game_stat(self, app_id: str | int) -> bool:
        """Reset all statistics for a game."""
        logger.info(f"Resetting all stats for App ID {app_id}...")
        res = self.steam_utility_handler.execute("reset_all_stats", str(app_id))
        if res["success"]:
            logger.info(f"Successfully reset all stats for App ID {app_id}.")
            return True
        logger.error(f"Failed to reset stats for App ID {app_id}: {res.get('stderr') or res.get('output')}")
        return False

    # --- Manifest Achievement Operations ---

    def unlock_manifest_ach(
        self,
        games_list: list[UnlockGame],
        target_apiname: str | None = None,
        ignore_timestamps: bool = False,
    ) -> bool:
        """Process a list of games for achievement unlocking with delay intervals."""
        if not games_list:
            logger.info("No games provided to unlock. Exiting.")
            return False

        self.owned_games = self.steam_api_client.fetch_owned_games()
        if not self.owned_games:
            logger.info("Failed to fetch owned games. Exiting.")
            return False

        games_to_process = []
        for game in games_list:
            if game.app_id in self.owned_games:
                games_to_process.append(game)
            else:
                logger.warning(f"Skipping {game.app_name} ({game.app_id}) (Not Owned)")

        if not games_to_process:
            logger.info("No games to process. Exiting.")
            return False

        for game in games_to_process:
            app_id = game.app_id
            app_name = game.app_name
            achievements_to_unlock = game.achievements

            if target_apiname:
                achievements_to_unlock = [
                    a for a in achievements_to_unlock if a.apiname == target_apiname or a.name == target_apiname
                ]
                if not achievements_to_unlock:
                    logger.info(f"Target achievement '{target_apiname}' not found for game {app_name}. Skipping.")
                    continue

            logger.info(f"Processing achievements for {app_name} ({app_id})...")

            steam_achievements = self.steam_utility_handler.fetch_achievements(app_id)
            already_unlocked = (
                {a["name"] for a in steam_achievements if a.get("achieved")} if steam_achievements else set()
            )

            profile_ach = {}
            if not ignore_timestamps:
                profile_ach = self.steam_api_client.fetch_profile_achievements(app_id)

            last_unlock_ts: int | None = None
            first_unlock_done = False

            for ach in achievements_to_unlock:
                api_name = ach.apiname
                friendly_name = ach.name
                delay_seconds = ach.delay_seconds

                if api_name in already_unlocked:
                    ach_ts = profile_ach.get(api_name, {}).get("unlocktime_ts")
                    if not first_unlock_done and ach_ts:
                        last_unlock_ts = ach_ts

                    if ach_ts:
                        logger.info(
                            f"Skipping '{friendly_name}' ({api_name}) - "
                            f"Already Unlocked on Steam ({ts_to_date(ach_ts)})."
                        )
                    else:
                        logger.info(f"Skipping '{friendly_name}' ({api_name}) - Already Unlocked on Steam.")
                    continue

                if not first_unlock_done:
                    wait_seconds = delay_seconds
                    if not ignore_timestamps and last_unlock_ts is not None:
                        target_ts = last_unlock_ts + delay_seconds
                        now_ts = int(time.time())
                        diff = target_ts - now_ts
                        if diff > 0:
                            wait_seconds = diff
                            logger.info(
                                f"Assessing unlock times: Last unlock was at {ts_to_date(last_unlock_ts)}. "
                                f"Waiting remaining {seconds_to_dhms(wait_seconds)} before "
                                f"unlocking '{friendly_name}' ({api_name})..."
                            )
                        else:
                            wait_seconds = 0
                            logger.info(
                                f"Assessing unlock times: Last unlock was at {ts_to_date(last_unlock_ts)}. "
                                f"Required delay ({seconds_to_dhms(delay_seconds)}) already elapsed. "
                                "Unlocking immediately."
                            )
                    elif delay_seconds > 0:
                        logger.info(
                            f"Waiting {seconds_to_dhms(delay_seconds)} before "
                            f"unlocking '{friendly_name}' ({api_name})..."
                        )
                else:
                    wait_seconds = delay_seconds
                    if wait_seconds > 0:
                        logger.info(
                            f"Waiting {seconds_to_dhms(wait_seconds)} before "
                            f"unlocking '{friendly_name}' ({api_name})..."
                        )

                if wait_seconds > 0:
                    wait_with_update(wait_seconds)

                logger.info(f"Unlocking '{friendly_name}' ({api_name})...")
                success = self._unlock_achievement(app_id, api_name)
                if success:
                    logger.info(f"Successfully unlocked '{friendly_name}'.")
                    last_unlock_ts = int(time.time())
                    first_unlock_done = True
                else:
                    logger.error(f"Failed to unlock '{friendly_name}'. Aborting remaining unlocks.")
                    return False

        return True

    def lock_manifest_ach(self, games_list: list[UnlockGame], target_apiname: str | None = None) -> bool:
        """Process a list of games for achievement relocking."""
        if not games_list:
            logger.info("No games provided to lock. Exiting.")
            return False

        self.owned_games = self.steam_api_client.fetch_owned_games()
        if not self.owned_games:
            logger.info("Failed to fetch owned games. Exiting.")
            return False

        games_to_process = []
        for game in games_list:
            if game.app_id in self.owned_games:
                games_to_process.append(game)
            else:
                logger.warning(f"Skipping {game.app_name} ({game.app_id}) (Not Owned)")

        if not games_to_process:
            logger.info("No games to process. Exiting.")
            return False

        for game in games_to_process:
            app_id = game.app_id
            app_name = game.app_name
            achievements_to_lock = game.achievements

            if target_apiname:
                achievements_to_lock = [
                    a for a in achievements_to_lock if a.apiname == target_apiname or a.name == target_apiname
                ]
                if not achievements_to_lock:
                    logger.info(f"Target achievement '{target_apiname}' not found for game {app_name}. Skipping.")
                    continue

            logger.info(f"Locking achievements for {app_name} ({app_id})...")

            steam_achievements = self.steam_utility_handler.fetch_achievements(app_id)
            unlocked_names = (
                {a["name"] for a in steam_achievements if a.get("achieved")} if steam_achievements else set()
            )

            for ach in achievements_to_lock:
                api_name = ach.apiname
                friendly_name = ach.name or api_name

                if unlocked_names and api_name not in unlocked_names:
                    logger.info(f"Skipping '{friendly_name}' ({api_name}) - Already Locked on Steam.")
                    continue

                logger.info(f"Locking '{friendly_name}' ({api_name})...")
                success = self._lock_achievement(app_id, api_name)
                if success:
                    logger.info(f"Successfully locked '{friendly_name}'.")
                else:
                    logger.error(f"Failed to lock '{friendly_name}'. Aborting remaining locks.")
                    return False

        return True

    def check_manifest_ach(
        self, games_list: list[UnlockGame], tolerance_seconds: int | None = None, target_apiname: str | None = None
    ) -> UnlockCheckResult:
        """Check achievement unlock delays across a list of game configs against actual Steam unlock times."""
        if tolerance_seconds is None:
            tolerance_seconds = self.tolerance_seconds

        games_with_differences: list[GameCheckResult] = []
        games_matching: list[GameCheckResult] = []
        games_skipped: list[GameCheckResult] = []

        for game_config in games_list:
            result = self._check_game_unlock_times(
                game_config, tolerance_seconds=tolerance_seconds, target_apiname=target_apiname
            )

            if result.get("error"):
                games_skipped.append(result)
            elif result.get("has_differences"):
                games_with_differences.append(result)
            else:
                games_matching.append(result)

        return {
            "games_with_differences": games_with_differences,
            "games_matching": games_matching,
            "games_skipped": games_skipped,
            "summary": {
                "total_games": len(games_list),
                "differing_games_count": len(games_with_differences),
                "matching_games_count": len(games_matching),
                "skipped_games_count": len(games_skipped),
            },
        }

    # --- Unlock Schedule & Time Verification ---

    def check_game_schedule(self, game_config: UnlockGame, tolerance_seconds: int | None = None) -> GameCheckResult:
        """Per-achievement schedule comparison for one game."""
        return self._check_game_unlock_times(game_config, tolerance_seconds=tolerance_seconds)

    def check_game_status(self, game_config: UnlockGame, tolerance_seconds: int | None = None) -> GameUnlockSummary:
        """Single-game unlock-schedule summary for status views."""
        result = self._check_game_unlock_times(game_config, tolerance_seconds=tolerance_seconds)

        status = GameUnlockStatus.DONE
        if result.get("error"):
            status = GameUnlockStatus.ERROR
        else:
            all_ach = result.get("all_achievements") or []
            if any(a.get("status") == AchievementStatus.DELAY_MISMATCH for a in all_ach):
                status = GameUnlockStatus.NEEDS_RERUN
            elif any(a.get("status") == AchievementStatus.LOCKED_ON_STEAM for a in all_ach):
                status = GameUnlockStatus.IN_PROGRESS

        return {
            "app_id": result.get("app_id"),
            "app_name": result.get("app_name"),
            "configured_count": result.get("total_manifest", 0),
            "unlocked_count": sum(1 for a in (result.get("all_achievements") or []) if a.get("achieved")),
            "status": status,
            "error": result.get("error"),
        }

    # --- Internal Helpers ---

    def _unlock_achievement(self, app_id: str | int, achievement_id: str) -> bool:
        """Unlock a single Steam achievement using SteamUtilityHandler."""
        res = self.steam_utility_handler.execute("unlock_achievement", str(app_id), str(achievement_id))
        if res["success"]:
            logger.info(f"Successfully unlocked achievement {achievement_id} for app {app_id}.")
            return True
        logger.error(
            f"Failed to unlock achievement {achievement_id} for app {app_id}: {res.get('stderr') or res.get('output')}"
        )
        return False

    def _lock_achievement(self, app_id: str | int, achievement_id: str) -> bool:
        """Lock a single Steam achievement using SteamUtilityHandler."""
        res = self.steam_utility_handler.execute("lock_achievement", str(app_id), str(achievement_id))
        if res["success"]:
            logger.info(f"Successfully locked achievement {achievement_id} for app {app_id}.")
            return True
        logger.error(
            f"Failed to lock achievement {achievement_id} for app {app_id}: {res.get('stderr') or res.get('output')}"
        )
        return False

    def _check_game_unlock_times(
        self, game_config: UnlockGame, tolerance_seconds: int | None = None, target_apiname: str | None = None
    ) -> GameCheckResult:
        """Check achievement unlock delays for a single game configuration."""
        if tolerance_seconds is None:
            tolerance_seconds = self.tolerance_seconds

        app_id = game_config.app_id
        app_name = game_config.app_name
        manifest_achievements = game_config.achievements

        if target_apiname:
            manifest_achievements = [
                a for a in manifest_achievements if a.apiname == target_apiname or a.name == target_apiname
            ]

        if not self.steam_id or not self.steam_api_key:
            err = "Missing STEAM_ID or STEAM_API_KEY in configuration."
            logger.error(f"Cannot check unlock times for {app_name} ({app_id}): {err}")
            return {
                "app_id": app_id,
                "app_name": app_name,
                "has_differences": False,
                "differing_count": 0,
                "total_manifest": len(manifest_achievements),
                "total_unlocked_actual": 0,
                "differing_achievements": [],
                "all_achievements": [],
                "error": err,
            }

        profile_ach = self.steam_api_client.fetch_profile_achievements(app_id)

        if not profile_ach:
            err = f"Could not retrieve profile achievements for {app_name} ({app_id}) from Steam Web API."
            logger.warning(err)
            return {
                "app_id": app_id,
                "app_name": app_name,
                "has_differences": False,
                "differing_count": 0,
                "total_manifest": len(manifest_achievements),
                "total_unlocked_actual": 0,
                "differing_achievements": [],
                "all_achievements": [],
                "error": err,
            }

        unlocked_actual = [a for a in profile_ach.values() if a.get("achieved") and a.get("unlocktime_ts") is not None]
        unlocked_actual.sort(key=lambda x: x["unlocktime_ts"] or 0)

        actual_info = {}
        for idx, ach in enumerate(unlocked_actual):
            ts = ach["unlocktime_ts"] or 0
            prev_ts = (unlocked_actual[idx - 1]["unlocktime_ts"] or 0) if idx > 0 else ts
            first_ts = unlocked_actual[0]["unlocktime_ts"] or 0

            step_delay = ts - prev_ts
            cum_delay = ts - first_ts

            actual_info[ach["apiname"]] = {
                "actual_order": idx,
                "unlocktime_ts": ts,
                "unlocktime_date": ach.get("unlocktime_date") or ts_to_date(ts),
                "actual_step_delay": step_delay,
                "actual_cumulative_delay": cum_delay,
            }

        manifest_cum = 0
        manifest_processed = []
        for idx, ach in enumerate(manifest_achievements):
            apiname = ach.apiname
            friendly_name = ach.name or apiname
            step_delay = ach.delay_seconds
            manifest_cum += step_delay
            manifest_processed.append(
                {
                    "manifest_order": idx,
                    "apiname": apiname,
                    "friendly_name": friendly_name,
                    "manifest_step_delay": step_delay,
                    "manifest_cumulative_delay": manifest_cum,
                }
            )

        differing_achievements: list[AchievementCheckEntry] = []
        all_achievements: list[AchievementCheckEntry] = []

        for item in manifest_processed:
            apiname = item["apiname"]
            friendly_name = item["friendly_name"]
            m_step = item["manifest_step_delay"]
            m_cum = item["manifest_cumulative_delay"]

            if apiname in actual_info:
                act = actual_info[apiname]
                a_step = act["actual_step_delay"]
                a_cum = act["actual_cumulative_delay"]
                step_diff = a_step - m_step
                cum_diff = a_cum - m_cum
                is_different = abs(step_diff) > tolerance_seconds

                status = AchievementStatus.MATCH if not is_different else AchievementStatus.DELAY_MISMATCH

                ach_entry: AchievementCheckEntry = {
                    "apiname": apiname,
                    "name": friendly_name,
                    "status": status,
                    "manifest_step_delay": m_step,
                    "actual_step_delay": a_step,
                    "step_delay_diff": step_diff,
                    "manifest_cumulative_delay": m_cum,
                    "actual_cumulative_delay": a_cum,
                    "cumulative_delay_diff": cum_diff,
                    "unlocktime_ts": act["unlocktime_ts"],
                    "unlocktime_date": act["unlocktime_date"],
                    "achieved": True,
                }
                all_achievements.append(ach_entry)
                if is_different:
                    differing_achievements.append(ach_entry)
            else:
                ach_entry = {
                    "apiname": apiname,
                    "name": friendly_name,
                    "status": AchievementStatus.LOCKED_ON_STEAM,
                    "manifest_step_delay": m_step,
                    "actual_step_delay": None,
                    "step_delay_diff": None,
                    "manifest_cumulative_delay": m_cum,
                    "actual_cumulative_delay": None,
                    "cumulative_delay_diff": None,
                    "unlocktime_ts": None,
                    "unlocktime_date": "N/A",
                    "achieved": False,
                }
                all_achievements.append(ach_entry)

        return {
            "app_id": app_id,
            "app_name": app_name,
            "has_differences": len(differing_achievements) > 0,
            "differing_count": len(differing_achievements),
            "total_manifest": len(manifest_achievements),
            "total_unlocked_actual": len(unlocked_actual),
            "differing_achievements": differing_achievements,
            "all_achievements": all_achievements,
            "error": None,
        }
