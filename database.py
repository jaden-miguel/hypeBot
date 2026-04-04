import sqlite3
import hashlib
import threading
from datetime import datetime, timezone, timedelta

import config

_local = threading.local()


def _connect() -> sqlite3.Connection:
    """Thread-local persistent connection with WAL mode."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-8000")  # 8MB cache
        _local.conn = conn
    return conn


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                id          TEXT PRIMARY KEY,
                source      TEXT NOT NULL,
                title       TEXT NOT NULL,
                url         TEXT,
                price       TEXT,
                summary     TEXT,
                ai_analysis TEXT,
                upvotes     INTEGER DEFAULT 0,
                comments    INTEGER DEFAULT 0,
                flair       TEXT DEFAULT '',
                image       TEXT DEFAULT '',
                is_alerted  INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_deals_source ON deals(source)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_deals_created ON deals(created_at)"
        )
        for col, dtype in [
            ("upvotes", "INTEGER DEFAULT 0"),
            ("comments", "INTEGER DEFAULT 0"),
            ("flair", "TEXT DEFAULT ''"),
            ("image", "TEXT DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE deals ADD COLUMN {col} {dtype}")
            except sqlite3.OperationalError:
                pass

        # ── Drops table ──────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS drops (
                id              TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                brand           TEXT DEFAULT '',
                release_dt      TEXT NOT NULL,
                release_label   TEXT DEFAULT '',
                price           TEXT DEFAULT '',
                url             TEXT DEFAULT '',
                image           TEXT DEFAULT '',
                source          TEXT DEFAULT '',
                notified_7day   INTEGER DEFAULT 0,
                notified_1day   INTEGER DEFAULT 0,
                notified_today  INTEGER DEFAULT 0,
                created_at      TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_drops_release ON drops(release_dt)"
        )

        # ── Price history table ───────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title_norm  TEXT NOT NULL,
                title       TEXT NOT NULL,
                source      TEXT NOT NULL,
                price       REAL NOT NULL,
                url         TEXT DEFAULT '',
                image       TEXT DEFAULT '',
                seen_at     TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ph_title ON price_history(title_norm)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ph_seen ON price_history(seen_at)"
        )

        # ── Item tracker for restock detection ────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS item_tracker (
                title_norm  TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                source      TEXT NOT NULL,
                last_seen   TEXT NOT NULL,
                last_price  REAL DEFAULT 0,
                times_seen  INTEGER DEFAULT 1,
                was_gone    INTEGER DEFAULT 0,
                url         TEXT DEFAULT '',
                image       TEXT DEFAULT ''
            )
        """)

        # ── Cycle analytics ───────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cycle_stats (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_at        TEXT NOT NULL,
                duration_s      REAL DEFAULT 0,
                deals_scanned   INTEGER DEFAULT 0,
                deals_new       INTEGER DEFAULT 0,
                alerts_sent     INTEGER DEFAULT 0,
                flips_found     INTEGER DEFAULT 0,
                restocks_found  INTEGER DEFAULT 0,
                lowest_prices   INTEGER DEFAULT 0,
                total_est_profit REAL DEFAULT 0,
                top_source      TEXT DEFAULT '',
                is_rapid        INTEGER DEFAULT 0
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cs_at ON cycle_stats(cycle_at)"
        )


def deal_hash(source: str, title: str, url: str = "") -> str:
    raw = f"{source}|{title}|{url}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_known_ids() -> set[str]:
    """Load all deal IDs into memory for fast O(1) dedup checks."""
    with _connect() as conn:
        rows = conn.execute("SELECT id FROM deals").fetchall()
        return {r[0] for r in rows}


