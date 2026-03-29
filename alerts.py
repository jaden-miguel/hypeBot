"""
Alert module — Telegram (HTML), Discord, email, console.
"""

import html
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

import config

log = logging.getLogger(__name__)

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


def send_alert(deal: dict, analysis: dict | None = None):
    msg = _format_message(deal, analysis)
    log.info("ALERT: %s", msg["title"])
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        _send_telegram(msg)
    if config.DISCORD_WEBHOOK_URL:
        _send_discord(msg)
    if config.ALERT_EMAIL_TO and config.SMTP_USER:
        _send_email(msg)
    _log_to_console(msg)


def _format_message(deal: dict, analysis: dict | None) -> dict:
    verdict_key = ""
    hype_score  = 0
    ai_summary  = ""
    trending    = False

    if analysis:
        verdict_key = analysis.get("verdict", "").upper()
        hype_score  = analysis.get("hype_score", 0)
        ai_summary  = analysis.get("summary", "")
        trending    = bool(analysis.get("trending", False))

    return {
        "title":      deal.get("title", "Unknown Deal"),
        "url":        deal.get("url", ""),
        "price":      deal.get("price", "") or "",
        "source":     deal.get("source", ""),
        "image":      deal.get("image", ""),
        "upvotes":    deal.get("upvotes", 0),
        "comments":   deal.get("comments", 0),
        "flair":      deal.get("flair", ""),
        "verdict":    verdict_key,
        "hype_score": hype_score,
        "ai_summary": ai_summary,
        "trending":   trending,
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


def _build_telegram_html(msg: dict) -> str:
    e = _h

    verdict   = msg["verdict"]
    v_emoji   = VERDICT_EMOJI.get(verdict, "💡")
    v_label   = VERDICT_LABEL.get(verdict, verdict.title() if verdict else "")
    src_emoji = SOURCE_EMOJI.get(msg["source"], "📡")

    lines = []

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
        lines.append(f"💰  <b>{e(msg['price'])}</b>")

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


def _send_telegram(msg: dict):
    caption = _build_telegram_html(msg)
    base    = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

    # Telegram captions max 1024 chars; messages max 4096
    if len(caption) > 1020:
        caption = caption[:1017] + "..."

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

    v_label  = VERDICT_LABEL.get(verdict, verdict.title() if verdict else "")

    embed = {
        "title":       msg["title"][:256],
        "url":         msg["url"] or None,
        "color":       VERDICT_COLOR.get(verdict, 0x7C4DFF),
        "description": msg["ai_summary"][:300] if msg["ai_summary"] else None,
        "fields":      [
            {"name": "Source",  "value": msg["source"],         "inline": True},
            {"name": "Price",   "value": msg["price"] or "N/A", "inline": True},
        ],
        "footer": {"text": "HypeBot • Streetwear Intelligence"},
    }

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
    sep = "─" * 62
    print(f"\n{sep}")
    print(f"  {msg['title']}")
    if msg["price"]:
        print(f"  💰 {msg['price']}")
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


def send_drop_alert(drop: dict, tier: str):
    """Send an upcoming release alert for 'today', '1day', or '7day' tiers."""
    log.info("DROP ALERT [%s]: %s", tier, drop.get("title", ""))
    _send_drop_telegram(drop, tier)
    if config.DISCORD_WEBHOOK_URL:
        _send_drop_discord(drop, tier)
    _log_drop_console(drop, tier)


def _send_drop_telegram(drop: dict, tier: str):
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

    lines.append(f"📡  {e(drop.get('source', ''))}")

    caption = "\n".join(lines)
    if len(caption) > 1020:
        caption = caption[:1017] + "..."

    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

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


def _send_drop_discord(drop: dict, tier: str):
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


def _log_drop_console(drop: dict, tier: str):
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
    print(f"  🔗 {drop.get('url', '')}")
    print(sep)
