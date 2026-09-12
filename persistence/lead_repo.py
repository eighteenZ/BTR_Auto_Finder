"""Lead repository — persistent, deduplicated, reusable lead store.

The repository is the single source of truth for acquired leads. Hunts link
to leads through ``hunt_leads`` and ``lead_sightings`` instead of storing
duplicate copies.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from persistence.db import get_session
from persistence.lead_identity import compute_lead_key, normalize_domain, normalize_email
from persistence.models import HuntLead, Lead, LeadSighting
from persistence.utils import now_iso

_JSON_LIST_FIELDS = ("emails", "phone_numbers", "decision_makers", "evidence")
_JSON_DICT_FIELDS = ("social_media",)


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _union_list(existing: list, incoming: list) -> list:
    """Order-preserving union that also collapses duplicate JSON objects."""
    merged: list = []
    seen: set[str] = set()
    for item in [*existing, *incoming]:
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(item)
    return merged


def _row_to_dict(row: Lead) -> dict[str, Any]:
    """Return the lead as a plain dict, preserving unknown original keys."""
    base: dict[str, Any] = dict(row.raw or {})
    base.update(
        {
            "company_name": row.company_name,
            "website": row.website,
            "industry": row.industry,
            "country_code": row.country_code,
            "address": row.address,
            "emails": list(row.emails or []),
            "phone_numbers": list(row.phone_numbers or []),
            "social_media": dict(row.social_media or {}),
            "decision_makers": list(row.decision_makers or []),
            "customs_data": row.customs_data,
            "evidence": list(row.evidence or []),
            "match_score": row.match_score,
            "fit_score": row.fit_score,
            "contactability_score": row.contactability_score,
            "priority_tier": row.priority_tier,
            "lead_id": row.id,
            "lead_key": row.lead_key,
            "domain": row.domain,
            "first_seen_at": row.first_seen_at,
            "last_seen_at": row.last_seen_at,
            "seen_count": row.seen_count,
        }
    )
    return base


def _apply_new_values(row: Lead, lead: dict) -> None:
    """Fill in scalar fields and union JSON fields with newly discovered data."""
    row.raw = {**dict(row.raw or {}), **lead}

    scalar_fields = {
        "company_name": "company_name",
        "website": "website",
        "industry": "industry",
        "country_code": "country_code",
        "address": "address",
        "customs_data": "customs_data",
        "priority_tier": "priority_tier",
    }
    for attr, key in scalar_fields.items():
        incoming = lead.get(key)
        if incoming and not getattr(row, attr):
            setattr(row, attr, str(incoming))

    domain = normalize_domain(lead.get("website", ""))
    if domain and not row.domain:
        row.domain = domain

    for field in _JSON_LIST_FIELDS:
        incoming = _as_list(lead.get(field))
        if incoming:
            setattr(row, field, _union_list(list(getattr(row, field) or []), incoming))

    for field in _JSON_DICT_FIELDS:
        incoming = _as_dict(lead.get(field))
        if incoming:
            setattr(row, field, {**dict(getattr(row, field) or {}), **incoming})

    for field in ("match_score", "fit_score", "contactability_score"):
        incoming = lead.get(field)
        if incoming is not None and not getattr(row, field):
            try:
                setattr(row, field, float(incoming))
            except (TypeError, ValueError):
                pass


def _new_lead_row(lead_key: str, lead: dict, *, hunt_id: str, now: str) -> Lead:
    row = Lead(
        id=uuid.uuid4().hex,
        lead_key=lead_key,
        first_seen_at=now,
        last_seen_at=now,
        seen_count=1,
        first_hunt_id=hunt_id,
        last_hunt_id=hunt_id,
        raw=dict(lead),
    )
    _apply_new_values(row, lead)
    return row


def _record_sighting(
    session: Session, lead_id: str, hunt_id: str, source_keyword: str, now: str
) -> None:
    if not hunt_id:
        return
    session.execute(
        _pg_insert(LeadSighting)
        .values(
            lead_id=lead_id,
            hunt_id=hunt_id,
            source_keyword=str(source_keyword or ""),
            seen_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_lead_sighting")
    )


def _record_hunt_lead(session: Session, hunt_id: str, lead_id: str, reused: bool, now: str) -> None:
    if not hunt_id:
        return
    session.execute(
        _pg_insert(HuntLead)
        .values(hunt_id=hunt_id, lead_id=lead_id, reused=1 if reused else 0, added_at=now)
        .on_conflict_do_nothing(index_elements=["hunt_id", "lead_id"])
    )


def _pg_insert(model):
    from sqlalchemy.dialects.postgresql import insert

    return insert(model)


def upsert_leads(
    leads: list[dict[str, Any]],
    *,
    hunt_id: str = "",
    source_keyword: str = "",
    dedup_mode: str = "reuse",
    session: Session | None = None,
) -> list[dict[str, Any]]:
    """Persist leads, merging duplicates and returning canonical lead dicts.

    ``dedup_mode``:
      - ``reuse``: existing leads are merged and returned with ``reused=True``
      - ``skip``: existing leads are recorded as sightings but not returned
      - ``off``: existing leads are merged but always returned as new
    """
    if session is not None:
        return _upsert_impl(leads, hunt_id, source_keyword, dedup_mode, session)
    with get_session() as own:
        return _upsert_impl(leads, hunt_id, source_keyword, dedup_mode, own)


def _upsert_impl(
    leads: list[dict[str, Any]],
    hunt_id: str,
    source_keyword: str,
    dedup_mode: str,
    session: Session,
) -> list[dict[str, Any]]:
    now = now_iso()
    out: list[dict[str, Any]] = []
    handled: dict[str, dict[str, Any]] = {}

    for lead in leads:
        if not isinstance(lead, dict):
            continue
        key = compute_lead_key(lead)

        if key and key in handled:
            _merge_dicts(handled[key], lead)
            continue

        if not key:
            out.append({**lead, "reused": False, "lead_key": ""})
            continue

        existing = session.execute(select(Lead).where(Lead.lead_key == key)).scalar_one_or_none()
        if existing is None:
            row = _new_lead_row(key, lead, hunt_id=hunt_id, now=now)
            session.add(row)
            session.flush()
            canonical = _row_to_dict(row)
            reused = False
        else:
            _apply_new_values(existing, lead)
            existing.last_seen_at = now
            existing.seen_count = int(existing.seen_count or 1) + 1
            if hunt_id:
                existing.last_hunt_id = hunt_id
            canonical = _row_to_dict(existing)
            reused = True

        _record_sighting(session, canonical["lead_id"], hunt_id, source_keyword, now)

        if dedup_mode == "skip" and reused:
            handled[key] = canonical
            continue

        if dedup_mode == "off":
            reused = False
        canonical["reused"] = reused
        handled[key] = canonical
        _record_hunt_lead(session, hunt_id, canonical["lead_id"], reused, now)
        out.append(canonical)

    return out


def _merge_dicts(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for field in _JSON_LIST_FIELDS:
        target[field] = _union_list(_as_list(target.get(field)), _as_list(incoming.get(field)))
    for field in _JSON_DICT_FIELDS:
        target[field] = {**_as_dict(target.get(field)), **_as_dict(incoming.get(field))}
    for field, value in incoming.items():
        if field in _JSON_LIST_FIELDS or field in _JSON_DICT_FIELDS:
            continue
        if value and not target.get(field):
            target[field] = value


def find_by_keys(keys: list[str], *, session: Session | None = None) -> dict[str, dict[str, Any]]:
    keys = [key for key in keys if key]
    if not keys:
        return {}
    if session is not None:
        return _find_by_keys_impl(keys, session)
    with get_session() as own:
        return _find_by_keys_impl(keys, own)


def _find_by_keys_impl(keys: list[str], session: Session) -> dict[str, dict[str, Any]]:
    rows = session.execute(select(Lead).where(Lead.lead_key.in_(keys))).scalars().all()
    return {row.lead_key: _row_to_dict(row) for row in rows}


def find_by_domains(
    domains: list[str], *, session: Session | None = None
) -> dict[str, dict[str, Any]]:
    clean = sorted({normalize_domain(d) for d in domains if d})
    if not clean:
        return {}
    if session is not None:
        return _find_by_domains_impl(clean, session)
    with get_session() as own:
        return _find_by_domains_impl(clean, own)


def _find_by_domains_impl(domains: list[str], session: Session) -> dict[str, dict[str, Any]]:
    rows = session.execute(select(Lead).where(Lead.domain.in_(domains))).scalars().all()
    return {row.domain: _row_to_dict(row) for row in rows if row.domain}


def filter_known(
    leads: list[dict[str, Any]], *, session: Session | None = None
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Split leads into (known-by-key, unknown-leads)."""
    keys = [compute_lead_key(lead) for lead in leads if isinstance(lead, dict)]
    known = find_by_keys(keys, session=session)
    unknown = [
        lead
        for lead in leads
        if isinstance(lead, dict) and compute_lead_key(lead) not in known
    ]
    return known, unknown


