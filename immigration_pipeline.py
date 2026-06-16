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

from pipeline_notify import notify_run_complete, notify_stage
from check_replies import run_check_replies
from immigration_db import ImmigrationDB
from immigration_scraper import scrape_sync
from immigration_sender import (
    get_emails_per_run,
    get_ensure_industry_settings,
    get_max_companies_per_run,
    get_max_queries_per_run,
    get_min_ensure_ca_connect_per_run,
    get_reserved_send_by_industry,
    load_sender_config,
    run_send,
    run_test_send,
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

    for child in ("immigration_scraper", "immigration_sender", "nvidia_llm", "check_replies", "ca_connect_pipeline"):
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


def _notify_pipeline_complete(*, dry_run: bool = False) -> None:
    """Long beep + optional spoken message when a pipeline command finishes."""
    notify_run_complete(dry_run=dry_run)


def _notify_scrape_complete(scrape_stats: dict, *, dry_run: bool = False) -> None:
    cfg = load_sender_config()
    notify_stage(
        cfg,
        "scrape_complete",
        dry_run=dry_run,
        with_email=int(scrape_stats.get("companies_with_email") or 0),
        emails=int(scrape_stats.get("emails_found") or 0),
    )


def cmd_scrape(args: argparse.Namespace, db: ImmigrationDB) -> int:
    industry = _validate_industry(getattr(args, "industry", None))
    dry_run = getattr(args, "dry_run", False)
    with prevent_windows_sleep():
        stats = scrape_sync(db, **_scrape_kwargs(args, industry), dry_run=dry_run)
    logger.info("Scrape complete: %s", stats)
    print_execution_summary(scrape_stats=stats, db=db)
    _notify_scrape_complete(stats, dry_run=dry_run)
    _notify_pipeline_complete(dry_run=dry_run)
    return 0


def _summary_stat(label: str, value, note: str = "") -> None:
    """Log one summary row: label, value, and an optional plain-language note."""
    logger.info("    %-32s %s", f"{label}:", value)
    if note:
        logger.info("      %s", note)


def print_execution_summary(
    *,
    scrape_stats: dict | None = None,
    reply_stats: dict | None = None,
    send_stats: dict | None = None,
    db: ImmigrationDB | None = None,
) -> None:
    """Print end-of-run summary to console and log (shown before batch file pause)."""
    cfg = load_sender_config()
    ensure_industry, min_send, min_scrape = get_ensure_industry_settings()
    min_ca_connect = get_min_ensure_ca_connect_per_run()
    industry_reserves = get_reserved_send_by_industry()

    logger.info("")
    logger.info("=" * 70)
    logger.info("  RUN SUMMARY")
    logger.info("=" * 70)

    if scrape_stats is not None:
        ca = scrape_stats.get("ca_connect") or {}
        ca_imported = int(ca.get("ca_emails_imported") or 0)
        total_with_email = int(scrape_stats.get("companies_with_email") or 0)
        total_emails = int(scrape_stats.get("emails_found") or 0)
        google_with_email = max(0, total_with_email - ca_imported)
        google_emails = max(0, total_emails - ca_imported)

        logger.info("  SCRAPE — this run")
        _summary_stat(
            "Run limits (config)",
            f"max {scrape_stats.get('max_companies', get_max_companies_per_run())} sites, "
            f"stop at {scrape_stats.get('email_target', get_emails_per_run())} with email, "
            f"max {get_max_queries_per_run()} Google queries",
            "Caps from sender_config.json; scrape stops early when email target is met.",
        )
        if scrape_stats.get("industry_filter") and scrape_stats.get("industry_filter") != "all":
            _summary_stat(
                "Industry filter",
                industry_name(scrape_stats["industry_filter"]),
                "Only this vertical was scraped (not the full rotation).",
            )

        if ca.get("ca_connect_ran"):
            logger.info("")
            logger.info("  CA Connect — https://caconnect.icai.org/")
            _summary_stat(
                "Search filters",
                ca.get("ca_connect_search", ""),
                "Service, state, and city from ca_connect_credentials.json.",
            )
            _summary_stat(
                "Listings on results page",
                ca.get("ca_connect_listings", 0),
                "Member + firm cards returned by CA Connect search (not all have email).",
            )
            _summary_stat(
                "Profile pages opened",
                ca.get("ca_profiles_enriched", 0),
                "Logged-in profile visits to extract email/mobile this run.",
            )
            failed = int(ca.get("ca_profiles_failed") or 0)
            if failed:
                _summary_stat(
                    "Profile pages failed",
                    failed,
                    "Could not read email from these profile URLs.",
                )
            _summary_stat(
                "New emails added to DB",
                ca_imported,
                "First-time imports into immigration.db as ca_cs_firms (duplicates skipped).",
            )
            upserted = int(ca.get("ca_companies_upserted") or 0)
            if upserted != ca_imported:
                _summary_stat(
                    "CA records upserted",
                    upserted,
                    "Companies created/updated in DB (may include already-known emails).",
                )
            if ca.get("error"):
                _summary_stat("CA Connect error", ca.get("error"))
            elif not ca.get("ca_connect_logged_in"):
                _summary_stat(
                    "CA Connect login",
                    "failed",
                    "Profile emails require ca_connect_credentials.json.",
                )
        elif ensure_industry and min_scrape > 0:
            _summary_stat(
                "CA Connect",
                "not run",
                "Expected for ca_cs_firms — check logs above for login/import errors.",
            )

        logger.info("")
        logger.info("  Google website scrape — this run")
        _summary_stat(
            "Websites visited",
            scrape_stats.get("companies_scraped", 0),
            "Company sites opened from Google search results (excludes CA Connect).",
        )
        _summary_stat(
            "Sites where email was found",
            google_with_email,
            "Distinct companies with at least one new email this run (Google only).",
        )
        _summary_stat(
            "Email addresses discovered",
            google_emails,
            "New email rows saved to the database from Google scraping.",
        )
        _summary_stat(
            "Google queries executed",
            scrape_stats.get("queries_run", 0),
            "Search queries consumed from the pending queue this run.",
        )

        if scrape_stats.get("ensure_industry_scrape"):
            iid = scrape_stats.get("ensure_industry_scrape", "")
            have = int(scrape_stats.get("ensure_industry_with_email") or 0)
            goal = int(scrape_stats.get("ensure_industry_scrape_target") or 0)
            met = "met" if have >= goal else "NOT MET"
            _summary_stat(
                f"Reserved industry ({iid})",
                f"{have} / {goal} new emails ({met})",
                f"{industry_name(iid)} — minimum new contacts per run "
                f"(sourced via CA Connect for ca_cs_firms).",
            )

        if scrape_stats.get("browser_used"):
            _summary_stat(
                "Browser",
                scrape_stats.get("browser_used"),
                "Engine used for Google website scraping.",
            )

    if reply_stats is not None:
        logger.info("")
        logger.info("  REPLIES — this run")
        if reply_stats.get("skipped"):
            _summary_stat(
                "Inbox check",
                "skipped",
                "Reply forwarding is off (check_replies_before_send: false in sender_config.json).",
            )
        else:
            _summary_stat(
                "Messages scanned",
                reply_stats.get("scanned", 0),
                "Inbox messages examined for human replies.",
            )
            _summary_stat(
                "Replies forwarded",
                reply_stats.get("forwarded", 0),
                f"Forwarded to {cfg.get('forward_to', 'forward_to in config')}.",
            )

    if send_stats is not None:
        logger.info("")
        logger.info("  SEND — this run")
        if send_stats.get("error"):
            _summary_stat("Error", send_stats.get("error"))
        limit = send_stats.get("send_limit") or get_emails_per_run()
        if send_stats.get("queue_size") is not None:
            _summary_stat(
                "Recipients queued",
                send_stats.get("queue_size", 0),
                f"Unsent contacts considered this run (send cap: {limit} per run).",
            )
        if min_ca_connect > 0:
            ca_reserved = int(send_stats.get("ensure_ca_connect_reserved") or 0)
            ca_sent = int(send_stats.get("sent_ca_connect") or 0)
            _summary_stat(
                "Queue slots for CA Connect JSON",
                f"{ca_reserved} / {min_ca_connect} reserved",
                "Individual CAs from ca_connect_results.json (caconnect.icai.org profiles), "
                "not Google-scraped CA firm sites.",
            )
            _summary_stat(
                "Sent to CA Connect JSON",
                ca_sent,
                "Funding intro emails to CAs imported from CA Connect this run.",
            )
        reserved_by = send_stats.get("reserved_in_queue_by_industry") or {}
        for iid, min_slots in industry_reserves.items():
            reserved = int(reserved_by.get(iid) or 0)
            _summary_stat(
                f"Queue slots for {iid}",
                f"{reserved} / {min_slots} reserved",
                f"{industry_name(iid)} — minimum send slots per run when contacts are available.",
            )
        if ensure_industry and min_send > 0:
            reserved = int(send_stats.get("ensure_reserved_in_queue") or 0)
            _summary_stat(
                f"Queue slots for {ensure_industry}",
                f"{reserved} / {min_send} reserved",
                f"{industry_name(ensure_industry)} — at least {min_send} email(s) "
                "held for this industry when contacts are available.",
            )
        _summary_stat(
            "Emails sent successfully",
            send_stats.get("sent", 0),
            "Partnership / funding intro emails delivered via Brevo this run.",
        )
        _summary_stat(
            "Send failures",
            send_stats.get("failed", 0),
            "Brevo API or transport errors (not counted as sent).",
        )
        _summary_stat(
            "Skipped",
            send_stats.get("skipped", 0),
            "Dry-run previews or recipients already marked sent in the database.",
        )
        sent_by = send_stats.get("sent_by_industry") or {}
        if sent_by:
            logger.info("    Sent by industry:")
            for iid, count in sorted(sent_by.items(), key=lambda x: (-x[1], x[0])):
                logger.info("      %-30s %s", industry_name(iid), count)

    if db is not None:
        totals = db.summary()
        unsent = db.count_unsent_recipients()
        logger.info("")
        logger.info("  DATABASE — cumulative (all runs)")
        _summary_stat(
            "Companies stored",
            totals.get("companies_total", 0),
            "All scraped organisations across every industry vertical.",
        )
        _summary_stat(
            "Companies with email",
            totals.get("companies_with_email", 0),
            "Records with email_status = found (ready or already used for outreach).",
        )
        _summary_stat(
            "Email addresses on file",
            totals.get("emails_found", 0),
            "Total rows in company_emails (one company may have several).",
        )
        _summary_stat(
            "Emails sent (lifetime)",
            totals.get("emails_sent", 0),
            "Unique recipients successfully emailed at least once.",
        )
        _summary_stat(
            "Send failures (lifetime)",
            totals.get("emails_failed", 0),
            "Recipients with a failed status in email_sent.",
        )
        _summary_stat(
            "Replies forwarded (lifetime)",
            totals.get("replies_forwarded", 0),
            "Human replies forwarded to your Gmail.",
        )
        _summary_stat(
            "Ready to send (now)",
            unsent,
            "Unsent contacts still in the outbound queue for future runs.",
        )
        _summary_stat(
            "Google queries pending",
            totals.get("search_queries_pending", 0),
            "Unused search queries waiting for future scrape runs.",
        )
        if ensure_industry:
            ca_pending = db.count_unsent_recipients_for_industry(ensure_industry)
            _summary_stat(
                f"Unsent {ensure_industry}",
                ca_pending,
                f"{industry_name(ensure_industry)} contacts not yet emailed (all sources).",
            )
        unsent_ca_json = db.count_unsent_ca_connect_recipients()
        if unsent_ca_json or min_ca_connect > 0:
            _summary_stat(
                "Unsent CA Connect JSON",
                unsent_ca_json,
                "Individual CAs from ca_connect_results.json still available to email.",
            )
        by_industry = db.summary_by_industry()
        if by_industry:
            logger.info("    Companies by industry (with email / total):")
            for row in by_industry:
                logger.info(
                    "      %-32s %s / %s",
                    industry_name(row["industry"]),
                    row["companies_with_email"],
                    row["companies_total"],
                )

    logger.info("=" * 70)
    logger.info("")


def _maybe_check_replies(db: ImmigrationDB, args: argparse.Namespace) -> dict:
    skipped = {"skipped": True, "scanned": 0, "forwarded": 0}
    if getattr(args, "dry_run", False):
        return skipped
    if getattr(args, "skip_replies", False):
        return skipped
    if not getattr(args, "check_replies", False) and not load_sender_config().get(
        "check_replies_before_send", False
    ):
        return skipped
    logger.info("=== Checking inboxes for human replies ===")
    cfg = load_sender_config()
    notify_stage(cfg, "reply_check_start", dry_run=getattr(args, "dry_run", False))
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
        if getattr(args, "test_to", None):
            stats = run_test_send(
                db,
                test_to=args.test_to,
                use_nvidia_praise=not args.no_nvidia_praise,
                dry_run=args.dry_run,
            )
        else:
            stats = run_send(
                db,
                limit=args.limit,
                use_nvidia_praise=not args.no_nvidia_praise,
                dry_run=args.dry_run,
            )
    logger.info("Send complete: %s", stats)
    print_execution_summary(reply_stats=reply_stats, send_stats=stats, db=db)
    _notify_pipeline_complete(dry_run=args.dry_run)
    if stats.get("error"):
        return 1
    return 0


def cmd_run(args: argparse.Namespace, db: ImmigrationDB) -> int:
    industry = _validate_industry(getattr(args, "industry", None))
    dry_run = getattr(args, "dry_run", False)
    cfg = load_sender_config()
    notify_stage(cfg, "run_start", dry_run=dry_run)
    with prevent_windows_sleep():
        scrape_stats = scrape_sync(db, **_scrape_kwargs(args, industry), dry_run=dry_run)
        logger.info("Scrape complete: %s", scrape_stats)
        _notify_scrape_complete(scrape_stats, dry_run=dry_run)
        reply_stats = _maybe_check_replies(db, args)
        send_stats = run_send(
            db,
            limit=args.send_limit,
            use_nvidia_praise=not args.no_nvidia_praise,
            dry_run=dry_run,
        )
        logger.info("Send complete: %s", send_stats)
    print_execution_summary(
        scrape_stats=scrape_stats,
        reply_stats=reply_stats,
        send_stats=send_stats,
        db=db,
    )
    _notify_pipeline_complete(dry_run=dry_run)
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
    send.add_argument(
        "--test-to",
        metavar="EMAIL",
        help="Send one test email to this address (last scraped company template); does not update database",
    )
    send.add_argument("--skip-replies", action="store_true", help="Do not check inbox before send")
    send.add_argument(
        "--check-replies",
        action="store_true",
        help="Scan inbox and forward replies before send (off by default; Brevo single sender)",
    )
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
    run.add_argument(
        "--check-replies",
        action="store_true",
        help="Scan inbox and forward replies before send (off by default)",
    )
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
