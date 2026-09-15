"""Marketing service — email campaign domain as an independent FastAPI app.

Owns draft review, campaign lifecycle, scheduling, sending and reply
detection. Interacts with the hunter service only through PostgreSQL
(email_drafts / campaign_jobs contract tables + the shared email tables).
"""

from __future__ import annotations

import asyncio
import logging
import socket
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.email_routes import (
    CreateCampaignRequest,
    create_email_campaign,
    router as email_router,
    start_email_campaign,
)
from api.settings_routes import router as settings_router
from config.settings import get_settings
from emailing.draft_store import CampaignJobQueue, now_iso
from emailing.readiness import ensure_imap_tested, ensure_smtp_tested
from emailing.reply_detector import run_reply_detection_once
from emailing.scheduler import run_scheduler_once
from emailing.store import EmailStore

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
)

CAMPAIGN_JOBS_POLL_SECONDS = 30


async def email_scheduler_loop() -> None:
    """Poll pending email jobs and dispatch due messages."""
    while True:
        try:
            settings = get_settings()
            if not bool(settings.email_auto_send_enabled):
                await asyncio.sleep(60)
                continue
            ensure_smtp_tested(settings)
            store = EmailStore(settings.email_db_path)
            store.init_db()
            result = await run_scheduler_once(store)
            if result["sent"] or result["failed"]:
                logger.info("[EmailScheduler] sent=%s failed=%s skipped=%s", result["sent"], result["failed"], result["skipped"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[EmailScheduler] polling iteration failed")
        await asyncio.sleep(60)


async def email_reply_loop() -> None:
    """Poll inbox for replies and stop follow-up sequences."""
    while True:
        try:
            settings = get_settings()
            if not bool(settings.email_reply_detection_enabled):
                await asyncio.sleep(max(30, int(settings.email_reply_check_interval_seconds)))
                continue
            ensure_imap_tested(settings)
            store = EmailStore(settings.email_db_path)
            store.init_db()
            account = store.get_account("default")
            if account:
                result = await run_reply_detection_once(store, account)
                if result["matched"]:
                    logger.info("[EmailReply] checked=%s matched=%s skipped=%s", result["checked"], result["matched"], result["skipped"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[EmailReply] polling iteration failed")
        await asyncio.sleep(max(30, int(settings.email_reply_check_interval_seconds)))


async def run_campaign_job_once() -> bool:
    """Claim one campaign job and execute it. Returns True if a job ran."""
    queue = CampaignJobQueue()
    job = await asyncio.to_thread(queue.claim_next, f"marketing-{socket.gethostname()}")
    if not job:
        return False

    import json

    job_id = str(job["id"])
    hunt_id = str(job["hunt_id"])
    try:
        payload = json.loads(str(job.get("payload_json") or "{}"))
    except Exception:
        payload = {}
    logger.info("[CampaignJobs] claimed job=%s hunt=%s", job_id[:8], hunt_id[:8])

    try:
        created = await create_email_campaign(
            hunt_id,
            CreateCampaignRequest(name=str(payload.get("name") or "Outbound Campaign")),
        )
        campaign_id = str(created.campaign_id)
        if int(created.sequence_count or 0) > 0 and bool(payload.get("auto_start", True)):
            await start_email_campaign(campaign_id)
            logger.info("[CampaignJobs] job=%s campaign=%s started (%d sequences)",
                        job_id[:8], campaign_id[:8], created.sequence_count)
        else:
            logger.info("[CampaignJobs] job=%s campaign=%s left draft (%d sequences)",
                        job_id[:8], campaign_id[:8], created.sequence_count)
        await asyncio.to_thread(queue.mark_completed, job_id, campaign_id=campaign_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[CampaignJobs] job=%s failed", job_id[:8])
        await asyncio.to_thread(queue.mark_failed, job_id, error=str(exc))
    return True


async def campaign_jobs_loop() -> None:
    """Consume campaign_jobs rows enqueued by the hunter service."""
    while True:
        try:
            worked = await run_campaign_job_once()
            if worked:
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[CampaignJobs] polling iteration failed")
        await asyncio.sleep(CAMPAIGN_JOBS_POLL_SECONDS)


@asynccontextmanager
async def marketing_lifespan(app: FastAPI):
    settings = get_settings()
    app.state.email_scheduler_task = asyncio.create_task(email_scheduler_loop())
    logger.info("[EmailScheduler] background loop started")
    app.state.email_reply_task = asyncio.create_task(email_reply_loop())
    logger.info("[EmailReply] background loop started")
    app.state.campaign_jobs_task = asyncio.create_task(campaign_jobs_loop())
    logger.info("[CampaignJobs] background loop started")
    yield
    for name in ("email_scheduler_task", "email_reply_task", "campaign_jobs_task"):
        task = getattr(app.state, name, None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            logger.info("[%s] background loop stopped", name.split("_task")[0])


def create_marketing_app() -> FastAPI:
    """Create the marketing (email outreach) service application."""
    settings = get_settings()

    app = FastAPI(
        title="AI Hunter Marketing API",
        description="Email outreach: draft review, campaigns, scheduling, replies.",
        version="1.0.0",
        lifespan=marketing_lifespan,
    )

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/v1/health", tags=["health"])
    async def marketing_health():
        return {"status": "ok", "service": "ai-hunter-marketing", "time": now_iso()}

    app.include_router(email_router)
    if settings.settings_api_enabled:
        app.include_router(settings_router)

    return app
