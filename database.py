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
