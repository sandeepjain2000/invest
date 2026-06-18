"""Separate SQLite store for bulk CA Connect profile enrichment (resumable)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from immigration_db import domain_from_ca_profile

DB_PATH = Path(__file__).resolve().parent / "data" / "db" / "ca_bulk.db"
CA_BULK_INDUSTRY = "ca_cs_firms"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CaBulkDB:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ca_searches (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                service         TEXT NOT NULL,
                state           TEXT NOT NULL,
                city            TEXT NOT NULL,
                search_url      TEXT,
                listing_count   INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'queued',
                listed_at       TEXT,
                completed_at    TEXT,
                created_at      TEXT NOT NULL,
                UNIQUE(service, state, city)
            );

            CREATE TABLE IF NOT EXISTS ca_listings (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                search_id           INTEGER NOT NULL,
                profile_url         TEXT NOT NULL UNIQUE,
                name                TEXT,
                listing_type        TEXT,
                professional_city   TEXT,
                location            TEXT,
                services_json       TEXT,
                email               TEXT,
                mobile              TEXT,
                website             TEXT,
                specialization_json TEXT,
                enrich_status       TEXT NOT NULL DEFAULT 'pending',
                enrich_error        TEXT,
                enriched_at         TEXT,
                created_at          TEXT NOT NULL,
                FOREIGN KEY (search_id) REFERENCES ca_searches(id)
            );

            CREATE INDEX IF NOT EXISTS idx_ca_listings_enrich
                ON ca_listings(enrich_status, search_id, id);

            CREATE TABLE IF NOT EXISTS bulk_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def get_state(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM bulk_state WHERE key = ?",
            (key,),
        ).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO bulk_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.conn.commit()

    def upsert_search(
        self,
        *,
        service: str,
        state: str,
        city: str,
        search_url: str = "",
        listing_count: int = 0,
        status: str = "queued",
    ) -> int:
        now = utc_now()
        cur = self.conn.execute(
            """
            INSERT INTO ca_searches
                (service, state, city, search_url, listing_count, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(service, state, city) DO UPDATE SET
                search_url = COALESCE(NULLIF(excluded.search_url, ''), ca_searches.search_url),
                listing_count = CASE
                    WHEN excluded.listing_count > 0 THEN excluded.listing_count
                    ELSE ca_searches.listing_count
                END
            """,
            (service, state, city, search_url, listing_count, status, now),
        )
        self.conn.commit()
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = self.conn.execute(
            """
            SELECT id FROM ca_searches
            WHERE service = ? AND state = ? AND city = ?
            """,
            (service, state, city),
        ).fetchone()
        return int(row["id"])

    def seed_search_queue(self, searches: list[dict[str, str]]) -> int:
        added = 0
        for item in searches:
            service = (item.get("service") or "Audit").strip()
            state = (item.get("state") or "").strip()
            city = (item.get("city") or "").strip()
            if not state or not city:
                continue
            before = self.conn.total_changes
            self.upsert_search(service=service, state=state, city=city, status="queued")
            if self.conn.total_changes > before:
                added += 1
        return added

    def get_search(self, search_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM ca_searches WHERE id = ?",
            (search_id,),
        ).fetchone()
        return dict(row) if row else None

    def next_search_for_run(self) -> dict[str, Any] | None:
        """Prefer a search that already has pending listings; else next queued city to harvest."""
        row = self.conn.execute(
            """
            SELECT s.*
            FROM ca_searches s
            WHERE EXISTS (
                SELECT 1 FROM ca_listings l
                WHERE l.search_id = s.id AND l.enrich_status = 'pending'
            )
            ORDER BY s.id ASC
            LIMIT 1
            """
        ).fetchone()
        if row:
            return dict(row)
        return self.next_search_needing_listings()

    def next_search_needing_listings(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM ca_searches
            WHERE status = 'queued'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None

    def next_search_needing_enrichment(self) -> dict[str, Any] | None:
        return self.next_search_for_run()

    def mark_search_listed(self, search_id: int, *, search_url: str, listing_count: int) -> None:
        self.conn.execute(
            """
            UPDATE ca_searches
            SET status = 'listed', search_url = ?, listing_count = ?, listed_at = ?
            WHERE id = ?
            """,
            (search_url, listing_count, utc_now(), search_id),
        )
        self.conn.commit()

    def mark_search_complete(self, search_id: int) -> None:
        self.conn.execute(
            """
            UPDATE ca_searches
            SET status = 'complete', completed_at = ?
            WHERE id = ?
            """,
            (utc_now(), search_id),
        )
        self.conn.commit()

    def insert_listings(self, search_id: int, listings: list[dict]) -> int:
        added = 0
        now = utc_now()
        for item in listings:
            profile_url = (item.get("profile_url") or "").strip()
            if not profile_url:
                continue
            services = item.get("services") or []
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO ca_listings
                    (search_id, profile_url, name, listing_type, professional_city,
                     location, services_json, email, mobile, website, enrich_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    search_id,
                    profile_url,
                    (item.get("name") or "").strip(),
                    (item.get("listing_type") or "").strip(),
                    (item.get("professional_city") or "").strip(),
                    (item.get("location") or "").strip(),
                    json.dumps(services, ensure_ascii=False),
                    (item.get("email") or "").strip().lower(),
                    (item.get("mobile") or "").strip(),
                    (item.get("website") or "").strip(),
                    "done" if (item.get("email") or "").strip() else "pending",
                    now,
                ),
            )
            if cur.rowcount:
                added += 1
            elif (item.get("email") or "").strip():
                self.conn.execute(
                    """
                    UPDATE ca_listings SET
                        email = COALESCE(NULLIF(?, ''), email),
                        mobile = COALESCE(NULLIF(?, ''), mobile),
                        enrich_status = CASE
                            WHEN enrich_status = 'pending' AND ? != '' THEN 'done'
                            ELSE enrich_status
                        END,
                        enriched_at = COALESCE(enriched_at, ?)
                    WHERE profile_url = ?
                    """,
                    (
                        item.get("email", "").strip().lower(),
                        item.get("mobile", "").strip(),
                        item.get("email", "").strip().lower(),
                        now,
                        profile_url,
                    ),
                )
        self.conn.commit()
        return added

    def next_pending_listings(self, search_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM ca_listings
            WHERE search_id = ? AND enrich_status = 'pending'
            ORDER BY id ASC
            LIMIT ?
            """,
            (search_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_pending_listings(self, search_id: int | None = None) -> int:
        if search_id is not None:
            row = self.conn.execute(
                """
                SELECT COUNT(*) AS n FROM ca_listings
                WHERE search_id = ? AND enrich_status = 'pending'
                """,
                (search_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                """
                SELECT COUNT(*) AS n FROM ca_listings
                WHERE enrich_status = 'pending'
                """
            ).fetchone()
        return int(row["n"]) if row else 0

    def save_enrichment(
        self,
        listing_id: int,
        *,
        email: str,
        mobile: str,
        website: str,
        specialization: list[str],
        member_name: str,
        status: str,
        error: str = "",
    ) -> bool:
        email = (email or "").strip().lower()
        status = status if status in ("done", "no_email", "failed", "login_required") else "failed"
        self.conn.execute(
            """
            UPDATE ca_listings SET
                email = ?,
                mobile = ?,
                website = ?,
                specialization_json = ?,
                name = COALESCE(NULLIF(?, ''), name),
                enrich_status = ?,
                enrich_error = ?,
                enriched_at = ?
            WHERE id = ?
            """,
            (
                email,
                (mobile or "").strip(),
                (website or "").strip(),
                json.dumps(specialization or [], ensure_ascii=False),
                (member_name or "").strip(),
                status,
                (error or "").strip()[:500],
                utc_now(),
                listing_id,
            ),
        )
        self.conn.commit()
        return bool(email)

    def email_exists(self, email: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM ca_listings WHERE lower(email) = lower(?) AND email != '' LIMIT 1",
            (email,),
        ).fetchone()
        return row is not None

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for label, sql in {
            "searches_total": "SELECT COUNT(*) FROM ca_searches",
            "searches_queued": "SELECT COUNT(*) FROM ca_searches WHERE status = 'queued'",
            "searches_listed": "SELECT COUNT(*) FROM ca_searches WHERE status = 'listed'",
            "searches_complete": "SELECT COUNT(*) FROM ca_searches WHERE status = 'complete'",
            "listings_total": "SELECT COUNT(*) FROM ca_listings",
            "listings_pending": "SELECT COUNT(*) FROM ca_listings WHERE enrich_status = 'pending'",
            "listings_with_email": "SELECT COUNT(*) FROM ca_listings WHERE email != '' AND email IS NOT NULL",
            "listings_no_email": "SELECT COUNT(*) FROM ca_listings WHERE enrich_status = 'no_email'",
            "listings_failed": "SELECT COUNT(*) FROM ca_listings WHERE enrich_status = 'failed'",
        }.items():
            out[label] = int(self.conn.execute(sql).fetchone()[0])
        return out

    def searches_progress(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
                s.id,
                s.service,
                s.state,
                s.city,
                s.status,
                s.listing_count,
                SUM(CASE WHEN l.enrich_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN l.email != '' AND l.email IS NOT NULL THEN 1 ELSE 0 END) AS with_email
            FROM ca_searches s
            LEFT JOIN ca_listings l ON l.search_id = s.id
            GROUP BY s.id
            ORDER BY s.id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def last_checkpoint(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT l.*, s.service, s.state, s.city
            FROM ca_listings l
            JOIN ca_searches s ON s.id = l.search_id
            WHERE l.enriched_at IS NOT NULL
            ORDER BY l.enriched_at DESC, l.id DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def listing_to_send_item(row: dict[str, Any]) -> dict[str, Any]:
        """Map ca_listings row to the shape expected by immigration_sender."""
        profile_url = (row.get("profile_url") or "").strip()
        email = (row.get("email") or "").strip().lower()
        name = (row.get("name") or "").strip()
        listing_type = (row.get("listing_type") or "member").strip()
        city = (
            (row.get("professional_city") or "").strip()
            or (row.get("city") or "").strip()
        )
        mobile = (row.get("mobile") or "").strip()
        website = (row.get("website") or "").strip() or profile_url
        domain = domain_from_ca_profile(profile_url)
        notes_parts = [f"CA Connect {listing_type}"]
        if city:
            notes_parts.append(city)
        if mobile:
            notes_parts.append(f"mobile:{mobile}")
        return {
            "email": email,
            "company_id": None,
            "company_name": name or domain or email,
            "domain": domain,
            "website": website,
            "industry": CA_BULK_INDUSTRY,
            "notes": " | ".join(notes_parts),
            "source_page": profile_url,
            "contact_source": "ca_bulk",
            "ca_bulk_listing_id": row.get("id"),
        }

    def pending_send_candidates(
        self,
        exclude_emails: set[str] | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Unsent CA portal contacts with enriched email (reads ca_listings directly)."""
        exclude = {e.lower() for e in (exclude_emails or set()) if e}
        rows = self.conn.execute(
            """
            SELECT
                l.id, l.profile_url, l.name, l.listing_type, l.professional_city,
                l.location, l.email, l.mobile, l.website, l.enrich_status,
                s.service, s.state, s.city
            FROM ca_listings l
            JOIN ca_searches s ON s.id = l.search_id
            WHERE l.email != '' AND l.email IS NOT NULL
              AND l.enrich_status = 'done'
            ORDER BY l.id ASC
            """
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = self.listing_to_send_item(dict(row))
            email = (item.get("email") or "").lower()
            if not email or email in exclude:
                continue
            out.append(item)
            if limit is not None and len(out) >= limit:
                break
        return out

    def count_unsent_recipients(self, exclude_emails: set[str] | None = None) -> int:
        exclude = {e.lower() for e in (exclude_emails or set()) if e}
        if not exclude:
            row = self.conn.execute(
                """
                SELECT COUNT(*) FROM ca_listings
                WHERE email != '' AND email IS NOT NULL AND enrich_status = 'done'
                """
            ).fetchone()
            return int(row[0]) if row else 0
        return len(self.pending_send_candidates(exclude))
