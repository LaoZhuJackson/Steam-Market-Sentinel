import json
import ssl
import threading
import time

import pytest
import requests

from steam_market_sentinel.steam_api import (
    SteamAPIError, SteamMarketClient, SteamRateLimited, apply_proxy, failure_message,
    parse_icon_url, parse_imported_history,
)


class Response:
    status_code = 200
    headers = {}

    def __init__(self, result):
        self.result = result

    def json(self):
        return self.result

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, result):
        self.result = result
        self.headers = {}
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params))
        return Response(self.result)


class ErrorResponse:
    def __init__(self, status):
        self.status_code = status
        self.headers = {}
        self.text = "null"


class ErrorSession:
    def __init__(self, status):
        self.status = status
        self.headers = {}

    def get(self, url, params, timeout):
        return ErrorResponse(self.status)


def test_quote_and_currency_parameters():
    session = FakeSession({"success": True, "lowest_price": "¥10.20", "volume": "123"})
    price, volume = SteamMarketClient(session=session).price(730, "AK-47 | Redline", "CNY")
    assert (price, volume) == (1020, "123")
    assert session.calls[0][1]["currency"] == 23
    assert session.calls[0][1]["market_hash_name"] == "AK-47 | Redline"


def test_search_result_has_image():
    session = FakeSession({"success": True, "total_count": 1, "results": [{
        "name": "Item", "hash_name": "Item", "app_name": "Game",
        "sell_price_text": "¥1.00",
        "asset_description": {"appid": 730, "icon_url": "abc123"},
    }]})
    items = SteamMarketClient(session=session).search("Item", 730)
    assert len(items) == 1
    assert items[0].appid == 730
    assert items[0].image_url.endswith("/abc123/128fx128f")


def test_history_med_price_parsing():
    session = FakeSession({"success": True, "prices": [["Nov 27 2013 01: +0", 12.34, "7"]],
                           "price_prefix": "¥", "price_suffix": ""})
    points, unit = SteamMarketClient(session=session).official_history(730, "Item")
    assert points[0][1] == 12.34
    assert unit == "¥…"
    assert session.calls[0][0].endswith("/pricehistory/")


def test_http_errors_carry_no_url_or_requests_internals():
    # These strings are shown verbatim in the status bar.
    client = SteamMarketClient(session=ErrorSession(400), retries=0)
    with pytest.raises(SteamAPIError) as excinfo:
        client.price(730, "Item", "CNY")
    message = str(excinfo.value)
    assert "HTTP 400" in message
    assert "steamcommunity.com" not in message
    assert "Client Error" not in message


def test_anonymous_history_rejection_is_explained():
    client = SteamMarketClient(session=ErrorSession(400), retries=0)
    with pytest.raises(SteamAPIError) as excinfo:
        client.official_history(3678970, "Ancient Arrow (Beyond)")
    message = str(excinfo.value)
    assert "未登录" in message
    assert "steamcommunity.com" not in message


OG_PAGE = ('<html><head><meta property="og:image" '
           'content="https://community.steamstatic.com/economy/image/ABC123"></head></html>')


def test_parse_icon_url_reads_the_opengraph_tag():
    assert parse_icon_url(OG_PAGE).endswith("/economy/image/ABC123")


def test_parse_icon_url_accepts_reversed_attribute_order():
    page = ('<meta content="https://community.steamstatic.com/economy/image/X" '
            'property="og:image">')
    assert parse_icon_url(page).endswith("/economy/image/X")


@pytest.mark.parametrize("page", [
    "<html><head></head></html>",                                          # no tag at all
    '<meta property="og:image" content="http://x.steamstatic.com/a">',      # not https
    '<meta property="og:image" content="https://evil.example.com/a">',      # not Steam's CDN
    '<meta property="og:image" content="">',
])
def test_parse_icon_url_rejects_anything_unusable(page):
    # A page must not be able to point the app at an arbitrary host.
    assert parse_icon_url(page) == ""


def test_imported_history_parses_the_canonical_response():
    payload = json.dumps({
        "success": True,
        "prices": [["Nov 27 2013 01: +0", 12.34, "7"], ["Nov 28 2013 01: +0", 11.00, "3"]],
        "price_prefix": "¥", "price_suffix": "",
    })
    points, label = parse_imported_history(payload)
    # 12.34 * 100 is 1233.99... in binary float; cents must be 1234, not 1233.
    assert [cents for _stamp, cents in points] == [1234, 1100]
    assert label == "¥…"


def test_imported_history_accepts_a_bare_array_with_console_noise():
    text = '响应: [["Nov 27 2013 01: +0", 5.5, "2"]]\n已复制到剪贴板'
    points, label = parse_imported_history(text)
    assert points[0][1] == 550
    assert label == ""


# Verbatim excerpt of a real response captured from a logged-in browser session
# (appid 3678970). Note the zero-padded day, the ': +0' suffix and 3-decimal
# prices — all of which a naive parser drops or mis-rounds.
REAL_PAYLOAD = (
    '{"success":true,"price_prefix":"¥","price_suffix":"","prices":['
    '["Sep 08 2026 01: +0",44.642,"34"],["Sep 09 2026 01: +0",40.135,"48"],'
    '["Sep 10 2026 01: +0",27.923,"52"],["Sep 11 2026 01: +0",26.28,"46"],'
    '["Sep 12 2026 01: +0",21.668,"62"],["Sep 12 2026 17: +0",20.827,"4"],'
    '["Oct 12 2026 15: +0",3.997,"3"]]}'
)


