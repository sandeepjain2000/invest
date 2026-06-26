#!/usr/bin/env python3
"""
Bulk, resumable CA Connect email harvester — separate database from immigration pipeline.

Stores all listing cards per city search, then enriches profile pages in batches.
Each run continues where the last run stopped.

Usage:
  python ca_bulk_import.py seed-searches          # load search_queue from ca_bulk_config.json
  python ca_bulk_import.py import-json            # bootstrap from data/ca_connect_results.json
  python ca_bulk_import.py status                 # progress summary
  python ca_bulk_import.py run                    # enrich up to goal (default 1000 new emails)
  python ca_bulk_import.py run --goal 500 --batch 100
  python ca_bulk_import.py export-csv             # export emails to CSV
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from browser_utils import launch_context, scrape_complete_beep
from ca_bulk_db import CaBulkDB, DB_PATH
from ca_connect_scraper import (
    DEFAULT_CREDENTIALS_FILE,
    DEFAULT_OUTPUT_FILE,
    _scrape_profile_details,
    load_credentials,
    login_if_configured,
    run_ca_connect_search,
)
from pipeline_progress import ca_connect_status, step as progress_step
from project_paths import (
    CA_BULK_CONFIG,
    CA_BULK_CONFIG_EXAMPLE,
    PROJECT_ROOT,
    resolve_config_file,
    resolve_project_path,
)

_SCRIPT_DIR = PROJECT_ROOT
_CONFIG_FILE = CA_BULK_CONFIG
_CONFIG_EXAMPLE = CA_BULK_CONFIG_EXAMPLE
_LOG_DIR = _SCRIPT_DIR / "logs"

logger = logging.getLogger("ca_bulk_import")


def setup_logging() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    log = logging.getLogger("ca_bulk_import")
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(ch)
    log_file = _LOG_DIR / f"ca_bulk_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
    fh = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    for child in ("ca_connect_scraper", "pipeline_progress"):
        cl = logging.getLogger(child)
        cl.setLevel(logging.DEBUG)
        cl.handlers.clear()
        cl.addHandler(ch)
        cl.addHandler(fh)
        cl.propagate = False
    log.info("Log file: %s", log_file)


def load_config() -> dict:
    path = _CONFIG_FILE if _CONFIG_FILE.is_file() else _CONFIG_EXAMPLE
    if not path.is_file():
        path = resolve_config_file("ca_bulk_config.json")
    if not path.is_file():
        path = resolve_config_file("ca_bulk_config.example.json")
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def db_from_config(cfg: dict) -> CaBulkDB:
    raw = (cfg.get("database_file") or "data/db/ca_bulk.db").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = _SCRIPT_DIR / path
    return CaBulkDB(path)


def credentials_from_config(cfg: dict) -> tuple[Path, dict]:
    raw = (cfg.get("credentials_file") or str(DEFAULT_CREDENTIALS_FILE)).strip()
    path = resolve_project_path(raw)
    return path, load_credentials(path)


async def harvest_search_listings(
    db: CaBulkDB,
    search: dict,
    *,
    credentials: dict,
    browser: str,
    headless: bool,
    city_aliases: dict[str, str] | None = None,
) -> int:
    """Run CA Connect search and store listing cards (no profile enrichment)."""
    progress_step(
        f"Listing harvest — {search['service']} | {search['state']} | {search['city']}"
    )
    stats = await run_ca_connect_search(
        service=search["service"],
        state=search["state"],
        city=search["city"],
        credentials=credentials,
        browser=browser,
        do_login=False,
        headless=headless,
        enrich_profiles=False,
        profile_limit=0,
        city_aliases=city_aliases,
    )
    listings = stats.get("results") or []
    added = db.insert_listings(int(search["id"]), listings)
    db.mark_search_listed(
        int(search["id"]),
        search_url=stats.get("search_url") or "",
        listing_count=len(listings),
    )
    logger.info(
        "Stored %s new listing(s) (%s total on page) for %s / %s / %s",
        added,
        len(listings),
        search["service"],
        search["state"],
        search["city"],
    )
    return added


async def enrich_batch(
    db: CaBulkDB,
    listings: list[dict],
    *,
    credentials: dict,
    browser: str,
    headless: bool,
    delay_sec: float,
) -> dict:
    stats = {
        "processed": 0,
        "new_emails": 0,
        "already_had_email": 0,
        "no_email": 0,
        "failed": 0,
    }
    if not listings:
        return stats

    async with async_playwright() as playwright:
        if headless:
            browser_obj = await playwright.chromium.launch(headless=True)
            context = await browser_obj.new_context(viewport={"width": 1366, "height": 900})
        else:
            context, _ = await launch_context(playwright, browser, headless=False)

        page = context.pages[0] if context.pages else await context.new_page()
        if not await login_if_configured(page, credentials):
            raise RuntimeError(
                "CA Connect login failed — fill ca_connect_credentials.json with valid login."
            )

        total = len(listings)
        for idx, row in enumerate(listings, start=1):
            listing_id = int(row["id"])
            profile_url = row["profile_url"]
            name = row.get("name") or ""
            stats["processed"] += 1
            try:
                details = await _scrape_profile_details(page, profile_url)
                if details.get("error"):
                    status = "login_required" if details["error"] == "login_required" else "failed"
                    db.save_enrichment(
                        listing_id,
                        email="",
                        mobile="",
                        website="",
                        specialization=[],
                        member_name=name,
                        status=status,
                        error=details["error"],
                    )
                    stats["failed"] += 1
                    logger.warning("Profile failed: %s — %s", name, details["error"])
                else:
                    email = (details.get("email") or "").strip().lower()
                    if email and db.email_exists(email) and email != (row.get("email") or "").lower():
                        db.save_enrichment(
                            listing_id,
                            email=email,
                            mobile=details.get("mobile") or "",
                            website=details.get("website") or "",
                            specialization=details.get("specialization") or [],
                            member_name=details.get("member_name") or name,
                            status="done",
                            error="duplicate_email_elsewhere",
                        )
                        stats["already_had_email"] += 1
                    elif email:
                        is_new = db.save_enrichment(
                            listing_id,
                            email=email,
                            mobile=details.get("mobile") or "",
                            website=details.get("website") or "",
                            specialization=details.get("specialization") or [],
                            member_name=details.get("member_name") or name,
                            status="done",
                        )
                        if is_new:
                            stats["new_emails"] += 1
                    else:
                        db.save_enrichment(
                            listing_id,
                            email="",
                            mobile=details.get("mobile") or "",
                            website=details.get("website") or "",
                            specialization=details.get("specialization") or [],
                            member_name=details.get("member_name") or name,
                            status="no_email",
                        )
                        stats["no_email"] += 1
                    ca_connect_status(
                        n=idx,
                        total=total,
                        name=details.get("member_name") or name,
                        email=email or "no email",
                    )
                    logger.info(
                        "  [%s/%s] %s | %s",
                        idx,
                        total,
                        details.get("member_name") or name,
                        email or "no email",
                    )
            except Exception as exc:
                db.save_enrichment(
                    listing_id,
                    email="",
                    mobile="",
                    website="",
                    specialization=[],
                    member_name=name,
                    status="failed",
                    error=str(exc),
                )
                stats["failed"] += 1
                logger.warning("Profile error %s: %s", profile_url, exc)
            finally:
                scrape_complete_beep()
                if idx < total:
                    await page.wait_for_timeout(int(delay_sec * 1000))

        await context.close()

    return stats


def city_aliases_from_config(cfg: dict) -> dict[str, str]:
    raw = cfg.get("city_aliases") or {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).strip(): str(value).strip()
        for key, value in raw.items()
        if str(key).strip() and str(value).strip()
    }


async def run_bulk_import(
    db: CaBulkDB,
    *,
    goal: int,
    batch_size: int,
    credentials: dict,
    browser: str,
    headless: bool,
    delay_sec: float,
    city_aliases: dict[str, str] | None = None,
) -> dict:
    run_stats = {
        "new_emails": 0,
        "profiles_processed": 0,
        "searches_listed": 0,
        "no_email": 0,
        "failed": 0,
    }

    progress_step(f"Bulk CA import — goal {goal} new email(s), batch size {batch_size}")

    while run_stats["new_emails"] < goal:
        search = db.next_search_needing_enrichment()
        if not search:
            logger.info("No searches left in queue.")
            break

        search_id = int(search["id"])
        pending = db.count_pending_listings(search_id)

        if search["status"] == "queued" or (
            search["status"] != "listed" and db.count_pending_listings(search_id) == 0
        ):
            await harvest_search_listings(
                db,
                search,
                credentials=credentials,
                browser=browser,
                headless=headless,
                city_aliases=city_aliases,
            )
            run_stats["searches_listed"] += 1
            search = db.get_search(search_id) or search
            pending = db.count_pending_listings(search_id)

        if pending == 0:
            db.mark_search_complete(search_id)
            logger.info(
                "Search complete: %s | %s | %s",
                search["service"],
                search["state"],
                search["city"],
            )
            continue

        remaining_goal = goal - run_stats["new_emails"]
        take = min(batch_size, pending)
        if remaining_goal <= 0:
            break

        listings = db.next_pending_listings(search_id, take)
        logger.info(
            "Enriching %s profile(s) — %s | %s | %s (%s pending in this city)",
            len(listings),
            search["service"],
            search["state"],
            search["city"],
            pending,
        )
        batch_stats = await enrich_batch(
            db,
            listings,
            credentials=credentials,
            browser=browser,
            headless=headless,
            delay_sec=delay_sec,
        )
        run_stats["new_emails"] += batch_stats["new_emails"]
        run_stats["profiles_processed"] += batch_stats["processed"]
        run_stats["no_email"] += batch_stats["no_email"]
        run_stats["failed"] += batch_stats["failed"]

        logger.info(
            "Batch done — %s new email(s) this batch | run total %s / %s",
            batch_stats["new_emails"],
            run_stats["new_emails"],
            goal,
        )

        if db.count_pending_listings(search_id) == 0:
            db.mark_search_complete(search_id)

        if batch_stats["processed"] == 0:
            break

    db.set_state("last_run_at", datetime.now().isoformat(timespec="seconds"))
    db.set_state("last_run_new_emails", str(run_stats["new_emails"]))
    return run_stats


def cmd_status(db: CaBulkDB) -> int:
    totals = db.summary()
    logger.info("=== CA bulk database: %s ===", db.path)
    for key, value in totals.items():
        logger.info("  %-22s %s", key.replace("_", " ") + ":", value)
    logger.info("")
    logger.info("  Searches:")
    for row in db.searches_progress():
        logger.info(
            "    [%s] %s | %s | %s — status=%s listings=%s emails=%s pending=%s",
            row["id"],
            row["service"],
            row["state"],
            row["city"],
            row["status"],
            row["listing_count"] or 0,
            row["with_email"] or 0,
            row["pending"] or 0,
        )
    last = db.last_checkpoint()
    if last:
        logger.info("")
        logger.info(
            "  Last enriched: %s <%s> — %s / %s / %s at %s",
            last.get("name") or "",
            last.get("email") or "no email",
            last.get("service"),
            last.get("state"),
            last.get("city"),
            last.get("enriched_at"),
        )
    logger.info("")
    logger.info("  Resume: python ca_bulk_import.py run")
    return 0


def cmd_seed_searches(db: CaBulkDB, cfg: dict) -> int:
    queue = cfg.get("search_queue") or []
    if not queue:
        logger.error("No search_queue in ca_bulk_config.json")
        return 1
    added = db.seed_search_queue(queue)
    logger.info("Search queue: %s row(s) in config, %s new search(es) added.", len(queue), added)
    return 0


def cmd_import_json(db: CaBulkDB, path: Path) -> int:
    if not path.exists():
        logger.error("File not found: %s", path)
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    service = data.get("service") or "Audit"
    state = data.get("state") or "Maharashtra"
    city = data.get("city") or "Pune"
    search_id = db.upsert_search(
        service=service,
        state=state,
        city=city,
        search_url=data.get("search_url") or "",
        listing_count=int(data.get("result_count") or 0),
        status="listed",
    )
    db.mark_search_listed(
        search_id,
        search_url=data.get("search_url") or "",
        listing_count=int(data.get("result_count") or len(data.get("results") or [])),
    )
    listings = data.get("results") or []
    added = db.insert_listings(search_id, listings)
    logger.info(
        "Imported %s listing(s) from %s into search %s (%s | %s | %s)",
        added,
        path,
        search_id,
        service,
        state,
        city,
    )
    return 0


def cmd_export_csv(db: CaBulkDB, out_path: Path) -> int:
    rows = db.conn.execute(
        """
        SELECT s.service, s.state, s.city, l.name, l.email, l.mobile,
               l.listing_type, l.profile_url, l.professional_city, l.enriched_at
        FROM ca_listings l
        JOIN ca_searches s ON s.id = l.search_id
        WHERE l.email != '' AND l.email IS NOT NULL
        ORDER BY l.id ASC
        """
    ).fetchall()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "service",
                "state",
                "city",
                "name",
                "email",
                "mobile",
                "listing_type",
                "profile_url",
                "professional_city",
                "enriched_at",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["service"],
                    row["state"],
                    row["city"],
                    row["name"],
                    row["email"],
                    row["mobile"],
                    row["listing_type"],
                    row["profile_url"],
                    row["professional_city"],
                    row["enriched_at"],
                ]
            )
    logger.info("Exported %s email(s) to %s", len(rows), out_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bulk resumable CA Connect email harvester")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show database progress and resume pointer")
    sub.add_parser("seed-searches", help="Load search_queue from ca_bulk_config.json")

    imp = sub.add_parser("import-json", help="Bootstrap from ca_connect_results.json")
    imp.add_argument(
        "--file",
        default=str(DEFAULT_OUTPUT_FILE),
        help="Path to CA Connect JSON results",
    )

    exp = sub.add_parser("export-csv", help="Export all emails to CSV")
    exp.add_argument(
        "--output",
        default="data/ca_bulk_emails.csv",
        help="CSV output path",
    )

    run = sub.add_parser("run", help="Resume enrichment until goal reached")
    run.add_argument(
        "--goal",
        type=int,
        default=None,
        help="Stop after this many NEW emails saved this run (default: goal_emails_per_run in config)",
    )
    run.add_argument(
        "--batch",
        type=int,
        default=None,
        help="Profiles per browser session batch (default: profiles_per_batch in config)",
    )
    run.add_argument("--headed", action="store_true", help="Show browser window")
    return parser


def main() -> int:
    setup_logging()
    args = build_parser().parse_args()
    cfg = load_config()
    db = db_from_config(cfg)

    try:
        if args.command == "status":
            return cmd_status(db)
        if args.command == "seed-searches":
            return cmd_seed_searches(db, cfg)
        if args.command == "import-json":
            path = Path(args.file)
            if not path.is_absolute():
                path = _SCRIPT_DIR / path
            return cmd_import_json(db, path)
        if args.command == "export-csv":
            out = Path(args.output)
            if not out.is_absolute():
                out = _SCRIPT_DIR / out
            return cmd_export_csv(db, out)
        if args.command == "run":
            _, credentials = credentials_from_config(cfg)
            goal = args.goal if args.goal is not None else int(cfg.get("goal_emails_per_run") or 1000)
            batch = args.batch if args.batch is not None else int(cfg.get("profiles_per_batch") or 50)
            headless = not args.headed and bool(cfg.get("headless", True))
            browser = (cfg.get("browser") or "auto").strip()
            delay = float(cfg.get("delay_between_profiles_sec") or 1.5)
            stats = asyncio.run(
                run_bulk_import(
                    db,
                    goal=max(1, goal),
                    batch_size=max(1, batch),
                    credentials=credentials,
                    browser=browser,
                    headless=headless,
                    delay_sec=delay,
                    city_aliases=city_aliases_from_config(cfg),
                )
            )
            logger.info("Run complete: %s", stats)
            cmd_status(db)
            return 0
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
