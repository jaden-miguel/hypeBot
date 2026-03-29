"""
Scraper module — concurrent RSS, web, and Reddit fetching.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

import config

log = logging.getLogger(__name__)

_session = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(config.REQUEST_HEADERS)
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=requests.adapters.Retry(
                total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503]
            ),
        )
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
    return _session


@dataclass
class Deal:
    source: str
    title: str
    url: str = ""
    price: str = ""
    summary: str = ""
    image: str = ""
    upvotes: int = 0
    comments: int = 0
    flair: str = ""


def _matches_interest(text: str) -> bool:
    low = text.lower()
    return any(b in low for b in config.BRANDS) or any(k in low for k in config.DEAL_KEYWORDS)


# ---------------------------------------------------------------------------
# RSS (one function per feed, run in parallel)
# ---------------------------------------------------------------------------

def _fetch_single_rss(name: str, url: str) -> list[Deal]:
    deals = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            link = entry.get("link", "")
            combined = f"{title} {summary}"
            if _matches_interest(combined):
                deals.append(Deal(
                    source=name,
                    title=title,
                    url=link,
                    summary=summary[:500],
                    image=_extract_rss_image(entry, summary),
                ))
    except Exception:
        log.exception("RSS error for %s", name)
    return deals


def _extract_rss_image(entry, summary: str) -> str:
    for media in entry.get("media_content", []):
        url = media.get("url", "")
        if url and _is_image_url(url):
            return url
    for thumb in entry.get("media_thumbnail", []):
        url = thumb.get("url", "")
        if url and _is_image_url(url):
            return url
    for enc in entry.get("enclosures", []):
        url = enc.get("href", enc.get("url", ""))
        if url and _is_image_url(url):
            return url
    if summary:
        soup = BeautifulSoup(summary, "html.parser")
        img = soup.find("img", src=True)
        if img:
            return img["src"]
    return ""


def _is_image_url(url: str) -> bool:
    low = url.lower().split("?")[0]
    return any(low.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"))


# ---------------------------------------------------------------------------
# Web scraping
# ---------------------------------------------------------------------------

def _fetch_single_web(target: dict) -> list[Deal]:
    deals = []
    try:
        session = _get_session()
        resp = session.get(target["url"], timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for card in soup.select(target["selector"])[:30]:
            title_el = card.select_one(target.get("title_sel", ""))
            price_el = card.select_one(target.get("price_sel", ""))
            title = title_el.get_text(strip=True) if title_el else ""
            price = price_el.get_text(strip=True) if price_el else ""

            link = card.get("href", "")
            if link and not link.startswith("http"):
                link = urljoin(target["url"], link)

            image = ""
            img_el = card.select_one("img[src]")
            if img_el:
                image = img_el.get("src", "") or img_el.get("data-src", "")
                if image and not image.startswith("http"):
                    image = urljoin(target["url"], image)

            if title and _matches_interest(f"{title} {price}"):
                deals.append(Deal(
                    source=target["name"], title=title,
                    url=link, price=price, image=image,
                ))
    except Exception:
        log.exception("Web scrape error for %s", target["name"])
    return deals


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

def _fetch_single_reddit(sub: str, opts: dict) -> list[Deal]:
    deals = []
    try:
        session = _get_session()
        sort = opts.get("sort", "hot")
        limit = opts.get("limit", 25)
        min_up = opts.get("min_upvotes", config.REDDIT_MIN_UPVOTES_DEFAULT)

        resp = session.get(
            f"https://www.reddit.com/r/{sub}/{sort}.json?limit={limit}&raw_json=1",
            headers={"User-Agent": config.REQUEST_HEADERS["User-Agent"]},
            timeout=config.REQUEST_TIMEOUT,
        )
        if resp.status_code == 429:
            log.warning("Reddit rate-limited on r/%s", sub)
            return deals
        resp.raise_for_status()

        for child in resp.json().get("data", {}).get("children", []):
            post = child.get("data", {})
            if post.get("stickied"):
                continue
            upvotes = post.get("ups", 0)
            if upvotes < min_up:
                continue

            title = post.get("title", "")
            selftext = post.get("selftext", "")[:500]
            flair = post.get("link_flair_text", "") or ""
            combined = f"{title} {selftext} {flair}"

            if _matches_interest(combined) or _is_trending(upvotes, post.get("num_comments", 0)):
                deals.append(Deal(
                    source=f"r/{sub}",
                    title=title,
                    url=f"https://reddit.com{post.get('permalink', '')}",
                    summary=selftext,
                    image=_extract_reddit_image(post),
                    upvotes=upvotes,
                    comments=post.get("num_comments", 0),
                    flair=flair,
                ))
    except Exception:
        log.exception("Reddit error for r/%s", sub)
    return deals


def _extract_reddit_image(post: dict) -> str:
    url = post.get("url_overridden_by_dest", post.get("url", ""))
    if url and any(h in url for h in ("i.redd.it", "i.imgur.com", "preview.redd.it")):
        return url
    if post.get("is_gallery"):
        for meta in post.get("media_metadata", {}).values():
            img = meta.get("s", {}).get("u", meta.get("s", {}).get("gif", ""))
            if img:
                return img.replace("&amp;", "&")
            break
    thumb = post.get("thumbnail", "")
    return thumb if thumb.startswith("http") else ""


def _is_trending(upvotes: int, comments: int) -> bool:
    return upvotes >= 200 or comments >= 50


# ---------------------------------------------------------------------------
# Combined — all sources in parallel
# ---------------------------------------------------------------------------

def _fetch_all_reddit() -> list[Deal]:
    """Reddit requests run sequentially to avoid rate-limit blocks."""
    deals: list[Deal] = []
    for sub, opts in config.REDDIT_SUBREDDITS.items():
        deals.extend(_fetch_single_reddit(sub, opts))
        time.sleep(config.REQUEST_DELAY)
    return deals


def fetch_all_deals() -> list[dict]:
    all_deals: list[Deal] = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = []

        for name, url in config.RSS_FEEDS.items():
            futures.append(pool.submit(_fetch_single_rss, name, url))
        for target in config.SCRAPE_TARGETS:
            futures.append(pool.submit(_fetch_single_web, target))
        # Reddit runs as one sequential batch in its own thread
        futures.append(pool.submit(_fetch_all_reddit))

        for f in as_completed(futures):
            try:
                all_deals.extend(f.result())
            except Exception:
                log.exception("Scraper thread failed")

    log.info("Total raw deals found: %d", len(all_deals))
    return [asdict(d) for d in all_deals]
