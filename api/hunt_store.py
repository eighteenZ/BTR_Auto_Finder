"""Hunt persistence — PostgreSQL-backed storage for hunt metadata and results.

Each hunt is stored as a row in ``hunts`` with the full runtime dict in a JSONB
column. Status/index columns are duplicated for querying. The public helper
signatures are kept stable so existing callers need no changes.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from persistence.db import get_session
from persistence.models import Hunt
from persistence.utils import now_iso

logger = logging.getLogger(__name__)


def _apply_columns(row: Hunt, hunt_data: dict[str, Any], *, updated_at: str) -> None:
    row.status = str(hunt_data.get("status", "") or "pending")
    row.current_stage = str(hunt_data.get("current_stage", "") or "")
    row.hunt_round = int(hunt_data.get("hunt_round", 0) or 0)
    row.leads_count = int(hunt_data.get("leads_count", 0) or 0)
    row.email_sequences_count = int(hunt_data.get("email_sequences_count", 0) or 0)
    row.error = str(hunt_data.get("error", "") or "")
    row.website_url = str(hunt_data.get("website_url", "") or "")
    row.updated_at = updated_at


def save_hunt(hunt_id: str, hunt_data: dict[str, Any]) -> None:
    """Persist a hunt (upsert) to PostgreSQL."""
    try:
        with get_session() as session:
            row = session.get(Hunt, hunt_id)
            if row is None:
                row = Hunt(
                    id=hunt_id,
                    created_at=str(hunt_data.get("created_at", "") or now_iso()),
                )
                session.add(row)
            _apply_columns(row, hunt_data, updated_at=now_iso())
            row.data = {"hunt_id": hunt_id, **hunt_data}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[HuntStore] Failed to save hunt %s: %s", hunt_id[:8], exc)


def load_all_hunts(*, mark_interrupted: bool = False) -> dict[str, dict[str, Any]]:
    """Load all hunts keyed by hunt_id.

    ``mark_interrupted=True`` is only used during process startup recovery and
    converts any running/pending hunts to failed.
    """
    hunts: dict[str, dict[str, Any]] = {}
    try:
        with get_session() as session:
            rows = session.execute(select(Hunt)).scalars().all()
            for row in rows:
                data = dict(row.data or {})
                data.pop("hunt_id", None)
                if mark_interrupted and data.get("status") in ("running", "pending"):
                    data["status"] = "failed"
                    data["error"] = "Process was interrupted (server restarted)"
                    data["completed_at"] = now_iso()
                    _apply_columns(row, data, updated_at=now_iso())
                    row.data = {"hunt_id": row.id, **data}
                    logger.info("[HuntStore] Marked interrupted hunt %s as failed", row.id[:8])
                hunts[row.id] = data
    except Exception as exc:  # noqa: BLE001
        logger.warning("[HuntStore] Failed to load hunts from database: %s", exc)
        return {}
    if hunts:
        logger.info("[HuntStore] Loaded %d hunt(s) from database", len(hunts))
    return hunts


def delete_hunt(hunt_id: str) -> None:
    try:
        with get_session() as session:
            row = session.get(Hunt, hunt_id)
            if row is not None:
                session.delete(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[HuntStore] Failed to delete hunt %s: %s", hunt_id[:8], exc)


def load_hunt(hunt_id: str) -> dict[str, Any] | None:
    try:
        with get_session() as session:
            row = session.get(Hunt, hunt_id)
            if row is None:
                return None
            data = dict(row.data or {})
            data.pop("hunt_id", None)
            return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("[HuntStore] Failed to load hunt %s: %s", hunt_id[:8], exc)
        return None
