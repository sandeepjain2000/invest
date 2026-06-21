"""Project directory layout — config JSON, body/subject templates, data."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "config"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
BODY_TEMPLATES_DIR = PROJECT_ROOT / "templates" / "bodies"
SUBJECT_TEMPLATES_DIR = PROJECT_ROOT / "templates" / "subjects"

INDUSTRIES_FILE = CONFIG_DIR / "industries.json"
SENDER_CONFIG_FILE = CONFIG_DIR / "sender_config.json"
DEFAULT_MAIL_CONFIG = CONFIG_DIR / "mail_config.json"
MAIL_CONFIG_EXAMPLE = CONFIG_DIR / "mail_config.example.json"
CA_BULK_CONFIG = CONFIG_DIR / "ca_bulk_config.json"
CA_BULK_CONFIG_EXAMPLE = CONFIG_DIR / "ca_bulk_config.example.json"
CA_CONNECT_CREDENTIALS = CONFIG_DIR / "ca_connect_credentials.json"
CA_CONNECT_CREDENTIALS_EXAMPLE = CONFIG_DIR / "ca_connect_credentials.example.json"


def resolve_project_path(value: str) -> Path:
    """Resolve a path relative to the project root."""
    path = Path((value or "").strip())
    if not path.parts:
        return PROJECT_ROOT
    return path if path.is_absolute() else PROJECT_ROOT / path


def resolve_config_file(name: str) -> Path:
    """Prefer config/{name}, fall back to project root (legacy)."""
    primary = CONFIG_DIR / name
    if primary.is_file():
        return primary
    legacy = PROJECT_ROOT / name
    if legacy.is_file():
        return legacy
    return primary


def resolve_body_template_path(template_file: str) -> Path:
    """
    Body HTML from templates/bodies/{filename}.
    industries.json and sender_config use filenames only (e.g. partnership.html).
    """
    raw = (template_file or "partnership.html").strip()
    path = Path(raw)
    if path.is_absolute():
        return path
    if len(path.parts) > 1:
        return PROJECT_ROOT / path
    candidate = BODY_TEMPLATES_DIR / raw
    if candidate.is_file():
        return candidate
    legacy = PROJECT_ROOT / raw
    if legacy.is_file():
        return legacy
    return candidate
