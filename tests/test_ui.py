"""Qt startup smoke tests; skipped only when PySide6 is not installed."""
import os
import time

import pytest

# Skip, rather than fail collection, when Qt is installed but its shared libraries
# are missing (import PySide6 succeeds there; importing the sub-modules is what
# dies, so importorskip("PySide6") alone would leave a collection error behind —
# which is exactly how the Linux CI job was failing, with pytest exit code 2).
pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("PySide6.QtCharts")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QMessageBox, QPushButton,
)

from steam_market_sentinel import ui as ui_module

from steam_market_sentinel.config import Settings
from steam_market_sentinel.database import Database
from steam_market_sentinel.models import MarketItem
from steam_market_sentinel.steam_api import SteamMarketClient
from steam_market_sentinel.style import PALETTE, STYLE, apply_theme
from steam_market_sentinel.ui import (
    COL_CHECK, COL_LOW, COL_NAME, COL_REMOVE, ImportHistoryDialog, MainWindow,
    SearchAddDialog, history_snippet, point_tooltip_text,
)


def _fake_import_dialog(monkeypatch, points, label="¥…"):
    """Stand in for ImportHistoryDialog so the import path can be driven directly."""
    class FakeDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def parsed(self):
            return list(points), label

    monkeypatch.setattr(ui_module, "ImportHistoryDialog", FakeDialog)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test may reach Steam.

    The UI now lazily fetches a missing thumbnail, so merely selecting an item can
    start a request. Blocking every client entry point keeps the suite offline (and
    turns an accidental call into a loud failure rather than a slow, flaky one).
    """
    def explode(*_args, **_kwargs):
        raise AssertionError("测试不应发起网络请求")

    for method in ("icon_url", "search", "price", "official_history"):
        monkeypatch.setattr(SteamMarketClient, method, explode)
    # Images are fetched with requests rather than Qt's network stack, so block that
    # entry point as well.
    monkeypatch.setattr(ui_module, "_fetch_image_bytes", explode)


def _row_of(window, item_id: int) -> int:
    """The list is sorted id DESC, so never assume a row number in tests."""
    for row in range(window.table.rowCount()):
        if window.table.item(row, COL_NAME).data(Qt.UserRole) == item_id:
            return row
    raise AssertionError(f"item {item_id} is not in the watchlist")


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
    _fake_import_dialog(monkeypatch, [(1_700_000_000, 500)], "S$…")  # SGD, setting is CNY
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


def test_image_bytes_reach_the_label_and_the_cache(monkeypatch, tmp_path):
    # The image pipeline: URL -> requests (never Qt's network stack) -> QPixmap ->
    # cache + label. Qt's own network crash cannot be unit-tested, so at least lock
    # the wiring so a regression shows up here.
    from PySide6.QtGui import QColor, QPixmap

    # Write the seed image to a file rather than QBuffer(QByteArray()): that
    # constructor keeps a pointer to a temporary and crashes on use.
    seed = tmp_path / "seed.png"
    source = QPixmap(4, 4)
    source.fill(QColor("#67e3be"))
    assert source.save(str(seed), "PNG")
    png = seed.read_bytes()

    host = "https://community.steamstatic.com/economy/image/x"
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    db.set_item_image(item, host)
    monkeypatch.setattr(ui_module, "_fetch_image_bytes", lambda _url: png)

    app, window = _window(tmp_path, db)
    for _ in range(100):
        app.processEvents()
        if window._image_cache:
            break
        time.sleep(0.02)
    assert host in window._image_cache          # fetched, decoded and cached
    assert not window.image.pixmap().isNull()   # and shown in the panel
    window.close()


def test_importing_history_updates_the_watchlist_low(monkeypatch, tmp_path):
    # The reported bug: the chart changed but the watchlist kept the pre-import 史低.
    db = Database(tmp_path / "data.sqlite")
    db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    db.record_snapshot(1, "CNY", 5000, checked_at=1_700_000_000)
    app, window = _window(tmp_path, db)
    assert window.table.item(0, COL_LOW).text() == "¥50.00"

    _fake_import_dialog(monkeypatch, [(1_699_000_000, 500)])
    window.import_official_history()
    app.processEvents()
    assert window.table.item(0, COL_LOW).text() == "¥5.00"  # the merged low
    assert "官方成交中位价" in window.chart.chart().title()
    window.close()


def test_tick_state_survives_a_poll_refresh(tmp_path):
    # The list is rebuilt on every poll (twice a second while monitoring), so the
    # ticks have to live outside the table items.
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    app, window = _window(tmp_path, db)
    window.table.item(0, COL_CHECK).setCheckState(Qt.Checked)
    app.processEvents()
    assert window._checked == {item}

    window.refresh_table()
    assert window.table.item(0, COL_CHECK).checkState() == Qt.Checked
    assert window._checked == {item}
    window.close()


def test_select_all_and_clear_all(tmp_path):
    db = Database(tmp_path / "data.sqlite")
    first = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    second = db.add_item(MarketItem(570, "Inscribed Gem", "Inscribed Gem"), None)
    app, window = _window(tmp_path, db)
    window.check_all_items()
    assert window._checked == {first, second}
    assert "已选 2" in window.count_label.text()

    window.uncheck_all_items()
    assert window._checked == set()
    assert "已选" not in window.count_label.text()
    window.close()


def test_each_row_has_a_remove_button(monkeypatch, tmp_path):
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    app, window = _window(tmp_path, db)
    remove = window.table.cellWidget(0, COL_REMOVE)
    assert isinstance(remove, QPushButton) and remove.text() == "×"

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    remove.click()
    app.processEvents()
    assert db.get_item(item, "CNY") is None
    assert window.table.rowCount() == 0
    window.close()


def test_chart_range_narrows_the_visible_window(tmp_path):
    # An old spike used to flatten everything recent; the range keeps the window tight.
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    now = int(time.time())
    db.record_snapshot(item, "CNY", 5000, checked_at=now - 40 * 86400)
    db.record_snapshot(item, "CNY", 1000, checked_at=now - 2 * 86400)
    app, window = _window(tmp_path, db)
    assert window.chart.chart().series()[0].count() == 2  # 全部

    window.range_combo.setCurrentIndex(window._range_index(7))
    app.processEvents()
    assert window.chart.chart().series()[0].count() == 1  # only the recent sample
    assert "最近 7 天内的" in window.history_hint.text()
    assert window.settings.chart_range_days == 7
    window.close()


def test_chart_range_says_so_when_the_window_is_empty(tmp_path):
    # Picking a narrow range on an old item must not look like the data vanished.
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    db.record_snapshot(item, "CNY", 1000, checked_at=int(time.time()) - 40 * 86400)
    app, window = _window(tmp_path, db)
    window.range_combo.setCurrentIndex(window._range_index(7))
    app.processEvents()
    assert "所选时间范围内没有数据" in window.chart.chart().title()
    assert "全部" in window.history_hint.text()
    window.close()


def test_rule_edits_are_saved_immediately(tmp_path):
    # The reported bug: setting a target, switching item and coming back showed it
    # unset, because nothing was written until the (removed) Save button was pressed.
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"))  # no target
    db.record_snapshot(item, "CNY", 1200, checked_at=1_700_000_000)
    app, window = _window(tmp_path, db)

    window.target_check.setChecked(True)  # no Save button involved
    app.processEvents()
    assert db.get_item(item, "CNY")["target_cents"] == 1200  # seeded from the price
    assert window.target_metric.text() == "¥12.00"           # and the card updated

    other = db.add_item(MarketItem(570, "Inscribed Gem", "Inscribed Gem"))
    window.refresh_table()
    window.table.selectRow(_row_of(window, other))
    app.processEvents()
    window.table.selectRow(_row_of(window, item))  # come back to it
    app.processEvents()
    assert window.target_check.isChecked()
    assert window.target_edit.value() == 12.0
    window.close()


def test_selecting_an_item_does_not_rewrite_its_rules(tmp_path):
    # Populating the widgets must not look like an edit: an item without a target
    # must not silently gain one just by being selected.
    db = Database(tmp_path / "data.sqlite")
    first = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    second = db.add_item(MarketItem(570, "Inscribed Gem", "Inscribed Gem"), None)
    app, window = _window(tmp_path, db)
    window.table.selectRow(1)
    app.processEvents()
    assert db.get_item(second, "CNY")["target_cents"] is None
    assert db.get_item(first, "CNY")["target_cents"] == 900
    window.close()


def test_rename_updates_the_list_and_falls_back_when_cleared(tmp_path):
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    app, window = _window(tmp_path, db)
    window.name_edit.setText("革命武器箱")
    window.name_edit.editingFinished.emit()  # pressing Enter / leaving the field
    app.processEvents()
    assert db.get_item(item, "CNY")["display_name"] == "革命武器箱"
    assert window.table.item(0, COL_NAME).text() == "革命武器箱"

    window.name_edit.setText("")  # clearing falls back to the market name
    window.name_edit.editingFinished.emit()
    app.processEvents()
    assert db.get_item(item, "CNY")["display_name"] == "Revolution Case"
    window.close()


def test_thumbnails_survive_a_restart(monkeypatch, tmp_path):
    # In-session switches already reused the memory cache; this locks the on-disk
    # cache, so a fresh session does not re-download what it already has.
    from PySide6.QtGui import QColor, QPixmap

    seed = tmp_path / "seed.png"
    source = QPixmap(4, 4)
    source.fill(QColor("#67e3be"))
    assert source.save(str(seed), "PNG")
    png = seed.read_bytes()

    host = "https://community.steamstatic.com/economy/image/z"
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    db.set_item_image(item, host)
    calls = []
    monkeypatch.setattr(ui_module, "_fetch_image_bytes",
                        lambda url: (calls.append(url), png)[1])

    app, first = _window(tmp_path, db)
    for _ in range(100):
        app.processEvents()
        if first._image_cache:
            break
        time.sleep(0.02)
    assert calls == [host]
    first.close()

    calls.clear()
    app2, second = _window(tmp_path, db)  # fresh window, empty memory cache
    for _ in range(100):
        app2.processEvents()
        if not second.image.pixmap().isNull():
            break
        time.sleep(0.02)
    assert calls == []  # served from disk
    assert not second.image.pixmap().isNull()
    second.close()


def test_monitoring_failures_and_recoveries_are_announced(tmp_path):
    worker = ui_module.MonitorWorker(tmp_path / "db.sqlite3",
                                     Settings(auto_start=False, error_alert_enabled=False))
    seen = []
    worker.notice.connect(lambda subject, body: seen.append((subject, body)))
    worker._notify("革命武器箱", "Revolution Case", RuntimeError("连接超时"))
    worker._notify("革命武器箱", "Revolution Case", None)
    assert "异常" in seen[0][0] and "连接超时" in seen[0][1]
    assert "恢复" in seen[1][0]


def test_the_import_dialog_offers_the_item_page():
    # Opening the item page belongs in the import flow: the snippet has to run there.
    QApplication.instance() or QApplication([])
    dialog = ImportHistoryDialog(None, "Revolution Case", "CNY",
                                 "https://steamcommunity.com/market/listings/730/Revolution%20Case")
    labels = [widget.text() for widget in dialog.findChildren(QPushButton)]
    assert "在 Steam 中查看" in labels
    assert "复制浏览器脚本" in labels
    dialog.close()


def test_thumbnail_fetch_is_attempted_only_once_per_item(tmp_path):
    db = Database(tmp_path / "data.sqlite")
    item = db.add_item(MarketItem(730, "Revolution Case", "Revolution Case"), 900)
    app, window = _window(tmp_path, db)
    assert item in window._thumb_tried  # attempted for the imageless item
    window.ensure_thumbnail({"id": item, "appid": 730, "hash_name": "Revolution Case",
                             "image_url": ""})
    assert len(window._thumb_tried) == 1  # not retried on every selection
    window.close()


def test_add_dialog_can_set_a_display_name():
    QApplication.instance() or QApplication([])
    dialog = SearchAddDialog()
    dialog.query.setText("https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline")
    dialog.name_edit.setText("红纹 AK")
    item, _target = dialog.chosen()
    assert item.display_name == "红纹 AK"
    assert item.hash_name == "AK-47 | Redline"  # the market name is untouched
    dialog.close()


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
