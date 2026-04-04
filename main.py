#!/usr/bin/env python3
"""
HypeBot v6 — 24/7 streetwear scalping machine.
Price error detection, smart timing, coupon stacking, low-stock urgency,
multi-store comparison, hidden clearance scraping. Finds deals before anyone.
"""

import gc
import logging
import logging.handlers
import signal
import sys
import time
from datetime import datetime, timezone, timedelta

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

# Peak drop hours (EST / UTC-5). Bot runs a quick extra scan during these.
_DROP_HOURS_UTC = [
    5,   # midnight EST — SNKRS surprise drops
    10,  # 5am EST — EU drops hit, early restocks
    14,  # 9am EST — brand sites update inventory
    15,  # 10am EST — Nike, most US drops go live
    16,  # 11am EST — secondary wave, Kith/Bodega/Concepts
]
_RAPID_SCAN_INTERVAL = 900  # 15 min between rapid scans during drop hours


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


def _is_drop_hour() -> bool:
    """Check if the current hour is a known peak drop window."""
    return datetime.now(timezone.utc).hour in _DROP_HOURS_UTC


def _find_cheapest_source(deals: list[dict]) -> dict[str, dict]:
    """Compare the same item across multiple stores.
    Returns a map of normalized title → {cheapest_price, cheapest_source, all_sources, savings}.
    """
    from scraper import _normalize_title
    price_map: dict[str, list[tuple[float, str, dict]]] = {}

    for deal in deals:
        title = _normalize_title(deal.get("title", ""))
        if not title or len(title) < 6:
            continue
        price = _extract_price(deal.get("price", ""))
        if price <= 0:
            continue
        if title not in price_map:
            price_map[title] = []
        price_map[title].append((price, deal.get("source", ""), deal))

    result = {}
    for title, entries in price_map.items():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda x: x[0])
        cheapest_price, cheapest_source, cheapest_deal = entries[0]
        highest_price = entries[-1][0]
        if highest_price > cheapest_price:
            savings = round(highest_price - cheapest_price, 2)
            result[title] = {
                "cheapest_price": cheapest_price,
                "cheapest_source": cheapest_source,
                "all_sources": [(p, s) for p, s, _ in entries],
                "savings": savings,
                "deal": cheapest_deal,
            }
    return result


