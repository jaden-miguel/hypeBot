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
