# Steam Play Manager

Automates scheduled achievement unlocking, playtime idling, and schedule verification for Steam library titles using manifest files and a persistent task queue.

> [!WARNING]
> **Disclaimer & Legal Notice**
> This tool is designed to manage play duration and achievement unlock statuses for games that the user has registered through Steam. Use this tool at your own risk — including the potential for your Steam account to be restricted or banned.

## Features

- **Library Sync** — Pulls owned games and playtime statistics from the Steam Web API into a local registry (`data/games.json`).
- **Scheduled Achievement Unlocks** — Unlocks achievements following per-achievement delay schedules (`manifests/unlock-manifest.json`), with instant single-achievement and batch modes.
- **Playtime Idling** — Simulates gameplay to reach target playtime thresholds defined in `manifests/idle-manifest.json`.
- **Schedule Verification** — Compares manifest delay schedules against actual profile unlock timestamps and playtime hours.
- **Persistent Task Queue** — Enqueues and executes background tasks sequentially via `tasks.json` with automatic crash recovery.

---

## Requirements

**Supported platforms: Windows & Linux** (by operating mode):
- **Windows**: Supports both **Direct Mode** (local Steam client IPC) and **Docker Stack** (headless agent).
- **Linux**: Supports **Docker Stack** (headless agent; local Steam client IPC is Windows-only).

