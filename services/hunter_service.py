"""Direct service facade for the lead-acquisition pipeline.

Use this module when you want to call the hunting capability from your own
FastAPI app, worker, or script without going through this project's HTTP API.

Example:
    from services.hunter_service import HuntRequest, run_hunt

    outcome = await run_hunt(
        HuntRequest(
            website_url="https://example.com",
            description="我想找东南亚的旅行社",
            target_regions=["Thailand", "Vietnam"],
            target_lead_count=50,
        )
    )
    for lead in outcome.leads:
        print(lead["company_name"], lead.get("emails"))
"""

from __future__ import annotations

import functools
import logging
import uuid
from typing import Any, Callable

from pydantic import BaseModel, Field

from agents.email_craft_agent import email_craft_node
from agents.insight_agent import insight_node
from agents.keyword_gen_agent import keyword_gen_node
from agents.lead_extract_agent import lead_extract_node, set_progress_callback
from agents.parse_description_agent import parse_description_node
from agents.search_agent import search_node
from api.hunt_store import save_hunt
from config.settings import get_settings
from graph.builder import build_graph
from graph.evaluate import evaluate_progress, should_continue_hunting
from observability.cost_tracker import get_tracker, remove_tracker
from persistence import lead_repo
from persistence.db import run_db
from persistence.utils import now_iso

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]


class HuntRequest(BaseModel):
    """Input for a single lead-acquisition run."""

    website_url: str = ""
    description: str = Field(
        default="",
        description="Free-form description, e.g. '我想找东南亚的旅行社'",
    )
    product_keywords: list[str] = Field(default_factory=list)
    target_customer_profile: str = ""
    target_regions: list[str] = Field(default_factory=list)
    uploaded_file_ids: list[str] = Field(default_factory=list)
    target_lead_count: int = Field(default=200, ge=1, le=10000)
    max_rounds: int = Field(default=10, ge=1, le=50)
    min_new_leads_threshold: int = Field(default=5, ge=1, le=100)
    enable_email_craft: bool = False
    email_template_examples: list[str] = Field(default_factory=list)
    email_template_notes: str = ""
    template_seed: dict[str, Any] | None = None
    dedup_mode: str | None = Field(
        default=None, description="reuse | skip | off; None uses the configured default"
    )


class HuntOutcome(BaseModel):
    """Result of a lead-acquisition run."""

    hunt_id: str
    status: str
    insight: dict | None = None
    leads: list[dict] = Field(default_factory=list)
    email_sequences: list[dict] = Field(default_factory=list)
    used_keywords: list[str] = Field(default_factory=list)
    hunt_round: int = 0
    round_feedback: dict | None = None
    keyword_search_stats: dict = Field(default_factory=dict)
    search_result_count: int = 0
    cost_summary: dict | None = None
    error: str | None = None


def _lead_key(lead: dict[str, Any]) -> str:
    website = str(lead.get("website", "") or "").strip().lower()
    if website:
        return f"w:{website}"
    company_name = str(lead.get("company_name", "") or "").strip().lower()
    if company_name:
        return f"c:{company_name}"
    emails = lead.get("emails") or []
    if isinstance(emails, list) and emails:
        return f"e:{str(emails[0]).strip().lower()}"
    return "raw"


