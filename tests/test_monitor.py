"""One outage must not turn into one notification per watched item."""
import pytest

# monitor.py imports PySide6.QtCore; on a machine without the Qt runtime libraries
# that import is what fails, so skip the module rather than error out mid-collection.
pytest.importorskip("PySide6.QtCore")

from steam_market_sentinel import monitor as monitor_module
from steam_market_sentinel.config import Settings
from steam_market_sentinel.database import Database
from steam_market_sentinel.models import MarketItem
from steam_market_sentinel.monitor import (
    NOTICE_WINDOW_SECONDS, RATE_LIMIT_BACKOFF_CAP, MonitorWorker, NoticeGate,
    rate_limit_backoff,
)
from steam_market_sentinel.steam_api import SteamRateLimited


class FakeClock:
    """time.monotonic, under the test's control."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _worker(tmp_path):
    """A worker that never polls and never sends remote notifications."""
    worker = MonitorWorker(tmp_path / "db.sqlite", Settings(error_alert_enabled=False))
    clock = FakeClock()
    worker.notice_gate = NoticeGate(clock=clock)
    seen: list[tuple[str, str]] = []
    worker.notice.connect(lambda subject, body: seen.append((subject, body)))
    return worker, clock, seen


def test_notice_gate_lets_the_first_of_a_burst_through():
    clock = FakeClock()
    gate = NoticeGate(clock=clock)
    assert gate.allow("failure") == 0      # first failure of the burst
    assert gate.allow("failure") is None   # the rest only update state
    assert gate.allow("failure") is None
    clock.advance(NOTICE_WINDOW_SECONDS + 1)
    # The next notice reports what it covered, so silence is never total.
    assert gate.allow("failure") == 2


def test_notice_gate_keeps_failures_and_recoveries_apart():
    clock = FakeClock()
    gate = NoticeGate(clock=clock)
    assert gate.allow("failure") == 0
    clock.advance(10)
    # A recovery right after a failure is a different event, not a repeat of it.
    assert gate.allow("recovery") == 0


def test_every_item_failing_notifies_once(tmp_path):
    worker, clock, seen = _worker(tmp_path)
    error = RuntimeError("网络请求失败（ConnectionError）")
    for name in ("商品A", "商品B", "商品C"):
        worker._notify(name, name, error)
    assert len(seen) == 1 and "商品A" in seen[0][0]
    # After the window the count of swallowed ones travels with the next notice.
    clock.advance(NOTICE_WINDOW_SECONDS + 1)
    worker._notify("商品D", "D", error)
    assert len(seen) == 2
    assert "另有 2 件商品也出现了同样的问题" in seen[1][1]


def test_recovery_burst_reports_the_held_ones(tmp_path):
    worker, clock, seen = _worker(tmp_path)
    worker._notify("商品A", "A", RuntimeError("boom"))
    worker._notify("商品B", "B", None)   # first recovery: sent
    worker._notify("商品C", "C", None)   # held
    assert len(seen) == 2 and "恢复" in seen[1][0]
    clock.advance(NOTICE_WINDOW_SECONDS + 1)
    worker._notify("商品D", "D", None)
    assert "另有 1 件商品也已恢复" in seen[-1][1]


def test_a_request_that_fails_while_quitting_is_not_a_failure(tmp_path, monkeypatch):
    """Closing the app must not mark the item 异常 or notify about it.

    The stop event arrives while a request is in flight, so the request fails on
    its way out — that is a cancellation, not a monitoring failure.
    """
    path = tmp_path / "db.sqlite"
    db = Database(path)
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"))
    worker = MonitorWorker(path, Settings(auto_start=False, error_alert_enabled=False))
    seen: list[str] = []
    worker.notice.connect(lambda subject, _body: seen.append(subject))

    class ClosingClient:
        """Stands in for a poll the user interrupted by closing the window."""

        def __init__(self, **_kwargs):
            self.session = type("Session", (), {"close": staticmethod(lambda: None)})()

        def price(self, *_args):
            worker.stop()
            raise RuntimeError("网络请求失败（ConnectionError）")

    monkeypatch.setattr(monitor_module, "SteamMarketClient", ClosingClient)
    worker.run()  # run inline: the request fails on the first item

    assert seen == []
    assert db.get_item(item, "CNY")["last_error"] is None


def test_the_poll_client_gets_the_configured_proxy(tmp_path, monkeypatch):
    """The monitor must honour 设置 → 网络代理; a blocked route is the usual failure."""
    seen = {}

    class RecordingClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def price(self, *_args):
            raise RuntimeError("boom")

        session = type("Session", (), {"close": staticmethod(lambda: None)})()

    settings = Settings(auto_start=False, error_alert_enabled=False,
                        proxy_mode="custom", proxy_url="http://127.0.0.1:7897")
    worker = MonitorWorker(tmp_path / "db.sqlite", settings)
    monkeypatch.setattr(monitor_module, "SteamMarketClient", RecordingClient)
    worker.stop()
    worker.run()
    assert seen["proxy"] == "http://127.0.0.1:7897"


def test_rate_limit_backoff_doubles_and_is_capped():
    # A limited IP needs minutes, not seconds — and coming back at the same moment as
    # every other instance behind that IP is what keeps the limit alive.
    assert [rate_limit_backoff(level, jitter=0) for level in (1, 2, 3)] == [600, 1200, 2400]
    assert rate_limit_backoff(9, jitter=0) == RATE_LIMIT_BACKOFF_CAP  # capped, never endless
    assert rate_limit_backoff(1, jitter=0.1) > 600       # jitter spreads instances out
    assert rate_limit_backoff(1, jitter=-9.9) >= 60.0    # but never collapses to zero


def test_the_item_gap_is_jittered_but_never_shortened():
    settings = Settings(auto_start=False, request_gap_seconds=20)
    worker = MonitorWorker("unused.sqlite", settings)
    gaps = [worker._item_gap() for _ in range(50)]
    assert all(20 <= gap <= 25 for gap in gaps)
    assert len(set(gaps)) > 1  # actually jittered, not a constant


def test_a_rate_limited_cycle_stops_instead_of_walking_the_list(tmp_path, monkeypatch):
    """One 429 covers every item: asking anyway is what keeps the limit alive."""
    path = tmp_path / "db.sqlite"
    db = Database(path)
    for index in range(3):
        db.add_item(MarketItem(730, f"Item {index}", f"Item {index}"))
    worker = MonitorWorker(path, Settings(auto_start=False, error_alert_enabled=False))
    calls = []
    messages = []
    worker.message.connect(messages.append)

    class LimitedClient:
        def __init__(self, **_kwargs):
            self.session = type("Session", (), {"close": staticmethod(lambda: None)})()

        def price(self, *_args):
            calls.append(1)
            raise SteamRateLimited("Steam 限流 (HTTP 429)", suggested_wait=None)

    monkeypatch.setattr(monitor_module, "SteamMarketClient", LimitedClient)
    worker._wake.set()  # do not sleep out the real backoff inside the test
    # Quit as soon as the backoff message arrives: one cycle is all this test wants.
    worker.message.connect(lambda text: worker.stop() if "限流" in text else None)
    worker.run()

    assert len(calls) == 1  # stopped at the first item, not all three
    row = db.list_items("CNY")[0]
    assert row["last_error_kind"] == "rate_limit" and row["last_error"]
    assert any("限流" in text and "分钟" in text for text in messages)


def test_a_rate_limit_notice_is_quieted_separately_from_a_failure(tmp_path):
    # They need different things from the user, so one must not silence the other.
    worker, _clock, seen = _worker(tmp_path)
    worker._notify("商品A", "A", SteamRateLimited("限流"), kind="rate_limit")
    worker._notify("商品B", "B", RuntimeError("断网"))
    assert len(seen) == 2
    assert "限流" in seen[0][0] and "异常" in seen[1][0]
    assert "出口 IP" in seen[0][1]  # says how to actually fix it
