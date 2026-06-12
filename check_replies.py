"""Detect human replies to partnership emails and forward to sandeepjain200019@gmail.com."""

from __future__ import annotations

import email
import imaplib
import logging
import re
import smtplib
import ssl
import time
from datetime import datetime, timedelta
from email.mime.message import MIMEMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import getaddresses, parseaddr
from pathlib import Path

from immigration_db import ImmigrationDB
from immigration_sender import load_sender_config, load_smtp_profiles
from nvidia_llm import classify_partnership_reply

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

BOUNCE_SENDERS = (
    "mailer-daemon",
    "postmaster",
    "mail-daemon",
    "noreply@",
    "no-reply@",
    "donotreply@",
)

AUTOREPLY_SUBJECTS = (
    "out of office",
    "automatic reply",
    "auto-reply",
    "autoreply",
    "away from",
    "on vacation",
    "delivery status notification",
    "undeliverable",
    "delivery failure",
)

FORWARD_DELAY_SECS = 5


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def normalize_subject(subject: str) -> str:
    text = (subject or "").strip()
    while True:
        lowered = text.lower()
        if lowered.startswith("re:"):
            text = text[3:].strip()
        elif lowered.startswith("fwd:") or lowered.startswith("fw:"):
            text = text[4:].strip()
        else:
            break
    return text.strip().lower()


def extract_from_address(msg: email.message.Message) -> str:
    for header in ("Reply-To", "From"):
        addrs = getaddresses([msg.get(header, "") or ""])
        for _, addr in addrs:
            if addr:
                return addr.strip().lower()
    return ""


def extract_body(msg: email.message.Message) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_type() in ("text/plain", "text/html"):
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        parts.append(payload.decode("utf-8", errors="replace"))
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                parts.append(payload.decode("utf-8", errors="replace"))
        except Exception:
            pass
    return " ".join(parts)[:8000]


def _normalize_msg_id(value: str) -> str:
    v = (value or "").strip().lower()
    if v.startswith("<") and v.endswith(">"):
        v = v[1:-1]
    return v.strip()


def _parse_references(value: str) -> list[str]:
    if not value:
        return []
    return [_normalize_msg_id(m) for m in re.findall(r"<[^>]+>", value)]


def thread_matches_sent(msg: email.message.Message, sent_ids: set[str]) -> bool:
    if not sent_ids:
        return False
    raw_in_reply = (msg.get("In-Reply-To", "") or "").strip()
    for token in _parse_references(raw_in_reply) or [_normalize_msg_id(raw_in_reply)]:
        if token and token in sent_ids:
            return True
    for ref in _parse_references(msg.get("References", "") or ""):
        if ref in sent_ids:
            return True
    return False


def has_reply_headers(msg: email.message.Message) -> bool:
    return bool((msg.get("In-Reply-To") or "").strip() or (msg.get("References") or "").strip())


def subject_base_from_full(subject: str) -> str:
    """'Exploring ... with croyez.in' -> 'exploring ...' (normalized)."""
    norm = normalize_subject(subject)
    if " with " in norm:
        return norm.rsplit(" with ", 1)[0].strip()
    return norm


def subject_matches_campaign(subject: str, campaign_bases: set[str]) -> bool:
    """
    Match base subject or per-company variant: '{base} with {domain}'.
    """
    norm = normalize_subject(subject)
    if not norm or not campaign_bases:
        return False
    if norm in campaign_bases:
        return True
    for base in campaign_bases:
        if not base:
            continue
        if norm == base:
            return True
        if norm.startswith(f"{base} with "):
            return True
    return False


def is_system_or_autoreply(msg: email.message.Message) -> bool:
    _, sender = parseaddr(msg.get("From", "") or "")
    sender = sender.lower()
    subject = (msg.get("Subject", "") or "").lower()

    if "[fwd" in subject and "human reply" in subject:
        return True

    auto_hdr = (msg.get("Auto-Submitted", "") or "").lower()
    if auto_hdr and auto_hdr != "no":
        return True

    for pat in BOUNCE_SENDERS:
        if pat in sender:
            return True
    for pat in AUTOREPLY_SUBJECTS:
        if pat in subject:
            return True
    return False


