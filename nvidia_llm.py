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


DEFAULT_SEARCH_SEEDS = [
    "immigration consultancy services",
    "visa immigration consultants",
    "student visa immigration agency",
    "work permit immigration advisors",
    "permanent residency immigration consultants",
    "overseas education and immigration services",
    "immigration law firm contact",
    "global mobility immigration services",
]


def generate_search_queries(count: int = 12, region: str = "India") -> list[str]:
    system = (
        "You generate Google search queries to discover immigration service provider "
        "company websites. Return only a JSON array of strings, no markdown."
    )
    user = (
        f"Create {count} distinct Google search query strings to find immigration "
        f"consultants, visa agencies, and immigration law firms in {region}. "
        "Use varied wording: student visa, work permit, PR, global mobility, "
        "overseas education + immigration, etc. Each query should be 4-10 words."
    )
    raw = call_nvidia_llm(system, user)
    queries = _parse_json_string_list(raw)
    if queries:
        return queries[:count]
    return DEFAULT_SEARCH_SEEDS[:count]


def generate_company_praise(company_name: str, website: str = "") -> str:
    system = (
        "Write one professional, warm sentence praising an immigration services "
        "company's work. Be specific but do not invent facts. No quotes, no greeting."
    )
    user = (
        f"Company: {company_name}\nWebsite: {website or 'unknown'}\n"
        "Mention their commitment to guiding clients through visa and immigration pathways."
    )
    line = call_nvidia_llm(system, user)
    if line:
        return line.strip().strip('"')
    return (
        f"I was impressed by {company_name}'s focus on helping clients navigate "
        "visa and immigration pathways with clarity and care."
    )


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
