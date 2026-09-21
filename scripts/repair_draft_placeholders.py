"""Repair already-stored drafts/hunts that still contain signature placeholders.

Drafts generated before placeholder sanitization ran at generation time keep
tokens like "[Your Phone]" in the stored JSON, which a human then reviews.
The generator and the draft-store boundary now sanitize on write; this script
cleans rows written earlier.

Idempotent: sequences without placeholders are left untouched, and manual
review decisions are preserved.

Usage:
    python scripts/repair_draft_placeholders.py [--dry-run] [--include-hunts]
    python scripts/repair_draft_placeholders.py --resign-email [--dry-run]
    python scripts/repair_draft_placeholders.py --fix-signature [--dry-run] [--include-hunts]
        Additionally replace the OLD sender address already rendered in
        signature contact lines with EMAIL_SIGNATURE_EMAIL (falls back to
        EMAIL_FROM_ADDRESS). Use after introducing/changing
        EMAIL_SIGNATURE_EMAIL so existing drafts show the new contact.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from emailing.body_format import find_placeholders, is_known_placeholder
from emailing.signature import (
    fix_generic_salutation,
    recipient_display_name,
    sanitize_outreach_text,
)
from emailing.draft_store import now_iso
from persistence.db import execute, fetch_all, get_session


def _resign_emails(emails: list, old_email: str, new_email: str) -> tuple[list, int]:
    """Swap an already-rendered sender address for the signature contact."""
    if not old_email or not new_email or old_email == new_email:
        return emails, 0
    replaced = 0
    out: list = []
    for item in emails or []:
        if isinstance(item, dict):
            entry = dict(item)
            for field in ("subject", "body_text"):
                text = str(entry.get(field, "") or "")
                if old_email in text:
                    entry[field] = text.replace(old_email, new_email)
                    replaced += 1
            out.append(entry)
        else:
            out.append(item)
    return out, replaced


def _clean_emails(emails: list, target: dict, settings, company_name: str = "") -> tuple[list, list[str]]:
    """Return (cleaned emails, report of what was touched).

    Report entries: the placeholder token that was filled in,
    ``UNFILLABLE:<token>`` for a token removed from the text, or
    ``salutation:generic->company_team`` when a generic salutation was
    rewritten (Dear Sir/Madam -> Dear {company} team).
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
            updated = original
            tokens = find_placeholders(original)
            if tokens:
                updated = sanitize_outreach_text(original, settings, recipient_name=recipient)
                for token in tokens:
                    report.append(token if is_known_placeholder(token) else f"UNFILLABLE:{token}")
            if field == "body_text":
                fixed = fix_generic_salutation(updated, company_name)
                if fixed != updated:
                    updated = fixed
                    report.append("salutation:generic->company_team")  # 让该行进入 repaired 统计
            if updated != original:
                entry[field] = updated
        cleaned.append(entry)
    return cleaned, report


def _repair_drafts(settings, *, dry_run: bool) -> tuple[int, int, list[str]]:
    with get_session() as session:
        rows = fetch_all(session, "SELECT id, company_name, target, emails FROM email_drafts")

    repaired = 0
    unfillable: list[str] = []
    for row in rows:
        emails, report = _clean_emails(
            row.get("emails") or [], row.get("target") or {}, settings,
            company_name=str(row.get("company_name", "") or ""),
        )
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
            company = str((sequence.get("lead") or {}).get("company_name", "") or "")
            emails, report = _clean_emails(
                sequence.get("emails") or [], sequence.get("target") or {}, settings,
                company_name=company,
            )
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
    parser.add_argument("--resign-email", action="store_true",
                        help="把已渲染的旧发信地址替换为 EMAIL_SIGNATURE_EMAIL（回退 EMAIL_FROM_ADDRESS）")
    parser.add_argument("--fix-signature", action="store_true",
                        help="对存量草稿补齐/纠正签名块：移除虚构落款、追加配置签名（幂等，保留审批状态）")
    args = parser.parse_args()

    settings = get_settings()
    signature_email = (settings.email_signature_email or settings.email_from_address or "").strip()
    print(f"发信人身份: name={settings.email_signature_name!r} title={settings.email_signature_title!r} "
          f"phone={settings.email_signature_phone!r} 联系邮箱={signature_email!r} "
          f"(发信通道 {settings.email_from_address!r})")

    print("\n== email_drafts ==")
    if args.fix_signature:
        from emailing.signature import ensure_signature_block

        with get_session() as session:
            rows = fetch_all(session, "SELECT id, company_name, status, emails, target FROM email_drafts")
        fixed = 0
        for row in rows:
            recipient = row.get("target") or {}
            emails_changed = 0
            cleaned: list = []
            for item in row.get("emails") or []:
                if not isinstance(item, dict):
                    cleaned.append(item)
                    continue
                entry = dict(item)
                body = str(entry.get("body_text", "") or "")
                company = str(row.get("company_name", "") or "")
                fixed_body = ensure_signature_block(
                    fix_generic_salutation(
                        sanitize_outreach_text(body, settings), company),
                    settings,
                )
                if fixed_body != body:
                    entry["body_text"] = fixed_body
                    emails_changed += 1
                cleaned.append(entry)
            if emails_changed:
                fixed += 1
                print(f"  draft {str(row['id'])[:8]} {str(row.get('company_name'))[:32]!r} "
                      f"(status={row.get('status')}): {emails_changed} 封正文签名已补齐/纠正")
                if not args.dry_run:
                    with get_session() as session:
                        execute(
                            session,
                            "UPDATE email_drafts SET emails = CAST(? AS jsonb), updated_at = ? WHERE id = ?",
                            (json.dumps(cleaned, ensure_ascii=False), now_iso(), row["id"]),
                        )
        print(f"{'[dry-run] ' if args.dry_run else ''}签名修复 {fixed}/{len(rows)} 条")
        return 0
    if args.resign_email:
        with get_session() as session:
            rows = fetch_all(session, "SELECT id, company_name, emails FROM email_drafts")
        resigned = 0
        for row in rows:
            emails, n = _resign_emails(row.get("emails") or [], settings.email_from_address, signature_email)
            if n:
                resigned += 1
                print(f"  draft {str(row['id'])[:8]} {str(row.get('company_name'))[:32]!r}: {n} 处联系邮箱 → {signature_email}")
                if not args.dry_run:
                    with get_session() as session:
                        execute(
                            session,
                            "UPDATE email_drafts SET emails = CAST(? AS jsonb), updated_at = ? WHERE id = ?",
                            (json.dumps(emails, ensure_ascii=False), now_iso(), row["id"]),
                        )
        print(f"{'[dry-run] ' if args.dry_run else ''}换签邮箱 {resigned} 条")
        return 0
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
