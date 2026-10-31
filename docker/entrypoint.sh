#!/bin/sh
set -e

# A provided command is treated as CLI args (docker compose run app <cli-args>).
if [ "$#" -gt 0 ]; then
    exec python3 -m steam_play_manager.cli "$@"
fi

# Auto-resume the pending task queue on start when enabled (default: true).
if [ "${TASK_AUTO_RUN:-true}" = "true" ]; then
    echo "[entrypoint] TASK_AUTO_RUN enabled - processing pending task queue..."
    python3 -m steam_play_manager.cli tasks run || echo "[entrypoint] WARNING: tasks run exited with code $?"
fi

# Default: keep the container alive so docker exec / logs work and up -d doesn't restart-loop.
exec sleep infinity
