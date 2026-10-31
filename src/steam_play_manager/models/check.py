from enum import StrEnum
from typing import TypedDict


class AchievementStatus(StrEnum):
    """Per-achievement unlock-delay comparison outcome."""

    MATCH = "MATCH"
    DELAY_MISMATCH = "DELAY_MISMATCH"
    LOCKED_ON_STEAM = "LOCKED_ON_STEAM"


class GameUnlockStatus(StrEnum):
    """Single-game unlock-schedule summary status."""

    DONE = "done"
    IN_PROGRESS = "in_progress"
    NEEDS_RERUN = "needs_rerun"
    ERROR = "error"


class AchievementCheckEntry(TypedDict):
    """Per-achievement delay comparison from unlock-time verification."""

    apiname: str
    name: str
    status: AchievementStatus
    manifest_step_delay: int
    actual_step_delay: int | None
    step_delay_diff: int | None
    manifest_cumulative_delay: int
    actual_cumulative_delay: int | None
    cumulative_delay_diff: int | None
    unlocktime_ts: int | None
    unlocktime_date: str
    achieved: bool


class GameCheckResult(TypedDict):
    """Per-game unlock delay comparison result."""

    app_id: int
    app_name: str
    has_differences: bool
    differing_count: int
    total_manifest: int
    total_unlocked_actual: int
    differing_achievements: list[AchievementCheckEntry]
    all_achievements: list[AchievementCheckEntry]
    error: str | None


class UnlockCheckSummary(TypedDict):
    """Aggregate counts from unlock-time verification."""

    total_games: int
    differing_games_count: int
    matching_games_count: int
    skipped_games_count: int


class UnlockCheckResult(TypedDict):
    """Result of unlock-time verification across a list of game configs."""

    games_with_differences: list[GameCheckResult]
    games_matching: list[GameCheckResult]
    games_skipped: list[GameCheckResult]
    summary: UnlockCheckSummary


class GameUnlockSummary(TypedDict):
    """Single-game unlock-schedule summary status."""

    app_id: int | None
    app_name: str | None
    configured_count: int
    unlocked_count: int
    status: GameUnlockStatus
    error: str | None


class IdleGameRecord(TypedDict):
    """Per-game playtime comparison from idle-time verification."""

    app_id: int
    app_name: str
    current_playtime_seconds: int
    desired_playtime_seconds: int
    needed_seconds: int
    is_completed: bool


class IdleCheckResult(TypedDict):
    """Result of idle-time verification across a list of game configs."""

    total_games: int
    games_needing_idle_count: int
    games_completed_count: int
    games_needing_idle: list[IdleGameRecord]
    games_completed: list[IdleGameRecord]
