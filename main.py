#!/usr/bin/env python3
"""
HypeBot — 24/7 streetwear deals monitor.
Scrapes RSS feeds and web pages, analyzes deals with Ollama, and sends alerts.
"""

import logging
import sys
import time

import config
import database
import scraper
import analyzer
import alerts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("hypebot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("hypebot")


def run_cycle():
    """Single scrape → analyze → alert cycle."""
    log.info("Starting scan cycle...")
    raw_deals = scraper.fetch_all_deals()
    new_count = 0

    for deal in raw_deals:
        deal_id = database.deal_hash(deal["source"], deal["title"], deal["url"])

        if database.deal_exists(deal_id):
            continue

        # AI analysis (skip if Ollama is unreachable)
        ai_result = None
        if analyzer.health_check():
            ai_result = analyzer.analyze_deal(
                title=deal["title"],
                summary=deal.get("summary", ""),
                price=deal.get("price", ""),
            )

        database.save_deal(
            deal_id=deal_id,
            source=deal["source"],
            title=deal["title"],
            url=deal.get("url", ""),
            price=deal.get("price", ""),
            summary=deal.get("summary", ""),
            ai_analysis=str(ai_result) if ai_result else "",
        )

        should_alert = True
        if ai_result and ai_result.get("verdict") == "pass":
            hype = ai_result.get("hype_score", 5)
            if isinstance(hype, int) and hype < 4:
                should_alert = False

        if should_alert:
            alerts.send_alert(deal, ai_result)
            database.mark_alerted(deal_id)

        new_count += 1

    log.info("Cycle complete — %d new deals processed.", new_count)
    return new_count


def main():
    log.info("=" * 60)
    log.info("  HypeBot starting up")
    log.info("  Ollama:  %s  |  Model: %s", config.OLLAMA_HOST, config.MODEL)
    log.info("  Interval: %ds", config.SCRAPE_INTERVAL)
    log.info("=" * 60)

    database.init_db()

    if not analyzer.health_check():
        log.warning(
            "Ollama not reachable at %s — bot will run without AI analysis. "
            "Make sure Ollama is running and the model '%s' is pulled.",
            config.OLLAMA_HOST,
            config.MODEL,
        )

    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            log.info("Shutting down...")
            break
        except Exception:
            log.exception("Cycle error — will retry next interval")

        log.info("Sleeping %ds until next cycle...", config.SCRAPE_INTERVAL)
        time.sleep(config.SCRAPE_INTERVAL)


if __name__ == "__main__":
    main()