def dedupe_leads(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return leads deduplicated by website / company name / first email."""
    merged: dict[str, dict[str, Any]] = {}
    for lead in leads:
        if isinstance(lead, dict):
            merged[_lead_key(lead)] = lead
    return list(merged.values())


def _initial_state(request: HuntRequest, hunt_id: str) -> dict[str, Any]:
    return {
        "website_url": request.website_url,
        "description": request.description,
        "product_keywords": request.product_keywords,
        "target_customer_profile": request.target_customer_profile,
        "target_regions": request.target_regions,
        "uploaded_files": list(request.uploaded_file_ids),
        "target_lead_count": request.target_lead_count,
        "max_rounds": request.max_rounds,
        "min_new_leads_threshold": request.min_new_leads_threshold,
        "enable_email_craft": request.enable_email_craft,
        "email_template_examples": list(request.email_template_examples),
        "email_template_notes": request.email_template_notes,
        "template_seed": request.template_seed or None,
        "dedup_mode": request.dedup_mode or "",
        "insight": None,
        "keywords": [],
        "used_keywords": [],
        "search_results": [],
        "seen_urls": [],
        "matched_platforms": [],
        "keyword_search_stats": {},
        "leads": [],
        "email_sequences": [],
        "hunt_round": 1,
        "prev_round_lead_count": 0,
        "round_feedback": None,
        "current_stage": "start",
        "hunt_id": hunt_id,
        "messages": [],
    }


async def run_hunt(
    request: HuntRequest,
    *,
    hunt_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
    persist: bool = True,
) -> HuntOutcome:
    """Run the full acquisition pipeline and return the accumulated result.

    This executes the Insight -> KeywordGen -> Search -> LeadExtract ->
    Evaluate loop (and optionally EmailCraft) in-process.

    When ``persist`` is True the hunt and its leads are written to the
    PostgreSQL repository; discovered leads are merged with existing ones and
    reused according to ``request.dedup_mode`` (defaults to the configured
    ``LEAD_DEDUP_MODE``).

    Note: ``progress_callback`` is wired through a module-level hook in the
    lead extraction agent, so concurrent runs inside one process are not
    supported. Use the queue/worker API for parallel execution.
    """
    settings = get_settings()
    dedup_mode = request.dedup_mode or settings.lead_dedup_mode
    hunt_id = hunt_id or str(uuid.uuid4())
    state = _initial_state(request, hunt_id)

    if persist:
        try:
            save_hunt(hunt_id, {"status": "running", "website_url": request.website_url})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[HunterService] failed to persist hunt start: %s", exc)

    if progress_callback is not None:
        set_progress_callback(progress_callback)

    node = functools.partial(
        lead_extract_node,
        lead_lookup=lead_repo.find_by_domains if persist else None,
        dedup_mode=dedup_mode,
    )

    accumulated: dict[str, Any] = dict(state)
    status = "completed"
    error: str | None = None
    try:
        graph = build_graph(
            parse_description_node=parse_description_node,
            insight_node=insight_node,
            keyword_gen_node=keyword_gen_node,
            search_node=search_node,
            lead_extract_node=node,
            evaluate_node=evaluate_progress,
            should_continue_fn=should_continue_hunting,
            email_craft_node=email_craft_node,
        )
        async for chunk in graph.astream(state):
            for node_name, node_output in chunk.items():
                if node_name == "__end__":
                    continue
                accumulated.update(node_output)
    except Exception as exc:  # noqa: BLE001 - surface failure through the outcome
        status = "failed"
        error = str(exc)
        logger.exception("[HunterService] hunt %s failed", hunt_id[:8])
    finally:
        if progress_callback is not None:
            set_progress_callback(None)

    leads = accumulated.get("leads", []) or []
    if persist and leads:
        try:
            leads = await run_db(
                lead_repo.upsert_leads,
                leads,
                hunt_id=hunt_id,
                dedup_mode=dedup_mode,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[HunterService] lead persistence failed: %s", exc)
            leads = dedupe_leads(leads)
    else:
        leads = dedupe_leads(leads)

    cost_summary = get_tracker(hunt_id).to_summary()
    remove_tracker(hunt_id)

    email_sequences = accumulated.get("email_sequences", []) or []
    if persist and email_sequences:
        try:
            from emailing.draft_store import EmailDraftStore

            written = await run_db(EmailDraftStore().upsert_from_sequences, hunt_id, email_sequences)
            logger.info("[HunterService] %d email draft(s) persisted for hunt %s", written, hunt_id[:8])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[HunterService] email draft persistence failed: %s", exc)

    if persist:
        try:
            save_hunt(
                hunt_id,
                {
                    "status": status,
                    "website_url": request.website_url,
                    "current_stage": accumulated.get("current_stage", "done"),
                    "hunt_round": accumulated.get("hunt_round", 0),
                    "leads_count": len(leads),
                    "email_sequences_count": len(email_sequences),
                    "completed_at": now_iso(),
                    "error": error or "",
                    "result": {
                        **accumulated,
                        "leads": leads,
                        "cost_summary": cost_summary,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[HunterService] failed to persist hunt result: %s", exc)

    return HuntOutcome(
        hunt_id=hunt_id,
        status=status,
        insight=accumulated.get("insight"),
        leads=leads,
        email_sequences=accumulated.get("email_sequences", []) or [],
        used_keywords=accumulated.get("used_keywords", []) or [],
        hunt_round=accumulated.get("hunt_round", 0) or 0,
        round_feedback=accumulated.get("round_feedback"),
        keyword_search_stats=accumulated.get("keyword_search_stats", {}) or {},
        search_result_count=len(accumulated.get("search_results", []) or []),
        cost_summary=cost_summary,
        error=error,
    )
