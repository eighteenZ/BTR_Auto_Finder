"""Account system: corporate-mailbox login replacing the shared API token.

Authentication model
--------------------
- Interactive users log in with their corporate email address and mailbox
  password; the credentials are verified by an IMAP LOGIN against the
  company mail server configured in ``EMAIL_IMAP_HOST``. No password is ever
  stored here.
- Programmatic callers (openclaw, scripts) authenticate with a per-user API
  key carried in ``X-API-Key`` / ``Authorization: Bearer`` — same headers as
  before, but the value identifies a user and inherits their role.
- Sessions are signed cookies (Starlette SessionMiddleware, 7 days).
- ``API_ACCESS_TOKEN`` keeps working as an admin-level break-glass credential
  for as long as it is configured, so existing deployments do not lock out.

Roles
-----
``admin``  everything, including the settings API and user management.
``member`` outreach operations: drafts, campaigns, leads, exports.
"""

from __future__ import annotations

import imaplib
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request, status

from config.settings import get_settings
from persistence.db import execute, fetch_all, fetch_one, get_session

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_ROLES = {"admin", "member"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── users store ──────────────────────────────────────────────────────────────


def _new_api_key() -> str:
    import secrets

    return f"ahk_{secrets.token_urlsafe(32)}"


def create_user(email: str, *, role: str = "member", verify_password: str | None = None,
                active: bool = True) -> dict[str, Any] | None:
    """Create a user. With ``verify_password`` the mailbox is IMAP-checked first.

    Returns the created row, or ``None`` when the mailbox check failed.
    """
    email = str(email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValueError(f"invalid email address: {email!r}")
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {sorted(VALID_ROLES)}")
    if verify_password is not None and not imap_login_verify(email, verify_password):
        return None

    user_id = str(uuid4())
    created = now_iso()
    with get_session() as session:
        execute(
            session,
            "INSERT INTO users (id, email, role, api_key, auth_provider, active, created_at) "
            "VALUES (?, ?, ?, ?, 'imap', ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET role = excluded.role, active = excluded.active",
            (user_id, email, role, _new_api_key(), active, created),
        )
    return get_user_by_email(email)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    email = str(email or "").strip().lower()
    with get_session() as session:
        return fetch_one(session, "SELECT * FROM users WHERE email = ?", (email,))


def get_user_by_api_key(api_key: str) -> dict[str, Any] | None:
    key = str(api_key or "").strip()
    if not key:
        return None
    with get_session() as session:
        return fetch_one(session, "SELECT * FROM users WHERE api_key = ? AND active = true", (key,))


def list_users() -> list[dict[str, Any]]:
    with get_session() as session:
        return fetch_all(session, "SELECT * FROM users ORDER BY created_at ASC")


def delete_user(email: str) -> bool:
    email = str(email or "").strip().lower()
    with get_session() as session:
        row = fetch_one(session, "SELECT id FROM users WHERE email = ?", (email,))
        if not row:
            return False
        execute(session, "DELETE FROM users WHERE email = ?", (email,))
    return True


def set_role(email: str, role: str) -> dict[str, Any] | None:
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {sorted(VALID_ROLES)}")
    with get_session() as session:
        execute(session, "UPDATE users SET role = ? WHERE email = ?", (role, email.strip().lower()))
    return get_user_by_email(email)


def reset_api_key(email: str) -> dict[str, Any] | None:
    with get_session() as session:
        execute(session, "UPDATE users SET api_key = ? WHERE email = ?", (_new_api_key(), email.strip().lower()))
    return get_user_by_email(email)


def touch_last_login(email: str) -> None:
    with get_session() as session:
        execute(session, "UPDATE users SET last_login_at = ? WHERE email = ?", (now_iso(), email.strip().lower()))


def count_admins() -> int:
    with get_session() as session:
        row = fetch_one(session, "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = true")
    return int((row or {}).get("n", 0))


# ── mailbox credential verification ─────────────────────────────────────────


def imap_login_verify(email: str, password: str) -> bool:
    """Verify mailbox credentials with a real IMAP LOGIN, then disconnect.

    Uses the corporate IMAP server from ``EMAIL_IMAP_HOST`` (default port 993,
    implicit TLS — same parameters the reply detector uses for its own inbox).
    Never raises on bad credentials; network failures raise ``ConnectionError``
    so callers can distinguish "wrong password" from "mail server unreachable".
    """
    settings = get_settings()
    host = str(settings.email_imap_host or "").strip()
    if not host:
        raise ConnectionError("EMAIL_IMAP_HOST is not configured; cannot verify mailbox credentials")
    port = int(settings.email_imap_port or 993)
    try:
        client = imaplib.IMAP4_SSL(host, port) if settings.email_use_tls else imaplib.IMAP4(host, port)
    except OSError as exc:
        raise ConnectionError(f"cannot reach IMAP server {host}:{port}: {exc}") from exc
    try:
        client.login(str(email).strip(), str(password))
        return True
    except imaplib.IMAP4.error:
        return False
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001 - logout failures must not mask the result
            pass


# ── request authentication ───────────────────────────────────────────────────

_LOCAL_HOSTS = {"", "127.0.0.1", "::1", "localhost", "testclient", "test"}
_SESSION_USER_KEY = "auth_user"


def authenticate(request: Request) -> dict[str, Any] | None:
    """Resolve the calling identity, or None for anonymous/local access.

    Priority: session cookie → per-user API key → legacy shared token (admin).
    """
    session_user = request.session.get(_SESSION_USER_KEY) if hasattr(request, "session") else None
    if session_user and isinstance(session_user, dict) and session_user.get("email"):
        user = get_user_by_email(str(session_user["email"]))
        if user and user.get("active"):
            return user
        return None

    provided = (
        request.headers.get("x-api-key")
        or request.query_params.get("api_key")
        or _bearer(request.headers.get("authorization"))
        or ""
    ).strip()
    if not provided:
        return None

    user = get_user_by_api_key(provided)
    if user:
        return user

    # Break-glass: the legacy shared token still grants admin while configured.
    settings = get_settings()
    if settings.api_access_token.strip() and provided == settings.api_access_token.strip():
        return {"id": "legacy-token", "email": "shared-token@local", "role": "admin",
                "api_key": provided, "active": True, "created_at": "", "last_login_at": ""}
    return None


def _bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _client_host(request: Request) -> str:
    return (request.client.host if request.client else "").strip().lower()


def _has_explicit_credentials(request: Request) -> bool:
    """Whether the caller presented any credential (valid or not)."""
    if request.headers.get("x-api-key"):
        return True
    if request.query_params.get("api_key"):
        return True
    return bool(_bearer(request.headers.get("authorization")))


def require_user(request: Request) -> dict[str, Any]:
    """Any authenticated principal (session, user API key, legacy token, localhost).

    Order matters: an explicit-but-invalid credential is a 401 even from
    localhost — a bad key must never silently fall through to the bypass.
    """
    user = authenticate(request)
    if user:
        return user
    if _has_explicit_credentials(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing credentials.")

    settings = get_settings()
    if _client_host(request) in _LOCAL_HOSTS:
        return {"id": "local", "email": "local@localhost", "role": "admin", "api_key": "",
                "active": True, "created_at": "", "last_login_at": ""}
    if not settings.api_access_token.strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API access requires login or an API key (API_ACCESS_TOKEN is not configured).",
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing credentials.")


def require_admin(request: Request) -> dict[str, Any]:
    """Admin-only surface: settings API and user management."""
    user = require_user(request)
    if str(user.get("role", "")) != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required.")
    return user


def login(email: str, password: str) -> dict[str, Any]:
    """Verify mailbox credentials and return the local user row.

    Raises HTTPException-shaped ValueError subclasses for the login route to
    surface: ValueError for bad input, ConnectionError for an unreachable
    mail server, PermissionError for rejected credentials or an unknown /
    inactive account (unknown users must verify their mailbox first —
    create them with scripts/create_user.py).
    """
    email = str(email or "").strip().lower()
    if not _EMAIL_RE.match(email) or not str(password or ""):
        raise ValueError("Email and password are required.")

    if not imap_login_verify(email, password):
        raise PermissionError("Mailbox rejected these credentials.")

    user = get_user_by_email(email)
    if not user:
        raise PermissionError("This mailbox is not registered. Ask an admin to create the account.")
    if not user.get("active"):
        raise PermissionError("This account is disabled.")
    touch_last_login(email)
    return user
