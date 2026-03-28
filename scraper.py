"""
Scraper module — pulls deals from RSS feeds and web pages.
Each function yields Deal dicts: {source, title, url, price, summary}.
"""

import logging
import time
from dataclasses import dataclass, asdict

import feedparser
import requests
from bs4 import BeautifulSoup

import config

log = logging.getLogger(__name__)


@dataclass
class Deal:
    source: str
    title: str
    url: str = ""
    price: str = ""
    summary: str = ""


def _matches_interest(text: str) -> bool:
    """Return True if text mentions a tracked brand or deal keyword."""
    low = text.lower()
    brand_hit = any(b in low for b in config.BRANDS)
    keyword_hit = any(k in low for k in config.DEAL_KEYWORDS)
    return brand_hit or keyword_hit


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------

def scrape_rss() -> list[Deal]:
    deals: list[Deal] = []
    for name, url in config.RSS_FEEDS.items():
        try:
            log.info("Fetching RSS: %s", name)
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")
                combined = f"{title} {summary}"
                if _matches_interest(combined):
                    deals.append(
                        Deal(
                            source=name,
                            title=title,
                            url=link,
                            summary=summary[:500],
                        )
                    )
        except Exception:
            log.exception("RSS error for %s", name)
    return deals


# ---------------------------------------------------------------------------
# Web scraping (with polite delays)
# ---------------------------------------------------------------------------

def scrape_web() -> list[Deal]:
    deals: list[Deal] = []
    session = requests.Session()
    session.headers.update(config.REQUEST_HEADERS)

    for target in config.SCRAPE_TARGETS:
        try:
            log.info("Scraping page: %s", target["name"])
            resp = session.get(target["url"], timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            cards = soup.select(target["selector"])
            for card in cards[:30]:  # cap per page
                title_el = card.select_one(target.get("title_sel", ""))
                price_el = card.select_one(target.get("price_sel", ""))

                title = title_el.get_text(strip=True) if title_el else ""
                price = price_el.get_text(strip=True) if price_el else ""
                link = card.get("href", "")

                if not link.startswith("http"):
                    from urllib.parse import urljoin
                    link = urljoin(target["url"], link)

                if title and _matches_interest(f"{title} {price}"):
                    deals.append(
                        Deal(
                            source=target["name"],
                            title=title,
                            url=link,
                            price=price,
                        )
                    )
            time.sleep(config.REQUEST_DELAY)
        except Exception:
            log.exception("Web scrape error for %s", target["name"])
    return deals


# ---------------------------------------------------------------------------
# Combined
# ---------------------------------------------------------------------------

def fetch_all_deals() -> list[dict]:
    """Run all scrapers and return list of deal dicts."""
    all_deals = scrape_rss() + scrape_web()
    log.info("Total raw deals found: %d", len(all_deals))
    return [asdict(d) for d in all_deals]
