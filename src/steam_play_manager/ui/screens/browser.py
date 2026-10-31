import logging
from collections.abc import Callable, Iterable
from typing import Any

from textual.widget import Widget
from textual.widgets import DataTable, OptionList, Static
from textual.widgets.option_list import Option

from steam_play_manager import tasks
from steam_play_manager.core.utils import seconds_to_dhms
from steam_play_manager.models import ProfileAchievement
from steam_play_manager.services.steam_utility import get_steam_utility_handler
from steam_play_manager.ui.helpers import (
    GameRecord,
    get_game_browser_records,
    get_owned_games_dict,
    get_profile_achievements,
)
from steam_play_manager.ui.screens.base import BaseDataTableScreen, BaseOptionListScreen, BaseSearchTableScreen
from steam_play_manager.ui.screens.common import (
    ConfirmScreen,
    ProgressLogScreen,
    TextPromptScreen,
)

logger = logging.getLogger(__name__)


class GameBrowserTableScreen(BaseSearchTableScreen):
    """Shared game-table rendering for every game-list screen."""

    def setup_columns(self, table: DataTable) -> None:
        table.add_columns("App ID", "App Name", "Playtime", "Last Played")

    def match_record(self, record: GameRecord, filter_text: str) -> bool:
        ft = filter_text.lower()
        return ft in record["app_name"].lower() or ft in str(record["app_id"])

    def add_record_row(self, table: DataTable, record: GameRecord) -> None:
        table.add_row(
            str(record["app_id"]),
            record["app_name"],
            seconds_to_dhms(record.get("playtime_seconds", 0)),
            record.get("last_played_date") or "[dim]—[/dim]",
            key=str(record["app_id"]),
        )

    def on_row_selected(self, row_key: str | None) -> None:
        record = next((r for r in self.records if str(r["app_id"]) == str(row_key)), None)
        if record:
            self.app.push_screen(GameDetailScreen(record))


class ManifestGameSelectScreen(GameBrowserTableScreen):
    nav_label: str = "SELECT"

    def __init__(
        self, initial_records: list[GameRecord], on_select: Callable[[GameRecord], None] | None = None
    ) -> None:
        super().__init__()
        self.initial_records = initial_records
        self.on_select = on_select
        self.sub_title = "Select Manifest Game"

    def get_records(self) -> list[GameRecord]:
        return self.initial_records

    def on_row_selected(self, row_key: str | None) -> None:
        record = next((r for r in self.records if str(r["app_id"]) == str(row_key)), None)
        if record:
            if self.on_select:
                self.on_select(record)
            else:
                self.app.push_screen(GameDetailScreen(record))


class GameSelectScreen(GameBrowserTableScreen):
    nav_label: str = "GAMES"

    def __init__(self) -> None:
        super().__init__()
        self.sub_title = "Game Browser"

    def get_records(self) -> list[GameRecord]:
        records = get_game_browser_records()
        owned = get_owned_games_dict()  # live from Steam API; {} if unconfigured/unreachable
        if not owned:
            return records
        existing = {r["app_id"] for r in records}
        for app_id, info in owned.items():
            if app_id in existing:
                continue
            records.append(
                {
                    "app_id": app_id,
                    "app_name": info.get("name") or f"App {app_id}",
                    "playtime_seconds": int(info.get("playtime_forever", 0)) * 60,
                    "last_played_date": "",
                    "category": None,
                    "unlock_config": None,
                    "idle_config": None,
                }
            )
        return sorted(records, key=lambda r: r["app_name"].lower())

    def on_row_selected(self, row_key: str | None) -> None:
        record = next((r for r in self.records if str(r["app_id"]) == str(row_key)), None)
        if record:
            self.app.push_screen(GameDetailScreen(record))


