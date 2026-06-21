"""Send partnership emails to scraped companies (Brevo API or legacy Gmail SMTP)."""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid
from pathlib import Path

from brevo_mail import (
    audit_brevo_message,
    ensure_brevo_sender_verified,
    load_brevo_transport,
    resolve_mail_config_path,
    send_html_via_brevo,
)
from ca_bulk_db import CaBulkDB
from immigration_db import ImmigrationDB, clean_domain, is_ca_connect_contact
from industries import (
    load_subject_template_text,
    signature_links_for,
    template_file_for,
    use_nvidia_praise_for,
)
from nvidia_llm import generate_company_praise
from pipeline_notify import notify_stage
from pipeline_progress import send_email_content, send_plan, send_status
from project_paths import resolve_body_template_path, resolve_config_file, resolve_project_path

logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
SMTP_CONFIG_FILE = Path(
    os.environ.get(
        "EMAIL_CONFIG_FILE",
        r"C:\Users\sandeep\Downloads\Claudes\EmailJson\email_config1001.json",
    )
)

EMAIL_SEND_DELAY = 10
SAME_DOMAIN_DELAY = 20

_domain_last_sent: dict[str, float] = {}


def sleep_between_sends(seconds: float, *, reason: str = "") -> None:
    """Pause between outbound emails; visible on terminal and in logs."""
    if seconds <= 0:
        return
    wait_display = int(seconds) if float(seconds).is_integer() else round(seconds, 1)
    suffix = f" ({reason})" if reason else ""
    print(f"Sleeping for {wait_display} seconds{suffix}...", flush=True)
    logger.info("Sleeping for %s seconds%s", wait_display, suffix)
    time.sleep(seconds)


def load_sender_config() -> dict:
    defaults = {
        "sender_name": "Sandeep Jain",
        "company_name": "PlacementsHub",
        "phone": "",
        "email": "",
        "website": "",
        "signature_links": [],
        "email_subject": "Exploring a potential partnership opportunity",
        "campaign_subjects": [],
        "forward_to": "sandeepjain200019@gmail.com",
        "check_replies_before_send": False,
        "check_replies_lookback_days": 30,
        "emails_per_run": 32,
        "max_companies_per_run": 100,
        "max_queries_per_run": 40,
        "send_method": "brevo_api",
        "template_file": "partnership.html",
        "mail_config_file": "config/mail_config.json",
        "ensure_industry_per_run": "",
        "min_ensure_industry_per_run": 0,
        "min_ensure_industry_scrape_per_run": 0,
        "min_ensure_ca_connect_per_run": 8,
        "reserved_send_by_industry": {
            "company_secretary_firms": 4,
            "tax_consultants": 4,
        },
        "ca_connect_enabled": False,
        "ca_connect_profiles_per_run": 10,
        "ca_connect_credentials_file": "config/ca_connect_credentials.json",
        "ca_connect_results_file": "data/ca_connect_results.json",
        "ca_bulk_send_enabled": True,
        "ca_bulk_send_only": True,
        "ca_bulk_emails_per_run": 10,
        "ca_bulk_database_file": "data/db/ca_bulk.db",
        "pipeline_complete_voice": True,
        "pipeline_stage_voice": True,
        "pipeline_complete_voice_message": "Partnership pipeline run complete.",
    }
    config_path = resolve_config_file("sender_config.json")
    if config_path.is_file():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        defaults.update(data)
    return defaults


def get_template_path(sender: dict | None = None, *, template_file: str | None = None) -> Path:
    sender = sender or load_sender_config()
    raw = (template_file or sender.get("template_file") or "partnership.html").strip()
    return resolve_body_template_path(raw)


def resolve_send_settings(
    industry_id: str,
    sender_cfg: dict,
    *,
    use_nvidia_praise: bool | None = None,
) -> dict:
    """Merge sender_config.json defaults with per-industry overrides from industries.json."""
    praise_enabled = (
        use_nvidia_praise
        if use_nvidia_praise is not None
        else use_nvidia_praise_for(industry_id)
    )
    body_template = template_file_for(industry_id) or sender_cfg.get("template_file", "partnership.html")
    return {
        "template_file": body_template,
        "use_nvidia_praise": praise_enabled,
        "signature_links": signature_links_for(industry_id) or sender_cfg.get("signature_links") or [],
    }


