"""
Alert module — Telegram (HTML), Discord, email, console.
Rate-limited to avoid API throttling during 24/7 operation.
"""

import html
import logging
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

import config

log = logging.getLogger(__name__)

_last_tg_send = 0.0
_TG_MIN_INTERVAL = 1.5  # seconds between Telegram API calls

VERDICT_LABEL = {
    "RECOMMENDED": "Recommended",
    "SKIP":        "Skip",
    "WATCH":       "Watch List",
}
VERDICT_EMOJI = {
    "RECOMMENDED": "✅",
    "SKIP":        "⛔",
    "WATCH":       "👀",
}
VERDICT_COLOR = {
    "RECOMMENDED": 0x00C853,
    "SKIP":        0xB0BEC5,
    "WATCH":       0xFF9800,
}
SOURCE_EMOJI = {
    "hypebeast":      "👾",
    "highsnobiety":   "🎨",
    "sneakernews":    "👟",
    "complexsneakers":"🧢",
    "grailed_blog":   "🏷",
}


def send_alert(deal: dict, analysis: dict | None = None,
               flip: dict | None = None, price_intel: dict | None = None):
    msg = _format_message(deal, analysis, flip, price_intel)
    log.info("ALERT: %s", msg["title"])
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        _send_telegram(msg)
    if config.DISCORD_WEBHOOK_URL:
        _send_discord(msg)
    if config.ALERT_EMAIL_TO and config.SMTP_USER:
        _send_email(msg)
    _log_to_console(msg)


def _format_message(deal: dict, analysis: dict | None,
                    flip: dict | None = None, price_intel: dict | None = None) -> dict:
    verdict_key = ""
    hype_score  = 0
    ai_summary  = ""
    trending    = False

    if analysis:
        verdict_key = analysis.get("verdict", "").upper()
        hype_score  = analysis.get("hype_score", 0)
        ai_summary  = analysis.get("summary", "")
        trending    = bool(analysis.get("trending", False))

    discount_pct   = deal.get("discount_pct", 0) or 0
    original_price = deal.get("original_price", "") or ""

    flip_score      = 0
    flip_verdict    = ""
    est_profit_low  = 0.0
    est_profit_high = 0.0
    est_resale_low  = 0.0
    est_resale_high = 0.0
    flip_signals    = []

    roi_pct         = 0
    platforms       = []
    urgency         = {}
    price_error     = None

    if flip:
        flip_score      = flip.get("flip_score", 0)
        flip_verdict    = flip.get("flip_verdict", "")
        est_profit_low  = flip.get("est_profit_low", 0)
        est_profit_high = flip.get("est_profit_high", 0)
        est_resale_low  = flip.get("est_resale_low", 0)
        est_resale_high = flip.get("est_resale_high", 0)
        flip_signals    = flip.get("signals", [])
        roi_pct         = flip.get("roi_pct", 0)
        platforms       = flip.get("platforms", [])
        urgency         = flip.get("urgency", {})
        price_error     = flip.get("price_error")

    is_lowest  = False
    is_restock = False
    price_drop = 0.0

    if price_intel:
        is_lowest  = bool(price_intel.get("is_lowest"))
        is_restock = bool(price_intel.get("is_restock"))
        price_drop = price_intel.get("price_drop", 0)

    return {
        "title":          deal.get("title", "Unknown Deal"),
        "url":            deal.get("url", ""),
        "price":          deal.get("price", "") or "",
        "original_price": original_price,
        "discount_pct":   discount_pct,
        "source":         deal.get("source", ""),
        "image":          deal.get("image", ""),
        "upvotes":        deal.get("upvotes", 0),
        "comments":       deal.get("comments", 0),
        "flair":          deal.get("flair", ""),
        "verdict":        verdict_key,
        "hype_score":     hype_score,
        "ai_summary":     ai_summary,
        "trending":       trending,
        "flip_score":     flip_score,
        "flip_verdict":   flip_verdict,
        "est_profit_low": est_profit_low,
        "est_profit_high": est_profit_high,
        "est_resale_low": est_resale_low,
        "est_resale_high": est_resale_high,
        "flip_signals":   flip_signals,
        "roi_pct":        roi_pct,
        "platforms":      platforms,
        "urgency":        urgency,
        "price_error":    price_error,
        "cheapest_of":    deal.get("_cheapest_of"),
        "is_lowest":      is_lowest,
        "is_restock":     is_restock,
        "price_drop":     price_drop,
    }


# ---------------------------------------------------------------------------
# Telegram — HTML mode (no slash spam, reliable formatting)
# ---------------------------------------------------------------------------

def _h(text: str) -> str:
    """Escape text for Telegram HTML mode."""
    return html.escape(str(text), quote=False)


def _hype_bar(score: int) -> str:
    """Visual hype meter — e.g. score 7 → ██████░░░░ 7/10"""
    if not isinstance(score, int) or score < 1:
        return ""
    filled = "█" * score
    empty  = "░" * (10 - score)
    return f"{filled}{empty} {score}/10"


