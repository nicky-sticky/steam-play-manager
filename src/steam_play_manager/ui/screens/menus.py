import logging
import threading
from collections.abc import Iterable
from typing import ClassVar

from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from steam_play_manager import tasks
from steam_play_manager.core import config
from steam_play_manager.core.auth import verify_steam_authentication
from steam_play_manager.models import IdleGame, IdleManifest, LoginMode, UnlockGame, UnlockManifest
from steam_play_manager.services.steam_utility import get_steam_utility_handler, is_steam_running
from steam_play_manager.ui.helpers import (
    GameRecord,
    get_category_games,
    get_category_options,
    get_game_browser_records,
    get_idle_manifest,
    get_library_games,
    get_owned_games_dict,
    get_unlock_manifest,
)
from steam_play_manager.ui.screens.base import BaseOptionListScreen, BaseScreen
from steam_play_manager.ui.screens.browser import GameSelectScreen, ManifestGameSelectScreen
from steam_play_manager.ui.screens.common import (
    ConfirmScreen,
    InfoScreen,
    OptionSelectionScreen,
    ProgressLogScreen,
    TextPromptScreen,
)
from steam_play_manager.ui.screens.status import CategoryAchievementsScreen, IdleStatusScreen
from steam_play_manager.ui.screens.tasks import TasksMenuScreen

logger = logging.getLogger(__name__)


class MainMenuScreen(BaseScreen):
    nav_label: str = "HOME"

    def __init__(self) -> None:
        super().__init__()
        self.sub_title = "Steam Play Manager"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Exit"),
        Binding("v", "verify_auth", "Verify Auth"),
    ]

    def compose_body(self) -> Iterable[Widget]:
        yield Static(id="summary")
        yield OptionList(
            Option(
                "[bold $text]>  AUTHENTICATE[/]\n"
                "   [dim]Verify credentials or complete Steam Guard authentication[/dim]",
                id="opt_auth",
            ),
            None,
            Option(
                "[bold $text]>  SYNC LIBRARY[/]\n"
                "   [dim]Fetch owned games, playtime, and achievement data from Steam[/dim]",
                id="opt_sync",
            ),
            None,
            Option(
                "[bold $text]>  SELECT GAME[/]\n"
                "   [dim]Browse library games to idle, sync, or toggle achievements[/dim]",
                id="opt_select",
            ),
            None,
            Option(
                "[bold $text]>  MANIFEST GAME IDLER[/]\n"
                "   [dim]Idle configured games individually, by category, or in bulk[/dim]",
                id="opt_bulk_idle",
            ),
            None,
            Option(
                "[bold $text]>  MANIFEST GAME UNLOCKER[/]\n"
                "   [dim]Unlock configured game achievements by schedule or category[/dim]",
                id="opt_bulk_unlock",
            ),
            None,
            Option(
                "[bold $text]>  MANAGE TASKS[/]\n"
                "   [dim]Queue, inspect, run, or reset background automation tasks[/dim]",
                id="opt_tasks",
            ),
            None,
            Option(
                "[$error]✕  EXIT[/]\n   [dim]Exit Steam Play Manager[/dim]",
                id="opt_exit",
            ),
            id="menu_options",
        )

    def on_mount(self) -> None:
        self.update_summary()
        # Automatically connect in background if in agent mode and saved token exists
        if config.settings.login_mode == LoginMode.AGENT:
            handler = get_steam_utility_handler()
            if handler.has_saved_token() and not handler.is_authenticated():
                self.run_worker(self._auto_connect_agent, thread=True)

    def _auto_connect_agent(self) -> None:
        try:
            verify_steam_authentication()
            self.app.call_from_thread(self.update_summary)
        except Exception as e:
            logger.error(f"Background agent auto-connect failed: {e}")

    def on_screen_resume(self) -> None:
        super().on_screen_resume()
        self.update_summary()

    def action_quit(self) -> None:
        self.app.exit()

    def action_verify_auth(self) -> None:
        self.app.push_screen(
            ProgressLogScreen("Auth Verification", lambda: verify_steam_authentication(self._guard_code_prompt))
        )

    def _guard_code_prompt(self, message: str) -> str | None:
        """Blocks the calling (worker) thread until the user enters a Steam Guard code or cancels."""
        result: dict[str, str | None] = {}
        done = threading.Event()

        def on_submit(value: str) -> None:
            result["code"] = value
            done.set()

        def on_cancel() -> None:
            result["code"] = None
            done.set()

        self.app.call_from_thread(
            self.app.push_screen,
            TextPromptScreen("Steam Guard", message, on_submit, on_cancel=on_cancel, allow_blank=False),
        )
        done.wait()
        return result.get("code")

    def update_summary(self) -> None:
        """Refresh the owned-game count shown in the summary."""
        self._refresh_owned_count()

    def _build_summary(self, owned_count: int) -> str:
        if config.settings.login_mode == LoginMode.CLIENT:
            steam_state = "running" if is_steam_running() else "not running"
        else:
            handler = get_steam_utility_handler()
            if handler.is_authenticated():
                steam_state = "agent · authenticated"
            elif handler.has_saved_token():
                steam_state = "agent · connecting (saved token)..."
            else:
                steam_state = "agent · not authenticated"
        return (
            f"[$text]Owned:[/]  [dim]{owned_count} games in Steam library[/dim]\n"
            f"[$text]Steam:[/]  [dim]{steam_state}[/dim]"
        )

    def _refresh_owned_count(self) -> None:
        if getattr(self, "_owned_refresh_running", False):
            return
        self._owned_refresh_running = True
        cached_count = len(get_library_games())
        self.query_one("#summary", Static).update(self._build_summary(cached_count))
        self.run_worker(self._fetch_owned_count, thread=True)

    def _fetch_owned_count(self) -> None:
        try:
            owned = get_owned_games_dict()
            count = len(owned) if owned else None
        except Exception:
            count = None
        self.app.call_from_thread(self._apply_owned_count, count)

    def _apply_owned_count(self, count: int | None) -> None:
        self._owned_refresh_running = False
        if count is not None:
            self.query_one("#summary", Static).update(self._build_summary(count))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id
        if opt_id == "opt_auth":
            self.action_verify_auth()
        elif opt_id == "opt_sync":
            self.app.push_screen(ProgressLogScreen("Sync Running", lambda: tasks.run_sync_lib()))
        elif opt_id == "opt_select":
            self.app.push_screen(GameSelectScreen())
        elif opt_id == "opt_bulk_idle":
            self.app.push_screen(IdlerMenuScreen())
        elif opt_id == "opt_bulk_unlock":
            self.app.push_screen(UnlockerMenuScreen())
        elif opt_id == "opt_tasks":
            self.app.push_screen(TasksMenuScreen())
        elif opt_id == "opt_exit":
            self.app.exit()


