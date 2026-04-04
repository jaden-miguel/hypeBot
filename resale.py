"""
Resale profit estimator — scores flip potential based on brand tier,
model heat, scarcity signals, and buy price vs estimated market value.
"""

import re
import logging

log = logging.getLogger(__name__)

# Brand tiers — multiplier against retail price for resale estimate.
# Based on typical secondary-market premiums.
_BRAND_TIERS: dict[str, tuple[float, float]] = {
    # (min_multiplier, max_multiplier) — range used depending on model heat
    # S-tier: routinely sell above retail
    "jordan":       (1.3, 3.0),
    "yeezy":        (1.2, 2.5),
    "off-white":    (1.5, 3.5),
    "travis scott": (2.0, 5.0),
    "chrome hearts": (1.5, 3.0),
    # A-tier: collabs and select models flip well
    "nike dunk":    (1.2, 2.0),
    "nike sb":      (1.3, 2.5),
    "new balance":  (1.1, 1.8),
    "fear of god":  (1.1, 1.6),
    "essentials":   (1.0, 1.3),
    "rick owens":   (1.1, 1.5),
    "raf simons":   (1.0, 1.4),
    "stone island":  (1.1, 1.5),
    "ami":          (1.0, 1.3),
    "amiri":        (1.1, 1.5),
    # B-tier: limited pieces flip, general stock doesn't
    "supreme":      (1.0, 2.5),
    "palace":       (1.0, 1.5),
    "kith":         (1.0, 1.4),
    "stussy":       (0.9, 1.2),
    "bape":         (1.0, 1.8),
    "corteiz":      (1.1, 1.8),
    "gallery dept": (1.1, 1.6),
    "rhude":        (1.0, 1.3),
    "arc'teryx":    (1.0, 1.3),
    "the north face": (0.9, 1.2),
    # Generics
    "nike":         (0.8, 1.5),
    "adidas":       (0.7, 1.3),
}

# High-heat models — these specific silhouettes have strong resale
_HOT_MODELS = {
    r"air jordan 1\b|aj1\b|\bj1\b":        0.9,
    r"air jordan 4\b|aj4\b|\bj4\b":        0.85,
    r"air jordan 3\b|aj3\b|\bj3\b":        0.7,
    r"air jordan 11\b|aj11\b":              0.7,
    r"dunk low\b":                          0.75,
    r"dunk high\b":                         0.5,
    r"air force 1\b|af1\b":                 0.4,
    r"air max 1\b":                         0.5,
    r"air max 90\b":                        0.45,
    r"yeezy 350\b|yeezy boost 350":        0.7,
    r"yeezy 700\b":                         0.5,
    r"yeezy slide\b":                       0.6,
    r"new balance 550\b|nb 550\b":          0.55,
    r"new balance 2002r\b|nb 2002r\b":      0.6,
    r"new balance 990\b|nb 990\b":          0.65,
    r"asics gel-kayano\b":                  0.45,
    r"asics gel-lyte\b":                    0.5,
    r"samba\b":                             0.5,
    r"gazelle\b":                           0.35,
    r"forum\b.*adidas|adidas.*forum":       0.35,
}
_HOT_MODEL_PATTERNS = [(re.compile(p, re.IGNORECASE), s) for p, s in _HOT_MODELS.items()]

# Scarcity / hype multiplier signals
_SCARCITY_SIGNALS = {
    "limited edition":  0.3,
    "limited":          0.15,
    "exclusive":        0.2,
    "collab":           0.25,
    "collaboration":    0.25,
    "x ":               0.1,   # "Nike x Travis Scott"
    " x ":              0.15,
    "1 of ":            0.4,
    "friends and family": 0.5,
    "f&f":              0.5,
    "sample":           0.3,
    "unreleased":       0.3,
    "sold out":         0.2,
    "oos":              0.15,
    "quickstrike":      0.25,
    "qs":               0.15,
    "tier 0":           0.3,
    "special box":      0.15,
    "og colorway":      0.2,
    "retro":            0.15,
    "restock":          0.1,
}

_PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")

