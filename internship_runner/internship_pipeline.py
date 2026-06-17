#!/usr/bin/env python3
"""
Internship workflow runner — separate roles with voice annotations.

Roles / commands:
  create   — Employer posts a new internship (draft)
  approve  — Admin approves and publishes
  apply    — Student applies to an open internship
  select   — Recruiter selects a candidate
  status   — Show database summary
  export-voice — Bundle transcripts for 3rd-party voice-over

Voice: Edge neural TTS (pip install edge-tts) when available.
       Always writes .txt transcripts + voice_manifest.jsonl for ElevenLabs etc.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from internship_db import InternshipDB, STATUS_DRAFT, STATUS_OPEN
from internship_notify import (
    annotate_stage,
    export_all_transcripts,
    load_config,
    pause_between_roles,
)

logger = logging.getLogger("internship_pipeline")


def setup_logger() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _defaults(cfg: dict) -> tuple[dict, dict]:
    return cfg.get("default_internship") or {}, cfg.get("default_applicant") or {}


def _auto(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "auto", False))


def _say(
    cfg: dict,
    args: argparse.Namespace,
    stage_key: str,
    role: str,
    **kwargs: object,
) -> None:
    annotate_stage(stage_key, role=role, config=cfg, auto=_auto(args), **kwargs)


def cmd_create(args: argparse.Namespace, db: InternshipDB, cfg: dict) -> int:
    d_intern, _ = _defaults(cfg)
    title = args.title or d_intern.get("title", "Software Engineering Intern")
    company = args.company or d_intern.get("company", "Acme Tech")
    description = args.description or d_intern.get("description", "")
    location = args.location or d_intern.get("location", "Remote")
    duration = args.weeks or d_intern.get("duration_weeks")

    _say(cfg, args, "create_start", "employer")
    internship_id = db.create_internship(
        title=title,
        company=company,
        description=description,
        location=location,
        duration_weeks=duration,
    )
    logger.info("Created internship #%s: %s @ %s (status: draft)", internship_id, title, company)
    _say(cfg, args, "create_complete", "employer", title=title, company=company)
    _say(cfg, args, "pipeline_complete", "employer")
    print(json.dumps({"internship_id": internship_id, "status": STATUS_DRAFT}, indent=2))
    return 0


def cmd_approve(args: argparse.Namespace, db: InternshipDB, cfg: dict) -> int:
    internship_id = args.id
    if not internship_id:
        drafts = db.list_internships(status=STATUS_DRAFT)
        if not drafts:
            logger.error("No draft internships to approve.")
            return 1
        internship_id = int(drafts[0]["id"])

    row = db.get_internship(internship_id)
    if not row:
        logger.error("Internship #%s not found.", internship_id)
        return 1

    _say(cfg, args, "approve_start", "approver", id=internship_id)
    if not db.approve_internship(internship_id, approved_by=args.approved_by or "admin"):
        logger.error("Internship #%s is not in draft status.", internship_id)
        return 1

    row = db.get_internship(internship_id)
    logger.info("Approved internship #%s — now open for applications.", internship_id)
    _say(
        cfg,
        args,
        "approve_complete",
        "approver",
        id=internship_id,
        title=row["title"],
        company=row["company"],
    )
    _say(cfg, args, "pipeline_complete", "approver")
    print(json.dumps({"internship_id": internship_id, "status": STATUS_OPEN}, indent=2))
    return 0


def cmd_apply(args: argparse.Namespace, db: InternshipDB, cfg: dict) -> int:
    _, d_app = _defaults(cfg)
    internship_id = args.id
    if not internship_id:
        open_rows = db.list_internships(status=STATUS_OPEN)
        if not open_rows:
            logger.error("No open internships. Run approve first.")
            return 1
        internship_id = int(open_rows[0]["id"])

    row = db.get_internship(internship_id)
    if not row:
        logger.error("Internship #%s not found.", internship_id)
        return 1

    name = args.name or d_app.get("name", "Applicant")
    email = args.email or d_app.get("email", "applicant@example.com")
    note = args.note or d_app.get("note", "")

    _say(cfg, args, "apply_start", "student", id=internship_id)
    app_id = db.apply(internship_id, applicant_name=name, email=email, note=note)
    if not app_id:
        logger.error("Cannot apply — internship #%s is not open.", internship_id)
        return 1

    logger.info("Application #%s submitted for internship #%s.", app_id, internship_id)
    _say(
        cfg,
        args,
        "apply_complete",
        "student",
        id=internship_id,
        title=row["title"],
        applicant_name=name,
    )
    _say(cfg, args, "pipeline_complete", "student")
    print(json.dumps({"application_id": app_id, "internship_id": internship_id}, indent=2))
    return 0


def cmd_select(args: argparse.Namespace, db: InternshipDB, cfg: dict) -> int:
    internship_id = args.id
    application_id = args.application_id

    if not internship_id:
        open_rows = db.list_internships(status=STATUS_OPEN)
        if not open_rows:
            logger.error("No open internships with pending applications.")
            return 1
        internship_id = int(open_rows[0]["id"])

    if not application_id:
        apps = db.list_applications(internship_id)
        if not apps:
            logger.error("No applications for internship #%s.", internship_id)
            return 1
        application_id = int(apps[0]["id"])

    internship = db.get_internship(internship_id)
    app = db.get_application(application_id)
    if not internship or not app:
        logger.error("Internship or application not found.")
        return 1

    _say(cfg, args, "select_start", "selector", id=internship_id)
    if not db.select_application(application_id):
        logger.error("Could not select application #%s.", application_id)
        return 1

    logger.info(
        "Selected %s (application #%s) for internship #%s.",
        app["applicant_name"],
        application_id,
        internship_id,
    )
    _say(
        cfg,
        args,
        "select_complete",
        "selector",
        id=internship_id,
        title=internship["title"],
        company=internship["company"],
        applicant_name=app["applicant_name"],
    )
    _say(cfg, args, "pipeline_complete", "selector")
    print(
        json.dumps(
            {
                "application_id": application_id,
                "internship_id": internship_id,
                "status": "selected",
            },
            indent=2,
        )
    )
    return 0


def cmd_status(_args: argparse.Namespace, db: InternshipDB, _cfg: dict) -> int:
    summary = db.summary()
    print(json.dumps(summary, indent=2))
    print()
    for row in db.list_internships():
        apps = db.list_applications(int(row["id"]))
        print(
            f"  #{row['id']} [{row['status']}] {row['title']} @ {row['company']} "
            f"— {len(apps)} application(s)"
        )
        for app in apps:
            print(f"      app #{app['id']} [{app['status']}] {app['applicant_name']} <{app['email']}>")
    return 0


def cmd_export_voice(_args: argparse.Namespace, _db: InternshipDB, _cfg: dict) -> int:
    dest = export_all_transcripts()
    logger.info("Transcripts exported to: %s", dest)
    return 0


def cmd_run_all(args: argparse.Namespace, db: InternshipDB, cfg: dict) -> int:
    """Full demo: create → approve → apply → select."""
    steps: list[tuple] = [
        (
            cmd_create,
            {
                "title": None,
                "company": None,
                "description": "",
                "location": "",
                "weeks": None,
            },
        ),
        (cmd_approve, {"id": None, "approved_by": "admin"}),
        (cmd_apply, {"id": None, "name": None, "email": None, "note": ""}),
        (cmd_select, {"id": None, "application_id": None}),
    ]
    for index, (fn, fields) in enumerate(steps):
        if index > 0 and _auto(args):
            pause_between_roles(cfg, auto=True)
        ns = argparse.Namespace(auto=_auto(args), **fields)
        code = fn(ns, db, cfg)
        if code != 0:
            return code
    return 0


def _add_auto_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Continuous run: timed pauses between narrations and roles (no keypress).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internship workflow with voice annotations")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Employer: create internship (draft)")
    create.add_argument("--title")
    create.add_argument("--company")
    create.add_argument("--description", default="")
    create.add_argument("--location", default="")
    create.add_argument("--weeks", type=int, default=None)
    _add_auto_arg(create)

    approve = sub.add_parser("approve", help="Approver: publish internship")
    approve.add_argument("--id", type=int, default=None)
    approve.add_argument("--approved-by", default="admin")
    _add_auto_arg(approve)

    apply = sub.add_parser("apply", help="Student: apply to internship")
    apply.add_argument("--id", type=int, default=None)
    apply.add_argument("--name")
    apply.add_argument("--email")
    apply.add_argument("--note", default="")
    _add_auto_arg(apply)

    select = sub.add_parser("select", help="Selector: pick a candidate")
    select.add_argument("--id", type=int, default=None, help="Internship ID")
    select.add_argument("--application-id", type=int, default=None)
    _add_auto_arg(select)

    sub.add_parser("status", help="Show internships and applications")
    sub.add_parser("export-voice", help="Export transcripts for 3rd-party voice-over")
    run_all = sub.add_parser("run-all", help="Demo all four roles in sequence")
    _add_auto_arg(run_all)

    return parser


def main() -> int:
    setup_logger()
    parser = build_parser()
    args = parser.parse_args()
    cfg = load_config()
    db = InternshipDB()
    try:
        handlers = {
            "create": cmd_create,
            "approve": cmd_approve,
            "apply": cmd_apply,
            "select": cmd_select,
            "status": cmd_status,
            "export-voice": cmd_export_voice,
            "run-all": cmd_run_all,
        }
        return handlers[args.command](args, db, cfg)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
