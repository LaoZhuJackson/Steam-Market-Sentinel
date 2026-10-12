"""Polling isolated in a QThread: no network calls on the GUI event loop."""
from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from .alerts import send_remote_notifications
from .config import Settings
from .database import Database
from .steam_api import SteamMarketClient


class MonitorWorker(QThread):
    updated = Signal(int, str)
    alert_found = Signal(object)
    message = Signal(str)
    cycle_complete = Signal()

    def __init__(self, database_path, settings: Settings):
        super().__init__()
        self.database_path = database_path
        self.settings = settings
        self._stop_event = threading.Event()
        self._wake = threading.Event()

    def stop(self):
        self._stop_event.set()
        self._wake.set()

    def refresh_now(self):
        self._wake.set()

    def run(self):
        db = Database(self.database_path)
        client = SteamMarketClient()
        self.message.emit("监控服务已启动")
        while not self._stop_event.is_set():
            try:
                watchlist = db.list_items(self.settings.currency, enabled_only=True)
                if not watchlist:
                    self.message.emit("尚无启用的监控商品")
                for index, item in enumerate(watchlist):
                    if self._stop_event.is_set():
                        break
                    if index > 0 and self._stop_event.wait(self.settings.request_gap_seconds):
                        break
                    try:
                        amount, volume = client.price(
                            item["appid"], item["hash_name"], self.settings.currency)
                        alerts = db.record_snapshot(item["id"], self.settings.currency,
                                                    amount, volume,
                                                    drop_alerts=self.settings.drop_alert_enabled)
                        self.updated.emit(item["id"], self.settings.currency)
                        for alert in alerts:
                            self.alert_found.emit(alert)
                            for error in send_remote_notifications(self.settings, alert):
                                self.message.emit(error)
                    except Exception as exc:
                        # Operational errors should not stop monitoring other items.
                        db.set_error(item["id"], str(exc))
                        self.message.emit(f"{item['display_name']}: {exc}")
                self.cycle_complete.emit()
            except Exception as exc:
                self.message.emit(f"监控周期失败: {exc}")
            if self._stop_event.is_set():
                break
            self._wake.wait(self.settings.interval_seconds)
            self._wake.clear()
        client.session.close()
        self.message.emit("监控服务已停止")
