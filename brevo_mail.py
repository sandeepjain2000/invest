"""Brevo transactional email — project-local mail_config.json (API + SMTP)."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from project_paths import (
    CONFIG_DIR,
    DEFAULT_MAIL_CONFIG,
    MAIL_CONFIG_EXAMPLE,
    PROJECT_ROOT,
    resolve_config_file,
    resolve_project_path,
)

logger = logging.getLogger(__name__)

_SCRIPT_DIR = PROJECT_ROOT


def resolve_mail_config_path(sender_cfg: dict | None = None) -> Path:
    """API key and Brevo sender live in config/mail_config.json."""
    env_path = (os.environ.get("MAIL_CONFIG_FILE") or "").strip()
    if env_path:
        return resolve_project_path(env_path)
    if sender_cfg:
        cfg_path = (sender_cfg.get("mail_config_file") or "").strip()
        if cfg_path:
            return resolve_project_path(cfg_path)
    return resolve_config_file("mail_config.json")


def load_mail_config(sender_cfg: dict | None = None) -> dict:
    path = resolve_mail_config_path(sender_cfg)
    if not path.exists():
        raise FileNotFoundError(
            f"mail_config.json not found: {path}. "
            f"Copy {MAIL_CONFIG_EXAMPLE} to {DEFAULT_MAIL_CONFIG} and add your Brevo keys."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_brevo_transport(sender_cfg: dict) -> dict:
    """
    Brevo API key + verified sender from project mail_config.json.
    Display name falls back to sender_name in sender_config.json.
    """
    mail_cfg = load_mail_config(sender_cfg)
    brevo = dict(mail_cfg.get("brevo") or {})
    api_key = (os.environ.get("BREVO_API_KEY") or brevo.get("api_key") or "").strip()
    if not api_key or api_key.startswith("YOUR_"):
        raise ValueError(
            "Set brevo.api_key in mail_config.json (or BREVO_API_KEY env). "
            f"Config: {resolve_mail_config_path(sender_cfg)}"
        )

    sender = dict(brevo.get("sender") or {})
    sender_email = (sender.get("email") or "").strip()
    if not sender_email:
        raise ValueError("Set brevo.sender.email in mail_config.json")

    sender_name = (sender.get("name") or sender_cfg.get("sender_name") or "").strip() or sender_email
    return {
        "api_key": api_key,
        "sender_name": sender_name,
        "sender_email": sender_email,
    }


def _brevo_get(api_key: str, path: str, *, params: dict | None = None) -> dict:
    query = urllib.parse.urlencode(params or {}, safe="@<>")
    url = f"https://api.brevo.com{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(
        url,
        headers={"api-key": api_key, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Brevo API error {error.code} for {path}: {body}") from error


def list_brevo_verified_senders(api_key: str) -> list[dict]:
    """Return verified senders from GET /v3/senders (active only)."""
    data = _brevo_get(api_key, "/v3/senders")
    senders = data.get("senders") or []
    verified: list[dict] = []
    for item in senders:
        email = (item.get("email") or "").strip().lower()
        if not email:
            continue
        if item.get("active") is False:
            continue
        verified.append({"email": email, "name": (item.get("name") or "").strip()})
    return verified


def ensure_brevo_sender_verified(transport: dict) -> None:
    """
    Fail fast before send if brevo.sender.email is not verified on this API key's account.
    Prevents async 'invalid sender' rejections that the API accepts but Brevo blocks later.
    """
    api_key = transport["api_key"]
    sender_email = transport["sender_email"].strip().lower()
    verified = list_brevo_verified_senders(api_key)
    verified_emails = {item["email"] for item in verified}
    if sender_email in verified_emails:
        return
    sample = ", ".join(sorted(verified_emails)[:5]) or "(none)"
    raise ValueError(
        f"brevo.sender.email '{transport['sender_email']}' is not verified on this Brevo account. "
        f"Verified senders: {sample}. "
        "Add the sender in Brevo → Senders & IP → Senders, or change mail_config.json."
    )


_BREVO_FAILURE_EVENTS = frozenset(
    {"blocked", "error", "hardbounce", "hard_bounce", "invalid", "invalid_email", "rejected"}
)


def lookup_transac_email(api_key: str, message_id: str) -> dict | None:
    """Find one transactional email log row by Brevo messageId."""
    message_id = (message_id or "").strip()
    if not message_id:
        return None
    data = _brevo_get(
        api_key,
        "/v3/smtp/emails",
        params={"messageId": message_id, "sort": "desc", "limit": 1},
    )
    rows = data.get("transactionalEmails") or []
    return rows[0] if rows else None


def fetch_transac_email_events(api_key: str, uuid: str) -> list[dict]:
    data = _brevo_get(api_key, f"/v3/smtp/emails/{urllib.parse.quote(uuid, safe='')}")
    return list(data.get("events") or [])


def classify_brevo_delivery(events: list[dict]) -> tuple[str, str]:
    """
    Return (status, detail) where status is delivered | failed | pending.
    Brevo may accept an API send (messageId returned) but reject delivery asynchronously.
    """
    if not events:
        return "pending", "no events yet"

    names: list[str] = []
    details: list[str] = []
    for event in events:
        name = (event.get("name") or event.get("event") or "").strip().lower()
        if name:
            names.append(name)
        for key in ("reason", "message", "description"):
            text = (event.get(key) or "").strip()
            if text:
                details.append(text)

    if any(name in _BREVO_FAILURE_EVENTS for name in names):
        return "failed", "; ".join(details[:2]) or ", ".join(names)
    if any(name in {"delivered", "opened", "clicks", "click"} for name in names):
        return "delivered", ", ".join(names)
    return "pending", ", ".join(names) or "awaiting delivery events"


def audit_brevo_message(api_key: str, message_id: str) -> dict:
    """Check Brevo delivery log for one messageId saved in email_sent."""
    row = lookup_transac_email(api_key, message_id)
    if not row:
        return {"message_id": message_id, "status": "unknown", "detail": "not found in Brevo logs"}
    uuid = (row.get("uuid") or "").strip()
    if not uuid:
        return {"message_id": message_id, "status": "unknown", "detail": "missing uuid in Brevo list row"}
    events = fetch_transac_email_events(api_key, uuid)
    status, detail = classify_brevo_delivery(events)
    return {
        "message_id": message_id,
        "uuid": uuid,
        "recipient": (row.get("email") or "").strip(),
        "from": (row.get("from") or "").strip(),
        "status": status,
        "detail": detail,
        "events": events,
    }


def send_html_via_brevo(
    *,
    recipient: str,
    subject: str,
    html_content: str,
    sender_name: str,
    sender_email: str,
    api_key: str,
) -> str:
    """
    Send one transactional email with local HTML (no Brevo dashboard template).
    Returns Brevo messageId string for reply-thread tracking.
    """
    import sib_api_v3_sdk
    from sib_api_v3_sdk.rest import ApiException

    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = api_key
    api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))

    payload = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": recipient}],
        sender={"name": sender_name, "email": sender_email},
        subject=subject,
        html_content=html_content,
    )
    try:
        result = api.send_transac_email(payload)
    except ApiException as error:
        raise RuntimeError(f"Brevo API send failed for {recipient}: {error}") from error

    message_id = getattr(result, "message_id", None) or getattr(result, "messageId", None)
    if message_id is None and isinstance(result, dict):
        message_id = result.get("messageId") or result.get("message_id")
    return str(message_id or "").strip()


def list_brevo_templates(sender_cfg: dict | None = None) -> None:
    """Print transactional template IDs from Brevo (for reference; partnership pipeline uses local HTML)."""
    import urllib.error
    import urllib.request

    brevo = load_brevo_transport(sender_cfg or {})
    api_key = brevo["api_key"]
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/templates?limit=100&sort=desc",
        headers={"api-key": api_key, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Brevo API error {error.code}: {body}") from error

    templates = data.get("templates") or []
    print(f"Brevo transactional templates ({data.get('count', len(templates))} total):")
    for t in templates:
        print(f"  #{t.get('id')} — {t.get('name', '')}")
