"""Lead repository API — query persisted, deduplicated leads."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.security import require_api_access
from persistence import lead_repo
from persistence.db import run_db

router = APIRouter(prefix="/api/v1", tags=["leads"], dependencies=[Depends(require_api_access)])


@router.get("/leads")
async def list_leads(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    domain: str = Query(default=""),
    hunt_id: str = Query(default=""),
):
    """List leads from the global repository, optionally filtered."""
    return await run_db(
        lead_repo.list_leads,
        limit=limit,
        offset=offset,
        domain=domain,
        hunt_id=hunt_id,
    )


@router.get("/leads/{lead_id}")
async def get_lead(lead_id: str):
    """Return a single lead with its sighting history."""
    lead = await run_db(lead_repo.get_lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead["sightings"] = await run_db(lead_repo.list_sightings, lead_id)
    return lead


@router.get("/hunts/{hunt_id}/leads")
async def list_hunt_leads(hunt_id: str):
    """Return the canonical leads linked to a hunt."""
    return await run_db(lead_repo.list_hunt_leads, hunt_id)
