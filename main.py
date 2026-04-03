#!/usr/bin/env python3
"""
HypeBot — 24/7 streetwear deals monitor.
Optimized for long-running operation: graceful shutdown, log rotation,
batch dedup, auto-pruning, and exponential backoff on errors.
"""

import gc
import logging
import logging.handlers
import signal
import sys
import time

import config
import database
import scraper
import analyzer
import alerts
import drops as drop_scraper

_shutdown = False


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


def run_drop_check():
    """Scrape upcoming drops and fire time-based alerts."""
    t0 = time.monotonic()
    raw_drops = drop_scraper.fetch_upcoming_drops()

    added = 0
    for drop in raw_drops:
        if database.save_drop(drop):
            added += 1

    # Fire alerts for drops hitting their notification tier
    pending = database.get_drops_needing_notification()
    for drop in pending:
        if _shutdown:
            break
        tier = drop.pop("_notify_tier")
        alerts.send_drop_alert(drop, tier)
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

    new_count = 0
    alert_count = 0

    for deal in raw_deals:
        if _shutdown:
            break

        deal_id = database.deal_hash(deal["source"], deal["title"], deal["url"])
        if deal_id in known_ids:
            continue

        ai_result = None
        if ollama_ok:
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
            deal_id=deal_id,
            source=deal["source"],
            title=deal["title"],
            url=deal.get("url", ""),
            price=deal.get("price", ""),
            summary=deal.get("summary", ""),
            ai_analysis=str(ai_result) if ai_result else "",
            upvotes=deal.get("upvotes", 0),
            comments=deal.get("comments", 0),
            flair=deal.get("flair", ""),
            image=deal.get("image", ""),
        )
        known_ids.add(deal_id)

        should_alert = True
        if ai_result:
            verdict = ai_result.get("verdict", "").lower()
            hype = ai_result.get("hype_score", 5)
            available_now = ai_result.get("available_now", True)
            if not isinstance(hype, int):
                try:
                    hype = int(hype)
                except (TypeError, ValueError):
                    hype = 5
            if verdict == "skip":
                should_alert = False
            elif not available_now:
                should_alert = False
            elif verdict in ("watch", "maybe") and hype < 4:
                should_alert = False

        if should_alert:
            alerts.send_alert(deal, ai_result)
            database.mark_alerted(deal_id)
            alert_count += 1

        new_count += 1

    elapsed = time.monotonic() - t0
    log.info(
        "Cycle done in %.1fs — %d new, %d alerted, %d total in DB",
        elapsed, new_count, alert_count, len(known_ids),
    )
    return new_count


def main():
    log.info("=" * 60)
    log.info("  HypeBot v2 — optimized for 24/7")
    log.info("  Ollama:   %s  |  Model: %s", config.OLLAMA_HOST, config.MODEL)
    log.info("  Interval: %ds  |  Sources: %d RSS, %d web, %d Reddit",
             config.SCRAPE_INTERVAL,
             len(config.RSS_FEEDS),
             len(config.SCRAPE_TARGETS),
             len(config.REDDIT_SUBREDDITS))
    log.info("=" * 60)

    database.init_db()

    consecutive_errors = 0
    cycle_count = 0

    while not _shutdown:
        try:
            run_drop_check()
            run_cycle()
            consecutive_errors = 0
            cycle_count += 1

            if cycle_count % 8 == 0:  # ~every 24h at 3h intervals
                pruned_deals = database.prune_old_deals(days=30)
                pruned_drops = database.prune_old_drops(days=7)
                if pruned_deals or pruned_drops:
                    log.info("Pruned %d old deals, %d old drops", pruned_deals, pruned_drops)
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
