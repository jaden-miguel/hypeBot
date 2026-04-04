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
Streetwear analyst. SKIP anything not clothing/footwear/accessories. \
SKIP if not currently available to purchase. Only "recommended" or "watch" for buyable items.
Respond ONLY as JSON:
{"verdict":"recommended"|"skip"|"watch","brand":"NAME","hype_score":1-10,"trending":BOOL,"available_now":BOOL,"summary":"1 sentence"}"""

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
        prompt_parts.append(f"Details: {summary[:200]}")

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
                "options": {"num_predict": 120, "temperature": 0.3},
            },
            timeout=30,
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

    # Fix common LLM quirks: TRUE/FALSE → true/false, trailing commas
    import re
    raw = raw.replace("TRUE", "true").replace("FALSE", "false")
    raw = raw.replace("True", "true").replace("False", "false")
    raw = re.sub(r",\s*}", "}", raw)
    raw = re.sub(r",\s*]", "]", raw)

    try:
        data = json.loads(raw)
        if isinstance(data.get("hype_score"), str) or "<" in str(data.get("brand", "")):
            log.warning("LLM returned template placeholder — defaulting to skip")
            return {"verdict": "skip", "brand": "unknown", "hype_score": 0,
                    "available_now": False, "summary": ""}
        return data
    except json.JSONDecodeError:
        log.warning("Could not parse LLM JSON: %.200s", raw)
        return {"verdict": "skip", "brand": "unknown", "hype_score": 0,
                "available_now": False, "summary": raw[:300]}


def health_check() -> bool:
    try:
        r = _get_session().get(f"{config.OLLAMA_HOST}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False
