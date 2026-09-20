"""Daily customs pipeline — ingest ImportYeti exports, verify leads, classify.

Runs as an asyncio loop inside the hunter app lifespan (60s tick, fires once
per local day at ``customs_daily_run_at_local``) and on demand from the API.

Daily flow:
1. ingest CSV/XLSX drops from ``customs_import_dir`` (processed files are moved
   to ``processed/``, failures to ``failed/``; record hashes make re-ingest a
   no-op regardless),
2. recompute procurement status for every lead that has trade records,
3. deep-verify a bounded batch of leads via free provider-page lookups
   (ImportYeti profile pages included) to protect Serper/Jina quota,
4. return stats for the xlsx export / Feishu summary.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import date, datetime
from pathlib import Path

from config.settings import get_settings
from persistence import customs_repo
from tools.customs_router import find_customs_data
from tools.customs_sources.importyeti_source import parse_file
from tools.google_search import GoogleSearchTool
from tools.jina_reader import JinaReaderTool

logger = logging.getLogger(__name__)

_IMPORT_SUFFIXES = {".csv", ".xlsx", ".xlsm"}


def today_local() -> date:
    return datetime.now().astimezone().date()


def parse_run_at(value: str) -> tuple[int, int] | None:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def run_time_reached(run_at: str, now: datetime) -> bool:
    parsed = parse_run_at(run_at)
    if parsed is None:
        logger.warning("[CustomsDaily] invalid CUSTOMS_DAILY_RUN_AT_LOCAL %r; defaulting to 08:00", run_at)
        parsed = (8, 0)
    hour, minute = parsed
    return (now.hour, now.minute) >= (hour, minute)


def _scan_import_dir(import_dir: str) -> list[Path]:
    root = Path(import_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in _IMPORT_SUFFIXES)


def _move_file(path: Path, sub_dir: str) -> Path:
    target_dir = path.parent / sub_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    counter = 1
    while target.exists():
        target = target_dir / f"{path.stem}-{counter}{path.suffix}"
        counter += 1
    shutil.move(str(path), str(target))
    return target


def ingest_import_dir(import_dir: str) -> dict:
    """Ingest every pending file in the watched directory."""
    summary = {
        "files_processed": 0,
        "files_failed": 0,
        "records_ingested": 0,
        "records_skipped": 0,
        "new_leads": 0,
        "failed_files": [],
    }
    for path in _scan_import_dir(import_dir):
        try:
            records = parse_file(path, source_ref=path.name)
            ingest = customs_repo.ingest_trade_records(records)
        except Exception:
            logger.exception("[CustomsDaily] failed to ingest %s", path.name)
            _move_file(path, "failed")
            summary["files_failed"] += 1
            summary["failed_files"].append(path.name)
            continue
        summary["files_processed"] += 1
        summary["records_ingested"] += ingest["records_ingested"]
        summary["records_skipped"] += ingest["records_skipped"]
        summary["new_leads"] += ingest["new_leads"]
        _move_file(path, "processed")
        logger.info(
            "[CustomsDaily] ingested %s: +%d records (%d dup skipped), %d new leads",
            path.name, ingest["records_ingested"], ingest["records_skipped"], ingest["new_leads"],
        )
    return summary


def ingest_uploaded_file(path: str, *, source_ref: str = "") -> dict:
    """Ingest one API-uploaded file; the file is kept (no move)."""
    records = parse_file(path, source_ref=source_ref or Path(path).name)
    ingest = customs_repo.ingest_trade_records(records)
    return {"records_ingested": ingest["records_ingested"], "records_skipped": ingest["records_skipped"], "new_leads": ingest["new_leads"]}


def recompute_all(*, active_months: int, min_shipments_90d: int) -> dict:
    """Recompute procurement status for every lead that has trade records."""
    today = today_local()
    lead_ids = customs_repo.list_lead_ids_with_records()
    active = 0
    for lead_id in lead_ids:
        result = customs_repo.recompute_lead_procurement(
            lead_id,
            today=today,
            active_months=active_months,
            min_shipments_90d=min_shipments_90d,
        )
        if result and result["procurement_status"] == customs_repo.PROCUREMENT_ACTIVE:
            active += 1
    return {"leads_recomputed": len(lead_ids), "leads_active": active}


async def verify_leads_via_provider(limit: int) -> dict:
    """Free provider-page lookup (ImportYeti pages included) for a lead batch.

    A page with a concrete shipment count confirms an importer but cannot date
    the last shipment, so such leads get ``recent`` (never downgraded to
    ``active`` without dated records). An empty lookup keeps the stored status:
    absence of evidence is not evidence of absence.
    """
    leads = await asyncio.to_thread(customs_repo.list_leads_for_check, limit)
    google = GoogleSearchTool()
    jina = JinaReaderTool()
    summary = {"leads_checked": 0, "lookups_failed": 0, "importer_confirmed": 0}
    for lead in leads:
        try:
            result = await find_customs_data(
                company_name=lead["company_name"],
                google_search=google,
                jina_reader=jina,
                website=str(lead.get("website") or ""),
                country=str(lead.get("country_code") or ""),
            )
        except Exception:
            logger.warning("[CustomsDaily] lookup failed for %s", lead["company_name"], exc_info=True)
            summary["lookups_failed"] += 1
            continue
        summary["leads_checked"] += 1
        evidence = result.get("evidence") or []
        best = evidence[0] if evidence else None
        shipments = int((best or {}).get("shipment_count") or 0)
        status = "recent" if shipments > 0 else ""
        if shipments > 0:
            summary["importer_confirmed"] += 1
        await asyncio.to_thread(
            customs_repo.save_lead_check,
            lead["lead_id"],
            status=status,
            summary=str(result.get("summary") or ""),
        )
    return summary


async def run_customs_pipeline(*, trigger: str = "scheduled", force: bool = False) -> dict:
    """Execute the full daily pipeline; idempotent per local day unless forced."""
    settings = get_settings()
    run_date = today_local().isoformat()
    if trigger == "scheduled" and not force:
        existing = await asyncio.to_thread(customs_repo.completed_run_for_date, run_date)
        if existing:
            return {"skipped": True, "reason": "already completed today", "run_id": existing, "run_date": run_date}

    run_id = await asyncio.to_thread(customs_repo.start_run, run_date, trigger=trigger)
    ingest: dict = {}
    recompute: dict = {}
    verify: dict = {}
    try:
        ingest = await asyncio.to_thread(ingest_import_dir, settings.customs_import_dir)
        recompute = await asyncio.to_thread(
            recompute_all,
            active_months=settings.customs_active_months,
            min_shipments_90d=settings.customs_min_shipments_90d,
        )
        verify = await verify_leads_via_provider(settings.customs_check_batch_size)
    except Exception as exc:
        logger.exception("[CustomsDaily] run %s failed", run_id)
        await asyncio.to_thread(
            customs_repo.finish_run, run_id, status="failed",
            stats={"ingest": ingest, "recompute": recompute, "verify": verify},
            error=str(exc)[:2000],
        )
        raise

    result = {
        "run_id": run_id,
        "run_date": run_date,
        "trigger": trigger,
        "ingest": ingest,
        "recompute": recompute,
        "verify": verify,
    }
    await asyncio.to_thread(
        customs_repo.finish_run, run_id, status="ok",
        stats={"ingest": ingest, "recompute": recompute, "verify": verify},
        files_processed=ingest["files_processed"],
        records_ingested=ingest["records_ingested"],
        leads_checked=verify["leads_checked"],
        leads_active=recompute["leads_active"],
        new_leads=ingest["new_leads"],
    )
    logger.info(
        "[CustomsDaily] run %s done: +%d records / %d files, %d leads recomputed (%d active), %d page-checked",
        run_id, ingest["records_ingested"], ingest["files_processed"],
        recompute["leads_recomputed"], recompute["leads_active"], verify["leads_checked"],
    )
    return result
