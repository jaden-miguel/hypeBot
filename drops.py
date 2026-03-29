"""
Drop tracker — scrapes upcoming release calendars and parses
release dates from RSS feeds. Provides structured Drop objects
with release_dt so the main loop can fire time-based alerts.
"""

import logging
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

import config

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Drop:
    id: str           # stable hash
    title: str
    brand: str
    release_dt: str   # ISO-8601 UTC, date-only if time unknown
    price: str = ""
    url: str = ""
    image: str = ""
    source: str = ""
    release_label: str = ""   # human-friendly "Apr 5, 2026" or "Apr 5 @ 10:00 AM"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_PATTERNS = [
    r"(\w+ \d{1,2},?\s*\d{4})",          # April 5, 2026
    r"(\d{1,2}/\d{1,2}/\d{4})",           # 4/5/2026
    r"(\d{4}-\d{2}-\d{2})",               # 2026-04-05
    r"(\w+ \d{1,2}(?:st|nd|rd|th)?),?\s*(\d{4})",  # April 5th 2026
]
_TIME_PATTERN = re.compile(r"(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?(?:\s*ET|EST|PST|PT|CT)?)")


def _parse_date(text: str) -> datetime | None:
    """Best-effort date parse from a text string."""
    text = text.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
                "%m/%d/%Y", "%Y-%m-%d", "%B %dth, %Y", "%B %dst, %Y",
                "%B %dnd, %Y", "%B %drd, %Y"):
        # Normalise ordinal suffixes
        clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text)
        try:
            return datetime.strptime(clean.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    # Fuzzy: find month name + day + year
    m = re.search(
        r"(\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*)\s+(\d{1,2})[\w,]*\s+(\d{4})",
        text, re.IGNORECASE,
    )
    if m:
        mon = _MONTH_MAP.get(m.group(1).lower()[:3])
        if mon:
            try:
                return datetime(int(m.group(3)), mon, int(m.group(2)),
                                tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def _drop_id(title: str, release_dt: str) -> str:
    import hashlib
    raw = f"{title.lower().strip()}|{release_dt[:10]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _is_brand_relevant(text: str) -> bool:
    low = text.lower()
    return any(b in low for b in config.BRANDS)


def _future_only(dt: datetime) -> bool:
    """Keep drops that haven't happened yet (within 60 days out)."""
    now = datetime.now(timezone.utc)
    return now - timedelta(days=1) <= dt <= now + timedelta(days=60)


def _format_label(dt: datetime, has_time: bool = False) -> str:
    now = datetime.now(timezone.utc)
    diff = (dt.date() - now.date()).days
    if diff == 0:
        base = "Today"
    elif diff == 1:
        base = "Tomorrow"
    else:
        base = dt.strftime("%b %-d, %Y")
    if has_time:
        return f"{base} @ {dt.strftime('%I:%M %p ET').lstrip('0')}"
    return base


def _get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(config.REQUEST_HEADERS)
    return s


# ---------------------------------------------------------------------------
# Source 1 — SneakerNews release calendar
# ---------------------------------------------------------------------------

def _scrape_sneakernews_calendar() -> list[Drop]:
    drops: list[Drop] = []
    try:
        session = _get_session()
        resp = session.get(
            "https://sneakernews.com/release-dates/",
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for card in soup.select(".releases-box"):
            title_el = card.select_one("a.prod-name")
            # Date with year lives inside .image-box, e.g. "March 28, 2026"
            date_el  = card.select_one(".image-box .release-date")
            price_el = card.select_one(".release-price")
            link_el  = card.select_one(".image-box a[href]")
            img_el   = card.select_one("img")

            title = title_el.get_text(strip=True) if title_el else ""
            if not title or not _is_brand_relevant(title):
                continue

            raw_date = date_el.get_text(strip=True) if date_el else ""
            dt = _parse_date(raw_date)
            if not dt or not _future_only(dt):
                continue

            url   = link_el["href"] if link_el else ""
            price_text = price_el.get_text(strip=True) if price_el else ""
            image = ""
            if img_el:
                image = img_el.get("src") or img_el.get("data-src") or ""

            drop_id = _drop_id(title, dt.isoformat())
            drops.append(Drop(
                id=drop_id, title=title, brand=_detect_brand(title),
                release_dt=dt.isoformat(), price=price_text,
                url=url, image=image, source="sneakernews_calendar",
                release_label=_format_label(dt),
            ))
    except Exception:
        log.exception("SneakerNews calendar scrape failed")
    return drops


# ---------------------------------------------------------------------------
# Source 2 — Kicks on Fire RSS (has release dates in titles/summaries)
# ---------------------------------------------------------------------------

def _scrape_kicksonfire_rss() -> list[Drop]:
    drops: list[Drop] = []
    try:
        feed = feedparser.parse("https://www.kicksonfire.com/feed/")
        for entry in feed.entries:
            title = entry.get("title", "")
            full_text = _extract_text(entry)

            if not _is_brand_relevant(full_text):
                continue
            if not any(kw in title.lower() for kw in
                       ("release", "drop", "launch", "date", "arrive", "debut")):
                continue

            dt = _find_date_in_text(full_text)
            if not dt or not _future_only(dt):
                continue

            image   = _extract_rss_image(entry, entry.get("summary", ""))
            drop_id = _drop_id(title, dt.isoformat())
            drops.append(Drop(
                id=drop_id, title=title, brand=_detect_brand(title),
                release_dt=dt.isoformat(), url=entry.get("link", ""),
                image=image, source="kicksonfire", release_label=_format_label(dt),
            ))
    except Exception:
        log.exception("Kicks on Fire RSS drop parse failed")
    return drops


# ---------------------------------------------------------------------------
# Source 3 — Hypebeast RSS (parses release dates from article text)
# ---------------------------------------------------------------------------

def _scrape_hypebeast_drops() -> list[Drop]:
    drops: list[Drop] = []
    try:
        feed = feedparser.parse("https://hypebeast.com/feed")
        for entry in feed.entries:
            title     = entry.get("title", "")
            full_text = _extract_text(entry)

            if not _is_brand_relevant(full_text):
                continue
            if not any(kw in title.lower() for kw in
                       ("release", "drop", "launch", "available", "arrive", "debut", "date")):
                continue

            dt = _find_date_in_text(full_text)
            if not dt or not _future_only(dt):
                continue

            image   = _extract_rss_image(entry, entry.get("summary", ""))
            drop_id = _drop_id(title, dt.isoformat())
            drops.append(Drop(
                id=drop_id, title=title, brand=_detect_brand(title),
                release_dt=dt.isoformat(), url=entry.get("link", ""),
                image=image, source="hypebeast_drops", release_label=_format_label(dt),
            ))
    except Exception:
        log.exception("Hypebeast drop parse failed")
    return drops


# ---------------------------------------------------------------------------
# Source 4 — SneakerNews RSS (same pattern)
# ---------------------------------------------------------------------------

def _scrape_sneakernews_rss_drops() -> list[Drop]:
    drops: list[Drop] = []
    try:
        feed = feedparser.parse("https://sneakernews.com/feed/")
        for entry in feed.entries:
            title     = entry.get("title", "")
            full_text = _extract_text(entry)

            if not _is_brand_relevant(full_text):
                continue
            if not any(kw in title.lower() for kw in
                       ("release", "drop", "launch", "date", "arrive", "debut",
                        "release info", "official images")):
                continue

            dt = _find_date_in_text(full_text)
            if not dt or not _future_only(dt):
                continue

            image   = _extract_rss_image(entry, entry.get("summary", ""))
            drop_id = _drop_id(title, dt.isoformat())
            drops.append(Drop(
                id=drop_id, title=title, brand=_detect_brand(title),
                release_dt=dt.isoformat(), url=entry.get("link", ""),
                image=image, source="sneakernews_rss", release_label=_format_label(dt),
            ))
    except Exception:
        log.exception("SneakerNews RSS drop parse failed")
    return drops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(entry) -> str:
    """Get as much plain text as possible from an RSS entry."""
    parts = [entry.get("title", ""), entry.get("summary", "")]
    for c in entry.get("content", []):
        raw = c.get("value", "")
        parts.append(BeautifulSoup(raw, "html.parser").get_text(" "))
    return " ".join(parts)


def _find_date_in_text(text: str) -> datetime | None:
    for pat in _DATE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            dt = _parse_date(m.group(0))
            if dt:
                return dt
    return None


def _detect_brand(text: str) -> str:
    low = text.lower()
    for brand in config.BRANDS:
        if brand in low:
            return brand.title()
    return ""


def _extract_rss_image(entry, summary: str) -> str:
    for media in entry.get("media_content", []):
        url = media.get("url", "")
        if url:
            return url
    for enc in entry.get("enclosures", []):
        url = enc.get("href", enc.get("url", ""))
        if url:
            return url
    if summary:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary)
        if m:
            return m.group(1)
    return ""


# ---------------------------------------------------------------------------
# Source 5 — Sole Collector release calendar RSS
# ---------------------------------------------------------------------------

def _scrape_solecollector_rss() -> list[Drop]:
    drops: list[Drop] = []
    try:
        feed = feedparser.parse("https://solecollector.com/feed/")
        for entry in feed.entries:
            title     = entry.get("title", "")
            full_text = _extract_text(entry)

            if not _is_brand_relevant(full_text):
                continue
            if not any(kw in title.lower() for kw in
                       ("release", "drop", "launch", "date", "available", "debut")):
                continue

            dt = _find_date_in_text(full_text)
            if not dt or not _future_only(dt):
                continue

            image   = _extract_rss_image(entry, entry.get("summary", ""))
            drop_id = _drop_id(title, dt.isoformat())
            drops.append(Drop(
                id=drop_id, title=title, brand=_detect_brand(title),
                release_dt=dt.isoformat(), url=entry.get("link", ""),
                image=image, source="solecollector", release_label=_format_label(dt),
            ))
    except Exception:
        log.exception("Sole Collector RSS drop parse failed")
    return drops


# ---------------------------------------------------------------------------
# Combined
# ---------------------------------------------------------------------------

def fetch_upcoming_drops() -> list[dict]:
    """Fetch upcoming drops from all sources, deduplicated by drop ID."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    sources = [
        _scrape_sneakernews_calendar,
        _scrape_sneakernews_rss_drops,
        _scrape_hypebeast_drops,
        _scrape_kicksonfire_rss,
        _scrape_solecollector_rss,
    ]

    all_drops: list[Drop] = []
    seen_ids: set[str] = set()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn): fn.__name__ for fn in sources}
        for future in as_completed(futures):
            try:
                for drop in future.result():
                    if drop.id not in seen_ids:
                        seen_ids.add(drop.id)
                        all_drops.append(drop)
            except Exception:
                log.exception("Drop source %s failed", futures[future])

    all_drops.sort(key=lambda d: d.release_dt)
    log.info("Upcoming drops found: %d", len(all_drops))
    return [asdict(d) for d in all_drops]
