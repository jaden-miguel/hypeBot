"""
Alert module — sends deal notifications via Telegram, Discord webhook, email, or console.
"""

import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

import config

log = logging.getLogger(__name__)


def send_alert(deal: dict, analysis: dict | None = None):
    """Dispatch a deal alert to all configured channels."""
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
    verdict = ""
    if analysis:
        v = analysis.get("verdict", "").upper()
        score = analysis.get("hype_score", "?")
        ai_summary = analysis.get("summary", "")
        verdict = f"**{v}** (Hype: {score}/10) — {ai_summary}"

    return {
        "title": deal.get("title", "Unknown Deal"),
        "url": deal.get("url", ""),
        "price": deal.get("price", "N/A"),
        "source": deal.get("source", ""),
        "image": deal.get("image", ""),
        "upvotes": deal.get("upvotes", 0),
        "comments": deal.get("comments", 0),
        "flair": deal.get("flair", ""),
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _send_telegram(msg: dict):
    verdict_plain = msg["verdict"].replace("**", "") if msg["verdict"] else ""
    text_parts = [
        f"🔥 *{_tg_escape(msg['title'])}*",
    ]
    if msg.get("flair"):
        text_parts.append(f"🏷 {_tg_escape(msg['flair'])}")
    text_parts.append(f"💰 Price: {_tg_escape(msg['price'])}")
    text_parts.append(f"📡 Source: {_tg_escape(msg['source'])}")
    if msg.get("upvotes") or msg.get("comments"):
        text_parts.append(f"⬆️ {msg['upvotes']} upvotes  💬 {msg['comments']} comments")
    if msg["url"]:
        text_parts.append(f"🔗 [View Deal]({msg['url']})")
    if verdict_plain:
        text_parts.append(f"🤖 {_tg_escape(verdict_plain)}")

    text = "\n".join(text_parts)
    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

    try:
        if msg.get("image"):
            resp = requests.post(
                f"{base}/sendPhoto",
                json={
                    "chat_id": config.TELEGRAM_CHAT_ID,
                    "photo": msg["image"],
                    "caption": text,
                    "parse_mode": "Markdown",
                },
                timeout=15,
            )
            if not resp.ok:
                log.warning("sendPhoto failed, falling back to sendMessage: %s", resp.text)
                resp = _tg_send_text(base, text)
        else:
            resp = _tg_send_text(base, text)

        if resp.ok:
            log.info("Telegram alert sent: %s", msg["title"])
        else:
            log.error("Telegram API error: %s", resp.text)
    except Exception:
        log.exception("Telegram alert failed")


def _tg_send_text(base: str, text: str):
    return requests.post(
        f"{base}/sendMessage",
        json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        },
        timeout=10,
    )


def _tg_escape(text: str) -> str:
    """Escape special Markdown characters for Telegram."""
    for ch in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(ch, f"\\{ch}")
    return text


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def _send_discord(msg: dict):
    embed = {
        "title": msg["title"],
        "url": msg["url"] if msg["url"] else None,
        "color": 0xFF5722,
        "fields": [
            {"name": "Source", "value": msg["source"], "inline": True},
            {"name": "Price", "value": msg["price"], "inline": True},
        ],
    }
    if msg.get("image"):
        embed["image"] = {"url": msg["image"]}
    if msg["verdict"]:
        embed["fields"].append({"name": "AI Verdict", "value": msg["verdict"]})

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
    body = (
        f"Deal: {msg['title']}\n"
        f"Price: {msg['price']}\n"
        f"Source: {msg['source']}\n"
        f"Link: {msg['url']}\n\n"
        f"AI Verdict: {msg['verdict']}\n"
    )
    email = MIMEMultipart()
    email["From"] = config.ALERT_EMAIL_FROM
    email["To"] = config.ALERT_EMAIL_TO
    email["Subject"] = f"[HypeBot] {msg['title']}"
    email.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASS)
            server.send_message(email)
        log.info("Email alert sent: %s", msg["title"])
    except Exception:
        log.exception("Email alert failed")


# ---------------------------------------------------------------------------
# Console (always runs)
# ---------------------------------------------------------------------------

def _log_to_console(msg: dict):
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  NEW DEAL: {msg['title']}")
    print(f"  Price:    {msg['price']}")
    print(f"  Source:   {msg['source']}")
    print(f"  Link:     {msg['url']}")
    if msg["verdict"]:
        print(f"  Verdict:  {msg['verdict']}")
    print(sep)
