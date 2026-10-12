"""Application entry point."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .config import Settings, data_directory
from .database import Database
from .style import apply_theme
from .ui import MainWindow, app_icon


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SteamMarketSentinel")
    app.setOrganizationName("SteamMarketSentinel")
    app.setWindowIcon(app_icon())
    apply_theme(app)
    home = data_directory()
    settings_file = home / "settings.json"
    main_window = MainWindow(Database(home / "sentinel.sqlite3"),
                             Settings.load(settings_file), settings_file)
    main_window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
