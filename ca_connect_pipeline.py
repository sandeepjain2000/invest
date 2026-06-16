"""CA Connect (https://caconnect.icai.org/) integration for the main scrape pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from ca_connect_scraper import (
    DEFAULT_CREDENTIALS_FILE,
    DEFAULT_OUTPUT_FILE,
    load_credentials,
    resolve_search_params,
    run_ca_connect_search,
    save_results,
)
from immigration_db import ImmigrationDB, domain_from_ca_profile
from immigration_sender import get_ensure_industry_settings, load_sender_config

logger = logging.getLogger(__name__)

CA_INDUSTRY = "ca_cs_firms"


def get_ca_connect_settings() -> dict:
    cfg = load_sender_config()
    _, _, min_scrape = get_ensure_industry_settings()
    profile_limit = int(cfg.get("ca_connect_profiles_per_run", 0) or 0)
    if profile_limit <= 0:
        profile_limit = max(min_scrape, 5)
    return {
        "enabled": bool(cfg.get("ca_connect_enabled", True)),
        "profile_limit": profile_limit,
        "results_file": Path(cfg.get("ca_connect_results_file", str(DEFAULT_OUTPUT_FILE))),
        "credentials_file": Path(
            cfg.get("ca_connect_credentials_file", str(DEFAULT_CREDENTIALS_FILE))
        ),
        "browser": (cfg.get("ca_connect_browser") or "auto").strip(),
    }


def import_ca_listings_to_db(
    db: ImmigrationDB,
    listings: list[dict],
    *,
    industry: str = CA_INDUSTRY,
) -> dict:
    stats = {
        "listings_seen": 0,
        "companies_upserted": 0,
        "emails_added": 0,
        "skipped_no_email": 0,
    }
    for item in listings:
        stats["listings_seen"] += 1
        profile = item.get("profile") or {}
        email = (item.get("email") or profile.get("email") or "").strip().lower()
        if not email:
            stats["skipped_no_email"] += 1
            continue

        profile_url = (
            item.get("profile_url") or profile.get("profile_url") or ""
        ).strip()
        website = (
            item.get("website") or profile.get("website") or profile_url
        ).strip()
        domain = domain_from_ca_profile(profile_url) if profile_url else ""
        if not domain:
            continue

        name = (item.get("name") or profile.get("member_name") or domain).strip()
        mobile = (item.get("mobile") or profile.get("mobile") or "").strip()
        listing_type = item.get("listing_type") or "member"
        city = item.get("professional_city") or profile.get("professional_city_preference") or ""
        notes_parts = [f"CA Connect {listing_type}"]
        if city:
            notes_parts.append(city)
        if mobile:
            notes_parts.append(f"mobile:{mobile}")
        notes = " | ".join(notes_parts)

        company_id = db.upsert_company(
            name=name,
            website=website or profile_url,
            search_query_id=None,
            industry=industry,
            notes=notes,
            domain=domain,
        )
        if not company_id:
            continue
        stats["companies_upserted"] += 1
        if db.add_company_email(company_id, email, profile_url or website):
            stats["emails_added"] += 1
            logger.info("  CA import: %s <%s>", name, email)

    return stats


async def _run_ca_connect_async(
    db: ImmigrationDB,
    *,
    browser: str,
    profile_limit: int,
    results_file: Path,
    credentials_file: Path,
) -> dict:
    stats = {
        "ca_connect_ran": True,
        "ca_connect_listings": 0,
        "ca_profiles_enriched": 0,
        "ca_profiles_failed": 0,
        "ca_emails_imported": 0,
        "ca_connect_logged_in": False,
        "ca_connect_search": "",
        "error": "",
    }

    prior_results: list[dict] = []
    if results_file.exists():
        try:
            prior = json.loads(results_file.read_text(encoding="utf-8"))
            prior_results = prior.get("results") or []
        except Exception as exc:
            logger.warning("Could not read prior CA Connect results: %s", exc)

    try:
        credentials = load_credentials(credentials_file)
    except FileNotFoundError as exc:
        stats["error"] = str(exc)
        logger.error("%s", exc)
        return stats

    search = resolve_search_params(credentials, {})
    stats["ca_connect_search"] = f"{search['service']} | {search['state']} | {search['city']}"

    ca_stats = await run_ca_connect_search(
        service=search["service"],
        state=search["state"],
        city=search["city"],
        credentials=credentials,
        browser=browser,
        do_login=True,
        headless=True,
        enrich_profiles=True,
        profile_limit=profile_limit,
        prior_results=prior_results,
    )
    stats["ca_connect_logged_in"] = bool(ca_stats.get("logged_in"))
    stats["browser_used"] = ca_stats.get("browser_used", "")
    if not stats["ca_connect_logged_in"]:
        stats["error"] = "CA Connect login failed — profile emails require credentials."
        logger.error(stats["error"])
        return stats

    stats["ca_profiles_enriched"] = int(ca_stats.get("profiles_enriched") or 0)
    stats["ca_profiles_failed"] = int(ca_stats.get("profiles_failed") or 0)
    stats["ca_connect_listings"] = int(ca_stats.get("result_count") or 0)

    save_results(ca_stats, results_file)
    import_stats = import_ca_listings_to_db(db, ca_stats.get("results") or [])
    stats["ca_emails_imported"] = import_stats["emails_added"]
    stats["ca_companies_upserted"] = import_stats["companies_upserted"]

    logger.info(
        "CA Connect: %s listings, enriched %s profiles, imported %s new email(s).",
        stats["ca_connect_listings"],
        stats["ca_profiles_enriched"],
        stats["ca_emails_imported"],
    )
    return stats


async def run_ca_connect_for_pipeline_async(
    db: ImmigrationDB,
    *,
    browser: str = "auto",
) -> dict:
    settings = get_ca_connect_settings()
    if not settings["enabled"]:
        logger.info("CA Connect scrape disabled in sender_config.json.")
        return {"ca_connect_ran": False}

    return await _run_ca_connect_async(
        db,
        browser=browser or settings["browser"],
        profile_limit=settings["profile_limit"],
        results_file=settings["results_file"],
        credentials_file=settings["credentials_file"],
    )


def run_ca_connect_for_pipeline(
    db: ImmigrationDB,
    *,
    browser: str = "auto",
) -> dict:
    """Sync entry point for standalone use (not inside an active event loop)."""
    return asyncio.run(run_ca_connect_for_pipeline_async(db, browser=browser))
