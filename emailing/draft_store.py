"""Draft + campaign-job stores: the hunter→marketing DB contract.

``email_drafts`` is written by the hunter pipeline (and the backfill script)
and consumed by the marketing service for approval and campaign creation.
``campaign_jobs`` is the DB-mediated handoff queue the hunter domain enqueues
into and the marketing service claims from. No hunt-domain imports here.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from persistence.db import execute, fetch_all, fetch_one, get_session
from persistence.lead_identity import compute_lead_key


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

        This is the single write path for the contract table, so placeholder
        sanitation happens here: whichever producer calls it (generator,
        backfill, repair), a stored draft never contains raw placeholders.

        Manual review state is never overwritten by a re-write from the
        hunter side — approval decisions belong to the marketing service.
        Content a reviewer has edited (``edited_by_review``) is also preserved:
        a regeneration must not silently replace wording a human already fixed.
        """
        payload = dict(payload)
        if payload.get("emails"):
            payload["emails"] = self._clean_emails(
                list(payload.get("emails") or []), payload.get("target") or {}
            )
        key = (str(payload.get("hunt_id", "") or ""), payload.get("sequence_index"))
        # Capture reviewer-edited content BEFORE the upsert overwrites it.
        reviewer_edits = self._reviewer_emails_if_any(*key)
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
        if reviewer_edits is not None:
            # A regeneration must not replace wording a human already edited:
            # restore the reviewer's version (keeping the new non-content fields).
            with get_session() as session:
                execute(
                    session,
                    "UPDATE email_drafts SET emails = CAST(? AS jsonb), updated_at = ? "
                    "WHERE hunt_id = ? AND sequence_index = ?",
                    (json.dumps(reviewer_edits, ensure_ascii=False), now_iso(), key[0], key[1]),
                )

    def _reviewer_emails_if_any(self, hunt_id: str, sequence_index: Any) -> list[Any] | None:
        """Return the reviewer-edited emails for a draft, or None if unedited."""
        with get_session() as session:
            row = fetch_one(
                session,
                "SELECT edited_by_review, emails FROM email_drafts "
                "WHERE hunt_id = ? AND sequence_index = ?",
                (hunt_id, sequence_index),
            )
        if row and row.get("edited_by_review"):
            return row.get("emails") or []
        return None

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

    def update_emails(self, draft_id: str, emails: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Replace a draft's outreach content on behalf of a human reviewer.

        Only a pending (``status='draft'``) draft may be edited — an approved
        draft's content must match what was approved, and a rejected draft is
        history. Content passes through the placeholder sanitizer, so a stored
        draft never carries raw tokens regardless of who wrote them. Returns
        the updated draft, or ``None`` when the draft is not in an editable
        state (caller maps that to 409).
        """
        current = self.get_draft(draft_id)
        if not current or str(current.get("status", "")) != "draft":
            return None
        emails = self._clean_emails(list(emails or []), current.get("target") or {})
        with get_session() as session:
            execute(
                session,
                "UPDATE email_drafts SET emails = CAST(? AS jsonb), edited_by_review = true, "
                "updated_at = ? WHERE id = ? AND status = 'draft'",
                (json.dumps(emails, ensure_ascii=False), now_iso(), draft_id),
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

    def count_drafts_by_status(self) -> dict[str, int]:
        """Lightweight status histogram for the review UI tab badges."""
        with get_session() as session:
            rows = fetch_all(session, "SELECT status, COUNT(*) AS n FROM email_drafts GROUP BY status")
        counts = {str(r["status"]): int(r["n"]) for r in rows}
        return {key: counts.get(key, 0) for key in ("draft", "approved", "rejected")}

    # ── hunter-side ingestion ─────────────────────────────────────────────

    def _lead_id_for_key(self, lead_key: str) -> str:
        if not lead_key:
            return ""
        with get_session() as session:
            row = fetch_one(session, "SELECT id FROM leads WHERE lead_key = ? LIMIT 1", (lead_key,))
        return str(row["id"]) if row else ""

    @staticmethod
    def _clean_emails(emails: list[Any], target: Any) -> list[Any]:
        """Strip signature placeholders before a draft lands in the contract table.

        Defense in depth: the generator already sanitizes, but drafts can also
        arrive from the legacy-JSON backfill, so the boundary normalises again.
        """
        from config.settings import get_settings

        from emailing.signature import recipient_display_name, sanitize_body_with_signature, sanitize_outreach_text

        settings = get_settings()
        recipient = recipient_display_name(target)
        cleaned: list[Any] = []
        for item in emails:
            if not isinstance(item, dict):
                cleaned.append(item)
                continue
            entry = dict(item)
            entry["subject"] = sanitize_outreach_text(str(entry.get("subject", "") or ""), settings, recipient_name=recipient)
            # Body = final deliverable: sanitize AND guarantee the configured signature.
            entry["body_text"] = sanitize_body_with_signature(str(entry.get("body_text", "") or ""), settings, recipient_name=recipient)
            cleaned.append(entry)
        return cleaned

    def _payload_from_sequence(self, hunt_id: str, index: int, seq: dict[str, Any]) -> dict[str, Any]:
        lead = seq.get("lead") or {}
        lead_key = compute_lead_key(lead) if lead else ""
        manual = seq.get("manual_review") or {}
        status = str(manual.get("decision", "") or "") or "draft"
        return {
            "id": str(uuid4()),
            "hunt_id": hunt_id,
            "sequence_index": index,
            "lead_id": self._lead_id_for_key(lead_key),
            "lead_key": lead_key,
            "company_name": str(lead.get("company_name", "") or ""),
            "website": str(lead.get("website", "") or ""),
            "locale": str(seq.get("locale", "en") or "en"),
            "target": seq.get("target") or {},
            "targets": seq.get("targets") or [],
            "emails": seq.get("emails") or [],
            "language_choice": seq.get("language_choice") or {},
            "strategy_brief": seq.get("strategy_brief") or {},
            "validation_summary": seq.get("validation_summary") or {},
            "review_summary": seq.get("review_summary") or {},
            "review_status": str(seq.get("review_status", "") or ""),
            "generation_mode": str(seq.get("generation_mode", "personalized") or "personalized"),
            "template_id": str(seq.get("template_id", "") or ""),
            "template_group": str(seq.get("template_group", "") or ""),
            "template_usage_index": int(seq.get("template_usage_index", 0) or 0),
            "template_max_send_count": int(seq.get("template_max_send_count", 0) or 0),
            "template_seed_source": str(seq.get("template_seed_source", "") or ""),
            "status": status,
            "manual_review": manual,
            "error": "",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

    def upsert_from_sequences(self, hunt_id: str, sequences: list[Any]) -> int:
        """Persist hunt-generated sequences into the draft contract table.

        Returns the number of drafts written. Manual decisions already stored
        are preserved (upsert_draft never overwrites manual_review/status).
        """
        written = 0
        for index, seq in enumerate(sequences or []):
            if not isinstance(seq, dict):
                continue
            self.upsert_draft(self._payload_from_sequence(hunt_id, index, seq))
            written += 1
        return written


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
                  AND (available_at = '' OR available_at <= ?)
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (now,),
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

    def requeue(self, job_id: str, *, delay_seconds: int) -> None:
        """Release a claimed job back to the queue with a delayed availability.

        Used when a campaign must wait for draft approval: attempt_count is
        rolled back so deferral cycles don't inflate real attempts.
        """
        now = now_iso()
        available = (datetime.now(timezone.utc) + timedelta(seconds=max(0, delay_seconds))).isoformat()
        with get_session() as session:
            execute(
                session,
                "UPDATE campaign_jobs SET status = 'queued', claimed_by = '', claimed_at = '', "
                "available_at = ?, attempt_count = CASE WHEN attempt_count > 0 THEN attempt_count - 1 ELSE 0 END, "
                "updated_at = ? WHERE id = ?",
                (available, now, job_id),
            )

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
