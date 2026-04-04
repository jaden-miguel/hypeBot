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
    "steal", "grail", "heat", "fire", "sleeper",
]

# At least one of these must appear in an RSS entry for it to pass the
# pre-filter. Signals that an item is currently buyable, not just announced.
AVAILABILITY_SIGNALS = [
    "available now", "available online", "available today",
    "buy now", "shop now", "order now", "purchase now",
    "in stock", "on sale now", "officially on sale",
    "ships now", "ships today", "now available",
    "you can buy", "you can grab", "you can pick up",
    "on shelves", "hit shelves", "hitting shelves",
    "dropped today", "out now", "drops today",
    "releasing today", "live now", "just dropped",
    "add to cart", "shop the collection",
]

# Items that match brands/keywords but aren't actual clothing or footwear
EXCLUDED_KEYWORDS = [
    "subscription", "subscribe", "membership", "member plan",
    "gift card", "giftcard", "e-gift", "egift",
    "app release", "mobile app", "download the app",
    "podcast", "playlist", "spotify", "apple music",
    "nft", "metaverse", "virtual land", "digital collectible",
    "crypto", "token sale", "blockchain",
    "insurance", "warranty plan", "protection plan",
    "meal kit", "food box", "snack box",
    "class pass", "fitness class", "training program",
    "credit card", "debit card", "cash back card",
    "streaming", "disney+", "hulu", "netflix",
    "vpn", "software", "saas",
    "concert ticket", "event ticket", "festival pass",
    "hotel", "flight", "travel deal", "vacation package",
    "furniture", "home decor", "candle", "diffuser",
    "skincare set", "cologne set", "fragrance subscription",
    "book release", "album release", "video game",
    "phone case", "airpods", "earbuds", "headphones",
    "laptop", "tablet", "smart watch",
]

# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------
REDDIT_SUBREDDITS = {
    "Arcteryx":           {"sort": "hot", "limit": 20, "min_upvotes": 5},
    "Lululemon":          {"sort": "hot", "limit": 20, "min_upvotes": 5},
    "frugalmalefashion":  {"sort": "hot", "limit": 30, "min_upvotes": 20},
    "sneakerdeals":       {"sort": "hot", "limit": 25, "min_upvotes": 10},
    "malefashionadvice":  {"sort": "new",  "limit": 25, "min_upvotes": 5},
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
    "dealmoon_fashion": "https://www.dealmoon.com/en/fashion-clothing/rss",
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
        "name": "kith_sale",
        "url": "https://kith.com/collections/sale",
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
    {
        "name": "endclothing_new",
        "url": "https://www.endclothing.com/us/footwear",
        "selector": ".product-card",
        "title_sel": ".product-card__title",
        "price_sel": ".product-card__price",
    },
    {
        "name": "concepts_new",
        "url": "https://cncpts.com/collections/new-arrivals",
        "selector": "a.product-item__image-link, a.product-item--link",
        "title_sel": ".product-item__title",
        "price_sel": ".price__regular .price-item, .price-item",
    },
    {
        "name": "bodega_new_arrivals",
        "url": "https://bdgastore.com/collections/new-arrivals",
        "selector": "a.product-item__image-link",
        "title_sel": ".product-item__title",
        "price_sel": ".price__regular .price-item",
    },
    {
        "name": "ssense_sale_men",
        "url": "https://www.ssense.com/en-us/men/sale",
        "selector": "div[data-testid='product-card'], .product-tile",
        "title_sel": ".product-name-plp, .product-tile__name",
        "price_sel": ".price-sale, .product-tile__price--sale",
        "compare_sel": ".price-original, .product-tile__price--original",
    },
    {
        "name": "nike_sale",
        "url": "https://www.nike.com/w/sale-3yaep",
        "selector": ".product-card",
        "title_sel": ".product-card__title",
        "price_sel": ".product-price.is--current-price",
        "compare_sel": ".product-price.is--striked-out",
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
