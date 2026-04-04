# HypeBot — 24/7 Streetwear Deals & Drops Monitor

Scrapes RSS feeds, retail sale pages, and Reddit for the best streetwear deals and upcoming releases. Uses Ollama for AI analysis and sends curated alerts to Telegram, Discord, or email — only the top deals make the cut.

## Architecture

```
┌──────────────┐     ┌──────────────────────────┐     ┌──────────────┐
│  RSS Feeds   │────▶│                          │────▶│   Ollama     │
│  Web Scrapers│     │        HypeBot v3        │     │   (LLM)      │
│  Reddit      │     │                          │     └──────────────┘
└──────────────┘     │  scraper.py  — fetch      │
                     │  analyzer.py — AI classify │     ┌──────────────┐
┌──────────────┐     │  alerts.py   — notify      │────▶│  Telegram /  │
│ Drop Calendar│────▶│  drops.py    — releases    │     │  Discord /   │
│  (sneaker    │     │  database.py — persistence │     │  Email       │
│   news/RSS)  │     │  main.py     — orchestrate │     └──────────────┘
└──────────────┘     └──────────────────────────┘
                              │
                     ┌────────┴────────┐
                     │  SQLite (deals  │
                     │   + drops DB)   │
                     └─────────────────┘
```

## What It Does

- **Deals** — Monitors 8 retail sites (Nike, Kith, END, SSENSE, Bodega, Concepts), 6 RSS feeds, and 5 Reddit communities. Only alerts on items with **20%+ discount** or high community hype.
- **Drops** — Tracks upcoming releases from SneakerNews, Hypebeast, KicksOnFire, and Sole Collector. Sends reminders at 7 days, 1 day, and day-of.
- **Quality Scoring** — Every deal gets a composite score based on discount %, hype score, trending status, and Reddit engagement. Only the **top 15 per cycle** are sent as alerts.
- **AI Analysis** — RSS and Reddit items go through Ollama for classification (recommended / watch / skip). Web-scraped sale items with clear discounts bypass AI for speed.

## Prerequisites

- **Docker** and **Docker Compose** installed
- **Ollama** running on your machine with a model pulled:
  ```bash
  ollama pull qwen2.5-coder:1.5b
  ```

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/jaden-miguel/hypeBot.git
cd hypeBot
cp .env.example .env
# Edit .env with your Telegram token, Discord webhook, etc.
```

### 2. Launch with Docker Compose

```bash
docker compose up -d
```

This starts:
- **Open WebUI** on `http://localhost:3000`
- **HypeBot** scanning every 3 hours in the background

### 3. Manage with batch scripts (Windows)

```
start.bat   — build and start the bot
stop.bat    — stop the bot
status.bat  — show container status
logs.bat    — tail live logs
```

### 4. Or run standalone (without Docker)

```bash
pip install -r requirements.txt
python main.py
```

## Configuration

All settings live in `config.py` and can be overridden via `.env`:

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3.1` | Model for deal analysis |
| `SCRAPE_INTERVAL` | `10800` (3 hours) | Seconds between scan cycles |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | *(empty)* | Your Telegram chat ID |
| `DISCORD_WEBHOOK_URL` | *(empty)* | Discord webhook for alerts |
| `ALERT_EMAIL_TO` | *(empty)* | Recipient email for alerts |
| `SMTP_HOST` / `SMTP_PORT` | `smtp.gmail.com` / `587` | SMTP server settings |
| `DATA_DIR` | project root | Where SQLite DB is stored |

## How Alerts Work

Each cycle processes deals in two phases:

1. **Classify** — Web-scraped items with prices skip AI (auto-classified by discount %). RSS and Reddit items go through Ollama.
2. **Rank & Cap** — All qualifying deals are scored and sorted. Only the top 15 are sent as alerts.

**Quality score formula:**
- Discount % × 2 (40% off = +80 points)
- Hype score × 5 (8/10 = +40 points)
- Trending bonus (+20 points)
- Verdict bonus (+30 recommended, +10 watch)
- Reddit upvotes (capped at +30) and comments (capped at +15)

**Telegram alert example:**
```
🏷  24% OFF

Nike Pegasus Trail 5 GORE-TEX

💰  $136.97  was $180  (24% off)
📡  nike_sale
✅  Recommended   ███████░░░ 7/10
```

## Project Structure

```
hypeBot/
├── main.py            # Orchestrator — quality scoring, alert cap, 24/7 loop
├── config.py          # All configuration & env vars
├── scraper.py         # RSS + web + Reddit scraping (concurrent)
├── analyzer.py        # Ollama LLM integration
├── alerts.py          # Telegram, Discord, email, console (rate-limited)
├── drops.py           # Upcoming release calendar scraper
├── database.py        # SQLite persistence (deals + drops, WAL mode)
├── requirements.txt   # Python dependencies
├── Dockerfile         # Container image with health check
├── docker-compose.yml # Full stack (Open WebUI + HypeBot)
├── .env.example       # Template for environment variables
├── start.bat          # Windows: start the bot
├── stop.bat           # Windows: stop the bot
├── status.bat         # Windows: check status
├── logs.bat           # Windows: tail logs
└── .gitignore
```

## 24/7 Operation

The bot is designed for always-on operation:

- **Docker restart policy** — `unless-stopped` ensures it survives reboots
- **Health check** — Docker monitors the container every 5 minutes
- **Graceful shutdown** — Catches SIGTERM/SIGINT for clean exit
- **Exponential backoff** — Recovers from transient errors without crashing
- **Log rotation** — 5MB max with 3 backups
- **DB pruning** — Automatically cleans deals older than 30 days and drops older than 7 days (~every 24 hours)
- **Telegram throttling** — 1.5s minimum between API calls to avoid rate limits
- **Memory limit** — Capped at 384MB to coexist with other services

## Logs

- Console output streams in real-time
- `hypebot.log` rotated at 5MB (3 backups)
- Docker: `docker logs -f hypebot`
