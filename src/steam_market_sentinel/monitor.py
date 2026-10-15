"""Polling isolated in a QThread: no network calls on the GUI event loop."""
from __future__ import annotations

import random
import threading
import time

from PySide6.QtCore import QThread, Signal

from .alerts import send_remote_notifications, send_text_notifications
from .config import Settings
from .database import Database
from .steam_api import SteamMarketClient, SteamRateLimited

# After a cycle in which something failed, come back quickly instead of waiting the
# full interval: a restored network should show up on its own, without the user
# pressing 立即刷新. The per-item gap still applies, so this stays gentle.
FAILURE_RETRY_SECONDS = 120

# Rate limiting is the opposite situation: coming back quickly is what keeps the
# limit alive. Steam limits the *market* per exit IP (measured: every market URL
# answered 429 while `/my/`, the store and api.steampowered.com answered 200 from
# the same IP), so a limited cycle stops early and waits this long, doubling per
# consecutive limited cycle up to the cap. One hour is the point at which waiting
# longer than the user's own cycle interval stops buying anything.
RATE_LIMIT_BACKOFF_SECONDS = 600
RATE_LIMIT_BACKOFF_CAP = 3600

# An outage is almost never item-specific. When the network cannot reach
# steamcommunity.com, every item in the watchlist fails in turn — so a notice per
# item means twenty popups and twenty emails all saying the same thing, and the same
# again when it recovers. Only the first notice of a burst gets through; the rest
# still update the row state, and how many were held back is reported in the next
# notice that is sent, so nothing is dropped silently.
NOTICE_WINDOW_SECONDS = 300


def rate_limit_backoff(level: int, jitter: float | None = None) -> float:
    """How long to wait after ``level`` consecutive rate-limited cycles (1 = first).

    10 minutes, then 20, 40, 60 (capped), each nudged by up to ±10% so a watchlist
    that was limited while sharing an IP does not come back in lockstep with every
    other instance behind it. ``jitter`` is injectable for tests.
    """
    base = min(RATE_LIMIT_BACKOFF_SECONDS * (2 ** max(0, level - 1)), RATE_LIMIT_BACKOFF_CAP)
    fraction = random.uniform(-0.1, 0.1) if jitter is None else jitter
    return max(60.0, base * (1 + fraction))


