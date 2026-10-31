import logging

from steam_play_manager.core import config
from steam_play_manager.core.utils import fetch_api_data, ts_to_date
from steam_play_manager.models import OwnedGame, ProfileAchievement

logger = logging.getLogger(__name__)


class SteamWebApiClient:
    """Handles communication with the external Steam Web API endpoints."""

    def __init__(
        self, steam_id: str | None = None, api_key: str | None = None, api_base_url: str | None = None
    ) -> None:
        self.steam_id = steam_id or config.settings.steam_id
        self.api_key = api_key or config.settings.steam_api_key
        self.api_base_url = api_base_url or config.settings.steam_api_base_url

    def fetch_owned_games(self) -> dict[int, OwnedGame]:
        """Fetch owned games dict for a Steam ID from Web API."""
        if not self.steam_id:
            logger.error("Cannot fetch owned games: Missing STEAM_ID.")
            return {}

        url = f"{self.api_base_url}/IPlayerService/GetOwnedGames/v0001/"
        params = {"steamid": str(self.steam_id), "include_appinfo": "1", "key": self.api_key}
        response = fetch_api_data(url, params)

        if not response or "response" not in response:
            return {}

        games_data: dict[int, OwnedGame] = {}
        for game in response["response"].get("games", []):
            app_id = game["appid"]
            games_data[app_id] = {
                "name": game.get("name", "Unknown"),
                "playtime_forever": game.get("playtime_forever", 0),
                "rtime_last_played": game.get("rtime_last_played", 0),
            }

        if not games_data:
            logger.error(f"No game data found for Steam ID {self.steam_id}")
            return {}

        logger.info(f"Found {len(games_data)} owned games.")
        return games_data

    def fetch_profile_achievements(self, app_id: str | int) -> dict[str, ProfileAchievement]:
        """Fetch achievement unlock timestamps for a profile from Steam Web API."""
        if not self.steam_id or not self.api_key:
            return {}

        url = f"{self.api_base_url}/ISteamUserStats/GetPlayerAchievements/v0001/"
        params = {"steamid": str(self.steam_id), "appid": str(app_id), "l": "english", "key": self.api_key}
        response = fetch_api_data(url, params)
        if not response or ("playerstats" not in response and "response" not in response):
            return {}

        achievements_data: dict[str, ProfileAchievement] = {}
        stats = response.get("playerstats") or response.get("response", {})
        if stats.get("achievements"):
            for ach in stats["achievements"]:
                is_achieved = ach.get("achieved") == 1
                unlocktime_ts = ach.get("unlocktime") if is_achieved else None
                achievements_data[ach["apiname"]] = {
                    "apiname": ach["apiname"],
                    "name": ach.get("name", ach["apiname"]),
                    "achieved": is_achieved,
                    "unlocktime_ts": unlocktime_ts,
                    "unlocktime_date": ts_to_date(unlocktime_ts) if unlocktime_ts else "",
                }

        if not achievements_data:
            logger.warning(f"No profile achievement data returned for Steam ID {self.steam_id} and App ID {app_id}")
            return {}

        return achievements_data
