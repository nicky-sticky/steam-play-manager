import logging
from collections.abc import Mapping, Sequence
from typing import Any

from steam_play_manager.core import config
from steam_play_manager.core.utils import seconds_to_dhms
from steam_play_manager.models import AchievementStatus, GameUnlockStatus
from steam_play_manager.services.achievement_unlocker import AchievementUnlocker

logger = logging.getLogger(__name__)


def build_idle_status_rows(
    records: Sequence[Mapping[str, Any]],
    owned_games: Mapping[int, Mapping[str, Any]] | None,
    tolerance_seconds: int | None = None,
) -> list[dict[str, Any]]:
    """Rows: App ID | App Name | Playtime | Manifest PT | Remaining | Status."""
    if tolerance_seconds is None:
        tolerance_seconds = config.settings.idle_tolerance_seconds

    rows = []
    for r in records:
        info = owned_games.get(int(r["app_id"]), {}) if owned_games else {}
        current = info.get("playtime_forever", 0) * 60
        idle_cfg = r.get("idle_config")
        desired = idle_cfg.desired_playtime_seconds if idle_cfg else 0

        if not desired:
            status = "[dim]UNCONFIGURED[/dim]"
            remaining = "[dim]—[/dim]"
        elif current >= desired or (current + tolerance_seconds >= desired):
            status = "[green]COMPLETE[/green]"
            remaining = "0s"
        elif current == 0:
            status = "[yellow]UNSTARTED[/yellow]"
            remaining = seconds_to_dhms(desired - current)
        else:
            status = "[yellow]IN PROGRESS[/yellow]"
            remaining = seconds_to_dhms(desired - current)

        rows.append(
            {
                "app_id": r["app_id"],
                "app_name": r["app_name"],
                "playtime": seconds_to_dhms(current),
                "manifest_pt": seconds_to_dhms(desired) if desired else "[dim]—[/dim]",
                "remaining": remaining,
                "status": status,
            }
        )
    return rows


def build_idle_status_log(
    record: dict[str, Any], owned_games: Mapping[int, Mapping[str, Any]] | None, tolerance_seconds: int | None = None
) -> str:
    """Render the full idle status report for a single game record."""
    if tolerance_seconds is None:
        tolerance_seconds = config.settings.idle_tolerance_seconds

    app_id = record.get("app_id") or 0
    app_name = record.get("app_name") or f"App {app_id}"
    info = owned_games.get(int(app_id), {}) if owned_games else {}
    current_sec = info.get("playtime_forever", 0) * 60
    idle_cfg = record.get("idle_config")
    desired_sec = idle_cfg.desired_playtime_seconds if idle_cfg else 0

    lines = []
    lines.append(f"[bold cyan]=== MANIFEST IDLER STATUS LOG: {app_name} (App ID: {app_id}) ===[/bold cyan]\n")
    lines.append("[bold yellow]Summary:[/bold yellow]")
    lines.append(f" • Current Playtime    : {seconds_to_dhms(current_sec)} ({current_sec:,}s)")
    if desired_sec:
        remaining_sec = max(0, desired_sec - current_sec)
        is_met = current_sec + tolerance_seconds >= desired_sec
        status_str = (
            "[green]COMPLETE[/green]"
            if is_met
            else ("[yellow]UNSTARTED[/yellow]" if current_sec == 0 else "[yellow]IN PROGRESS[/yellow]")
        )
        lines.append(f" • Manifest Target PT  : {seconds_to_dhms(desired_sec)} ({desired_sec:,}s)")
        lines.append(f" • Remaining Target    : {seconds_to_dhms(remaining_sec)} ({remaining_sec:,}s)")
        lines.append(f" • Tolerance Applied   : {tolerance_seconds}s (config: IDLE_TOLERANCE_SECONDS)")
        lines.append(f" • Idler Status        : {status_str}\n")
        lines.append("[bold yellow]Status Details:[/bold yellow]")
        lines.append(f" • Target Met?         : {'YES' if is_met else 'NO'}")
        lines.append(" • Completion Criteria : Current playtime + tolerance >= Target playtime")
    else:
        lines.append(" • Manifest Target PT  : [dim]Not configured[/dim]")
        lines.append(" • Idler Status        : [dim]UNCONFIGURED[/dim]")

    return "\n".join(lines)


