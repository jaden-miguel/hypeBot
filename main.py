#!/usr/bin/env python3
"""
HypeBot v4 — 24/7 streetwear deals, drops & flip monitor.
Quality-scored alerts with resale profit estimation, price history,
restock detection, daily digest. Capped per cycle, graceful shutdown.
"""

import gc
import logging
import logging.handlers
import signal
import sys
import time
from datetime import datetime, timezone

import config
import database
import scraper
import analyzer
import alerts
import resale
import drops as drop_scraper

_shutdown = False

MAX_ALERTS_PER_CYCLE = 15
MIN_DEAL_DISCOUNT = 20
MIN_HYPE_TO_ALERT = 5


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
console = logging.StreamHandler(sys.stdout)
console.setFormatter(log_formatter)

file_handler = logging.handlers.RotatingFileHandler(
    "hypebot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
file_handler.setFormatter(log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[console, file_handler])
log = logging.getLogger("hypebot")

_PRICE_RE = __import__("re").compile(r"\$\s*([\d,]+(?:\.\d{2})?)")


def _extract_price(text: str) -> float:
    m = _PRICE_RE.search(text)
    return float(m.group(1).replace(",", "")) if m else 0.0


def _quality_score(deal: dict, ai: dict | None, flip: dict | None = None,
                   price_intel: dict | None = None) -> float:
    """Compute a quality score to rank deals. Higher = better deal.
    Flip potential + price intelligence are the heaviest weights."""
    score = 0.0
    disc = deal.get("discount_pct", 0) or 0
    score += disc * 2.0

    if flip:
        score += flip.get("flip_score", 0) * 2
        profit = flip.get("est_profit_low", 0)
        if profit > 0:
            score += min(profit, 200)

    if price_intel:
        if price_intel.get("is_lowest"):
            score += 50                          # new lowest price = big signal
        if price_intel.get("is_restock"):
            score += 80                          # restocks are highly flippable
        if price_intel.get("price_drop", 0) > 0:
            score += min(price_intel["price_drop"], 60)

    if ai:
        hype = ai.get("hype_score", 0)
        if isinstance(hype, int):
            score += hype * 5
        if ai.get("trending"):
            score += 20
        verdict = ai.get("verdict", "").lower()
        if verdict == "recommended":
            score += 30
        elif verdict == "watch":
            score += 10

    upvotes = deal.get("upvotes", 0)
    comments = deal.get("comments", 0)
    if upvotes:
        score += min(upvotes / 5, 30)
    if comments:
        score += min(comments / 3, 15)

    return score


def run_drop_check():
    """Scrape upcoming drops and fire time-based alerts with flip estimates."""
    t0 = time.monotonic()
    raw_drops = drop_scraper.fetch_upcoming_drops()

    added = 0
    for drop in raw_drops:
        if database.save_drop(drop):
            added += 1

    pending = database.get_drops_needing_notification()
    for drop in pending:
        if _shutdown:
            break
        tier = drop.pop("_notify_tier")
        flip_est = resale.estimate_resale({
            "title": drop.get("title", ""),
            "price": drop.get("price", ""),
            "summary": "",
        })
        alerts.send_drop_alert(drop, tier, flip_est)
        database.mark_drop_notified(drop["id"], tier)

    log.info(
        "Drop check done in %.1fs — %d new drops tracked, %d alerts fired",
        time.monotonic() - t0, added, len(pending),
    )


def run_cycle():
    t0 = time.monotonic()
    log.info("Scan cycle starting...")

    known_ids = database.load_known_ids()
    log.info("Loaded %d known deal IDs into memory", len(known_ids))

    raw_deals = scraper.fetch_all_deals()

    ollama_ok = analyzer.health_check()
    if not ollama_ok:
        log.warning("Ollama unreachable — skipping AI analysis this cycle")

    candidates: list[tuple[dict, dict | None, dict | None, dict | None, float]] = []
    new_count = 0
    current_titles: set[str] = set()

    for deal in raw_deals:
        if _shutdown:
            break

        current_titles.add(deal.get("title", ""))

        deal_id = database.deal_hash(deal["source"], deal["title"], deal["url"])
        if deal_id in known_ids:
            continue

        is_web_deal = (
            deal.get("price")
            and not deal.get("summary")
            and not deal.get("upvotes")
        )
        disc = deal.get("discount_pct", 0) or 0

        # Record price and check for price drops / restocks
        buy_price = _extract_price(deal.get("price", ""))
        price_intel = None
        if buy_price > 0:
            price_intel = database.record_price(
                title=deal.get("title", ""),
                source=deal.get("source", ""),
                price=buy_price,
                url=deal.get("url", ""),
                image=deal.get("image", ""),
            )

        ai_result = None
        if is_web_deal:
            if disc < MIN_DEAL_DISCOUNT:
                # Check if this is a restock of a previously gone item
                is_restock = price_intel and price_intel.get("is_restock")
                if not is_restock:
                    database.save_deal(
                        deal_id=deal_id, source=deal["source"],
                        title=deal["title"], url=deal.get("url", ""),
                        price=deal.get("price", ""),
                        summary="", ai_analysis="", image=deal.get("image", ""),
                    )
                    known_ids.add(deal_id)
                    new_count += 1
                    continue

            ai_result = {
                "verdict": "recommended",
                "brand": "",
                "hype_score": min(10, 5 + disc // 10),
                "trending": disc >= 30,
                "available_now": True,
                "summary": f"{disc}% off — sale price",
            }
        elif ollama_ok:
            ai_result = analyzer.analyze_deal(
                title=deal["title"],
                summary=deal.get("summary", ""),
                price=deal.get("price", ""),
                upvotes=deal.get("upvotes", 0),
                comments=deal.get("comments", 0),
                source=deal.get("source", ""),
                flair=deal.get("flair", ""),
            )

        database.save_deal(
            deal_id=deal_id, source=deal["source"],
            title=deal["title"], url=deal.get("url", ""),
            price=deal.get("price", ""), summary=deal.get("summary", ""),
            ai_analysis=str(ai_result) if ai_result else "",
            upvotes=deal.get("upvotes", 0),
            comments=deal.get("comments", 0),
            flair=deal.get("flair", ""), image=deal.get("image", ""),
        )
        known_ids.add(deal_id)
        new_count += 1

        flip_est = resale.estimate_resale(deal)

        # Quick-reject — but never reject strong flips, restocks, or lowest prices
        is_high_value = (
            flip_est.get("flip_score", 0) >= 50
            or (price_intel and price_intel.get("is_restock"))
            or (price_intel and price_intel.get("is_lowest") and buy_price > 0)
        )
        if ai_result and not is_high_value:
            verdict = ai_result.get("verdict", "").lower()
            available = ai_result.get("available_now", True)
            hype = ai_result.get("hype_score", 0)
            if not isinstance(hype, int):
                try:
                    hype = int(hype)
                except (TypeError, ValueError):
                    hype = 0

            if verdict == "skip" or not available:
                continue
            if verdict in ("watch", "maybe") and hype < MIN_HYPE_TO_ALERT:
                continue

        score = _quality_score(deal, ai_result, flip_est, price_intel)
        candidates.append((deal, ai_result, flip_est, price_intel, score))

    # Mark items not seen this cycle as gone (for restock detection next cycle)
    if current_titles:
        gone = database.mark_gone_items(current_titles)
        if gone:
            log.info("Marked %d items as gone (potential future restocks)", gone)

    # Phase 2: rank by quality and alert only the top deals
    candidates.sort(key=lambda c: c[4], reverse=True)
    alert_count = 0

    for deal, ai_result, flip_est, price_intel, score in candidates[:MAX_ALERTS_PER_CYCLE]:
        if _shutdown:
            break
        deal_id = database.deal_hash(deal["source"], deal["title"], deal["url"])
        alerts.send_alert(deal, ai_result, flip_est, price_intel)
        database.mark_alerted(deal_id)
        alert_count += 1

    if len(candidates) > MAX_ALERTS_PER_CYCLE:
        log.info(
            "Capped alerts: %d qualified, sent top %d (scores %.0f–%.0f)",
            len(candidates), alert_count,
            candidates[0][4] if candidates else 0,
            candidates[min(alert_count, len(candidates)) - 1][4] if candidates else 0,
        )

    elapsed = time.monotonic() - t0
    log.info(
        "Cycle done in %.1fs — %d new, %d alerted, %d total in DB",
        elapsed, new_count, alert_count, len(known_ids),
    )
    return alert_count


def run_daily_digest(cycle_count: int):
    """Send a daily summary of the best deals found in the last 24 hours."""
    if cycle_count % 8 != 0 or cycle_count == 0:
        return

    recent = database.get_recent_deals(limit=100)
    alerted = [d for d in recent if d.get("is_alerted")]

    if not alerted:
        return

    alerts.send_daily_digest(alerted[:10])
    log.info("Daily digest sent with %d deals", min(len(alerted), 10))


def main():
    log.info("=" * 60)
    log.info("  HypeBot v4 — deals, drops & flips 24/7")
    log.info("  Ollama:   %s  |  Model: %s", config.OLLAMA_HOST, config.MODEL)
    log.info("  Interval: %ds  |  Sources: %d RSS, %d web, %d Reddit",
             config.SCRAPE_INTERVAL,
             len(config.RSS_FEEDS),
             len(config.SCRAPE_TARGETS),
             len(config.REDDIT_SUBREDDITS))
    log.info("  Max alerts/cycle: %d  |  Min discount: %d%%",
             MAX_ALERTS_PER_CYCLE, MIN_DEAL_DISCOUNT)
    log.info("  Features: resale engine, price tracking, restock alerts")
    log.info("=" * 60)

    database.init_db()

    consecutive_errors = 0
    cycle_count = 0

    while not _shutdown:
        try:
            run_drop_check()
            alert_count = run_cycle()
            consecutive_errors = 0
            cycle_count += 1

            run_daily_digest(cycle_count)

            if cycle_count % 8 == 0:
                pruned_deals = database.prune_old_deals(days=30)
                pruned_drops = database.prune_old_drops(days=7)
                pruned_ph = database.prune_price_history(days=30)
                pruned_it = database.prune_item_tracker(days=30)
                total_pruned = pruned_deals + pruned_drops + pruned_ph + pruned_it
                if total_pruned:
                    log.info("Pruned %d deals, %d drops, %d prices, %d tracker entries",
                             pruned_deals, pruned_drops, pruned_ph, pruned_it)
                gc.collect()

        except Exception:
            consecutive_errors += 1
            backoff = min(60 * consecutive_errors, 600)
            log.exception(
                "Cycle error #%d — backing off %ds", consecutive_errors, backoff
            )
            time.sleep(backoff)
            continue

        if _shutdown:
            break

        log.info("Sleeping %ds until next cycle...", config.SCRAPE_INTERVAL)
        for _ in range(config.SCRAPE_INTERVAL):
            if _shutdown:
                break
            time.sleep(1)

    log.info("HypeBot shut down gracefully.")


if __name__ == "__main__":
    main()
