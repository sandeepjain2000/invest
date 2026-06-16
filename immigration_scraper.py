"""Browser-based discovery of immigration providers and email extraction."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Iterable
from urllib.parse import quote_plus, urlparse

from playwright.async_api import Page, async_playwright

from browser_utils import dismiss_page_obstructions, launch_context, scrape_complete_beep
from immigration_db import ImmigrationDB, clean_domain
from immigration_sender import get_ensure_industry_settings, load_sender_config
from pipeline_notify import notify_stage
from industries import (
    default_region,
    google_scrape_industry_ids,
    queries_per_industry,
    scrape_source_for,
    seed_queries_for,
)
from nvidia_llm import generate_queries_for_all_industries, generate_search_queries
from ca_connect_pipeline import run_ca_connect_for_pipeline_async

logger = logging.getLogger(__name__)

SITE_HARD_LIMIT_SEC = 180
GOTO_TIMEOUT_MS = 60_000
STEP_LOAD_TIMEOUT_MS = 20_000
PAGE_DEFAULT_TIMEOUT_MS = 45_000
SEARCH_RESULT_LIMIT = 12

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9][a-zA-Z0-9._%+\-]*@[a-zA-Z0-9][a-zA-Z0-9.\-]*\.[a-zA-Z]{2,}"
)

SKIP_DOMAINS = {
    "google.com",
    "google.co.in",
    "youtube.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "wikipedia.org",
    "yelp.com",
    "justdial.com",
    "indiamart.com",
    "sulekha.com",
    "quora.com",
    "reddit.com",
}

SKIP_EMAIL_SUFFIXES = (
    "@sentry.io",
    "@wixpress.com",
    "@example.com",
    "@email.com",
    "@domain.com",
    "@yourcompany.com",
    "@test.com",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
)

CONTACT_SELECTORS = [
    'a[href*="contact" i]',
    'a:has-text("Contact")',
    'a:has-text("Contact Us")',
    'a:has-text("Get in touch")',
    'a:has-text("Reach us")',
    'button:has-text("Contact")',
    'nav a[href*="contact" i]',
]


def normalize_emails(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in EMAIL_RE.findall(text or ""):
        email = match.strip().lower().rstrip(".,;)")
        if any(email.endswith(s) or s in email for s in SKIP_EMAIL_SUFFIXES):
            continue
        if email in seen:
            continue
        domain = email.split("@", 1)[-1]
        if domain in SKIP_DOMAINS:
            continue
        seen.add(email)
        found.append(email)
    return found


def clean_company_title(title: str, domain: str) -> str:
    text = (title or "").replace("\u2013", "-").replace("\u2014", "-")
    first = text.split("\n")[0].strip()
    for sep in ("|", " - ", " – "):
        if sep in first:
            first = first.split(sep)[0].strip()
    first = re.sub(r"https?://\S+", "", first).strip(" -")
    if len(first) >= 3:
        return first
    base = domain.split(".")[0] if domain else "Company"
    return base.replace("-", " ").title()


def should_skip_result_url(url: str) -> bool:
    domain = clean_domain(url)
    if not domain:
        return True
    return any(domain == skip or domain.endswith("." + skip) for skip in SKIP_DOMAINS)


async def google_search_urls(page: Page, query: str, limit: int = SEARCH_RESULT_LIMIT) -> list[dict]:
    url = f"https://www.google.com/search?q={quote_plus(query)}&num={limit}"
    logger.info("Google search: %s", query)
    await page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
    await dismiss_page_obstructions(page)
    await page.wait_for_timeout(2000)

    results: list[dict] = []
    anchors = page.locator("#search a[href^='http']")
    count = await anchors.count()
    for i in range(count):
        if len(results) >= limit:
            break
        try:
            href = await anchors.nth(i).get_attribute("href")
            title = (await anchors.nth(i).inner_text(timeout=2000) or "").strip()
        except Exception:
            continue
        if not href or should_skip_result_url(href):
            continue
        domain = clean_domain(href)
        if any(r["domain"] == domain for r in results):
            continue
        results.append(
            {
                "title": clean_company_title(title, domain),
                "url": href,
                "domain": domain,
            }
        )
    logger.info("  Found %s unique result(s).", len(results))
    return results


async def click_contact_if_needed(page: Page) -> str | None:
    await dismiss_page_obstructions(page, max_rounds=2)
    for sel in CONTACT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=2000):
                await loc.click(timeout=10000)
                await page.wait_for_load_state("domcontentloaded", timeout=GOTO_TIMEOUT_MS)
                await page.wait_for_timeout(1500)
                return page.url
        except Exception:
            continue
    return None


async def extract_emails_from_page(page: Page) -> tuple[list[str], str]:
    html = await page.content()
    body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    emails = normalize_emails(html + "\n" + body_text)
    return emails, page.url


async def _abort_slow_page(page: Page) -> None:
    """Navigate away from a hung page so the next site can load."""
    try:
        await page.goto("about:blank", wait_until="commit", timeout=5000)
    except Exception:
        pass


async def _scrape_company_emails_impl(page: Page, website: str) -> tuple[list[str], str, str]:
    notes = ""
    source = website
    try:
        await page.goto(website, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
    except Exception as exc:
        return [], website, f"navigation_failed: {exc}"

    await dismiss_page_obstructions(page)
    try:
        await page.wait_for_load_state("networkidle", timeout=STEP_LOAD_TIMEOUT_MS)
    except Exception:
        await page.wait_for_timeout(3000)
    await dismiss_page_obstructions(page)

    emails, source = await extract_emails_from_page(page)
    if emails:
        return emails, source, notes

    contact_url = await click_contact_if_needed(page)
    if contact_url:
        await dismiss_page_obstructions(page)
        try:
            await page.wait_for_load_state("networkidle", timeout=STEP_LOAD_TIMEOUT_MS)
        except Exception:
            await page.wait_for_timeout(2000)
        await dismiss_page_obstructions(page)
        emails, source = await extract_emails_from_page(page)
        if emails:
            return emails, source, notes

    if "@" not in (await page.content()):
        notes = "no_at_symbol_on_page"
    else:
        notes = "at_symbol_but_no_valid_email"
    return [], source, notes


async def scrape_company_emails(page: Page, website: str) -> tuple[list[str], str, str]:
    """
    Scrape one company site with a hard wall-clock cap (3 minutes).
    Moves on to the next site if the cap is exceeded.
    """
    try:
        return await asyncio.wait_for(
            _scrape_company_emails_impl(page, website),
            timeout=SITE_HARD_LIMIT_SEC,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Hard timeout (%ss) — moving on: %s",
            SITE_HARD_LIMIT_SEC,
            website,
        )
        await _abort_slow_page(page)
        return [], website, f"site_timeout_{SITE_HARD_LIMIT_SEC}s"
    except Exception as exc:
        logger.warning("Scrape error for %s: %s", website, exc)
        await _abort_slow_page(page)
        return [], website, f"scrape_error: {exc}"


def _auto_seed_queries(
    db: ImmigrationDB,
    *,
    region: str,
    industry: str | None,
    use_nvidia: bool,
) -> int:
    added = 0
    google_ids = set(google_scrape_industry_ids())
    if industry:
        if industry not in google_ids:
            return 0
        queries = (
            generate_search_queries(
                count=queries_per_industry(),
                region=region,
                industry_id=industry,
            )
            if use_nvidia
            else seed_queries_for(industry)[: queries_per_industry()]
        )
        if not queries:
            queries = seed_queries_for(industry)
        added += db.add_search_queries(queries, industry=industry, source="nvidia" if use_nvidia else "seed")
        return added

    batch = generate_queries_for_all_industries(
        region=region,
        per_industry=queries_per_industry(),
        use_nvidia=use_nvidia,
    )
    for iid, queries in batch.items():
        if iid not in google_ids:
            continue
        added += db.add_search_queries(
            queries,
            industry=iid,
            source="nvidia" if use_nvidia else "seed",
        )
    return added


def _scrape_targets_met(
    stats: dict,
    *,
    email_target: int,
    ensure_industry: str | None,
    min_ensure_scrape: int,
) -> bool:
    if stats["companies_with_email"] < email_target:
        return False
    if ensure_industry and min_ensure_scrape > 0:
        key = ensure_industry.strip().lower()
        have = int(stats.get("ensure_industry_with_email", 0))
        if have < min_ensure_scrape:
            return False
    return True


async def run_scrape(
    db: ImmigrationDB,
    *,
    max_companies: int = 50,
    max_queries: int = 20,
    email_target: int = 10,
    browser: str = "auto",
    region: str | None = None,
    industry: str | None = None,
    seed_keywords: bool = True,
    use_nvidia_seed: bool = True,
    dry_run: bool = False,
) -> dict:
    region = region or default_region()
    email_target = max(1, int(email_target or 10))
    cfg = load_sender_config()
    notify_stage(cfg, "scrape_start", dry_run=dry_run)
    ensure_industry, _, min_ensure_scrape = get_ensure_industry_settings()
    ensure_via_ca_connect = bool(
        ensure_industry and scrape_source_for(ensure_industry) == "ca_connect"
    )
    apply_ensure_scrape = bool(not industry and ensure_industry and min_ensure_scrape > 0)
    google_exclude = [
        iid
        for iid in google_scrape_industry_ids(active_only=False)
        if scrape_source_for(iid) == "ca_connect"
    ]
    google_only = not industry or scrape_source_for(industry) != "ca_connect"

    stats = {
        "queries_run": 0,
        "companies_scraped": 0,
        "companies_with_email": 0,
        "emails_found": 0,
        "email_target": email_target,
        "max_companies": max_companies,
        "browser_used": "",
        "industry_filter": industry or "all",
        "ensure_industry_scrape": ensure_industry if apply_ensure_scrape else "",
        "ensure_industry_scrape_target": min_ensure_scrape if apply_ensure_scrape else 0,
        "ensure_industry_with_email": 0,
        "ca_connect": {},
    }

    if run_ca_connect_for_pipeline_async and (not industry or ensure_via_ca_connect):
        logger.info("=== CA Connect scrape (https://caconnect.icai.org/) ===")
        notify_stage(cfg, "ca_connect_start", dry_run=dry_run)
        ca_stats = await run_ca_connect_for_pipeline_async(db, browser=browser)
        stats["ca_connect"] = ca_stats
        if ca_stats.get("ca_connect_ran"):
            imported = int(ca_stats.get("ca_emails_imported") or 0)
            stats["emails_found"] += imported
            stats["companies_with_email"] += imported
            if ensure_via_ca_connect:
                stats["ensure_industry_with_email"] = imported
            stats["browser_used"] = stats["browser_used"] or ca_stats.get("browser_used", "")
            notify_stage(
                cfg,
                "ca_connect_complete",
                dry_run=dry_run,
                imported=imported,
            )

    if not google_only:
        logger.info(
            "Industry %s uses CA Connect only — skipping Google website scrape.",
            industry,
        )
        if apply_ensure_scrape:
            logger.info(
                "Scrape targets: %s companies with email (goal %s), "
                "%s from %s (goal %s).",
                stats["companies_with_email"],
                email_target,
                stats["ensure_industry_with_email"],
                ensure_industry,
                min_ensure_scrape,
            )
        return stats

    notify_stage(cfg, "google_scrape_start", dry_run=dry_run)

    if apply_ensure_scrape:
        logger.info(
            "Scrape targets: up to %s companies, stop after %s with email "
            "(incl. at least %s from %s via %s)",
            max_companies,
            email_target,
            min_ensure_scrape,
            ensure_industry,
            "CA Connect" if ensure_via_ca_connect else "Google",
        )
    else:
        logger.info(
            "Scrape targets: up to %s companies, stop early after %s company(ies) with email",
            max_companies,
            email_target,
        )

    if not industry:
        logger.info(
            "Industry selection: random order on each query pick (distributes across verticals)"
        )

    prefer_google = None
    if (
        apply_ensure_scrape
        and ensure_industry
        and not ensure_via_ca_connect
        and stats["ensure_industry_with_email"] < min_ensure_scrape
    ):
        prefer_google = ensure_industry

    pending = db.next_pending_query(
        industry if google_only else None,
        prefer_industry=prefer_google,
        exclude_industries=google_exclude if not industry else None,
    )
    if not pending and seed_keywords and google_only:
        added = _auto_seed_queries(
            db,
            region=region,
            industry=industry,
            use_nvidia=use_nvidia_seed,
        )
        logger.info("Seeded %s search query(ies) across industry scope.", added)
        pending = db.next_pending_query(
            industry,
            prefer_industry=prefer_google,
            exclude_industries=google_exclude if not industry else None,
        )

    async with async_playwright() as playwright:
        context, browser_used = await launch_context(playwright, browser)
        stats["browser_used"] = browser_used or stats["browser_used"]
        logger.info("Using browser: %s", stats["browser_used"])
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(PAGE_DEFAULT_TIMEOUT_MS)

        queries_done = 0
        while stats["companies_scraped"] < max_companies:
            if _scrape_targets_met(
                stats,
                email_target=email_target,
                ensure_industry=ensure_industry if apply_ensure_scrape else None,
                min_ensure_scrape=min_ensure_scrape,
            ):
                if apply_ensure_scrape:
                    logger.info(
                        "Scrape targets met: %s companies with email (goal %s), "
                        "%s from %s (goal %s) — stopping.",
                        stats["companies_with_email"],
                        email_target,
                        stats["ensure_industry_with_email"],
                        ensure_industry,
                        min_ensure_scrape,
                    )
                else:
                    logger.info(
                        "Email target reached (%s companies with email, goal %s) — stopping scrape.",
                        stats["companies_with_email"],
                        email_target,
                    )
                break

            if queries_done >= max_queries:
                if apply_ensure_scrape and stats["ensure_industry_with_email"] < min_ensure_scrape:
                    logger.warning(
                        "Reached max_queries (%s) with only %s/%s %s companies with email.",
                        max_queries,
                        stats["ensure_industry_with_email"],
                        min_ensure_scrape,
                        ensure_industry,
                    )
                else:
                    logger.info("Reached max_queries cap (%s) for this run.", max_queries)
                break

            prefer_query = None
            if (
                apply_ensure_scrape
                and ensure_industry
                and not ensure_via_ca_connect
                and stats["ensure_industry_with_email"] < min_ensure_scrape
            ):
                prefer_query = ensure_industry

            row = db.next_pending_query(
                industry,
                prefer_industry=prefer_query,
                exclude_industries=google_exclude if not industry else None,
            )
            if not row and seed_keywords:
                added = _auto_seed_queries(
                    db,
                    region=region,
                    industry=industry,
                    use_nvidia=use_nvidia_seed,
                )
                if added:
                    logger.info("Seeded %s more search query(ies) to continue scraping.", added)
                row = db.next_pending_query(
                    industry,
                    prefer_industry=prefer_query,
                    exclude_industries=google_exclude if not industry else None,
                )
            if not row:
                logger.info("No more search queries available.")
                break

            query_id = int(row["id"])
            query_text = row["query_text"]
            query_industry = row["industry"] if "industry" in row.keys() else "overseas_education_immigration"
            logger.info("Industry: %s | Query: %s", query_industry, query_text)
            results = await google_search_urls(page, query_text)
            stats["queries_run"] += 1
            queries_done += 1

            for result in results:
                if stats["companies_scraped"] >= max_companies:
                    break
                domain = result["domain"]
                if db.domain_exists(domain):
                    logger.info("Skip duplicate domain: %s", domain)
                    continue

                company_id = db.upsert_company(
                    name=result["title"],
                    website=result["url"],
                    search_query_id=query_id,
                    industry=query_industry,
                )
                if not company_id:
                    continue

                stats["companies_scraped"] += 1
                logger.info("Scraping %s (%s)", result["title"], result["url"])
                emails, source_page, notes = await scrape_company_emails(page, result["url"])
                if emails:
                    stats["companies_with_email"] += 1
                    if (
                        apply_ensure_scrape
                        and ensure_industry
                        and not ensure_via_ca_connect
                        and (query_industry or "").lower() == ensure_industry.lower()
                    ):
                        stats["ensure_industry_with_email"] += 1
                    for email in emails:
                        if db.add_company_email(company_id, email, source_page):
                            stats["emails_found"] += 1
                            logger.info("  Email found: %s", email)
                else:
                    db.mark_company_no_email(company_id)
                    logger.info("  No email found (%s).", notes or "moved on")
                scrape_complete_beep()

            db.mark_query_done(query_id, status="done")

        await context.close()

    return stats


def scrape_sync(db: ImmigrationDB, **kwargs) -> dict:
    return asyncio.run(run_scrape(db, **kwargs))
