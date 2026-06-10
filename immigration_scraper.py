"""Browser-based discovery of immigration providers and email extraction."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Iterable
from urllib.parse import quote_plus, urlparse

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from immigration_db import ImmigrationDB, clean_domain
from nvidia_llm import DEFAULT_SEARCH_SEEDS, generate_search_queries

logger = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 180_000
NAV_TIMEOUT_MS = 180_000
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


async def launch_context(playwright: Playwright, browser: str) -> tuple[BrowserContext, str]:
    label = browser.lower()
    viewport = {"width": 1366, "height": 900}
    launch_args = ["--disable-blink-features=AutomationControlled"]

    if label in ("auto", "chromium", "chrome"):
        try:
            browser_obj = await playwright.chromium.launch(
                headless=False,
                channel="chrome",
                args=launch_args,
            )
            context = await browser_obj.new_context(viewport=viewport)
            return context, "chrome"
        except Exception as exc:
            logger.warning("Chrome launch failed (%s), trying Chromium.", exc)
            try:
                browser_obj = await playwright.chromium.launch(
                    headless=False,
                    args=launch_args,
                )
                context = await browser_obj.new_context(viewport=viewport)
                return context, "chromium"
            except Exception as exc2:
                logger.warning("Chromium launch failed (%s), trying Firefox.", exc2)

    browser_obj = await playwright.firefox.launch(headless=False)
    context = await browser_obj.new_context(viewport=viewport)
    return context, "firefox"


async def dismiss_cookie_banners(page: Page) -> None:
    candidates = [
        'button:has-text("Accept")',
        'button:has-text("Accept all")',
        'button:has-text("I agree")',
        'button:has-text("Got it")',
        'button:has-text("OK")',
    ]
    for sel in candidates:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click(timeout=3000)
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def google_search_urls(page: Page, query: str, limit: int = SEARCH_RESULT_LIMIT) -> list[dict]:
    url = f"https://www.google.com/search?q={quote_plus(query)}&num={limit}"
    logger.info("Google search: %s", query)
    await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    await dismiss_cookie_banners(page)
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
    for sel in CONTACT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=2000):
                await loc.click(timeout=10000)
                await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
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


async def scrape_company_emails(page: Page, website: str) -> tuple[list[str], str, str]:
    notes = ""
    try:
        await page.goto(website, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except Exception as exc:
        return [], website, f"navigation_failed: {exc}"

    await dismiss_cookie_banners(page)
    try:
        await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
    except Exception:
        await page.wait_for_timeout(5000)

    emails, source = await extract_emails_from_page(page)
    if emails:
        return emails, source, notes

    contact_url = await click_contact_if_needed(page)
    if contact_url:
        try:
            await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
        except Exception:
            await page.wait_for_timeout(3000)
        emails, source = await extract_emails_from_page(page)
        if emails:
            return emails, source, notes

    if "@" not in (await page.content()):
        notes = "no_at_symbol_on_page"
    else:
        notes = "at_symbol_but_no_valid_email"
    return [], source, notes


async def run_scrape(
    db: ImmigrationDB,
    *,
    max_companies: int = 20,
    max_queries: int = 5,
    browser: str = "auto",
    region: str = "India",
    seed_keywords: bool = True,
) -> dict:
    stats = {"queries_run": 0, "companies_scraped": 0, "emails_found": 0, "browser_used": ""}

    pending = db.next_pending_query()
    if not pending and seed_keywords:
        seeds = generate_search_queries(count=12, region=region)
        if not seeds:
            seeds = DEFAULT_SEARCH_SEEDS
        added = db.add_search_queries(seeds, source="nvidia")
        logger.info("Seeded %s search query(ies).", added)
        pending = db.next_pending_query()

    async with async_playwright() as playwright:
        context, browser_used = await launch_context(playwright, browser)
        stats["browser_used"] = browser_used
        logger.info("Using browser: %s", browser_used)
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT_MS)

        queries_done = 0
        while queries_done < max_queries and stats["companies_scraped"] < max_companies:
            row = db.next_pending_query()
            if not row:
                break

            query_id = int(row["id"])
            query_text = row["query_text"]
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
                )
                if not company_id:
                    continue

                stats["companies_scraped"] += 1
                logger.info("Scraping %s (%s)", result["title"], result["url"])
                emails, source_page, notes = await scrape_company_emails(page, result["url"])
                if emails:
                    for email in emails:
                        if db.add_company_email(company_id, email, source_page):
                            stats["emails_found"] += 1
                            logger.info("  Email found: %s", email)
                else:
                    db.mark_company_no_email(company_id)
                    logger.info("  No email found (%s).", notes or "moved on")

            db.mark_query_done(query_id, status="done")

        await context.close()

    return stats


def scrape_sync(db: ImmigrationDB, **kwargs) -> dict:
    return asyncio.run(run_scrape(db, **kwargs))
