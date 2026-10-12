from steam_market_sentinel.config import Settings


def test_settings_round_trip_includes_the_view_state(tmp_path):
    path = tmp_path / "settings.json"
    Settings(currency="USD", selected_item=7, drop_alert_enabled=True).save(path)
    loaded = Settings.load(path)
    assert (loaded.currency, loaded.selected_item) == ("USD", 7)
    assert loaded.drop_alert_enabled is True


def test_settings_tolerate_unknown_keys_and_bad_values(tmp_path):
    # A hand-edited or older file must not reset everything to defaults. Real files
    # still carry "history_mode", a key this version no longer has.
    path = tmp_path / "settings.json"
    path.write_text('{"currency": "EUR", "history_mode": 9, "selected_item": "nope"}',
                    encoding="utf-8")
    loaded = Settings.load(path)
    assert loaded.currency == "EUR"
    assert loaded.selected_item is None
