"""Shared API access control.

The account system (api/auth.py, corporate-mailbox login) is the primary
mechanism; ``require_api_access`` below is kept as the import-compatible name
every router already depends on. Sessions and per-user API keys replace the
single shared ``API_ACCESS_TOKEN``, which remains a break-glass admin
credential while it is configured.

Roles: ``require_user`` for operational endpoints, ``require_admin`` for the
settings API and user management.
"""

from __future__ import annotations

from fastapi import Request

import api.auth as auth
from api.auth import require_admin, require_user  # re-exported


def require_api_access(request: Request) -> dict:
    """Auth dependency used across routers (name kept for compatibility).

    Accepts a session cookie, a per-user API key, the legacy shared token
    (admin), or a localhost caller. Returns the resolved principal; role
    checks use :func:`require_admin`.
    """
    return auth.require_user(request)


__all__ = ["require_api_access", "require_user", "require_admin", "auth"]
