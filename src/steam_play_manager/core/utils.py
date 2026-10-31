import contextlib
import json
import logging
import os
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
import requests

logger = logging.getLogger(__name__)


# --- Platform Setup ---


def enable_ansi_utf8() -> None:
    """Enable ANSI escape sequences and UTF-8 output on Windows consoles."""
    if sys.platform != "win32":
        return
    os.system("")
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


# --- Formatting ---


def safe_int(value: Any, default: int | None = None) -> int | None:
    """Convert value to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return default


def seconds_to_dhms(seconds: Any) -> str:
    """Convert a duration in seconds to a human-readable days/hours/minutes/seconds string."""
    try:
        seconds = int(seconds)
    except (ValueError, TypeError, OverflowError):
        return "N/A"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days}d {hours}h {minutes}m {secs}s"


def ts_to_date(ts: int | float | None) -> str:
    """Convert a unix timestamp to a formatted datetime string."""
    if ts is None or ts == 0:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


# --- File I/O ---


def read_toml(path: str | Path | None, default: Any = None) -> Any:
    """Load data from a TOML file with specified fallback."""
    fallback = {} if default is None else default
    if not path:
        return fallback

    p = Path(path)
    if not p.exists():
        return fallback

    try:
        with p.open("rb") as f:
            return tomllib.load(f)
    except Exception as e:
        logger.error(f"Failed to read TOML from {path}: {e}")

    return fallback


def read_json(path: str | Path | None, default: Any = None) -> Any:
    """Load data from a JSON file with specified fallback."""
    fallback = {} if default is None else default
    if not path:
        return fallback

    p = Path(path)
    if not p.exists():
        return fallback

    try:
        with p.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read JSON from {path}: {e}")

    return fallback


def write_json(path: str | Path | None, data: Any, indent: int = 4) -> bool:
    """Save data to a JSON file safely with formatting."""
    if not path:
        return False
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Failed to write JSON to {path}: {e}")
        return False


# --- Network ---

_API_REQUEST_TIMEOUT = 10.0


def fetch_api_data(
    url: str, params: dict | None = None, retries: int = 3, timeout: float = _API_REQUEST_TIMEOUT
) -> dict | None:
    """
    Fetch JSON data from an external API with retries and exponential backoff.
    Args:
        url: Endpoint to request.
        params: Query parameters for the request.
        retries: Number of attempts before giving up.
        timeout: Per-request timeout in seconds.
    Returns:
        Parsed JSON response, or None when the request ultimately fails.
    """
    if params is None:
        params = {}

    for i in range(retries):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            if response.status_code == 400:
                return None
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            if i < retries - 1:
                wait_time = 2 ** (i + 1)
                logger.debug(f"Request failed ({e}). Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logger.debug(f"Failed to fetch data: {e}")
                return None
    return None


# --- Processes ---


def is_process_running(process_name: str) -> bool:
    """Check if there is any running process matching the given name."""
    return any(proc.info["name"] == process_name for proc in psutil.process_iter(["name"]))


# --- Time & Waiting ---


def wait_with_update(
    delay_seconds: int | float,
    update_seconds: int | None = None,
    label: str = "Delay",
) -> None:
    """
    Wait for a duration, logging periodic status updates.
    The wait is chunked into at most `update_seconds`-sized sleeps so shutdown
    or interrupts are noticed within one interval, and uses `time.monotonic()`
    so wall-clock changes do not distort progress.

    Args:
        delay_seconds: Total wait duration in seconds.
        update_seconds: Fixed log cadence; when None (default) the cadence
            adapts to the time remaining — granular near the end, coarse far
            out: 1s at <1min, 1min at <5min, 5min at <30min, 15min at <2h,
            30min at <8h, hourly beyond.
        label: Prefix used on each progress line.
    """
    if delay_seconds <= 0:
        return

    start = time.monotonic()
    elapsed = 0.0
    while elapsed < delay_seconds:
        remaining = delay_seconds - elapsed
        if update_seconds is not None:
            interval = int(update_seconds)
        elif remaining <= 60:
            interval = 1
        elif remaining <= 300:
            interval = 60
        elif remaining <= 1800:
            interval = 300
        elif remaining <= 7200:
            interval = 900
        elif remaining <= 28800:
            interval = 1800
        else:
            interval = 3600
        time.sleep(min(interval, remaining))
        elapsed = time.monotonic() - start
        remaining = delay_seconds - elapsed
        if remaining > 0:
            logger.info(
                f"{label} in progress: {elapsed:.0f}s elapsed. Approximately {seconds_to_dhms(remaining)} remaining."
            )
        else:
            logger.info(f"{label} complete: {seconds_to_dhms(delay_seconds)} elapsed.")