def _quality_score(deal: dict, ai: dict | None, flip: dict | None = None,
                   price_intel: dict | None = None) -> float:
    """Compute a quality score to rank deals. Higher = better deal.
    Price errors get maximum priority. Then flips, restocks, lowest prices."""
    score = 0.0
    disc = deal.get("discount_pct", 0) or 0
    score += disc * 2.0

    if flip:
        score += flip.get("flip_score", 0) * 2
        profit = flip.get("est_profit_low", 0)
        if profit > 0:
            score += min(profit, 200)
        if flip.get("price_error"):
            score += 500  # price errors go straight to the top

    if price_intel:
        if price_intel.get("is_lowest"):
            score += 50
        if price_intel.get("is_restock"):
            score += 80
        if price_intel.get("price_drop", 0) > 0:
            score += min(price_intel["price_drop"], 60)

    # Multi-store cheapest bonus
    if deal.get("_cheapest_of"):
        savings = deal["_cheapest_of"].get("savings", 0)
        score += min(savings, 80)

    # Low stock urgency bonus
    flair = deal.get("flair", "").lower()
    if "low stock" in flair or "price error" in flair:
        score += 100
    if any(kw in flair for kw in ("just dropped", "live now", "hurry", "going fast")):
        score += 60

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

    cheapest_map = _find_cheapest_source(raw_deals)
    if cheapest_map:
        log.info("Multi-store comparison: %d items found cheaper at alt stores", len(cheapest_map))

    ollama_ok = analyzer.health_check()
    if not ollama_ok:
        log.warning("Ollama unreachable — skipping AI analysis this cycle")

    candidates: list[tuple[dict, dict | None, dict | None, dict | None, float]] = []
    new_count = 0
    current_titles: set[str] = set()

    # Analytics counters
    _cy_flips = 0
    _cy_restocks = 0
    _cy_lowest = 0
    _cy_profit = 0.0
    _cy_source_counts: dict[str, int] = {}

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

        # Track analytics
        src = deal.get("source", "unknown")
        _cy_source_counts[src] = _cy_source_counts.get(src, 0) + 1
        if flip_est.get("flip_score", 0) >= 40:
            _cy_flips += 1
        if flip_est.get("est_profit_low", 0) > 0:
            _cy_profit += flip_est["est_profit_low"]
        if price_intel and price_intel.get("is_restock"):
            _cy_restocks += 1
        if price_intel and price_intel.get("is_lowest") and buy_price > 0:
            _cy_lowest += 1

        if flip_est.get("price_error"):
            pe = flip_est["price_error"]
            log.warning("PRICE ERROR DETECTED: %s — $%.2f vs retail $%.0f (%d%% off)",
                        deal.get("title", ""), pe["paid"], pe["expected_retail"], pe["savings_pct"])

        # Tag multi-store cheapest
        from scraper import _normalize_title as _norm
        norm_title = _norm(deal.get("title", ""))
        store_cmp = cheapest_map.get(norm_title)
        if store_cmp and deal.get("source") == store_cmp["cheapest_source"]:
            deal["_cheapest_of"] = store_cmp

        # Quick-reject — but never reject strong flips, restocks, lowest prices, or price errors
        is_high_value = (
            flip_est.get("flip_score", 0) >= 50
            or (price_intel and price_intel.get("is_restock"))
            or (price_intel and price_intel.get("is_lowest") and buy_price > 0)
            or flip_est.get("price_error")
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
        _buffer_for_digest(deal, flip_est, price_intel)
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

    top_source = max(_cy_source_counts, key=_cy_source_counts.get) if _cy_source_counts else ""

    return {
        "duration_s": elapsed,
        "deals_scanned": len(raw_deals),
        "deals_new": new_count,
        "alerts_sent": alert_count,
        "flips_found": _cy_flips,
        "restocks_found": _cy_restocks,
        "lowest_prices": _cy_lowest,
        "total_est_profit": _cy_profit,
        "top_source": top_source,
        "is_rapid": _is_drop_hour(),
    }


_digest_buffer: list[dict] = []


def _buffer_for_digest(deal: dict, flip: dict | None, price_intel: dict | None):
    """Accumulate the best deals throughout cycles for the daily digest."""
    entry = {
        "title": deal.get("title", ""),
        "url": deal.get("url", ""),
        "price": deal.get("price", ""),
        "source": deal.get("source", ""),
    }
    if flip:
        entry["est_profit_low"] = flip.get("est_profit_low", 0)
        entry["est_profit_high"] = flip.get("est_profit_high", 0)
        entry["roi_pct"] = flip.get("roi_pct", 0)
    _digest_buffer.append(entry)


def run_daily_digest(cycle_count: int):
    """Send a daily summary of the best deals found in the last 24 hours."""
    global _digest_buffer
    if cycle_count % 8 != 0 or cycle_count == 0:
        return

    if not _digest_buffer:
        return

    sorted_buf = sorted(
        _digest_buffer,
        key=lambda d: d.get("est_profit_low", 0),
        reverse=True,
    )

    alerts.send_daily_digest(sorted_buf[:10])
    log.info("Daily digest sent with %d deals", min(len(sorted_buf), 10))
    _digest_buffer = []


def run_analytics_report():
    """Generate and send the full analytics report."""
    try:
        data = database.get_analytics(days=7)
        data["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        alerts.send_analytics_report(data)
        log.info("Analytics report sent (7-day)")
    except Exception:
        log.exception("Analytics report failed")


def main():
    log.info("=" * 60)
    log.info("  HypeBot v6 — streetwear scalping machine")
    log.info("  Ollama:   %s  |  Model: %s", config.OLLAMA_HOST, config.MODEL)
    log.info("  Interval: %ds  |  Rapid scan: %ds (drop hours)",
             config.SCRAPE_INTERVAL, _RAPID_SCAN_INTERVAL)
    log.info("  Sources: %d RSS, %d web, %d Reddit",
             len(config.RSS_FEEDS),
             len(config.SCRAPE_TARGETS),
             len(config.REDDIT_SUBREDDITS))
    log.info("  Max alerts/cycle: %d  |  Min discount: %d%%",
             MAX_ALERTS_PER_CYCLE, MIN_DEAL_DISCOUNT)
    log.info("  Edge: price errors, hidden clearance, smart timing,")
    log.info("        promo codes, low stock, multi-store comparison")
    log.info("  Analytics: cycle summaries, weekly reports, price trends")
    log.info("=" * 60)

    database.init_db()

    log.info("Sending money playbook to Telegram...")
    try:
        alerts.send_playbook()
    except Exception:
        log.exception("Playbook send failed — continuing")

    # Send startup analytics if any history exists
    try:
        data = database.get_analytics(days=7)
        if data.get("total_cycles", 0) > 0:
            data["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            alerts.send_analytics_report(data)
            log.info("Startup analytics report sent")
    except Exception:
        log.exception("Startup analytics failed — continuing")

    consecutive_errors = 0
    cycle_count = 0

    while not _shutdown:
        try:
            run_drop_check()
            cycle_stats = run_cycle()
            consecutive_errors = 0
            cycle_count += 1

            # Record analytics
            cycle_stats["cycle_num"] = cycle_count
            database.record_cycle_stats(cycle_stats)

            # Send compact cycle summary to Telegram
            alerts.send_cycle_summary(cycle_stats)

            run_daily_digest(cycle_count)

            # Send full analytics report every 24 cycles (~3 days at 3hr intervals, or weekly)
            if cycle_count % 24 == 0:
                run_analytics_report()

            if cycle_count % 8 == 0:
                pruned_deals = database.prune_old_deals(days=30)
                pruned_drops = database.prune_old_drops(days=7)
                pruned_ph = database.prune_price_history(days=30)
                pruned_it = database.prune_item_tracker(days=30)
                pruned_cs = database.prune_cycle_stats(days=90)
                total_pruned = pruned_deals + pruned_drops + pruned_ph + pruned_it + pruned_cs
                if total_pruned:
                    log.info("Pruned %d deals, %d drops, %d prices, %d tracker, %d stats",
                             pruned_deals, pruned_drops, pruned_ph, pruned_it, pruned_cs)
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

        # Smart timing: during peak drop hours, scan every 15 min instead of 3 hours
        if _is_drop_hour():
            sleep_time = _RAPID_SCAN_INTERVAL
            log.info("DROP HOUR detected — rapid scan mode, next scan in %ds", sleep_time)
        else:
            sleep_time = config.SCRAPE_INTERVAL

        log.info("Sleeping %ds until next cycle...", sleep_time)
        for _ in range(sleep_time):
            if _shutdown:
                break
            time.sleep(1)

    log.info("HypeBot shut down gracefully.")


if __name__ == "__main__":
    main()