def get_emails_per_run() -> int:
    """Max emails to send in one execution (from sender_config.json)."""
    value = load_sender_config().get("emails_per_run", 10)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 10


def get_max_companies_per_run() -> int:
    """Max company sites to scrape per run (from sender_config.json)."""
    value = load_sender_config().get("max_companies_per_run", 50)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 50


def get_max_queries_per_run() -> int:
    """Max Google search queries per scrape run (safety cap)."""
    value = load_sender_config().get("max_queries_per_run", 20)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 20


def get_scrape_headless() -> bool:
    """Run Google scrape in headless mode when true (default: visible browser)."""
    return bool(load_sender_config().get("scrape_headless", False))


def get_ensure_industry_settings() -> tuple[str | None, int, int]:
    """Return (industry_id, min_send_per_run, min_scrape_per_run) from sender_config.json."""
    cfg = load_sender_config()
    industry = (cfg.get("ensure_industry_per_run") or "").strip() or None
    try:
        min_send = max(0, int(cfg.get("min_ensure_industry_per_run", 0)))
    except (TypeError, ValueError):
        min_send = 0
    try:
        min_scrape = max(0, int(cfg.get("min_ensure_industry_scrape_per_run", 0)))
    except (TypeError, ValueError):
        min_scrape = 0
    return industry, min_send, min_scrape


def get_min_ensure_ca_connect_per_run() -> int:
    """Min send slots reserved for CA portal contacts (ca_bulk.db or immigration import)."""
    try:
        return max(0, int(load_sender_config().get("min_ensure_ca_connect_per_run", 0)))
    except (TypeError, ValueError):
        return 0


def get_ca_bulk_send_settings() -> dict:
    cfg = load_sender_config()
    raw = (cfg.get("ca_bulk_database_file") or "data/db/ca_bulk.db").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = _SCRIPT_DIR / path
    bulk_enabled = bool(cfg.get("ca_bulk_send_enabled", True))
    return {
        "enabled": bulk_enabled,
        "send_only": bool(cfg.get("ca_bulk_send_only", bulk_enabled)),
        "database_file": path,
    }


def load_ca_bulk_send_candidates(db: ImmigrationDB) -> list[dict]:
    """Unsent CA portal contacts from ca_bulk.db (no copy into immigration.db)."""
    settings = get_ca_bulk_send_settings()
    if not settings["enabled"]:
        return []
    path = settings["database_file"]
    if not path.exists():
        return []
    bulk_db = CaBulkDB(path)
    try:
        return bulk_db.pending_send_candidates(db.sent_email_addresses())
    finally:
        bulk_db.close()


def count_unsent_ca_portal_recipients(db: ImmigrationDB) -> dict[str, int]:
    """Unsent CA portal contacts; when ca_bulk_send_only, bulk DB is the sole source."""
    settings = get_ca_bulk_send_settings()
    sent = db.sent_email_addresses()
    bulk_count = 0
    if settings["enabled"] and settings["database_file"].exists():
        bulk_db = CaBulkDB(settings["database_file"])
        try:
            bulk_items = bulk_db.pending_send_candidates(sent)
            bulk_count = len(bulk_items)
        finally:
            bulk_db.close()
    if settings.get("send_only"):
        return {"bulk": bulk_count, "immigration": 0, "total": bulk_count}
    bulk_emails = set()
    if bulk_count:
        bulk_db = CaBulkDB(settings["database_file"])
        try:
            bulk_emails = {
                (item.get("email") or "").lower()
                for item in bulk_db.pending_send_candidates(sent)
            }
        finally:
            bulk_db.close()
    immigration_count = sum(
        1
        for item in db._pending_send_candidates()
        if is_ca_connect_contact(item)
        and (item.get("email") or "").lower() not in bulk_emails
    )
    return {
        "bulk": bulk_count,
        "immigration": immigration_count,
        "total": bulk_count + immigration_count,
    }


def get_reserved_send_by_industry() -> dict[str, int]:
    """Industry ID -> min send slots reserved per run (from reserved_send_by_industry)."""
    raw = load_sender_config().get("reserved_send_by_industry") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for iid, count in raw.items():
        key = (str(iid) or "").strip()
        if not key:
            continue
        try:
            n = max(0, int(count))
        except (TypeError, ValueError):
            continue
        if n > 0:
            out[key] = n
    return out


