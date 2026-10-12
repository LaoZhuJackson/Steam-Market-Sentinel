"""Qt startup smoke tests; skipped only when PySide6 is not installed."""
import os

import pytest

PySide6 = pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox

from steam_market_sentinel import ui as ui_module

from steam_market_sentinel.config import Settings
from steam_market_sentinel.database import Database
from steam_market_sentinel.models import MarketItem
from steam_market_sentinel.steam_api import SteamMarketClient
from steam_market_sentinel.style import PALETTE, STYLE, apply_theme
from steam_market_sentinel.ui import (
    MainWindow, SearchAddDialog, history_snippet, point_tooltip_text,
)


def _window(tmp_path, db):
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    window = MainWindow(db, Settings(auto_start=False, minimize_to_tray=False),
                        tmp_path / "settings.json")
    window.show()
    window.table.selectRow(0)
    app.processEvents()
    return app, window


def test_stylesheet_colours_are_valid():
    # Qt ignores malformed declarations silently, so a doubled '#' (PALETTE
    # values already carry one) would disable the whole theme with no error.
    assert "##" not in STYLE
    assert PALETTE["accent"] in STYLE
    assert PALETTE["card"] in STYLE


def test_apply_theme_is_idempotent():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    apply_theme(app)
    assert app.styleSheet()


def test_main_window_starts_without_polling(tmp_path):
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    settings = Settings(auto_start=False, minimize_to_tray=False)
    window = MainWindow(Database(tmp_path / "data.sqlite"), settings, tmp_path / "settings.json")
    assert window.table.rowCount() == 0
    assert window.run_button.text().startswith("▶")
    window.close()
    app.processEvents()


def test_history_snippet_asks_for_the_configured_currency():
    assert "currency=23&country=CN" in history_snippet("CNY")
    assert "currency=1&country=US" in history_snippet("USD")
    # The placeholder must be gone, and the JS template literals must survive
    # (the snippet is a plain string precisely so ${...} is not interpolated).
    for currency in ("CNY", "USD", "EUR"):
        assert "__REQUESTED_CURRENCY__" not in history_snippet(currency)
        assert "${p[2]}" in history_snippet(currency)
    # Brackets in item names must be percent-encoded, not left raw.
    assert "!'()*" in history_snippet("CNY")


def test_point_tooltip_text_shows_time_and_price():
    text = point_tooltip_text("¥", 1_700_000_000, 12.3)
    assert text.endswith("¥12.30")
    assert len(text.splitlines()) == 2


def test_hovering_a_chart_point_does_not_crash(tmp_path):
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    db.record_snapshot(item, "CNY", 1234, checked_at=1_700_000_000)
    app, window = _window(tmp_path, db)
    point = QPointF(1_700_000_000_000.0, 12.34)  # x is msecs, y is yuan
    window._on_point_hovered(point, True, "¥")
    window._on_point_hovered(point, False, "¥")
    window.close()


def test_dense_series_drops_marker_dots(tmp_path):
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    db.save_official_history(
        item, [(1_700_000_000 + index * 3600, 1000 + index) for index in range(120)],
        "¥…", "CNY")
    app, window = _window(tmp_path, db)
    series = window.chart.chart().series()[0]
    assert series.count() == 120
    assert not series.pointsVisible()  # 120 dots would be a blob
    window.close()


def test_sparse_series_keeps_its_marker(tmp_path):
    # One point draws no line at all, so it must keep its marker to be visible.
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    db.record_snapshot(item, "CNY", 1234, checked_at=1_700_000_000)
    app, window = _window(tmp_path, db)
    series = window.chart.chart().series()[0]
    assert series.count() == 1
    assert series.pointsVisible()
    window.close()


