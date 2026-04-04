#!/usr/bin/env python3
"""
HypeBot — 24/7 streetwear deals & drops monitor.
Runs lean: quality-scored alerts, capped per cycle, graceful shutdown,
log rotation, batch dedup, auto-pruning, exponential backoff.
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


def _quality_score(deal: dict, ai: dict | None, flip: dict | None = None) -> float:
    """Compute a quality score to rank deals. Higher = better deal.
    Flip potential is the heaviest weight — money-making opportunities rank first."""
    score = 0.0
    disc = deal.get("discount_pct", 0) or 0
    score += disc * 2.0                         # 40% off → +80

    if flip:
        score += flip.get("flip_score", 0) * 2  # flip 70 → +140 (dominant factor)
        profit = flip.get("est_profit_low", 0)
        if profit > 0:
            score += min(profit, 200)            # $100 profit → +100 (capped 200)

    if ai:
        hype = ai.get("hype_score", 0)
        if isinstance(hype, int):
            score += hype * 5                   # hype 8 → +40
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
        score += min(upvotes / 5, 30)           # 150 upvotes → +30 (capped)
    if comments:
        score += min(comments / 3, 15)           # 45 comments → +15 (capped)

    return score


def run_drop_check():
    """Scrape upcoming drops and fire time-based alerts."""
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

    # Phase 1: dedup, classify, estimate resale, and score all deals
    # candidates: (deal, ai_result, flip_estimate, quality_score)
    candidates: list[tuple[dict, dict | None, dict | None, float]] = []
    new_count = 0

    for deal in raw_deals:
        if _shutdown:
            break

        deal_id = database.deal_hash(deal["source"], deal["title"], deal["url"])
        if deal_id in known_ids:
            continue

        is_web_deal = (
            deal.get("price")
            and not deal.get("summary")
            and not deal.get("upvotes")
        )
        disc = deal.get("discount_pct", 0) or 0

        ai_result = None
        if is_web_deal:
            if disc < MIN_DEAL_DISCOUNT:
                # Full-price or small discount — save silently, no alert
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

        # Resale profit estimation
        flip_est = resale.estimate_resale(deal)

        # Quick-reject before scoring — but never reject strong flips
        is_strong_flip = flip_est.get("flip_score", 0) >= 50
        if ai_result and not is_strong_flip:
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

        score = _quality_score(deal, ai_result, flip_est)
        candidates.append((deal, ai_result, flip_est, score))

    # Phase 2: rank by quality and alert only the top deals
    candidates.sort(key=lambda c: c[3], reverse=True)
    alert_count = 0

    for deal, ai_result, flip_est, score in candidates[:MAX_ALERTS_PER_CYCLE]:
        if _shutdown:
            break
        deal_id = database.deal_hash(deal["source"], deal["title"], deal["url"])
        alerts.send_alert(deal, ai_result, flip_est)
        database.mark_alerted(deal_id)
        alert_count += 1

    if len(candidates) > MAX_ALERTS_PER_CYCLE:
        log.info(
            "Capped alerts: %d qualified, sent top %d (scores %.0f–%.0f)",
            len(candidates), alert_count,
            candidates[0][3] if candidates else 0,
            candidates[min(alert_count, len(candidates)) - 1][3] if candidates else 0,
        )

    elapsed = time.monotonic() - t0
    log.info(
        "Cycle done in %.1fs — %d new, %d alerted, %d total in DB",
        elapsed, new_count, alert_count, len(known_ids),
    )
    return new_count


def main():
    log.info("=" * 60)
    log.info("  HypeBot v3 — lean 24/7 operation")
    log.info("  Ollama:   %s  |  Model: %s", config.OLLAMA_HOST, config.MODEL)
    log.info("  Interval: %ds  |  Sources: %d RSS, %d web, %d Reddit",
             config.SCRAPE_INTERVAL,
             len(config.RSS_FEEDS),
             len(config.SCRAPE_TARGETS),
             len(config.REDDIT_SUBREDDITS))
    log.info("  Max alerts/cycle: %d  |  Min deal discount: %d%%",
             MAX_ALERTS_PER_CYCLE, MIN_DEAL_DISCOUNT)
    log.info("=" * 60)

    database.init_db()

    consecutive_errors = 0
    cycle_count = 0
    cycle_start = time.monotonic()

    while not _shutdown:
        try:
            cycle_start = time.monotonic()
            run_drop_check()
            run_cycle()
            consecutive_errors = 0
            cycle_count += 1

            if cycle_count % 8 == 0:
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
