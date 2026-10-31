import logging
from functools import cache
from typing import cast

from steam_play_manager.core import config
from steam_play_manager.core.manifests import load_manifest
from steam_play_manager.core.utils import read_json
from steam_play_manager.models import IdleManifest, LibraryGame, OwnedGame, ProfileAchievement, UnlockManifest
from steam_play_manager.services.steam_client import SteamWebApiClient

logger = logging.getLogger(__name__)


def get_owned_games_dict() -> dict[int, OwnedGame]:
    """Fetch owned games from the Steam API, or {} when unconfigured/unreachable."""
    steam_id = config.settings.steam_id
    steam_api_key = config.settings.steam_api_key
    if not steam_id or not steam_api_key:
        return {}
    try:
        return SteamWebApiClient().fetch_owned_games()
    except Exception:
        logger.exception("Failed to fetch owned games from the Steam API")
        return {}


def get_profile_achievements(app_id: str | int) -> dict[str, ProfileAchievement]:
    """Fetch achievement unlock data for a game, or {} when unconfigured/unreachable."""
    if not config.settings.steam_id or not config.settings.steam_api_key:
        return {}
    try:
        return SteamWebApiClient().fetch_profile_achievements(app_id)
    except Exception:
        logger.exception("Failed to fetch profile achievements from the Steam API")
        return {}


def get_library_games() -> dict[int, LibraryGame]:
    """Return the synced Steam library as an app ID to game mapping (data/games.json)."""
    data = read_json(config.settings.games_data_path)
    if not isinstance(data, dict):
        return {}
    return {
        int(g["app_id"]): cast(LibraryGame, g)
        for g in data.get("games", [])
        if isinstance(g, dict) and g.get("app_id") is not None
    }


@cache
def get_unlock_manifest() -> UnlockManifest:
    """Load and cache the unlock manifest (empty manifest on missing/invalid file)."""
    try:
        return load_manifest(config.settings.unlock_manifest_path)
    except Exception as e:
        logger.error(f"Failed to load unlock manifest: {e}")
        return UnlockManifest()


@cache
def get_idle_manifest() -> IdleManifest:
    """Load and cache the idle manifest (empty manifest on missing/invalid file)."""
    try:
        return load_manifest(config.settings.idle_manifest_path, IdleManifest)
    except Exception as e:
        logger.error(f"Failed to load idle manifest: {e}")
        return IdleManifest()


def get_category_options(manifest_data: UnlockManifest | IdleManifest) -> list[str]:
    """Return category keys defined in the manifest (empty list if no categories defined)."""
    return [k for k, ids in manifest_data.categories.items() if ids]
