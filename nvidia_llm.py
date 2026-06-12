"""NVIDIA NIM client with API-key rotation."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

NVIDIA_KEYS_DIR = Path(
    os.environ.get(
        "NVIDIA_KEYS_DIR",
        r"C:\Users\sandeep\Downloads\Claudes\nvidia_keys",
    )
)
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = "meta/llama-3.3-70b-instruct"

_key_index = 0
_keys_cache: list[str] | None = None


def load_nvidia_keys(keys_dir: Path | None = None) -> list[str]:
    global _keys_cache
    if _keys_cache is not None:
        return _keys_cache

    directory = keys_dir or NVIDIA_KEYS_DIR
    keys: list[str] = []
    if not directory.exists():
        logger.warning("NVIDIA keys directory not found: %s", directory)
        _keys_cache = keys
        return keys

    for path in sorted(directory.glob("key*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            key = (data.get("api_key") or "").strip()
            if key:
                keys.append(key)
        except Exception as exc:
            logger.warning("Failed to read %s: %s", path.name, exc)

    logger.info("Loaded %s NVIDIA API key(s) for rotation.", len(keys))
    _keys_cache = keys
    return keys


def get_next_nvidia_key() -> str:
    global _key_index
    keys = load_nvidia_keys()
    if not keys:
        return ""
    key = keys[_key_index % len(keys)]
    _key_index += 1
    return key


def _call_chat_completions(
    *,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int = 30,
) -> str:
    payload = {
        "model": NVIDIA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 512,
    }
    req = urllib.request.Request(
        NVIDIA_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return (data["choices"][0]["message"]["content"] or "").strip()


def call_nvidia_llm(system_prompt: str, user_prompt: str) -> str:
    keys = load_nvidia_keys()
    max_attempts = min(5, len(keys)) if keys else 1
    for attempt in range(max_attempts):
        api_key = get_next_nvidia_key()
        if not api_key:
            logger.error("No NVIDIA API key available.")
            return ""
        try:
            return _call_chat_completions(
                api_key=api_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except Exception as exc:
            logger.warning(
                "NVIDIA call failed (attempt %s/%s): %s — rotating key.",
                attempt + 1,
                max_attempts,
                exc,
            )
            time.sleep(1)
    return ""


from industries import (
    default_region,
    get_industry,
    industry_name,
    list_industries,
    praise_hint_for,
    queries_per_industry,
    randomized_industry_ids,
    seed_queries_for,
)


def generate_search_queries(
    count: int = 12,
    region: str | None = None,
    *,
    industry_id: str = "overseas_education_immigration",
) -> list[str]:
    region = region or default_region()
    industry = get_industry(industry_id) or {}
    name = industry.get("name") or industry_id
    examples = industry.get("seed_queries") or seed_queries_for(industry_id)
    example_text = "\n".join(f"- {q}" for q in examples[:4])

    system = (
        "You generate Google search queries to discover company websites for outreach. "
        "Return only a JSON array of strings, no markdown."
    )
    user = (
        f"Create {count} distinct Google search query strings to find {name} in {region}. "
        f"Focus on companies that work with students, graduates, colleges, or employability. "
        f"Each query should be 4-12 words and suitable for finding contact pages.\n"
        f"Example queries for this sector:\n{example_text}"
    )
    raw = call_nvidia_llm(system, user)
    queries = _parse_json_string_list(raw)
    if queries:
        return queries[:count]
    return seed_queries_for(industry_id)[:count]


def generate_queries_for_all_industries(
    *,
    region: str | None = None,
    per_industry: int | None = None,
    use_nvidia: bool = True,
) -> dict[str, list[str]]:
    region = region or default_region()
    per = per_industry or queries_per_industry()
    out: dict[str, list[str]] = {}
    for iid in randomized_industry_ids(active_only=True):
        if use_nvidia:
            queries = generate_search_queries(count=per, region=region, industry_id=iid)
        else:
            queries = seed_queries_for(iid)[:per]
        if not queries:
            queries = seed_queries_for(iid)[:per]
        out[iid] = queries
    return out


def generate_company_praise(
    company_name: str,
    website: str = "",
    *,
    industry_id: str = "overseas_education_immigration",
) -> str:
    hint = praise_hint_for(industry_id)
    sector = industry_name(industry_id)
    system = (
        "Write one professional, warm sentence praising a company's work. "
        "Be specific but do not invent facts. No quotes, no greeting."
    )
    user = (
        f"Company: {company_name}\n"
        f"Sector: {sector}\n"
        f"Website: {website or 'unknown'}\n"
        f"Mention their strengths related to {hint}."
    )
    line = call_nvidia_llm(system, user)
    if line:
        return line.strip().strip('"')
    return (
        f"I was impressed by {company_name}'s work in {hint} "
        "and the value it creates for students and institutions."
    )


def classify_partnership_reply(
    *,
    subject: str,
    from_addr: str,
    body: str,
    has_reply_headers: bool,
    from_sent_recipient: bool,
    subject_matches_campaign: bool,
    thread_message_id_match: bool,
) -> dict:
    """NVIDIA classification for borderline human-reply detection."""
    truncated = (body or "")[:4000]
    system = (
        "You analyze inbound email for a partnership outreach campaign (campus placements, "
        "EdTech, recruitment, immigration consultancies). Decide if this is a genuine human "
        "reply to Sandeep's outreach that should be forwarded.\n\n"
        "Rules:\n"
        "1. MUST be a human reply (not cold mail, newsletter, notification, spam).\n"
        "2. MUST NOT be auto-response (OOO, mailer-daemon, automated confirmations).\n"
        "3. Subject line alone with Re: is NOT enough — use the signals provided.\n"
        "4. Forward interest, questions, meeting requests, or polite engagement.\n"
        "5. If unsure, should_forward=false.\n\n"
        "Output ONLY raw JSON:\n"
        '{"is_reply":bool,"is_auto_response":bool,"should_forward":bool,"reason":"..."}'
    )
    user = (
        f"From: {from_addr}\n"
        f"Subject: {subject}\n"
        f"Thread Message-ID match to our sent mail: {thread_message_id_match}\n"
        f"From address was a campaign recipient: {from_sent_recipient}\n"
        f"Has In-Reply-To/References headers: {has_reply_headers}\n"
        f"Subject matches known campaign subject list: {subject_matches_campaign}\n\n"
        f"BODY:\n{truncated}"
    )
    fallback = {
        "is_reply": False,
        "is_auto_response": False,
        "should_forward": False,
        "reason": "LLM unavailable or parse error",
    }
    raw = call_nvidia_llm(system, user)
    if not raw:
        return fallback
    try:
        text = raw.strip()
        if "```" in text:
            for part in text.split("```"):
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    text = part
                    break
        data = json.loads(text)
        return {
            "is_reply": bool(data.get("is_reply", False)),
            "is_auto_response": bool(data.get("is_auto_response", False)),
            "should_forward": bool(data.get("should_forward", False)),
            "reason": str(data.get("reason", "")),
        }
    except Exception as exc:
        logger.warning("Failed to parse reply classification JSON: %s", exc)
        return fallback


def _parse_json_string_list(raw: str) -> list[str]:
    if not raw:
        return []
    text = raw.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("["):
                text = part
                break
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except json.JSONDecodeError:
        pass
    return [line.strip("-• ").strip() for line in text.splitlines() if line.strip()]
