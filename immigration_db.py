"""SQLite persistence for immigration scrape + email pipeline."""

from __future__ import annotations

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
                query_text  TEXT NOT NULL UNIQUE,
                status      TEXT NOT NULL DEFAULT 'pending',
                source      TEXT,
                created_at  TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS companies (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT,
                website         TEXT,
                domain          TEXT NOT NULL UNIQUE,
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

            CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);
            CREATE INDEX IF NOT EXISTS idx_search_status ON search_queries(status);
            """
        )
        self.conn.commit()

    def add_search_queries(self, queries: list[str], source: str = "seed") -> int:
        added = 0
        now = utc_now()
        for q in queries:
            q = (q or "").strip()
            if not q:
                continue
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO search_queries (query_text, status, source, created_at)
                VALUES (?, 'pending', ?, ?)
                """,
                (q, source, now),
            )
            if cur.rowcount:
                added += 1
        self.conn.commit()
        return added

    def next_pending_query(self) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM search_queries
            WHERE status = 'pending'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()

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
        email_status: str = "pending",
        notes: str = "",
    ) -> int | None:
        domain = clean_domain(website)
        if not domain:
            return None
        now = utc_now()
        cur = self.conn.execute(
            """
            INSERT INTO companies (name, website, domain, search_query_id, email_status, scraped_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                name = COALESCE(excluded.name, companies.name),
                website = COALESCE(excluded.website, companies.website),
                scraped_at = excluded.scraped_at,
                notes = CASE
                    WHEN excluded.notes != '' THEN excluded.notes
                    ELSE companies.notes
                END
            """,
            (name or domain, website, domain, search_query_id, email_status, now, notes),
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
            SELECT ce.email, ce.company_id, c.name AS company_name, c.domain, c.website
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
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO email_sent
                (email, company_id, company_name, from_profile, subject, status, error_message, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
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
        }.items():
            out[label] = int(self.conn.execute(sql).fetchone()[0])
        return out
