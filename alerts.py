"""
Alert module — sends deal notifications via Discord webhook, email, or console log.
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
        "verdict": verdict,
    }


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
