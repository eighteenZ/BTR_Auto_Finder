"""Customs pipeline API — watchlist, daily runs, file imports, procurement leads."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from api.export_routes import XLSX_MEDIA_TYPE, build_workbook
from api.security import require_api_access
from config.settings import get_settings
from persistence import customs_repo
from persistence.db import run_db
from services.customs_daily_service import ingest_uploaded_file, run_customs_pipeline

router = APIRouter(prefix="/api/v1/customs", tags=["customs"], dependencies=[Depends(require_api_access)])

PROCUREMENT_STATUSES = ("active", "recent", "stale", "none", "unknown")
_IMPORT_SUFFIXES = {".csv", ".xlsx", ".xlsm"}

Column = tuple[str, Callable[[dict[str, Any]], Any]]

PROCUREMENT_COLUMNS: list[Column] = [
    ("公司名称", lambda l: l.get("company_name", "")),
    ("采购状态", lambda l: l.get("procurement_status", "")),
    ("最近进口", lambda l: l.get("last_import_at", "")),
    ("近90天批次", lambda l: l.get("import_count_90d", "")),
    ("官网", lambda l: l.get("website", "")),
    ("国家/地区", lambda l: l.get("country_code", "")),
    ("邮箱", lambda l: "; ".join(str(e) for e in (l.get("emails") or []))),
    ("优先级", lambda l: l.get("priority_tier", "")),
    ("契合度", lambda l: l.get("fit_score", "")),
    ("可触达度", lambda l: l.get("contactability_score", "")),
    ("海关摘要", lambda l: l.get("customs_data", "")),
    ("最近核查", lambda l: l.get("customs_last_checked_at", "")),
]


# ── Watchlist ────────────────────────────────────────────────────────────────


class WatchItemRequest(BaseModel):
    hs_code: str = Field(min_length=6, max_length=10)
    product_keywords: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    note: str = ""


@router.get("/watchlist")
async def get_watchlist(enabled_only: bool = Query(default=False)):
    return await run_db(customs_repo.list_watch_items, enabled_only=enabled_only)


@router.post("/watchlist")
async def add_watch_item(payload: WatchItemRequest):
    hs_code = "".join(ch for ch in payload.hs_code if ch.isdigit())
    if len(hs_code) not in (6, 8, 10):
        raise HTTPException(status_code=400, detail="hs_code must contain 6, 8 or 10 digits")
    return await run_db(
        customs_repo.add_watch_item,
        hs_code=hs_code,
        product_keywords=[kw.strip() for kw in payload.product_keywords if kw.strip()],
        countries=[c.strip() for c in payload.countries if c.strip()],
        note=payload.note,
    )


@router.delete("/watchlist/{item_id}")
async def delete_watch_item(item_id: str):
    deleted = await run_db(customs_repo.remove_watch_item, item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Watch item not found")
    return {"deleted": True}


# ── Daily runs ───────────────────────────────────────────────────────────────


class RunRequest(BaseModel):
    force: bool = False  # bypass the once-per-day idempotency guard


@router.post("/runs")
async def trigger_run(payload: RunRequest | None = None):
    try:
        return await run_customs_pipeline(trigger="manual", force=bool(payload.force) if payload else False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Customs pipeline failed: {str(exc)[:500]}") from exc


@router.get("/runs")
async def list_runs(limit: int = Query(default=20, le=100)):
    return await run_db(customs_repo.list_runs, limit)


# ── File imports ─────────────────────────────────────────────────────────────


@router.post("/imports")
async def upload_import_file(file: UploadFile):
    """Upload an ImportYeti CSV/XLSX export and ingest it immediately."""
    settings = get_settings()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _IMPORT_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{suffix}'; use CSV or XLSX")
    contents = await file.read()
    if len(contents) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds the {settings.max_upload_size_mb} MB limit")

    import_dir = Path(settings.customs_import_dir)
    import_dir.mkdir(parents=True, exist_ok=True)
    dest = import_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}{suffix}"
    dest.write_bytes(contents)
    try:
        summary = await run_db(ingest_uploaded_file, str(dest), source_ref=file.filename or dest.name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {str(exc)[:500]}") from exc
    return {"file": dest.name, **summary}


# ── Procurement-qualified leads ──────────────────────────────────────────────


@router.get("/procurement-leads")
async def procurement_leads(
    status: str = Query(default="", description="active | recent | stale | none | unknown; empty = all"),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
):
    if status and status not in PROCUREMENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status '{status}'")
    return await run_db(customs_repo.list_procurement_leads, status=status, limit=limit, offset=offset)


@router.get("/procurement-leads/export")
async def export_procurement_leads(
    status: str = Query(default="active"),
    limit: int = Query(default=1000, le=5000),
):
    """Download procurement-qualified leads as xlsx (default: active buyers)."""
    if status and status not in PROCUREMENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status '{status}'")
    leads = await run_db(customs_repo.list_procurement_leads, status=status, limit=limit)
    content = build_workbook(leads, PROCUREMENT_COLUMNS, sheet_title=f"procurement-{status or 'all'}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"customs-{status or 'all'}-{stamp}.xlsx"
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Lead-Count": str(len(leads)),
        },
    )
