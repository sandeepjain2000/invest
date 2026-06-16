"""Stage and completion voice prompts for the partnership pipeline."""

from __future__ import annotations

from browser_utils import pipeline_complete_beep, pipeline_complete_voice, scrape_complete_beep

DEFAULT_COMPLETE_VOICE_MESSAGE = "Partnership pipeline run complete."
DEFAULT_STAGE_MESSAGES = {
    "run_start": "Starting partnership pipeline run.",
    "scrape_start": "Starting scrape phase.",
    "ca_connect_start": "Starting CA Connect scrape.",
    "ca_connect_complete": "CA Connect complete. {imported} emails imported.",
    "google_scrape_start": "Starting Google company scrape.",
    "scrape_complete": "Scrape phase complete. {with_email} companies with email.",
    "reply_check_start": "Checking inboxes for replies.",
    "send_start": "Starting send phase. {count} emails queued.",
    "send_queue_empty": "No emails in send queue.",
    "send_complete": "Send phase complete. {sent} sent, {failed} failed.",
}


def voice_enabled(config: dict) -> bool:
    return bool(config.get("pipeline_complete_voice", True))


def stage_voice_enabled(config: dict) -> bool:
    if "pipeline_stage_voice" in config:
        return bool(config.get("pipeline_stage_voice"))
    return voice_enabled(config)


def stage_message(config: dict, key: str, **kwargs: object) -> str:
    overrides = config.get("pipeline_stage_messages") or {}
    template = str(overrides.get(key) or DEFAULT_STAGE_MESSAGES.get(key) or "").strip()
    if not template:
        return ""
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError):
        return template


def notify_stage(
    config: dict,
    key: str,
    *,
    beep: bool = True,
    dry_run: bool = False,
    **kwargs: object,
) -> None:
    """Short beep + optional spoken message for an intermediate pipeline stage."""
    if dry_run or not stage_voice_enabled(config):
        return
    message = stage_message(config, key, **kwargs)
    if beep:
        scrape_complete_beep()
    if message:
        pipeline_complete_voice(message)


def notify_run_complete(config: dict | None = None, *, dry_run: bool = False) -> None:
    """Long beep + optional spoken message when a pipeline command finishes."""
    if dry_run:
        return
    cfg = config
    if cfg is None:
        from immigration_sender import load_sender_config

        cfg = load_sender_config()
    pipeline_complete_beep()
    if voice_enabled(cfg):
        message = (
            cfg.get("pipeline_complete_voice_message") or DEFAULT_COMPLETE_VOICE_MESSAGE
        ).strip()
        if message:
            pipeline_complete_voice(message)