def get_lead(lead_id: str, *, session: Session | None = None) -> dict[str, Any] | None:
    if session is not None:
        row = session.get(Lead, lead_id)
        return _row_to_dict(row) if row else None
    with get_session() as own:
        row = own.get(Lead, lead_id)
        return _row_to_dict(row) if row else None


def list_leads(
    *,
    limit: int = 100,
    offset: int = 0,
    domain: str = "",
    hunt_id: str = "",
    session: Session | None = None,
) -> list[dict[str, Any]]:
    def _impl(s: Session) -> list[dict[str, Any]]:
        stmt = select(Lead).order_by(Lead.last_seen_at.desc(), Lead.id.asc())
        if domain:
            stmt = stmt.where(Lead.domain == normalize_domain(domain))
        if hunt_id:
            stmt = stmt.join(HuntLead, HuntLead.lead_id == Lead.id).where(
                HuntLead.hunt_id == hunt_id
            )
        stmt = stmt.limit(max(1, int(limit))).offset(max(0, int(offset)))
        return [_row_to_dict(row) for row in s.execute(stmt).scalars().all()]

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


def list_sightings(lead_id: str, *, session: Session | None = None) -> list[dict[str, Any]]:
    def _impl(s: Session) -> list[dict[str, Any]]:
        rows = s.execute(
            select(LeadSighting)
            .where(LeadSighting.lead_id == lead_id)
            .order_by(LeadSighting.seen_at.desc())
        ).scalars().all()
        return [
            {
                "hunt_id": row.hunt_id,
                "source_keyword": row.source_keyword,
                "seen_at": row.seen_at,
            }
            for row in rows
        ]

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


def list_hunt_leads(hunt_id: str, *, session: Session | None = None) -> list[dict[str, Any]]:
    def _impl(s: Session) -> list[dict[str, Any]]:
        links = s.execute(select(HuntLead).where(HuntLead.hunt_id == hunt_id)).scalars().all()
        lead_ids = [link.lead_id for link in links]
        if not lead_ids:
            return []
        rows = s.execute(select(Lead).where(Lead.id.in_(lead_ids))).scalars().all()
        by_id = {row.id: row for row in rows}
        result: list[dict[str, Any]] = []
        for link in links:
            row = by_id.get(link.lead_id)
            if not row:
                continue
            item = _row_to_dict(row)
            item["reused"] = bool(link.reused)
            result.append(item)
        return result

    if session is not None:
        return _impl(session)
    with get_session() as own:
        return _impl(own)


__all__ = [
    "upsert_leads",
    "find_by_keys",
    "find_by_domains",
    "filter_known",
    "get_lead",
    "list_leads",
    "list_sightings",
    "list_hunt_leads",
    "normalize_email",
]