def save_deal(
    deal_id: str,
    source: str,
    title: str,
    url: str = "",
    price: str = "",
    summary: str = "",
    ai_analysis: str = "",
    upvotes: int = 0,
    comments: int = 0,
    flair: str = "",
    image: str = "",
) -> bool:
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO deals
                    (id, source, title, url, price, summary, ai_analysis,
                     upvotes, comments, flair, image, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deal_id, source, title, url, price, summary, ai_analysis,
                    upvotes, comments, flair, image,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return conn.total_changes > 0
    except sqlite3.Error:
        return False


def mark_alerted(deal_id: str):
    with _connect() as conn:
        conn.execute("UPDATE deals SET is_alerted = 1 WHERE id = ?", (deal_id,))


def prune_old_deals(days: int = 30):
    """Delete deals older than N days to keep the DB lean."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        deleted = conn.execute(
            "DELETE FROM deals WHERE created_at < ?", (cutoff,)
        ).rowcount
        if deleted:
            conn.execute("PRAGMA optimize")
    return deleted


def get_recent_deals(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM deals ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def count_deals() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]


# ---------------------------------------------------------------------------
# Drops
# ---------------------------------------------------------------------------

def save_drop(drop: dict) -> bool:
    """Insert a drop, ignore if already exists. Returns True if new."""
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO drops
                    (id, title, brand, release_dt, release_label,
                     price, url, image, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    drop["id"], drop["title"], drop.get("brand", ""),
                    drop["release_dt"], drop.get("release_label", ""),
                    drop.get("price", ""), drop.get("url", ""),
                    drop.get("image", ""), drop.get("source", ""),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return conn.total_changes > 0
    except sqlite3.Error:
        return False


def get_drops_needing_notification() -> list[dict]:
    """Return drops that are due for a 7-day, 1-day, or day-of alert."""
    now  = datetime.now(timezone.utc)
    rows_out = []

    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM drops WHERE release_dt >= ? ORDER BY release_dt",
            ((now - timedelta(hours=12)).isoformat(),),
        ).fetchall()

    for row in rows:
        d = dict(row)
        try:
            dt = datetime.fromisoformat(d["release_dt"])
        except ValueError:
            continue

        days_away = (dt.date() - now.date()).days

        tier = None
        if days_away == 0 and not d["notified_today"]:
            tier = "today"
        elif days_away == 1 and not d["notified_1day"]:
            tier = "1day"
        elif 5 <= days_away <= 7 and not d["notified_7day"]:
            tier = "7day"

        if tier:
            d["_notify_tier"] = tier
            rows_out.append(d)

    return rows_out


def mark_drop_notified(drop_id: str, tier: str):
    col_map = {"today": "notified_today", "1day": "notified_1day", "7day": "notified_7day"}
    col = col_map.get(tier)
    if col:
        with _connect() as conn:
            conn.execute(f"UPDATE drops SET {col} = 1 WHERE id = ?", (drop_id,))


def prune_old_drops(days: int = 7):
    """Remove drops whose release date has passed by more than N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        return conn.execute(
            "DELETE FROM drops WHERE release_dt < ?", (cutoff,)
        ).rowcount


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------

def _normalize_title(title: str) -> str:
    import re
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def record_price(title: str, source: str, price: float,
                 url: str = "", image: str = "") -> dict:
    """Record a price snapshot and return price intelligence.

    Returns:
        {
            "is_lowest": bool,
            "previous_low": float,
            "price_drop": float,     # how much it dropped from prev low
            "is_restock": bool,       # was gone, now it's back
            "times_seen": int,
        }
    """
    now = datetime.now(timezone.utc).isoformat()
    norm = _normalize_title(title)
    if not norm or len(norm) < 4:
        return {"is_lowest": False, "previous_low": 0, "price_drop": 0,
                "is_restock": False, "times_seen": 0}

    conn = _connect()

    # Record price snapshot
    conn.execute(
        "INSERT INTO price_history (title_norm, title, source, price, url, image, seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (norm, title, source, price, url, image, now),
    )

    # Find previous lowest price for this item
    row = conn.execute(
        "SELECT MIN(price) as min_price, COUNT(*) as cnt FROM price_history "
        "WHERE title_norm = ? AND id != last_insert_rowid()",
        (norm,),
    ).fetchone()
    prev_low = row["min_price"] if row and row["min_price"] is not None else 0
    prev_count = row["cnt"] if row else 0

    is_lowest = price < prev_low if prev_low > 0 else (prev_count == 0)
    price_drop = prev_low - price if prev_low > price > 0 else 0

    # Update item tracker for restock detection
    tracker = conn.execute(
        "SELECT * FROM item_tracker WHERE title_norm = ?", (norm,),
    ).fetchone()

    is_restock = False
    times_seen = 1

    if tracker:
        was_gone = tracker["was_gone"]
        times_seen = tracker["times_seen"] + 1
        is_restock = bool(was_gone)

        conn.execute(
            "UPDATE item_tracker SET last_seen = ?, last_price = ?, "
            "times_seen = ?, was_gone = 0, url = ?, image = ? "
            "WHERE title_norm = ?",
            (now, price, times_seen, url, image, norm),
        )
    else:
        conn.execute(
            "INSERT INTO item_tracker (title_norm, title, source, last_seen, "
            "last_price, times_seen, url, image) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (norm, title, source, now, price, url, image),
        )

    conn.commit()

    return {
        "is_lowest": is_lowest,
        "previous_low": prev_low,
        "price_drop": price_drop,
        "is_restock": is_restock,
        "times_seen": times_seen,
    }


def mark_gone_items(current_titles: set[str]):
    """Mark items as 'gone' if they weren't seen in the current cycle.
    Only marks items from web scraper sources (not RSS/Reddit)."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=6)).isoformat()
    conn = _connect()

    rows = conn.execute(
        "SELECT title_norm FROM item_tracker WHERE last_seen < ? AND was_gone = 0",
        (cutoff,),
    ).fetchall()

    norms = {_normalize_title(t) for t in current_titles}
    marked = 0
    for row in rows:
        if row["title_norm"] not in norms:
            conn.execute(
                "UPDATE item_tracker SET was_gone = 1 WHERE title_norm = ?",
                (row["title_norm"],),
            )
            marked += 1

    if marked:
        conn.commit()
    return marked


def prune_price_history(days: int = 30):
    """Remove old price snapshots."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        return conn.execute(
            "DELETE FROM price_history WHERE seen_at < ?", (cutoff,)
        ).rowcount