def test_the_selected_item_survives_a_restart(tmp_path):
    # The bug this locks: the rows were stored fine, but the app reopened on the
    # first row, so an imported series looked as though it had been lost.
    settings_path = tmp_path / "settings.json"
    db = Database(tmp_path / "data.sqlite")
    older = db.add_item(MarketItem(570, "Inscribed Gem", "Inscribed Gem"), None)
    db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    db.save_official_history(older, [(1_700_000_000, 1234)], "¥…", "CNY")

    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    first = MainWindow(db, Settings(auto_start=False, minimize_to_tray=False), settings_path)
    first.table.selectRow(1)  # row 1 = the older item, which has the import
    app.processEvents()
    assert "官方成交中位价" in first.chart.chart().title()
    first.close()  # persists the view state
    app.processEvents()

    reopened = MainWindow(Database(tmp_path / "data.sqlite"),
                          Settings.load(settings_path), settings_path)
    app.processEvents()
    assert reopened.current_item_id == older
    assert "官方成交中位价" in reopened.chart.chart().title()
    reopened.close()


def test_switching_items_redraws_for_that_item(tmp_path):
    db = Database(tmp_path / "data.sqlite")
    imported = db.add_item(MarketItem(570, "Inscribed Gem", "Inscribed Gem"), None)
    db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    db.save_official_history(imported, [(1_700_000_000, 500)], "¥…", "CNY")
    app, window = _window(tmp_path, db)  # row 0 = newest = the item without an import
    assert window.chart.chart().title() == "尚无记录"

    window.table.selectRow(1)  # the item that has the import
    app.processEvents()
    assert "官方成交中位价" in window.chart.chart().title()
    assert window.chart.chart().series()[0].count() == 1
    window.close()


def test_the_chart_reports_the_merged_mix(tmp_path):
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    db.record_snapshot(item, "CNY", 1000, checked_at=1_700_000_000)
    db.save_official_history(item, [(1_699_000_000, 500)], "¥…", "CNY")
    app, window = _window(tmp_path, db)
    hint = window.history_hint.text()
    assert "本地 1" in hint and "导入的官方成交中位价 1" in hint
    # The single line really does contain both the local and the imported point.
    assert window.chart.chart().series()[0].count() == 2
    window.close()


def test_a_currency_mismatched_import_is_refused(monkeypatch, tmp_path):
    # There is one merged view, so an import in another currency has nowhere honest
    # to be shown — it must be refused rather than stored invisibly.
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    app, window = _window(tmp_path, db)

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warned.append(args))

    class FakeDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def parsed(self):
            return [(1_700_000_000, 500)], "S$…"  # SGD, while the setting is CNY

    monkeypatch.setattr(ui_module, "ImportHistoryDialog", FakeDialog)
    window.import_official_history()

    assert warned                                        # the user was told why
    assert db.official_series(item) == ([], "", None)    # and nothing was stored
    window.close()


def test_the_empty_state_points_at_the_import_button(tmp_path):
    db = Database(tmp_path / "data.sqlite")
    db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    app, window = _window(tmp_path, db)
    assert window.chart.chart().title() == "尚无记录"
    assert "导入官方历史" in window.history_hint.text()
    window.close()


def test_the_chart_never_calls_the_network(monkeypatch, tmp_path):
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    db.save_official_history(item, [(1_700_000_000, 1234)], "¥…", "CNY")

    def explode(*_args, **_kwargs):
        # Tripwire: Steam refuses this anonymously, so no user-facing path may
        # call it. If a future change reintroduces a fetch, this fires.
        raise AssertionError("图表不应联网：Steam 只对已登录会话返回该数据")

    monkeypatch.setattr(SteamMarketClient, "official_history", explode)
    app, window = _window(tmp_path, db)
    assert window.chart.chart().series()[0].count() == 1
    window.close()


def test_add_dialog_needs_a_selection_then_accepts_a_pasted_link():
    app = QApplication.instance() or QApplication([])
    dialog = SearchAddDialog()
    ok = dialog.actions.button(QDialogButtonBox.Ok)
    assert not ok.isEnabled()  # nothing picked yet: no "添加监控"
    with pytest.raises(ValueError):
        dialog.chosen()

    # Pasting a listing link resolves it outright, with no search request...
    dialog.query.setText("https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline")
    app.processEvents()
    item, target = dialog.chosen()
    assert (item.appid, item.hash_name) == (730, "AK-47 | Redline")
    assert target is None
    assert ok.isEnabled()
    # ...and it still shows up as a result row, so the list always mirrors the candidate.
    assert dialog.results.rowCount() == 1
    assert dialog.results.item(0, 0).text() == "AK-47 | Redline"
    dialog.close()