def sync_ca_connect_json_to_db(db: ImmigrationDB) -> dict:
    """Import any enriched emails from ca_connect_results.json before building send queue."""
    from ca_connect_pipeline import import_ca_listings_to_db

    cfg = load_sender_config()
    path = Path(cfg.get("ca_connect_results_file", "data/ca_connect_results.json"))
    if not path.is_absolute():
        path = _SCRIPT_DIR / path
    if not path.exists():
        return {"emails_added": 0, "skipped": True}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read CA Connect JSON for send sync: %s", exc)
        return {"emails_added": 0, "error": str(exc)}
    stats = import_ca_listings_to_db(db, data.get("results") or [])
    if stats.get("emails_added"):
        logger.info(
            "Synced %s new CA Connect email(s) from JSON into database before send.",
            stats["emails_added"],
        )
    return stats


def get_send_method() -> str:
    return (load_sender_config().get("send_method") or "brevo_api").strip().lower()


def load_smtp_profiles(path: Path | None = None) -> tuple[list[dict], dict[str, str]]:
    config_path = path or SMTP_CONFIG_FILE
    if not config_path.exists():
        logger.error("SMTP config not found: %s", config_path)
        return [], {}
    data = json.loads(config_path.read_text(encoding="utf-8"))
    passwords = data.get("profiles", {})
    profiles = [
        {"email": email, "name": email}
        for email, password in passwords.items()
        if (email or "").strip() and (password or "").strip()
    ]
    return profiles, passwords


def _render_signature_links(sender: dict, links: list | None = None) -> str:
    link_items = links if links is not None else (sender.get("signature_links") or [])
    if not link_items and sender.get("website"):
        link_items = [{"label": sender.get("company_name", "Website"), "url": sender["website"]}]
    lines: list[str] = []
    for item in link_items:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        if not url:
            continue
        label = (item.get("label") or "").strip() or url
        lines.append(f'<a href="{url}">{label}</a>')
    if not lines:
        return ""
    return "<br>\n".join(lines) + "<br>\n"


def read_template(
    company_name: str,
    company_praise: str,
    sender: dict,
    *,
    template_path: Path | None = None,
    signature_links: list | None = None,
) -> str | None:
    path = template_path or get_template_path(sender)
    if not path.exists():
        logger.error("Template not found: %s", path)
        return None
    html = path.read_text(encoding="utf-8")
    signature_email = (sender.get("email") or "").strip()
    email_html = (
        f'<a href="mailto:{signature_email}">{signature_email}</a>'
        if signature_email
        else ""
    )
    return (
        html.replace("{{RecipientCompany}}", company_name)
        .replace("{{CompanyPraise}}", company_praise)
        .replace("{{SenderName}}", sender.get("sender_name", ""))
        .replace("{{CompanyName}}", sender.get("company_name", ""))
        .replace("{{Phone}}", sender.get("phone", ""))
        .replace("{{Email}}", email_html)
        .replace("{{SignatureLinks}}", _render_signature_links(sender, signature_links))
    )


def build_email_message(
    *,
    industry_id: str,
    company_name: str,
    domain: str,
    website: str,
    sender_cfg: dict,
    use_nvidia_praise: bool | None = None,
) -> tuple[str | None, str]:
    settings = resolve_send_settings(industry_id, sender_cfg, use_nvidia_praise=use_nvidia_praise)
    praise = ""
    if settings["use_nvidia_praise"]:
        praise = _build_praise(
            company_name,
            website,
            use_nvidia_praise=True,
            industry_id=industry_id,
        )
    template_path = get_template_path(sender_cfg, template_file=settings["template_file"])
    html = read_template(
        company_name,
        praise,
        sender_cfg,
        template_path=template_path,
        signature_links=settings["signature_links"],
    )
    subject = load_subject_template_text(
        industry_id,
        settings["template_file"],
        company_name=company_name,
        domain=domain,
        fallback_subject=sender_cfg.get("email_subject", "Exploring a potential partnership opportunity"),
    )
    return html, subject


def check_domain_delay(domain: str) -> None:
    if not domain:
        return
    last = _domain_last_sent.get(domain)
    if last is not None:
        elapsed = time.time() - last
        if elapsed < SAME_DOMAIN_DELAY:
            wait = SAME_DOMAIN_DELAY - elapsed
            sleep_between_sends(wait, reason=f"same domain {domain}")
    _domain_last_sent[domain] = time.time()


