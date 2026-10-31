import contextlib
import logging
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from steam_play_manager.core import config
from steam_play_manager.core.executable import build_launch_command
from steam_play_manager.core.utils import seconds_to_dhms, wait_with_update
from steam_play_manager.models import IdleCheckResult, IdleGame, IdleGameRecord, LoginMode, OwnedGame
from steam_play_manager.services.steam_client import SteamWebApiClient
from steam_play_manager.services.steam_utility import SteamUtilityHandler, get_steam_utility_handler

logger = logging.getLogger(__name__)


_ACTIVE_IDLE_PROCS = set()


class SteamGameIdler:
    """Manages single-game and manifest-driven batch idling across Client and Agent modes."""

    def __init__(
        self,
        steam_id: str | None = None,
        steam_api_key: str | None = None,
        steam_api_base_url: str | None = None,
        steam_utility_path: str | Path | None = None,
        max_processes: int | None = None,
        tolerance_seconds: int | None = None,
        steam_utility_handler: SteamUtilityHandler | None = None,
        steam_api_client: SteamWebApiClient | None = None,
    ) -> None:
        s = config.settings
        self.steam_id = s.steam_id if steam_id is None else steam_id
        self.steam_api_key = s.steam_api_key if steam_api_key is None else steam_api_key
        self.steam_api_base_url = s.steam_api_base_url if steam_api_base_url is None else steam_api_base_url
        self.steam_utility_path = s.steam_utility_path if steam_utility_path is None else steam_utility_path
        self.max_processes = s.max_client_idle_workers if max_processes is None else int(max_processes)
        self.tolerance_seconds = s.idle_tolerance_seconds if tolerance_seconds is None else int(tolerance_seconds)
        self._steam_utility_handler = steam_utility_handler
        self.steam_api_client = steam_api_client or SteamWebApiClient(
            steam_id=self.steam_id, api_key=self.steam_api_key, api_base_url=self.steam_api_base_url
        )
        self.owned_games: dict[int, OwnedGame] = {}

    @property
    def steam_utility_handler(self) -> SteamUtilityHandler:
        """Resolve the handler lazily so test patches on get_steam_utility_handler take effect."""
        if self._steam_utility_handler is not None:
            return self._steam_utility_handler
        return get_steam_utility_handler()

    # --- Single Game Operations ---

    def idle_game(self, app_id: str | int, idle_seconds: int, check_interval: int = 60) -> bool:
        """Idle a single game for idle_seconds across Client or Agent mode."""
        handler = self.steam_utility_handler
        logger.info(f"Starting idle for App ID {app_id} for {seconds_to_dhms(idle_seconds)}")
        proc = None

        try:
            if handler.login_mode == LoginMode.AGENT:
                res = handler.execute("idle", str(app_id))
                if not res.get("success"):
                    logger.error(
                        f"Failed to start agent idle for App ID {app_id}: {res.get('stderr') or res.get('output')}"
                    )
                    return False
            else:
                try:
                    proc = subprocess.Popen(
                        build_launch_command(
                            handler.steam_utility_path, handler.steam_utility_dll, "idle", str(app_id)
                        ),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                except FileNotFoundError:
                    logger.error(f"SteamUtility not found at: {handler.steam_utility_path}")
                    return False
                except Exception as e:
                    logger.error(f"Failed to start SteamUtility for App ID {app_id}: {e}")
                    return False
                _ACTIVE_IDLE_PROCS.add(proc)

            wait_with_update(idle_seconds, update_seconds=check_interval, label=f"Idling App ID {app_id}")
            logger.info(f"Finished idling App ID {app_id}")
            return True
        except KeyboardInterrupt:
            logger.warning(f"Idling interrupted for App ID {app_id}")
            return False
        except Exception as e:
            logger.error(f"Error while idling App ID {app_id}: {e}")
            return False
        finally:
            if handler.login_mode == LoginMode.AGENT:
                if handler._daemon_proc and handler._daemon_proc.poll() is None:
                    handler.execute("idle")
            elif proc is not None:
                _ACTIVE_IDLE_PROCS.discard(proc)
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()

    # --- Manifest Operations ---

    def idle_manifest(self, games_list: list[IdleGame], tolerance_seconds: int | None = None) -> bool:
        """Processes a list of games for playtime idling."""
        if tolerance_seconds is None:
            tolerance_seconds = config.settings.idle_tolerance_seconds

        if not games_list:
            logger.info("No games provided to idle. Exiting.")
            return False

        logger.info(f"Checking owned games playtimes for {len(games_list)} configured games...")
        self.owned_games = self.steam_api_client.fetch_owned_games()

        games_to_idle = []
        for g in games_list:
            app_id = g.app_id
            if app_id is None:
                continue

            if self.owned_games and app_id not in self.owned_games:
                logger.warning(f"Skipping {g.app_name} ({app_id}) (Not Owned)")
                continue

            app_name = g.app_name or str(app_id)
            desired_sec = g.desired_playtime_seconds

            owned_info = self.owned_games.get(app_id, {})
            current_sec = owned_info.get("playtime_forever", 0) * 60

            if desired_sec <= 0:
                logger.info(f"Skipping {app_name} ({app_id}) — no target playtime configured.")
                continue

            if current_sec >= desired_sec:
                logger.info(
                    f"Skipping {app_name} ({app_id}) — "
                    f"current ({seconds_to_dhms(current_sec)}) >= target ({seconds_to_dhms(desired_sec)})"
                )
                continue

            if current_sec + tolerance_seconds >= desired_sec:
                logger.info(
                    f"Skipping {app_name} ({app_id}) — "
                    f"within {tolerance_seconds}s tolerance of target "
                    f"({seconds_to_dhms(current_sec)} / {seconds_to_dhms(desired_sec)})"
                )
                continue

            needed_sec = desired_sec - current_sec
            logger.info(
                f"Queuing {app_name} ({app_id}) — "
                f"current ({seconds_to_dhms(current_sec)}) < target ({seconds_to_dhms(desired_sec)}) | "
                f"needed: {seconds_to_dhms(needed_sec)}"
            )
            games_to_idle.append(
                {
                    "app_id": str(app_id),
                    "app_name": app_name,
                    "needed_seconds": needed_sec,
                    "current_seconds": current_sec,
                    "desired_seconds": desired_sec,
                }
            )

        if not games_to_idle:
            logger.info("All games have reached their target playtimes!")
            return True

        if config.settings.login_mode == LoginMode.AGENT:
            logger.info(f"Starting Agent mode batch idling for {len(games_to_idle)} games...")
            try:
                return self._idle_manifest_agent(games_to_idle)
            except KeyboardInterrupt:
                logger.warning("Batch idling interrupted.")
                return False

        logger.info(
            f"Starting Client mode idle processes for {len(games_to_idle)} games (max workers: {self.max_processes})..."
        )
        with ThreadPoolExecutor(max_workers=self.max_processes) as executor:
            futures = {
                executor.submit(
                    self.idle_game,
                    game["app_id"],
                    game["needed_seconds"],
                ): game
                for game in games_to_idle
            }

            for future in as_completed(futures):
                game = futures[future]
                try:
                    future.result()
                    logger.info(f"Completed idling for {game['app_name']} ({game['app_id']})")
                except Exception as e:
                    logger.error(f"Error idling {game['app_name']} ({game['app_id']}): {e}")

        return True

    def check_manifest_idle(self, games_list: list[IdleGame], tolerance_seconds: int | None = None) -> IdleCheckResult:
        """Checks current owned game playtimes against desired idle playtimes in the manifest."""
        if tolerance_seconds is None:
            tolerance_seconds = config.settings.idle_tolerance_seconds

        if not games_list:
            logger.info("No games provided to check.")
            return {
                "total_games": 0,
                "games_needing_idle_count": 0,
                "games_completed_count": 0,
                "games_needing_idle": [],
                "games_completed": [],
            }

        self.owned_games = self.steam_api_client.fetch_owned_games()

        needing_idle: list[IdleGameRecord] = []
        completed: list[IdleGameRecord] = []

        for g in games_list:
            app_id = g.app_id
            if not app_id:
                continue

            app_name = g.app_name or str(app_id)
            desired_sec = g.desired_playtime_seconds

            owned_info = self.owned_games.get(app_id, {})
            current_sec = owned_info.get("playtime_forever", 0) * 60

            needed_sec = max(0, desired_sec - current_sec)
            is_completed = current_sec + tolerance_seconds >= desired_sec
            record: IdleGameRecord = {
                "app_id": app_id,
                "app_name": app_name,
                "current_playtime_seconds": current_sec,
                "desired_playtime_seconds": desired_sec,
                "needed_seconds": needed_sec,
                "is_completed": is_completed,
            }

            if is_completed:
                completed.append(record)
            else:
                needing_idle.append(record)

        return {
            "total_games": len(games_list),
            "games_needing_idle_count": len(needing_idle),
            "games_completed_count": len(completed),
            "games_needing_idle": needing_idle,
            "games_completed": completed,
        }

    # --- Internal Helpers ---

    def _idle_manifest_agent(self, games_to_idle: list[dict[str, Any]]) -> bool:
        """Batch idle games in Agent mode."""
        handler = self.steam_utility_handler
        app_ids = [g["app_id"] for g in games_to_idle]

        res = handler.execute("idle", *app_ids)
        if not res.get("success"):
            logger.error(f"Failed to set agent idle batch: {res.get('stderr') or res.get('output')}")
            return False

        try:
            remaining_games = list(games_to_idle)
            start_time = time.monotonic()

            while remaining_games:
                elapsed = time.monotonic() - start_time
                still_needing = []

                for game in remaining_games:
                    if elapsed >= game["needed_seconds"]:
                        logger.info(f"Finished idling for {game['app_name']} ({game['app_id']})")
                    else:
                        still_needing.append(game)

                remaining_games = still_needing
                if not remaining_games:
                    break

                current_app_ids = [g["app_id"] for g in remaining_games]
                handler.execute("idle", *current_app_ids)

                time.sleep(min(30, max(1, int(min(g["needed_seconds"] - elapsed for g in remaining_games)))))

            logger.info("Agent mode batch idling complete.")
            return True
        finally:
            if handler._daemon_proc and handler._daemon_proc.poll() is None:
                handler.execute("idle")


def terminate_active_idle_processes() -> None:
    """Terminate any SteamUtility idle subprocesses still running (e.g. on app exit)."""
    for proc in list(_ACTIVE_IDLE_PROCS):
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                with contextlib.suppress(Exception):
                    proc.kill()

    _ACTIVE_IDLE_PROCS.clear()
