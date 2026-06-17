"""Scrape CA / CA firm listings from ICAI CA Connect (https://caconnect.icai.org/)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from pathlib import Path

from playwright.async_api import Page, async_playwright

from browser_utils import dismiss_page_obstructions, launch_context, scrape_complete_beep
from pipeline_progress import ca_connect_status

logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CREDENTIALS_FILE = _SCRIPT_DIR / "ca_connect_credentials.json"
DEFAULT_OUTPUT_FILE = _SCRIPT_DIR / "data" / "ca_connect_results.json"

CA_CONNECT_HOME = "https://caconnect.icai.org/"
CA_CONNECT_SEARCH_PAGE = "https://caconnect.icai.org/search-your-ca"
CA_CONNECT_LOGIN = "https://caconnect.icai.org/login"


def load_credentials(path: Path | None = None) -> dict:
    cred_path = path or Path(
        os.environ.get("CA_CONNECT_CREDENTIALS_FILE", str(DEFAULT_CREDENTIALS_FILE))
    )
    if not cred_path.exists():
        example = _SCRIPT_DIR / "ca_connect_credentials.example.json"
        raise FileNotFoundError(
            f"Credentials not found: {cred_path}. "
            f"Copy {example.name} to {cred_path.name} and add your login details."
        )
    return json.loads(cred_path.read_text(encoding="utf-8"))


def resolve_search_params(credentials: dict, overrides: dict | None = None) -> dict:
    defaults = dict(credentials.get("search_defaults") or {})
    overrides = overrides or {}
    return {
        "service": (overrides.get("service") or defaults.get("service") or "Audit").strip(),
        "state": (overrides.get("state") or defaults.get("state") or "Maharashtra").strip(),
        "city": (overrides.get("city") or defaults.get("city") or "Pune").strip(),
    }


async def login_if_configured(page: Page, credentials: dict) -> bool:
    """Optional login — public search usually works without it."""
    login = dict(credentials.get("login") or {})
    email = (login.get("email") or "").strip()
    password = (login.get("password") or "").strip()
    if not email or not password or email.startswith("YOUR_"):
        logger.info("No CA Connect login credentials — continuing as guest.")
        return False

    logger.info("Logging in to CA Connect as %s", email)
    await page.goto(CA_CONNECT_LOGIN, wait_until="domcontentloaded", timeout=60_000)
    await dismiss_page_obstructions(page)
    await page.fill("#email", email)
    await page.fill("#password", password)
    if login.get("remember"):
        try:
            await page.check("#remember", timeout=2000)
        except Exception:
            pass
    await page.locator('form[action*="login"] input[type="submit"], form[action*="login"] button[type="submit"]').first.click()
    await page.wait_for_load_state("domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(3000)
    if "login" in page.url.lower():
        logger.warning("Login may have failed — still on login page. Check credentials/captcha.")
        return False
    logger.info("Login OK — current URL: %s", page.url)
    return True


async def _wait_for_city_options(page: Page, *, min_options: int = 2, timeout_ms: int = 15_000) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        count = await page.locator("#city option").count()
        if count >= min_options:
            return
        await page.wait_for_timeout(500)
    raise TimeoutError("City dropdown did not populate after state selection.")


PROFILE_LABELS: dict[str, list[str]] = {
    "member_name": ["Member Name", "Firm Name"],
    "mobile": ["Mobile", "Mobile No", "Mobile No."],
    "email": ["Email", "E-mail"],
    "website": ["Website"],
    "location": ["Location"],
    "professional_city_preference": ["Professional city preference", "Professional City preference"],
}


def _value_for_label(lines: list[str], labels: list[str]) -> str:
    label_keys = {l.lower() for vals in PROFILE_LABELS.values() for l in vals}
    for label in labels:
        key = label.lower()
        for i, line in enumerate(lines):
            low = line.lower()
            if low == key or low.rstrip(":") == key:
                if i + 1 < len(lines):
                    nxt = lines[i + 1].strip()
                    if nxt and nxt.lower() not in label_keys:
                        return nxt
            if low.startswith(key + ":"):
                return line.split(":", 1)[1].strip()
    return ""


async def _parse_profile_page(page: Page) -> dict:
    """
    Parse profile page layout (Professional Information, Specialization, etc.).
    Requires an authenticated session — guest users are redirected to /login.
    """
    if "login" in page.url.lower():
        return {"error": "login_required"}

    await page.wait_for_timeout(1500)
    body_text = await page.inner_text("body")
    lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]

    profile: dict = {
        "member_name": "",
        "mobile": "",
        "email": "",
        "website": "",
        "location": "",
        "professional_city_preference": "",
        "specialization": [],
        "profile_url": page.url,
    }

    for field, labels in PROFILE_LABELS.items():
        profile[field] = _value_for_label(lines, labels)

    mail_link = page.locator('a[href^="mailto:"]').first
    if not profile["email"] and await mail_link.count():
        href = await mail_link.get_attribute("href") or ""
        profile["email"] = href.replace("mailto:", "").strip()

    tags = [
        re.sub(r"\s+", " ", t).strip()
        for t in await page.locator(".boxCe, .services_area .boxCe, button.boxCe").all_inner_texts()
        if t.strip()
    ]
    if tags:
        profile["specialization"] = list(dict.fromkeys(tags))

    if not profile["member_name"]:
        for sel in ("h3", "h4", ".profile-name", ".member-name"):
            loc = page.locator(sel).first
            if await loc.count():
                text = (await loc.inner_text() or "").strip()
                if text and len(text) < 200:
                    profile["member_name"] = re.sub(r"\s+", " ", text)
                    break

    return profile


async def _scrape_profile_details(page: Page, profile_url: str) -> dict:
    if not profile_url:
        return {"error": "missing_profile_url"}
    await page.goto(profile_url, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(1500)
    return await _parse_profile_page(page)


async def _enrich_listings_with_profiles(
    page: Page,
    listings: list[dict],
    *,
    limit: int,
    delay_sec: float = 1.5,
) -> tuple[int, int]:
    """Visit profile pages and merge email/mobile/etc. Returns (enriched, failed)."""
    enriched = 0
    failed = 0
    todo = [
        item
        for item in listings
        if item.get("profile_url")
        and not (item.get("email") or (item.get("profile") or {}).get("email"))
    ][: max(0, limit)]
    logger.info("Enriching %s profile page(s) with contact details...", len(todo))

    for idx, item in enumerate(todo, start=1):
        url = item["profile_url"]
        try:
            details = await _scrape_profile_details(page, url)
            if details.get("error"):
                failed += 1
                item["profile_error"] = details["error"]
                logger.warning("Profile %s: %s", url, details["error"])
                continue
            item["profile"] = details
            item["email"] = details.get("email") or item.get("email") or ""
            item["mobile"] = details.get("mobile") or ""
            item["website"] = details.get("website") or ""
            if details.get("specialization"):
                item["specialization"] = details["specialization"]
            enriched += 1
            ca_connect_status(
                n=idx,
                total=len(todo),
                name=details.get("member_name") or item.get("name") or "",
                email=details.get("email") or "",
            )
            logger.info(
                "  [%s/%s] %s | %s | %s",
                idx,
                len(todo),
                details.get("member_name") or item.get("name"),
                details.get("email") or "no email",
                details.get("mobile") or "no mobile",
            )
        except Exception as exc:
            failed += 1
            item["profile_error"] = str(exc)
            logger.warning("Profile failed %s: %s", url, exc)
        finally:
            scrape_complete_beep()
        if idx < len(todo):
            await page.wait_for_timeout(int(delay_sec * 1000))

    return enriched, failed


def merge_prior_enrichment(new_results: list[dict], prior_results: list[dict]) -> None:
    """Restore profile/email fields from a prior run when re-searching CA Connect."""
    prior_by_url = {
        row["profile_url"]: row
        for row in prior_results
        if row.get("profile_url")
    }
    for item in new_results:
        url = item.get("profile_url")
        if not url or url not in prior_by_url:
            continue
        old = prior_by_url[url]
        for key in ("profile", "email", "mobile", "website", "specialization", "profile_error"):
            if old.get(key) and not item.get(key):
                item[key] = old[key]


async def run_ca_connect_search(
    *,
    service: str = "Audit",
    state: str = "Maharashtra",
    city: str = "Pune",
    credentials: dict | None = None,
    browser: str = "auto",
    do_login: bool = False,
    headless: bool = True,
    enrich_profiles: bool = False,
    profile_limit: int = 0,
    prior_results: list[dict] | None = None,
) -> dict:
    credentials = credentials or {}
    stats = {
        "service": service,
        "state": state,
        "city": city,
        "search_url": "",
        "logged_in": False,
        "summary": {},
        "members": [],
        "firms": [],
        "results": [],
        "result_count": 0,
        "member_count": 0,
        "firm_count": 0,
        "profiles_enriched": 0,
        "profiles_failed": 0,
    }

    async with async_playwright() as playwright:
        if headless:
            browser_obj = await playwright.chromium.launch(headless=True)
            context = await browser_obj.new_context(viewport={"width": 1366, "height": 900})
            browser_label = "chromium-headless"
        else:
            context, browser_label = await launch_context(playwright, browser)

        page = context.pages[0] if context.pages else await context.new_page()
        stats["browser_used"] = browser_label

        if do_login and credentials:
            stats["logged_in"] = await login_if_configured(page, credentials)
        elif enrich_profiles:
            stats["logged_in"] = await login_if_configured(page, credentials)
            if not stats["logged_in"]:
                raise RuntimeError(
                    "Profile pages require CA Connect login. "
                    "Fill ca_connect_credentials.json and run with --login --enrich-profiles."
                )

        logger.info("Opening CA Connect search page: %s", CA_CONNECT_SEARCH_PAGE)
        await page.goto(CA_CONNECT_SEARCH_PAGE, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_selector("#services", state="visible", timeout=30_000)
        await page.locator("#services").scroll_into_view_if_needed()
        await page.wait_for_timeout(1000)

        logger.info("Selecting service=%s state=%s city=%s", service, state, city)
        await page.select_option("#services", label=service)
        await page.select_option("#state", label=state)
        await _wait_for_city_options(page)

        city_options = await page.eval_on_selector(
            "#city",
            "el => Array.from(el.options).map(o => o.text.trim()).filter(Boolean)",
        )
        city_pick = city
        if city not in city_options:
            matches = [c for c in city_options if city.lower() in c.lower()]
            if not matches:
                raise ValueError(
                    f"City '{city}' not found for state '{state}'. "
                    f"Sample options: {city_options[:12]}"
                )
            city_pick = matches[0]
            logger.info("City matched: %s -> %s", city, city_pick)
        await page.select_option("#city", label=city_pick)

        await page.locator("button.btn-warning", has_text="Search Your CA").first.click()
        await page.wait_for_load_state("domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(3000)
        stats["search_url"] = page.url
        logger.info("Search results URL: %s", page.url)

        extracted = await _extract_search_results(page)
        stats.update(extracted)
        stats["member_count"] = len(stats["members"])
        stats["firm_count"] = len(stats["firms"])

        if prior_results:
            merge_prior_enrichment(stats["results"], prior_results)

        if enrich_profiles and profile_limit > 0:
            enriched, failed = await _enrich_listings_with_profiles(
                page,
                stats["results"],
                limit=profile_limit,
            )
            stats["profiles_enriched"] = enriched
            stats["profiles_failed"] = failed
            # Keep members/firms in sync with merged profile fields
            stats["members"] = [r for r in stats["results"] if r.get("listing_type") == "member"]
            stats["firms"] = [r for r in stats["results"] if r.get("listing_type") == "firm"]

        await context.close()

    return stats


async def _parse_result_cards(page: Page, listing_type: str) -> list[dict]:
    """
    Parse listing cards exactly as shown on CA Connect search results:
    .searchBox.scr with name, Professional City, location, service tags, View Profile.
    """
    cards = page.locator(".searchBox.scr")
    count = await cards.count()
    results: list[dict] = []
    seen: set[str] = set()

    for i in range(count):
        card = cards.nth(i)
        try:
            name = re.sub(
                r"\s+",
                " ",
                (await card.locator("p b").first.inner_text(timeout=3000) or "").strip(),
            )
            if not name:
                continue

            profile_link = card.locator('a[href*="memberProfile"], a[href*="firmProfile"]').first
            profile_url = ""
            if await profile_link.count():
                profile_url = (await profile_link.get_attribute("href") or "").strip()

            dedupe_key = f"{name}|{profile_url}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            professional_city = ""
            if await card.locator(".pcity").count():
                pcity_text = await card.locator(".pcity").inner_text()
                professional_city = re.sub(
                    r"Professional City:\s*",
                    "",
                    pcity_text,
                    flags=re.I,
                )
                professional_city = re.sub(r"\s+", " ", professional_city).strip()

            location = ""
            if await card.locator(".state").count():
                location = re.sub(
                    r"\s+",
                    " ",
                    (await card.locator(".state").inner_text() or "").strip(),
                )

            services = [
                re.sub(r"\s+", " ", s).strip()
                for s in await card.locator(".services_area .boxCe").all_inner_texts()
                if s.strip()
            ]

            image_url = ""
            if await card.locator("img.searchImgeresult").count():
                image_url = (await card.locator("img.searchImgeresult").first.get_attribute("src") or "").strip()

            card_type = listing_type
            if profile_url:
                if "firmProfile" in profile_url:
                    card_type = "firm"
                elif "memberProfile" in profile_url:
                    card_type = "member"

            results.append(
                {
                    "name": name,
                    "listing_type": card_type,
                    "professional_city": professional_city,
                    "location": location,
                    "services": services,
                    "profile_url": profile_url,
                    "image_url": image_url,
                }
            )
        except Exception as exc:
            logger.debug("Skip card %s: %s", i, exc)
            continue

    return results


async def _read_result_summary(page: Page) -> dict:
    text = ""
    try:
        text = await page.locator(".container.text-align-center, .col-md-12.text-center").first.inner_text(
            timeout=5000
        )
    except Exception:
        return {}
    members_match = re.search(r"Total Members\((\d+)\)", text)
    firms_match = re.search(r"Total Firms\((\d+)\)", text)
    return {
        "total_members": int(members_match.group(1)) if members_match else None,
        "total_firms": int(firms_match.group(1)) if firms_match else None,
    }


async def _extract_search_results(page: Page) -> dict:
    """Parse Members tab then CA Firms tab — matches portal listing layout."""
    summary = await _read_result_summary(page)
    members = await _parse_result_cards(page, "member")
    logger.info("Members tab: %s cards", len(members))

    firms: list[dict] = []
    firms_tab = page.get_by_role("link", name="CA Firms")
    if await firms_tab.count():
        await firms_tab.first.click()
        await page.wait_for_load_state("domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(2500)
        firms = await _parse_result_cards(page, "firm")
        logger.info("CA Firms tab: %s cards", len(firms))

    combined = members + firms
    return {
        "summary": summary,
        "members": members,
        "firms": firms,
        "results": combined,
        "result_count": len(combined),
    }


def save_results(stats: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved %s results to %s", stats.get("result_count", 0), output_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search CA Connect for CA / CA firms.")
    parser.add_argument("--service", default=None, help="Preferred service (default: Audit)")
    parser.add_argument("--state", default=None, help="State/UT (default: Maharashtra)")
    parser.add_argument("--city", default=None, help="Member/Firm city (default: Pune)")
    parser.add_argument(
        "--credentials-file",
        default=None,
        help="Path to ca_connect_credentials.json",
    )
    parser.add_argument("--login", action="store_true", help="Log in before search (required for profile details)")
    parser.add_argument(
        "--enrich-profiles",
        action="store_true",
        help="Open each View Profile page and extract email/mobile (requires --login)",
    )
    parser.add_argument(
        "--profile-limit",
        type=int,
        default=10,
        help="Max profile pages to open when --enrich-profiles is set (default: 10)",
    )
    parser.add_argument(
        "--enrich-from",
        default=None,
        help="Enrich profiles from an existing ca_connect_results.json (skip search)",
    )
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_FILE),
        help="JSON output path for results",
    )
    parser.add_argument("--browser", choices=["auto", "chrome", "chromium", "firefox"], default="auto")
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    cred_path = Path(args.credentials_file) if args.credentials_file else DEFAULT_CREDENTIALS_FILE
    try:
        credentials = load_credentials(cred_path)
    except FileNotFoundError as exc:
        if args.login or args.enrich_profiles or args.enrich_from:
            logger.error("%s", exc)
            return 1
        credentials = {
            "search_defaults": {
                "service": args.service or "Audit",
                "state": args.state or "Maharashtra",
                "city": args.city or "Pune",
            }
        }

    if args.enrich_from:
        stats = json.loads(Path(args.enrich_from).read_text(encoding="utf-8"))
        async with async_playwright() as playwright:
            if args.headed:
                context, _ = await launch_context(playwright, args.browser)
            else:
                browser_obj = await playwright.chromium.launch(headless=True)
                context = await browser_obj.new_context(viewport={"width": 1366, "height": 900})
            page = context.pages[0] if context.pages else await context.new_page()
            if not await login_if_configured(page, credentials):
                logger.error("Login failed — profile pages need CA Connect credentials.")
                await context.close()
                return 1
            enriched, failed = await _enrich_listings_with_profiles(
                page,
                stats.get("results") or [],
                limit=args.profile_limit,
            )
            stats["profiles_enriched"] = enriched
            stats["profiles_failed"] = failed
            await context.close()
        save_results(stats, Path(args.output))
        print(f"Enriched {enriched} profiles ({failed} failed). Saved: {args.output}")
        return 0

    search = resolve_search_params(
        credentials,
        {"service": args.service, "state": args.state, "city": args.city},
    )
    stats = await run_ca_connect_search(
        service=search["service"],
        state=search["state"],
        city=search["city"],
        credentials=credentials,
        browser=args.browser,
        do_login=args.login,
        headless=not args.headed,
        enrich_profiles=args.enrich_profiles,
        profile_limit=args.profile_limit,
    )
    save_results(stats, Path(args.output))

    print(f"Search: {search['service']} | {search['state']} | {search['city']}")
    if stats.get("summary"):
        print(f"Portal totals: {stats['summary']}")
    print(f"Members scraped: {stats.get('member_count', 0)}")
    print(f"Firms scraped:   {stats.get('firm_count', 0)}")
    print(f"Total:           {stats['result_count']}")
    if stats.get("profiles_enriched"):
        print(f"Profiles enriched: {stats['profiles_enriched']} ({stats.get('profiles_failed', 0)} failed)")
    print(f"URL: {stats['search_url']}")
    print(f"Saved: {args.output}")
    for row in stats["results"][:8]:
        svc = ", ".join(row.get("services") or [])
        email = row.get("email") or (row.get("profile") or {}).get("email") or ""
        extra = f" | {email}" if email else ""
        print(
            f"  - [{row.get('listing_type')}] {row.get('name')} | "
            f"{row.get('professional_city')} | {svc}{extra}"
        )
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