def test_imported_history_handles_a_real_captured_payload():
    points, label = parse_imported_history(REAL_PAYLOAD)
    assert label == "¥…"          # CNY, from the response itself
    assert len(points) == 7
    cents = [value for _stamp, value in points]
    assert cents[0] == 4464                 # 44.642 -> rounds up, not truncates
    assert cents[3] == 2628
    assert cents[-1] == 400                 # 3.997 -> 399.7 -> 400
    stamps = [stamp for stamp, _value in points]
    assert stamps == sorted(stamps)         # chronological, not just parseable
    # The day is zero-padded and the hour is followed by ': +0' — both must survive.
    assert stamps[-1] - stamps[0] > 30 * 86400


@pytest.mark.parametrize("bad", [
    "null",
    "",
    "not json at all",
    json.dumps({"success": False, "prices": []}),
    json.dumps({"success": True, "prices": []}),
    json.dumps({"success": True}),
])
def test_imported_history_rejects_unusable_input(bad):
    with pytest.raises(ValueError):
        parse_imported_history(bad)


def test_a_cancelled_client_stops_before_making_any_request():
    # Quitting must not wait out retries: with the monitor's stop event already set,
    # no request is started and no backoff is slept through.
    cancel = threading.Event()
    client = SteamMarketClient(session=FakeSession({"success": True}), cancel=cancel)
    cancel.set()
    started = time.monotonic()
    with pytest.raises(SteamAPIError, match="取消"):
        client.price(730, "Revolution Case", "CNY")
    assert client.session.calls == []
    with pytest.raises(SteamAPIError, match="取消"):
        client._sleep(30)  # the exact delay does not matter: wait() returns at once
    assert time.monotonic() - started < 1


def test_proxy_modes_reach_the_session():
    # "" keeps requests' own behaviour, "direct" turns it off, a URL overrides it.
    system = requests.Session()
    apply_proxy(system, "")
    assert system.trust_env is True and not system.proxies

    direct = SteamMarketClient(session=requests.Session(), proxy="direct")
    assert direct.session.trust_env is False and direct.session.proxies == {}

    custom = SteamMarketClient(session=requests.Session(), proxy="http://127.0.0.1:7897")
    assert custom.session.proxies["http"] == "http://127.0.0.1:7897"
    assert custom.session.proxies["https"] == "http://127.0.0.1:7897"


def test_failure_message_names_the_layer_that_failed():
    # A blocked direct route resets the TLS handshake. "网络请求失败（ConnectionError）"
    # left the user with nothing to act on, which is what this mapping fixes.
    reset = requests.exceptions.ConnectionError("('Connection aborted.', ConnectionResetError)")
    reset.__cause__ = ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接。")
    message = failure_message(reset)
    assert "连接被重置" in message and "代理" in message

    # requests also reports a dropped handshake as SSLError — that is a cut route,
    # not a certificate problem, so the chain has to be inspected before the class.
    dropped = requests.exceptions.SSLError("EOF occurred in violation of protocol")
    dropped.__context__ = ssl.SSLEOFError("EOF occurred in violation of protocol")
    assert "连接被重置" in failure_message(dropped)

    assert "代理连接失败" in failure_message(
        requests.exceptions.ProxyError("cannot connect to proxy"))
    assert "TLS 证书" in failure_message(requests.exceptions.SSLError("bad certificate"))
    assert "超时" in failure_message(requests.exceptions.ConnectTimeout("timed out"))
    assert "代理地址无效" in failure_message(requests.exceptions.InvalidSchema("no scheme"))
    # requests says this when socks:// is used without PySocks; the fix is one pip line.
    assert "requests[socks]" in failure_message(
        requests.exceptions.InvalidSchema("Missing dependencies for SOCKS support."))
    # Anything unmapped still names its own class, rather than claiming a cause.
    assert failure_message(requests.exceptions.TooManyRedirects("loop")) == \
        "网络请求失败（TooManyRedirects）"


class RateLimitSession:
    """Answers 429 (with an optional Retry-After) a few times, then the result."""

    def __init__(self, limit=1, retry_after="", result=None):
        self.headers = {}
        self.calls = 0
        self.limit = limit
        self.retry_after = retry_after
        self.result = result or {"success": True, "lowest_price": "¥1.00", "volume": "1"}

    def get(self, url, params, timeout):
        self.calls += 1
        if self.calls <= self.limit:
            response = ErrorResponse(429)
            if self.retry_after:
                response.headers["Retry-After"] = self.retry_after
            return response
        return Response(self.result)


def test_rate_limit_is_not_hammered_with_retries():
    # The market's 429 is per exit IP and lasts minutes: three attempts per item times
    # twenty items is sixty requests, which is what keeps the limit alive.
    session = RateLimitSession(limit=99)
    client = SteamMarketClient(session=session, retries=2)
    with pytest.raises(SteamRateLimited) as caught:
        client.price(730, "Revolution Case", "CNY")
    assert session.calls == 1
    assert caught.value.status == 429
    assert caught.value.suggested_wait is None
    assert isinstance(caught.value, SteamAPIError)  # existing callers keep working

    long_wait = RateLimitSession(limit=99, retry_after="300")
    with pytest.raises(SteamRateLimited) as caught:
        SteamMarketClient(session=long_wait, retries=2).price(730, "Revolution Case", "CNY")
    assert long_wait.calls == 1  # a 5-minute wait is the caller's business, not ours
    assert caught.value.suggested_wait == 300


def test_a_short_retry_after_is_honoured_with_one_retry():
    # Steam telling us "1 second" is worth waiting out; a limitation it just told us
    # about is not the same as a wedged socket.
    session = RateLimitSession(limit=1, retry_after="1")
    price, _volume = SteamMarketClient(session=session, retries=2).price(
        730, "Revolution Case", "CNY")
    assert (session.calls, price) == (2, 100)
