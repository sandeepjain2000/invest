"""Brevo transactional email — project-local mail_config.json (API + SMTP)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MAIL_CONFIG = _SCRIPT_DIR / "mail_config.json"


def resolve_mail_config_path(sender_cfg: dict | None = None) -> Path:
    """API key and Brevo sender live in mail_config.json in this project folder."""
    env_path = (os.environ.get("MAIL_CONFIG_FILE") or "").strip()
    if env_path:
        path = Path(env_path)
        return path if path.is_absolute() else _SCRIPT_DIR / path
    if sender_cfg:
        cfg_path = (sender_cfg.get("mail_config_file") or "").strip()
        if cfg_path:
            path = Path(cfg_path)
            return path if path.is_absolute() else _SCRIPT_DIR / path
    return DEFAULT_MAIL_CONFIG


def load_mail_config(sender_cfg: dict | None = None) -> dict:
    path = resolve_mail_config_path(sender_cfg)
    if not path.exists():
        raise FileNotFoundError(
            f"mail_config.json not found: {path}. "
            "Copy mail_config.example.json to mail_config.json and add your Brevo keys."
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
