"""SQLite storage for internship workflow demo."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "data" / "db" / "internships.db"

STATUS_DRAFT = "draft"
STATUS_OPEN = "open"
STATUS_CLOSED = "closed"

APP_SUBMITTED = "submitted"
APP_SHORTLISTED = "shortlisted"
APP_SELECTED = "selected"
APP_REJECTED = "rejected"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class InternshipDB:
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
            CREATE TABLE IF NOT EXISTS internships (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT NOT NULL,
                company         TEXT NOT NULL,
                description     TEXT,
                location        TEXT,
                duration_weeks  INTEGER,
                status          TEXT NOT NULL DEFAULT 'draft',
                created_at      TEXT NOT NULL,
                approved_at     TEXT,
                approved_by     TEXT
            );

            CREATE TABLE IF NOT EXISTS applications (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                internship_id   INTEGER NOT NULL,
                applicant_name  TEXT NOT NULL,
                email           TEXT NOT NULL,
                note            TEXT,
                status          TEXT NOT NULL DEFAULT 'submitted',
                applied_at      TEXT NOT NULL,
                selected_at     TEXT,
                FOREIGN KEY (internship_id) REFERENCES internships(id)
            );

            CREATE INDEX IF NOT EXISTS idx_internships_status ON internships(status);
            CREATE INDEX IF NOT EXISTS idx_applications_internship ON applications(internship_id);
            """
        )
        self.conn.commit()

    def create_internship(
        self,
        *,
        title: str,
        company: str,
        description: str = "",
        location: str = "",
        duration_weeks: int | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO internships
                (title, company, description, location, duration_weeks, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title.strip(),
                company.strip(),
                description.strip(),
                location.strip(),
                duration_weeks,
                STATUS_DRAFT,
                utc_now(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_internship(self, internship_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM internships WHERE id = ?",
            (internship_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_internships(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM internships WHERE status = ? ORDER BY id DESC",
                (status.strip(),),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM internships ORDER BY id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def approve_internship(self, internship_id: int, *, approved_by: str = "admin") -> bool:
        row = self.get_internship(internship_id)
        if not row or row["status"] != STATUS_DRAFT:
            return False
        self.conn.execute(
            """
            UPDATE internships
            SET status = ?, approved_at = ?, approved_by = ?
            WHERE id = ?
            """,
            (STATUS_OPEN, utc_now(), approved_by.strip(), internship_id),
        )
        self.conn.commit()
        return True

    def apply(
        self,
        internship_id: int,
        *,
        applicant_name: str,
        email: str,
        note: str = "",
    ) -> int | None:
        row = self.get_internship(internship_id)
        if not row or row["status"] != STATUS_OPEN:
            return None
        cur = self.conn.execute(
            """
            INSERT INTO applications
                (internship_id, applicant_name, email, note, status, applied_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                internship_id,
                applicant_name.strip(),
                email.strip().lower(),
                note.strip(),
                APP_SUBMITTED,
                utc_now(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_applications(
        self,
        internship_id: int,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                """
                SELECT * FROM applications
                WHERE internship_id = ? AND status = ?
                ORDER BY id ASC
                """,
                (internship_id, status),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM applications
                WHERE internship_id = ?
                ORDER BY id ASC
                """,
                (internship_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_application(self, application_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        return dict(row) if row else None

    def select_application(self, application_id: int) -> bool:
        app = self.get_application(application_id)
        if not app or app["status"] == APP_SELECTED:
            return False
        internship = self.get_internship(int(app["internship_id"]))
        if not internship or internship["status"] != STATUS_OPEN:
            return False

        now = utc_now()
        self.conn.execute(
            """
            UPDATE applications
            SET status = ?, selected_at = ?
            WHERE id = ?
            """,
            (APP_SELECTED, now, application_id),
        )
        self.conn.execute(
            """
            UPDATE applications
            SET status = ?
            WHERE internship_id = ? AND id != ? AND status = ?
            """,
            (APP_REJECTED, app["internship_id"], application_id, APP_SUBMITTED),
        )
        self.conn.execute(
            "UPDATE internships SET status = ? WHERE id = ?",
            (STATUS_CLOSED, app["internship_id"]),
        )
        self.conn.commit()
        return True

    def summary(self) -> dict[str, int]:
        return {
            "internships_total": int(
                self.conn.execute("SELECT COUNT(*) FROM internships").fetchone()[0]
            ),
            "internships_open": int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM internships WHERE status = ?",
                    (STATUS_OPEN,),
                ).fetchone()[0]
            ),
            "applications_total": int(
                self.conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            ),
            "applications_selected": int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM applications WHERE status = ?",
                    (APP_SELECTED,),
                ).fetchone()[0]
            ),
        }
