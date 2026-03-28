"""
AI analysis via Ollama — sends deals to your local LLM for evaluation.
Connects to your Ollama instance (exposed on 11434) or Open WebUI (port 3000).
"""

import logging
import requests

import config

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a streetwear deals analyst. You track brands like Supreme, \
Kith, Palace, Nike, Jordan, Yeezy, Arc'teryx, The North Face, Raf Simons, Rick Owens, \
Fear of God, Stone Island, Off-White, and more.

When given a deal or product listing, respond with a brief JSON object:
{
  "verdict": "cop" | "pass" | "maybe",
  "brand": "<detected brand>",
  "hype_score": <1-10>,
  "summary": "<1-2 sentence take on the deal>"
}

Only output the JSON, nothing else."""


def analyze_deal(title: str, summary: str = "", price: str = "") -> dict | None:
    """Send a deal to Ollama and parse the verdict."""
    prompt_parts = [f"Product: {title}"]
    if price:
        prompt_parts.append(f"Price: {price}")
    if summary:
        prompt_parts.append(f"Details: {summary[:400]}")
    prompt = "\n".join(prompt_parts)

    try:
        resp = requests.post(
            f"{config.OLLAMA_HOST}/api/chat",
            json={
                "model": config.MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        return _parse_verdict(content)
    except Exception:
        log.exception("Ollama analysis failed for: %s", title)
        return None


def _parse_verdict(raw: str) -> dict:
    """Best-effort parse of the LLM JSON response."""
    import json

    raw = raw.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Could not parse LLM response as JSON: %s", raw[:200])
        return {
            "verdict": "maybe",
            "brand": "unknown",
            "hype_score": 5,
            "summary": raw[:300],
        }


def health_check() -> bool:
    """Quick ping to verify Ollama is reachable."""
    try:
        r = requests.get(f"{config.OLLAMA_HOST}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False