_FLIP_BADGE = {
    "strong flip":  ("💰🔥", "STRONG FLIP"),
    "possible flip": ("💰", "POSSIBLE FLIP"),
    "hold value":   ("📈", "HOLDS VALUE"),
}


_URGENCY_EMOJI = {
    "critical": "🔴",
    "high":     "🟠",
    "medium":   "🟡",
    "low":      "🟢",
}


def _build_telegram_html(msg: dict) -> str:
    e = _h

    disc      = msg.get("discount_pct", 0)
    flip_v    = msg.get("flip_verdict", "")
    flip_s    = msg.get("flip_score", 0)
    roi       = msg.get("roi_pct", 0)
    profit_lo = msg.get("est_profit_low", 0)
    profit_hi = msg.get("est_profit_high", 0)
    resale_lo = msg.get("est_resale_low", 0)
    resale_hi = msg.get("est_resale_high", 0)
    platforms = msg.get("platforms", [])
    urgency   = msg.get("urgency", {})
    is_flip   = flip_v in _FLIP_BADGE and profit_lo > 0

    pe        = msg.get("price_error")
    cheapest  = msg.get("cheapest_of")

    lines = []

    # ── PRICE ERROR BANNER (highest priority) ──
    if pe:
        lines.append(f"🚨🚨  <b>POSSIBLE PRICE ERROR</b>  🚨🚨")
        lines.append(f"Listed at <b>${pe['paid']:.0f}</b> — retail is <b>${pe['expected_retail']:.0f}</b> ({pe['savings_pct']}% off)")
        lines.append("<b>BUY IMMEDIATELY before they fix it!</b>")
        lines.append("")

    # ── URGENCY BANNER ──
    elif urgency and urgency.get("level") in ("critical", "high"):
        u_emoji = _URGENCY_EMOJI.get(urgency["level"], "")
        lines.append(f'{u_emoji} <b>{urgency.get("label", "")}</b> — {e(urgency.get("reason", ""))}')
        lines.append("")

    # ── RESTOCK / LOWEST PRICE special banners ──
    if msg.get("is_restock"):
        lines.append("🔄  <b>RESTOCK — was sold out, back in stock!</b>")
        lines.append("")
    if msg.get("is_lowest") and msg.get("price_drop", 0) > 0:
        lines.append(f"📉  <b>LOWEST PRICE — ${msg['price_drop']:.0f} below previous low</b>")
        lines.append("")

    # ── CHEAPEST SOURCE banner ──
    if cheapest and cheapest.get("savings", 0) >= 5:
        other_prices = ", ".join(
            f"${p:.0f} ({s})" for p, s in cheapest.get("all_sources", [])[1:]
        )
        lines.append(f"🏆  <b>CHEAPEST SOURCE</b> — ${cheapest['savings']:.0f} less than {other_prices}")
        lines.append("")

    # ── TITLE ──
    if msg["url"]:
        lines.append(f'<b><a href="{e(msg["url"])}">{e(msg["title"])}</a></b>')
    else:
        lines.append(f"<b>{e(msg['title'])}</b>")
    flair_text = msg.get("flair", "")
    if flair_text:
        if flair_text.upper().startswith("CODE:"):
            lines.append(f"🎟  <b>{e(flair_text)}</b>")
        elif flair_text.upper() == "LOW STOCK":
            lines.append("⚠️  <b>LOW STOCK — limited sizes remaining</b>")
        else:
            lines.append(f'<i>{e(flair_text)}</i>')
    lines.append("")

    # ── ACTION PLAN (the money part) ──
    if is_flip:
        lines.append("━━━ 💵 <b>ACTION PLAN</b> ━━━")
        lines.append("")

        # Step 1: BUY
        if msg["price"]:
            orig = msg.get("original_price", "")
            source_name = msg.get("source", "").replace("_", " ").title()
            if orig and disc:
                lines.append(f"1️⃣  <b>BUY</b> at {e(source_name)} for <b>{e(msg['price'])}</b>  <s>{e(orig)}</s>  ({disc}% off)")
            else:
                lines.append(f"1️⃣  <b>BUY</b> at {e(source_name)} for <b>{e(msg['price'])}</b>")

        # Step 2: SELL
        if platforms:
            plat_names = " / ".join(p["name"] for p in platforms[:3])
            lines.append(f"2️⃣  <b>SELL</b> on {e(plat_names)} for <b>${resale_lo:.0f}–${resale_hi:.0f}</b>")

        # Step 3: PROFIT
        lines.append(f"3️⃣  <b>PROFIT: +${profit_lo:.0f}–${profit_hi:.0f}</b>  ({roi}% ROI)")
        lines.append("")

        # Platform breakdown
        if platforms:
            lines.append("📱  <b>Where to sell:</b>")
            for p in platforms[:3]:
                lines.append(f"  • <b>{e(p['name'])}</b> ({p['fee_pct']}% fee) — {e(p['why'])}")
            lines.append("")

    else:
        # Non-flip deal — still show price info
        if msg["price"]:
            orig = msg.get("original_price", "")
            if orig and disc:
                lines.append(f"💰  <b>{e(msg['price'])}</b>  <s>{e(orig)}</s>  ({disc}% off)")
            else:
                lines.append(f"💰  <b>{e(msg['price'])}</b>")
        if resale_lo > 0 and flip_s >= 20:
            lines.append(f"📊  Resale est: <b>${resale_lo:.0f}–${resale_hi:.0f}</b>")
        lines.append("")

    # ── Community / source signals ──
    src_emoji = SOURCE_EMOJI.get(msg["source"], "📡")
    if msg["upvotes"] or msg["comments"]:
        lines.append(f"⬆️  {msg['upvotes']:,} upvotes   💬 {msg['comments']:,} comments")
    if msg["trending"]:
        lines.append("🚀  <b>Trending Now</b>")

    # ── Hype bar ──
    hype = msg.get("hype_score", 0)
    if isinstance(hype, int) and hype > 0:
        lines.append(f"🔥  {_hype_bar(hype)}")

    return "\n".join(lines)


