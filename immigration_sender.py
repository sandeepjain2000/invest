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

from brevo_mail import load_brevo_transport, resolve_mail_config_path, send_html_via_brevo
from immigration_db import ImmigrationDB, clean_domain
from nvidia_llm import generate_company_praise

logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE_FILE = _SCRIPT_DIR / "partnership.html"
SENDER_CONFIG_FILE = _SCRIPT_DIR / "sender_config.json"
SMTP_CONFIG_FILE = Path(
    os.environ.get(
        "EMAIL_CONFIG_FILE",
        r"C:\Users\sandeep\Downloads\Claudes\EmailJson\email_config1001.json",
    )
)

EMAIL_SEND_DELAY = 5
SAME_DOMAIN_DELAY = 20

_domain_last_sent: dict[str, float] = {}


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
        "emails_per_run": 10,
        "max_companies_per_run": 50,
        "max_queries_per_run": 20,
        "send_method": "brevo_api",
        "template_file": "partnership.html",
        "mail_config_file": "mail_config.json",
    }
    if SENDER_CONFIG_FILE.exists():
        data = json.loads(SENDER_CONFIG_FILE.read_text(encoding="utf-8"))
        defaults.update(data)
    return defaults


def get_template_path(sender: dict | None = None) -> Path:
    sender = sender or load_sender_config()
    raw = (sender.get("template_file") or "partnership.html").strip()
    path = Path(raw)
    return path if path.is_absolute() else _SCRIPT_DIR / path


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


def format_email_subject(base_subject: str, domain: str) -> str:
    """
    Unique subject per company: '{base} with {domain}'.
    Example: Exploring a potential partnership opportunity with croyez.in
    """
    base = (base_subject or "").strip()
    dom = clean_domain(domain)
    if not base:
        return dom or "Partnership opportunity"
    if not dom:
        return base
    return f"{base} with {dom}"


def get_max_queries_per_run() -> int:
    """Max Google search queries per scrape run (safety cap)."""
    value = load_sender_config().get("max_queries_per_run", 20)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 20


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


def _render_signature_links(sender: dict) -> str:
    links = sender.get("signature_links") or []
    if not links and sender.get("website"):
        links = [{"label": sender.get("company_name", "Website"), "url": sender["website"]}]
    lines: list[str] = []
    for item in links:
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
        .replace("{{SignatureLinks}}", _render_signature_links(sender))
    )


def check_domain_delay(domain: str) -> None:
    if not domain:
        return
    last = _domain_last_sent.get(domain)
    if last is not None:
        elapsed = time.time() - last
        if elapsed < SAME_DOMAIN_DELAY:
            wait = SAME_DOMAIN_DELAY - elapsed
            logger.info("Domain cooldown: waiting %ss for %s", int(wait), domain)
            time.sleep(wait)
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
    praise = _build_praise(
        company_name,
        website,
        use_nvidia_praise=use_nvidia_praise,
        industry_id=industry_id,
    )
    html = read_template(company_name, praise, sender_cfg)
    if not html:
        return False

    base_subject = sender_cfg.get(
        "email_subject", "Exploring a potential partnership opportunity"
    )
    subject = format_email_subject(base_subject, domain)
    db.ensure_campaign_subject(base_subject)

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
                "SENT (Brevo) -> %s (%s) | %s | messageId=%s",
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
            time.sleep(EMAIL_SEND_DELAY)
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
        time.sleep(EMAIL_SEND_DELAY)
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


def run_send(
    db: ImmigrationDB,
    *,
    limit: int | None = None,
    smtp_config: Path | None = None,
    use_nvidia_praise: bool = True,
    dry_run: bool = False,
) -> dict:
    sender_cfg = load_sender_config()
    method = get_send_method()
    brevo_transport = None
    profiles: list[dict] = []
    passwords: dict[str, str] = {}

    if method == "brevo_api":
        try:
            brevo_transport = load_brevo_transport(sender_cfg)
            logger.info(
                "Send transport: Brevo API (config: %s, template: %s)",
                resolve_mail_config_path(sender_cfg),
                get_template_path(sender_cfg),
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

    send_limit = limit if limit is not None else get_emails_per_run()
    logger.info("Sending up to %s email(s) this run (emails_per_run).", send_limit)
    queue = db.pending_send_queue(limit=send_limit)
    stats = {"sent": 0, "failed": 0, "skipped": 0}

    for idx, item in enumerate(queue):
        if method == "gmail_smtp":
            profile = profiles[idx % len(profiles)]
            from_email = profile["email"]
            password = passwords[from_email]
        else:
            from_email = ""
            password = ""

        if dry_run:
            praise = _build_praise(
                item.get("company_name") or item.get("domain", ""),
                item.get("website", ""),
                use_nvidia_praise=use_nvidia_praise,
                industry_id=item.get("industry", "overseas_education_immigration"),
            )
            transport_label = (
                brevo_transport["sender_email"] if brevo_transport else from_email
            )
            logger.info(
                "DRY-RUN would send to %s (%s) via %s from %s | praise: %s",
                item["email"],
                item.get("company_name"),
                method,
                transport_label,
                praise[:80],
            )
            stats["skipped"] += 1
            continue

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
        elif db.email_already_sent(item["email"]):
            stats["skipped"] += 1
        else:
            stats["failed"] += 1

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

    praise = _build_praise(
        company_name,
        website,
        use_nvidia_praise=use_nvidia_praise,
        industry_id=industry_id,
    )
    html = read_template(company_name, praise, sender_cfg)
    if not html:
        return {"sent": 0, "failed": 0, "skipped": 0, "error": "template_missing"}

    base_subject = sender_cfg.get(
        "email_subject", "Exploring a potential partnership opportunity"
    )
    subject = format_email_subject(base_subject, domain)

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
        logger.info("Praise preview: %s", praise[:120])
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
