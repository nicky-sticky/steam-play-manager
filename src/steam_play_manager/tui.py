import atexit

from steam_play_manager.core.logger import setup_logger
from steam_play_manager.core.utils import enable_ansi_utf8
from steam_play_manager.services.game_idler import terminate_active_idle_processes
from steam_play_manager.ui.app import UnlockerApp

# Clean up SteamUtility idle subprocesses on exit
atexit.register(terminate_active_idle_processes)

enable_ansi_utf8()

setup_logger(verbose=False, console=False)


def main() -> None:
    """Launch the Textual application."""
    app = UnlockerApp()
    app.run()


if __name__ == "__main__":
    main()
