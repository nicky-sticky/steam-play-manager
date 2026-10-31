import logging
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NotRequired, TypedDict

from steam_play_manager.core import config
from steam_play_manager.models import LoginMode
from steam_play_manager.services.steam_utility import get_steam_utility_handler, is_steam_running

logger = logging.getLogger(__name__)


class LoginStatus(TypedDict):
    """Current auth/login mode status."""

    mode: LoginMode
    process_running: bool
    steam_path: Path | None
    has_agent_credentials: bool


class AuthResult(TypedDict):
    """Result of verify_steam_authentication."""

    success: bool
    mode: LoginMode
    message: str
    steam_id: NotRequired[str]
    username: NotRequired[str]
    requires_2fa: NotRequired[bool]


def get_login_status() -> LoginStatus:
    """Returns dict describing current Steam auth/login mode status."""
    mode = LoginMode(config.settings.login_mode)
    process_running = is_steam_running()

    return {
        "mode": mode,
        "process_running": process_running,
        "steam_path": config.settings.steam_path,
        "has_agent_credentials": bool(config.settings.steam_username and config.settings.steam_password),
    }


def verify_steam_authentication(guard_code_prompt: Callable[[str], str | None] | None = None) -> AuthResult:
    """Verifies Steam authentication using SteamUtility.exe based on active LOGIN_MODE."""
    mode = LoginMode(config.settings.login_mode)

    if mode == LoginMode.CLIENT:
        if not is_steam_running():
            return {
                "success": False,
                "mode": LoginMode.CLIENT,
                "message": "Local Steam client process (steam.exe) is NOT running.",
            }

        steam_utility = config.settings.steam_utility_path
        if not Path(steam_utility).exists():
            return {
                "success": False,
                "mode": LoginMode.CLIENT,
                "message": f"SteamUtility executable not found at {steam_utility}",
            }

        cmd = [str(steam_utility), "check_ownership", "[570940]"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            output = res.stdout + res.stderr
            match = re.search(r"Steam ID:\s*(\d+)", output)
            steam_id_str = match.group(1) if match else config.settings.steam_id
            return {
                "success": True,
                "mode": LoginMode.CLIENT,
                "steam_id": steam_id_str,
                "message": f"Successfully connected to active local Steam client (SteamID: {steam_id_str}).",
            }
        except Exception as e:
            return {
                "success": False,
                "mode": LoginMode.CLIENT,
                "message": f"Failed to communicate with local Steam client IPC: {e}",
            }
    else:
        handler = get_steam_utility_handler()
        ok, message = handler.agent_login(guard_code_prompt)
        if ok:
            return {
                "success": True,
                "mode": LoginMode.AGENT,
                "username": config.settings.steam_username,
                "message": f"Successfully authenticated Agent session as '{config.settings.steam_username}'.",
            }
        lowered = message.lower()
        return {
            "success": False,
            "mode": LoginMode.AGENT,
            "requires_2fa": "guard" in lowered or "2fa" in lowered,
            "message": message,
        }
