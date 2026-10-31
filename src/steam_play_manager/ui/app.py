from pathlib import Path

from textual.app import App

from steam_play_manager.services.game_idler import terminate_active_idle_processes
from steam_play_manager.ui.screens.menus import MainMenuScreen


class UnlockerApp(App):
    TITLE = "STEAM PLAY MANAGER"
    ENABLE_COMMAND_PALETTE = True
    CSS_PATH = str(Path(__file__).parent / "style.tcss")

    def on_mount(self) -> None:
        self.theme = "textual-dark"
        self.push_screen(MainMenuScreen())

    def on_unmount(self) -> None:
        terminate_active_idle_processes()
