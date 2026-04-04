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

    if flip:
        flip_score      = flip.get("flip_score", 0)
        flip_verdict    = flip.get("flip_verdict", "")
        est_profit_low  = flip.get("est_profit_low", 0)
        est_profit_high = flip.get("est_profit_high", 0)
        est_resale_low  = flip.get("est_resale_low", 0)
        est_resale_high = flip.get("est_resale_high", 0)
        flip_signals    = flip.get("signals", [])

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


def _build_telegram_html(msg: dict) -> str:
    e = _h

    verdict   = msg["verdict"]
    v_emoji   = VERDICT_EMOJI.get(verdict, "💡")
    v_label   = VERDICT_LABEL.get(verdict, verdict.title() if verdict else "")
    src_emoji = SOURCE_EMOJI.get(msg["source"], "📡")
    disc      = msg.get("discount_pct", 0)
    flip_v    = msg.get("flip_verdict", "")
    flip_s    = msg.get("flip_score", 0)

    lines = []

    # ── Priority badges (most important signals first) ──
    if msg.get("is_restock"):
        lines.append("🔄  <b>RESTOCK ALERT — was sold out, back in stock!</b>")
        lines.append("")
    elif flip_v in _FLIP_BADGE:
        f_emoji, f_label = _FLIP_BADGE[flip_v]
        profit_low = msg.get("est_profit_low", 0)
        profit_high = msg.get("est_profit_high", 0)
        if profit_low > 0:
            lines.append(f"{f_emoji}  <b>{f_label}  —  est. +${profit_low:.0f}–${profit_high:.0f} profit</b>")
        else:
            lines.append(f"{f_emoji}  <b>{f_label}</b>")
        lines.append("")

    if msg.get("is_lowest") and msg.get("price_drop", 0) > 0:
        lines.append(f"📉  <b>LOWEST PRICE — ${msg['price_drop']:.0f} below previous low</b>")
        lines.append("")
    elif disc >= 15 and flip_v not in _FLIP_BADGE and not msg.get("is_restock"):
        lines.append(f"🏷  <b>{disc}% OFF</b>")
        lines.append("")

    # ── Title ──
    if msg["url"]:
        lines.append(f'<b><a href="{e(msg["url"])}">{e(msg["title"])}</a></b>')
    else:
        lines.append(f"<b>{e(msg['title'])}</b>")

    # ── Flair badge ──
    if msg["flair"]:
        lines.append(f'<i>{e(msg["flair"])}</i>')

    lines.append("")

    # ── Price ──
    if msg["price"]:
        orig = msg.get("original_price", "")
        if orig and disc:
            lines.append(f"💰  <b>{e(msg['price'])}</b>  <s>{e(orig)}</s>  ({disc}% off)")
        else:
            lines.append(f"💰  <b>{e(msg['price'])}</b>")

    # ── Resale estimate ──
    resale_low = msg.get("est_resale_low", 0)
    resale_high = msg.get("est_resale_high", 0)
    if resale_low > 0 and flip_s >= 20:
        lines.append(f"📊  Resale est: <b>${resale_low:.0f}–${resale_high:.0f}</b>")

    # ── Source ──
    lines.append(f"{src_emoji}  {e(msg['source'])}")

    # ── Reddit signals ──
    if msg["upvotes"] or msg["comments"]:
        lines.append(f"⬆️  {msg['upvotes']:,} upvotes   💬 {msg['comments']:,} comments")

    # ── Trending badge ──
    if msg["trending"]:
        lines.append("🚀  <b>Trending Now</b>")

    lines.append("")

    # ── Analysis block ──
    if verdict:
        hype_bar = _hype_bar(msg["hype_score"])
        lines.append(f"{v_emoji}  <b>{v_label}</b>   {hype_bar}")
        if msg["ai_summary"]:
            lines.append(f"<i>{e(msg['ai_summary'])}</i>")

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
    verdict  = msg["verdict"]
    v_emoji  = VERDICT_EMOJI.get(verdict, "💡")
    hype_bar = _hype_bar(msg["hype_score"])
    disc     = msg.get("discount_pct", 0)
    flip_v   = msg.get("flip_verdict", "")
    flip_s   = msg.get("flip_score", 0)

    v_label  = VERDICT_LABEL.get(verdict, verdict.title() if verdict else "")

    # Title prefix: flip alert takes priority over plain discount badge
    if flip_v in _FLIP_BADGE:
        f_emoji, f_label = _FLIP_BADGE[flip_v]
        title_prefix = f"{f_emoji} {f_label} — "
    elif disc >= 15:
        title_prefix = f"🏷 {disc}% OFF — "
    else:
        title_prefix = ""

    price_display = msg["price"] or "N/A"
    orig = msg.get("original_price", "")
    if orig and disc:
        price_display = f"{msg['price']}  ~~{orig}~~  ({disc}% off)"

    # Gold color for strong flips
    if flip_s >= 65:
        color = 0xFFD700
    elif flip_s >= 40:
        color = 0xFFA000
    elif disc >= 30:
        color = 0xFF1744
    else:
        color = VERDICT_COLOR.get(verdict, 0x7C4DFF)

    embed = {
        "title":       f"{title_prefix}{msg['title']}"[:256],
        "url":         msg["url"] or None,
        "color":       color,
        "description": msg["ai_summary"][:300] if msg["ai_summary"] else None,
        "fields":      [
            {"name": "Source",  "value": msg["source"],  "inline": True},
            {"name": "Price",   "value": price_display,  "inline": True},
        ],
        "footer": {"text": "HypeBot • Streetwear Intelligence"},
    }

    if flip_s >= 20:
        profit_low = msg.get("est_profit_low", 0)
        profit_high = msg.get("est_profit_high", 0)
        resale_low = msg.get("est_resale_low", 0)
        resale_high = msg.get("est_resale_high", 0)
        flip_text = f"Resale est: ${resale_low:.0f}–${resale_high:.0f}"
        if profit_low > 0:
            flip_text += f"\nEst. profit: +${profit_low:.0f}–${profit_high:.0f}"
        embed["fields"].append({
            "name": "💰 Flip Potential",
            "value": flip_text,
            "inline": True,
        })

    if msg["upvotes"] or msg["comments"]:
        embed["fields"].append({
            "name":   "Community Signal",
            "value":  f"⬆️ {msg['upvotes']:,} upvotes  💬 {msg['comments']:,} comments",
            "inline": True,
        })
    if verdict:
        embed["fields"].append({
            "name":   "Analysis",
            "value":  f"{v_emoji} {v_label}   {hype_bar}",
            "inline": False,
        })
    if msg.get("image"):
        embed["image"] = {"url": msg["image"]}
    if msg["trending"]:
        embed["fields"].append({"name": "Market Signal", "value": "🚀 Trending", "inline": True})

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
    verdict  = msg["verdict"]
    v_emoji  = VERDICT_EMOJI.get(verdict, "💡")
    v_label  = VERDICT_LABEL.get(verdict, verdict.title() if verdict else "")
    hype_bar = _hype_bar(msg["hype_score"])
    disc     = msg.get("discount_pct", 0)
    flip_v   = msg.get("flip_verdict", "")
    flip_s   = msg.get("flip_score", 0)
    sep = "─" * 62
    print(f"\n{sep}")
    if msg.get("is_restock"):
        print("  🔄  RESTOCK ALERT — was sold out, back in stock!")
    elif flip_v in _FLIP_BADGE:
        f_emoji, f_label = _FLIP_BADGE[flip_v]
        profit_low = msg.get("est_profit_low", 0)
        profit_high = msg.get("est_profit_high", 0)
        if profit_low > 0:
            print(f"  {f_emoji}  {f_label}  —  est. +${profit_low:.0f}–${profit_high:.0f} profit")
        else:
            print(f"  {f_emoji}  {f_label}")
    elif disc >= 15:
        print(f"  🏷  {disc}% OFF")
    if msg.get("is_lowest") and msg.get("price_drop", 0) > 0:
        print(f"  📉  LOWEST PRICE — ${msg['price_drop']:.0f} below previous low")
    print(f"  {msg['title']}")
    if msg["price"]:
        orig = msg.get("original_price", "")
        if orig and disc:
            print(f"  💰 {msg['price']}  (was {orig}, {disc}% off)")
        else:
            print(f"  💰 {msg['price']}")
    if flip_s >= 20:
        resale_low = msg.get("est_resale_low", 0)
        resale_high = msg.get("est_resale_high", 0)
        print(f"  📊 Resale est: ${resale_low:.0f}–${resale_high:.0f}")
    print(f"  📡 {msg['source']}")
    if msg["upvotes"] or msg["comments"]:
        print(f"  ⬆️  {msg['upvotes']:,} upvotes   💬 {msg['comments']:,} comments")
    if verdict:
        print(f"  {v_emoji} {v_label}   {hype_bar}")
    if msg["ai_summary"]:
        print(f"  {msg['ai_summary'][:120]}")
    print(f"  🔗 {msg['url']}")
    print(sep)


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

    # Flip potential for the drop
    if flip and flip.get("flip_score", 0) >= 30:
        fv = flip.get("flip_verdict", "")
        profit_lo = flip.get("est_profit_low", 0)
        profit_hi = flip.get("est_profit_high", 0)
        if profit_lo > 0:
            lines.append(f"💰  Flip potential: <b>+${profit_lo:.0f}–${profit_hi:.0f}</b> ({fv})")
        else:
            lines.append(f"💰  Flip potential: <b>{fv}</b>")

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
    lines = [
        "📊  <b>DAILY DIGEST — Today's Best Finds</b>",
        "",
    ]

    for i, deal in enumerate(deals, 1):
        title = deal.get("title", "")[:60]
        price = deal.get("price", "")
        source = deal.get("source", "")
        url = deal.get("url", "")

        if url:
            lines.append(f'{i}. <a href="{e(url)}">{e(title)}</a>')
        else:
            lines.append(f"{i}. {e(title)}")

        details = []
        if price:
            details.append(f"💰 {e(price)}")
        if source:
            details.append(f"📡 {e(source)}")
        if details:
            lines.append(f"   {'  •  '.join(details)}")
        lines.append("")

    lines.append(f"<i>{len(deals)} deals tracked today</i>")

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
