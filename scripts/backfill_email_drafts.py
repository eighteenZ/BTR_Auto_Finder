"""Backfill email_drafts from legacy hunts.data JSONB.

Reads ``hunts.data -> result.email_sequences`` (the pre-split storage where
drafts, approvals and send state lived inside the hunt row) and upserts them
into the ``email_drafts`` contract table. Idempotent: keyed on
(hunt_id, sequence_index); manual decisions recorded in the JSON are imported
and never overwritten by later re-runs.

Usage:
    python scripts/backfill_email_drafts.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emailing.draft_store import EmailDraftStore, now_iso
from persistence.db import fetch_all, get_session
from persistence.lead_identity import compute_lead_key


def _lead_id_for_key(lead_key: str) -> str:
    if not lead_key:
        return ""
    with get_session() as session:
        row = fetch_all(session, "SELECT id FROM leads WHERE lead_key = ? LIMIT 1", (lead_key,))
    return str(row[0]["id"]) if row else ""


def _to_draft_payload(hunt_id: str, index: int, seq: dict) -> dict:
    lead = seq.get("lead") or {}
    lead_key = compute_lead_key(lead) if lead else ""
    manual = seq.get("manual_review") or {}
    status = str(manual.get("decision", "") or "") or "draft"
    return {
        "id": str(uuid4()),
        "hunt_id": hunt_id,
        "sequence_index": index,
        "lead_id": _lead_id_for_key(lead_key),
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    with get_session() as session:
        hunts = fetch_all(session, "SELECT id, data FROM hunts ORDER BY created_at ASC")

    store = EmailDraftStore()
    total_sequences = 0
    written = 0
    for hunt in hunts:
        data = hunt.get("data") or {}
        result = data.get("result") or {}
        sequences = result.get("email_sequences") or []
        for index, seq in enumerate(sequences):
            if not isinstance(seq, dict):
                continue
            total_sequences += 1
            payload = _to_draft_payload(str(hunt["id"]), index, seq)
            if args.dry_run:
                print(f"[dry-run] hunt={hunt['id'][:8]} index={index} "
                      f"company={payload['company_name']!r} status={payload['status']}")
                continue
            store.upsert_draft(payload)
            written += 1

    print(f"{'dry-run: ' if args.dry_run else ''}sequences found={total_sequences}, written={written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