class NoticeGate:
    """At most one notice of each kind (failure / recovery) per window."""

    def __init__(self, window_seconds: float = NOTICE_WINDOW_SECONDS,
                 clock=time.monotonic):
        self.window_seconds = window_seconds
        self._clock = clock
        self._sent_at: dict[str, float] = {}
        self._held: dict[str, int] = {}

    def allow(self, kind: str) -> int | None:
        """``None`` to stay quiet, otherwise how many earlier notices this one covers.

        The count is handed to the next notice that *is* sent, which is what keeps
        the quiet window from hiding the fact that other items had problems too.
        """
        now = self._clock()
        last = self._sent_at.get(kind)
        if last is not None and now - last < self.window_seconds:
            self._held[kind] = self._held.get(kind, 0) + 1
            return None
        self._sent_at[kind] = now
        return self._held.pop(kind, 0)


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
        self.notice_gate = NoticeGate()

    def stop(self):
        self._stop_event.set()
        self._wake.set()

    def refresh_now(self):
        self._wake.set()

    def run(self):
        db = Database(self.database_path)
        # Hand the client our stop event: quitting must not wait out a stack of
        # retry sleeps (see SteamMarketClient._sleep).
        client = SteamMarketClient(cancel=self._stop_event,
                                   proxy=self.settings.proxy_argument())
        self.message.emit("监控服务已启动")
        rate_limit_level = 0
        while not self._stop_event.is_set():
            had_failure = False
            rate_limited = False
            try:
                watchlist = db.list_items(self.settings.currency, enabled_only=True)
                if not watchlist:
                    self.message.emit("尚无启用的监控商品")
                for index, item in enumerate(watchlist):
                    if self._stop_event.is_set():
                        break
                    if index > 0 and self._stop_event.wait(self._item_gap()):
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
                    except SteamRateLimited as exc:
                        # The market limits the *exit IP*, so every remaining item
                        # would be refused too — and asking anyway is what keeps the
                        # limit alive. Stop the cycle and back off instead.
                        if self._stop_event.is_set():
                            break
                        rate_limited = True
                        db.set_error(item["id"], str(exc), kind="rate_limit")
                        self.updated.emit(item["id"], self.settings.currency)
                        if not item["last_error"]:
                            self._notify(item["display_name"], item["hash_name"], exc,
                                         kind="rate_limit")
                        break
                    except Exception as exc:
                        if self._stop_event.is_set():
                            # We are shutting down: whatever the request raised is a
                            # cancellation, not a monitoring failure. Without this
                            # the last item would be marked 异常 on the way out.
                            break
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
            rate_limit_level = rate_limit_level + 1 if rate_limited else 0
            if rate_limited:
                delay = rate_limit_backoff(rate_limit_level)
                self.message.emit(
                    f"Steam 限流：已暂停整轮，{delay / 60:.0f} 分钟后重试"
                    "（市场按出口 IP 限流：换个代理节点通常立刻见效，"
                    "或把「每轮检查间隔」调大）")
            elif had_failure:
                delay = FAILURE_RETRY_SECONDS
            else:
                delay = self.settings.interval_seconds
            self._wake.wait(delay)
            self._wake.clear()
        client.session.close()
        self.message.emit("监控服务已停止")

    def _item_gap(self) -> float:
        """The configured per-item gap, jittered.

        A perfectly even cadence is the easiest thing for a rate limiter to spot, so
        the gap is only ever *lengthened*: the setting stays a floor the user can
        reason about ("至少 6 秒"), and the extra 0–25% decorrelates the requests.
        """
        gap = self.settings.request_gap_seconds
        return gap + random.uniform(0, max(1.0, gap * 0.25))

    def _notify(self, display_name: str, hash_name: str, exc: Exception | None,
                kind: str | None = None) -> None:
        """Tell the user about a failure, or about a recovery from one.

        Rate-limited through ``notice_gate``: one outage hits every item, and a
        notice per item is noise rather than information (see NoticeGate). ``kind``
        defaults to "failure"/"recovery"; a rate limit passes its own kind so it is
        counted and quieted separately from a network outage — they need different
        things from the user.
        """
        if kind is None:
            kind = "recovery" if exc is None else "failure"
        held = self.notice_gate.allow(kind)
        if held is None:
            return  # state is already updated; stay quiet
        if exc is None:
            subject = f"[Steam 监控恢复] {display_name}"
            body = f"已能重新获取「{hash_name}」的价格，监控恢复正常。"
        elif kind == "rate_limit":
            subject = f"[Steam 限流 429] {display_name}"
            body = (f"Steam 正在限流当前出口 IP，未能获取「{hash_name}」的价格。\n\n"
                    "市场是按出口 IP 限流的，与轮询节奏无关（同一个 IP 上非市场页面、"
                    "商店和官方 API 仍然正常），所以调快调慢都不解决。"
                    "程序已暂停整轮并逐步退避；换个代理节点通常立刻见效，"
                    "也可以把「每轮检查间隔」调大。")
        else:
            subject = f"[Steam 监控异常] {display_name}"
            body = (f"未能获取「{hash_name}」的价格：{exc}\n\n"
                    "常见原因是当前网络无法访问 Steam 市场（例如国内直连）。"
                    "网络恢复后会自动重试，成功时会再通知你一次。")
        if held:
            what = "也已恢复" if exc is None else "也出现了同样的问题"
            body += (f"\n\n同一批里另有 {held} 件商品{what}，"
                     f"{int(self.notice_gate.window_seconds) // 60} 分钟内不再逐条提醒。")
        self.notice.emit(subject, body)
        if self.settings.error_alert_enabled:
            for error in send_text_notifications(self.settings, subject, body):
                self.message.emit(error)