def _build_praise(
    company_name: str,
    website: str,
    *,
    use_nvidia_praise: bool,
    industry_id: str,
) -> str:
    if use_nvidia_praise:
        return generate_company_praise(company_name, website, industry_id=industry_id)
    return (
        f"I was impressed by {company_name}'s dedication to supporting students "
        "and strengthening career outcomes."
    )


def _record_send_result(
    db: ImmigrationDB,
    *,
    recipient: str,
    company_id: int | None,
    company_name: str,
    from_profile: str,
    subject: str,
    status: str,
    message_id: str = "",
    error_message: str = "",
) -> None:
    db.record_email_sent(
        email=recipient,
        company_id=company_id,
        company_name=company_name,
        from_profile=from_profile,
        subject=subject,
        status=status,
        message_id=message_id or None,
        error_message=error_message or None,
    )


def send_one(
    db: ImmigrationDB,
    *,
    recipient: str,
    company_id: int | None,
    company_name: str,
    domain: str,
    website: str,
    sender_cfg: dict,
    use_nvidia_praise: bool = True,
    industry_id: str = "overseas_education_immigration",
    from_email: str = "",
    smtp_password: str = "",
    brevo_transport: dict | None = None,
) -> bool:
    if db.email_already_sent(recipient):
        logger.info("Already sent: %s", recipient)
        return False

    check_domain_delay(domain)
    industry_id = industry_id or "overseas_education_immigration"
    html, subject = build_email_message(
        industry_id=industry_id,
        company_name=company_name,
        domain=domain,
        website=website,
        sender_cfg=sender_cfg,
        use_nvidia_praise=use_nvidia_praise,
    )
    if not html:
        return False

    send_email_content(recipient=recipient, subject=subject, html=html)

    db.ensure_campaign_subject(
        (subject.split(" with ")[0] if " with " in subject else subject).strip()
    )

    method = get_send_method()
    if method == "brevo_api":
        transport = brevo_transport or load_brevo_transport(sender_cfg)
        from_profile = transport["sender_email"]
        try:
            message_id = send_html_via_brevo(
                recipient=recipient,
                subject=subject,
                html_content=html,
                sender_name=transport["sender_name"],
                sender_email=transport["sender_email"],
                api_key=transport["api_key"],
            )
            logger.info(
                "SENT (Brevo) -> %s (%s) | %s | messageId=%s "
                "(API accepted; run audit-brevo to confirm delivery)",
                recipient,
                company_name,
                subject,
                message_id or "—",
            )
            _record_send_result(
                db,
                recipient=recipient,
                company_id=company_id,
                company_name=company_name,
                from_profile=from_profile,
                subject=subject,
                status="sent",
                message_id=message_id,
            )
            sleep_between_sends(EMAIL_SEND_DELAY)
            return True
        except Exception as exc:
            logger.error("Brevo send failed for %s: %s", recipient, exc)
            _record_send_result(
                db,
                recipient=recipient,
                company_id=company_id,
                company_name=company_name,
                from_profile=from_profile,
                subject=subject,
                status="failed",
                error_message=str(exc),
            )
            return False

    if not from_email or not smtp_password:
        logger.error("Gmail SMTP requires from_email and smtp_password")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = recipient
    msg["Message-ID"] = make_msgid()
    message_id = msg["Message-ID"]
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(from_email, smtp_password)
            server.send_message(msg)
        logger.info("SENT (Gmail) -> %s (%s) | %s", recipient, company_name, subject)
        _record_send_result(
            db,
            recipient=recipient,
            company_id=company_id,
            company_name=company_name,
            from_profile=from_email,
            subject=subject,
            status="sent",
            message_id=message_id,
        )
        sleep_between_sends(EMAIL_SEND_DELAY)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP auth failed for %s", from_email)
        _record_send_result(
            db,
            recipient=recipient,
            company_id=company_id,
            company_name=company_name,
            from_profile=from_email,
            subject=subject,
            status="failed",
            error_message="smtp_auth_error",
        )
        return False
    except Exception as exc:
        logger.error("Send failed for %s: %s", recipient, exc)
        _record_send_result(
            db,
            recipient=recipient,
            company_id=company_id,
            company_name=company_name,
            from_profile=from_email,
            subject=subject,
            status="failed",
            error_message=str(exc),
        )
        return False


