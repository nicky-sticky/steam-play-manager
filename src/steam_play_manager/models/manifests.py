from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from steam_play_manager.core.utils import read_json
from steam_play_manager.models.config import _as_categories, _as_int, _as_str

_ManifestT = TypeVar("_ManifestT", bound="BaseManifest")


@dataclass
class BaseManifest:
    """Shared structure and validation for all manifest types."""

    categories: dict[str, list[int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.categories, dict):
            raise TypeError("Manifest 'categories' must be a dictionary mapping string keys to list of app IDs.")
        for cat_name, app_ids in self.categories.items():
            if not isinstance(app_ids, list) or not all(isinstance(aid, int) for aid in app_ids):
                raise TypeError(f"Category '{cat_name}' app IDs must be a list of integers.")

    @classmethod
    def from_dict(cls, data: dict) -> _ManifestT:
        """Build a manifest from raw JSON data (implemented by subclasses)."""
        raise NotImplementedError

    @classmethod
    def load_from_file(cls: type[_ManifestT], path: Path) -> _ManifestT:
        """Reads and constructs the manifest directly from a file path."""
        data = read_json(path)
        return cls.from_dict(data)


@dataclass
class BaseGame:
    """Shared App ID / App Name coercion for manifest game entries."""

    app_id: int
    app_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.app_id, int) or self.app_id <= 0:
            raise ValueError("Game App ID must be a positive integer.")

        self.app_name = (self.app_name or "").strip()
        if not self.app_name:
            self.app_name = f"App {self.app_id}"


@dataclass
class Achievement:
    """A single achievement entry in an unlock manifest."""

    apiname: str
    name: str = ""
    delay_seconds: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.apiname, str) or not self.apiname.strip():
            raise ValueError("Achievement 'apiname' must be a non-empty string.")

        self.apiname = self.apiname.strip()
        self.name = (self.name or "").strip()

        if not isinstance(self.delay_seconds, int) or self.delay_seconds < 0:
            raise ValueError("Achievement 'delay_seconds' must be a non-negative integer.")

    @classmethod
    def from_dict(cls, data: dict) -> "Achievement":
        """Build an Achievement from raw JSON data."""
        return cls(
            apiname=_as_str(data.get("apiname")),
            name=_as_str(data.get("name")),
            delay_seconds=_as_int(data.get("delay_seconds"), 0),
        )


@dataclass
class UnlockGame(BaseGame):
    """A game entry from unlock-manifest.json (achievement unlocking)."""

    achievements: list[Achievement]

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.achievements, list):
            raise TypeError("Game 'achievements' must be a list.")

    @classmethod
    def from_dict(cls, data: dict) -> "UnlockGame":
        """Build an UnlockGame from raw JSON data."""
        return cls(
            app_id=_as_int(data.get("app_id"), 0),
            app_name=_as_str(data.get("app_name")),
            achievements=[Achievement.from_dict(a) for a in data.get("achievements", [])],
        )


@dataclass
class UnlockManifest(BaseManifest):
    """Top-level structure of unlock-manifest.json."""

    games: list[UnlockGame] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.games, list):
            raise TypeError("Manifest 'games' must be a list.")

    @classmethod
    def from_dict(cls, data: dict) -> "UnlockManifest":
        """Build an UnlockManifest from raw JSON data."""
        return cls(
            categories=_as_categories(data.get("categories")),
            games=[UnlockGame.from_dict(g) for g in data.get("games", [])],
        )


@dataclass
class IdleGame(BaseGame):
    """A game entry from idle-manifest.json (playtime idling)."""

    desired_playtime_seconds: int

    def __post_init__(self) -> None:
        super().__post_init__()

        if not isinstance(self.desired_playtime_seconds, int) or self.desired_playtime_seconds < 0:
            raise ValueError("Game 'desired_playtime_seconds' must be a non-negative integer.")

    @classmethod
    def from_dict(cls, data: dict) -> "IdleGame":
        """Build an IdleGame from raw JSON data."""
        return cls(
            app_id=_as_int(data.get("app_id"), 0),
            app_name=_as_str(data.get("app_name")),
            desired_playtime_seconds=_as_int(data.get("desired_playtime_seconds"), -1),
        )


@dataclass
class IdleManifest(BaseManifest):
    """Top-level structure of idle-manifest.json."""

    games: list[IdleGame] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.games, list):
            raise TypeError("Manifest 'games' must be a list.")

    @classmethod
    def from_dict(cls, data: dict) -> "IdleManifest":
        """Build an IdleManifest from raw JSON data."""
        return cls(
            categories=_as_categories(data.get("categories")),
            games=[IdleGame.from_dict(g) for g in data.get("games", [])],
        )