def _tg_throttle():
    """Wait if needed to respect Telegram rate limits."""
    global _last_tg_send
    elapsed = time.monotonic() - _last_tg_send
    if elapsed < _TG_MIN_INTERVAL:
        time.sleep(_TG_MIN_INTERVAL - elapsed)
    _last_tg_send = time.monotonic()


def _send_telegram(msg: dict):
    caption = _build_telegram_html(msg)
    base    = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

    if len(caption) > 1020:
        caption = caption[:1017] + "..."

    _tg_throttle()

    try:
        resp = None

        if msg.get("image"):
            resp = requests.post(
                f"{base}/sendPhoto",
                json={
                    "chat_id":    config.TELEGRAM_CHAT_ID,
                    "photo":      msg["image"],
                    "caption":    caption,
                    "parse_mode": "HTML",
                },
                timeout=15,
            )
            if not resp.ok:
                log.warning("sendPhoto failed (%s) — falling back to sendMessage", resp.status_code)
                resp = None

        if resp is None or not resp.ok:
            resp = requests.post(
                f"{base}/sendMessage",
                json={
                    "chat_id":                  config.TELEGRAM_CHAT_ID,
                    "text":                     caption,
                    "parse_mode":               "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=10,
            )

        if resp.ok:
            log.info("Telegram alert sent: %s", msg["title"])
        else:
            log.error("Telegram API error %s: %s", resp.status_code, resp.text[:200])

    except Exception:
        log.exception("Telegram alert failed")


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def _send_discord(msg: dict):
    disc      = msg.get("discount_pct", 0)
    flip_v    = msg.get("flip_verdict", "")
    flip_s    = msg.get("flip_score", 0)
    profit_lo = msg.get("est_profit_low", 0)
    profit_hi = msg.get("est_profit_high", 0)
    resale_lo = msg.get("est_resale_low", 0)
    resale_hi = msg.get("est_resale_high", 0)
    roi       = msg.get("roi_pct", 0)
    platforms = msg.get("platforms", [])
    urgency   = msg.get("urgency", {})
    is_flip   = flip_v in _FLIP_BADGE and profit_lo > 0

    if is_flip:
        title_prefix = f"💰 +${profit_lo:.0f}–${profit_hi:.0f} PROFIT — "
    elif disc >= 15:
        title_prefix = f"🏷 {disc}% OFF — "
    else:
        title_prefix = ""

    if flip_s >= 65:
        color = 0xFFD700
    elif flip_s >= 40:
        color = 0xFFA000
    elif disc >= 30:
        color = 0xFF1744
    else:
        verdict = msg["verdict"]
        color = VERDICT_COLOR.get(verdict, 0x7C4DFF)

    description_parts = []
    if urgency and urgency.get("level") in ("critical", "high"):
        description_parts.append(f"**{urgency.get('label', '')}** — {urgency.get('reason', '')}")
    if msg["ai_summary"]:
        description_parts.append(msg["ai_summary"][:200])

    embed = {
        "title":       f"{title_prefix}{msg['title']}"[:256],
        "url":         msg["url"] or None,
        "color":       color,
        "description": "\n".join(description_parts) if description_parts else None,
        "fields":      [],
        "footer": {"text": "HypeBot • Streetwear Money Machine"},
    }

    # Action plan fields
    if is_flip:
        source_name = msg.get("source", "").replace("_", " ").title()
        price_display = msg["price"] or "N/A"
        orig = msg.get("original_price", "")
        if orig and disc:
            price_display = f"{msg['price']}  ~~{orig}~~  ({disc}% off)"

        embed["fields"].append({
            "name": "1️⃣ BUY", "value": f"{price_display}\nat {source_name}", "inline": True,
        })
        if platforms:
            plat_list = "\n".join(f"• {p['name']} ({p['fee_pct']}% fee)" for p in platforms[:3])
            embed["fields"].append({
                "name": f"2️⃣ SELL (${resale_lo:.0f}–${resale_hi:.0f})",
                "value": plat_list, "inline": True,
            })
        embed["fields"].append({
            "name": "3️⃣ PROFIT",
            "value": f"**+${profit_lo:.0f}–${profit_hi:.0f}**\n({roi}% ROI)", "inline": True,
        })
    else:
        price_display = msg["price"] or "N/A"
        orig = msg.get("original_price", "")
        if orig and disc:
            price_display = f"{msg['price']}  ~~{orig}~~  ({disc}% off)"
        embed["fields"].append({"name": "Price", "value": price_display, "inline": True})
        embed["fields"].append({"name": "Source", "value": msg["source"], "inline": True})
        if resale_lo > 0 and flip_s >= 20:
            embed["fields"].append({
                "name": "📊 Resale Est.", "value": f"${resale_lo:.0f}–${resale_hi:.0f}", "inline": True,
            })

    if msg["upvotes"] or msg["comments"]:
        embed["fields"].append({
            "name": "Community",
            "value": f"⬆️ {msg['upvotes']:,}  💬 {msg['comments']:,}", "inline": True,
        })
    if msg.get("image"):
        embed["image"] = {"url": msg["image"]}

    try:
        requests.post(
            config.DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=10,
        )
        log.info("Discord alert sent: %s", msg["title"])
    except Exception:
        log.exception("Discord alert failed")


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _send_email(msg: dict):
    body = "\n".join([
        f"Deal: {msg['title']}",
        f"Price: {msg['price'] or 'N/A'}",
        f"Source: {msg['source']}",
        f"Link: {msg['url']}",
        "",
        f"Verdict: {msg['verdict']}  (Hype {msg['hype_score']}/10)",
        f"{msg['ai_summary']}",
    ])
    em = MIMEMultipart()
    em["From"]    = config.ALERT_EMAIL_FROM
    em["To"]      = config.ALERT_EMAIL_TO
    v_label = VERDICT_LABEL.get(msg["verdict"], msg["verdict"])
    em["Subject"] = f"[HypeBot] {v_label} — {msg['title']}"
    em.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as s:
            s.starttls()
            s.login(config.SMTP_USER, config.SMTP_PASS)
            s.send_message(em)
        log.info("Email alert sent: %s", msg["title"])
    except Exception:
        log.exception("Email alert failed")


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------

def _log_to_console(msg: dict):
    disc      = msg.get("discount_pct", 0)
    flip_v    = msg.get("flip_verdict", "")
    flip_s    = msg.get("flip_score", 0)
    roi       = msg.get("roi_pct", 0)
    profit_lo = msg.get("est_profit_low", 0)
    profit_hi = msg.get("est_profit_high", 0)
    resale_lo = msg.get("est_resale_low", 0)
    resale_hi = msg.get("est_resale_high", 0)
    platforms = msg.get("platforms", [])
    urgency   = msg.get("urgency", {})
    pe        = msg.get("price_error")
    cheapest  = msg.get("cheapest_of")
    is_flip   = flip_v in _FLIP_BADGE and profit_lo > 0

    sep = "─" * 62
    print(f"\n{sep}")

    if pe:
        print(f"  🚨🚨 POSSIBLE PRICE ERROR — ${pe['paid']:.0f} vs retail ${pe['expected_retail']:.0f} ({pe['savings_pct']}% off)")
    elif urgency and urgency.get("level") in ("critical", "high"):
        print(f"  {_URGENCY_EMOJI.get(urgency['level'], '')} {urgency.get('label', '')} — {urgency.get('reason', '')}")

    if msg.get("is_restock"):
        print("  🔄  RESTOCK — was sold out, back in stock!")
    if msg.get("is_lowest") and msg.get("price_drop", 0) > 0:
        print(f"  📉  LOWEST PRICE — ${msg['price_drop']:.0f} below previous low")
    if cheapest and cheapest.get("savings", 0) >= 5:
        print(f"  🏆  CHEAPEST SOURCE — ${cheapest['savings']:.0f} less than other stores")

    flair_text = msg.get("flair", "")
    if flair_text and flair_text.upper().startswith("CODE:"):
        print(f"  🎟  {flair_text}")
    if flair_text and flair_text.upper() == "LOW STOCK":
        print(f"  ⚠️  LOW STOCK — limited sizes remaining")

    print(f"  {msg['title']}")

    if is_flip:
        print(f"  ━━━ ACTION PLAN ━━━")
        source_name = msg.get("source", "").replace("_", " ").title()
        if msg["price"]:
            orig = msg.get("original_price", "")
            if orig and disc:
                print(f"  1. BUY at {source_name} for {msg['price']}  (was {orig}, {disc}% off)")
            else:
                print(f"  1. BUY at {source_name} for {msg['price']}")
        if platforms:
            plat_names = " / ".join(p["name"] for p in platforms[:3])
            print(f"  2. SELL on {plat_names} for ${resale_lo:.0f}–${resale_hi:.0f}")
        print(f"  3. PROFIT: +${profit_lo:.0f}–${profit_hi:.0f}  ({roi}% ROI)")
    else:
        if msg["price"]:
            orig = msg.get("original_price", "")
            if orig and disc:
                print(f"  💰 {msg['price']}  (was {orig}, {disc}% off)")
            else:
                print(f"  💰 {msg['price']}")
        if resale_lo > 0 and flip_s >= 20:
            print(f"  📊 Resale est: ${resale_lo:.0f}–${resale_hi:.0f}")

    print(f"  📡 {msg['source']}")
    if msg["upvotes"] or msg["comments"]:
        print(f"  ⬆️  {msg['upvotes']:,} upvotes   💬 {msg['comments']:,} comments")

    hype = msg.get("hype_score", 0)
    if isinstance(hype, int) and hype > 0:
        verdict = msg["verdict"]
        v_emoji = VERDICT_EMOJI.get(verdict, "💡")
        v_label = VERDICT_LABEL.get(verdict, verdict.title() if verdict else "")
        print(f"  {v_emoji} {v_label}   {_hype_bar(hype)}")

    print(f"  🔗 {msg['url']}")
    print(sep)


# ===========================================================================
# STARTUP PLAYBOOK — one-time guide sent when bot starts
# ===========================================================================

_PLAYBOOK_HTML = """
💵  <b>HYPEBOT v6 — MONEY PLAYBOOK</b>

Your bot is scanning 24/7 with edge-finding tech.
Here's how to use each alert to make money:

━━━ <b>ALERT PRIORITY</b> ━━━

🚨🚨 <b>PRICE ERRORS</b> — #1 money maker
Store listed item way below retail.
BUY INSTANTLY — they fix these in minutes.

💰🔥 <b>STRONG FLIP</b> — 3-step action plan
BUY → SELL → PROFIT with ROI shown.

🔄 <b>RESTOCK</b> — sold out, now back
Previously gone items return. Act fast.

📉 <b>LOWEST PRICE</b> — cheapest ever seen
Bot tracks prices across cycles.

🏆 <b>CHEAPEST SOURCE</b> — multi-store scan
Same item, found cheaper than other stores.

🎟 <b>PROMO CODES</b> — auto-detected
Stackable coupons from Reddit. Extra margin.

⚠️ <b>LOW STOCK</b> — about to sell out
Limited sizes remaining.

━━━ <b>WHERE TO SELL</b> ━━━

👟 StockX — sneakers (10% fee)
👟 GOAT — sneakers, new + used (10%)
👕 Grailed — designer streetwear (9%)
🛒 eBay — everything, biggest pool (13%)

━━━ <b>SMART TIMING</b> ━━━

Bot runs rapid scans (every 15 min) during:
• 10am EST — Nike / most US drops
• Midnight EST — SNKRS surprise drops
• Early AM — EU restocks hit US

Normal: every 3 hours.

━━━ <b>QUICK START</b> ━━━

1. Create accounts: StockX + GOAT + eBay
2. 🚨 Price Error → buy IMMEDIATELY
3. 💰🔥 Strong Flip → follow action plan
4. List within 24hrs on recommended platform
5. Track profit in a spreadsheet

<i>Scanning {sources} sources now!</i>
""".strip()


def send_playbook():
    """Send the one-time money playbook via Telegram on startup."""
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return

    total_sources = (len(config.RSS_FEEDS) + len(config.SCRAPE_TARGETS)
                     + len(config.REDDIT_SUBREDDITS))
    text = _PLAYBOOK_HTML.replace("{sources}", str(total_sources))

    _tg_throttle()

    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
    try:
        resp = requests.post(
            f"{base}/sendMessage",
            json={
                "chat_id":                  config.TELEGRAM_CHAT_ID,
                "text":                     text,
                "parse_mode":               "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.ok:
            log.info("Playbook sent to Telegram")
        else:
            log.error("Playbook send error %s: %s", resp.status_code, resp.text[:200])
    except Exception:
        log.exception("Playbook send failed")


# ===========================================================================
# DROP ALERTS — upcoming release notifications
# ===========================================================================

_TIER_LABEL = {
    "today": ("🚨", "DROPPING TODAY"),
    "1day":  ("⏰", "DROPPING TOMORROW"),
    "7day":  ("📅", "DROPPING THIS WEEK"),
}


def send_drop_alert(drop: dict, tier: str, flip: dict | None = None):
    """Send an upcoming release alert for 'today', '1day', or '7day' tiers."""
    log.info("DROP ALERT [%s]: %s", tier, drop.get("title", ""))
    _send_drop_telegram(drop, tier, flip)
    if config.DISCORD_WEBHOOK_URL:
        _send_drop_discord(drop, tier, flip)
    _log_drop_console(drop, tier, flip)


def _send_drop_telegram(drop: dict, tier: str, flip: dict | None = None):
    e = _h
    t_emoji, t_label = _TIER_LABEL.get(tier, ("📅", "UPCOMING DROP"))
    release_label = drop.get("release_label") or drop.get("release_dt", "")[:10]

    lines = [
        f"{t_emoji} <b>{t_label}</b>",
        "",
    ]
    if drop.get("url"):
        lines.append(f'<b><a href="{e(drop["url"])}">{e(drop["title"])}</a></b>')
    else:
        lines.append(f"<b>{e(drop['title'])}</b>")

    if drop.get("brand"):
        lines.append(f"<i>{e(drop['brand'])}</i>")

    lines.append("")
    lines.append(f"🗓  <b>{e(release_label)}</b>")

    if drop.get("price"):
        lines.append(f"💰  <b>{e(drop['price'])}</b>")

    if flip and flip.get("flip_score", 0) >= 30:
        profit_lo = flip.get("est_profit_low", 0)
        profit_hi = flip.get("est_profit_high", 0)
        roi = flip.get("roi_pct", 0)
        resale_lo = flip.get("est_resale_low", 0)
        resale_hi = flip.get("est_resale_high", 0)
        plats = flip.get("platforms", [])

        if profit_lo > 0:
            lines.append("")
            lines.append("━━━ 💵 <b>FLIP PLAN</b> ━━━")
            lines.append(f"📈  Resale est: <b>${resale_lo:.0f}–${resale_hi:.0f}</b>")
            lines.append(f"💰  Potential profit: <b>+${profit_lo:.0f}–${profit_hi:.0f}</b>  ({roi}% ROI)")
            if plats:
                plat_names = " / ".join(p["name"] for p in plats[:3])
                lines.append(f"📱  Sell on: <b>{e(plat_names)}</b>")

        urgency_d = flip.get("urgency", {})
        if urgency_d and urgency_d.get("level") in ("critical", "high"):
            u_emoji = _URGENCY_EMOJI.get(urgency_d["level"], "")
            lines.append(f'{u_emoji}  <b>{urgency_d.get("label", "")}</b>')

    lines.append("")
    lines.append(f"📡  {e(drop.get('source', ''))}")

    caption = "\n".join(lines)
    if len(caption) > 1020:
        caption = caption[:1017] + "..."

    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

    _tg_throttle()

    try:
        resp = None
        if drop.get("image"):
            resp = requests.post(
                f"{base}/sendPhoto",
                json={
                    "chat_id":    config.TELEGRAM_CHAT_ID,
                    "photo":      drop["image"],
                    "caption":    caption,
                    "parse_mode": "HTML",
                },
                timeout=15,
            )
            if not resp.ok:
                resp = None

        if resp is None or not resp.ok:
            resp = requests.post(
                f"{base}/sendMessage",
                json={
                    "chat_id":                  config.TELEGRAM_CHAT_ID,
                    "text":                     caption,
                    "parse_mode":               "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=10,
            )

        if resp.ok:
            log.info("Drop Telegram alert sent: %s", drop.get("title", ""))
        else:
            log.error("Drop Telegram error %s: %s", resp.status_code, resp.text[:200])
    except Exception:
        log.exception("Drop Telegram alert failed")


def _send_drop_discord(drop: dict, tier: str, flip: dict | None = None):
    t_emoji, t_label = _TIER_LABEL.get(tier, ("📅", "UPCOMING DROP"))
    release_label = drop.get("release_label") or drop.get("release_dt", "")[:10]
    color = {"today": 0xFF1744, "1day": 0xFF9800, "7day": 0x2196F3}.get(tier, 0x9C27B0)

    embed = {
        "title":       f"{t_emoji} {t_label}: {drop['title'][:200]}",
        "url":         drop.get("url") or None,
        "color":       color,
        "fields": [
            {"name": "Release Date", "value": release_label,               "inline": True},
            {"name": "Retail Price", "value": drop.get("price") or "TBD",  "inline": True},
            {"name": "Brand",        "value": drop.get("brand") or "N/A",  "inline": True},
            {"name": "Source",       "value": drop.get("source", ""),      "inline": True},
        ],
        "footer": {"text": "HypeBot • Release Calendar"},
    }
    if flip and flip.get("flip_score", 0) >= 30:
        profit_lo = flip.get("est_profit_low", 0)
        profit_hi = flip.get("est_profit_high", 0)
        flip_text = flip.get("flip_verdict", "")
        if profit_lo > 0:
            flip_text += f"\nEst. profit: +${profit_lo:.0f}–${profit_hi:.0f}"
        embed["fields"].append({
            "name": "💰 Flip Potential", "value": flip_text, "inline": True,
        })
    if drop.get("image"):
        embed["image"] = {"url": drop["image"]}

    try:
        requests.post(
            config.DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=10,
        )
    except Exception:
        log.exception("Drop Discord alert failed")


def _log_drop_console(drop: dict, tier: str, flip: dict | None = None):
    t_emoji, t_label = _TIER_LABEL.get(tier, ("📅", "UPCOMING DROP"))
    release_label = drop.get("release_label") or drop.get("release_dt", "")[:10]
    sep = "═" * 62
    print(f"\n{sep}")
    print(f"  {t_emoji}  {t_label}")
    print(f"  {drop['title']}")
    if drop.get("brand"):
        print(f"  Brand:  {drop['brand']}")
    print(f"  🗓  {release_label}")
    if drop.get("price"):
        print(f"  💰 {drop['price']}")
    if flip and flip.get("flip_score", 0) >= 30:
        profit_lo = flip.get("est_profit_low", 0)
        profit_hi = flip.get("est_profit_high", 0)
        if profit_lo > 0:
            print(f"  💰 Flip: +${profit_lo:.0f}–${profit_hi:.0f} ({flip.get('flip_verdict', '')})")
    print(f"  🔗 {drop.get('url', '')}")
    print(sep)


# ===========================================================================
# DAILY DIGEST — consolidated summary of the day's best opportunities
# ===========================================================================

def send_daily_digest(deals: list[dict]):
    """Send a consolidated daily summary via Telegram."""
    if not deals or not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return

    e = _h

    flip_deals = [d for d in deals if d.get("est_profit_low", 0) > 0]
    total_potential = sum(d.get("est_profit_low", 0) for d in flip_deals)

    lines = [
        "💵  <b>DAILY MONEY REPORT</b>",
        "",
    ]

    if flip_deals:
        lines.append(f"📊  <b>{len(flip_deals)}</b> flip opportunities found today")
        lines.append(f"💰  Total potential profit: <b>${total_potential:.0f}+</b>")
        lines.append("")
        lines.append("━━━ <b>TOP MONEY MOVES</b> ━━━")
        lines.append("")

    for i, deal in enumerate(deals[:10], 1):
        title = deal.get("title", "")[:55]
        price = deal.get("price", "")
        url = deal.get("url", "")
        profit_lo = deal.get("est_profit_low", 0)
        profit_hi = deal.get("est_profit_high", 0)
        roi = deal.get("roi_pct", 0)

        if url:
            lines.append(f'{i}. <a href="{e(url)}">{e(title)}</a>')
        else:
            lines.append(f"{i}. {e(title)}")

        details = []
        if price:
            details.append(f"Buy: {e(price)}")
        if profit_lo > 0:
            details.append(f"+${profit_lo:.0f}–${profit_hi:.0f} ({roi}% ROI)")
        if details:
            lines.append(f"   {'  →  '.join(details)}")
        lines.append("")

    lines.append(f"<i>{len(deals)} opportunities tracked today</i>")

    caption = "\n".join(lines)
    if len(caption) > 4090:
        caption = caption[:4087] + "..."

    _tg_throttle()

    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
    try:
        resp = requests.post(
            f"{base}/sendMessage",
            json={
                "chat_id":                  config.TELEGRAM_CHAT_ID,
                "text":                     caption,
                "parse_mode":               "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.ok:
            log.info("Daily digest sent")
        else:
            log.error("Daily digest error %s: %s", resp.status_code, resp.text[:200])
    except Exception:
        log.exception("Daily digest failed")


# ===========================================================================
# ANALYTICS REPORT
# ===========================================================================

def _trend_arrow(trend: list[tuple]) -> str:
    """Show price trend direction from daily averages."""
    if len(trend) < 2:
        return "—"
    first_avg = trend[0][1]
    last_avg = trend[-1][1]
    if first_avg <= 0:
        return "—"
    change_pct = ((last_avg - first_avg) / first_avg) * 100
    if change_pct < -3:
        return f"📉 {change_pct:+.1f}%"
    elif change_pct > 3:
        return f"📈 {change_pct:+.1f}%"
    return f"➡️ {change_pct:+.1f}%"


def _bar_chart(items: list[tuple], max_bars: int = 8) -> list[str]:
    """Build a simple text-based horizontal bar chart."""
    if not items:
        return ["  (no data)"]
    top = items[:max_bars]
    max_val = max(v for _, v in top) if top else 1
    lines = []
    for label, val in top:
        bar_len = max(1, int((val / max_val) * 10))
        bar = "█" * bar_len
        lines.append(f"  {bar} <b>{val}</b> — {_h(label)}")
    return lines


def send_analytics_report(data: dict):
    """Send a rich analytics report via Telegram."""
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return

    e = _h
    days = data.get("days", 7)

    lines = [
        f"📊  <b>ANALYTICS — Last {days} Days</b>",
        "",
    ]

    # ── Overview stats ──
    lines.append("━━━ <b>OVERVIEW</b> ━━━")
    lines.append("")
    lines.append(f"🔄  Scan cycles: <b>{data.get('total_cycles', 0)}</b>"
                 f"  ({data.get('rapid_cycles', 0)} rapid)")
    lines.append(f"📦  Deals scanned: <b>{data.get('total_scanned', 0):,}</b>")
    lines.append(f"🆕  New deals found: <b>{data.get('total_new', 0):,}</b>")
    lines.append(f"🔔  Alerts sent: <b>{data.get('total_alerts', 0)}</b>")
    lines.append(f"⏱  Avg cycle time: <b>{data.get('avg_duration', 0):.0f}s</b>")
    lines.append("")

    # ── Money stats ──
    lines.append("━━━ 💰 <b>MONEY</b> ━━━")
    lines.append("")
    total_profit = data.get("sum_profit", 0)
    lines.append(f"💰  Total est. profit found: <b>${total_profit:,.0f}</b>")
    lines.append(f"💰🔥  Flip opportunities: <b>{data.get('total_flips', 0)}</b>")
    lines.append(f"🔄  Restocks caught: <b>{data.get('total_restocks', 0)}</b>")
    lines.append(f"📉  Lowest prices found: <b>{data.get('total_lowest', 0)}</b>")
    lines.append("")

    # ── Top sources ──
    sources = data.get("sources", [])
    if sources:
        lines.append("━━━ 📡 <b>TOP SOURCES</b> ━━━")
        lines.append("")
        lines.extend(_bar_chart(sources, 6))
        lines.append("")

    # ── Top alerted sources ──
    alerted = data.get("alerted_sources", [])
    if alerted:
        lines.append("━━━ 🔔 <b>BEST ALERT SOURCES</b> ━━━")
        lines.append("")
        lines.extend(_bar_chart(alerted, 5))
        lines.append("")

    # ── Price trend ──
    trend = data.get("price_trend", [])
    if trend:
        arrow = _trend_arrow(trend)
        lines.append("━━━ 📈 <b>PRICE TREND</b> ━━━")
        lines.append("")
        lines.append(f"  Direction: {arrow}")
        lines.append(f"  Snapshots tracked: <b>{data.get('total_snapshots', 0):,}</b>")
        lines.append(f"  Unique items: <b>{data.get('unique_items', 0):,}</b>")
        if len(trend) >= 2:
            lines.append(f"  Avg {trend[0][0]}: ${trend[0][1]:.0f}")
            lines.append(f"  Avg {trend[-1][0]}: ${trend[-1][1]:.0f}")
        lines.append("")

    # ── Most tracked items ──
    top_items = data.get("top_items", [])
    if top_items:
        lines.append("━━━ 👁 <b>MOST TRACKED</b> ━━━")
        lines.append("")
        for i, item in enumerate(top_items[:5], 1):
            title = item.get("title", "")[:40]
            seen = item.get("times_seen", 0)
            price = item.get("last_price", 0)
            lines.append(f"  {i}. {e(title)}")
            lines.append(f"     Seen {seen}x  •  Last ${price:.0f}")
        lines.append("")

    # ── Upcoming drops ──
    upcoming = data.get("upcoming_drops", 0)
    if upcoming:
        lines.append(f"📅  Upcoming drops tracked: <b>{upcoming}</b>")
        lines.append("")

    lines.append(f"<i>Report generated {e(data.get('generated', ''))}</i>")

    caption = "\n".join(lines)
    if len(caption) > 4090:
        caption = caption[:4087] + "..."

    _tg_throttle()

    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
    try:
        resp = requests.post(
            f"{base}/sendMessage",
            json={
                "chat_id":                  config.TELEGRAM_CHAT_ID,
                "text":                     caption,
                "parse_mode":               "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.ok:
            log.info("Analytics report sent")
        else:
            log.error("Analytics report error %s: %s", resp.status_code, resp.text[:200])
    except Exception:
        log.exception("Analytics report failed")


def send_cycle_summary(stats: dict):
    """Send a compact one-line cycle summary to Telegram after each scan."""
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return

    parts = [f"🔄 Cycle #{stats.get('cycle_num', '?')}"]
    parts.append(f"{stats.get('deals_new', 0)} new")
    parts.append(f"{stats.get('alerts_sent', 0)} alerts")

    flips = stats.get("flips_found", 0)
    if flips:
        parts.append(f"💰 {flips} flips")

    profit = stats.get("total_est_profit", 0)
    if profit > 0:
        parts.append(f"${profit:.0f} potential")

    restocks = stats.get("restocks_found", 0)
    if restocks:
        parts.append(f"🔄 {restocks} restocks")

    duration = stats.get("duration_s", 0)
    parts.append(f"⏱ {duration:.0f}s")

    text = "  •  ".join(parts)

    _tg_throttle()

    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
    try:
        requests.post(
            f"{base}/sendMessage",
            json={
                "chat_id":    config.TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception:
        log.exception("Cycle summary send failed")