def get_ca_bulk_emails_per_run() -> int:
    try:
        return max(1, int(load_sender_config().get("ca_bulk_emails_per_run", 10)))
    except (TypeError, ValueError):
        return 10


def run_send_ca_bulk(
    db: ImmigrationDB,
    *,
    limit: int | None = None,
    smtp_config: Path | None = None,
    use_nvidia_praise: bool = True,
    dry_run: bool = False,
) -> dict:
    """Send only to CAs from ca_bulk.db (caconnect.icai.org harvest). No scrape."""
    return run_send(
        db,
        limit=limit,
        smtp_config=smtp_config,
        use_nvidia_praise=use_nvidia_praise,
        dry_run=dry_run,
        ca_only=True,
    )


def run_send(
    db: ImmigrationDB,
    *,
    limit: int | None = None,
    smtp_config: Path | None = None,
    use_nvidia_praise: bool = True,
    dry_run: bool = False,
    ca_only: bool = False,
) -> dict:
    sender_cfg = load_sender_config()
    method = get_send_method()
    brevo_transport = None
    profiles: list[dict] = []
    passwords: dict[str, str] = {}

    if method == "brevo_api":
        try:
            brevo_transport = load_brevo_transport(sender_cfg)
            ensure_brevo_sender_verified(brevo_transport)
            logger.info(
                "Send transport: Brevo API (config: %s, template: %s, sender: %s)",
                resolve_mail_config_path(sender_cfg),
                get_template_path(sender_cfg),
                brevo_transport["sender_email"],
            )
        except (FileNotFoundError, ValueError) as exc:
            if dry_run:
                logger.warning("Dry-run: Brevo config not loaded (%s)", exc)
                brevo_transport = {
                    "sender_email": sender_cfg.get("email") or "brevo-sender@configured",
                    "sender_name": sender_cfg.get("sender_name", "Sender"),
                    "api_key": "",
                }
            else:
                return {"sent": 0, "failed": 0, "skipped": 0, "error": str(exc)}
    else:
        profiles, passwords = load_smtp_profiles(smtp_config)
        if not profiles:
            return {"sent": 0, "failed": 0, "skipped": 0, "error": "no_smtp_profiles"}
        logger.info("Send transport: Gmail SMTP")

    send_limit = limit if limit is not None else (
        get_ca_bulk_emails_per_run() if ca_only else get_emails_per_run()
    )

    if ca_only:
        bulk_settings = get_ca_bulk_send_settings()
        if not bulk_settings["enabled"]:
            return {"sent": 0, "failed": 0, "skipped": 0, "error": "ca_bulk_send_disabled"}
        if not bulk_settings["database_file"].exists():
            return {"sent": 0, "failed": 0, "skipped": 0, "error": "ca_bulk_db_missing"}
        bulk_ca_items = load_ca_bulk_send_candidates(db)
        queue = bulk_ca_items[:send_limit]
        logger.info(
            "CA-only send from ca_bulk.db — %s unsent, up to %s this run (caconnect.icai.org).",
            len(bulk_ca_items),
            send_limit,
        )
        stats = {
            "sent": 0,
            "failed": 0,
            "skipped": 0,
            "queue_size": len(queue),
            "ca_only": True,
            "ca_bulk_unsent": len(bulk_ca_items),
            "sent_ca_connect": 0,
            "sent_by_industry": {},
            "send_limit": send_limit,
        }
    else:
        ensure_industry, min_ensure, _ = get_ensure_industry_settings()
        min_ca_connect = get_min_ensure_ca_connect_per_run()
        industry_reserves = get_reserved_send_by_industry()

        bulk_settings = get_ca_bulk_send_settings()
        bulk_ca_items = load_ca_bulk_send_candidates(db) if bulk_settings["enabled"] else []
        ca_bulk_only = bool(bulk_settings.get("send_only"))
        if bulk_ca_items:
            logger.info(
                "CA send queue: %s unsent contact(s) from ca_bulk.db (caconnect.icai.org).",
                len(bulk_ca_items),
            )
        elif ca_bulk_only:
            logger.warning(
                "CA bulk send enabled but ca_bulk.db has no unsent emails — "
                "no CA portal contacts will be emailed this run (run_ca_bulk_import.bat)."
            )
        elif bulk_settings["enabled"]:
            sync_ca_connect_json_to_db(db)

        logger.info("Sending up to %s email(s) this run (emails_per_run).", send_limit)
        queue = db.pending_send_queue(
            send_limit,
            ensure_industry=ensure_industry,
            min_from_industry=min_ensure,
            min_from_ca_connect=min_ca_connect,
            industry_reserves=industry_reserves,
            extra_ca_items=bulk_ca_items or None,
            ca_bulk_only=ca_bulk_only,
        )
        stats = {
            "sent": 0,
            "failed": 0,
            "skipped": 0,
            "queue_size": len(queue),
            "ensure_industry": ensure_industry or "",
            "ensure_min_slots": min_ensure,
            "ensure_reserved_in_queue": 0,
            "ensure_ca_connect_min": min_ca_connect,
            "ensure_ca_connect_reserved": 0,
            "ensure_ca_connect_from_bulk": 0,
            "ca_bulk_unsent": len(bulk_ca_items),
            "sent_ca_connect": 0,
            "reserved_by_industry": industry_reserves,
            "reserved_in_queue_by_industry": {},
            "sent_by_industry": {},
            "send_limit": send_limit,
        }
        if min_ca_connect > 0:
            ca_reserved = sum(1 for item in queue if is_ca_connect_contact(item))
            ca_from_bulk = sum(
                1 for item in queue if (item.get("contact_source") or "").lower() == "ca_bulk"
            )
            stats["ensure_ca_connect_reserved"] = ca_reserved
            stats["ensure_ca_connect_from_bulk"] = ca_from_bulk
            if ca_reserved >= min_ca_connect:
                logger.info(
                    "Send queue reserves %s/%s slot(s) for CA portal contacts (%s from ca_bulk.db).",
                    ca_reserved,
                    min_ca_connect,
                    ca_from_bulk,
                )
            else:
                logger.warning(
                    "Only %s/%s CA portal contact(s) in queue — "
                    "run ca_bulk import or enrich more profiles on caconnect.icai.org.",
                    ca_reserved,
                    min_ca_connect,
                )
        for iid, min_slots in industry_reserves.items():
            reserved = sum(
                1 for item in queue if (item.get("industry") or "").lower() == iid.lower()
            )
            stats["reserved_in_queue_by_industry"][iid] = reserved
            if reserved >= min_slots:
                logger.info(
                    "Send queue reserves %s/%s slot(s) for industry %s.",
                    reserved,
                    min_slots,
                    iid,
                )
            else:
                logger.warning(
                    "Only %s/%s pending %s contact(s) in queue.",
                    reserved,
                    min_slots,
                    iid,
                )
        if ensure_industry and min_ensure > 0:
            reserved = sum(
                1
                for item in queue
                if (item.get("industry") or "").lower() == ensure_industry.lower()
            )
            stats["ensure_reserved_in_queue"] = reserved
            if reserved >= min_ensure:
                logger.info(
                    "Send queue reserves %s/%s slot(s) for industry %s.",
                    reserved,
                    min_ensure,
                    ensure_industry,
                )
            else:
                logger.warning(
                    "No pending %s emails in queue — sending %s other recipient(s) this run.",
                    ensure_industry,
                    len(queue),
                )

    if ca_only and not queue:
        logger.warning("No unsent CA emails in ca_bulk.db — run run_ca_bulk_import.bat first.")
        return {**stats, "error": "ca_bulk_queue_empty"}

    if dry_run:
        pass
    elif queue:
        notify_stage(sender_cfg, "send_start", count=len(queue))
    else:
        notify_stage(sender_cfg, "send_queue_empty")

    send_plan(total=len(queue))

    for idx, item in enumerate(queue):
        if method == "gmail_smtp":
            profile = profiles[idx % len(profiles)]
            from_email = profile["email"]
            password = passwords[from_email]
        else:
            from_email = ""
            password = ""

        if dry_run:
            industry_id = item.get("industry", "overseas_education_immigration")
            settings = resolve_send_settings(
                industry_id,
                sender_cfg,
                use_nvidia_praise=use_nvidia_praise,
            )
            html, subject = build_email_message(
                industry_id=industry_id,
                company_name=item.get("company_name") or item.get("domain", ""),
                domain=item.get("domain", ""),
                website=item.get("website", ""),
                sender_cfg=sender_cfg,
                use_nvidia_praise=use_nvidia_praise,
            )
            if html:
                send_email_content(recipient=item["email"], subject=subject, html=html)
            transport_label = (
                brevo_transport["sender_email"] if brevo_transport else from_email
            )
            logger.info(
                "DRY-RUN would send to %s (%s) via %s from %s | template: %s | subject: %s",
                item["email"],
                item.get("company_name"),
                method,
                transport_label,
                settings["template_file"],
                subject,
            )
            stats["skipped"] += 1
            send_status(
                n=idx + 1,
                total=len(queue),
                sent=stats["sent"],
                failed=stats["failed"],
                skipped=stats["skipped"],
                recipient=item["email"],
                company=item.get("company_name") or "",
                industry=item.get("industry") or "",
                outcome="dry-run",
            )
            continue

        send_status(
            n=idx + 1,
            total=len(queue),
            sent=stats["sent"],
            failed=stats["failed"],
            skipped=stats["skipped"],
            recipient=item["email"],
            company=item.get("company_name") or "",
            industry=item.get("industry") or "",
            outcome="sending",
        )

        ok = send_one(
            db,
            recipient=item["email"],
            company_id=item.get("company_id"),
            company_name=item.get("company_name") or item.get("domain", "your organisation"),
            domain=item.get("domain", ""),
            website=item.get("website", ""),
            from_email=from_email,
            smtp_password=password,
            sender_cfg=sender_cfg,
            use_nvidia_praise=use_nvidia_praise,
            industry_id=item.get("industry", "overseas_education_immigration"),
            brevo_transport=brevo_transport,
        )
        if ok:
            stats["sent"] += 1
            iid = item.get("industry") or "overseas_education_immigration"
            stats["sent_by_industry"][iid] = stats["sent_by_industry"].get(iid, 0) + 1
            if is_ca_connect_contact(item):
                stats["sent_ca_connect"] += 1
            outcome = "sent"
        elif db.email_already_sent(item["email"]):
            stats["skipped"] += 1
            outcome = "skipped"
        else:
            stats["failed"] += 1
            outcome = "failed"

        send_status(
            n=idx + 1,
            total=len(queue),
            sent=stats["sent"],
            failed=stats["failed"],
            skipped=stats["skipped"],
            recipient=item["email"],
            company=item.get("company_name") or "",
            industry=item.get("industry") or "",
            outcome=outcome,
        )

    if not dry_run:
        notify_stage(
            sender_cfg,
            "send_complete",
            sent=stats["sent"],
            failed=stats["failed"],
        )

    return stats


