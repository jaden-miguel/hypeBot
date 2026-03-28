# HypeBot — 24/7 Streetwear Deals Monitor

Scrapes RSS feeds and streetwear sites for drops and deals from brands like Supreme, Kith, Palace, Nike, Arc'teryx, The North Face, and more. Runs alongside your Ollama + Open WebUI stack and uses your local LLM to analyze whether each deal is worth copping.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  RSS Feeds   │────▶│              │────▶│   Ollama     │
│  + Web Pages │     │   HypeBot    │     │   (LLM)      │
└──────────────┘     │              │     └──────────────┘
                     │  scraper.py  │
                     │  analyzer.py │     ┌──────────────┐
                     │  alerts.py   │────▶│  Discord /   │
                     │  database.py │     │  Email /     │
                     └──────────────┘     │  Console     │
                           │              └──────────────┘
                     ┌─────┴──────┐
                     │  SQLite DB │
                     └────────────┘
```

## Prerequisites

- **Docker** and **Docker Compose** installed
- **Ollama** running on your machine with a model pulled:
  ```bash
  ollama pull llama3.1
  ```

## Quick Start

### 1. Clone and configure

```bash
cd hypeBot
cp .env.example .env
# Edit .env with your Discord webhook URL, email settings, etc.
```

### 2. Launch everything with Docker Compose

```bash
docker-compose up -d
```

This starts:
- **Open WebUI** on `http://localhost:3000`
- **HypeBot** scraping every 5 minutes in the background

### 3. Or run the bot standalone (without Docker)

```bash
pip install -r requirements.txt
python main.py
```

## Configuration

All settings live in `config.py` and can be overridden via environment variables (or `.env`):

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3.1` | Model for deal analysis |
| `SCRAPE_INTERVAL` | `300` | Seconds between scan cycles |
| `DISCORD_WEBHOOK_URL` | *(empty)* | Discord webhook for alerts |
| `ALERT_EMAIL_TO` | *(empty)* | Recipient email for alerts |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | *(empty)* | SMTP login |
| `SMTP_PASS` | *(empty)* | SMTP password / app password |
| `DATA_DIR` | project root | Where SQLite DB is stored |

## Adding Feeds & Scrape Targets

Edit `config.py`:

- **RSS_FEEDS** — add any RSS URL keyed by a name
- **SCRAPE_TARGETS** — add web pages with CSS selectors for product cards
- **BRANDS** — add/remove brand names to track
- **DEAL_KEYWORDS** — add/remove trigger keywords

## How AI Analysis Works

Each new deal is sent to your Ollama model with a streetwear-focused system prompt. The model returns a JSON verdict:

```json
{
  "verdict": "cop",
  "brand": "Nike",
  "hype_score": 8,
  "summary": "Air Jordan 1 Retro High OG at 30% off is a solid deal."
}
```

- **cop** — worth buying, alert fires
- **pass** — skip (only suppresses alert if hype_score < 4)
- **maybe** — alert fires so you can decide

## Project Structure

```
hypeBot/
├── main.py           # Entry point — loop with scrape/analyze/alert
├── config.py         # All configuration & env vars
├── scraper.py        # RSS + web scraping logic
├── analyzer.py       # Ollama LLM integration
├── alerts.py         # Discord, email, console alerts
├── database.py       # SQLite persistence
├── requirements.txt  # Python dependencies
├── Dockerfile        # Container image for the bot
├── docker-compose.yml# Full stack (Open WebUI + HypeBot)
├── .env.example      # Template for environment variables
└── .gitignore
```

## Logs

- Console output streams in real-time
- `hypebot.log` is written in the working directory
- Docker: `docker logs -f hypebot`