class IdlerMenuScreen(BaseOptionListScreen):
    nav_label: str = "IDLER"

    def __init__(self) -> None:
        super().__init__()
        self.sub_title = "Manifest Game Idler"

    @property
    def manifest(self) -> IdleManifest:
        """The loaded idle manifest."""
        return get_idle_manifest()

    @property
    def games(self) -> list[IdleGame]:
        """The manifest's configured games."""
        return self.manifest.games

    def get_records(self) -> list[GameRecord]:
        """Return the game browser records for games in the idle manifest."""
        game_ids = {g.app_id for g in self.games}
        return [r for r in get_game_browser_records() if r["app_id"] in game_ids]

    def compose_body(self) -> Iterable[Widget]:
        cats = len(get_category_options(self.manifest))
        summary = (
            f"[$text]Manifest Game Idler[/] - [$warning]Select an action to execute:[/]\n"
            f"[$text]Manifest:[/] [dim]{len(self.games)} games · {cats} categories[/dim]"
        )
        yield Static(summary, id="summary")
        yield OptionList(id="options_list")

    def get_options(self) -> list[Option | None]:
        return [
            Option(
                "[bold $text]>  VIEW IDLE STATUS[/]\n"
                "   [dim]Inspect configured games, playtime, and idle progress[/dim]",
                id="opt_idle_status",
            ),
            None,
            Option(
                "[bold $text]>  IDLE MANIFEST GAME[/]\n   [dim]Select a single manifest game to idle[/dim]",
                id="opt_select_game",
            ),
            None,
            Option(
                "[bold $text]>  IDLE MANIFEST CATEGORY GAMES (PARALLEL)[/]\n"
                "   [dim]Idle all games in a specific manifest category concurrently[/dim]",
                id="opt_select_cat",
            ),
            None,
            Option(
                "[$error]▶  IDLE ALL MANIFEST GAMES (PARALLEL)[/]\n"
                "   [dim]Launch idle processes for every game across all categories[/dim]",
                id="opt_idle_all",
            ),
        ]

    def on_option_selected(self, option_id: str | None, option_prompt: str) -> None:
        if option_id == "opt_idle_status":
            self.app.push_screen(IdleStatusScreen(self.get_records()))
        elif option_id == "opt_select_game":
            self.app.push_screen(ManifestGameSelectScreen(self.get_records(), on_select=self._on_game_selected))
        elif option_id == "opt_select_cat":
            self._select_category()
        elif option_id == "opt_idle_all":
            self.app.push_screen(
                ConfirmScreen(
                    f"Idle ALL {len(self.games)} games in the idle manifest?",
                    lambda: self.app.push_screen(
                        ProgressLogScreen("Idler Running", lambda: tasks.run_idle_manifest(self.games, "ALL GAMES"))
                    ),
                )
            )

    def _on_game_selected(self, record: GameRecord) -> None:
        game = next((g for g in self.games if g.app_id == record["app_id"]), None)
        if game is None:
            logger.warning(f"Idle manifest game not found for App ID {record['app_id']}")
            return
        self.app.push_screen(
            ProgressLogScreen("Idler Running", lambda: tasks.run_idle_manifest([game], record["app_name"]))
        )

    def _select_category(self) -> None:
        categories = get_category_options(self.manifest)
        if not categories:
            self.app.push_screen(InfoScreen("No categories defined in idle manifest."))
            return
        self.app.push_screen(
            OptionSelectionScreen(
                title="Select Category",
                options=categories,
                on_select=self._on_category_selected,
            )
        )

    def _on_category_selected(self, cat: str) -> None:
        cat_games = get_category_games(self.manifest, cat)
        self.app.push_screen(ProgressLogScreen("Idler Running", lambda: tasks.run_idle_manifest(cat_games, cat)))


