"""SQLite persistence for immigration scrape + email pipeline."""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DB_PATH = Path(__file__).resolve().parent / "data" / "db" / "immigration.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_domain(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    host = urlparse(raw).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


class ImmigrationDB:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS search_queries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                query_text  TEXT NOT NULL,
                industry    TEXT NOT NULL DEFAULT 'overseas_education_immigration',
                status      TEXT NOT NULL DEFAULT 'pending',
                source      TEXT,
                created_at  TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE (query_text, industry)
            );

            CREATE TABLE IF NOT EXISTS companies (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT,
                website         TEXT,
                domain          TEXT NOT NULL UNIQUE,
                industry        TEXT NOT NULL DEFAULT 'overseas_education_immigration',
                search_query_id INTEGER,
                email_status    TEXT NOT NULL DEFAULT 'pending',
                scraped_at      TEXT,
                notes           TEXT,
                FOREIGN KEY (search_query_id) REFERENCES search_queries(id)
            );

            CREATE TABLE IF NOT EXISTS company_emails (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id  INTEGER NOT NULL,
                email       TEXT NOT NULL UNIQUE,
                source_page TEXT,
                found_at    TEXT NOT NULL,
                FOREIGN KEY (company_id) REFERENCES companies(id)
            );

            CREATE TABLE IF NOT EXISTS email_sent (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT NOT NULL UNIQUE,
                company_id    INTEGER,
                company_name  TEXT,
                from_profile  TEXT,
                subject       TEXT,
                status        TEXT NOT NULL DEFAULT 'sent',
                error_message TEXT,
                sent_at       TEXT,
                FOREIGN KEY (company_id) REFERENCES companies(id)
            );

            CREATE TABLE IF NOT EXISTS campaign_subjects (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                subject    TEXT NOT NULL UNIQUE,
                first_used TEXT NOT NULL,
                last_used  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reply_forwards (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                gmail_account    TEXT NOT NULL,
                incoming_msg_id  TEXT NOT NULL,
                from_address     TEXT,
                subject          TEXT,
                detection_method TEXT,
                llm_reason       TEXT,
                forwarded_at     TEXT NOT NULL,
                UNIQUE (gmail_account, incoming_msg_id)
            );

            CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);
            CREATE INDEX IF NOT EXISTS idx_search_status ON search_queries(status);
            """
        )
        self._migrate_schema()
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_companies_industry ON companies(industry)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_industry ON search_queries(industry)"
        )
        self.conn.commit()

    def _migrate_schema(self) -> None:
        sq_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(search_queries)")}
        if "industry" not in sq_cols:
            self.conn.execute(
                "ALTER TABLE search_queries ADD COLUMN industry TEXT NOT NULL "
                "DEFAULT 'overseas_education_immigration'"
            )
        co_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(companies)")}
        if "industry" not in co_cols:
            self.conn.execute(
                "ALTER TABLE companies ADD COLUMN industry TEXT NOT NULL "
                "DEFAULT 'overseas_education_immigration'"
            )
        es_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(email_sent)")}
        if "message_id" not in es_cols:
            self.conn.execute("ALTER TABLE email_sent ADD COLUMN message_id TEXT")

    def ensure_campaign_subject(self, subject: str) -> None:
        subject = (subject or "").strip()
        if not subject:
            return
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO campaign_subjects (subject, first_used, last_used)
            VALUES (?, ?, ?)
            ON CONFLICT(subject) DO UPDATE SET last_used = excluded.last_used
            """,
            (subject, now, now),
        )
        self.conn.commit()

    def list_campaign_subjects(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT subject FROM campaign_subjects ORDER BY last_used DESC"
        ).fetchall()
        return [r[0] for r in rows]

    def distinct_sent_subjects(self) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT subject FROM email_sent
            WHERE status = 'sent' AND subject IS NOT NULL AND trim(subject) != ''
            """
        ).fetchall()
        return [r[0] for r in rows]

    def sent_message_ids(self) -> set[str]:
        rows = self.conn.execute(
            """
            SELECT message_id FROM email_sent
            WHERE status = 'sent' AND message_id IS NOT NULL AND trim(message_id) != ''
            """
        ).fetchall()
        return {r[0].strip().lower() for r in rows}

    def sent_recipient_emails(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT email FROM email_sent WHERE status = 'sent'"
        ).fetchall()
        return {r[0].strip().lower() for r in rows}

    def reply_already_forwarded(self, gmail_account: str, incoming_msg_id: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM reply_forwards
            WHERE lower(gmail_account) = lower(?) AND lower(incoming_msg_id) = lower(?)
            LIMIT 1
            """,
            (gmail_account, incoming_msg_id),
        ).fetchone()
        return row is not None

    def record_reply_forward(
        self,
        *,
        gmail_account: str,
        incoming_msg_id: str,
        from_address: str,
        subject: str,
        detection_method: str,
        llm_reason: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO reply_forwards
                (gmail_account, incoming_msg_id, from_address, subject,
                 detection_method, llm_reason, forwarded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gmail_account,
                incoming_msg_id,
                from_address,
                subject,
                detection_method,
                llm_reason,
                utc_now(),
            ),
        )
        self.conn.commit()

    def add_search_queries(
        self,
        queries: list[str],
        *,
        industry: str = "overseas_education_immigration",
        source: str = "seed",
    ) -> int:
        added = 0
        now = utc_now()
        industry = (industry or "overseas_education_immigration").strip()
        for q in queries:
            q = (q or "").strip()
            if not q:
                continue
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO search_queries
                    (query_text, industry, status, source, created_at)
                VALUES (?, ?, 'pending', ?, ?)
                """,
                (q, industry, source, now),
            )
            if cur.rowcount:
                added += 1
        self.conn.commit()
        return added

    def next_pending_query(self, industry: str | None = None) -> sqlite3.Row | None:
        if industry:
            return self.conn.execute(
                """
                SELECT * FROM search_queries
                WHERE status = 'pending' AND industry = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (industry.strip(),),
            ).fetchone()

        pending_industries = [
            r[0]
            for r in self.conn.execute(
                """
                SELECT DISTINCT industry FROM search_queries
                WHERE status = 'pending'
                """
            ).fetchall()
        ]
        if not pending_industries:
            return None

        random.shuffle(pending_industries)
        for iid in pending_industries:
            row = self.conn.execute(
                """
                SELECT * FROM search_queries
                WHERE status = 'pending' AND industry = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (iid,),
            ).fetchone()
            if row:
                return row
        return None

    def mark_query_done(self, query_id: int, status: str = "done") -> None:
        self.conn.execute(
            """
            UPDATE search_queries
            SET status = ?, completed_at = ?
            WHERE id = ?
            """,
            (status, utc_now(), query_id),
        )
        self.conn.commit()

    def domain_exists(self, domain: str) -> bool:
        domain = clean_domain(domain)
        if not domain:
            return True
        row = self.conn.execute(
            "SELECT 1 FROM companies WHERE domain = ? LIMIT 1",
            (domain,),
        ).fetchone()
        return row is not None

    def upsert_company(
        self,
        *,
        name: str,
        website: str,
        search_query_id: int | None,
        industry: str = "overseas_education_immigration",
        email_status: str = "pending",
        notes: str = "",
    ) -> int | None:
        domain = clean_domain(website)
        if not domain:
            return None
        now = utc_now()
        cur = self.conn.execute(
            """
            INSERT INTO companies
                (name, website, domain, industry, search_query_id, email_status, scraped_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                name = COALESCE(excluded.name, companies.name),
                website = COALESCE(excluded.website, companies.website),
                industry = COALESCE(companies.industry, excluded.industry),
                scraped_at = excluded.scraped_at,
                notes = CASE
                    WHEN excluded.notes != '' THEN excluded.notes
                    ELSE companies.notes
                END
            """,
            (name or domain, website, domain, industry, search_query_id, email_status, now, notes),
        )
        self.conn.commit()
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = self.conn.execute(
            "SELECT id FROM companies WHERE domain = ?",
            (domain,),
        ).fetchone()
        return int(row["id"]) if row else None

    def add_company_email(self, company_id: int, email: str, source_page: str) -> bool:
        email = (email or "").strip().lower()
        if not email:
            return False
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO company_emails (company_id, email, source_page, found_at)
            VALUES (?, ?, ?, ?)
            """,
            (company_id, email, source_page, utc_now()),
        )
        if cur.rowcount:
            self.conn.execute(
                "UPDATE companies SET email_status = 'found' WHERE id = ?",
                (company_id,),
            )
            self.conn.commit()
            return True
        self.conn.commit()
        return False

    def mark_company_no_email(self, company_id: int) -> None:
        self.conn.execute(
            """
            UPDATE companies
            SET email_status = 'no_email', scraped_at = ?
            WHERE id = ?
            """,
            (utc_now(), company_id),
        )
        self.conn.commit()

    def _email_priority(self, email: str) -> int:
        local = email.split("@", 1)[0].lower()
        preferred = (
            "info",
            "contact",
            "enquiries",
            "inquiry",
            "hello",
            "admin",
            "support",
            "sales",
            "office",
        )
        if local in preferred:
            return preferred.index(local)
        if local.startswith("info") or local.startswith("contact"):
            return 2
        return 100

    def pending_send_queue(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT ce.email, ce.company_id, c.name AS company_name, c.domain,
                   c.website, c.industry
            FROM company_emails ce
            JOIN companies c ON c.id = ce.company_id
            LEFT JOIN email_sent es ON lower(es.email) = lower(ce.email)
            WHERE es.id IS NULL
            ORDER BY ce.company_id ASC, ce.id ASC
            """
        ).fetchall()

        best_by_company: dict[int, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            cid = int(item["company_id"])
            email = item["email"]
            score = self._email_priority(email)
            current = best_by_company.get(cid)
            if current is None or score < current["_priority"]:
                item["_priority"] = score
                best_by_company[cid] = item

        ordered = sorted(best_by_company.values(), key=lambda x: (x["_priority"], x["company_id"]))
        out: list[dict[str, Any]] = []
        for item in ordered[:limit]:
            item.pop("_priority", None)
            out.append(item)
        return out

    def record_email_sent(
        self,
        *,
        email: str,
        company_id: int | None,
        company_name: str,
        from_profile: str,
        subject: str,
        status: str = "sent",
        error_message: str = "",
        message_id: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO email_sent
                (email, company_id, company_name, from_profile, subject,
                 status, error_message, sent_at, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                email.lower(),
                company_id,
                company_name,
                from_profile,
                subject,
                status,
                error_message,
                utc_now() if status == "sent" else None,
                (message_id or "").strip() or None,
            ),
        )
        if status == "sent" and subject:
            self.ensure_campaign_subject(subject)
        self.conn.commit()

    def email_already_sent(self, email: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM email_sent WHERE lower(email) = lower(?) AND status = 'sent' LIMIT 1",
            (email,),
        ).fetchone()
        return row is not None

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for label, sql in {
            "search_queries_pending": "SELECT COUNT(*) FROM search_queries WHERE status='pending'",
            "search_queries_done": "SELECT COUNT(*) FROM search_queries WHERE status='done'",
            "companies_total": "SELECT COUNT(*) FROM companies",
            "companies_with_email": "SELECT COUNT(*) FROM companies WHERE email_status='found'",
            "emails_found": "SELECT COUNT(*) FROM company_emails",
            "emails_sent": "SELECT COUNT(*) FROM email_sent WHERE status='sent'",
            "emails_failed": "SELECT COUNT(*) FROM email_sent WHERE status='failed'",
            "replies_forwarded": "SELECT COUNT(*) FROM reply_forwards",
            "campaign_subjects": "SELECT COUNT(*) FROM campaign_subjects",
        }.items():
            out[label] = int(self.conn.execute(sql).fetchone()[0])
        return out

    def summary_by_industry(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
                c.industry,
                COUNT(*) AS companies_total,
                SUM(CASE WHEN c.email_status = 'found' THEN 1 ELSE 0 END) AS companies_with_email
            FROM companies c
            GROUP BY c.industry
            ORDER BY c.industry ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
