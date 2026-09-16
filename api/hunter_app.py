"""Hunter service — lead acquisition domain as an independent FastAPI app.

Runs the hunt pipeline, automation queue consumer, lead repository and SSE.
Email marketing concerns (draft review, campaigns, scheduling, replies) live
in api.marketing_app; the two services communicate only through PostgreSQL.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.app import (
    _automation_consumer_loop,
    _automation_notify_loop,
    _automation_worker_id,
    _embedded_consumer_enabled,
    _now_iso,
    _template_seed_prewarm_loop,
)
from api.automation_routes import router as automation_router
from api.leads_routes import router as leads_router
from api.export_routes import router as export_router
from api.routes import router
from api.settings_routes import router as settings_router
from api.sse import sse_router
from automation.job_queue import HuntJobQueue
from automation.runtime import update_worker_state
from config.settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def hunter_lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings

    # The in-memory hunt cache is hydrated at routes-module import time.
    queue = HuntJobQueue(settings.automation_queue_db_path)
    queue.init_db()
    recovered_jobs = queue.recover_interrupted_running_jobs(updated_at=_now_iso())
    if recovered_jobs:
        logger.warning("[AutomationConsumer] recovered %s interrupted running job(s) after startup", recovered_jobs)

    from observability.setup import setup_observability

    setup_observability()

    app.state.automation_notify_task = asyncio.create_task(_automation_notify_loop())
    logger.info("[AutomationNotify] background loop started")
    app.state.template_seed_task = asyncio.create_task(_template_seed_prewarm_loop())
    logger.info("[TemplateSeedWorker] background loop started")
    app.state.automation_consumer_task = asyncio.create_task(_automation_consumer_loop())
    logger.info("[AutomationConsumer] background loop started")
    update_worker_state("consumer", enabled=_embedded_consumer_enabled(settings), running=True, worker_id=_automation_worker_id())

    yield

    for name in ("automation_notify_task", "template_seed_task", "automation_consumer_task"):
        task = getattr(app.state, name, None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            logger.info("[%s] background loop stopped", name)
    update_worker_state("consumer", running=False, active_job_id="")


def create_hunter_app() -> FastAPI:
    """Create the hunter (lead acquisition) service application."""
    settings = get_settings()

    app = FastAPI(
        title="AI Hunter API",
        description="Lead hunting: pipeline, automation queue, leads, SSE.",
        version="1.0.0",
        lifespan=hunter_lifespan,
    )

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    app.include_router(router, prefix="/api/v1")
    app.include_router(automation_router)
    app.include_router(leads_router)
    app.include_router(export_router)
    app.include_router(sse_router, prefix="/api/v1")
    if settings.settings_api_enabled:
        app.include_router(settings_router)

    return app
