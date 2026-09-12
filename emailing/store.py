"""SQLite-free email automation store — PostgreSQL via SQLAlchemy.

The public method signatures are kept identical to the previous SQLite
implementation so callers need no changes.
"""

from __future__ import annotations

from typing import Any

from persistence.db import execute, fetch_all, fetch_one, get_session


class EmailStore:
    def __init__(self, db_path: str = "") -> None:
        # ``db_path`` is accepted for backward compatibility but ignored.
        self.db_path = db_path

    def init_db(self) -> None:
        """Schema is managed by Alembic; kept as a no-op for callers."""
        return None

    # ── accounts ──────────────────────────────────────────────────────────
    def upsert_account(self, payload: dict[str, Any]) -> None:
        cols = [
            "id", "provider_type", "from_name", "from_email", "reply_to",
            "smtp_host", "smtp_port", "smtp_username", "smtp_secret_encrypted",
            "imap_host", "imap_port", "imap_username", "imap_secret_encrypted",
            "use_tls", "status", "daily_send_limit", "hourly_send_limit",
            "last_test_at", "created_at", "updated_at",
        ]
        values = [payload.get(col, "") for col in cols]
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{col}=excluded.{col}" for col in cols[1:])
        with get_session() as session:
            execute(
                session,
                f"INSERT INTO email_accounts ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                values,
            )

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            return fetch_one(session, "SELECT * FROM email_accounts WHERE id = ?", (account_id,))

    # ── campaigns ─────────────────────────────────────────────────────────
    def create_campaign(self, payload: dict[str, Any]) -> None:
        cols = list(payload.keys())
        with get_session() as session:
            execute(
                session,
                f"INSERT INTO email_campaigns ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [payload[c] for c in cols],
            )

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            return fetch_one(session, "SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,))

    def list_campaigns_for_hunt(self, hunt_id: str) -> list[dict[str, Any]]:
        with get_session() as session:
            return fetch_all(
                session,
                "SELECT * FROM email_campaigns WHERE hunt_id = ? ORDER BY created_at DESC",
                (hunt_id,),
            )

    def update_campaign_status(self, campaign_id: str, status: str, *, updated_at: str) -> None:
        with get_session() as session:
            execute(
                session,
                "UPDATE email_campaigns SET status = ?, updated_at = ? WHERE id = ?",
                (status, updated_at, campaign_id),
            )

    # ── sequences ─────────────────────────────────────────────────────────
    def create_sequence(self, payload: dict[str, Any]) -> None:
        cols = list(payload.keys())
        with get_session() as session:
            execute(
                session,
                f"INSERT INTO lead_email_sequences ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [payload[c] for c in cols],
            )

    def get_sequence(self, sequence_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            return fetch_one(session, "SELECT * FROM lead_email_sequences WHERE id = ?", (sequence_id,))

    def list_sequences_for_campaign(self, campaign_id: str) -> list[dict[str, Any]]:
        with get_session() as session:
            return fetch_all(
                session,
                "SELECT * FROM lead_email_sequences WHERE campaign_id = ? ORDER BY created_at ASC",
                (campaign_id,),
            )

    def has_contact_history_for_lead_key(self, lead_key: str) -> bool:
        """Return whether a lead/email pair was already queued or contacted before.

        Purely failed sequences with no sent messages do not block retry.
        """
        with get_session() as session:
            row = fetch_one(
                session,
                """
                SELECT 1 AS hit
                FROM lead_email_sequences seq
                LEFT JOIN email_messages msg
                  ON msg.sequence_id = seq.id
                 AND msg.status = 'sent'
                WHERE seq.lead_key = ?
                  AND (
                    seq.status != 'failed'
                    OR msg.id IS NOT NULL
                  )
                LIMIT 1
                """,
                (lead_key,),
            )
        return row is not None

    def update_sequence_status(
        self,
        sequence_id: str,
        *,
        status: str,
        updated_at: str,
        current_step: int | None = None,
        stop_reason: str | None = None,
        replied_at: str | None = None,
        last_sent_at: str | None = None,
        next_scheduled_at: str | None = None,
    ) -> None:
        fields = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, updated_at]
        for name, value in [
            ("current_step", current_step),
            ("stop_reason", stop_reason),
            ("replied_at", replied_at),
            ("last_sent_at", last_sent_at),
            ("next_scheduled_at", next_scheduled_at),
        ]:
            if value is not None:
                fields.append(f"{name} = ?")
                values.append(value)
        values.append(sequence_id)
        with get_session() as session:
            execute(session, f"UPDATE lead_email_sequences SET {', '.join(fields)} WHERE id = ?", values)

    # ── messages ──────────────────────────────────────────────────────────
    def create_message(self, payload: dict[str, Any]) -> None:
        cols = list(payload.keys())
        with get_session() as session:
            execute(
                session,
                f"INSERT INTO email_messages ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [payload[c] for c in cols],
            )

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            return fetch_one(session, "SELECT * FROM email_messages WHERE id = ?", (message_id,))

    def find_message_by_provider_message_id(self, provider_message_id: str) -> dict[str, Any] | None:
        with get_session() as session:
            return fetch_one(
                session,
                "SELECT * FROM email_messages WHERE provider_message_id = ? LIMIT 1",
                (provider_message_id,),
            )

    def get_message_for_step(self, sequence_id: str, step_number: int) -> dict[str, Any] | None:
        with get_session() as session:
            return fetch_one(
                session,
                "SELECT * FROM email_messages WHERE sequence_id = ? AND step_number = ?",
                (sequence_id, step_number),
            )

    def list_messages_for_sequence(self, sequence_id: str) -> list[dict[str, Any]]:
        with get_session() as session:
            return fetch_all(
                session,
                "SELECT * FROM email_messages WHERE sequence_id = ? ORDER BY step_number ASC",
                (sequence_id,),
            )

    def list_pending_messages_ready(self, now_iso: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with get_session() as session:
            return fetch_all(
                session,
                "SELECT * FROM email_messages WHERE status = 'pending' AND scheduled_at <= ? "
                "ORDER BY scheduled_at ASC LIMIT ?",
                (now_iso, limit),
            )

    def mark_message_sent(self, message_id: str, *, provider_message_id: str, thread_key: str, sent_at: str) -> None:
        with get_session() as session:
            execute(
                session,
                "UPDATE email_messages SET status = 'sent', provider_message_id = ?, thread_key = ?, "
                "sent_at = ?, updated_at = ? WHERE id = ?",
                (provider_message_id, thread_key, sent_at, sent_at, message_id),
            )

    def mark_message_failed(self, message_id: str, *, failure_reason: str, updated_at: str) -> None:
        with get_session() as session:
            execute(
                session,
                "UPDATE email_messages SET status = 'failed', failure_reason = ?, updated_at = ? WHERE id = ?",
                (failure_reason, updated_at, message_id),
            )

    def cancel_future_pending_messages(self, sequence_id: str, *, updated_at: str) -> None:
        with get_session() as session:
            execute(
                session,
                "UPDATE email_messages SET status = 'cancelled', updated_at = ? "
                "WHERE sequence_id = ? AND status = 'pending'",
                (updated_at, sequence_id),
            )

    def count_messages_for_campaign(self, campaign_id: str, *, status: str | None = None) -> int:
        query = (
            "SELECT COUNT(*) AS count FROM email_messages m "
            "JOIN lead_email_sequences s ON s.id = m.sequence_id "
            "WHERE s.campaign_id = ?"
        )
        params: list[Any] = [campaign_id]
        if status:
            query += " AND m.status = ?"
            params.append(status)
        with get_session() as session:
            row = fetch_one(session, query, params)
        return int(row["count"]) if row else 0

    def count_messages_by_status(self, status: str) -> int:
        with get_session() as session:
            row = fetch_one(session, "SELECT COUNT(*) AS count FROM email_messages WHERE status = ?", (status,))
        return int(row["count"]) if row else 0

    def count_sequences_by_status(self, *statuses: str) -> int:
        if not statuses:
            return 0
        placeholders = ", ".join("?" for _ in statuses)
        with get_session() as session:
            row = fetch_one(
                session,
                f"SELECT COUNT(*) AS count FROM lead_email_sequences WHERE status IN ({placeholders})",
                list(statuses),
            )
        return int(row["count"]) if row else 0

    def count_campaigns_by_status(self, *statuses: str) -> int:
        if not statuses:
            return 0
        placeholders = ", ".join("?" for _ in statuses)
        with get_session() as session:
            row = fetch_one(
                session,
                f"SELECT COUNT(*) AS count FROM email_campaigns WHERE status IN ({placeholders})",
                list(statuses),
            )
        return int(row["count"]) if row else 0

    def count_messages_since(self, status: str, *, since_iso: str, time_field: str = "updated_at") -> int:
        if time_field not in {"created_at", "updated_at", "scheduled_at", "sent_at"}:
            raise ValueError("Unsupported time field")
        with get_session() as session:
            row = fetch_one(
                session,
                f"SELECT COUNT(*) AS count FROM email_messages WHERE status = ? AND {time_field} >= ?",
                (status, since_iso),
            )
        return int(row["count"]) if row else 0

    def find_sent_message_by_lead_email_and_subject(self, lead_email: str, subject: str) -> dict[str, Any] | None:
        with get_session() as session:
            return fetch_one(
                session,
                "SELECT m.* FROM email_messages m "
                "JOIN lead_email_sequences s ON s.id = m.sequence_id "
                "WHERE m.status = 'sent' AND lower(s.lead_email) = lower(?) AND lower(m.subject) = lower(?) "
                "ORDER BY m.sent_at DESC LIMIT 1",
                (lead_email, subject),
            )

    # ── replies ───────────────────────────────────────────────────────────
    def has_reply_event(self, raw_ref: str) -> bool:
        with get_session() as session:
            row = fetch_one(session, "SELECT 1 AS hit FROM email_reply_events WHERE raw_ref = ? LIMIT 1", (raw_ref,))
        return row is not None

    def create_reply_event(self, payload: dict[str, Any]) -> None:
        cols = list(payload.keys())
        with get_session() as session:
            execute(
                session,
                f"INSERT INTO email_reply_events ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [payload[c] for c in cols],
            )

    def list_reply_events_for_sequence(self, sequence_id: str) -> list[dict[str, Any]]:
        with get_session() as session:
            return fetch_all(
                session,
                "SELECT * FROM email_reply_events WHERE sequence_id = ? ORDER BY received_at DESC",
                (sequence_id,),
            )

    def count_reply_events_since(self, since_iso: str) -> int:
        with get_session() as session:
            row = fetch_one(
                session,
                "SELECT COUNT(*) AS count FROM email_reply_events WHERE received_at >= ?",
                (since_iso,),
            )
        return int(row["count"]) if row else 0

    def list_recent_message_failures(self, *, since_iso: str, limit: int = 10) -> list[dict[str, Any]]:
        with get_session() as session:
            return fetch_all(
                session,
                """
                SELECT m.subject, m.failure_reason, m.updated_at, s.lead_email
                FROM email_messages m
                JOIN lead_email_sequences s ON s.id = m.sequence_id
                WHERE m.status = 'failed' AND m.updated_at >= ?
                ORDER BY m.updated_at DESC
                LIMIT ?
                """,
                (since_iso, limit),
            )

    def list_sent_messages_since(self, *, since_iso: str, limit: int = 100) -> list[dict[str, Any]]:
        with get_session() as session:
            return fetch_all(
                session,
                """
                SELECT
                  m.id,
                  m.subject,
                  m.sent_at,
                  s.lead_email,
                  s.lead_name,
                  s.hunt_id,
                  s.campaign_id
                FROM email_messages m
                JOIN lead_email_sequences s ON s.id = m.sequence_id
                WHERE m.status = 'sent' AND m.sent_at >= ?
                ORDER BY m.sent_at ASC, m.id ASC
                LIMIT ?
                """,
                (since_iso, limit),
            )

    def list_reply_events_since(self, *, since_iso: str, limit: int = 100) -> list[dict[str, Any]]:
        with get_session() as session:
            return fetch_all(
                session,
                """
                SELECT
                  r.id,
                  r.from_email,
                  r.subject,
                  r.snippet,
                  r.received_at,
                  s.lead_name,
                  s.hunt_id,
                  s.campaign_id
                FROM email_reply_events r
                JOIN lead_email_sequences s ON s.id = r.sequence_id
                WHERE r.received_at >= ?
                ORDER BY r.received_at DESC, r.id DESC
                LIMIT ?
                """,
                (since_iso, limit),
            )

    def list_message_failure_reasons(self, *, since_iso: str, limit: int = 5) -> list[dict[str, Any]]:
        with get_session() as session:
            return fetch_all(
                session,
                """
                SELECT failure_reason, COUNT(*) AS count
                FROM email_messages
                WHERE status = 'failed' AND updated_at >= ?
                GROUP BY failure_reason
                ORDER BY count DESC, failure_reason ASC
                LIMIT ?
                """,
                (since_iso, limit),
            )

    def get_template_performance_for_campaign(
        self,
        campaign_id: str,
        *,
        underperforming_min_assigned: int = 10,
        underperforming_min_reply_rate: float = 1.0,
    ) -> dict[str, dict[str, Any]]:
        sequences = [seq for seq in self.list_sequences_for_campaign(campaign_id) if seq.get("template_id")]
        if not sequences:
            return {}

        template_summary: dict[str, dict[str, Any]] = {}
        with get_session() as session:
            for sequence in sequences:
                template_id = str(sequence.get("template_id") or "")
                if not template_id:
                    continue
                summary = template_summary.setdefault(
                    template_id,
                    {
                        "template_id": template_id,
                        "template_group": str(sequence.get("template_group") or ""),
                        "generation_mode": str(sequence.get("generation_mode") or "template_pool"),
                        "assigned_count": 0,
                        "max_send_count": int(sequence.get("template_max_send_count") or 0),
                        "sent_count": 0,
                        "replied_count": 0,
                        "reply_rate": 0.0,
                        "remaining_capacity": 0,
                        "status": "warming_up",
                        "optimization_needed": False,
                        "recommended_action": "keep_collecting_data",
                        "reason": "Not enough delivery/reply data yet.",
                    },
                )
                summary["assigned_count"] += 1
                if sequence.get("status") == "replied":
                    summary["replied_count"] += 1

                row = fetch_one(
                    session,
                    "SELECT COUNT(*) AS count FROM email_messages WHERE sequence_id = ? AND status = 'sent'",
                    (sequence["id"],),
                )
                summary["sent_count"] += int(row["count"]) if row else 0

        for summary in template_summary.values():
            assigned = int(summary["assigned_count"])
            replied = int(summary["replied_count"])
            max_send_count = int(summary["max_send_count"])
            summary["reply_rate"] = round((replied / assigned) * 100, 2) if assigned else 0.0
            summary["remaining_capacity"] = max(max_send_count - assigned, 0)
            if max_send_count and assigned >= max_send_count:
                summary["status"] = "exhausted"
                summary["optimization_needed"] = True
                summary["recommended_action"] = "create_new_template_version"
                summary["reason"] = "Template reached the configured assignment cap."
            elif assigned >= underperforming_min_assigned and summary["reply_rate"] < underperforming_min_reply_rate:
                summary["status"] = "underperforming"
                summary["optimization_needed"] = True
                summary["recommended_action"] = "optimize_template_before_more_sends"
                summary["reason"] = (
                    f"Reply rate {summary['reply_rate']}% is below the threshold "
                    f"{underperforming_min_reply_rate}% after {assigned} assignments."
                )
            else:
                summary["status"] = "warming_up"
                summary["optimization_needed"] = False
                summary["recommended_action"] = "keep_collecting_data"
                summary["reason"] = "Continue sending until enough reply data accumulates."
        return template_summary