def build_campaign_subject_bases(db: ImmigrationDB, sender_cfg: dict) -> set[str]:
    """Base subject lines only (without ' with domain' suffix)."""
    bases: set[str] = set()
    for raw in sender_cfg.get("campaign_subjects") or []:
        if raw:
            base = normalize_subject(str(raw))
            bases.add(subject_base_from_full(base) if " with " in base else base)
            db.ensure_campaign_subject(str(raw).strip())
    current = (sender_cfg.get("email_subject") or "").strip()
    if current:
        bases.add(normalize_subject(current))
        db.ensure_campaign_subject(current)
    for raw in db.list_campaign_subjects():
        b = normalize_subject(raw)
        bases.add(subject_base_from_full(b) if " with " in b else b)
    for raw in db.distinct_sent_subjects():
        bases.add(subject_base_from_full(raw))
    return {b for b in bases if b}


def classify_reply(
    msg: email.message.Message,
    *,
    sent_recipients: set[str],
    sent_message_ids: set[str],
    campaign_bases: set[str],
) -> tuple[str, str]:
    """
    Returns (decision, reason).
    decision: forward | nvidia | skip
    """
    if is_system_or_autoreply(msg):
        return "skip", "system_or_autoreply"

    from_addr = extract_from_address(msg)
    subject = msg.get("Subject", "") or ""
    thread_match = thread_matches_sent(msg, sent_message_ids)
    from_sent = from_addr in sent_recipients
    reply_hdrs = has_reply_headers(msg)
    subj_match = subject_matches_campaign(subject, campaign_bases)

    if thread_match:
        return "forward", "thread_message_id_match"
    if from_sent and reply_hdrs:
        return "forward", "sent_recipient_with_reply_headers"
    if from_sent and subj_match and reply_hdrs:
        return "forward", "sent_recipient_subject_and_reply_headers"

    if from_sent and (subj_match or reply_hdrs):
        return "nvidia", "borderline_sent_recipient_partial_signals"
    if from_sent:
        return "nvidia", "borderline_sent_recipient_only"

    return "skip", "no_correlation"


def forward_reply(
    *,
    smtp_email: str,
    smtp_password: str,
    forward_to: str,
    original_msg: email.message.Message,
    from_address: str,
) -> bool:
    try:
        fwd = MIMEMultipart("mixed")
        fwd["From"] = smtp_email
        fwd["To"] = forward_to
        fwd["Subject"] = f"[FWD – Partnership Reply] {original_msg.get('Subject', '')}"
        fwd["Auto-Submitted"] = "auto-replied"

        note = MIMEText(
            f"<p><b>Forwarded partnership reply from:</b> {from_address}</p><hr>",
            "html",
        )
        fwd.attach(note)
        fwd.attach(MIMEMessage(original_msg))

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=_ssl_ctx()) as server:
            server.login(smtp_email, smtp_password)
            server.send_message(fwd)
        return True
    except Exception as exc:
        logger.error("Forward failed: %s", exc)
        return False


def _imap_connect(gmail_address: str, app_password: str) -> imaplib.IMAP4_SSL:
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(gmail_address, app_password.replace(" ", ""))
    mail.select("INBOX")
    return mail


