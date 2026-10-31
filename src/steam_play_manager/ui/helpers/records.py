from typing import TypedDict

from steam_play_manager.models import IdleGame, IdleManifest, UnlockGame, UnlockManifest
from steam_play_manager.ui.helpers.data import get_idle_manifest, get_library_games, get_unlock_manifest


class GameRecord(TypedDict):
    """Shape of a game record used across the TUI screens."""

    app_id: int
    app_name: str
    playtime_seconds: int
    last_played_date: str
    category: str | None
    unlock_config: UnlockGame | None
    idle_config: IdleGame | None


def _game_key(game: UnlockGame | IdleGame) -> int:
    """Return the app ID used as the record key for a manifest game."""
    return game.app_id


def _category_map(manifest_data: UnlockManifest | IdleManifest) -> dict[int, str]:
    """Map app_id (int) -> category key from a manifest's 'categories' section."""
    mapping: dict[int, str] = {}
    for cat, ids in manifest_data.categories.items():
        for aid in ids:
            mapping[aid] = cat
    return mapping


def get_game_browser_records() -> list[GameRecord]:
    """Merge the synced library + manifests into one record set for browsing."""
    library = get_library_games()
    unlock_manifest = get_unlock_manifest()
    idle_manifest = get_idle_manifest()
    unlock_cat = _category_map(unlock_manifest)
    idle_cat = _category_map(idle_manifest)

    records: dict[int, GameRecord] = {}

    def ensure(app_id: int) -> GameRecord:
        return records.setdefault(
            app_id,
            {
                "app_id": app_id,
                "app_name": "",
                "playtime_seconds": 0,
                "last_played_date": "",
                "category": None,
                "unlock_config": None,
                "idle_config": None,
            },
        )

    for game in unlock_manifest.games:
        aid = _game_key(game)
        rec = ensure(aid)
        if not rec["app_name"]:
            rec["app_name"] = game.app_name or f"App {aid}"
        rec["unlock_config"] = game
        rec["category"] = unlock_cat.get(aid) or rec["category"]

    for game in idle_manifest.games:
        aid = _game_key(game)
        rec = ensure(aid)
        if not rec["app_name"]:
            rec["app_name"] = game.app_name or f"App {aid}"
        rec["idle_config"] = game
        rec["category"] = idle_cat.get(aid) or rec["category"]

    for aid, lib in library.items():
        rec = ensure(aid)
        if not rec["app_name"]:
            rec["app_name"] = lib.get("app_name") or f"App {aid}"
        rec["playtime_seconds"] = lib.get("playtime_seconds", 0)
        rec["last_played_date"] = lib.get("last_played_date", "")

    return sorted(records.values(), key=lambda r: r["app_name"].lower())
