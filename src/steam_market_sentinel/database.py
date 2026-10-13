"""Small SQLite store. Each call owns its connection; safe across UI/worker threads."""
from __future__ import annotations

from contextlib import contextmanager
import csv
import json
from pathlib import Path
import sqlite3
import time
from typing import Iterator

from .models import Alert, MarketItem

# Imported official history is a display-only series; cap it so a stray paste of
# something huge cannot fill the local database.
MAX_OFFICIAL_POINTS = 20000

# Single source of truth for the item row shape, so get_item and list_items can
# never drift apart. The four placeholders are the currency, bound in order.
_ITEM_SELECT = """
    SELECT i.*,
      (SELECT s.price_cents FROM snapshots s
       WHERE s.item_id=i.id AND s.currency=?
       ORDER BY s.recorded_at DESC, s.id DESC LIMIT 1) AS current_cents,
      (SELECT MIN(price_cents) FROM (
           SELECT s.price_cents FROM snapshots s
            WHERE s.item_id=i.id AND s.currency=?
           UNION ALL
           SELECT o.price_cents FROM official_prices o
            WHERE o.item_id=i.id AND o.currency=?
       )) AS low_cents,
      (SELECT MAX(s.recorded_at) FROM snapshots s
       WHERE s.item_id=i.id AND s.currency=?) AS currency_checked_at
    FROM items i
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY,
                    appid INTEGER NOT NULL CHECK (appid > 0),
                    hash_name TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    app_name TEXT NOT NULL DEFAULT '',
                    image_url TEXT NOT NULL DEFAULT '',
                    target_cents INTEGER CHECK (target_cents >= 0),
                    new_low_enabled INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    last_checked_at INTEGER,
                    last_error TEXT,
                    UNIQUE(appid, hash_name)
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY,
                    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                    currency TEXT NOT NULL,
                    recorded_at INTEGER NOT NULL,
                    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
                    volume TEXT,
                    UNIQUE(item_id, currency, recorded_at)
                );
                CREATE INDEX IF NOT EXISTS idx_snapshots_item_currency
                    ON snapshots(item_id, currency, recorded_at);
                CREATE TABLE IF NOT EXISTS alert_state (
                    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                    currency TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(item_id, currency, rule)
                );
                CREATE TABLE IF NOT EXISTS official_prices (
                    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                    recorded_at INTEGER NOT NULL,
                    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
                    currency TEXT NOT NULL DEFAULT '',
                    currency_label TEXT NOT NULL DEFAULT '',
                    imported_at INTEGER NOT NULL,
                    PRIMARY KEY(item_id, recorded_at)
                );
            """)
            # Additive migration for databases created before the merge feature:
            # imports were not currency-tagged, so they must not be merged into a
            # series of a different currency. There is no migration framework —
            # create-only DDL plus guarded ALTERs like this is the upgrade path.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(official_prices)")}
            if "currency" not in columns:
                db.execute(
                    "ALTER TABLE official_prices ADD COLUMN currency TEXT NOT NULL DEFAULT ''")

    def add_item(self, item: MarketItem, target_cents: int | None = None) -> int:
        with self.connection() as db:
            return self._add_item(db, item, target_cents)

    def _add_item(self, db: sqlite3.Connection, item: MarketItem,
                  target_cents: int | None = None) -> int:
        if item.appid <= 0 or not item.hash_name.strip():
            raise ValueError("AppID 和商品名称不能为空")
        if target_cents is not None and target_cents < 0:
            raise ValueError("目标价格不能为负数")
        cur = db.execute("""
            INSERT INTO items(appid, hash_name, display_name, app_name,
                              image_url, target_cents, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
        """, (item.appid, item.hash_name.strip(), item.display_name.strip() or
               item.hash_name.strip(), item.app_name, item.image_url,
               target_cents, int(time.time())))
        return int(cur.lastrowid)

    def list_items(self, currency: str, *, enabled_only: bool = False) -> list[dict]:
        sql = _ITEM_SELECT
        if enabled_only:
            sql += " WHERE i.enabled=1"
        sql += " ORDER BY i.id DESC"
        with self.connection() as db:
            return [dict(row) for row in db.execute(sql, (currency,) * 4).fetchall()]

    def get_item(self, item_id: int, currency: str) -> dict | None:
        """Single-row variant of list_items.

        The UI calls this on every selection change; going through list_items
        would run three correlated subqueries over the whole watchlist each time.
        """
        with self.connection() as db:
            row = db.execute(_ITEM_SELECT + " WHERE i.id=?",
                             (currency,) * 4 + (item_id,)).fetchone()
        return dict(row) if row is not None else None

    def update_item(self, item_id: int, target_cents: int | None,
                    new_low_enabled: bool, enabled: bool) -> None:
        with self.connection() as db:
            self._update_item(db, item_id, target_cents, new_low_enabled, enabled)

    def _update_item(self, db: sqlite3.Connection, item_id: int, target_cents: int | None,
                     new_low_enabled: bool, enabled: bool) -> None:
        if target_cents is not None and target_cents < 0:
            raise ValueError("目标价格不能为负数")
        old = db.execute("SELECT target_cents FROM items WHERE id=?", (item_id,)).fetchone()
        if old is None:
            raise ValueError("商品不存在")
        db.execute("""UPDATE items SET target_cents=?,new_low_enabled=?,enabled=?
                      WHERE id=?""", (target_cents, int(new_low_enabled), int(enabled), item_id))
        if old["target_cents"] != target_cents:
            # Re-arm: a missing latch row reads as "outside the target".
            db.execute("DELETE FROM alert_state WHERE item_id=? AND rule='target'", (item_id,))

    def delete_item(self, item_id: int) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM items WHERE id=?", (item_id,))

    def set_item_display_name(self, item_id: int, name: str) -> None:
        """Rename how an item is shown locally; ``hash_name`` is never touched."""
        with self.connection() as db:
            db.execute("UPDATE items SET display_name=? WHERE id=?", (name, item_id))

    def set_item_image(self, item_id: int, url: str) -> None:
        """Store a thumbnail URL fetched after the item was added.

        Items added by pasting a link arrive without one; the poller never touches
        this, it is filled in once by the UI.
        """
        with self.connection() as db:
            db.execute("UPDATE items SET image_url=? WHERE id=?", (url, item_id))

    def set_error(self, item_id: int, message: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE items SET last_error=? WHERE id=?", (message[:300], item_id))

    def record_snapshot(self, item_id: int, currency: str, price_cents: int,
                        volume: str = "", checked_at: int | None = None,
                        drop_alerts: bool = False) -> list[Alert]:
        """Transactionally record price and emit *transitions*, not repeated alerts.

        Historical-low alerts start only after the FIRST successful observation.
        Price unchanged => one heartbeat sample every 6 hours, reducing DB size.

        ``drop_alerts`` fires on any price strictly below the previous *local*
        sample. That baseline is deliberately local-only: an imported official
        history can push the observed low out of reach, but it can never make the
        previous local sample look lower than it was.
        """
        if price_cents < 0:
            raise ValueError("invalid price")
        now = int(checked_at if checked_at is not None else time.time())
        events: list[Alert] = []
        with self.connection() as db:
            item = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if item is None or not item["enabled"]:
                return []
            # Local samples only: this drives the on-change / 6-hour heartbeat
            # dedup below, so comparing against an imported point (a different
            # metric, and possibly newer than the last local sample) would make the
            # poller store bogus extra samples.
            previous = db.execute("""SELECT price_cents, recorded_at FROM snapshots
                                     WHERE item_id=? AND currency=?
                                     ORDER BY recorded_at DESC, id DESC LIMIT 1""",
                                  (item_id, currency)).fetchone()
            # The observed low *does* include an imported official history, by
            # explicit user choice: it extends the same series, so a new low is
            # measured against everything known.
            low_row = db.execute("""SELECT MIN(price_cents) AS value FROM (
                                        SELECT price_cents FROM snapshots
                                         WHERE item_id=? AND currency=?
                                        UNION ALL
                                        SELECT price_cents FROM official_prices
                                         WHERE item_id=? AND currency=?)""",
                                 (item_id, currency, item_id, currency)).fetchone()
            old_low = low_row["value"]
            if previous is None or price_cents != previous["price_cents"] or \
                    now - previous["recorded_at"] >= 21600:
                db.execute("""INSERT INTO snapshots(item_id,currency,recorded_at,
                              price_cents,volume) VALUES(?,?,?,?,?)
                              ON CONFLICT(item_id,currency,recorded_at)
                              DO UPDATE SET price_cents=excluded.price_cents,
                                            volume=excluded.volume""",
                           (item_id, currency, now, price_cents, volume))
            db.execute("UPDATE items SET last_checked_at=?,last_error=NULL WHERE id=?",
                       (now, item_id))
            target = item["target_cents"]
            inside_target = target is not None and price_cents <= target
            current_state = db.execute("""SELECT active FROM alert_state
                                           WHERE item_id=? AND currency=? AND rule='target'""",
                                       (item_id, currency)).fetchone()
            was_inside = bool(current_state["active"]) if current_state else False
            if inside_target and not was_inside:
                events.append(Alert(item_id, item["appid"], item["hash_name"],
                                    item["display_name"], "target", price_cents, target, currency))
            if inside_target != was_inside:
                # Only transitions need to be persisted; a missing row already
                # reads as "outside the target", so a no-op write per item per
                # poll would be pure overhead.
                db.execute("""INSERT INTO alert_state(item_id,currency,rule,active)
                              VALUES(?,?,'target',?)
                              ON CONFLICT(item_id,currency,rule)
                              DO UPDATE SET active=excluded.active""",
                           (item_id, currency, int(inside_target)))
            if item["new_low_enabled"] and old_low is not None and price_cents < old_low:
                events.append(Alert(item_id, item["appid"], item["hash_name"],
                                    item["display_name"], "new_low", price_cents, old_low, currency))
            if drop_alerts and previous is not None and price_cents < previous["price_cents"]:
                # previous is the last LOCAL sample, so this keeps working even when
                # an imported history has lowered the observed-low baseline beyond it.
                events.append(Alert(item_id, item["appid"], item["hash_name"],
                                    item["display_name"], "drop", price_cents,
                                    int(previous["price_cents"]), currency))
        return events

    def get_history(self, item_id: int, currency: str, limit: int = 2500) -> list[tuple[int, int]]:
        """The observed series: local samples merged with an imported official history."""
        with self.connection() as db:
            rows = db.execute("""
                SELECT recorded_at, price_cents FROM (
                    SELECT recorded_at, price_cents FROM snapshots
                     WHERE item_id=? AND currency=?
                    UNION ALL
                    SELECT recorded_at, price_cents FROM official_prices
                     WHERE item_id=? AND currency=?
                ) ORDER BY recorded_at DESC LIMIT ?""",
                              (item_id, currency, item_id, currency, limit)).fetchall()
        return [(int(row[0]), int(row[1])) for row in reversed(rows)]

    # --- imported official sales history ---------------------------------
    # Written by the browser-import flow. It is kept in its own table but *merged
    # into the observed series on read* (see get_history / _ITEM_SELECT), so an
    # imported history extends the same line the poller writes to. Only rows whose
    # currency matches the series being read are merged, so switching currencies
    # cannot silently mix them.

    def save_official_history(self, item_id: int, points: list[tuple[int, int]],
                              currency_label: str = "", currency: str = "") -> int:
        """Replace an item's imported official series. Returns the points stored."""
        if len(points) > MAX_OFFICIAL_POINTS:
            raise ValueError(f"数据点过多（上限 {MAX_OFFICIAL_POINTS}），请确认导入的是单个商品的历史")
        # A merged paste can repeat a timestamp; keeping the last one per stamp
        # avoids colliding on the (item_id, recorded_at) primary key.
        deduped = {stamp: cents for stamp, cents in points if cents >= 0}
        if not deduped:
            raise ValueError("没有可导入的数据点")
        now = int(time.time())
        with self.connection() as db:
            db.execute("DELETE FROM official_prices WHERE item_id=?", (item_id,))
            db.executemany("""INSERT INTO official_prices(item_id, recorded_at, price_cents,
                              currency, currency_label, imported_at) VALUES(?,?,?,?,?,?)""",
                           [(item_id, stamp, cents, currency, currency_label, now)
                            for stamp, cents in sorted(deduped.items())])
        return len(deduped)

    def official_series(self, item_id: int) -> tuple[list[tuple[int, int]], str, int | None]:
        """The import as stored: ``(points, currency_label, imported_at)``.

        The chart does *not* use this — it reads the merged series via
        ``get_history``. This is the way to read an import back on its own (what was
        imported, and when, regardless of currency), which is what the tests use to
        verify a round-trip.
        """
        with self.connection() as db:
            rows = db.execute("""SELECT recorded_at, price_cents, currency_label, imported_at
                                 FROM official_prices WHERE item_id=?
                                 ORDER BY recorded_at""", (item_id,)).fetchall()
        if not rows:
            return [], "", None
        points = [(int(row[0]), int(row[1])) for row in rows]
        return points, str(rows[0][2]), int(rows[0][3])

    def merged_point_counts(self, item_id: int, currency: str) -> tuple[int, int]:
        """``(local samples, imported points)`` that make up this currency's series.

        The imported count is currency-scoped because that is exactly what
        ``get_history`` merges — an import made under another currency stays out.
        """
        with self.connection() as db:
            local = db.execute("SELECT COUNT(*) FROM snapshots WHERE item_id=? AND currency=?",
                               (item_id, currency)).fetchone()[0]
            imported = db.execute(
                "SELECT COUNT(*) FROM official_prices WHERE item_id=? AND currency=?",
                (item_id, currency)).fetchone()[0]
        return int(local), int(imported)

    def export_history_csv(self, path: Path, currency: str,
                           item_ids: set[int] | None = None) -> None:
        """Export the merged series, optionally only for the given items.

        A ``source`` column distinguishes rows the poller observed from imported
        official points, which are a different quantity even though they share the
        chart — without it the export would silently mix the two. ``item_ids=None``
        means every item; an empty set exports only the header.
        """
        # Both branches of the UNION are filtered the same way; their parameters must
        # stay in the SQL's own order (currency, then ids, per branch).
        local_where, official_where = "s.currency=?", "o.currency=?"
        local_params: list = [currency]
        official_params: list = [currency]
        if item_ids is not None:
            if not item_ids:  # explicitly nothing selected: header only
                path.write_text("", encoding="utf-8-sig")
                return
            placeholders = ",".join(["?"] * len(item_ids))
            local_where += f" AND s.item_id IN ({placeholders})"
            official_where += f" AND o.item_id IN ({placeholders})"
            local_params += list(item_ids)
            official_params += list(item_ids)
        with self.connection() as db, path.open("w", newline="", encoding="utf-8-sig") as dest:
            writer = csv.writer(dest)
            writer.writerow(["appid", "market_hash_name", "currency", "utc_timestamp",
                             "lowest_listing_price", "volume", "source"])
            for row in db.execute(f"""
                    SELECT i.appid, i.hash_name, s.currency, s.recorded_at, s.price_cents,
                           s.volume, 'local'
                      FROM snapshots s JOIN items i ON i.id=s.item_id
                     WHERE {local_where}
                    UNION ALL
                    SELECT i.appid, i.hash_name, o.currency, o.recorded_at, o.price_cents,
                           NULL, 'official'
                      FROM official_prices o JOIN items i ON i.id=o.item_id
                     WHERE {official_where}
                     ORDER BY 4""", local_params + official_params):
                writer.writerow([row[0], row[1], row[2], row[3], f"{row[4]/100:.2f}",
                                 row[5], row[6]])

    def export_watchlist(self, path: Path, item_ids: set[int] | None = None) -> int:
        """Export the watchlist, optionally only for the given items."""
        sql = """SELECT appid,hash_name,display_name,app_name,image_url,
                 target_cents,new_low_enabled,enabled FROM items"""
        params: tuple = ()
        if item_ids is not None:
            if not item_ids:  # explicitly nothing selected
                path.write_text("[]", encoding="utf-8")
                return 0
            sql += f" WHERE id IN ({','.join('?' * len(item_ids))})"
            params = tuple(item_ids)
        sql += " ORDER BY id"
        with self.connection() as db:
            rows = db.execute(sql, params).fetchall()
        records = [dict(row) for row in rows]
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(records)

    def import_watchlist(self, path: Path) -> tuple[int, int]:
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list) or len(records) > 10000:
            raise ValueError("无效的监控清单 JSON（最多 10000 件）")
        added = skipped = 0
        # One transaction for the whole batch: going through the public
        # add_item/update_item would open and commit two connections per record.
        with self.connection() as db:
            for row in records:
                if not isinstance(row, dict):
                    skipped += 1
                    continue
                try:
                    appid = int(row["appid"])
                    name = str(row["hash_name"])
                    target = row.get("target_cents")
                    target = int(target) if target is not None else None
                    item = MarketItem(appid, name, str(row.get("display_name") or name),
                                      str(row.get("app_name") or ""),
                                      str(row.get("image_url") or ""))
                    item_id = self._add_item(db, item, target)
                    self._update_item(db, item_id, target, bool(row.get("new_low_enabled", 1)),
                                      bool(row.get("enabled", 1)))
                    added += 1
                except (KeyError, ValueError, TypeError, sqlite3.IntegrityError):
                    # SQLite's default ABORT rolls back only the failing
                    # statement, so a duplicate skips that row and the batch
                    # (including rows already inserted) still commits.
                    skipped += 1
        return added, skipped
