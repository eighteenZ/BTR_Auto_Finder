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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emailing.draft_store import EmailDraftStore
from persistence.db import fetch_all, get_session


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
        total_sequences += len(sequences)
        if args.dry_run:
            for index, seq in enumerate(sequences):
                if isinstance(seq, dict):
                    lead = seq.get("lead") or {}
                    decision = (seq.get("manual_review") or {}).get("decision", "draft")
                    print(f"[dry-run] hunt={hunt['id'][:8]} index={index} "
                          f"company={lead.get('company_name')!r} status={decision}")
            continue
        written += store.upsert_from_sequences(str(hunt["id"]), sequences)

    print(f"{'dry-run: ' if args.dry_run else ''}sequences found={total_sequences}, written={written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
