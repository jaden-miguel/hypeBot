import sqlite3
import hashlib
from datetime import datetime, timezone

import config


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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
                is_alerted  INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_deals_source
            ON deals(source)
        """)


def deal_hash(source: str, title: str, url: str = "") -> str:
    raw = f"{source}|{title}|{url}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def deal_exists(deal_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM deals WHERE id = ?", (deal_id,)).fetchone()
        return row is not None


def save_deal(
    deal_id: str,
    source: str,
    title: str,
    url: str = "",
    price: str = "",
    summary: str = "",
    ai_analysis: str = "",
) -> bool:
    """Returns True if this is a new deal (inserted), False if it already existed."""
    if deal_exists(deal_id):
        return False
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO deals (id, source, title, url, price, summary, ai_analysis, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                deal_id,
                source,
                title,
                url,
                price,
                summary,
                ai_analysis,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return True


def mark_alerted(deal_id: str):
    with _connect() as conn:
        conn.execute("UPDATE deals SET is_alerted = 1 WHERE id = ?", (deal_id,))


def get_recent_deals(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM deals ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
