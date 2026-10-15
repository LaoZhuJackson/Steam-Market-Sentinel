from steam_market_sentinel.config import PROXY_MODES, Settings


def test_settings_round_trip_includes_the_view_state(tmp_path):
    path = tmp_path / "settings.json"
    Settings(currency="USD", selected_item=7, drop_alert_enabled=True,
             chart_range_days=30).save(path)
    loaded = Settings.load(path)
    assert (loaded.currency, loaded.selected_item) == ("USD", 7)
    assert loaded.drop_alert_enabled is True
    assert loaded.chart_range_days == 30


def test_settings_tolerate_unknown_keys_and_bad_values(tmp_path):
    # A hand-edited or older file must not reset everything to defaults. Real files
    # still carry "history_mode", a key this version no longer has.
    path = tmp_path / "settings.json"
    path.write_text('{"currency": "EUR", "history_mode": 9, "selected_item": "nope",'
                    ' "chart_range_days": 999}', encoding="utf-8")
    loaded = Settings.load(path)
    assert loaded.currency == "EUR"
    assert loaded.selected_item is None
    assert loaded.chart_range_days == 0  # not one of the offered ranges -> 全部


def test_settings_round_trip_includes_the_proxy(tmp_path):
    path = tmp_path / "settings.json"
    Settings(proxy_mode="custom", proxy_url="http://127.0.0.1:7897").save(path)
    loaded = Settings.load(path)
    assert loaded.proxy_mode == "custom"
    assert loaded.proxy_argument() == "http://127.0.0.1:7897"


def test_proxy_settings_fall_back_instead_of_breaking_every_request(tmp_path):
    # proxy_argument() is what the HTTP layer gets: "" is requests' own behaviour
    # (OS proxy, then HTTP(S)_PROXY), "direct" turns even that off.
    assert Settings().proxy_argument() == ""
    assert Settings(proxy_mode="direct").proxy_argument() == "direct"
    assert Settings(proxy_mode="custom", proxy_url="   ").proxy_argument() == ""

    path = tmp_path / "settings.json"
    # An unknown mode and an address with no scheme are both ignored: silently
    # handing either to requests would fail every single poll.
    path.write_text('{"proxy_mode": "socks", "proxy_url": "127.0.0.1:7897"}',
                    encoding="utf-8")
    loaded = Settings.load(path)
    assert (loaded.proxy_mode, loaded.proxy_url) == ("system", "")
    assert len(PROXY_MODES) == 3
