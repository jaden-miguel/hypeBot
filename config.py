import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "deals.db"

# ---------------------------------------------------------------------------
# Ollama / Open WebUI
# ---------------------------------------------------------------------------
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
OPENWEBUI_HOST = os.getenv("OPENWEBUI_HOST", "http://host.docker.internal:3000")
MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

# ---------------------------------------------------------------------------
# Scraper timing (seconds)
# ---------------------------------------------------------------------------
SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL", "300"))  # 5 min default

# ---------------------------------------------------------------------------
# Target brands & keywords
# ---------------------------------------------------------------------------
BRANDS = [
    "supreme", "kith", "palace", "stussy", "bape",
    "nike", "jordan", "adidas", "yeezy", "new balance",
    "raf simons", "rick owens", "fear of god", "essentials",
    "arc'teryx", "the north face", "stone island",
    "off-white", "rhude", "gallery dept", "corteiz",
]

DEAL_KEYWORDS = [
    "sale", "drop", "restock", "% off", "discount",
    "clearance", "release", "launch", "limited", "exclusive",
    "markdown", "deal", "price cut", "new arrival",
    "w2c", "find", "haul", "qc", "in hand", "review",
    "steal", "grail", "heat", "fire", "sleeper",
]

# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------
REDDIT_SUBREDDITS = {
    # Fashion reps & finds
    "fashionreps":        {"sort": "hot", "limit": 30, "min_upvotes": 20},
    "repbudgetsneakers":  {"sort": "hot", "limit": 25, "min_upvotes": 15},
    "qualityreps":        {"sort": "hot", "limit": 20, "min_upvotes": 10},
    # Legit streetwear & sneakers
    "streetwear":         {"sort": "hot", "limit": 25, "min_upvotes": 50},
    "sneakers":           {"sort": "hot", "limit": 25, "min_upvotes": 50},
    "supremeclothing":    {"sort": "hot", "limit": 20, "min_upvotes": 15},
    "palaceclothing":     {"sort": "hot", "limit": 20, "min_upvotes": 10},
    # Deals
    "frugalmalefashion":  {"sort": "hot", "limit": 25, "min_upvotes": 30},
    "sneakerdeals":       {"sort": "hot", "limit": 25, "min_upvotes": 10},
}
REDDIT_MIN_UPVOTES_DEFAULT = 10

# ---------------------------------------------------------------------------
# RSS feeds
# ---------------------------------------------------------------------------
RSS_FEEDS = {
    "hypebeast": "https://hypebeast.com/feed",
    "highsnobiety": "https://www.highsnobiety.com/feed/",
    "sneakernews": "https://sneakernews.com/feed/",
    "complexsneakers": "https://www.complex.com/sneakers/feed",
    "grailed_blog": "https://www.grailed.com/drycleanonly/feed",
}

# ---------------------------------------------------------------------------
# Web scrape targets (pages with sale/drop sections)
# ---------------------------------------------------------------------------
SCRAPE_TARGETS = [
    {
        "name": "kith_new_arrivals",
        "url": "https://kith.com/collections/new-arrivals",
        "selector": "a.product-card",
        "title_sel": ".product-card__title",
        "price_sel": ".product-card__price",
    },
    {
        "name": "endclothing_sale",
        "url": "https://www.endclothing.com/us/sale",
        "selector": ".product-card",
        "title_sel": ".product-card__title",
        "price_sel": ".product-card__price",
    },
]

# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM", "")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")

# ---------------------------------------------------------------------------
# Request headers (be respectful)
# ---------------------------------------------------------------------------
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
REQUEST_TIMEOUT = 15
REQUEST_DELAY = 2  # seconds between requests to the same host
