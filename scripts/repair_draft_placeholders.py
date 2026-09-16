"""Repair already-stored drafts/hunts that still contain signature placeholders.

Drafts generated before placeholder sanitization ran at generation time keep
tokens like "[Your Phone]" in the stored JSON, which a human then reviews.
The generator and the draft-store boundary now sanitize on write; this script
cleans rows written earlier.

Idempotent: sequences without placeholders are left untouched, and manual
review decisions are preserved.

Usage:
    python scripts/repair_draft_placeholders.py [--dry-run] [--include-hunts]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from emailing.body_format import find_placeholders, is_known_placeholder
from emailing.signature import recipient_display_name, sanitize_outreach_text
from emailing.draft_store import now_iso
from persistence.db import execute, fetch_all, get_session


def _clean_emails(emails: list, target: dict, settings) -> tuple[list, list[str]]:
    """Return (cleaned emails, report of what was touched).

    Report entries are either the placeholder token that was filled in, or
    ``UNFILLABLE:<token>`` for a token that had to be removed from the text.
    """
    recipient = recipient_display_name(target or {})
    cleaned: list = []
    report: list[str] = []
    for item in emails or []:
        if not isinstance(item, dict):
            cleaned.append(item)
            continue
        entry = dict(item)
        for field in ("subject", "body_text"):
            original = str(entry.get(field, "") or "")
            tokens = find_placeholders(original)
            if not tokens:
                continue
            entry[field] = sanitize_outreach_text(original, settings, recipient_name=recipient)
            for token in tokens:
                report.append(token if is_known_placeholder(token) else f"UNFILLABLE:{token}")
        cleaned.append(entry)
    return cleaned, report


def _repair_drafts(settings, *, dry_run: bool) -> tuple[int, int, list[str]]:
    with get_session() as session:
        rows = fetch_all(session, "SELECT id, company_name, target, emails FROM email_drafts")

    repaired = 0
    unfillable: list[str] = []
    for row in rows:
        emails, report = _clean_emails(row.get("emails") or [], row.get("target") or {}, settings)
        if not report:
            continue
        removed = [t for t in report if t.startswith("UNFILLABLE:")]
        unfillable.extend(removed)
        repaired += 1
        filled = sorted({t for t in report if not t.startswith("UNFILLABLE:")})
        print(f"  draft {str(row['id'])[:8]} {str(row.get('company_name'))[:32]!r}: "
              f"填充 {len(filled)} 种 {filled}"
              + (f" | 移除无法填充 {sorted(set(removed))}" if removed else ""))
        if not dry_run:
            with get_session() as session:
                execute(
                    session,
                    "UPDATE email_drafts SET emails = CAST(? AS jsonb), updated_at = ? WHERE id = ?",
                    (json.dumps(emails, ensure_ascii=False), now_iso(), row["id"]),
                )
    return repaired, len(rows), unfillable


def _repair_hunts(settings, *, dry_run: bool) -> int:
    with get_session() as session:
        rows = fetch_all(session, "SELECT id, data FROM hunts")

    repaired = 0
    for row in rows:
        data = row.get("data") or {}
        result = data.get("result") or {}
        sequences = result.get("email_sequences")
        if not isinstance(sequences, list) or not sequences:
            continue
        hunt_touched = False
        for sequence in sequences:
            if not isinstance(sequence, dict):
                continue
            emails, report = _clean_emails(sequence.get("emails") or [], sequence.get("target") or {}, settings)
            if report:
                sequence["emails"] = emails
                hunt_touched = True
        if hunt_touched:
            repaired += 1
            print(f"  hunt {str(row['id'])[:8]}: 已清洗内嵌 email_sequences")
            if not dry_run:
                with get_session() as session:
                    execute(
                        session,
                        "UPDATE hunts SET data = CAST(? AS jsonb) WHERE id = ?",
                        (json.dumps(data, ensure_ascii=False, default=str), row["id"]),
                    )
    return repaired


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-hunts", action="store_true", help="同时清洗 hunts.data 内嵌的草稿副本")
    args = parser.parse_args()

    settings = get_settings()
    print(f"发信人身份: name={settings.email_signature_name!r} title={settings.email_signature_title!r} "
          f"phone={settings.email_signature_phone!r} email={settings.email_from_address!r}")

    print("\n== email_drafts ==")
    repaired, total, unfillable = _repair_drafts(settings, dry_run=args.dry_run)
    print(f"{'[dry-run] ' if args.dry_run else ''}已清洗 {repaired}/{total} 条草稿")

    if args.include_hunts:
        print("\n== hunts.data.result.email_sequences ==")
        hunts_repaired = _repair_hunts(settings, dry_run=args.dry_run)
        print(f"{'[dry-run] ' if args.dry_run else ''}已清洗 {hunts_repaired} 个 hunt")

    if unfillable:
        print(f"\n注意：{len(unfillable)} 处无法填充的占位符已从正文移除，相关草稿已标记需人工复核: "
              f"{sorted(set(t.split(':', 1)[1] for t in unfillable))[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
