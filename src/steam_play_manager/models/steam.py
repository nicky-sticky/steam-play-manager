from typing import TypedDict


class LibraryGame(TypedDict):
    """A single game entry in games.json."""

    app_id: int
    app_name: str
    playtime_seconds: int
    last_played_ts: int
    last_played_date: str


class LibraryFile(TypedDict):
    """Top-level structure of games.json."""

    last_synced_at: str
    total_games: int
    games: list[LibraryGame]


class OwnedGame(TypedDict):
    """A single owned game as returned by the Steam Web API client."""

    name: str
    playtime_forever: int
    rtime_last_played: int


class ProfileAchievement(TypedDict):
    """A profile achievement as returned by the Steam Web API client."""

    apiname: str
    name: str
    achieved: bool
    unlocktime_ts: int | None
    unlocktime_date: str
