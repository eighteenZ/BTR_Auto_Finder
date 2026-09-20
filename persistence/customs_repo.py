"""Customs pipeline repository — record ingestion, lead linking, and
procurement-intent classification.

``classify_procurement`` is a pure, DB-free rule (unit-tested directly); the
rest are thin SQLAlchemy helpers in the ``lead_repo`` style.

Procurement statuses:
- ``active``  last import within ``active_months`` — buying now
- ``recent``  last import within 2x ``active_months`` — active buyer, slowing
- ``stale``   has records, all older than that
- ``none``    no records for this lead
- ``unknown`` never checked
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from persistence.db import get_session
from persistence.lead_identity import compute_lead_key
from persistence.lead_repo import upsert_leads
from persistence.models import CustomsImportRecord, CustomsSyncRun, CustomsWatchItem, Lead
from persistence.utils import now_iso
from tools.customs_sources.importyeti_source import TradeRecord

logger = logging.getLogger(__name__)

PROCUREMENT_ACTIVE = "active"
PROCUREMENT_RECENT = "recent"
PROCUREMENT_STALE = "stale"
PROCUREMENT_NONE = "none"
PROCUREMENT_UNKNOWN = "unknown"

_TIER_ORDER = {"reject": 0, "low": 1, "medium": 2, "high": 3}


def classify_procurement(
    records: list[dict],
    *,
    today: date,
    active_months: int = 6,
) -> dict:
    """Derive procurement intent from a lead's dated trade records.

    ``records`` items need at least ``arrival_date`` (ISO ``YYYY-MM-DD`` or
    empty). The 90-day shipment count is reported separately so callers can
    apply a volume threshold for priority bumps.
    """
    dated: list[date] = []
    for row in records:
        raw = str((row or {}).get("arrival_date") or "")
        try:
            dated.append(date.fromisoformat(raw))
        except ValueError:
            continue

    count_90d = sum(1 for d in dated if d >= today - timedelta(days=90))
    active_window = max(30, int(active_months) * 30)

    if not dated:
        result = {
            "procurement_status": PROCUREMENT_NONE,
            "last_import_at": "",
            "import_count_90d": count_90d,
            "days_since_last_import": None,
        }
    else:
        last = max(dated)
        days_since = (today - last).days
        if days_since <= active_window:
            status = PROCUREMENT_ACTIVE
        elif days_since <= active_window * 2:
            status = PROCUREMENT_RECENT
        else:
            status = PROCUREMENT_STALE
        result = {
            "procurement_status": status,
            "last_import_at": last.isoformat(),
            "import_count_90d": count_90d,
            "days_since_last_import": days_since,
        }
    result["active_window_days"] = active_window
    return result


def _lead_dict_for_record(rec: TradeRecord, *, lead_country_code: str) -> dict:
    return {
        "company_name": rec.company_name,
        "website": f"https://{rec.domain}" if rec.domain else "",
        "country_code": lead_country_code,
    }


def ingest_trade_records(
    records: list[TradeRecord],
    *,
    lead_country_code: str = "us",
    session=None,
) -> dict:
    """Insert hash-deduped records, upsert matching leads, link records→leads.

    ImportYeti consignees are US importers, so new leads default to
    ``lead_country_code="us"``; the same default feeds the ``x:`` fallback
    lead key so company-name variants collide consistently.
    """
    summary = {
        "records_ingested": 0,
        "records_skipped": 0,
        "new_leads": 0,
        "leads_touched": 0,
    }
    if not records:
        return summary

    def _impl(s) -> dict:
        now = now_iso()
        keyed: list[tuple[str, TradeRecord]] = []
        lead_dicts: dict[str, dict] = {}
        for rec in records:
            lead_dict = _lead_dict_for_record(rec, lead_country_code=lead_country_code)
            key = compute_lead_key(lead_dict)
            keyed.append((key, rec))
            if key and key not in lead_dicts:
                lead_dicts[key] = lead_dict

        canonical: dict[str, dict] = {}
        if lead_dicts:
            for row in upsert_leads(list(lead_dicts.values()), session=s):
                canonical[row["lead_key"]] = row
        summary["new_leads"] = sum(1 for row in canonical.values() if not row.get("reused"))
        summary["leads_touched"] = len(canonical)

        for key, rec in keyed:
            lead_row = canonical.get(key) or {}
            inserted_id = s.execute(
                pg_insert(CustomsImportRecord)
                .values(
                    id=uuid.uuid4().hex,
                    lead_id=str(lead_row.get("lead_id") or ""),
                    lead_key=key,
                    company_name=rec.company_name,
                    domain=rec.domain,
                    consignee_name=rec.consignee_name,
                    supplier_name=rec.supplier_name,
                    country=rec.country,
                    hs_code=rec.hs_code,
                    product_description=rec.product_description,
                    arrival_date=rec.arrival_date,
                    quantity=rec.quantity,
                    weight=rec.weight,
                    source=rec.source,
                    source_ref=rec.source_ref,
                    record_hash=rec.record_hash,
                    raw=rec.raw,
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=["record_hash"])
                .returning(CustomsImportRecord.id)
            ).scalar_one_or_none()
            # RETURNING yields no row when the unique hash already exists
            # (rowcount is unreliable for skipped ON CONFLICT inserts).
            if inserted_id:
                summary["records_ingested"] += 1
            else:
                summary["records_skipped"] += 1
        return summary

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


def recompute_lead_procurement(
    lead_id: str,
    *,
    today: date,
    active_months: int = 6,
    min_shipments_90d: int = 2,
    session=None,
) -> dict | None:
    """Recompute procurement fields for one lead from its stored records.

    An ``active`` lead with enough 90-day volume gets its ``priority_tier``
    raised to ``high`` (never lowered); ``customs_score`` only exists inside
    the hunt pipeline, so the persisted signal here is the tier.
    """
    def _impl(s) -> dict | None:
        rows = s.execute(
            select(CustomsImportRecord.arrival_date).where(
                CustomsImportRecord.lead_id == lead_id
            )
        ).all()
        classification = classify_procurement(
            [{"arrival_date": r[0]} for r in rows],
            today=today,
            active_months=active_months,
        )
        lead = s.get(Lead, lead_id)
        if lead is None:
            return None
        lead.procurement_status = classification["procurement_status"]
        lead.last_import_at = classification["last_import_at"]
        lead.import_count_90d = classification["import_count_90d"]
        lead.customs_last_checked_at = now_iso()
        if classification["procurement_status"] == PROCUREMENT_ACTIVE:
            volume_ok = classification["import_count_90d"] >= max(1, int(min_shipments_90d))
            current = str(lead.priority_tier or "").lower()
            if volume_ok and _TIER_ORDER.get(current, 1) < _TIER_ORDER["high"]:
                lead.priority_tier = "high"
        return classification

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


def list_leads_for_check(limit: int = 10, *, session=None) -> list[dict]:
    """Leads due a provider-page verification, least-recently checked first."""
    def _impl(s) -> list[dict]:
        rows = s.execute(
            select(Lead)
            .where(Lead.company_name != "")
            .order_by(Lead.customs_last_checked_at.asc(), Lead.id.asc())
            .limit(max(1, int(limit)))
        ).scalars().all()
        return [
            {
                "lead_id": row.id,
                "company_name": row.company_name,
                "website": row.website,
                "country_code": row.country_code,
                "procurement_status": row.procurement_status,
                "customs_data": row.customs_data,
            }
            for row in rows
        ]

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


def save_lead_check(
    lead_id: str,
    *,
    status: str,
    last_import_at: str = "",
    import_count_90d: int = 0,
    summary: str = "",
    session=None,
) -> bool:
    """Persist a provider-page verification result for one lead.

    ``status`` may be empty to keep the stored value (e.g. when a paywalled
    lookup found nothing — absence of evidence is not evidence of absence).
    """
    def _impl(s) -> bool:
        lead = s.get(Lead, lead_id)
        if lead is None:
            return False
        if status:
            lead.procurement_status = status
        if last_import_at:
            lead.last_import_at = last_import_at
        if import_count_90d:
            lead.import_count_90d = int(import_count_90d)
        if summary:
            lead.customs_data = summary
        lead.customs_last_checked_at = now_iso()
        return True

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


def list_lead_ids_with_records(*, session=None) -> list[str]:
    """Distinct lead ids that have at least one stored trade record."""
    def _impl(s) -> list[str]:
        rows = s.execute(
            select(CustomsImportRecord.lead_id)
            .where(CustomsImportRecord.lead_id != "")
            .distinct()
        ).all()
        return [r[0] for r in rows if r[0]]

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


def list_procurement_leads(
    *,
    status: str = "",
    limit: int = 100,
    offset: int = 0,
    session=None,
) -> list[dict]:
    """Leads with their procurement fields, optionally filtered by status."""
    def _impl(s) -> list[dict]:
        stmt = (
            select(Lead)
            .where(Lead.company_name != "")
            .order_by(Lead.last_import_at.desc(), Lead.import_count_90d.desc(), Lead.id.asc())
        )
        if status:
            stmt = stmt.where(Lead.procurement_status == status)
        stmt = stmt.limit(max(1, int(limit))).offset(max(0, int(offset)))
        rows = s.execute(stmt).scalars().all()
        return [
            {
                "lead_id": row.id,
                "lead_key": row.lead_key,
                "company_name": row.company_name,
                "domain": row.domain,
                "website": row.website,
                "country_code": row.country_code,
                "emails": list(row.emails or []),
                "priority_tier": row.priority_tier,
                "procurement_status": row.procurement_status,
                "last_import_at": row.last_import_at,
                "import_count_90d": row.import_count_90d,
                "customs_last_checked_at": row.customs_last_checked_at,
                "customs_data": row.customs_data,
                "fit_score": row.fit_score,
                "contactability_score": row.contactability_score,
            }
            for row in rows
        ]

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


# ── Daily-run bookkeeping ────────────────────────────────────────────────────


def start_run(run_date: str, *, trigger: str = "scheduled", session=None) -> str:
    run_id = uuid.uuid4().hex
    now = now_iso()

    def _impl(s) -> str:
        s.add(
            CustomsSyncRun(
                id=run_id,
                run_date=run_date,
                trigger=trigger,
                status="running",
                started_at=now,
            )
        )
        return run_id

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


def finish_run(
    run_id: str,
    *,
    status: str,
    stats: dict | None = None,
    error: str = "",
    files_processed: int = 0,
    records_ingested: int = 0,
    leads_checked: int = 0,
    leads_active: int = 0,
    new_leads: int = 0,
    session=None,
) -> None:
    def _impl(s) -> None:
        run = s.get(CustomsSyncRun, run_id)
        if run is None:
            return
        run.status = status
        run.stats = stats or {}
        run.error = error
        run.files_processed = int(files_processed)
        run.records_ingested = int(records_ingested)
        run.leads_checked = int(leads_checked)
        run.leads_active = int(leads_active)
        run.new_leads = int(new_leads)
        run.finished_at = now_iso()

    if session is not None:
        _impl(session)
        return
    with get_session() as own:
        _impl(own)


def completed_run_for_date(run_date: str, *, session=None) -> str | None:
    """Return the id of an already-successful run for ``run_date``, if any."""
    def _impl(s) -> str | None:
        row = s.execute(
            select(CustomsSyncRun.id)
            .where(CustomsSyncRun.run_date == run_date, CustomsSyncRun.status == "ok")
            .order_by(CustomsSyncRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return row

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


def list_runs(limit: int = 20, *, session=None) -> list[dict]:
    def _impl(s) -> list[dict]:
        rows = s.execute(
            select(CustomsSyncRun)
            .order_by(CustomsSyncRun.started_at.desc())
            .limit(max(1, int(limit)))
        ).scalars().all()
        return [
            {
                "id": row.id,
                "run_date": row.run_date,
                "trigger": row.trigger,
                "status": row.status,
                "files_processed": row.files_processed,
                "records_ingested": row.records_ingested,
                "leads_checked": row.leads_checked,
                "leads_active": row.leads_active,
                "new_leads": row.new_leads,
                "stats": row.stats,
                "error": row.error,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
            }
            for row in rows
        ]

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


# ── Watchlist ────────────────────────────────────────────────────────────────


def list_watch_items(*, enabled_only: bool = False, session=None) -> list[dict]:
    def _impl(s) -> list[dict]:
        stmt = select(CustomsWatchItem).order_by(CustomsWatchItem.hs_code.asc())
        if enabled_only:
            stmt = stmt.where(CustomsWatchItem.enabled == 1)
        rows = s.execute(stmt).scalars().all()
        return [
            {
                "id": row.id,
                "hs_code": row.hs_code,
                "product_keywords": list(row.product_keywords or []),
                "countries": list(row.countries or []),
                "note": row.note,
                "enabled": bool(row.enabled),
            }
            for row in rows
        ]

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


def add_watch_item(
    *,
    hs_code: str,
    product_keywords: list[str] | None = None,
    countries: list[str] | None = None,
    note: str = "",
    session=None,
) -> dict:
    def _impl(s) -> dict:
        now = now_iso()
        row = s.execute(
            pg_insert(CustomsWatchItem)
            .values(
                id=uuid.uuid4().hex,
                hs_code=str(hs_code).strip(),
                product_keywords=list(product_keywords or []),
                countries=list(countries or []),
                note=str(note),
                enabled=1,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["hs_code"],
                set_={
                    "product_keywords": list(product_keywords or []),
                    "countries": list(countries or []),
                    "note": str(note),
                    "enabled": 1,
                    "updated_at": now,
                },
            )
            .returning(CustomsWatchItem)
        ).scalar_one()
        return {
            "id": row.id,
            "hs_code": row.hs_code,
            "product_keywords": list(row.product_keywords or []),
            "countries": list(row.countries or []),
            "note": row.note,
            "enabled": bool(row.enabled),
        }

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


def remove_watch_item(item_id: str, *, session=None) -> bool:
    def _impl(s) -> bool:
        row = s.get(CustomsWatchItem, item_id)
        if row is None:
            return False
        s.delete(row)
        return True

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)