# Retail price floors — if buy price is below this, it's likely a price error
# or an absurd deal that needs immediate action. Keyed by regex pattern.
_RETAIL_FLOORS: list[tuple[re.Pattern, float]] = [
    (re.compile(r"air jordan 1\b|aj1\b", re.I),   170),
    (re.compile(r"air jordan 4\b|aj4\b", re.I),   200),
    (re.compile(r"air jordan 3\b|aj3\b", re.I),   200),
    (re.compile(r"air jordan 11\b|aj11\b", re.I),  225),
    (re.compile(r"dunk low\b", re.I),              110),
    (re.compile(r"dunk high\b", re.I),             120),
    (re.compile(r"air force 1\b|af1\b", re.I),    110),
    (re.compile(r"air max 1\b", re.I),             140),
    (re.compile(r"air max 90\b", re.I),            130),
    (re.compile(r"air max 95\b", re.I),            185),
    (re.compile(r"yeezy 350\b|yeezy boost 350", re.I), 230),
    (re.compile(r"yeezy 700\b", re.I),             240),
    (re.compile(r"yeezy slide\b", re.I),           70),
    (re.compile(r"new balance 990\b|nb 990\b", re.I), 200),
    (re.compile(r"new balance 550\b|nb 550\b", re.I), 110),
    (re.compile(r"new balance 2002r\b|nb 2002r", re.I), 140),
    (re.compile(r"samba\b.*adidas|adidas.*samba", re.I), 100),
    (re.compile(r"gazelle\b.*adidas|adidas.*gazelle", re.I), 100),
    (re.compile(r"ultraboost\b", re.I),            190),
    (re.compile(r"fear of god|fog\b", re.I),       150),
    (re.compile(r"essentials\b", re.I),            60),
    (re.compile(r"supreme.*box logo|box logo.*supreme", re.I), 180),
    (re.compile(r"arc.teryx.*jacket|jacket.*arc.teryx", re.I), 350),
    (re.compile(r"stone island.*crewneck|crewneck.*stone island", re.I), 300),
]


def _check_price_error(text: str, buy_price: float) -> dict | None:
    """Detect if an item is priced far below its known retail floor.
    Returns price error info or None."""
    if buy_price <= 0:
        return None
    for pattern, floor in _RETAIL_FLOORS:
        if pattern.search(text):
            if buy_price < floor * 0.45:
                return {
                    "is_price_error": True,
                    "expected_retail": floor,
                    "paid": buy_price,
                    "savings_pct": round((1 - buy_price / floor) * 100),
                    "severity": "extreme" if buy_price < floor * 0.3 else "likely",
                }
            break
    return None


def _parse_price(text: str) -> float:
    """Extract a numeric price from text like '$149.99' or '$85'."""
    m = _PRICE_RE.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    return 0.0


def _detect_brand_tier(text: str) -> tuple[str, float, float]:
    """Find the best-matching brand tier. Returns (brand, min_mult, max_mult)."""
    low = text.lower()
    best_brand = ""
    best_min = 0.8
    best_max = 1.0

    for brand, (mn, mx) in _BRAND_TIERS.items():
        if brand in low:
            if mx > best_max:
                best_brand = brand
                best_min = mn
                best_max = mx

    return best_brand, best_min, best_max


def _model_heat(text: str) -> float:
    """Score 0-1 based on specific hot model detection."""
    best = 0.0
    for pattern, heat in _HOT_MODEL_PATTERNS:
        if pattern.search(text):
            best = max(best, heat)
    return best


def _scarcity_score(text: str) -> float:
    """Score 0-1 based on scarcity/hype signals in the text."""
    low = text.lower()
    total = 0.0
    for signal, weight in _SCARCITY_SIGNALS.items():
        if signal in low:
            total += weight
    return min(total, 1.0)


