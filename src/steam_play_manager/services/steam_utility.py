import atexit
import base64
import contextlib
import itertools
import json
import logging
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO, TypedDict

from steam_play_manager.core import config
from steam_play_manager.core.executable import build_launch_command
from steam_play_manager.core.utils import is_process_running, read_json, write_json
from steam_play_manager.models import LoginMode, TokensFile

logger = logging.getLogger(__name__)

_AGENT_REQUEST_TIMEOUT = 30.0
_AGENT_READ_TIMEOUT = 10.0
_AGENT_LOGIN_TIMEOUT = 60.0
_AGENT_CONFIRMATION_TIMEOUT = 90.0
_CLIENT_COMMAND_TIMEOUT = 60.0
_LOGIN_EVENTS = ("refresh_token", "login_failed", "guard_code_incorrect")
_AGENT_MAX_IDLE_APPS = 32
_SESSION_ERRORS = ("not_logged_on", "logged_off", "logged_out", "not_authenticated")


class AchievementInfo(TypedDict):
    """A single achievement as reported by the local SteamUtility handler."""

    name: str
    friendly_name: str
    achieved: bool


class SteamUtilityResult(TypedDict):
    """Envelope returned by SteamUtilityHandler for every command result."""

    success: bool
    stderr: str
    output: str
    data: Any


def _response(success: bool, output: str, stderr: str = "", data: Any = None) -> SteamUtilityResult:
    """Build a SteamUtilityResult dict."""
    return {
        "success": success,
        "stderr": stderr,
        "output": output,
        "data": data,
    }


def _is_session_error(error: str) -> bool:
    """Return whether a daemon error means the agent session has dropped."""
    lowered = error.strip().lower()
    return any(token in lowered for token in _SESSION_ERRORS)


