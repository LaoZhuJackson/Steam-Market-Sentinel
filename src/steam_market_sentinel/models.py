"""Domain models and invariants. Money is represented in integer minor units."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from urllib.parse import unquote, urlsplit


CURRENCIES = {
    "CNY": (23, "CN", "¥"),
    "USD": (1, "US", "$"),
    "EUR": (3, "DE", "€"),
}


@dataclass(frozen=True)
class MarketItem:
    appid: int
    hash_name: str
    display_name: str
    app_name: str = ""
    image_url: str = ""
    price_text: str = ""


@dataclass(frozen=True)
class Alert:
    item_id: int
    appid: int
    hash_name: str
    title: str
    rule: str  # target | new_low | drop
    price_cents: int
    comparison_cents: int
    currency: str


def parse_price_cents(value: str | int | float | None) -> int | None:
    """Parse Steam's localized price strings; reject blanks/non-finite/negative values."""
    if value is None:
        return None
    raw = str(value).replace("\u00a0", "").replace("\u202f", "").strip()
    raw = re.sub(r"[^\d.,-]", "", raw)
    if not raw or raw.startswith("-"):
        return None
    if "," in raw and "." in raw:
        decimal_sep = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        raw = raw.replace(thousands, "").replace(decimal_sep, ".")
    elif "," in raw or "." in raw:
        sep = "," if "," in raw else "."
        parts = raw.split(sep)
        if len(parts) > 2:
            # "1,234,567" (or "1.234.567")
            if all(len(p) == 3 for p in parts[1:]):
                raw = "".join(parts)
            elif all(len(p) == 3 for p in parts[1:-1]) and len(parts[-1]) <= 2:
                raw = "".join(parts[:-1]) + "." + parts[-1]
            else:
                return None
        elif len(parts) == 2:
            if len(parts[1]) == 3 and len(parts[0]) >= 1:
                raw = "".join(parts)
            else:
                raw = parts[0] + "." + parts[1]
    try:
        cents = int((Decimal(raw) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None
    return cents if cents >= 0 else None


def cents_text(cents: int | None, currency: str = "CNY") -> str:
    if cents is None:
        return "—"
    symbol = CURRENCIES[currency][2]
    return f"{symbol}{cents / 100:,.2f}"


def from_amount(amount: float) -> int:
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def listing_url(appid: int, name: str) -> str:
    from urllib.parse import quote
    return f"https://steamcommunity.com/market/listings/{appid}/{quote(name, safe='')}"


def parse_listing_url(value: str) -> tuple[int, str] | None:
    """Accept official Steam market listing links only, preventing URL confusion."""
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme != "https" or parsed.netloc.lower() != "steamcommunity.com":
            return None
        match = re.fullmatch(r"/market/listings/(\d+)/([^/]+)/?", parsed.path)
        if not match:
            return None
        appid, name = int(match.group(1)), unquote(match.group(2))
        if appid <= 0 or not name:
            return None
        return appid, name
    except (ValueError, TypeError):
        return None
