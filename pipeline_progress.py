"""Clear terminal progress lines for long pipeline runs."""

from __future__ import annotations

import logging

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
) -> None:
    tail = ""
    if recipient:
        label = (company or recipient)[:40]
        tail = f" | {label} <{recipient}>"
    if industry:
        tail += f" [{industry_name(industry)}]"
    log.info(
        "PROGRESS | Send %s/%s | ok=%s fail=%s skip=%s%s",
        n,
        total,
        sent,
        failed,
        skipped,
        tail,
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
