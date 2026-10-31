import logging
from collections.abc import Iterable
from typing import Any

from textual.widget import Widget
from textual.widgets import DataTable, Input, RichLog

from steam_play_manager.core.utils import seconds_to_dhms
from steam_play_manager.ui.helpers import (
    GameRecord,
    build_achievement_status_log,
    build_achievement_status_rows,
    build_idle_status_log,
    build_idle_status_rows,
    get_owned_games_dict,
)
from steam_play_manager.ui.screens.base import BaseScreen, BaseSearchTableScreen
from steam_play_manager.ui.screens.browser import GameDetailScreen

logger = logging.getLogger(__name__)


class StatusTableScreen(BaseSearchTableScreen):
    """Shared chrome for the status tables."""

    def __init__(self, show_search: bool = True) -> None:
        super().__init__()
        self.show_search = show_search

    def compose_body(self) -> Iterable[Widget]:
        if self.show_search:
            yield Input(placeholder="Search / Filter...", id="search_input")
        yield DataTable(id="data_table")

    def on_mount(self) -> None:
        table = self.query_one("#data_table", DataTable)
        table.cursor_type = "row"
        self.setup_columns(table)
        self.loading = True
        self.run_worker(self.load_records, thread=True)
        if self.show_search:
            self.query_one("#search_input", Input).focus()

    def finish_loading(self, records: list[Any]) -> None:
        self.records = records
        self.loading = False
        self.update_table_view(self._filter_text())

    def _filter_text(self) -> str:
        if self.show_search:
            return self.query_one("#search_input", Input).value
        return ""

    def match_record(self, record: dict[str, Any], filter_text: str) -> bool:
        ft = filter_text.lower()
        return ft in record["app_name"].lower() or ft in str(record["app_id"])

    def add_record_row(self, table: DataTable, record: dict[str, Any]) -> None:
        table.add_row(*self._row_cells(record), key=str(record["app_id"]))

    def _row_cells(self, record: dict[str, Any]) -> list[Any]:
        """Ordered cell values matching setup_columns - one per column."""
        raise NotImplementedError

    def on_row_selected(self, row_key: str | None) -> None:
        row = next((r for r in self.records if str(r["app_id"]) == str(row_key)), None)
        if row:
            rec = row.get("record") or row
            self.app.push_screen(GameDetailScreen(rec))


class StatusLogScreen(BaseScreen):
    nav_label: str = "LOG"

    def __init__(self, title: str, log_text: str) -> None:
        super().__init__()
        self.sub_title = title
        self.log_text = log_text

    def compose_body(self) -> Iterable[Widget]:
        yield RichLog(id="status_log", highlight=False, markup=True)

    def on_mount(self) -> None:
        log_widget = self.query_one("#status_log", RichLog)
        log_widget.write(self.log_text)


class CategoryAchievementsScreen(StatusTableScreen):
    nav_label: str = "ACH STATUS"

    def __init__(self, initial_records: list[GameRecord]) -> None:
        super().__init__()
        self.initial_records = initial_records
        self.sub_title = "Achievement Status"

    def setup_columns(self, table: DataTable) -> None:
        table.add_columns("App ID", "App Name", "Achievements", "Manifest Ach", "Status")

    def get_records(self) -> list[dict[str, Any]]:
        return [
            {**row, "record": r}
            for r, row in zip(self.initial_records, build_achievement_status_rows(self.initial_records), strict=True)
        ]

    def _row_cells(self, record: dict[str, Any]) -> list[Any]:
        return [
            record["app_id"],
            record["app_name"],
            record["achievements"],
            record["manifest_ach"],
            record["status"],
        ]

    def on_row_selected(self, row_key: str | None) -> None:
        row = next((r for r in self.records if str(r["app_id"]) == str(row_key)), None)
        if row:
            rec = row.get("record") or row
            log_text = build_achievement_status_log(rec)
            self.app.push_screen(StatusLogScreen(f"{rec['app_name']} - Achievement Schedule Log", log_text))


class IdleStatusScreen(StatusTableScreen):
    nav_label: str = "IDLE STATUS"

    def __init__(self, initial_records: list[GameRecord], show_search: bool = True) -> None:
        super().__init__(show_search=show_search)
        self.initial_records = initial_records
        self.sub_title = "Idle Status"
        self._bare = len(initial_records) == 1

    def setup_columns(self, table: DataTable) -> None:
        if self._bare:
            table.add_columns("App ID", "App Name", "Playtime", "Status")
        else:
            table.add_columns("App ID", "App Name", "Playtime", "Manifest PT", "Remaining", "Status")

    def get_records(self) -> list[dict[str, Any]]:
        owned = get_owned_games_dict()
        if self._bare:
            rec = self.initial_records[0]
            info = owned.get(rec["app_id"], {}) if owned else {}
            playtime_sec = int(info.get("playtime_forever", 0)) * 60
            playtime = seconds_to_dhms(playtime_sec)
            status = "[yellow]UNPLAYED[/yellow]" if playtime_sec == 0 else "[cyan]IN PROGRESS[/cyan]"
            return [
                {
                    "app_id": rec["app_id"],
                    "app_name": rec["app_name"],
                    "playtime": playtime,
                    "status": status,
                    "record": rec,
                }
            ]
        return [
            {**row, "record": r, "_owned": owned}
            for r, row in zip(self.initial_records, build_idle_status_rows(self.initial_records, owned), strict=True)
        ]

    def _row_cells(self, record: dict[str, Any]) -> list[Any]:
        if self._bare:
            return [
                record["app_id"],
                record["app_name"],
                record["playtime"],
                record["status"],
            ]
        return [
            record["app_id"],
            record["app_name"],
            record["playtime"],
            record["manifest_pt"],
            record["remaining"],
            record["status"],
        ]

    def on_row_selected(self, row_key: str | None) -> None:
        row = next((r for r in self.records if str(r["app_id"]) == str(row_key)), None)
        if row:
            rec = row.get("record") or row
            owned = row.get("_owned") or get_owned_games_dict()
            log_text = build_idle_status_log(rec, owned)
            self.app.push_screen(StatusLogScreen(f"{rec['app_name']} - Idler Status Log", log_text))
