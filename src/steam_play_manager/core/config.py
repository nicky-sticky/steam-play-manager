import logging
import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path

from dotenv import load_dotenv

from steam_play_manager.core.utils import read_toml
from steam_play_manager.models import FileConfig, LoginMode

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    load_dotenv(env_file)


def _load_file_config() -> FileConfig:
    """Read config.toml and coerce its contents into a FileConfig model."""
    data = read_toml(PROJECT_ROOT / "config.toml") or {}
    if not isinstance(data, dict):
        logger.warning("config.toml did not contain a table; using defaults.")
        data = {}

    known = {f.name for f in fields(FileConfig)}
    for key in data:
        if key not in known:
            logger.warning(f"Ignoring unknown config.toml key '{key}'.")

    return FileConfig.from_dict(data)


@dataclass(frozen=True)
class Settings:
    """All runtime configuration settings, loaded from environment and config.toml."""

    # Credentials
    steam_id: str
    steam_api_key: str
    steam_username: str
    steam_password: str
    # Paths
    games_data_path: Path
    unlock_manifest_path: Path
    idle_manifest_path: Path
    tasks_file_path: Path
    steam_utility_path: Path
    agent_tokens_path: Path
    steam_path: Path | None
    # Config
    steam_api_base_url: str
    login_mode: LoginMode
    unlock_delay_tolerance_seconds: int
    idle_tolerance_seconds: int
    max_client_idle_workers: int

    @classmethod
    def load(cls, file: FileConfig) -> "Settings":
        """Build runtime settings: environment wins, then config.toml, then defaults."""
        steam_raw = os.getenv("STEAM_PATH", file.steam_path)
        workers_env = os.getenv("MAX_CLIENT_IDLE_WORKERS")
        return cls(
            steam_id=os.getenv("STEAM_ID", ""),
            steam_api_key=os.getenv("STEAM_API_KEY", ""),
            steam_username=os.getenv("STEAM_USERNAME", ""),
            steam_password=os.getenv("STEAM_PASSWORD", ""),
            games_data_path=PROJECT_ROOT / "data" / "games.json",
            unlock_manifest_path=PROJECT_ROOT / "manifests" / "unlock-manifest.json",
            idle_manifest_path=PROJECT_ROOT / "manifests" / "idle-manifest.json",
            tasks_file_path=PROJECT_ROOT / "tasks.json",
            steam_utility_path=Path(
                os.getenv("STEAM_UTILITY_PATH")
                or (
                    PROJECT_ROOT / "libs" / "win-x64" / "SteamUtility.exe"
                    if sys.platform == "win32"
                    else PROJECT_ROOT / "libs" / "linux-x64" / "SteamUtility"
                )
            ),
            agent_tokens_path=PROJECT_ROOT / "tokens.json",
            steam_path=Path(steam_raw) if steam_raw else None,
            steam_api_base_url="https://api.steampowered.com",
            login_mode=LoginMode((os.getenv("LOGIN_MODE") or file.login_mode).lower()),
            unlock_delay_tolerance_seconds=file.unlock_delay_tolerance_seconds,
            idle_tolerance_seconds=file.idle_tolerance_seconds,
            max_client_idle_workers=(
                int(workers_env) if workers_env and workers_env.isdigit() else file.max_client_idle_workers
            ),
        )


settings = Settings.load(_load_file_config())