def estimate_resale(deal: dict) -> dict:
    """
    Estimate resale potential for a deal.

    Returns:
        {
            "flip_score": 0-100,          # overall flip potential
            "est_resale_low": float,      # conservative resale estimate
            "est_resale_high": float,     # optimistic resale estimate
            "est_profit_low": float,      # min profit (resale_low - buy_price)
            "est_profit_high": float,     # max profit
            "flip_verdict": str,          # "strong flip" | "possible flip" | "hold value" | "depreciates"
            "brand_tier": str,
            "signals": list[str],         # what triggered the score
        }
    """
    title = deal.get("title", "")
    combined = f"{title} {deal.get('summary', '')} {deal.get('flair', '')}"

    buy_price = _parse_price(deal.get("price", ""))
    if not buy_price:
        buy_price = _parse_price(deal.get("original_price", ""))

    brand, tier_min, tier_max = _detect_brand_tier(combined)
    model = _model_heat(combined)
    scarcity = _scarcity_score(combined)
    disc_pct = deal.get("discount_pct", 0) or 0

    signals = []
    if brand:
        signals.append(f"brand:{brand}")
    if model > 0.3:
        signals.append("hot model")
    if scarcity > 0.2:
        signals.append("scarcity signals")
    if disc_pct >= 30:
        signals.append(f"{disc_pct}% below retail")

    # Heat score: weighted combination
    heat = (model * 0.4) + (scarcity * 0.35) + (min(disc_pct, 50) / 50 * 0.25)

    # Interpolate between tier min/max based on heat
    mult_low = tier_min + (tier_max - tier_min) * heat * 0.5
    mult_high = tier_min + (tier_max - tier_min) * heat

    # If buying at a discount, the effective multiplier against buy price is higher
    if disc_pct > 0:
        discount_boost = 1 + (disc_pct / 100)
        mult_low *= discount_boost
        mult_high *= discount_boost

    est_resale_low = round(buy_price * mult_low, 2) if buy_price else 0
    est_resale_high = round(buy_price * mult_high, 2) if buy_price else 0
    est_profit_low = round(est_resale_low - buy_price, 2) if buy_price else 0
    est_profit_high = round(est_resale_high - buy_price, 2) if buy_price else 0

    # Flip score: 0-100 composite
    flip_score = 0.0
    if buy_price:
        roi_low = est_profit_low / buy_price if buy_price else 0
        flip_score += min(roi_low * 100, 40)         # ROI contribution (capped 40)
    flip_score += model * 30                          # model heat (capped 30)
    flip_score += scarcity * 20                       # scarcity (capped 20)
    if brand:
        flip_score += 10                              # brand recognition
    flip_score = min(round(flip_score), 100)

    if flip_score >= 65:
        verdict = "strong flip"
    elif flip_score >= 40:
        verdict = "possible flip"
    elif flip_score >= 20:
        verdict = "hold value"
    else:
        verdict = "depreciates"

    price_error = _check_price_error(combined, buy_price)
    if price_error:
        signals.append("PRICE ERROR")
        flip_score = min(flip_score + 40, 100)
        if flip_score < 65:
            flip_score = 80
        verdict = "strong flip"

    roi_pct = round((est_profit_low / buy_price) * 100) if buy_price and est_profit_low > 0 else 0

    platforms = _recommend_platforms(combined, buy_price, model, brand)
    urgency = _assess_urgency(combined, disc_pct, model, scarcity)

    if price_error:
        urgency = {"level": "critical", "label": "ACT NOW",
                   "reason": f"Possible price error — {price_error['savings_pct']}% below retail ${price_error['expected_retail']:.0f}"}

    return {
        "flip_score": flip_score,
        "est_resale_low": est_resale_low,
        "est_resale_high": est_resale_high,
        "est_profit_low": est_profit_low,
        "est_profit_high": est_profit_high,
        "flip_verdict": verdict,
        "brand_tier": brand,
        "signals": signals,
        "roi_pct": roi_pct,
        "platforms": platforms,
        "urgency": urgency,
        "price_error": price_error,
    }


# ---------------------------------------------------------------------------
# Platform recommendations
# ---------------------------------------------------------------------------

_SNEAKER_PATTERNS = re.compile(
    r"dunk|jordan|air max|air force|yeezy|foam runner|slide|990|550|2002r|"
    r"gel-|samba|gazelle|forum|blazer|waffle|pegasus|vomero",
    re.IGNORECASE,
)


def _recommend_platforms(text: str, price: float, model_heat: float,
                         brand: str) -> list[dict]:
    """Recommend the best resale platforms for this item type."""
    platforms = []
    is_sneaker = bool(_SNEAKER_PATTERNS.search(text))
    low = text.lower()

    if is_sneaker or model_heat > 0.3:
        platforms.append({
            "name": "StockX",
            "why": "Largest sneaker market, instant price discovery",
            "fee_pct": 10,
            "best_for": "sneakers, hyped releases",
        })
        platforms.append({
            "name": "GOAT",
            "why": "Used + new options, good for worn pairs too",
            "fee_pct": 10,
            "best_for": "sneakers, rare finds",
        })

    if any(b in low for b in ("supreme", "palace", "kith", "bape", "gallery dept",
                               "chrome hearts", "rick owens", "raf simons", "amiri")):
        platforms.append({
            "name": "Grailed",
            "why": "Best for designer & streetwear apparel",
            "fee_pct": 9,
            "best_for": "clothing, accessories, designer",
        })

    platforms.append({
        "name": "eBay",
        "why": "Huge buyer pool, authenticity guarantee for $150+",
        "fee_pct": 13,
        "best_for": "everything, wider audience",
    })

    if not platforms or (is_sneaker and len(platforms) < 2):
        platforms.insert(0, {
            "name": "StockX",
            "why": "Price transparency, fast sales",
            "fee_pct": 10,
            "best_for": "sneakers",
        })

    return platforms[:3]


def _assess_urgency(text: str, disc_pct: int, model_heat: float,
                    scarcity: float) -> dict:
    """Assess how quickly the user should act."""
    low = text.lower()

    if any(kw in low for kw in ("restock", "just dropped", "limited", "qs",
                                 "quickstrike", "sold out")):
        return {"level": "critical", "label": "ACT NOW",
                "reason": "Limited stock — will sell out fast"}

    if scarcity > 0.3 or model_heat > 0.7:
        return {"level": "high", "label": "MOVE FAST",
                "reason": "High demand model — sizes disappear quickly"}

    if disc_pct >= 40:
        return {"level": "high", "label": "MOVE FAST",
                "reason": f"{disc_pct}% off won't last — deep discounts get cleared"}

    if disc_pct >= 25 and model_heat > 0.3:
        return {"level": "medium", "label": "ACT TODAY",
                "reason": "Good price on a popular model"}

    return {"level": "low", "label": "BROWSE",
            "reason": "Solid deal — take your time"}
