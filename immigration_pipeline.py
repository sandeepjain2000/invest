#!/usr/bin/env python3
"""
Immigration provider scrape + email pipeline.

Scrapes immigration service provider websites via browser (Chrome/Firefox),
extracts emails (landing page + Contact page), stores everything in SQLite,
and sends partnership emails using partnership.html + EmailJson SMTP config.

Usage:
  python immigration_pipeline.py status
  python immigration_pipeline.py seed-keywords --count 15 --region India
  python immigration_pipeline.py scrape --max-companies 20 --browser auto
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

from immigration_db import ImmigrationDB
from immigration_scraper import scrape_sync
from immigration_sender import get_emails_per_run, run_send
from nvidia_llm import DEFAULT_SEARCH_SEEDS, generate_search_queries

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

    for child in ("immigration_scraper", "immigration_sender", "nvidia_llm"):
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


def cmd_status(db: ImmigrationDB) -> int:
    summary = db.summary()
    logger.info("=== Immigration pipeline status ===")
    for key, value in summary.items():
        logger.info("  %-24s %s", key, value)
    return 0


def cmd_seed_keywords(db: ImmigrationDB, count: int, region: str) -> int:
    queries = generate_search_queries(count=count, region=region)
    if not queries:
        queries = DEFAULT_SEARCH_SEEDS[:count]
        source = "default_seed"
    else:
        source = "nvidia"
    added = db.add_search_queries(queries, source=source)
    logger.info("Added %s new search query(ies) (source=%s).", added, source)
    for q in queries[:5]:
        logger.info("  - %s", q)
    if len(queries) > 5:
        logger.info("  ... and %s more", len(queries) - 5)
    return 0


def cmd_scrape(args: argparse.Namespace, db: ImmigrationDB) -> int:
    with prevent_windows_sleep():
        stats = scrape_sync(
            db,
            max_companies=args.max_companies,
            max_queries=args.max_queries,
            browser=args.browser,
            region=args.region,
            seed_keywords=not args.no_seed,
        )
    logger.info("Scrape complete: %s", stats)
    return 0


def cmd_send(args: argparse.Namespace, db: ImmigrationDB) -> int:
    with prevent_windows_sleep():
        stats = run_send(
            db,
            limit=args.limit,
            use_nvidia_praise=not args.no_nvidia_praise,
            dry_run=args.dry_run,
        )
    logger.info("Send complete: %s", stats)
    if stats.get("error"):
        return 1
    return 0


def cmd_run(args: argparse.Namespace, db: ImmigrationDB) -> int:
    with prevent_windows_sleep():
        scrape_stats = scrape_sync(
            db,
            max_companies=args.max_companies,
            max_queries=args.max_queries,
            browser=args.browser,
            region=args.region,
            seed_keywords=not args.no_seed,
        )
        logger.info("Scrape complete: %s", scrape_stats)
        send_stats = run_send(
            db,
            limit=args.send_limit,
            use_nvidia_praise=not args.no_nvidia_praise,
            dry_run=getattr(args, "dry_run", False),
        )
        logger.info("Send complete: %s", send_stats)
    return 0 if not send_stats.get("error") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Immigration scrape + email pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show SQLite summary counts")

    seed = sub.add_parser("seed-keywords", help="Generate search queries via NVIDIA")
    seed.add_argument("--count", type=int, default=12)
    seed.add_argument("--region", default="India")

    scrape = sub.add_parser("scrape", help="Browser scrape immigration providers")
    scrape.add_argument("--max-companies", type=int, default=20)
    scrape.add_argument("--max-queries", type=int, default=5)
    scrape.add_argument("--browser", choices=["auto", "chrome", "chromium", "firefox"], default="auto")
    scrape.add_argument("--region", default="India")
    scrape.add_argument("--no-seed", action="store_true", help="Do not auto-seed queries")

    send = sub.add_parser("send", help="Send partnership emails to scraped addresses")
    send.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Override emails_per_run from sender_config.json",
    )
    send.add_argument("--no-nvidia-praise", action="store_true")
    send.add_argument("--dry-run", action="store_true", help="Build messages only; do not SMTP send")

    run = sub.add_parser("run", help="Scrape then send in one run")
    run.add_argument("--max-companies", type=int, default=15)
    run.add_argument("--max-queries", type=int, default=5)
    run.add_argument(
        "--send-limit",
        type=int,
        default=None,
        help="Override emails_per_run from sender_config.json",
    )
    run.add_argument("--browser", choices=["auto", "chrome", "chromium", "firefox"], default="auto")
    run.add_argument("--region", default="India")
    run.add_argument("--no-seed", action="store_true")
    run.add_argument("--no-nvidia-praise", action="store_true")

    return parser


def main() -> int:
    clear_terminal()
    init_logging()
    parser = build_parser()
    args = parser.parse_args()
    db = ImmigrationDB()
    try:
        if args.command == "status":
            return cmd_status(db)
        if args.command == "seed-keywords":
            return cmd_seed_keywords(db, args.count, args.region)
        if args.command == "scrape":
            return cmd_scrape(args, db)
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
