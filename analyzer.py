"""
AI analysis via Ollama — persistent session with keep-alive.
"""

import json
import logging

import requests
import requests.adapters

import config

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an elite streetwear intelligence analyst. You deeply understand hype culture, \
resale markets, rep communities (r/fashionreps, r/qualityreps), and retail drops.

Brands you track: Supreme, Kith, Palace, Stussy, BAPE, Nike, Jordan, Adidas, Yeezy, \
New Balance, Raf Simons, Rick Owens, Fear of God, Essentials, Arc'teryx, The North Face, \
Stone Island, Off-White, Rhude, Gallery Dept, Corteiz, Represent, Amiri, Chrome Hearts.

You understand:
- Reddit community signals: high upvotes + comments = community is hyped
- Flair tags: [W2C] = someone wants it (demand signal), [FIND] = new source discovered, \
  [QC] = quality check (people buying), [REVIEW] = proven product, [DEAL] = price drop
- Resale value indicators: limited drops, collabs, and OOS items hold/gain value
- Seasonal trends: what's hot right now vs. played out

Respond ONLY with this JSON:
{
  "verdict": "cop" | "pass" | "maybe",
  "brand": "<brand name>",
  "hype_score": <1-10>,
  "trending": true | false,
  "summary": "<1-2 sentence verdict with WHY — mention resale potential, community buzz, or value>"
}"""

_session = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=2, pool_maxsize=2,
            max_retries=requests.adapters.Retry(total=1, backoff_factor=0.5),
        )
        _session.mount("http://", adapter)
    return _session


def analyze_deal(
    title: str,
    summary: str = "",
    price: str = "",
    upvotes: int = 0,
    comments: int = 0,
    source: str = "",
    flair: str = "",
) -> dict | None:
    prompt_parts = [f"Product: {title}"]
    if price:
        prompt_parts.append(f"Price: {price}")
    if source:
        prompt_parts.append(f"Source: {source}")
    if flair:
        prompt_parts.append(f"Flair/Tag: {flair}")
    if upvotes or comments:
        prompt_parts.append(f"Community: {upvotes} upvotes, {comments} comments")
    if summary:
        prompt_parts.append(f"Details: {summary[:400]}")

    try:
        resp = _get_session().post(
            f"{config.OLLAMA_HOST}/api/chat",
            json={
                "model": config.MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "\n".join(prompt_parts)},
                ],
                "stream": False,
                "options": {"num_predict": 200},
            },
            timeout=45,
        )
        resp.raise_for_status()
        return _parse_verdict(resp.json()["message"]["content"])
    except Exception:
        log.exception("Ollama analysis failed for: %s", title)
        return None


def _parse_verdict(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Could not parse LLM JSON: %.200s", raw)
        return {
            "verdict": "maybe",
            "brand": "unknown",
            "hype_score": 5,
            "summary": raw[:300],
        }


def health_check() -> bool:
    try:
        r = _get_session().get(f"{config.OLLAMA_HOST}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False