- [Python](https://www.python.org/) 3.11–3.13 (in PATH; host runtime for Direct mode and `run.bat` / `run.sh` launcher dispatcher)

### Mode Requirements

- **Direct Mode (Windows)**:
  - Running Steam desktop client (`STEAM_PATH` in `.env`)
  - Steam Web API credentials (`STEAM_ID`, `STEAM_API_KEY`)

- **Docker Agent Mode (Windows & Linux)**:
  - Docker Engine & Docker Compose v2
  - Steam account credentials (`STEAM_USERNAME`, `STEAM_PASSWORD` in `.env`)

- **SteamUtility (bundled)**: Pre-compiled executables and runtime libraries are bundled under `libs/`, built from source:

  ```text
  libs/
  ├── win-x64/      # Windows bundle (SteamUtility.exe + runtime + Steamworks.NET) - used in Direct mode
  └── linux-x64/    # Linux agent-daemon bundle (SteamUtility ELF + runtime) - used in Docker mode
  ```

  The bundles are published from the vendored C# source in `third_party/steamutility/` (pinned to [steam-game-idler](https://github.com/zevnda/steam-game-idler) v6.2.2 by zevnda, Elastic License 2.0 — see `third_party/steamutility/LICENSE`) via `scripts/build_steamutility.py`.

## Installation

### Direct CLI/TUI

Run `setup.bat` (Windows) or `./setup.sh` (Linux) from the repository root to create the virtual environment (`env/`), install dependencies, and generate config files from `.dist` templates.

```powershell
setup.bat
```
*(For development tooling including pytest and ruff, use `setup.bat --dev`)*

### Docker Stack

Run `setup.bat --config` (Windows) or `./setup.sh --config` (Linux) to copy configuration templates without setting up a local Python environment.

```powershell
setup.bat --config
```

### Configuration Templates

| Template | Live File | Purpose |
| :--- | :--- | :--- |
| `.env.dist` | `.env` | Steam credentials (`STEAM_ID`, `STEAM_API_KEY`, agent login). |
| `config.dist.toml` | `config.toml` | Application tolerance thresholds. |
| `tasks.dist.json` | `tasks.json` | Persistent task queue file. |
| `manifests/unlock-manifest.dist.json` | `manifests/unlock-manifest.json` | Achievement unlock schedules (delays per achievement). |
| `manifests/idle-manifest.dist.json` | `manifests/idle-manifest.json` | Target playtime thresholds per game. |

## Configuration

### 1. Steam Credentials (`.env`)

Edit `.env` at the project root to set your credentials and login mode:

```dotenv
# "client" (Local Windows Steam client) or "agent" (Headless Docker/Linux session)
LOGIN_MODE=client

# Steam Web API (Required for library sync)
STEAM_ID=76561198000000000
STEAM_API_KEY=0123456789ABCDEF0123456789ABCDEF

# Client Mode (Required when LOGIN_MODE=client)
STEAM_PATH=C:\Program Files (x86)\Steam

# Agent Mode (Required when LOGIN_MODE=agent)
STEAM_USERNAME=your_username
STEAM_PASSWORD=your_password
```

### 2. Application Settings (`config.toml`)

Edit `config.toml` to configure validation tolerances:

```toml
unlock_delay_tolerance_seconds = 10
idle_tolerance_seconds = 60
```

| Key | Default | Purpose |
| :--- | :--- | :--- |
| `unlock_delay_tolerance_seconds` | `10` | Allowed variance (seconds) when verifying unlock delays against actual Steam timestamps. |
| `idle_tolerance_seconds` | `60` | Allowed variance (seconds) when verifying playtime against idle targets. |

### 3. Manifests (`manifests/`)

- `manifests/unlock-manifest.json` — Defines achievement unlock delay schedules per game:
  ```json
  {
    "categories": { "priority": [570940] },
    "games": [
      {
        "app_id": 570940,
        "app_name": "Dark Souls: Remastered",
        "achievements": [
          { "apiname": "ACH_KNIGHT", "name": "Knight", "delay_seconds": 3600 }
        ]
      }
    ]
  }
  ```
- `manifests/idle-manifest.json` — Defines target playtime targets per game:
  ```json
  {
    "categories": { "priority": [570940] },
    "games": [
      {
        "app_id": 570940,
        "app_name": "Dark Souls: Remastered",
        "desired_playtime_seconds": 302514
      }
    ]
  }
  ```

---

## Usage

Commands can be run directly on the host or inside Docker containers using the launcher aliases:

### Direct Execution

#### Interactive TUI

```powershell
.\tui.bat
```

#### CLI Quick Commands

```powershell
# Sync owned games library
.\run.bat sync

# Unlock achievements for a single game
.\run.bat game --app-id 570940 unlocker unlock --all

# Run scheduled unlocker for a manifest category
.\run.bat manifest --category priority unlocker unlock

# Verify achievement unlock schedule status
.\run.bat manifest --category priority unlocker status

# Process task queue
.\run.bat tasks-run
```

### Docker Execution

```powershell
# Start background stack
.\run.bat docker-up

# Follow container logs
.\run.bat docker-logs

# Run task queue in foreground
.\run.bat docker-tasks-run

# Run task queue detached in background
.\run.bat docker-tasks-queue

# Reset task queue in container
.\run.bat docker-tasks-reset

# Launch TUI inside container
.\run.bat docker-tui
```

## CLI Reference

### Top-Level Commands

| Command | Description |
| :--- | :--- |
| `login` | Verify Steam connection and authentication status. |
| `sync [--app-id <id>]` | Sync owned games library to `data/games.json`. |
| `tasks {run,reset,add,remove}` | Manage and process the persistent task queue. |
| `manifest [-g <id> \| -c <cat> \| --all] {unlocker,idler}` | Run unlocker or idler operations over manifest scopes. |
| `game --app-id <id> {unlocker,idler,stats}` | Run direct operations on a specific Steam App ID. |

### Task Queue Management (`tasks`)

| Command | Description |
| :--- | :--- |
| `tasks run [--task-id <id>]` | Execute pending tasks in `tasks.json`. |
| `tasks reset [--task-id <id>]` | Reset task status back to `PENDING`. |
| `tasks add --command <cmd> [--args ...] [--kwargs <json>]` | Add a new task to the queue. |
| `tasks remove <task_id>` | Remove a task from the queue by ID. |

### Single Game Direct (`game --app-id <id>`)

| Tool | Command | Description |
| :--- | :--- | :--- |
| `unlocker` | `unlock [--apiname <name>]` | Unlock single achievement or all achievements in manifest schedule for this game. |
| `unlocker` | `unlock --all` | Instantly unlock all achievements. |
| `unlocker` | `lock [--apiname <name>]` | Lock achievements. |
| `unlocker` | `lock --all` | Instantly lock all achievements. |
| `unlocker` | `toggle --apiname <name>` | Toggle achievement lock state. |
| `idler` | `idle [--seconds <n>]` | Idle game (duration in seconds, or indefinitely if omitted). |
| `stats` | `get` | Display current achievement and statistic values. |
| `stats` | `update --stats-json <json>` | Update game statistics. |
| `stats` | `reset` | Reset all statistics for the game. |

### Manifest Targeting (`manifest [-g <id> | -c <cat> | --all]`)

| Tool | Command | Description |
| :--- | :--- | :--- |
| `unlocker` | `unlock [--ignore-timestamps]` | Run scheduled unlocks for targeted manifest games (offsets against actual Steam unlock times by default). |
| `unlocker` | `status` | Compare manifest delay schedules against actual unlock times. |
| `idler` | `idle` | Idle targeted manifest games toward target playtimes. |
| `idler` | `status` | Compare current playtime against manifest target playtimes. |
