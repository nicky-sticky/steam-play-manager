from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

_E = TypeVar("_E", bound=StrEnum)


def _as_str(value: Any, default: str = "") -> str:
    """Coerce value to str, falling back to default when missing."""
    return str(value) if value is not None else default


def _as_int(value: Any, default: int = 0) -> int:
    """Coerce value to int, falling back to default when missing or invalid."""
    if value is not None:
        try:
            return int(value)
        except (ValueError, TypeError):
            pass
    return default


def _as_bool(value: Any, default: bool = True) -> bool:
    """Coerce value to bool, falling back to default when missing or invalid."""
    if value is not None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        if isinstance(value, (int, float)):
            return bool(value)
    return default


def _as_categories(value: Any, default: dict[str, list[int]] | None = None) -> dict[str, list[int]]:
    """Coerce raw manifest categories {name: [app_id, ...]} tolerantly."""
    if not isinstance(value, dict):
        return dict(default) if default else {}
    return {
        str(cat_name): [_as_int(aid, 0) for aid in app_ids]
        for cat_name, app_ids in value.items()
        if isinstance(app_ids, list)
    }


def _as_enum(value: Any, enum_type: type[_E], default: _E) -> _E:
    """Coerce value to an enum member, falling back to the default member."""
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value.strip().lower())
        except ValueError:
            return default
    return default


class LoginMode(StrEnum):
    """Authentication mode: SteamUtility agent session or local Steam client."""

    AGENT = "agent"
    CLIENT = "client"


@dataclass(frozen=True)
class FileConfig:
    """Shape of config.toml — one field per documented key."""

    steam_path: str = ""
    login_mode: LoginMode = LoginMode.AGENT
    unlock_delay_tolerance_seconds: int = 10
    idle_tolerance_seconds: int = 60
    max_client_idle_workers: int = 5

    @classmethod
    def from_dict(cls, data: dict) -> "FileConfig":
        """Build a FileConfig from raw TOML data, coercing values tolerantly."""
        return cls(
            steam_path=_as_str(data.get("steam_path")),
            login_mode=_as_enum(data.get("login_mode"), LoginMode, LoginMode.AGENT),
            unlock_delay_tolerance_seconds=_as_int(data.get("unlock_delay_tolerance_seconds"), 10),
            idle_tolerance_seconds=_as_int(data.get("idle_tolerance_seconds"), 60),
            max_client_idle_workers=_as_int(data.get("max_client_idle_workers"), 5),
        )


@dataclass
class TokensFile:
    """Refresh tokens for SteamUtility agent sessions (tokens.json)."""

    tokens: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> "TokensFile":
        """Build a TokensFile from raw JSON, keeping only valid entries."""
        if not isinstance(data, dict):
            return cls()
        return cls(tokens={str(key): value for key, value in data.items() if isinstance(value, str) and value})

    def set(self, username: str, token: str) -> None:
        """Store the refresh token for a username (normalized to lowercase)."""
        self.tokens[username.strip().lower()] = token

    def get(self, username: str) -> str | None:
        """Return the saved refresh token for a username, or None."""
        return self.tokens.get(username.strip().lower())
