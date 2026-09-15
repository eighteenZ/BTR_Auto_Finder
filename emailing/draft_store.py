"""Draft + campaign-job stores: the hunter→marketing DB contract.

``email_drafts`` is written by the hunter pipeline (and the backfill script)
and consumed by the marketing service for approval and campaign creation.
``campaign_jobs`` is the DB-mediated handoff queue the hunter domain enqueues
into and the marketing service claims from. No hunt-domain imports here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from persistence.db import execute, fetch_all, fetch_one, get_session


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_DRAFT_COLS = [
    "id", "hunt_id", "sequence_index", "lead_id", "lead_key", "company_name",
    "website", "locale", "target", "targets", "emails", "language_choice",
    "strategy_brief", "validation_summary", "review_summary", "review_status",
    "generation_mode", "template_id", "template_group", "template_usage_index",
    "template_max_send_count", "template_seed_source", "status", "manual_review",
    "error", "created_at", "updated_at",
]

_JSONB_DRAFT_COLS = {
    "target", "targets", "emails", "language_choice", "strategy_brief",
    "validation_summary", "review_summary", "manual_review",
}


class EmailDraftStore:
    """CRUD for the email_drafts contract table."""

    def __init__(self, db_path: str = "") -> None:
        # ``db_path`` kept for signature parity with EmailStore; ignored.
        self.db_path = db_path

    def init_db(self) -> None:
        """Schema is managed by Alembic; no-op for callers."""
        return None

    def upsert_draft(self, payload: dict[str, Any]) -> None:
        """Insert or refresh a draft keyed on (hunt_id, sequence_index).

        Manual review state is never overwritten by a re-write from the
        hunter side — approval decisions belong to the marketing service.
        """
        values = []
        for col in _DRAFT_COLS:
            value = payload.get(col, "")
            if col in _JSONB_DRAFT_COLS:
                if not isinstance(value, (dict, list)):
                    value = {} if col not in {"targets", "emails"} else []
                value = json.dumps(value, ensure_ascii=False, default=str)
            values.append(value)
        placeholders = ", ".join(
            "CAST(? AS jsonb)" if col in _JSONB_DRAFT_COLS else "?" for col in _DRAFT_COLS
        )
        updates = ", ".join(
            f"{col}=excluded.{col}"
            for col in _DRAFT_COLS
            if col not in {"id", "hunt_id", "sequence_index", "created_at", "manual_review", "status"}
        )
        with get_session() as session:
            execute(
                session,
                f"INSERT INTO email_drafts ({', '.join(_DRAFT_COLS)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(hunt_id, sequence_index) DO UPDATE SET {updates}",
                values,
            )

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            return fetch_one(session, "SELECT * FROM email_drafts WHERE id = ?", (draft_id,))

    def get_draft_by_index(self, hunt_id: str, sequence_index: int) -> dict[str, Any] | None:
        with get_session() as session:
            return fetch_one(
                session,
                "SELECT * FROM email_drafts WHERE hunt_id = ? AND sequence_index = ?",
                (hunt_id, sequence_index),
            )

    def list_drafts_for_hunt(self, hunt_id: str) -> list[dict[str, Any]]:
        with get_session() as session:
            return fetch_all(
                session,
                "SELECT * FROM email_drafts WHERE hunt_id = ? ORDER BY sequence_index ASC",
                (hunt_id,),
            )

    def list_drafts(self, *, status: str = "", hunt_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        clauses, values = [], []
        if status:
            clauses.append("status = ?")
            values.append(status)
        if hunt_id:
            clauses.append("hunt_id = ?")
            values.append(hunt_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with get_session() as session:
            return fetch_all(
                session,
                f"SELECT * FROM email_drafts {where} ORDER BY created_at DESC LIMIT ?",
                tuple(values),
            )

    def set_decision(self, draft_id: str, *, decision: str, notes: str = "") -> dict[str, Any] | None:
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be 'approved' or 'rejected'")
        review = {"decision": decision, "notes": notes, "updated_at": now_iso()}
        with get_session() as session:
            execute(
                session,
                "UPDATE email_drafts SET status = ?, manual_review = CAST(? AS jsonb), updated_at = ? WHERE id = ?",
                (decision, json.dumps(review, ensure_ascii=False), review["updated_at"], draft_id),
            )
        return self.get_draft(draft_id)

    def count_drafts_for_hunt(self, hunt_id: str, *, status: str = "") -> int:
        clause, values = "hunt_id = ?", [hunt_id]
        if status:
            clause += " AND status = ?"
            values.append(status)
        with get_session() as session:
            row = fetch_one(
                session, f"SELECT COUNT(*) AS count FROM email_drafts WHERE {clause}", tuple(values)
            )
        return int((row or {}).get("count", 0))


class CampaignJobQueue:
    """DB-mediated handoff queue: hunter enqueues, marketing claims."""

    def __init__(self, db_path: str = "") -> None:
        self.db_path = db_path

    def init_db(self) -> None:
        return None

    def enqueue(self, hunt_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(uuid4())
        created = now_iso()
        with get_session() as session:
            execute(
                session,
                "INSERT INTO campaign_jobs (id, hunt_id, status, payload_json, created_at, updated_at) "
                "VALUES (?, ?, 'queued', ?, ?, ?)",
                (job_id, hunt_id, json.dumps(payload), created, created),
            )
        return {"id": job_id, "hunt_id": hunt_id, "status": "queued"}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            return fetch_one(session, "SELECT * FROM campaign_jobs WHERE id = ?", (job_id,))

    def list_jobs(self, *, hunt_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if hunt_id:
            with get_session() as session:
                return fetch_all(
                    session,
                    "SELECT * FROM campaign_jobs WHERE hunt_id = ? ORDER BY created_at DESC LIMIT ?",
                    (hunt_id, limit),
                )
        with get_session() as session:
            return fetch_all(
                session,
                "SELECT * FROM campaign_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )

    def claim_next(self, worker_id: str) -> dict[str, Any] | None:
        now = now_iso()
        with get_session() as session:
            row = fetch_one(
                session,
                """
                SELECT * FROM campaign_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
            )
            if not row:
                return None
            execute(
                session,
                "UPDATE campaign_jobs SET status = 'running', claimed_by = ?, claimed_at = ?, "
                "attempt_count = attempt_count + 1, updated_at = ? WHERE id = ?",
                (worker_id, now, now, row["id"]),
            )
        return self.get_job(row["id"])

    def mark_completed(self, job_id: str, *, campaign_id: str) -> None:
        now = now_iso()
        with get_session() as session:
            execute(
                session,
                "UPDATE campaign_jobs SET status = 'completed', campaign_id = ?, finished_at = ?, "
                "updated_at = ?, last_error = '' WHERE id = ?",
                (campaign_id, now, now, job_id),
            )

    def mark_failed(self, job_id: str, *, error: str) -> None:
        now = now_iso()
        with get_session() as session:
            execute(
                session,
                "UPDATE campaign_jobs SET status = 'failed', last_error = ?, finished_at = ?, "
                "updated_at = ? WHERE id = ?",
                (error, now, now, job_id),
            )