class UnlockerMenuScreen(BaseOptionListScreen):
    nav_label: str = "UNLOCKER"

    def __init__(self) -> None:
        super().__init__()
        self.sub_title = "Manifest Game Unlocker"

    @property
    def manifest(self) -> UnlockManifest:
        """The loaded unlock manifest."""
        return get_unlock_manifest()

    @property
    def games(self) -> list[UnlockGame]:
        """The manifest's configured games."""
        return self.manifest.games

    def get_records(self) -> list[GameRecord]:
        """Return the game browser records for games in the unlock manifest."""
        game_ids = {g.app_id for g in self.games}
        return [r for r in get_game_browser_records() if r["app_id"] in game_ids]

    def compose_body(self) -> Iterable[Widget]:
        cats = len(get_category_options(self.manifest))
        summary = (
            f"[$text]Manifest Game Unlocker[/] - [$warning]Select an action to execute:[/]\n"
            f"[$text]Manifest:[/] [dim]{len(self.games)} games · {cats} categories[/dim]"
        )
        yield Static(summary, id="summary")
        yield OptionList(id="options_list")

    def get_options(self) -> list[Option | None]:
        return [
            Option(
                "[bold $text]>  VIEW ACHIEVEMENT STATUS[/]\n"
                "   [dim]Inspect lock/unlock progress and schedule for manifest games[/dim]",
                id="opt_ach_status",
            ),
            None,
            Option(
                "[bold $text]>  UNLOCK MANIFEST GAME ACHIEVEMENTS (SCHEDULE)[/]\n"
                "   [dim]Unlock achievements for a single game according to schedule[/dim]",
                id="opt_select_game",
            ),
            None,
            Option(
                "[bold $text]>  UNLOCK MANIFEST CATEGORY ACHIEVEMENTS (SCHEDULE)[/]\n"
                "   [dim]Unlock achievements for all games in a category concurrently[/dim]",
                id="opt_select_cat",
            ),
            None,
            Option(
                "[$error]▶  UNLOCK ALL MANIFEST ACHIEVEMENTS (SCHEDULE)[/]\n"
                "   [dim]Unlock all scheduled achievements across every configured game[/dim]",
                id="opt_unlock_all",
            ),
        ]

    def on_option_selected(self, option_id: str | None, option_prompt: str) -> None:
        if option_id == "opt_ach_status":
            self.app.push_screen(CategoryAchievementsScreen(self.get_records()))
        elif option_id == "opt_select_game":
            self.app.push_screen(ManifestGameSelectScreen(self.get_records(), on_select=self._on_game_selected))
        elif option_id == "opt_select_cat":
            self._select_category()
        elif option_id == "opt_unlock_all":
            self.app.push_screen(
                ConfirmScreen(
                    f"Unlock achievements for ALL {len(self.games)} games in the unlock manifest?",
                    lambda: self.app.push_screen(
                        ProgressLogScreen(
                            "Unlocker Running", lambda: tasks.run_unlock_manifest_ach(self.games, "ALL GAMES")
                        )
                    ),
                )
            )

    def _on_game_selected(self, record: GameRecord) -> None:
        game = next((g for g in self.games if g.app_id == record["app_id"]), None)
        if game is None:
            logger.warning(f"Unlock manifest game not found for App ID {record['app_id']}")
            return
        self.app.push_screen(
            ProgressLogScreen("Unlocker Running", lambda: tasks.run_unlock_manifest_ach([game], record["app_name"]))
        )

    def _select_category(self) -> None:
        categories = get_category_options(self.manifest)
        if not categories:
            self.app.push_screen(InfoScreen("No categories defined in unlock manifest."))
            return
        self.app.push_screen(
            OptionSelectionScreen(
                title="Select Category",
                options=categories,
                on_select=self._on_category_selected,
            )
        )

    def _on_category_selected(self, cat: str) -> None:
        cat_games = get_category_games(self.manifest, cat)
        self.app.push_screen(
            ProgressLogScreen("Unlocker Running", lambda: tasks.run_unlock_manifest_ach(cat_games, cat))
        )