def run_test_send(
    db: ImmigrationDB,
    *,
    test_to: str,
    use_nvidia_praise: bool = True,
    dry_run: bool = False,
) -> dict:
    """Send one email to test_to using the last scraped company; does not update the database."""
    recipient = (test_to or "").strip()
    if not recipient:
        return {"sent": 0, "failed": 0, "skipped": 0, "error": "test_to_empty"}

    company = db.last_scraped_company()
    if not company:
        return {"sent": 0, "failed": 0, "skipped": 0, "error": "no_scraped_companies"}

    sender_cfg = load_sender_config()
    company_name = company.get("name") or company.get("domain") or "your organisation"
    domain = company.get("domain") or ""
    website = company.get("website") or ""
    industry_id = company.get("industry") or "overseas_education_immigration"
    html, subject = build_email_message(
        industry_id=industry_id,
        company_name=company_name,
        domain=domain,
        website=website,
        sender_cfg=sender_cfg,
        use_nvidia_praise=use_nvidia_praise,
    )
    if not html:
        return {"sent": 0, "failed": 0, "skipped": 0, "error": "template_missing"}

    send_email_content(recipient=recipient, subject=subject, html=html)

    if dry_run:
        try:
            transport = load_brevo_transport(sender_cfg)
            from_label = transport["sender_email"]
        except (FileNotFoundError, ValueError) as exc:
            from_label = sender_cfg.get("email") or f"not configured ({exc})"
        logger.info(
            "DRY-RUN test email -> %s | company: %s (%s) | from: %s | subject: %s",
            recipient,
            company_name,
            domain,
            from_label,
            subject,
        )
        settings = resolve_send_settings(industry_id, sender_cfg, use_nvidia_praise=use_nvidia_praise)
        logger.info("Template: %s", settings["template_file"])
        return {
            "sent": 0,
            "failed": 0,
            "skipped": 1,
            "test_to": recipient,
            "company_name": company_name,
            "domain": domain,
            "subject": subject,
        }

    method = get_send_method()
    if method != "brevo_api":
        return {"sent": 0, "failed": 0, "skipped": 0, "error": "test_send_brevo_only"}

    try:
        transport = load_brevo_transport(sender_cfg)
        ensure_brevo_sender_verified(transport)
    except (FileNotFoundError, ValueError) as exc:
        return {"sent": 0, "failed": 0, "skipped": 0, "error": str(exc)}

    try:
        message_id = send_html_via_brevo(
            recipient=recipient,
            subject=subject,
            html_content=html,
            sender_name=transport["sender_name"],
            sender_email=transport["sender_email"],
            api_key=transport["api_key"],
        )
        logger.info(
            "TEST SENT (Brevo) -> %s | company: %s (%s) | subject: %s | messageId=%s",
            recipient,
            company_name,
            domain,
            subject,
            message_id or "—",
        )
        return {
            "sent": 1,
            "failed": 0,
            "skipped": 0,
            "test_to": recipient,
            "company_name": company_name,
            "domain": domain,
            "subject": subject,
            "message_id": message_id,
        }
    except Exception as exc:
        logger.error("Test send failed for %s: %s", recipient, exc)
        return {
            "sent": 0,
            "failed": 1,
            "skipped": 0,
            "error": str(exc),
            "test_to": recipient,
            "company_name": company_name,
            "domain": domain,
            "subject": subject,
        }


