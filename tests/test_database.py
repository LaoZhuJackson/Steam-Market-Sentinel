import json
import sqlite3

import pytest

from steam_market_sentinel.database import Database
from steam_market_sentinel.models import MarketItem


def sample(db):
    return db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)


def test_alert_crossing_and_observed_low(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    item_id = sample(db)
    # First sample creates an observed baseline; should NOT trigger "historical low".
    assert db.record_snapshot(item_id, "CNY", 1100, checked_at=10000) == []
    alerts = db.record_snapshot(item_id, "CNY", 850, checked_at=10001)
    assert {a.rule for a in alerts} == {"new_low", "target"}
    assert db.record_snapshot(item_id, "CNY", 800, checked_at=10002)[0].rule == "new_low"
    assert db.record_snapshot(item_id, "CNY", 800, checked_at=10003) == []
    assert db.record_snapshot(item_id, "CNY", 1000, checked_at=10004) == []
    assert {a.rule for a in db.record_snapshot(item_id, "CNY", 880, checked_at=10005)} == {"target"}
    row = db.get_item(item_id, "CNY")
    assert row["low_cents"] == 800
    assert row["current_cents"] == 880


def test_editing_threshold_resets_alert_state(tmp_path):
    db = Database(tmp_path / "data.sqlite")
    item = sample(db)
    db.record_snapshot(item, "CNY", 850, checked_at=100)
    db.update_item(item, 950, True, True)
    assert [e.rule for e in db.record_snapshot(item, "CNY", 850, checked_at=101)] == ["target"]


def test_currency_snapshots_isolated(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    item = sample(db)
    db.record_snapshot(item, "CNY", 1200, checked_at=1)
    db.record_snapshot(item, "USD", 200, checked_at=2)
    assert db.get_item(item, "CNY")["current_cents"] == 1200
    assert db.get_item(item, "USD")["low_cents"] == 200
    assert len(db.get_history(item, "USD")) == 1


def test_no_extra_unchanged_samples(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    item = sample(db)
    db.record_snapshot(item, "CNY", 1000, checked_at=1)
    db.record_snapshot(item, "CNY", 1000, checked_at=2)
    assert len(db.get_history(item, "CNY")) == 1
    db.record_snapshot(item, "CNY", 1000, checked_at=21602)
    assert len(db.get_history(item, "CNY")) == 2


def test_import_export_and_duplicates(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    sample(db)
    with pytest.raises(sqlite3.IntegrityError):
        sample(db)
    dest = tmp_path / "watchlist.json"
    assert db.export_watchlist(dest) == 1
    data = json.loads(dest.read_text())
    assert data[0]["hash_name"] == "Revolution Case"
    other = Database(tmp_path / "other.sqlite")
    assert other.import_watchlist(dest) == (1, 0)
    assert other.import_watchlist(dest) == (0, 1)


def test_snapshot_ignored_after_removal_and_paused(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    item = sample(db)
    db.update_item(item, None, True, False)
    assert db.record_snapshot(item, "CNY", 500) == []
    assert db.get_history(item, "CNY") == []
    db.delete_item(item)
    assert db.record_snapshot(item, "CNY", 200) == []


def test_unchanged_poll_advances_last_checked_without_a_snapshot(tmp_path):
    # The panel shows 最后检查 from last_checked_at: on an unchanged price a poll
    # still happened even though the 6-hour/on-change rule stores no new sample.
    db = Database(tmp_path / "db.sqlite")
    item = sample(db)
    db.record_snapshot(item, "CNY", 1000, checked_at=1000)
    db.record_snapshot(item, "CNY", 1000, checked_at=1900)  # 15 minutes later
    row = db.get_item(item, "CNY")
    assert row["last_checked_at"] == 1900
    assert row["currency_checked_at"] == 1000
    assert len(db.get_history(item, "CNY")) == 1


def test_get_item_matches_list_items_shape(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    item = sample(db)
    db.record_snapshot(item, "CNY", 900, checked_at=10)
    row = db.get_item(item, "CNY")
    listed = db.list_items("CNY")[0]
    # The single-row lookup must stay interchangeable with the list query.
    assert set(row) == set(listed)
    assert (row["current_cents"], row["low_cents"]) == (900, 900)
    assert (listed["current_cents"], listed["low_cents"]) == (900, 900)
    assert db.get_item(9999, "CNY") is None


def test_imported_points_merge_into_the_observed_series(tmp_path):
    # By explicit user choice the imported official history is appended to the local
    # series: one line, one 史低 — even though the two are different metrics.
    db = Database(tmp_path / "db.sqlite")
    item = sample(db)  # target 900
    db.record_snapshot(item, "CNY", 1100, checked_at=1000)
    assert db.save_official_history(item, [(500, 50), (600, 70)], "¥…", "CNY") == 2

    assert db.get_history(item, "CNY") == [(500, 50), (600, 70), (1000, 1100)]
    assert db.merged_point_counts(item, "CNY") == (1, 2)
    row = db.get_item(item, "CNY")
    assert row["low_cents"] == 50           # the imported dip is the low now
    assert row["current_cents"] == 1100     # only local samples drive the current price
    assert row["currency_checked_at"] == 1000
    # The alert baseline is the merged low, so a price above it is not a "new low".
    assert db.record_snapshot(item, "CNY", 1000, checked_at=1001) == []

    dest = tmp_path / "prices.csv"
    db.export_history_csv(dest, "CNY")
    lines = dest.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].endswith("source")  # the mix is never silent
    assert sum(",official" in line for line in lines) == 2
    assert sum(",local" in line for line in lines) == 2  # both polls above wrote a sample


def test_drop_alert_survives_an_imported_history(tmp_path):
    # Why this rule exists: an imported official series can put 史低 out of
    # reach so "new low" never fires again. A drop is measured against the previous
    # *local* sample, so it keeps working regardless.
    db = Database(tmp_path / "db.sqlite")
    item = sample(db)  # target 900
    db.record_snapshot(item, "CNY", 5000, checked_at=1000)
    db.save_official_history(item, [(500, 50)], "¥…", "CNY")  # a deep historical dip

    # Cheaper than last time, but nowhere near the merged low of 50.
    alerts = db.record_snapshot(item, "CNY", 4800, checked_at=1001, drop_alerts=True)
    assert [alert.rule for alert in alerts] == ["drop"]
    assert alerts[0].comparison_cents == 5000

    assert db.record_snapshot(item, "CNY", 4700, checked_at=1002) == []  # off by default
    fresh = db.add_item(MarketItem(570, "Inscribed Gem", "Inscribed Gem"))
    # The first sample is a baseline: there is nothing to have dropped from.
    assert db.record_snapshot(fresh, "CNY", 100, checked_at=1003, drop_alerts=True) == []


def test_drop_alert_composes_with_the_other_rules(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    item = sample(db)  # target 900
    db.record_snapshot(item, "CNY", 1000, checked_at=1000)
    alerts = db.record_snapshot(item, "CNY", 850, checked_at=1001, drop_alerts=True)
    assert [alert.rule for alert in alerts] == ["target", "new_low", "drop"]


def test_imported_points_do_not_merge_across_currencies(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    item = sample(db)
    db.record_snapshot(item, "CNY", 1100, checked_at=1000)
    db.save_official_history(item, [(500, 50)], "S$…", "SGD")  # a different currency

    assert db.get_history(item, "CNY") == [(1000, 1100)]  # never mixed in
    assert db.get_item(item, "CNY")["low_cents"] == 1100
    assert db.merged_point_counts(item, "CNY") == (1, 0)
    assert db.merged_point_counts(item, "SGD") == (0, 1)
    assert db.official_series(item)[0] == [(500, 50)]  # still viewable in official mode


def test_official_history_reimport_replaces_wholesale(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    item = sample(db)
    db.record_snapshot(item, "CNY", 1000, checked_at=1)
    db.save_official_history(item, [(10, 100), (20, 200)], "¥…", "CNY")
    db.save_official_history(item, [(10, 111), (20, 222)], "$…", "CNY")

    points, label, imported_at = db.official_series(item)
    assert points == [(10, 111), (20, 222)]  # replaced, not appended
    assert label == "$…"
    assert imported_at is not None
    assert db.merged_point_counts(item, "CNY") == (1, 2)  # still exactly two


def test_official_history_dedupes_and_cascades(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    item = sample(db)
    assert db.save_official_history(item, [(10, 100), (10, 777)], "") == 1  # last wins
    assert db.official_series(item)[0] == [(10, 777)]
    with pytest.raises(sqlite3.IntegrityError):
        db.save_official_history(9999, [(1, 1)], "")  # unknown item, no orphans
    db.delete_item(item)
    assert db.official_series(item)[0] == []


def test_official_prices_gains_the_currency_column(tmp_path):
    # A database created by the first release of the import feature has no currency
    # column. The guarded ALTER must add it and keep the already-imported points.
    path = tmp_path / "old.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.executescript("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY, appid INTEGER NOT NULL, hash_name TEXT NOT NULL,
            display_name TEXT NOT NULL, app_name TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '', target_cents INTEGER,
            new_low_enabled INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL, last_checked_at INTEGER, last_error TEXT,
            UNIQUE(appid, hash_name));
        CREATE TABLE official_prices (
            item_id INTEGER NOT NULL, recorded_at INTEGER NOT NULL, price_cents INTEGER NOT NULL,
            currency_label TEXT NOT NULL DEFAULT '', imported_at INTEGER NOT NULL,
            PRIMARY KEY(item_id, recorded_at));
        INSERT INTO items(appid, hash_name, display_name, created_at)
            VALUES(730, 'Old', 'Old', 5);
        INSERT INTO official_prices(item_id, recorded_at, price_cents, currency_label, imported_at)
            VALUES(1, 700, 4321, '¥…', 800);
    """)
    legacy.commit()
    legacy.close()

    db = Database(path)
    assert db.official_series(1) == ([(700, 4321)], "¥…", 800)  # import survived
    # Untagged legacy rows stay out of every currency's series — a silent mix is
    # worse than no merge.
    assert db.merged_point_counts(1, "CNY") == (0, 0)


def test_existing_database_gains_the_official_table(tmp_path):
    path = tmp_path / "old.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.executescript("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY, appid INTEGER NOT NULL, hash_name TEXT NOT NULL,
            display_name TEXT NOT NULL, app_name TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '', target_cents INTEGER,
            new_low_enabled INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL, last_checked_at INTEGER, last_error TEXT,
            UNIQUE(appid, hash_name));
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            currency TEXT NOT NULL, recorded_at INTEGER NOT NULL, price_cents INTEGER NOT NULL,
            volume TEXT, UNIQUE(item_id, currency, recorded_at));
        INSERT INTO items(appid, hash_name, display_name, created_at)
            VALUES(730, 'Old Item', 'Old Item', 5);
    """)
    legacy.commit()
    legacy.close()

    db = Database(path)  # create-only schema upgrade must not disturb old rows
    assert [row["hash_name"] for row in db.list_items("CNY")] == ["Old Item"]
    assert db.official_series(1) == ([], "", None)


def test_import_continues_after_duplicate_row(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    sample(db)  # 730 / Revolution Case is already watched
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps([
        {"appid": 730, "hash_name": "Revolution Case"},  # duplicate -> skipped
        {"appid": 570, "hash_name": "Inscribed Gem"},    # must still be added
    ]), encoding="utf-8")
    assert db.import_watchlist(path) == (1, 1)
    assert {row["hash_name"] for row in db.list_items("CNY")} == {
        "Revolution Case", "Inscribed Gem"}
