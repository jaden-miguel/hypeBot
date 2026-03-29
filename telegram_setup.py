#!/usr/bin/env python3
"""
One-time setup: finds your Telegram chat_id.

1. Open Telegram and send /start to your bot (@jmHypebot)
2. Run this script: python telegram_setup.py
3. It prints your chat_id — paste it into .env as TELEGRAM_CHAT_ID
"""

import os
import sys
import requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

if not TOKEN:
    try:
        from pathlib import Path
        env_path = Path(__file__).resolve().parent / ".env"
        for line in env_path.read_text().splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                TOKEN = line.split("=", 1)[1].strip()
                break
    except FileNotFoundError:
        pass

if not TOKEN:
    print("ERROR: Set TELEGRAM_BOT_TOKEN in .env first.")
    sys.exit(1)

print(f"Bot token: {TOKEN[:10]}...{TOKEN[-4:]}")
print("Checking for messages sent to your bot...\n")

resp = requests.get(
    f"https://api.telegram.org/bot{TOKEN}/getUpdates",
    timeout=10,
)

if not resp.ok:
    print(f"API error: {resp.text}")
    sys.exit(1)

data = resp.json()
updates = data.get("result", [])

if not updates:
    print("No messages found.")
    print("  1. Open Telegram")
    print("  2. Search for @jmHypebot")
    print("  3. Send /start")
    print("  4. Run this script again")
    sys.exit(0)

seen = set()
for update in updates:
    msg = update.get("message", {})
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    name = chat.get("first_name", "") + " " + chat.get("last_name", "")
    username = chat.get("username", "")

    if chat_id and chat_id not in seen:
        seen.add(chat_id)
        print(f"  Chat ID:  {chat_id}")
        print(f"  Name:     {name.strip()}")
        print(f"  Username: @{username}")
        print()

if seen:
    chat_id = list(seen)[0]
    print(f"Add this to your .env file:")
    print(f"  TELEGRAM_CHAT_ID={chat_id}")

    # Send a test message
    test = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": "✅ HypeBot connected! You'll receive deal alerts here.",
        },
        timeout=10,
    )
    if test.ok:
        print(f"\nTest message sent to Telegram!")
    else:
        print(f"\nTest message failed: {test.text}")