class GameDetailScreen(BaseOptionListScreen):
    nav_label: str = "GAME"

    def __init__(self, record: GameRecord) -> None:
        super().__init__()
        self.record = record
        self.sub_title = record["app_name"]

    def compose_body(self) -> Iterable[Widget]:
        # Summary is populated by a worker on mount; computing it here would block the UI on fetches.
        yield Static("[dim]Loading...[/dim]", id="summary")
        yield OptionList(id="options_list")

    def on_mount(self) -> None:
        super().on_mount()
        self.run_worker(self._refresh_summary, thread=True)

    def _header_text(self) -> str:
        rec = self.record
        lines = [
            "[$text]Game:[/] - [$warning]Select an action to execute:[/]",
            f"[$text]App ID:[/]      [dim]{rec['app_id']}[/dim]",
            f"[$text]App Name:[/]   [dim]{rec['app_name']}[/dim]",
            f"[$text]Playtime:[/]   [dim]{seconds_to_dhms(rec.get('playtime_seconds', 0))}[/dim]",
        ]
        app_id = str(rec["app_id"])
        total_ach = 0
        unlocked_count = 0
        # Prefer the direct SteamUtility fetch: it reflects lock/unlock changes immediately,
        # while GetPlayerAchievements is Web-API-cached and can lag for minutes.
        steam_ach = get_steam_utility_handler().fetch_achievements(app_id) or []
        if steam_ach:
            total_ach = len(steam_ach)
            unlocked_count = sum(1 for a in steam_ach if a.get("achieved"))
        if not total_ach:
            profile = get_profile_achievements(app_id)
            if profile:
                total_ach = len(profile)
                unlocked_count = sum(1 for a in profile.values() if a.get("achieved"))

        ach_str = f"{unlocked_count}/{total_ach}" if total_ach > 0 else "[dim]—[/dim]"
        lines.append(f"[$text]Achievements:[/] [dim]{ach_str}[/dim]")
        return "\n".join(lines)

    def on_screen_resume(self) -> None:
        """Recompute the summary after returning from an operation or achievement screen."""
        super().on_screen_resume()
        self.run_worker(self._refresh_summary, thread=True)

    def _refresh_summary(self) -> None:
        try:
            text = self._header_text()
        except Exception:
            logger.exception("Failed to refresh game summary")
            return
        try:
            self.app.call_from_thread(self.query_one("#summary", Static).update, text)
        except Exception:
            # The screen may have been popped while the fetch was in flight.
            logger.exception("Failed to update game summary")

    def get_options(self) -> list[Option | None]:
        return [
            Option(
                "[bold $text]>  SYNC GAME[/]\n   [dim]Fetch fresh playtime and achievement details from Steam[/dim]",
                id="opt_sync",
            ),
            None,
            Option(
                "[bold $text]>  IDLE GAME[/]\n   [dim]Launch idle process for a custom duration in seconds[/dim]",
                id="opt_idle_duration",
            ),
            None,
            Option(
                "[bold $text]>  TOGGLE ACHIEVEMENT[/]\n   [dim]Browse achievements and toggle unlock/lock state[/dim]",
                id="opt_ach_toggle",
            ),
            None,
            Option(
                "[bold $text]>  UNLOCK ACHIEVEMENT[/]\n"
                "   [dim]Browse locked achievements and unlock individually[/dim]",
                id="opt_ach_unlock",
            ),
            None,
            Option(
                "[bold $text]>  LOCK ACHIEVEMENT[/]\n"
                "   [dim]Browse unlocked achievements and relock individually[/dim]",
                id="opt_ach_lock",
            ),
            None,
            Option(
                "[$error]▶  UNLOCK ALL ACHIEVEMENTS[/]\n"
                "   [dim]Instantly unlock all remaining achievements for this game[/dim]",
                id="opt_ach_unlock_all",
            ),
            None,
            Option(
                "[$error]▶  LOCK ALL ACHIEVEMENTS[/]\n   [dim]Instantly lock all achievements for this game[/dim]",
                id="opt_ach_lock_all",
            ),
        ]

    def on_option_selected(self, option_id: str | None, option_prompt: str) -> None:
        rec = self.record
        app_id = str(rec["app_id"])

        if option_id == "opt_sync":
            self._run("Syncing Game", lambda: tasks.run_sync_lib(target_app_id=int(app_id)))
        elif option_id == "opt_idle_duration":
            self.app.push_screen(
                TextPromptScreen(
                    "Idle Duration",
                    "Enter duration in seconds...",
                    lambda val: self._run("Idler Running", lambda: tasks.run_idle_game(app_id, int(val))),
                    allow_blank=False,
                )
            )
        elif option_id == "opt_ach_toggle":
            self.app.push_screen(AchievementSelectScreen(app_id, rec["app_name"], "toggle"))
        elif option_id == "opt_ach_unlock":
            self.app.push_screen(AchievementSelectScreen(app_id, rec["app_name"], "unlock"))
        elif option_id == "opt_ach_lock":
            self.app.push_screen(AchievementSelectScreen(app_id, rec["app_name"], "lock"))
        elif option_id == "opt_ach_unlock_all":
            self._confirm(
                "Unlock ALL achievements for this game?",
                lambda: self._run("Unlocker Running", lambda: tasks.run_unlock_all_game_ach(app_id)),
            )
        elif option_id == "opt_ach_lock_all":
            self._confirm(
                "Lock ALL achievements for this game?",
                lambda: self._run("Locker Running", lambda: tasks.run_lock_all_game_ach(app_id)),
            )

    def _run(self, title: str, callable_: Callable[[], Any]) -> None:
        self.app.push_screen(ProgressLogScreen(title, callable_))

    def _confirm(self, message: str, on_yes: Callable[[], None]) -> None:
        self.app.push_screen(ConfirmScreen(message, on_yes))


