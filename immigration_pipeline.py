#!/usr/bin/env python3
"""
Partnership outreach pipeline — multi-industry scrape + email.

Scrapes company websites across strategic industry verticals via browser,
extracts emails, stores in SQLite, and sends partnership emails.

Usage:
  python immigration_pipeline.py list-industries
  python immigration_pipeline.py status
  python immigration_pipeline.py seed-keywords --all
  python immigration_pipeline.py seed-keywords --industry recruitment_staffing
  python immigration_pipeline.py scrape --max-companies 20 --browser auto
  python immigration_pipeline.py scrape --industry edtech --max-companies 10
  python immigration_pipeline.py send
  python immigration_pipeline.py run --max-companies 15
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator

from check_replies import run_check_replies
from immigration_db import ImmigrationDB
from immigration_scraper import scrape_sync
from immigration_sender import (
    get_emails_per_run,
    get_max_companies_per_run,
    get_max_queries_per_run,
    run_send,
)
from industries import (
    default_region,
    get_industry,
    industry_ids,
    industry_name,
    list_industries,
    queries_per_industry,
    randomized_industry_ids,
    seed_queries_for,
)
from nvidia_llm import generate_queries_for_all_industries, generate_search_queries

_SCRIPT_DIR = Path(__file__).resolve().parent
_LOG_DIR = _SCRIPT_DIR / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger: logging.Logger = logging.getLogger("immigration_pipeline")


def clear_terminal() -> None:
    if sys.platform == "win32":
        os.system("cls")
    else:
        os.system("clear")


def setup_logger() -> logging.Logger:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    log = logging.getLogger("immigration_pipeline")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.handlers.clear()

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(ch)

    log_file = _LOG_DIR / f"immigration_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
    fh = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    log.info("Log file: %s", log_file)

    for child in ("immigration_scraper", "immigration_sender", "nvidia_llm", "check_replies"):
        child_log = logging.getLogger(child)
        child_log.setLevel(logging.DEBUG)
        child_log.handlers.clear()
        child_log.addHandler(ch)
        child_log.addHandler(fh)
        child_log.propagate = False

    return log


def init_logging() -> logging.Logger:
    global logger
    logger = setup_logger()
    return logger


@contextmanager
def prevent_windows_sleep() -> Generator[None, None, None]:
    if sys.platform != "win32":
        yield
        return
    try:
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        )
        yield
    finally:
        try:
            import ctypes

            ES_CONTINUOUS = 0x80000000
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass


def _validate_industry(industry: str | None) -> str | None:
    if not industry:
        return None
    if not get_industry(industry):
        logger.error("Unknown industry '%s'. Run: list-industries", industry)
        raise SystemExit(1)
    return industry


def cmd_list_industries() -> int:
    logger.info("=== Industry verticals (industries.json) ===")
    for item in list_industries(active_only=False):
        active = "active" if item.get("active", True) else "inactive"
        logger.info(
            "  [%s] %s — %s (%s)",
            item.get("rank"),
            item.get("id"),
            item.get("name"),
            active,
        )
        for q in (item.get("seed_queries") or [])[:2]:
            logger.info("      e.g. %s", q)
    return 0


def cmd_status(db: ImmigrationDB) -> int:
    summary = db.summary()
    logger.info("=== Pipeline status ===")
    for key, value in summary.items():
        logger.info("  %-24s %s", key, value)
    by_industry = db.summary_by_industry()
    if by_industry:
        logger.info("")
        logger.info("=== Companies by industry ===")
        for row in by_industry:
            logger.info(
                "  %-32s total=%s  with_email=%s",
                industry_name(row["industry"]),
                row["companies_total"],
                row["companies_with_email"],
            )
    return 0


def cmd_seed_keywords(
    db: ImmigrationDB,
    *,
    count: int,
    region: str,
    industry: str | None,
    seed_all: bool,
    use_nvidia: bool,
) -> int:
    total_added = 0

    if seed_all or not industry:
        batch = generate_queries_for_all_industries(
            region=region,
            per_industry=count or queries_per_industry(),
            use_nvidia=use_nvidia,
        ) if use_nvidia else {
            iid: seed_queries_for(iid)[: count or queries_per_industry()]
            for iid in randomized_industry_ids(active_only=True)
        }
        for iid, queries in batch.items():
            if not queries:
                queries = seed_queries_for(iid)[: count or queries_per_industry()]
            added = db.add_search_queries(
                queries,
                industry=iid,
                source="nvidia" if use_nvidia else "seed",
            )
            total_added += added
            logger.info("  %s: +%s queries", industry_name(iid), added)
    else:
        industry = _validate_industry(industry)
        queries = (
            generate_search_queries(count=count, region=region, industry_id=industry or "")
            if use_nvidia
            else seed_queries_for(industry or "")[:count]
        )
        if not queries:
            queries = seed_queries_for(industry or "")[:count]
        total_added = db.add_search_queries(
            queries,
            industry=industry or "overseas_education_immigration",
            source="nvidia" if use_nvidia else "seed",
        )
        logger.info("Added %s queries for %s.", total_added, industry_name(industry or ""))

    logger.info("Total new queries seeded: %s", total_added)
    return 0


def _scrape_kwargs(args: argparse.Namespace, industry: str | None) -> dict:
    return {
        "max_companies": args.max_companies or get_max_companies_per_run(),
        "max_queries": args.max_queries or get_max_queries_per_run(),
        "email_target": get_emails_per_run(),
        "browser": args.browser,
        "region": args.region,
        "industry": industry,
        "seed_keywords": not args.no_seed,
        "use_nvidia_seed": not args.no_nvidia_seed,
    }


def cmd_scrape(args: argparse.Namespace, db: ImmigrationDB) -> int:
    industry = _validate_industry(getattr(args, "industry", None))
    with prevent_windows_sleep():
        stats = scrape_sync(db, **_scrape_kwargs(args, industry))
    logger.info("Scrape complete: %s", stats)
    print_execution_summary(scrape_stats=stats, db=db)
    return 0


def print_execution_summary(
    *,
    scrape_stats: dict | None = None,
    reply_stats: dict | None = None,
    send_stats: dict | None = None,
    db: ImmigrationDB | None = None,
) -> None:
    """Print end-of-run summary to console and log (shown before batch file pause)."""
    logger.info("")
    logger.info("=" * 62)
    logger.info("  RUN SUMMARY")
    logger.info("=" * 62)

    if scrape_stats is not None:
        logger.info("  Scrape (this run)")
        logger.info("    Companies scraped:        %s", scrape_stats.get("companies_scraped", 0))
        logger.info("    Companies with email:     %s", scrape_stats.get("companies_with_email", 0))
        logger.info("    Emails scraped/found:     %s", scrape_stats.get("emails_found", 0))
        logger.info("    Search queries run:       %s", scrape_stats.get("queries_run", 0))
        if scrape_stats.get("browser_used"):
            logger.info("    Browser used:             %s", scrape_stats.get("browser_used"))

    if reply_stats is not None:
        if reply_stats.get("skipped"):
            logger.info("  Replies (this run)")
            logger.info("    Reply check:              skipped")
        else:
            logger.info("  Replies (this run)")
            logger.info("    Inbox messages scanned:   %s", reply_stats.get("scanned", 0))
            logger.info("    Replies forwarded:        %s", reply_stats.get("forwarded", 0))

    if send_stats is not None:
        logger.info("  Send (this run)")
        if send_stats.get("error"):
            logger.info("    Error:                    %s", send_stats.get("error"))
        logger.info("    Emails sent:              %s", send_stats.get("sent", 0))
        logger.info("    Send failed:              %s", send_stats.get("failed", 0))
        logger.info("    Skipped (dry-run/already): %s", send_stats.get("skipped", 0))

    if db is not None:
        totals = db.summary()
        logger.info("  Database totals (all runs)")
        logger.info("    Companies in database:    %s", totals.get("companies_total", 0))
        logger.info("    Companies with email:     %s", totals.get("companies_with_email", 0))
        logger.info("    Emails scraped (total):   %s", totals.get("emails_found", 0))
        logger.info("    Emails sent (total):      %s", totals.get("emails_sent", 0))
        logger.info("    Replies forwarded (total): %s", totals.get("replies_forwarded", 0))

    logger.info("=" * 62)
    logger.info("")


def _maybe_check_replies(db: ImmigrationDB, args: argparse.Namespace) -> dict:
    if getattr(args, "skip_replies", False) or getattr(args, "dry_run", False):
        return {"skipped": True, "scanned": 0, "forwarded": 0}
    logger.info("=== Checking inboxes for human replies ===")
    reply_stats = run_check_replies(
        db,
        use_nvidia=not getattr(args, "no_nvidia_replies", False),
    )
    logger.info("Reply check: %s", reply_stats)
    return reply_stats


def cmd_check_replies(args: argparse.Namespace, db: ImmigrationDB) -> int:
    with prevent_windows_sleep():
        stats = run_check_replies(
            db,
            use_nvidia=not args.no_nvidia,
        )
    logger.info("Done: %s", stats)
    print_execution_summary(reply_stats=stats, db=db)
    return 0 if not stats.get("error") else 1


def cmd_send(args: argparse.Namespace, db: ImmigrationDB) -> int:
    with prevent_windows_sleep():
        reply_stats = _maybe_check_replies(db, args)
        stats = run_send(
            db,
            limit=args.limit,
            use_nvidia_praise=not args.no_nvidia_praise,
            dry_run=args.dry_run,
        )
    logger.info("Send complete: %s", stats)
    print_execution_summary(reply_stats=reply_stats, send_stats=stats, db=db)
    if stats.get("error"):
        return 1
    return 0


def cmd_run(args: argparse.Namespace, db: ImmigrationDB) -> int:
    industry = _validate_industry(getattr(args, "industry", None))
    with prevent_windows_sleep():
        scrape_stats = scrape_sync(db, **_scrape_kwargs(args, industry))
        logger.info("Scrape complete: %s", scrape_stats)
        reply_stats = _maybe_check_replies(db, args)
        send_stats = run_send(
            db,
            limit=args.send_limit,
            use_nvidia_praise=not args.no_nvidia_praise,
            dry_run=getattr(args, "dry_run", False),
        )
        logger.info("Send complete: %s", send_stats)
    print_execution_summary(
        scrape_stats=scrape_stats,
        reply_stats=reply_stats,
        send_stats=send_stats,
        db=db,
    )
    return 0 if not send_stats.get("error") else 1


def _add_industry_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--industry",
        choices=industry_ids(active_only=False),
        default=None,
        help="Limit to one industry vertical (default: rotate all active industries)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Multi-industry partnership scrape + email pipeline"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-industries", help="Show all industry verticals from industries.json")
    sub.add_parser("status", help="Show SQLite summary counts")

    seed = sub.add_parser("seed-keywords", help="Seed Google search queries")
    seed.add_argument("--count", type=int, default=0, help="Queries per industry (0 = use industries.json default)")
    seed.add_argument("--region", default=default_region())
    seed.add_argument("--all", action="store_true", help="Seed all active industries")
    seed.add_argument("--no-nvidia", action="store_true", help="Use static seed_queries from industries.json only")
    _add_industry_arg(seed)

    scrape = sub.add_parser("scrape", help="Browser scrape company websites for emails")
    scrape.add_argument("--max-companies", type=int, default=None, help="Override max_companies_per_run in sender_config.json")
    scrape.add_argument("--max-queries", type=int, default=None, help="Override max_queries_per_run in sender_config.json")
    scrape.add_argument("--browser", choices=["auto", "chrome", "chromium", "firefox"], default="auto")
    scrape.add_argument("--region", default=default_region())
    scrape.add_argument("--no-seed", action="store_true", help="Do not auto-seed queries")
    scrape.add_argument("--no-nvidia-seed", action="store_true", help="Auto-seed from industries.json only")
    _add_industry_arg(scrape)

    replies = sub.add_parser("check-replies", help="Scan inboxes and forward human replies")
    replies.add_argument("--no-nvidia", action="store_true", help="Skip NVIDIA for borderline cases")

    send = sub.add_parser("send", help="Send partnership emails to scraped addresses")
    send.add_argument("--limit", type=int, default=None, help="Override emails_per_run")
    send.add_argument("--no-nvidia-praise", action="store_true")
    send.add_argument("--dry-run", action="store_true")
    send.add_argument("--skip-replies", action="store_true", help="Do not check inbox before send")
    send.add_argument("--no-nvidia-replies", action="store_true", help="Skip NVIDIA for borderline replies")

    run = sub.add_parser("run", help="Scrape then send in one run")
    run.add_argument("--max-companies", type=int, default=None, help="Override max_companies_per_run in sender_config.json")
    run.add_argument("--max-queries", type=int, default=None, help="Override max_queries_per_run in sender_config.json")
    run.add_argument("--send-limit", type=int, default=None)
    run.add_argument("--browser", choices=["auto", "chrome", "chromium", "firefox"], default="auto")
    run.add_argument("--region", default=default_region())
    run.add_argument("--no-seed", action="store_true")
    run.add_argument("--no-nvidia-seed", action="store_true")
    run.add_argument("--no-nvidia-praise", action="store_true")
    run.add_argument("--skip-replies", action="store_true")
    run.add_argument("--no-nvidia-replies", action="store_true")
    _add_industry_arg(run)

    return parser


def main() -> int:
    clear_terminal()
    init_logging()
    parser = build_parser()
    args = parser.parse_args()
    db = ImmigrationDB()
    try:
        if args.command == "list-industries":
            return cmd_list_industries()
        if args.command == "status":
            return cmd_status(db)
        if args.command == "seed-keywords":
            per = args.count or queries_per_industry()
            seed_all = args.all or not args.industry
            return cmd_seed_keywords(
                db,
                count=per,
                region=args.region,
                industry=args.industry,
                seed_all=seed_all,
                use_nvidia=not args.no_nvidia,
            )
        if args.command == "scrape":
            return cmd_scrape(args, db)
        if args.command == "check-replies":
            return cmd_check_replies(args, db)
        if args.command == "send":
            return cmd_send(args, db)
        if args.command == "run":
            return cmd_run(args, db)
        parser.print_help()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
