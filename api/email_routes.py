"""Email marketing API routes — operates purely on the email-domain tables.

Drafts arrive through the ``email_drafts`` contract table (written by the
hunter service); campaigns/sequences/messages/replies live in the email
tables. This module deliberately imports nothing from the hunt domain
except read-only lead identity helpers.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.security import require_api_access
from config.settings import get_settings
from emailing.draft_store import EmailDraftStore, now_iso
from emailing.policy import expand_email_targets
from emailing.readiness import ensure_imap_tested, ensure_smtp_ready, ensure_smtp_tested
from emailing.reply_detector import run_reply_detection_once
from emailing.scheduler import run_scheduler_once
from emailing.smtp_client import send_smtp_email
from emailing.store import EmailStore
from persistence.lead_identity import official_domain

router = APIRouter(prefix="/api/v1", tags=["email"])


def _outreach_lead_key(lead: dict[str, Any], target_email: str) -> str:
    """Canonical outreach key: lead identity (domain/company) + target email."""
    email = str(target_email or "").strip().lower()
    if not email:
        return ""
    domain = official_domain(str(lead.get("website") or ""))
    if domain:
        return f"d:{domain}|{email}"
    company = "".join(str(lead.get("company_name") or "").lower().split())
    if company:
        return f"c:{company}|{email}"
    return f"e:{email}"


def _legacy_outreach_lead_key(lead: dict[str, Any], target_email: str) -> str:
    """Previous key format, kept for backward-compatible dedup reads."""
    return (
        str(lead.get("website") or lead.get("company_name") or "").lower()
        + "|"
        + str(target_email or "").lower()
    )


def _store() -> EmailStore:
    store = EmailStore(get_settings().email_db_path)
    store.init_db()
    return store


def _draft_store() -> EmailDraftStore:
    store = EmailDraftStore(get_settings().email_db_path)
    store.init_db()
    return store


def _default_account(store: EmailStore) -> dict[str, Any]:
    settings = get_settings()
    account_id = "default"
    existing = store.get_account(account_id)
    current = now_iso()
    payload = {
        "id": account_id,
        "provider_type": settings.email_provider_type,
        "from_name": settings.email_from_name,
        "from_email": settings.email_from_address,
        "reply_to": settings.email_reply_to or settings.email_from_address,
        "smtp_host": settings.email_smtp_host,
        "smtp_port": settings.email_smtp_port,
        "smtp_username": settings.email_smtp_username,
        "smtp_secret_encrypted": settings.email_smtp_password,
        "imap_host": settings.email_imap_host,
        "imap_port": settings.email_imap_port,
        "imap_username": settings.email_imap_username,
        "imap_secret_encrypted": settings.email_imap_password,
        "use_tls": 1 if settings.email_use_tls else 0,
        "status": "active",
        "daily_send_limit": settings.email_daily_send_limit,
        "hourly_send_limit": settings.email_hourly_send_limit,
        "last_test_at": "",
        "created_at": existing.get("created_at", current) if existing else current,
        "updated_at": current,
    }
    store.upsert_account(payload)
    return store.get_account(account_id) or payload


def _draft_is_campaign_ready(draft: dict[str, Any]) -> bool:
    if str(draft.get("status", "") or "") in {"approved", "rejected"}:
        return draft["status"] == "approved"
    manual_review = draft.get("manual_review")
    decision = str(manual_review.get("decision", "") or "") if isinstance(manual_review, dict) else ""
    if decision in {"approved", "rejected"}:
        return decision == "approved"
    if not bool(getattr(get_settings(), "email_require_approval_before_send", True)):
        return True
    return False


def _campaign_summary(store: EmailStore, campaign_id: str) -> dict[str, Any]:
    settings = get_settings()
    campaign = store.get_campaign(campaign_id)
    sequences = store.list_sequences_for_campaign(campaign_id)
    template_summary = store.get_template_performance_for_campaign(
        campaign_id,
        underperforming_min_assigned=int(getattr(settings, "email_template_underperforming_min_assigned", 10) or 10),
        underperforming_min_reply_rate=float(getattr(settings, "email_template_underperforming_min_reply_rate", 1.0) or 1.0),
    )
    return {
        "campaign": campaign,
        "sequence_count": len(sequences),
        "sent_count": store.count_messages_for_campaign(campaign_id, status="sent"),
        "pending_count": store.count_messages_for_campaign(campaign_id, status="pending"),
        "failed_count": store.count_messages_for_campaign(campaign_id, status="failed"),
        "template_summary": list(template_summary.values()),
        "sequences": sequences,
    }


# ── Draft review API ─────────────────────────────────────────────────────────


class DraftDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    notes: str = ""


class DraftDecisionResponse(BaseModel):
    draft_id: str
    hunt_id: str
    sequence_index: int
    decision: str
    manual_review: dict[str, Any]
    campaign_job_id: str = ""


class DraftContentUpdate(BaseModel):
    emails: list[dict[str, Any]] = Field(min_length=1, max_length=3)


class SendDraftRequest(BaseModel):
    sequence_number: int = Field(default=1, ge=1, le=3)


class SendDraftResponse(BaseModel):
    draft_id: str
    sequence_number: int
    sent_to: str
    subject: str
    status: str


def _draft_public(draft: dict[str, Any]) -> dict[str, Any]:
    return draft


@router.get("/hunts/{hunt_id}/email-drafts", dependencies=[Depends(require_api_access)])
async def list_hunt_email_drafts(hunt_id: str):
    return [_draft_public(d) for d in _draft_store().list_drafts_for_hunt(hunt_id)]


@router.get("/email-drafts", dependencies=[Depends(require_api_access)])
async def list_email_drafts(status: str = "", hunt_id: str = "", limit: int = 200):
    return [_draft_public(d) for d in _draft_store().list_drafts(status=status, hunt_id=hunt_id, limit=limit)]


def _ensure_campaign_job(hunt_id: str, *, campaign_name_prefix: str = "Auto campaign") -> str | None:
    """Guarantee a campaign job exists for this hunt (idempotent).

    Queue-driven hunts enqueue one at completion; direct `/hunts` runs do not.
    Approving a draft from the review surface therefore tops one up so both
    paths share the same wait-for-approval → build-campaign automation.
    """
    from emailing.draft_store import CampaignJobQueue

    queue = CampaignJobQueue()
    for job in queue.list_jobs(hunt_id=hunt_id, limit=50):
        if str(job.get("status", "")) in {"queued", "running"}:
            return str(job["id"])
    job = queue.enqueue(hunt_id, {"name": f"{campaign_name_prefix} {hunt_id[:8]}", "auto_start": True})
    return str(job["id"])


async def _decide_draft(draft_id: str, request: DraftDecisionRequest) -> DraftDecisionResponse:
    drafts = _draft_store()
    draft = drafts.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Email draft not found")
    updated = drafts.set_decision(draft_id, decision=request.decision, notes=request.notes)
    review = (updated or {}).get("manual_review") or {}
    job_id = ""
    if request.decision == "approved":
        hunt_id = str(draft.get("hunt_id", "") or "")
        job_id = await asyncio.to_thread(_ensure_campaign_job, hunt_id) if hunt_id else ""
    return DraftDecisionResponse(
        draft_id=draft_id,
        hunt_id=str(draft.get("hunt_id", "")),
        sequence_index=int(draft.get("sequence_index", 0) or 0),
        decision=request.decision,
        manual_review=review,
        campaign_job_id=job_id,
    )


@router.post("/email-drafts/{draft_id}/decision", response_model=DraftDecisionResponse, dependencies=[Depends(require_api_access)])
async def decide_email_draft(draft_id: str, request: DraftDecisionRequest):
    """Approve or reject a generated outreach draft."""
    return await _decide_draft(draft_id, request)


_MAX_SEND_DAY = 30


def _validated_emails(raw: list[dict[str, Any]], *, locale: str) -> list[dict[str, Any]]:
    """Validate and normalise reviewer-supplied steps, preserving order."""
    steps: list[dict[str, Any]] = []
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail=f"Step {position} must be an object")
        subject = str(item.get("subject", "") or "").strip()
        body = str(item.get("body_text", "") or "").strip()
        if not subject:
            raise HTTPException(status_code=422, detail=f"Step {position}: subject must not be empty")
        if not body:
            raise HTTPException(status_code=422, detail=f"Step {position}: body_text must not be empty")
        try:
            sequence_number = int(item.get("sequence_number", position) or position)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"Step {position}: invalid sequence_number")
        if sequence_number != position:
            raise HTTPException(
                status_code=422,
                detail=f"Step {position}: sequence_number must be {position} (steps stay in order, no gaps)",
            )
        try:
            send_day = int(item.get("suggested_send_day", 0) or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"Step {position}: invalid suggested_send_day")
        if not 0 <= send_day <= _MAX_SEND_DAY:
            raise HTTPException(status_code=422, detail=f"Step {position}: suggested_send_day must be 0-{_MAX_SEND_DAY}")
        steps.append({
            "sequence_number": sequence_number,
            "email_type": str(item.get("email_type", "") or ""),
            "subject": subject,
            "body_text": body,
            "suggested_send_day": send_day,
            **({"personalization_points": item["personalization_points"]} if item.get("personalization_points") else {}),
            **({"cultural_adaptations": item["cultural_adaptations"]} if item.get("cultural_adaptations") else {}),
        })
    return steps


@router.patch("/email-drafts/{draft_id}/content", dependencies=[Depends(require_api_access)])
async def update_email_draft_content(draft_id: str, request: DraftContentUpdate):
    """Let a reviewer edit the outreach content of a pending draft.

    Only a pending (undecided) draft is editable. Content passes through the
    same placeholder sanitizer as generation, so a stored draft never carries
    raw tokens; the edited flag also protects this wording from being clobbered
    by a hunter-side regeneration.
    """
    drafts = _draft_store()
    draft = drafts.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Email draft not found")
    if str(draft.get("status", "")) != "draft":
        raise HTTPException(
            status_code=409,
            detail=f"Draft is {draft.get('status')}; only pending drafts are editable",
        )

    steps = _validated_emails(request.emails, locale=str(draft.get("locale") or "en"))
    settings = get_settings()
    from emailing.signature import recipient_display_name, sanitize_outreach_text

    recipient = recipient_display_name(draft.get("target") or {})
    for step in steps:
        step["subject"] = sanitize_outreach_text(step["subject"], settings, recipient_name=recipient)
        step["body_text"] = sanitize_outreach_text(step["body_text"], settings, recipient_name=recipient)

    updated = await asyncio.to_thread(drafts.update_emails, draft_id, steps)
    if not updated:
        raise HTTPException(status_code=409, detail="Draft is no longer pending")
    return {"draft": updated, "edited_by_review": True}


@router.post(
    "/hunts/{hunt_id}/email-sequences/{sequence_index}/decision",
    response_model=DraftDecisionResponse,
    dependencies=[Depends(require_api_access)],
    include_in_schema=False,
)
async def decide_email_sequence_legacy(hunt_id: str, sequence_index: int, request: DraftDecisionRequest):
    """Legacy path: map (hunt_id, sequence_index) onto the draft and decide."""
    draft = _draft_store().get_draft_by_index(hunt_id, sequence_index)
    if not draft:
        raise HTTPException(status_code=404, detail="Email draft not found for this hunt/index")
    return await _decide_draft(str(draft["id"]), request)


def _draft_recipient(draft: dict[str, Any]) -> str:
    target = draft.get("target") or {}
    if isinstance(target, dict):
        email = str(target.get("target_email", "") or "").strip()
        if email:
            return email
    for item in draft.get("targets") or []:
        email = str((item or {}).get("target_email", "") or "").strip()
        if email:
            return email
    return ""


@router.post("/email-drafts/{draft_id}/send", response_model=SendDraftResponse, dependencies=[Depends(require_api_access)])
async def send_email_draft(draft_id: str, request: SendDraftRequest):
    """Manually send one step of an approved draft via SMTP."""
    drafts = _draft_store()
    draft = drafts.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Email draft not found")
    if not _draft_is_campaign_ready(draft):
        raise HTTPException(status_code=409, detail="Email draft must be approved before sending")

    recipient = _draft_recipient(draft)
    if not recipient:
        raise HTTPException(status_code=422, detail="No recipient email found on this draft")

    selected = None
    for item in draft.get("emails") or []:
        if isinstance(item, dict) and int(item.get("sequence_number", 0) or 0) == request.sequence_number:
            selected = item
            break
    if not selected:
        raise HTTPException(status_code=404, detail="Requested draft step not found")

    settings = get_settings()
    try:
        ensure_smtp_ready(settings)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        send_result = await asyncio.to_thread(
            send_smtp_email,
            settings,
            to_address=recipient,
            subject=str(selected.get("subject", "") or ""),
            body_text=str(selected.get("body_text", "") or ""),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SendDraftResponse(
        draft_id=draft_id,
        sequence_number=request.sequence_number,
        sent_to=recipient,
        subject=str(selected.get("subject", "") or ""),
        status=str(send_result.get("status", "sent")),
    )


# ── Campaign API ──────────────────────────────────────────────────────────────


class CreateCampaignRequest(BaseModel):
    name: str = "Outbound Campaign"


class CampaignResponse(BaseModel):
    campaign_id: str
    status: str
    sequence_count: int


@router.post("/hunts/{hunt_id}/email-campaigns", response_model=CampaignResponse, dependencies=[Depends(require_api_access)])
async def create_email_campaign(hunt_id: str, payload: CreateCampaignRequest):
    drafts = _draft_store().list_drafts_for_hunt(hunt_id)
    if not drafts:
        raise HTTPException(status_code=400, detail="No generated email drafts found for this hunt")

    settings = get_settings()
    try:
        ensure_smtp_ready(settings)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    store = _store()
    account = _default_account(store)
    campaign_id = str(uuid.uuid4())
    created = now_iso()
    store.create_campaign({
        "id": campaign_id,
        "hunt_id": hunt_id,
        "email_account_id": account["id"],
        "name": payload.name,
        "status": "draft",
        "language_mode": settings.email_language_mode,
        "default_language": settings.email_default_language,
        "fallback_language": settings.email_fallback_language,
        "tone": settings.email_tone,
        "step1_delay_days": settings.email_step1_delay_days,
        "step2_delay_days": settings.email_step2_delay_days,
        "step3_delay_days": settings.email_step3_delay_days,
        "min_fit_score": settings.email_min_fit_score_to_send,
        "min_contactability_score": settings.email_min_contactability_score_to_send,
        "created_at": created,
        "updated_at": created,
    })
    base_time = datetime.now(timezone.utc)
    for draft in drafts:
        lead = {
            "company_name": str(draft.get("company_name", "") or ""),
            "website": str(draft.get("website", "") or ""),
        }
        primary_target = draft.get("target") or {}
        raw_targets = []
        if isinstance(primary_target, dict):
            raw_targets.append(primary_target)
        raw_targets.extend(draft.get("targets") or [])
        if not raw_targets:
            # Fallback only when the draft carries no explicit target at all.
            raw_targets.extend(expand_email_targets(lead) or [])
        seen_target_emails: set[str] = set()
        targets = []
        for target in raw_targets:
            if not isinstance(target, dict):
                continue
            target_email = str(target.get("target_email", "") or "").strip().lower()
            if not target_email or target_email in seen_target_emails:
                continue
            seen_target_emails.add(target_email)
            targets.append(target)
        emails = draft.get("emails") or []
        if not targets or not emails:
            continue
        if not _draft_is_campaign_ready(draft):
            continue
        for target in targets:
            target_email = str(target.get("target_email") or "")
            lead_key = _outreach_lead_key(lead, target_email)
            legacy_key = _legacy_outreach_lead_key(lead, target_email)
            already_contacted = store.has_contact_history_for_lead_key(lead_key) if lead_key else False
            if not already_contacted and legacy_key and legacy_key != lead_key:
                already_contacted = store.has_contact_history_for_lead_key(legacy_key)
            if already_contacted:
                continue
            sequence_id = str(uuid.uuid4())
            store.create_sequence({
                "id": sequence_id,
                "campaign_id": campaign_id,
                "hunt_id": hunt_id,
                "lead_key": lead_key or (sequence_id.lower() + "|" + str(target.get("target_email") or "").lower()),
                "lead_email": str(target.get("target_email") or ""),
                "lead_name": str(lead.get("company_name") or ""),
                "decision_maker_name": str(target.get("target_name") or ""),
                "decision_maker_title": str(target.get("target_title") or ""),
                "locale": str(draft.get("locale") or "en_US"),
                "generation_mode": str(draft.get("generation_mode") or "personalized"),
                "template_id": str(draft.get("template_id") or ""),
                "template_group": str(draft.get("template_group") or ""),
                "template_usage_index": int(draft.get("template_usage_index", 0) or 0),
                "template_max_send_count": int(draft.get("template_max_send_count", 0) or 0),
                "status": "scheduled",
                "current_step": 0,
                "stop_reason": "",
                "replied_at": "",
                "last_sent_at": "",
                "next_scheduled_at": "",
                "created_at": created,
                "updated_at": created,
            })
            next_scheduled = ""
            for email in emails:
                step_number = int(email.get("sequence_number", 1) or 1)
                delay_days = int(email.get("suggested_send_day", 0) or 0)
                scheduled_at = (base_time + timedelta(days=delay_days)).isoformat()
                if step_number == 1:
                    next_scheduled = scheduled_at
                store.create_message({
                    "id": str(uuid.uuid4()),
                    "sequence_id": sequence_id,
                    "step_number": step_number,
                    "goal": str(email.get("email_type", "") or ""),
                    "locale": str(draft.get("locale") or "en_US"),
                    "subject": str(email.get("subject", "") or ""),
                    "body_text": str(email.get("body_text", "") or ""),
                    "status": "pending",
                    "scheduled_at": scheduled_at,
                    "sent_at": "",
                    "provider_message_id": "",
                    "thread_key": "",
                    "failure_reason": "",
                    "created_at": created,
                    "updated_at": created,
                })
            store.update_sequence_status(sequence_id, status="scheduled", updated_at=created, next_scheduled_at=next_scheduled)

    summary = _campaign_summary(store, campaign_id)
    return CampaignResponse(campaign_id=campaign_id, status="draft", sequence_count=summary["sequence_count"])


@router.get("/hunts/{hunt_id}/email-campaigns", dependencies=[Depends(require_api_access)])
async def list_email_campaigns(hunt_id: str):
    store = _store()
    campaigns = store.list_campaigns_for_hunt(hunt_id)
    return [{"campaign": c, **_campaign_summary(store, c["id"])} for c in campaigns]


@router.post("/email-campaigns/{campaign_id}/start", dependencies=[Depends(require_api_access)])
async def start_email_campaign(campaign_id: str):
    store = _store()
    campaign = store.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    settings = get_settings()
    try:
        ensure_smtp_tested(settings)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if str(campaign.get("email_account_id", "")) == "default":
        _default_account(store)
    updated = now_iso()
    store.update_campaign_status(campaign_id, "active", updated_at=updated)

    return {"campaign_id": campaign_id, "status": "active"}


@router.post("/email-campaigns/{campaign_id}/pause", dependencies=[Depends(require_api_access)])
async def pause_email_campaign(campaign_id: str):
    store = _store()
    campaign = store.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    updated = now_iso()
    store.update_campaign_status(campaign_id, "paused", updated_at=updated)

    return {"campaign_id": campaign_id, "status": "paused"}


@router.get("/email-sequences/{sequence_id}", dependencies=[Depends(require_api_access)])
async def get_email_sequence(sequence_id: str):
    store = _store()
    sequence = store.get_sequence(sequence_id)
    if not sequence:
        raise HTTPException(status_code=404, detail="Sequence not found")
    messages = store.list_messages_for_sequence(sequence_id)
    reply_events = store.list_reply_events_for_sequence(sequence_id)
    return {"sequence": sequence, "messages": messages, "reply_events": reply_events}


@router.post("/email-scheduler/run", dependencies=[Depends(require_api_access)])
async def run_email_scheduler():
    store = _store()
    try:
        ensure_smtp_tested(get_settings())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await run_scheduler_once(store)


@router.post("/email-replies/check", dependencies=[Depends(require_api_access)])
async def run_email_reply_check():
    store = _store()
    settings = get_settings()
    try:
        ensure_imap_tested(settings)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    account = _default_account(store)
    return await run_reply_detection_once(store, account)
