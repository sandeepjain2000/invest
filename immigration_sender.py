"""Send partnership emails to scraped immigration providers."""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from immigration_db import ImmigrationDB
from nvidia_llm import generate_company_praise

logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_FILE = _SCRIPT_DIR / "partnership.html"
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
        "emails_per_run": 2,
    }
    if SENDER_CONFIG_FILE.exists():
        data = json.loads(SENDER_CONFIG_FILE.read_text(encoding="utf-8"))
        defaults.update(data)
    return defaults


def get_emails_per_run() -> int:
    """Max emails to send in one execution (from sender_config.json)."""
    value = load_sender_config().get("emails_per_run", 2)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 2


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
) -> str | None:
    if not TEMPLATE_FILE.exists():
        logger.error("Template not found: %s", TEMPLATE_FILE)
        return None
    html = TEMPLATE_FILE.read_text(encoding="utf-8")
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


def send_one(
    db: ImmigrationDB,
    *,
    recipient: str,
    company_id: int | None,
    company_name: str,
    domain: str,
    website: str,
    from_email: str,
    smtp_password: str,
    sender_cfg: dict,
    use_nvidia_praise: bool = True,
) -> bool:
    if db.email_already_sent(recipient):
        logger.info("Already sent: %s", recipient)
        return False

    check_domain_delay(domain)
    praise = (
        generate_company_praise(company_name, website)
        if use_nvidia_praise
        else (
            f"I was impressed by {company_name}'s dedication to supporting clients "
            "through immigration and visa processes."
        )
    )
    html = read_template(company_name, praise, sender_cfg)
    if not html:
        return False

    subject = sender_cfg.get("email_subject", "Exploring a potential partnership opportunity")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(from_email, smtp_password)
            server.send_message(msg)
        logger.info("SENT -> %s (%s)", recipient, company_name)
        db.record_email_sent(
            email=recipient,
            company_id=company_id,
            company_name=company_name,
            from_profile=from_email,
            subject=subject,
            status="sent",
        )
        time.sleep(EMAIL_SEND_DELAY)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP auth failed for %s", from_email)
        db.record_email_sent(
            email=recipient,
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
        db.record_email_sent(
            email=recipient,
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
    profiles, passwords = load_smtp_profiles(smtp_config)
    if not profiles:
        return {"sent": 0, "failed": 0, "skipped": 0, "error": "no_smtp_profiles"}

    sender_cfg = load_sender_config()
    send_limit = limit if limit is not None else get_emails_per_run()
    logger.info("Sending up to %s email(s) this run (emails_per_run).", send_limit)
    queue = db.pending_send_queue(limit=send_limit)
    stats = {"sent": 0, "failed": 0, "skipped": 0}

    for idx, item in enumerate(queue):
        profile = profiles[idx % len(profiles)]
        from_email = profile["email"]
        password = passwords[from_email]
        if dry_run:
            praise = (
                generate_company_praise(
                    item.get("company_name") or item.get("domain", ""),
                    item.get("website", ""),
                )
                if use_nvidia_praise
                else "..."
            )
            logger.info(
                "DRY-RUN would send to %s (%s) from %s | praise: %s",
                item["email"],
                item.get("company_name"),
                from_email,
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
        )
        if ok:
            stats["sent"] += 1
        else:
            if db.email_already_sent(item["email"]):
                stats["skipped"] += 1
            else:
                stats["failed"] += 1

    return stats
