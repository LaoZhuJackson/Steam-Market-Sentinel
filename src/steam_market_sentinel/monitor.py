"""Polling isolated in a QThread: no network calls on the GUI event loop."""
from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from .alerts import send_remote_notifications, send_text_notifications
from .config import Settings
from .database import Database
from .steam_api import SteamMarketClient

# After a cycle in which something failed, come back quickly instead of waiting the
# full interval: a restored network should show up on its own, without the user
# pressing 立即刷新. The per-item gap still applies, so this stays gentle.
FAILURE_RETRY_SECONDS = 120


class MonitorWorker(QThread):
    updated = Signal(int, str)
    alert_found = Signal(object)
    notice = Signal(str, str)  # subject, body — failures and recoveries
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
            had_failure = False
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
                        if item["last_error"]:
                            # The row was still marked failed, so this is a recovery.
                            self._notify(item["display_name"], item["hash_name"], None)
                        for alert in alerts:
                            self.alert_found.emit(alert)
                            for error in send_remote_notifications(self.settings, alert):
                                self.message.emit(error)
                    except Exception as exc:
                        # Operational errors should not stop monitoring other items.
                        had_failure = True
                        db.set_error(item["id"], str(exc))
                        self.message.emit(f"{item['display_name']}: {exc}")
                        # Let the table show 异常 now rather than at the end of a
                        # cycle that may still have dozens of items to go.
                        self.updated.emit(item["id"], self.settings.currency)
                        if not item["last_error"]:
                            # Only on the first failure of an outage, so an offline
                            # machine does not notify every cycle.
                            self._notify(item["display_name"], item["hash_name"], exc)
                self.cycle_complete.emit()
            except Exception as exc:
                self.message.emit(f"监控周期失败: {exc}")
            if self._stop_event.is_set():
                break
            self._wake.wait(FAILURE_RETRY_SECONDS if had_failure
                            else self.settings.interval_seconds)
            self._wake.clear()
        client.session.close()
        self.message.emit("监控服务已停止")

    def _notify(self, display_name: str, hash_name: str, exc: Exception | None) -> None:
        """Tell the user about a failure, or about a recovery from one."""
        if exc is None:
            subject = f"[Steam 监控恢复] {display_name}"
            body = f"已能重新获取「{hash_name}」的价格，监控恢复正常。"
        else:
            subject = f"[Steam 监控异常] {display_name}"
            body = (f"未能获取「{hash_name}」的价格：{exc}\n\n"
                    "常见原因是当前网络无法访问 Steam 市场（例如国内直连）。"
                    "网络恢复后会自动重试，成功时会再通知你一次。")
        self.notice.emit(subject, body)
        if self.settings.error_alert_enabled:
            for error in send_text_notifications(self.settings, subject, body):
                self.message.emit(error)
