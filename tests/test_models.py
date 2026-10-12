from steam_market_sentinel.models import parse_price_cents, parse_listing_url, cents_text


def test_price_parser_locales():
    assert parse_price_cents("¥1,234.56") == 123456
    assert parse_price_cents("1.234,56 €") == 123456
    assert parse_price_cents("$12.34") == 1234
    assert parse_price_cents("0,12€") == 12
    assert parse_price_cents("€1.234") == 123400
    assert parse_price_cents("￥1,234") == 123400
    assert parse_price_cents("12,34") == 1234
    assert parse_price_cents("—") is None
    assert parse_price_cents("-10") is None
    assert cents_text(2450, "CNY") == "¥24.50"


def test_listings_are_safely_parsed():
    url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
    assert parse_listing_url(url) == (730, "AK-47 | Redline (Field-Tested)")
    assert parse_listing_url("https://fake-steamcommunity.com/market/listings/730/A") is None
    assert parse_listing_url("javascript:alert(1)") is None
