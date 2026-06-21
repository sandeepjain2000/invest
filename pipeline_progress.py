"""Clear terminal progress lines for long pipeline runs."""

from __future__ import annotations

import logging
import re
import sys
from html import unescape

from industries import industry_name

log = logging.getLogger("pipeline_progress")


def step(message: str) -> None:
    log.info("")
    log.info("=" * 62)
    log.info("PROGRESS | %s", message)
    log.info("=" * 62)


def scrape_plan(*, max_companies: int, email_target: int, max_queries: int, industry: str = "") -> None:
    scope = industry_name(industry) if industry else "all industries"
    log.info(
        "PROGRESS | Scrape plan — %s | up to %s sites | goal %s with email | max %s queries",
        scope,
        max_companies,
        email_target,
        max_queries,
    )


def scrape_status(
    *,
    query_n: int,
    query_max: int,
    companies: int,
    company_max: int,
    with_email: int,
    email_target: int,
    industry: str = "",
) -> None:
    parts = [
        f"query {query_n}/{query_max}",
        f"sites {companies}/{company_max}",
        f"with email {with_email}/{email_target}",
    ]
    if industry:
        parts.append(industry_name(industry))
    log.info("PROGRESS | Scrape — %s", " | ".join(parts))


def scrape_company(
    *,
    companies: int,
    company_max: int,
    with_email: int,
    email_target: int,
    name: str,
    found_email: bool,
) -> None:
    flag = "email found" if found_email else "no email"
    log.info(
        "PROGRESS | Site %s/%s | with email %s/%s | %s — %s",
        companies,
        company_max,
        with_email,
        email_target,
        name[:60],
        flag,
    )


def ca_connect_status(*, n: int, total: int, name: str = "", email: str = "") -> None:
    log.info(
        "PROGRESS | CA Connect %s/%s | %s | %s",
        n,
        total,
        (name or "—")[:50],
        email or "no email",
    )


def send_plan(*, total: int) -> None:
    log.info("PROGRESS | Send plan — %s recipient(s) in queue", total)


def _html_to_plain(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?i)</li>", "\n", text)
    text = re.sub(r"(?i)</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _preview_lines(*, recipient: str, subject: str, plain: str) -> list[str]:
    lines = [
        "-" * 62,
        f"Email content — To: {recipient}",
        f"Subject: {subject}",
        "Body:",
    ]
    if plain:
        lines.extend(f"  {line}" for line in plain.splitlines())
    else:
        lines.append("  (empty body)")
    lines.extend(["-" * 62, "", ""])
    return lines


def _write_terminal_preview(lines: list[str]) -> None:
    """One atomic stdout write so logging on the same stream cannot interleave or drop lines."""
    block = "\n".join(lines)
    sys.stdout.write(block)
    if not block.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def _write_file_preview(lines: list[str]) -> None:
    """Persist the full preview in the log file (console stays timestamp-free)."""
    for handler in log.handlers:
        if not isinstance(handler, logging.FileHandler):
            continue
        for line in lines:
            if line.startswith("PROGRESS |") or line == "":
                msg = line
            elif set(line) == {"-"}:
                msg = line
            elif line.startswith("  "):
                msg = f"PROGRESS | {line}"
            elif line.startswith("Email content") or line.startswith("Subject:") or line == "Body:":
                msg = f"PROGRESS | {line}"
            else:
                msg = line
            record = log.makeRecord(
                log.name,
                logging.INFO,
                "",
                0,
                msg,
                (),
                None,
            )
            handler.handle(record)


def send_email_content(*, recipient: str, subject: str, html: str) -> None:
    """Print subject and plain-text body preview to the terminal (no log timestamps on body)."""
    plain = _html_to_plain(html)
    lines = _preview_lines(recipient=recipient, subject=subject, plain=plain)
    _write_terminal_preview(lines)
    _write_file_preview(lines)
    log.info("PROGRESS | Email preview — To: %s | Subject: %s", recipient, subject)


def send_status(
    *,
    n: int,
    total: int,
    sent: int,
    failed: int,
    skipped: int,
    recipient: str = "",
    company: str = "",
    industry: str = "",
    outcome: str = "",
) -> None:
    email = recipient or "—"
    name = (company or "—")[:45]
    ind = f" [{industry_name(industry)}]" if industry else ""
    tag = outcome.upper() if outcome else "—"
    log.info(
        "PROGRESS | Send %s/%s | %s | %s | %s%s | ok=%s fail=%s skip=%s",
        n,
        total,
        tag,
        email,
        name,
        ind,
        sent,
        failed,
        skipped,
    )


def done(message: str) -> None:
    log.info("PROGRESS | Done — %s", message)


def google_block_warning(*, reason: str, query: str = "", headless: bool = False) -> None:
    mode = "headless" if headless else "visible browser"
    log.warning("")
    log.warning("!" * 62)
    log.warning("PROGRESS | GOOGLE BLOCK / CHALLENGE DETECTED (%s)", mode)
    if query:
        log.warning("PROGRESS | Query: %s", query)
    log.warning("PROGRESS | Reason: %s", reason)
    log.warning(
        "PROGRESS | Fix: run without --headless, complete consent/CAPTCHA in the browser, "
        "or wait and retry later"
    )
    log.warning("!" * 62)
    log.warning("")
