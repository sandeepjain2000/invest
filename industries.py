"""Industry vertical definitions for multi-sector scraping."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

INDUSTRIES_FILE = Path(__file__).resolve().parent / "industries.json"

_cache: dict[str, Any] | None = None


def load_industries_config() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    data = json.loads(INDUSTRIES_FILE.read_text(encoding="utf-8"))
    _cache = data
    return data


def list_industries(*, active_only: bool = True) -> list[dict[str, Any]]:
    items = load_industries_config().get("industries", [])
    if active_only:
        items = [i for i in items if i.get("active", True)]
    return sorted(items, key=lambda x: int(x.get("rank", 999)))


def get_industry(industry_id: str) -> dict[str, Any] | None:
    industry_id = (industry_id or "").strip().lower()
    for item in load_industries_config().get("industries", []):
        if item.get("id", "").lower() == industry_id:
            return item
    return None


def industry_ids(*, active_only: bool = True) -> list[str]:
    return [i["id"] for i in list_industries(active_only=active_only)]


def randomized_industry_ids(*, active_only: bool = True) -> list[str]:
    """Active industry IDs in random order — new shuffle on every call."""
    ids = industry_ids(active_only=active_only)
    random.shuffle(ids)
    return ids


def default_region() -> str:
    return load_industries_config().get("region_default", "India")


def queries_per_industry() -> int:
    return int(load_industries_config().get("queries_per_industry", 6))


def seed_queries_for(industry_id: str) -> list[str]:
    item = get_industry(industry_id)
    if not item:
        return []
    return [str(q).strip() for q in item.get("seed_queries", []) if str(q).strip()]


def praise_hint_for(industry_id: str) -> str:
    item = get_industry(industry_id)
    if not item:
        return "student career outcomes and institutional partnerships"
    return (item.get("praise_hint") or "").strip() or "student career outcomes"


def industry_name(industry_id: str) -> str:
    item = get_industry(industry_id)
    return (item or {}).get("name") or industry_id


def template_file_for(industry_id: str) -> str | None:
    item = get_industry(industry_id)
    if not item:
        return None
    raw = (item.get("template_file") or "").strip()
    return raw or None


def email_subject_for(industry_id: str) -> str | None:
    item = get_industry(industry_id)
    if not item:
        return None
    raw = (item.get("email_subject") or "").strip()
    return raw or None


def subject_append_domain_for(industry_id: str) -> bool:
    item = get_industry(industry_id)
    if not item:
        return True
    return bool(item.get("subject_append_domain", True))


def use_nvidia_praise_for(industry_id: str) -> bool:
    item = get_industry(industry_id)
    if not item:
        return True
    return bool(item.get("use_nvidia_praise", True))


def signature_links_for(industry_id: str) -> list[dict[str, str]] | None:
    item = get_industry(industry_id)
    if not item:
        return None
    links = item.get("signature_links")
    if not isinstance(links, list) or not links:
        return None
    return links


def scrape_source_for(industry_id: str) -> str:
    item = get_industry(industry_id)
    if not item:
        return "google"
    return (item.get("scrape_source") or "google").strip().lower()


def google_scrape_industry_ids(*, active_only: bool = True) -> list[str]:
    """Industry IDs handled by the Google/website scraper (excludes e.g. ca_connect)."""
    return [
        i["id"]
        for i in list_industries(active_only=active_only)
        if scrape_source_for(i["id"]) != "ca_connect"
    ]


def industry_is_active(industry_id: str) -> bool:
    item = get_industry(industry_id)
    if not item:
        return True
    return bool(item.get("active", True))


def inactive_industry_ids() -> list[str]:
    return [
        i["id"]
        for i in load_industries_config().get("industries", [])
        if not i.get("active", True)
    ]
