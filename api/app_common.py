"""Shared FastAPI app wiring for the hunter/marketing services.

Keeps middleware (sessions, CORS), the auth router and the OpenAPI security
scheme identical across both services.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from api.auth_routes import router as auth_router

_SESSION_COOKIE_NAME = "session"
_SESSION_MAX_AGE_SECONDS = 7 * 24 * 3600


def _session_secret(settings) -> str:
    """Stable cookie-signing secret.

    Derived from API_ACCESS_TOKEN when set (rotating it invalidates sessions —
    acceptable, users simply log in again), else from the database URL so a
    tokenless deployment still gets unguessable cookies.
    """
    import hashlib

    token = str(getattr(settings, "api_access_token", "") or "").strip()
    if token:
        return f"ai-hunter-session:{token}"
    return "ai-hunter-session:" + hashlib.sha256(
        f"ai-hunter:no-token:{getattr(settings, 'database_url', '')}".encode()
    ).hexdigest()


def _install_common(app: FastAPI, settings) -> None:
    """Sessions, CORS, the auth router and the Swagger security scheme."""
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(settings),
        session_cookie=_SESSION_COOKIE_NAME,
        max_age=_SESSION_MAX_AGE_SECONDS,
        same_site="lax",
        https_only=False,
    )
    # Draft bodies are bulky JSON; gzip cuts the transpacific transfer ~5-10x.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(auth_router)

    # Document X-API-Key so remote users can Authorize in /docs. The auth
    # dependency reads the header directly; this only affects the schema.
    _original_openapi = app.openapi

    def _custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = _original_openapi()
        components = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        components["ApiKeyAuth"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "Per-user API key (issued on first mailbox login or by an admin).",
        }
        schema["security"] = [{"ApiKeyAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = _custom_openapi
