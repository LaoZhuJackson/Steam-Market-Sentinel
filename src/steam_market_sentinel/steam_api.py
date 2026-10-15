"""Steam's undocumented public endpoints. No account credentials required."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import random
import ssl
import threading
import time
from urllib.parse import quote, urlsplit

import requests

from .models import CURRENCIES, MarketItem, from_amount, parse_price_cents

ROOT = "https://steamcommunity.com/market"
IMAGE_PREFIX = "https://community.fastly.steamstatic.com/economy/image/"

_DATE_PREFIX = re.compile(r"[A-Za-z]{3}\s+\d{1,2}\s+\d{4}\s+\d{2}")

# The listing page advertises the item icon in its OpenGraph tags. That is a stable,
# documented place for it — unlike the page's own hashed class names — so it is the
# only part of the markup this app reads. Both attribute orders are accepted.
_OG_IMAGE_PATTERNS = (
    r"""<meta[^>]+property=["']og:image["'][^>]*?content=["']([^"']+)["']""",
    r"""<meta[^>]+content=["']([^"']+)["'][^>]*?property=["']og:image["']""",
)


class SteamAPIError(Exception):
    """A failed Steam call, with a message safe to show to the user."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class SteamRateLimited(SteamAPIError):
    """Steam answered 429. It limits the *market* per IP, not per session.

    That means every item in a watchlist is limited at the same moment, so the
    caller must stop the whole cycle and back off instead of walking the rest of
    the list (see `MonitorWorker`). ``suggested_wait`` is Steam's own ``Retry-After``
    when it sends one, else ``None``.
    """

    def __init__(self, message: str, suggested_wait: float | None = None):
        super().__init__(message, 429)
        self.suggested_wait = suggested_wait


# A 429 with a short Retry-After is worth one in-place wait; anything longer is an
# IP-level limit that will still be there in seconds, and retrying it is exactly what
# keeps it alive. See the 429 handling in `_request`.
MAX_RATE_LIMIT_RETRY_WAIT = 20.0


def parse_icon_url(html: str) -> str:
    """The item thumbnail Steam advertises in a listing page's OpenGraph tags.

    Returns ``""`` when there is no usable ``og:image`` — only HTTPS URLs on Steam's
    image CDN are accepted, so a page cannot point the app at an arbitrary host.
    """
    for pattern in _OG_IMAGE_PATTERNS:
        match = re.search(pattern, html, re.I)
        if not match:
            continue
        url = match.group(1).strip()
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.scheme == "https" and host.endswith(".steamstatic.com"):
            return url
    return ""


def parse_history_entries(entries) -> list[tuple[int, float]]:
    """Turn Steam's ``[[date, price, volume], ...]`` rows into epoch/price pairs."""
    history: list[tuple[int, float]] = []
    for entry in entries:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        try:
            # Steam dates look like 'Nov 27 2013 01: +0'; chart times are approximate.
            date_match = _DATE_PREFIX.match(str(entry[0]))
            if not date_match:
                continue
            stamp = int(datetime.strptime(" ".join(date_match.group().split()), "%b %d %Y %H").replace(
                tzinfo=timezone.utc).timestamp())
            history.append((stamp, float(entry[1])))
        except (ValueError, TypeError):
            continue
    return history


def _first_json_value(text: str):
    """Return the first JSON value in ``text``.

    A paste usually carries console noise ("响应: {...}") around the payload, so
    scan for the first brace/bracket that actually starts a JSON value.
    """
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(text, index)
        except ValueError:
            continue
        return value
    raise ValueError("没有找到 JSON 数据（请确认粘贴的是脚本输出的完整响应）")


def parse_imported_history(text: str) -> tuple[list[tuple[int, int]], str]:
    """Parse history pasted from the browser into ``(epoch, cents)`` points.

    Accepts Steam's ``pricehistory`` response
    (``{"success":true,"prices":[["Nov 27 2013 01: +0",12.34,"7"],...]}``) or a
    bare ``[[date, price], ...]`` array. Prices become integer cents here, so the
    database stores integers and the chart divides by 100 exactly once:
    ``int(price * 100)`` would be a cent low (12.34 * 100 == 1233.99...), hence
    ``models.from_amount``.
    """
    payload = _first_json_value(text)
    if isinstance(payload, dict):
        if not payload.get("success"):
            raise ValueError("Steam 返回 success=false，未取得该商品的成交历史")
        entries = payload.get("prices")
        if entries is None:
            raise ValueError("响应里没有 prices 字段（可能是 null 或错误响应）")
        prefix = str(payload.get("price_prefix") or "")
        suffix = str(payload.get("price_suffix") or "")
        label = f"{prefix}…{suffix}" if (prefix or suffix) else ""
    elif isinstance(payload, list):
        entries, label = payload, ""
    else:
        raise ValueError("无法识别的数据格式（既不是对象也不是数组）")
    if not isinstance(entries, list):
        raise ValueError("prices 字段不是数组")
    points = [(stamp, from_amount(price)) for stamp, price in parse_history_entries(entries)]
    if not points:
        raise ValueError("未解析出任何数据点（日期或价格格式可能已变化）")
    return points, label


def apply_proxy(session: requests.Session, proxy: str = "") -> None:
    """Point a session at the configured route to Steam (see ``config.PROXY_MODES``).

    ``""`` leaves requests' own behaviour intact (an enabled Windows proxy, then
    ``HTTP(S)_PROXY``); ``"direct"`` ignores both; anything else is an explicit proxy
    URL. Steam's market is blocked on many networks while the *store* and the image
    CDN still answer, and a browser can be fine while this process is not — a game
    accelerator usually covers only the browser or the game, and ``requests`` cannot
    read a PAC script at all. That asymmetry is why the route has to be selectable.
    """
    if proxy == "direct":
        session.trust_env = False
        session.proxies.clear()
    elif proxy:
        session.proxies.update({"http": proxy, "https": proxy})


# A blocked route is not a certificate problem, even though requests wraps both in
# SSLError: the peer drops the handshake, which surfaces as a reset or a bare EOF.
_ROUTE_CUT_MARKERS = (ConnectionResetError, ConnectionAbortedError, ssl.SSLEOFError)


def _deepest_cause(exc: BaseException) -> BaseException:
    """Follow the exception chain to the bottom.

    requests/urllib3 link their inner exceptions with ``__context__`` as often as
    with ``__cause__``, so both must be followed — walking only ``__cause__`` misses
    the reset that explains the failure. Bounded, because the chain is user-visible.
    """
    node = exc
    for _ in range(8):
        nxt = node.__cause__ or node.__context__
        if nxt is None or nxt is node:
            break
        node = nxt
    return node


def failure_message(exc: requests.RequestException) -> str:
    """A short Chinese message naming the layer that failed, shown verbatim in the UI.

    "网络请求失败（ConnectionError）" told the user nothing — the user hit exactly
    that while their browser could still open the market. The usual cause here is a
    blocked direct route (Steam's *market* is blocked while the store and the image
    CDN still answer), which resets the connection during the TLS handshake, so that
    case names the cause and the way out instead of the exception class.
    """
    if isinstance(exc, requests.exceptions.ProxyError):
        return "代理连接失败：请检查设置里的代理地址、代理是否已启动"
    if isinstance(exc, requests.exceptions.InvalidSchema) and "SOCKS" in str(exc).upper():
        return '代理地址是 SOCKS，需要额外依赖：pip install "requests[socks]"'
    if isinstance(exc, (requests.exceptions.InvalidSchema, requests.exceptions.InvalidURL,
                        requests.exceptions.MissingSchema, requests.exceptions.URLRequired)):
        return "代理地址无效：需要以 http:// 或 socks5:// 开头"
    if isinstance(_deepest_cause(exc), _ROUTE_CUT_MARKERS):
        return ("连接被重置（TLS 握手被中断）：直连多半被阻断。"
                "请在「设置 → 常规 → 网络代理」里填写代理地址（例如 http://127.0.0.1:7897），"
                "或让加速器把本程序也加入加速")
    if isinstance(exc, requests.exceptions.SSLError):
        return "TLS 证书校验失败：若使用代理或加速器，请确认其证书已被系统信任"
    if isinstance(exc, requests.exceptions.Timeout):
        return "连接 Steam 超时（网络不通或代理不可用）"
    return f"网络请求失败（{type(exc).__name__}）"


class SteamMarketClient:
    def __init__(self, session: requests.Session | None = None, retries: int = 2,
                 cancel: threading.Event | None = None, proxy: str = ""):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "SteamMarketSentinel/0.2 (+personal price monitoring)",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.8",
        })
        self.retries = retries
        # The monitor hands its stop event in, so a shutdown does not have to wait
        # out the backoff sleeps below (nor start one more request).
        self.cancel = cancel
        apply_proxy(self.session, proxy)

    def _cancelled(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()

    def _sleep(self, seconds: float) -> None:
        """Backoff that a cancel cuts short. Only the request already in flight
        still has to reach its own socket timeout; everything after it is skipped."""
        if self.cancel is not None:
            if self.cancel.wait(seconds):
                raise SteamAPIError("请求已取消（程序正在退出）")
        else:
            time.sleep(seconds)

    def _request(self, url: str, params: dict | None = None) -> requests.Response:
        """Shared retry/backoff for every undocumented Steam endpoint.

        Messages here are shown verbatim in the UI, so they carry no URL and no
        ``requests`` internals — just what happened. 4xx is never retried: those
        are permanent answers from Steam, not transient failures.
        """
        for attempt in range(self.retries + 1):
            if self._cancelled():
                raise SteamAPIError("请求已取消（程序正在退出）")
            try:
                response = self.session.get(url, params=params, timeout=(8, 15))
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise SteamAPIError(failure_message(exc)) from exc
                self._sleep(2 * (2 ** attempt))
                continue
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else None
                if wait is not None and wait <= MAX_RATE_LIMIT_RETRY_WAIT and attempt < self.retries:
                    # Steam told us exactly how long: wait that out and try once more.
                    self._sleep(max(1.0, wait))
                    continue
                # Otherwise this is the market's per-IP limit, which covers every item
                # at once and lasts minutes. Retrying here only multiplies the requests
                # that caused it, so fail fast and let the caller back the cycle off.
                raise SteamRateLimited(
                    "Steam 限流 (HTTP 429)：市场按出口 IP 限流，本程序会暂停整轮并退避重试；"
                    "建议更换代理节点，或把「每轮检查间隔」调大",
                    suggested_wait=wait)
            if response.status_code in (502, 503, 504) and attempt < self.retries:
                self._sleep(2 * (2 ** attempt) + random.uniform(0, 0.5))
                continue
            if response.status_code >= 400:
                raise SteamAPIError(f"Steam 返回 HTTP {response.status_code}", response.status_code)
            return response
        raise SteamAPIError("Steam 请求失败")

    def _get(self, route: str, params: dict) -> dict:
        try:
            data = self._request(f"{ROOT}/{route}", params).json()
        except ValueError as exc:
            raise SteamAPIError("Steam 返回了非 JSON 数据") from exc
        if not isinstance(data, dict):
            raise SteamAPIError("Steam 返回了非 JSON 对象")
        return data

    def price(self, appid: int, hash_name: str, currency: str) -> tuple[int, str]:
        currency_id, country, _ = CURRENCIES[currency]
        data = self._get("priceoverview/", {
            "appid": appid, "market_hash_name": hash_name,
            "currency": currency_id, "country": country,
        })
        if not data.get("success"):
            raise SteamAPIError("Steam 未返回有效价格（可能暂无在售或当前不可用）")
        cents = parse_price_cents(data.get("lowest_price"))
        if cents is None:
            raise SteamAPIError("Steam 暂无最低在售价格")
        return cents, str(data.get("volume") or "")

    def search(self, query: str, appid: int = 0, limit: int = 30,
               currency: str = "CNY") -> list[MarketItem]:
        """Search, up to a few result pages; count may be capped by Steam."""
        query = query.strip()
        if not query:
            return []
        result: list[MarketItem] = []
        start = 0
        while len(result) < limit:
            currency_id, country, _ = CURRENCIES[currency]
            params = {"query": query, "start": start, "count": min(20, limit-len(result)),
                      "norender": 1, "currency": currency_id, "country": country}
            if appid > 0:
                params["appid"] = appid
            payload = self._get("search/render/", params)
            if not payload.get("success"):
                raise SteamAPIError("Steam 搜索失败")
            entries = payload.get("results") or []
            if not isinstance(entries, list):
                raise SteamAPIError("Steam 搜索响应格式改变")
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                asset = entry.get("asset_description") or {}
                try:
                    asset_appid = int(asset.get("appid") or entry.get("appid") or appid)
                except (ValueError, TypeError):
                    continue
                name = str(entry.get("hash_name") or asset.get("market_hash_name") or "")
                if asset_appid <= 0 or not name:
                    continue
                icon = str(asset.get("icon_url") or "")
                image_url = IMAGE_PREFIX + quote(icon, safe="-_/") + "/128fx128f" if icon else ""
                result.append(MarketItem(asset_appid, name, str(entry.get("name") or name),
                                         str(entry.get("app_name") or ""), image_url,
                                         str(entry.get("sell_price_text") or "")))
                if len(result) == limit:
                    break
            received = len(entries)
            if received == 0:
                break
            start += received  # Steam may ignore requested count on anonymous clients.
            total = int(payload.get("total_count") or 0)
            if start >= total:
                break
            self._sleep(1.5)
        return result

    def icon_url(self, appid: int, hash_name: str) -> str:
        """The item's thumbnail URL, read from its public listing page.

        One anonymous request, only made for items that have no image yet (those
        added by pasting a link; the search path already returns icons).
        """
        html = self._request(f"{ROOT}/listings/{appid}/{quote(hash_name, safe='')}").text
        url = parse_icon_url(html)
        if not url:
            raise SteamAPIError("Steam 商品页面未提供商品图片")
        return url

    def official_history(self, appid: int, hash_name: str) -> tuple[list[tuple[int, float]], str]:
        """Steam median *sales* history, not the current lowest ask.

        **Requires a logged-in session.** Verified: the identical request returns
        HTTP 400 with body ``[]`` while anonymous — from this client *and* from a
        browser console on the item page — and returns real data the moment the
        browser is logged in. The graph is also *not* embedded in the listing page
        HTML (the page is client-rendered now).

        This app never holds credentials, so this must **not** be called from a
        user-facing path; the UI deliberately does not, and uses the browser
        import instead (``parse_imported_history`` + "导入官方历史", persisted into
        the display-only ``official_prices`` table). It is kept because it is the
        documented client-side shape of the endpoint, and its error mapping is
        covered by tests, for any future authenticated flow. Caches nothing and
        never feeds the alert rules.
        """
        try:
            payload = self._get("pricehistory/", {"appid": appid, "market_hash_name": hash_name})
        except SteamAPIError as exc:
            if exc.status in (400, 401, 403):
                raise SteamAPIError("Steam 拒绝向未登录的请求提供成交历史（该图表需要浏览器会话）") from exc
            raise
        prices = payload.get("prices") or []
        if not payload.get("success") or not prices:
            raise SteamAPIError("Steam 未返回该商品的成交历史（可能暂无成交或需要登录）")
        history = parse_history_entries(prices)
        if not history:
            raise SteamAPIError("Steam 成交历史格式无法解析")
        currency_label = str(payload.get("price_prefix") or "") + "…" + str(payload.get("price_suffix") or "")
        return history, currency_label