class AchievementSelectScreen(BaseDataTableScreen):
    nav_label: str = "ACHIEVEMENTS"

    def __init__(self, app_id: str, app_name: str, action: str) -> None:
        super().__init__()
        self.sub_title = f"{action.title()} Achievement — {app_name}"
        self.app_id = app_id
        self.app_name = app_name
        self.action = action

    def setup_columns(self, table: DataTable) -> None:
        table.add_columns("#", "API Name", "Achievement Name", "Unlock Time", "Status")

    def get_records(self, profile: dict[str, ProfileAchievement] | None = None) -> list[tuple[str, str, str, str, str]]:
        """Return achievement rows for the current action, enriched with the given Web API profile."""
        app_id = self.app_id
        profile = profile or {}
        rows: list[tuple[str, str, str, str, str]] = []
        # The direct SteamUtility fetch reflects lock/unlock changes immediately; the Web
        # API profile lags, so it is only used to enrich unlock times (never fetched here).
        steam_ach = get_steam_utility_handler().fetch_achievements(app_id) or []
        for index, a in enumerate(steam_ach, start=1):
            apiname = a["name"]
            row = self._build_row(
                index,
                apiname,
                a.get("friendly_name"),
                (profile.get(apiname) or {}).get("unlocktime_date"),
                bool(a.get("achieved")),
            )
            if row is not None:
                rows.append(row)
        if steam_ach:
            return rows

        # Fall back to the Web API profile when the local utility reported no data.
        for index, (apiname, ach) in enumerate(profile.items(), start=1):
            row = self._build_row(
                index, apiname, ach.get("name"), ach.get("unlocktime_date"), bool(ach.get("achieved"))
            )
            if row is not None:
                rows.append(row)
        return rows

    def _build_row(
        self, index: int, apiname: str, friendly_name: str | None, unlock_time: str | None, is_achieved: bool
    ) -> tuple[str, str, str, str, str] | None:
        """Return a table row for the current action, or None when the achievement doesn't qualify."""
        if self.action == "unlock" and is_achieved:
            return None
        if self.action == "lock" and not is_achieved:
            return None
        status = "[green]UNLOCKED[/green]" if is_achieved else "[yellow]LOCKED[/yellow]"
        # The Web API profile lags the fresh SteamUtility state, so a stale profile can still
        # carry an unlock time for an achievement the fresh source reports as locked.
        display_time = unlock_time if is_achieved else None
        return (str(index), apiname, friendly_name or apiname, display_time or "[dim]—[/dim]", status)

    def load_and_populate(self) -> None:
        """Load records from the local utility, then enrich unlock times in the background."""
        super().load_and_populate()
        self.run_worker(self._enrich_records, thread=True)

    def populate(self, records: list[tuple[str, str, str, str, str]]) -> None:
        table = self.query_one("#data_table", DataTable)
        table.clear()
        for row in records:
            # row[1] is the API Name used as the row key
            table.add_row(*row, key=row[1])
        self.loading = False

    def on_screen_resume(self) -> None:
        """Reload the achievement list after returning from an operation screen."""
        super().on_screen_resume()
        self.run_worker(self.load_and_populate, thread=True)

    def _enrich_records(self) -> None:
        """Fetch Web API unlock times in the background and repopulate when available."""
        profile = get_profile_achievements(self.app_id)
        if not profile:
            return
        try:
            records = self.get_records(profile)
        except Exception:
            logger.exception("Failed to enrich achievement records with unlock times")
            return
        try:
            self.app.call_from_thread(self.populate, records)
        except Exception:
            logger.exception("Failed to repopulate achievement records after enrichment")

    def on_row_selected(self, row_key: str | None) -> None:
        apiname = str(row_key)
        if self.action == "toggle":
            self.app.push_screen(
                ProgressLogScreen("Toggling Achievement", lambda: tasks.run_toggle_game_ach(self.app_id, apiname))
            )
        elif self.action == "unlock":
            self.app.push_screen(
                ProgressLogScreen("Unlocking Achievement", lambda: tasks.run_unlock_game_ach(self.app_id, apiname))
            )
        elif self.action == "lock":
            self.app.push_screen(
                ProgressLogScreen("Locking Achievement", lambda: tasks.run_lock_game_ach(self.app_id, apiname))
            )