def _search_recent_uids(mail: imaplib.IMAP4_SSL, lookback_days: int) -> list[bytes]:
    since = (datetime.now() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
    typ, data = mail.uid("SEARCH", None, f"(SINCE {since})")
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _incoming_message_id(msg: email.message.Message) -> str:
    mid = (msg.get("Message-ID") or "").strip()
    if mid:
        return mid
    return f"no-mid-{hash(extract_from_address(msg) + (msg.get('Subject') or ''))}"


def check_account_replies(
    db: ImmigrationDB,
    *,
    gmail_address: str,
    app_password: str,
    forward_to: str,
    lookback_days: int,
    sent_recipients: set[str],
    sent_message_ids: set[str],
    campaign_bases: set[str],
    use_nvidia: bool = True,
) -> dict:
    stats = {"scanned": 0, "forwarded": 0, "skipped": 0, "nvidia": 0, "errors": 0}
    try:
        mail = _imap_connect(gmail_address, app_password)
    except Exception as exc:
        logger.error("IMAP login failed for %s: %s", gmail_address, exc)
        stats["errors"] += 1
        return stats

    uids = _search_recent_uids(mail, lookback_days)
    logger.info("  %s: %s INBOX message(s) in last %s days", gmail_address, len(uids), lookback_days)

    for uid in uids:
        try:
            typ, data = mail.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                continue
            raw = data[0][1]
            if not isinstance(raw, bytes):
                continue
            msg = email.message_from_bytes(raw)
            stats["scanned"] += 1

            incoming_id = _incoming_message_id(msg)
            if db.reply_already_forwarded(gmail_address, incoming_id):
                stats["skipped"] += 1
                continue

            decision, reason = classify_reply(
                msg,
                sent_recipients=sent_recipients,
                sent_message_ids=sent_message_ids,
                campaign_bases=campaign_bases,
            )

            from_addr = extract_from_address(msg)
            subject = msg.get("Subject", "") or ""
            thread_match = thread_matches_sent(msg, sent_message_ids)
            reply_hdrs = has_reply_headers(msg)
            subj_match = subject_matches_campaign(subject, campaign_bases)
            from_sent = from_addr in sent_recipients

            should_forward = False
            detection_method = reason

            if decision == "forward":
                should_forward = True
            elif decision == "nvidia" and use_nvidia:
                stats["nvidia"] += 1
                logger.info("  NVIDIA borderline: %s | %s", from_addr, subject[:60])
                clf = classify_partnership_reply(
                    subject=subject,
                    from_addr=from_addr,
                    body=extract_body(msg),
                    has_reply_headers=reply_hdrs,
                    from_sent_recipient=from_sent,
                    subject_matches_campaign=subj_match,
                    thread_message_id_match=thread_match,
                )
                detection_method = f"nvidia:{clf.get('reason', '')}"
                if clf.get("is_auto_response"):
                    should_forward = False
                    reason = "nvidia_auto_response"
                else:
                    should_forward = bool(clf.get("should_forward"))
                logger.info(
                    "    NVIDIA -> forward=%s reason=%s",
                    should_forward,
                    clf.get("reason", ""),
                )
            else:
                stats["skipped"] += 1
                logger.debug("  Skip (%s): %s", reason, subject[:50])
                continue

            if not should_forward:
                stats["skipped"] += 1
                continue

            ok = forward_reply(
                smtp_email=gmail_address,
                smtp_password=app_password,
                forward_to=forward_to,
                original_msg=msg,
                from_address=from_addr,
            )
            if ok:
                db.record_reply_forward(
                    gmail_account=gmail_address,
                    incoming_msg_id=incoming_id,
                    from_address=from_addr,
                    subject=subject,
                    detection_method=detection_method,
                    llm_reason=detection_method if detection_method.startswith("nvidia:") else "",
                )
                stats["forwarded"] += 1
                logger.info("  FORWARDED -> %s | from %s | %s", forward_to, from_addr, subject[:60])
                if stats["forwarded"] < len(uids):
                    time.sleep(FORWARD_DELAY_SECS)
            else:
                stats["errors"] += 1

        except Exception as exc:
            logger.warning("  Error processing UID %s: %s", uid, exc)
            stats["errors"] += 1

    try:
        mail.logout()
    except Exception:
        pass
    return stats


def run_check_replies(
    db: ImmigrationDB,
    *,
    smtp_config: Path | None = None,
    use_nvidia: bool = True,
) -> dict:
    sender_cfg = load_sender_config()
    forward_to = (sender_cfg.get("forward_to") or "sandeepjain200019@gmail.com").strip()
    lookback = int(sender_cfg.get("check_replies_lookback_days") or 30)

    campaign_bases = build_campaign_subject_bases(db, sender_cfg)
    logger.info(
        "Reply check: forward_to=%s lookback=%sd campaign_subject_bases=%s",
        forward_to,
        lookback,
        len(campaign_bases),
    )

    sent_recipients = db.sent_recipient_emails()
    sent_message_ids = {_normalize_msg_id(mid) for mid in db.sent_message_ids()}
    logger.info(
        "Known sent recipients: %s | stored Message-IDs: %s",
        len(sent_recipients),
        len(sent_message_ids),
    )

    profiles, passwords = load_smtp_profiles(smtp_config)
    if not profiles:
        return {"error": "no_smtp_profiles", "forwarded": 0}

    totals = {"scanned": 0, "forwarded": 0, "skipped": 0, "nvidia": 0, "errors": 0}
    for profile in profiles:
        gmail = profile["email"]
        password = passwords[gmail]
        logger.info("Checking inbox: %s", gmail)
        acct_stats = check_account_replies(
            db,
            gmail_address=gmail,
            app_password=password,
            forward_to=forward_to,
            lookback_days=lookback,
            sent_recipients=sent_recipients,
            sent_message_ids=sent_message_ids,
            campaign_bases=campaign_bases,
            use_nvidia=use_nvidia,
        )
        for key in totals:
            totals[key] += acct_stats.get(key, 0)

    logger.info("Reply check complete: %s", totals)
    return totals
