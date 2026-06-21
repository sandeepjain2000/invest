"""Industry vertical definitions for multi-sector scraping."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from project_paths import (
    INDUSTRIES_FILE,
    PROJECT_ROOT,
    SUBJECT_TEMPLATES_DIR,
)

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


def subject_template_path_for(body_template_file: str) -> Path:
    """
    Subject template paired with a body HTML file.
    partnership_coaching.html -> templates/subjects/partnership_coaching.subject.txt
    """
    stem = Path((body_template_file or "").strip()).stem or "partnership"
    return SUBJECT_TEMPLATES_DIR / f"{stem}.subject.txt"


def subject_template_file_for(industry_id: str) -> str | None:
    """Optional explicit subject template path in industries.json."""
    item = get_industry(industry_id)
    if not item:
        return None
    raw = (item.get("subject_template_file") or "").strip()
    return raw or None


def resolve_subject_template_path(industry_id: str, body_template_file: str) -> Path:
    explicit = subject_template_file_for(industry_id)
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else PROJECT_ROOT / path
    return subject_template_path_for(body_template_file)


_GENERIC_COMPANY_NAMES = frozenset(
    {
        "careers",
        "contact",
        "contact us",
        "home",
        "about us",
        "about",
        "vacancy list",
        "enquiries",
        "inquiry",
        "hello",
        "admin",
        "support",
        "sales",
        "office",
    }
)


def recipient_identity(*, company_name: str, domain: str) -> str:
    """Meaningful company/CA name, or domain when the name is a generic page title."""
    from immigration_db import clean_domain

    name = " ".join((company_name or "").split()).strip()
    dom = clean_domain(domain)
    if name and name.lower() not in _GENERIC_COMPANY_NAMES and len(name) >= 3:
        return name[:100]
    return dom


def render_subject_template(
    template: str,
    *,
    company_name: str = "",
    domain: str = "",
    college_name: str = "",
) -> str:
    """Fill {{RecipientCompany}}, {{Domain}}, {{RecipientIdentity}}, {{CollegeName}} in subject template."""
    from immigration_db import clean_domain

    line = (template or "").strip()
    if not line:
        return ""

    dom = clean_domain(domain)
    name = " ".join((company_name or "").split()).strip()
    identity = recipient_identity(company_name=company_name, domain=domain)
    college = " ".join((college_name or "").split()).strip()
    if len(college) > 120:
        college = college[:117].rstrip() + "..."

    return (
        line.replace("{{RecipientCompany}}", name)
        .replace("{{Domain}}", dom)
        .replace("{{CompanyDomain}}", dom)
        .replace("{{RecipientIdentity}}", identity)
        .replace("{{CollegeName}}", college)
        .strip()
    )


def load_subject_template_text(
    industry_id: str,
    body_template_file: str,
    *,
    company_name: str = "",
    domain: str = "",
    fallback_subject: str = "",
) -> str:
    """Load and render templates/subjects/{stem}.subject.txt (first line)."""
    path = resolve_subject_template_path(industry_id, body_template_file)
    if path.is_file():
        raw = path.read_text(encoding="utf-8").strip()
        line = raw.splitlines()[0].strip() if raw else ""
    else:
        line = (email_subject_for(industry_id) or fallback_subject or "").strip()

    return render_subject_template(line, company_name=company_name, domain=domain)


def email_subject_for(industry_id: str) -> str | None:
    item = get_industry(industry_id)
    if not item:
        return None
    raw = (item.get("email_subject") or "").strip()
    return raw or None

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
