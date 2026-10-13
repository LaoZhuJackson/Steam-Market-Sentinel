"""Steam's undocumented public endpoints. No account credentials required."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import random
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


class SteamMarketClient:
    def __init__(self, session: requests.Session | None = None, retries: int = 2):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "SteamMarketSentinel/0.2 (+personal price monitoring)",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.8",
        })
        self.retries = retries

    def _request(self, url: str, params: dict | None = None) -> requests.Response:
        """Shared retry/backoff for every undocumented Steam endpoint.

        Messages here are shown verbatim in the UI, so they carry no URL and no
        ``requests`` internals — just what happened. 4xx is never retried: those
        are permanent answers from Steam, not transient failures.
        """
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=(8, 15))
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise SteamAPIError(f"网络请求失败（{type(exc).__name__}）") from exc
                time.sleep(2 * (2 ** attempt))
                continue
            if response.status_code == 429:
                if attempt >= self.retries:
                    raise SteamAPIError("Steam 限流 (HTTP 429)，请延长轮询间隔后重试", 429)
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 6 * (2 ** attempt)
                time.sleep(max(3, min(60, delay)))
                continue
            if response.status_code in (502, 503, 504) and attempt < self.retries:
                time.sleep(2 * (2 ** attempt) + random.uniform(0, 0.5))
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
            time.sleep(1.5)
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