def run_audit_brevo_deliveries(
    db: ImmigrationDB,
    *,
    days: int = 14,
    limit: int = 200,
    dry_run: bool = False,
) -> dict:
    """
    Poll Brevo transactional logs for messageIds stored as status=sent.
    Updates email_sent to delivery_failed when Brevo reports blocked/error events.
    """
    sender_cfg = load_sender_config()
    if get_send_method() != "brevo_api":
        return {"checked": 0, "delivered": 0, "failed": 0, "pending": 0, "error": "brevo_api_only"}

    try:
        transport = load_brevo_transport(sender_cfg)
    except (FileNotFoundError, ValueError) as exc:
        return {"checked": 0, "delivered": 0, "failed": 0, "pending": 0, "error": str(exc)}

    rows = db.list_sent_for_brevo_audit(days=days, limit=limit)
    stats = {"checked": 0, "delivered": 0, "failed": 0, "pending": 0, "unknown": 0, "updated": 0}
    api_key = transport["api_key"]

    for row in rows:
        message_id = (row.get("message_id") or "").strip()
        if not message_id:
            continue
        stats["checked"] += 1
        try:
            result = audit_brevo_message(api_key, message_id)
        except Exception as exc:
            logger.warning("Brevo audit failed for %s: %s", message_id, exc)
            stats["unknown"] += 1
            continue

        brevo_status = result.get("status") or "unknown"
        detail = (result.get("detail") or "").strip()
        recipient = (row.get("email") or result.get("recipient") or "").strip()

        if brevo_status == "failed":
            stats["failed"] += 1
            logger.error(
                "Brevo delivery FAILED -> %s | messageId=%s | %s",
                recipient,
                message_id,
                detail or "blocked/error",
            )
            if not dry_run:
                db.update_email_send_status(
                    recipient,
                    status="delivery_failed",
                    error_message=detail or "brevo_delivery_failed",
                )
                stats["updated"] += 1
        elif brevo_status == "delivered":
            stats["delivered"] += 1
            logger.info("Brevo delivery OK -> %s | messageId=%s", recipient, message_id)
        elif brevo_status == "pending":
            stats["pending"] += 1
            logger.info(
                "Brevo delivery pending -> %s | messageId=%s | %s",
                recipient,
                message_id,
                detail,
            )
        else:
            stats["unknown"] += 1
            logger.warning(
                "Brevo delivery unknown -> %s | messageId=%s | %s",
                recipient,
                message_id,
                detail,
            )

    logger.info("Brevo delivery audit: %s", stats)
    return stats