def build_achievement_status_rows(
    records: Sequence[Mapping[str, Any]], tolerance_seconds: int | None = None
) -> list[dict[str, Any]]:
    """Rows: App ID | App Name | Achievements | Manifest Ach | Status."""
    if tolerance_seconds is None:
        tolerance_seconds = config.settings.unlock_delay_tolerance_seconds

    unlocker = AchievementUnlocker()
    rows = []
    for r in records:
        unlock_cfg = r.get("unlock_config")
        if unlock_cfg is None or not unlock_cfg.achievements:
            rows.append(
                {
                    "app_id": r["app_id"],
                    "app_name": r["app_name"],
                    "achievements": "[dim]—[/dim]",
                    "manifest_ach": "[dim]—[/dim]",
                    "status": "[dim]UNCONFIGURED[/dim]",
                }
            )
            continue

        total = len(unlock_cfg.achievements)
        summary = unlocker.check_game_status(unlock_cfg, tolerance_seconds=tolerance_seconds)
        unlocked = summary.get("unlocked_count", 0)
        s_val = summary.get("status")

        if s_val == GameUnlockStatus.DONE:
            status = "[green]COMPLETE[/green]"
        elif s_val == GameUnlockStatus.NEEDS_RERUN:
            status = "[red]DRIFT[/red]"
        elif unlocked == 0:
            status = "[yellow]UNSTARTED[/yellow]"
        else:
            status = "[yellow]IN PROGRESS[/yellow]"

        rows.append(
            {
                "app_id": r["app_id"],
                "app_name": r["app_name"],
                "achievements": f"{unlocked}/{total}",
                "manifest_ach": str(total),
                "status": status,
            }
        )
    return rows


def build_achievement_status_log(record: dict[str, Any], tolerance_seconds: int | None = None) -> str:
    """Build a formatted achievement schedule log for a game record."""
    if tolerance_seconds is None:
        tolerance_seconds = config.settings.unlock_delay_tolerance_seconds

    app_id = record.get("app_id")
    app_name = record.get("app_name") or f"App {app_id}"
    unlock_cfg = record.get("unlock_config")
    manifest_achs = unlock_cfg.achievements if unlock_cfg else []

    lines = []
    lines.append(f"[bold cyan]=== MANIFEST UNLOCK SCHEDULE LOG: {app_name} (App ID: {app_id}) ===[/bold cyan]\n")

    if not manifest_achs:
        lines.append("[bold yellow]Summary:[/bold yellow]")
        lines.append(" • Manifest Target      : [dim]Not configured[/dim]")
        lines.append(" • Schedule Status      : [dim]UNCONFIGURED[/dim]")
        return "\n".join(lines)

    unlocker = AchievementUnlocker()
    assert unlock_cfg is not None
    result = unlocker.check_game_schedule(unlock_cfg, tolerance_seconds=tolerance_seconds)

    if result.get("error"):
        lines.append(f"[red]Error: {result['error']}[/red]")
        return "\n".join(lines)

    all_ach = result.get("all_achievements") or []
    unlocked_count = sum(1 for a in all_ach if a.get("achieved"))
    total_manifest = len(manifest_achs)
    drift_count = sum(1 for a in all_ach if a.get("status") == AchievementStatus.DELAY_MISMATCH)

    if unlocked_count == total_manifest and drift_count == 0:
        overall_status = "[green]COMPLETE[/green]"
    elif drift_count > 0:
        overall_status = "[red]DRIFT[/red]"
    elif unlocked_count == 0:
        overall_status = "[yellow]UNSTARTED[/yellow]"
    else:
        overall_status = "[yellow]IN PROGRESS[/yellow]"

    lines.append("[bold yellow]Summary:[/bold yellow]")
    lines.append(f" • Manifest Configured   : {total_manifest} achievements")
    lines.append(f" • Unlocked on Steam     : {unlocked_count} / {total_manifest}")
    lines.append(f" • Timing Drift Count    : {drift_count}")
    lines.append(f" • Tolerance Applied     : ±{tolerance_seconds}s (config: UNLOCK_DELAY_TOLERANCE_SECONDS)")
    lines.append(f" • Schedule Status       : {overall_status}\n")

    lines.append("[bold yellow]Per-Achievement Breakdown:[/bold yellow]")
    for idx, ach in enumerate(all_ach, start=1):
        name = ach.get("name") or ach.get("apiname")
        apiname = ach.get("apiname")
        status_raw = ach.get("status")

        if status_raw == AchievementStatus.MATCH:
            st_markup = "[green]MATCH[/green]"
        elif status_raw == AchievementStatus.DELAY_MISMATCH:
            st_markup = "[red]DRIFT[/red]"
        else:
            st_markup = "[yellow]LOCKED ON STEAM[/yellow]"

        lines.append(f" {idx:2d}. {name} ([dim]{apiname}[/dim])")
        lines.append(f"     - Status       : {st_markup}")
        m_step = ach.get("manifest_step_delay", 0)
        a_step = ach.get("actual_step_delay")
        step_diff = ach.get("step_delay_diff") or 0

        if a_step is not None:
            diff_str = f"+{step_diff}s" if step_diff >= 0 else f"{step_diff}s"
            tolerance_note = (
                f"within ±{tolerance_seconds}s tolerance"
                if abs(step_diff) <= tolerance_seconds
                else f"OUTSIDE ±{tolerance_seconds}s tolerance"
            )
            lines.append(f"     - Target Step  : {seconds_to_dhms(m_step)} ({m_step}s)")
            lines.append(
                f"     - Actual Step  : {seconds_to_dhms(a_step)} ({a_step}s) | Diff: {diff_str} ({tolerance_note})"
            )
            lines.append(f"     - Unlocked At  : {ach.get('unlocktime_date', '—')}")
        else:
            lines.append(f"     - Target Step  : {seconds_to_dhms(m_step)} ({m_step}s)")

    return "\n".join(lines)