class SteamUtilityHandler:
    """Unified execution handler for SteamUtility.exe (Client & Agent modes)."""

    def __init__(self, login_mode: LoginMode | str | None = None, steam_utility_path: str | Path | None = None) -> None:
        self.login_mode = LoginMode((login_mode or config.settings.login_mode).lower())
        self.steam_utility_path = Path(steam_utility_path or config.settings.steam_utility_path)
        self.steam_utility_dll = self.steam_utility_path.with_suffix(".dll")
        self._daemon_proc = None
        self._daemon_reader = None
        self._stderr_reader = None
        self._line_queue = queue.Queue()
        self._event_queue = queue.Queue()
        self._next_id = itertools.count(1)
        self._agent_logged_in = False
        self._agent_error = ""
        self._daemon_stale = False
        # Serializes daemon IPC: concurrent workers would otherwise interleave stdin writes
        # and steal each other's responses from the shared queue, stalling requests.
        self._ipc_lock = threading.Lock()

    def execute(self, command: str, *args: object) -> SteamUtilityResult:
        """
        Routes and executes a SteamUtility command based on login_mode.
        Args:
            command: Command name to execute.
            *args: Positional command arguments.
        Returns:
            Command execution result.
        """
        if self.login_mode == LoginMode.AGENT:
            return self._execute_agent(command, *args)
        return self._execute_client(command, *args)

    def fetch_achievements(self, app_id: str | int) -> list[AchievementInfo]:
        """Fetch the achievement list for a game via the local utility."""
        res = self.execute("get_achievement_data", str(app_id))

        data = res.get("data") or {}
        result = data.get("result") if isinstance(data, dict) else None
        achievements = result.get("achievements") if isinstance(result, dict) else None
        if achievements is not None:
            return [
                {
                    "name": ach["id"],
                    "friendly_name": ach["name"],
                    "achieved": ach.get("achieved", False),
                }
                for ach in achievements
            ]

        logger.error(f"Failed to fetch achievements for app {app_id}: {res.get('stderr') or res.get('output')}")
        return []

    def agent_login(self, guard_code_prompt: Callable[[str], str | None] | None = None) -> tuple[bool, str]:
        """Ensure the agent daemon is running and authenticated."""
        proc = self._ensure_agent_daemon(guard_code_prompt)
        if proc is None:
            return False, self._agent_error or "Agent daemon unavailable."
        return True, f"Agent session authenticated as '{config.settings.steam_username}'."

    def is_authenticated(self) -> bool | None:
        """Check if a live agent session is established."""
        if self.login_mode != LoginMode.AGENT:
            return None
        return self._agent_logged_in and self._daemon_proc is not None and self._daemon_proc.poll() is None

    def has_saved_token(self) -> bool:
        """Check whether a saved agent refresh token exists for configured username."""
        return bool(self._load_refresh_token(config.settings.steam_username))

    def close(self) -> None:
        """Cleanly terminate the active agent daemon process."""
        if self._daemon_proc is not None and self._daemon_proc.poll() is None:
            self._send_and_wait("logout", timeout=5)
        self._teardown_daemon()

    # --- Client Mode ---

    def _execute_client(self, command: str, *args: object) -> SteamUtilityResult:
        """Execute a one-shot CLI command against local running Steam client."""
        if not self.steam_utility_path.exists():
            msg = f"SteamUtility.exe not found at {self.steam_utility_path}"
            logger.error(msg)
            return _response(False, msg, msg)

        if command == "idle":
            msg = "Client-mode 'idle' must be spawned via the idler service."
            logger.error(msg)
            return _response(False, msg, msg)

        cmd = build_launch_command(self.steam_utility_path, self.steam_utility_dll, command, *[str(a) for a in args])
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=_CLIENT_COMMAND_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            logger.error("SteamUtility client command '%s' timed out after %ss", command, _CLIENT_COMMAND_TIMEOUT)
            return _response(False, f"SteamUtility command timed out after {int(_CLIENT_COMMAND_TIMEOUT)}s.", str(e))
        except Exception as e:
            logger.error("Unexpected error running SteamUtility client command '%s': %s", command, e)
            return _response(False, str(e), str(e))

        output = (res.stdout or "") + (res.stderr or "")
        data = self._parse_client_envelope(res.stdout)
        if data is not None:
            ok = bool(data.get("ok"))
            if not ok:
                logger.error("SteamUtility client command '%s' failed: %s", command, data.get("error"))
            return _response(ok, res.stdout.strip(), data.get("error") or "", data)

        if res.returncode != 0:
            logger.error("SteamUtility client command '%s' failed (exit %s): %s", command, res.returncode, output)
            return _response(False, output, output)
        return _response(True, output)

    @staticmethod
    def _parse_client_envelope(stdout: str) -> dict[str, Any] | None:
        """Parse JSON envelope line from modern CLI output."""
        try:
            data = json.loads(stdout.strip())
        except (json.JSONDecodeError, AttributeError):
            return None
        return data if isinstance(data, dict) and "ok" in data else None

    # --- Agent Mode: Daemon Lifecycle ---

    def _ensure_agent_daemon(
        self, guard_code_prompt: Callable[[str], str | None] | None = None
    ) -> subprocess.Popen | None:
        """Ensure agent daemon subprocess is running and logged in."""
        if self._daemon_stale:
            logger.warning("Recycling stale agent daemon (a previous request timed out).")
            self._teardown_daemon()
            self._daemon_stale = False

        if self._agent_logged_in and self._daemon_proc is not None and self._daemon_proc.poll() is None:
            return self._daemon_proc

        if not config.settings.steam_username:
            self._agent_error = "Agent credentials missing - set STEAM_USERNAME in .env"
            logger.error(self._agent_error)
            return None

        if self._daemon_proc is None or self._daemon_proc.poll() is not None:
            self._spawn_daemon()
            if self._daemon_proc is None:
                return None

        return self._login(guard_code_prompt)

    def _spawn_daemon(self) -> subprocess.Popen | None:
        """Spawn SteamUtility agent daemon process and start background reader threads."""
        if not self.steam_utility_path.exists():
            self._agent_error = f"SteamUtility.exe not found at {self.steam_utility_path}"
            logger.error(self._agent_error)
            self._daemon_proc = None
            return

        self._line_queue = queue.Queue()
        self._event_queue = queue.Queue()
        try:
            proc = subprocess.Popen(
                build_launch_command(self.steam_utility_path, self.steam_utility_dll, "agent"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as e:
            self._agent_error = f"Failed to spawn SteamUtility agent daemon: {e}"
            logger.error(self._agent_error)
            self._daemon_proc = None
            return

        self._daemon_proc = proc
        self._agent_logged_in = False
        self._daemon_reader = threading.Thread(target=self._read_stdout_lines, args=(proc,), daemon=True)
        self._daemon_reader.start()
        self._stderr_reader = threading.Thread(target=self._drain_stderr, args=(proc.stderr,), daemon=True)
        self._stderr_reader.start()
        logger.info("Spawned SteamUtility agent daemon (pid %s)", proc.pid)

    def _teardown_daemon(self) -> None:
        """Terminate the daemon process and reset all daemon state."""
        proc = self._daemon_proc
        if proc is not None:
            with contextlib.suppress(Exception):
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
        self._daemon_proc = None
        self._daemon_reader = None
        self._stderr_reader = None
        self._agent_logged_in = False

    def _read_stdout_lines(self, proc: subprocess.Popen) -> None:
        """Background stdout reader thread parsing daemon JSON messages."""
        stdout = proc.stdout
        if stdout is None:
            return
        for raw_line in stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ignoring non-JSON agent daemon line: %s", line[:200])
                continue
            if msg.get("event"):
                self._handle_agent_event(msg)
                if msg.get("event") in _LOGIN_EVENTS:
                    self._event_queue.put(msg)
                continue
            self._line_queue.put(msg)

    def _handle_agent_event(self, msg: dict[str, Any]) -> None:
        """Process async events emitted by agent daemon."""
        event = msg.get("event")
        if event == "refresh_token":
            username = msg.get("username")
            token_b64 = msg.get("tokenB64")
            if username and token_b64:
                self._save_refresh_token(username, token_b64)
            logger.info("Agent daemon refresh_token captured for '%s'", username)
        elif event == "status_changed":
            logger.info(
                "Agent daemon status_changed: result=%s loggedOn=%s",
                msg.get("result"),
                msg.get("loggedOn"),
            )
        elif event == "idle_state":
            logger.info("Agent daemon idle_state: %s", msg.get("appIds"))
        elif event == "login_failed":
            logger.error("Agent daemon login_failed: %s", msg.get("error"))
        else:
            logger.info("Agent daemon event: %s", event)

    def _drain_stderr(self, stream: TextIO) -> None:
        """Background stderr reader thread draining process output pipe."""
        for line in stream:
            line = line.strip()
            if line:
                logger.debug("SteamUtility[agent stderr]: %s", line)

    @staticmethod
    def _save_refresh_token(username: str, token_b64: str) -> None:
        """Save refresh token to tokens.json."""
        try:
            tokens = TokensFile.from_dict(read_json(config.settings.agent_tokens_path))
            tokens.set(username, token_b64)
            write_json(config.settings.agent_tokens_path, tokens.tokens)
        except Exception as e:
            logger.error("Failed to save agent refresh token: %s", e)

    @staticmethod
    def _load_refresh_token(username: str) -> str | None:
        """Load saved refresh token for username."""
        return TokensFile.from_dict(read_json(config.settings.agent_tokens_path)).get(username)

    def _login(self, guard_code_prompt: Callable[[str], str | None] | None = None) -> subprocess.Popen | None:
        """Authenticate daemon over IPC using token or credentials."""
        username = config.settings.steam_username

        saved_token = self._load_refresh_token(username)
        if saved_token:
            resp = self._send_and_wait("login_with_token", user=username, refreshTokenB64=saved_token)
            if resp is None:
                return None
            if resp.get("ok"):
                self._agent_logged_in = True
                self._agent_error = ""
                logger.info("Agent session resumed via refresh token for '%s'", username)
                return self._daemon_proc
            if resp.get("error") != "refresh_token_invalid":
                self._agent_error = f"Agent login failed: {resp.get('error')}"
                logger.error(self._agent_error)
                return None
            logger.info("Saved refresh token expired - falling back to credentials login.")

        password = config.settings.steam_password
        if not password:
            self._agent_error = "Agent credentials missing - set STEAM_PASSWORD in .env"
            logger.error(self._agent_error)
            return None
        pass_b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")

        resp = self._send_and_wait("login", timeout=_AGENT_LOGIN_TIMEOUT, user=username, passB64=pass_b64)
        if resp is None:
            self._agent_error = self._agent_error or "Agent login failed (no response from daemon)."
            return None
        if not resp.get("ok"):
            self._agent_error = f"Agent login failed: {resp.get('error')}"
            logger.error(self._agent_error)
            return None

        result = resp.get("result")
        status = result.get("status") if isinstance(result, dict) else None

        if status == "need_guard_code":
            assert isinstance(result, dict)
            return self._login_with_guard_code(result, guard_code_prompt)
        if status == "need_guard_confirmation":
            return self._login_with_confirmation()

        self._agent_logged_in = True
        self._agent_error = ""
        logger.info("Agent session established for '%s'", username)
        return self._daemon_proc

    def _login_with_guard_code(
        self, result: dict[str, Any], guard_code_prompt: Callable[[str], str | None] | None
    ) -> subprocess.Popen | None:
        """Handle Steam Guard code authentication prompt flow."""
        guard_type = result.get("guardType") or "code"
        detail = result.get("detail") or ""
        if guard_code_prompt is None:
            self._agent_error = (
                f"Agent login requires a Steam Guard {guard_type} code"
                + (f" ({detail})" if detail else "")
                + " - no prompt handler is available."
            )
            logger.error(self._agent_error)
            return None

        while True:
            message = f"Steam Guard {guard_type} code" + (f" sent to {detail}" if detail else "") + ":"
            code = guard_code_prompt(message)
            if not code:
                self._agent_error = "Agent login cancelled (Steam Guard prompt dismissed)."
                logger.warning(self._agent_error)
                return None

            resp = self._send_and_wait("submit_guard_code", code=code)
            if resp is None:
                self._agent_error = self._agent_error or "Failed to submit Steam Guard code."
                return None
            if not resp.get("ok"):
                self._agent_error = f"Failed to submit Steam Guard code: {resp.get('error')}"
                logger.error(self._agent_error)
                return None

            event = self._wait_for_event(
                ("refresh_token", "guard_code_incorrect", "login_failed"), timeout=_AGENT_LOGIN_TIMEOUT
            )
            if event is None:
                return None
            name = event.get("event")
            if name == "refresh_token":
                self._agent_logged_in = True
                self._agent_error = ""
                logger.info("Agent session established after Steam Guard verification.")
                return self._daemon_proc
            if name == "guard_code_incorrect":
                logger.warning("Steam Guard code was incorrect - please try again.")
                continue
            self._agent_error = f"Agent login failed after Steam Guard: {event.get('error') or 'unknown error'}"
            logger.error(self._agent_error)
            return None

    def _login_with_confirmation(self) -> subprocess.Popen | None:
        """Handle Steam Guard mobile device confirmation approval."""
        logger.info("Steam Guard device confirmation required - approve the login in the Steam app.")
        event = self._wait_for_event(("refresh_token", "login_failed"), timeout=_AGENT_CONFIRMATION_TIMEOUT)
        if event is None:
            return None
        if event.get("event") == "refresh_token":
            self._agent_logged_in = True
            self._agent_error = ""
            logger.info("Agent session established after Steam Guard confirmation.")
            return self._daemon_proc
        self._agent_error = f"Agent login failed: {event.get('error') or 'unknown error'}"
        logger.error(self._agent_error)
        return None

    def _wait_for_event(self, names: Sequence[str], timeout: float) -> dict[str, Any] | None:
        """Wait for daemon async event matching one of names."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if self._daemon_proc is not None and self._daemon_proc.poll() is not None:
                    self._agent_error = "Agent daemon exited before login completed."
                    logger.error(self._agent_error)
                else:
                    self._agent_error = "Timed out waiting for agent daemon event."
                    logger.error(self._agent_error)
                return None
            try:
                msg = self._event_queue.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue
            if msg.get("event") in names:
                return msg

    def _send_and_wait(self, cmd: str, timeout: float | None = None, **payload: Any) -> dict[str, Any] | None:
        """Send JSON request line to daemon and wait for response envelope."""
        proc = self._daemon_proc
        if proc is None or proc.poll() is not None:
            self._agent_error = "Agent daemon is not running."
            return None

        req_id = str(next(self._next_id))
        request = {"id": req_id, "cmd": cmd}
        request.update(payload)

        # The daemon processes one request at a time, so holding the lock across write+read
        # serializes clients and prevents interleaved writes or lost responses.
        with self._ipc_lock:
            stdin = proc.stdin
            if stdin is None:
                self._agent_error = "Agent daemon stdin is not available."
                return None
            try:
                stdin.write(json.dumps(request) + "\n")
                stdin.flush()
            except Exception as e:
                self._agent_error = f"Failed to write to agent daemon: {e}"
                logger.error(self._agent_error)
                return None

            return self._read_response(req_id, proc, timeout or _AGENT_REQUEST_TIMEOUT)

    def _read_response(self, req_id: str, proc: subprocess.Popen, timeout: float) -> dict[str, Any] | None:
        """Read daemon response queue until response matching req_id arrives."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if proc.poll() is not None:
                    self._agent_error = "Agent daemon exited before responding."
                    logger.error(self._agent_error)
                else:
                    # The daemon is alive but not answering - mark it stale so the next
                    # command recycles the process instead of waiting on it forever.
                    self._daemon_stale = True
                    self._agent_error = f"Timed out waiting for agent daemon response ({int(timeout)}s)."
                    logger.error(self._agent_error)
                return None
            try:
                msg = self._line_queue.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue
            if msg.get("id") != req_id:
                logger.warning("Dropping agent daemon response for unexpected request id %s", msg.get("id"))
                continue
            return msg

    def _send_and_wait_retry(self, cmd: str, **payload: Any) -> dict[str, Any] | None:
        """Send a daemon request, recovering once from a dropped session or stalled daemon."""
        resp = self._send_and_wait(cmd, **payload)
        if resp is not None and not resp.get("ok") and _is_session_error(resp.get("error") or ""):
            logger.warning("Agent session dropped (%s); re-authenticating and retrying '%s'.", resp.get("error"), cmd)
            if self._reconnect_agent():
                resp = self._send_and_wait(cmd, **payload)
        elif resp is None and self._daemon_stale:
            logger.warning("Agent daemon request for '%s' stalled; recycling daemon and retrying.", cmd)
            if self._recycle_daemon():
                resp = self._send_and_wait(cmd, **payload)
        return resp

    def _reconnect_agent(self) -> bool:
        """Re-establish the agent session on the live daemon (saved token first)."""
        self._agent_logged_in = False
        return self._login() is not None

    def _recycle_daemon(self) -> bool:
        """Replace the stalled daemon process with a fresh one and re-login."""
        self._teardown_daemon()
        self._daemon_stale = False
        return self._ensure_agent_daemon() is not None

    # --- Agent Mode: Command Mapping ---

    def _execute_agent(self, command: str, *args: object) -> SteamUtilityResult:
        """Execute command through agent daemon IPC."""
        proc = self._ensure_agent_daemon()
        if proc is None or proc.poll() is not None:
            return _response(False, self._agent_error or "Agent daemon unavailable.", self._agent_error or "")

        if command == "unlock_all_achievements":
            return self._agent_set_all_achievements(args, unlock=True)
        if command == "lock_all_achievements":
            return self._agent_set_all_achievements(args, unlock=False)
        if command == "toggle_achievement":
            return self._agent_toggle_achievement(args)

        resp = self._agent_simple_request(command, args)
        if resp is None:
            return _response(False, self._agent_error or "Agent daemon request failed.", self._agent_error or "")
        if not resp.get("ok"):
            error = resp.get("error") or "unknown error"
            logger.error("Agent command '%s' failed: %s", command, error)
            return _response(False, error, error)
        return _response(True, json.dumps(resp), data=resp)

    def _agent_simple_request(self, command: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
        """Map simple command to daemon request envelope."""
        if command == "idle":
            app_ids = list(dict.fromkeys(int(a) for a in args))[:_AGENT_MAX_IDLE_APPS]
            return self._send_and_wait_retry("idle_set", appIds=app_ids)
        if command == "get_achievement_data":
            return self._send_and_wait_retry(
                "achievements_get", appId=int(args[0]), language="english", timeout=_AGENT_READ_TIMEOUT
            )
        if command == "unlock_achievement":
            return self._send_and_wait_retry(
                "achievement_set", appId=int(args[0]), achievementId=str(args[1]), unlock=True
            )
        if command == "lock_achievement":
            return self._send_and_wait_retry(
                "achievement_set", appId=int(args[0]), achievementId=str(args[1]), unlock=False
            )
        if command == "update_stats":
            stats = self._parse_stats_arg(args[1] if len(args) > 1 else "")
            return self._send_and_wait_retry("stats_update", appId=int(args[0]), stats=stats)
        if command == "reset_all_stats":
            return self._send_and_wait_retry("stats_reset_all", appId=int(args[0]))

        self._agent_error = f"Unknown agent command: {command}"
        logger.error(self._agent_error)
        return None

    def _agent_set_all_achievements(self, args: tuple[Any, ...], unlock: bool) -> SteamUtilityResult:
        """Set all achievements for a game to unlocked or locked."""
        app_id = int(args[0])
        data = self._agent_get_achievements(app_id)
        if data is None:
            return _response(False, self._agent_error or "Failed to fetch achievements.", self._agent_error or "")

        changes = [
            {"id": ach["id"], "unlock": unlock}
            for ach in data.get("achievements", [])
            if bool(ach.get("achieved", False)) != unlock
        ]
        if not changes:
            state = "unlocked" if unlock else "locked"
            logger.info("App %s achievements are already %s; nothing to do.", app_id, state)
            return _response(True, "No changes required.", data={"result": {"succeeded": [], "failed": []}})

        resp = self._send_and_wait_retry("achievement_set_bulk", appId=app_id, achievementChanges=changes)
        if resp is None:
            return _response(False, self._agent_error or "Agent daemon request failed.", self._agent_error or "")
        if not resp.get("ok"):
            error = resp.get("error") or "unknown error"
            logger.error("achievement_set_bulk failed for app %s: %s", app_id, error)
            return _response(False, error, error)
        return _response(True, json.dumps(resp), data=resp)

    def _agent_toggle_achievement(self, args: tuple[Any, ...]) -> SteamUtilityResult:
        """Toggle a single achievement between unlocked and locked."""
        app_id = int(args[0])
        achievement_id = str(args[1])
        data = self._agent_get_achievements(app_id)
        if data is None:
            return _response(False, self._agent_error or "Failed to fetch achievements.", self._agent_error or "")

        target = next((a for a in data.get("achievements", []) if a.get("id") == achievement_id), None)
        if target is None:
            msg = f"Achievement not found: {achievement_id}"
            return _response(False, msg, msg)

        resp = self._send_and_wait_retry(
            "achievement_set",
            appId=app_id,
            achievementId=achievement_id,
            unlock=not bool(target.get("achieved", False)),
        )
        if resp is None:
            return _response(False, self._agent_error or "Agent daemon request failed.", self._agent_error or "")
        if not resp.get("ok"):
            error = resp.get("error") or "unknown error"
            logger.error("achievement_set failed for app %s achievement %s: %s", app_id, achievement_id, error)
            return _response(False, error, error)
        return _response(True, json.dumps(resp), data=resp)

    def _agent_get_achievements(self, app_id: int) -> dict[str, Any] | None:
        """Fetch achievement data for game from daemon."""
        resp = self._send_and_wait_retry(
            "achievements_get", appId=app_id, language="english", timeout=_AGENT_READ_TIMEOUT
        )
        if resp is None:
            return None
        if not resp.get("ok"):
            self._agent_error = f"achievements_get failed: {resp.get('error')}"
            logger.error(self._agent_error)
            return None
        result = resp.get("result")
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _parse_stats_arg(raw: Any) -> list[dict[str, Any]]:
        """Normalize stats argument to [{name, value}] shape."""
        if isinstance(raw, (dict, list)):
            data = raw
        else:
            text = str(raw).strip()
            if not text:
                data = {}
            else:
                path = Path(text)
                if path.exists():
                    try:
                        text = path.read_text(encoding="utf-8")
                    except OSError as e:
                        logger.error("Failed to read stats file %s: %s", path, e)
                        return []
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    logger.error("Invalid stats JSON: %s", text[:200])
                    return []

        if isinstance(data, dict):
            return [{"name": str(k), "value": float(v)} for k, v in data.items()]
        if isinstance(data, list):
            return [{"name": str(s.get("name") or s.get("id")), "value": float(s.get("value", 0))} for s in data]
        return []


_handler_instance = None


def get_steam_utility_handler() -> SteamUtilityHandler:
    """Get singleton instance of SteamUtilityHandler."""
    global _handler_instance
    if _handler_instance is None:
        _handler_instance = SteamUtilityHandler()
    return _handler_instance


def _close_handler_at_exit() -> None:
    """Ensure active agent daemon process is closed at Python process exit."""
    if _handler_instance is not None:
        _handler_instance.close()


def steam_process_names() -> list[str]:
    """Return the process names used to detect a running Steam client on this platform."""
    return ["steam.exe"] if sys.platform == "win32" else ["steam", "steamwebhelper", "steam.exe"]


def is_steam_running() -> bool:
    """Returns True if the Steam client process is running."""
    return any(is_process_running(name) for name in steam_process_names())


def is_steam_ready() -> bool:
    """Returns True if the current login mode's Steam runtime is available."""
    if config.settings.login_mode == LoginMode.CLIENT:
        return is_steam_running()
    return bool(get_steam_utility_handler().is_authenticated())


atexit.register(_close_handler_at_exit)
