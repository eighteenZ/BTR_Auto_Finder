"""Migrate legacy SQLite data into PostgreSQL.

Sources:
  * {hunts_dir}/*.json           -> hunts + leads + lead_sightings + hunt_leads
  * automation_queue.db          -> hunt_jobs
  * email_automation.db          -> email_accounts / email_campaigns /
                                    lead_email_sequences / email_messages /
                                    email_reply_events

The script is idempotent: existing rows are left untouched. Run with
``--dry-run`` to inspect counts without writing.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from persistence import lead_repo
from persistence.db import execute, get_session
from persistence.models import Hunt
from persistence.utils import now_iso

_EMAIL_TABLES = [
    "email_accounts",
    "email_campaigns",
    "lead_email_sequences",
    "email_messages",
    "email_reply_events",
]


def _sqlite_rows(db_path: Path, table: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _sqlite_table_exists(db_path: Path, table: str) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _insert_ignore(session, table: str, row: dict[str, Any]) -> None:
    cols = list(row.keys())
    placeholders = ", ".join("?" for _ in cols)
    execute(
        session,
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
        [row[c] for c in cols],
    )


def migrate_hunts(hunts_dir: Path, *, dry_run: bool = False) -> dict[str, int]:
    stats = {"hunts": 0, "leads": 0}
    if not hunts_dir.exists():
        return stats

    for path in sorted(hunts_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        hunt_id = str(data.pop("hunt_id", path.stem))
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        leads = result.get("leads") or []
        stats["hunts"] += 1
        stats["leads"] += len(leads)
        if dry_run:
            continue

        with get_session() as session:
            row = session.get(Hunt, hunt_id)
            if row is None:
                row = Hunt(id=hunt_id, created_at=str(data.get("created_at", "") or now_iso()))
                session.add(row)
            row.status = str(data.get("status", "") or "completed")
            row.current_stage = str(data.get("current_stage", "") or "")
            row.hunt_round = int(data.get("hunt_round", 0) or 0)
            row.leads_count = len(leads)
            row.email_sequences_count = int(data.get("email_sequences_count", 0) or 0)
            row.error = str(data.get("error", "") or "")
            row.website_url = str(data.get("website_url", "") or "")
            row.updated_at = now_iso()
            row.data = {"hunt_id": hunt_id, **data}

        if leads:
            lead_repo.upsert_leads(leads, hunt_id=hunt_id, dedup_mode="reuse")
    return stats


def migrate_queue(queue_db: Path, *, dry_run: bool = False) -> int:
    if not queue_db.exists() or not _sqlite_table_exists(queue_db, "hunt_jobs"):
        return 0
    rows = _sqlite_rows(queue_db, "hunt_jobs")
    if dry_run:
        return len(rows)
    with get_session() as session:
        for row in rows:
            _insert_ignore(session, "hunt_jobs", row)
    return len(rows)


def migrate_email(email_db: Path, *, dry_run: bool = False) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not email_db.exists():
        return counts
    for table in _EMAIL_TABLES:
        if not _sqlite_table_exists(email_db, table):
            continue
        rows = _sqlite_rows(email_db, table)
        counts[table] = len(rows)
        if dry_run:
            continue
        with get_session() as session:
            for row in rows:
                _insert_ignore(session, table, row)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy SQLite data into PostgreSQL")
    parser.add_argument("--hunts-dir", default="data/hunts")
    parser.add_argument("--queue-db", default="automation_queue.db")
    parser.add_argument("--email-db", default="email_automation.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    hunts = migrate_hunts(Path(args.hunts_dir), dry_run=args.dry_run)
    jobs = migrate_queue(Path(args.queue_db), dry_run=args.dry_run)
    emails = migrate_email(Path(args.email_db), dry_run=args.dry_run)

    print(f"[migrate] hunts={hunts['hunts']} leads={hunts['leads']}")
    print(f"[migrate] hunt_jobs={jobs}")
    print(f"[migrate] email={emails}")
    if args.dry_run:
        print("[migrate] dry-run: no data written")


if __name__ == "__main__":
    main()