def prune_item_tracker(days: int = 30):
    """Remove stale items from tracker."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        return conn.execute(
            "DELETE FROM item_tracker WHERE last_seen < ?", (cutoff,)
        ).rowcount


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def record_cycle_stats(stats: dict):
    """Save one row of cycle-level analytics."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO cycle_stats
               (cycle_at, duration_s, deals_scanned, deals_new, alerts_sent,
                flips_found, restocks_found, lowest_prices, total_est_profit,
                top_source, is_rapid)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now,
                stats.get("duration_s", 0),
                stats.get("deals_scanned", 0),
                stats.get("deals_new", 0),
                stats.get("alerts_sent", 0),
                stats.get("flips_found", 0),
                stats.get("restocks_found", 0),
                stats.get("lowest_prices", 0),
                stats.get("total_est_profit", 0),
                stats.get("top_source", ""),
                1 if stats.get("is_rapid") else 0,
            ),
        )


def get_analytics(days: int = 7) -> dict:
    """Aggregate analytics over the last N days from all tables."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = _connect()

    # ── Cycle stats aggregates ──
    cs = conn.execute(
        """SELECT
             COUNT(*)                   AS total_cycles,
             COALESCE(SUM(deals_scanned), 0)   AS total_scanned,
             COALESCE(SUM(deals_new), 0)        AS total_new,
             COALESCE(SUM(alerts_sent), 0)      AS total_alerts,
             COALESCE(SUM(flips_found), 0)      AS total_flips,
             COALESCE(SUM(restocks_found), 0)   AS total_restocks,
             COALESCE(SUM(lowest_prices), 0)    AS total_lowest,
             COALESCE(SUM(total_est_profit), 0) AS sum_profit,
             COALESCE(AVG(duration_s), 0)       AS avg_duration,
             SUM(is_rapid)              AS rapid_cycles
           FROM cycle_stats WHERE cycle_at >= ?""",
        (cutoff,),
    ).fetchone()

    # ── Deals by source ──
    source_rows = conn.execute(
        """SELECT source, COUNT(*) AS cnt
           FROM deals WHERE created_at >= ?
           GROUP BY source ORDER BY cnt DESC LIMIT 10""",
        (cutoff,),
    ).fetchall()

    # ── Alerted deals by source ──
    alerted_rows = conn.execute(
        """SELECT source, COUNT(*) AS cnt
           FROM deals WHERE created_at >= ? AND is_alerted = 1
           GROUP BY source ORDER BY cnt DESC LIMIT 5""",
        (cutoff,),
    ).fetchall()

    # ── Price trends — avg price per day for the last N days ──
    trend_rows = conn.execute(
        """SELECT DATE(seen_at) AS day, AVG(price) AS avg_price, COUNT(*) AS cnt
           FROM price_history WHERE seen_at >= ?
           GROUP BY DATE(seen_at) ORDER BY day""",
        (cutoff,),
    ).fetchall()

    # ── Most tracked items ──
    top_items = conn.execute(
        """SELECT title, times_seen, last_price, source
           FROM item_tracker ORDER BY times_seen DESC LIMIT 5"""
    ).fetchall()

    # ── Restocked items in period ──
    restocked = conn.execute(
        """SELECT title, source, last_price
           FROM item_tracker WHERE was_gone = 0 AND times_seen > 1
           ORDER BY times_seen DESC LIMIT 5"""
    ).fetchall()

    # ── Total unique items tracked ──
    unique_items = conn.execute(
        "SELECT COUNT(*) FROM item_tracker"
    ).fetchone()[0]

    # ── Total price snapshots ──
    total_snapshots = conn.execute(
        "SELECT COUNT(*) FROM price_history WHERE seen_at >= ?", (cutoff,)
    ).fetchone()[0]

    # ── Upcoming drops ──
    now_iso = datetime.now(timezone.utc).isoformat()
    upcoming_drops = conn.execute(
        "SELECT COUNT(*) FROM drops WHERE release_dt >= ?", (now_iso,)
    ).fetchone()[0]

    return {
        "days": days,
        "total_cycles": cs["total_cycles"] if cs else 0,
        "total_scanned": cs["total_scanned"] if cs else 0,
        "total_new": cs["total_new"] if cs else 0,
        "total_alerts": cs["total_alerts"] if cs else 0,
        "total_flips": cs["total_flips"] if cs else 0,
        "total_restocks": cs["total_restocks"] if cs else 0,
        "total_lowest": cs["total_lowest"] if cs else 0,
        "sum_profit": cs["sum_profit"] if cs else 0,
        "avg_duration": cs["avg_duration"] if cs else 0,
        "rapid_cycles": cs["rapid_cycles"] if cs else 0,
        "sources": [(r["source"], r["cnt"]) for r in source_rows],
        "alerted_sources": [(r["source"], r["cnt"]) for r in alerted_rows],
        "price_trend": [(r["day"], r["avg_price"], r["cnt"]) for r in trend_rows],
        "top_items": [dict(r) for r in top_items],
        "restocked": [dict(r) for r in restocked],
        "unique_items": unique_items,
        "total_snapshots": total_snapshots,
        "upcoming_drops": upcoming_drops,
    }


def prune_cycle_stats(days: int = 90):
    """Keep cycle stats for the last N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        return conn.execute(
            "DELETE FROM cycle_stats WHERE cycle_at < ?", (cutoff,)
        ).rowcount
