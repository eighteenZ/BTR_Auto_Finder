"""Login/logout/session routes for the corporate-mailbox account system."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, SecretStr

import api.auth as auth

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_USER_KEY = auth._SESSION_USER_KEY
SESSION_MAX_AGE_SECONDS = 7 * 24 * 3600


class LoginRequest(BaseModel):
    email: str
    password: SecretStr


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "email": user.get("email", ""),
        "role": user.get("role", ""),
        "api_key": user.get("api_key", ""),
        "last_login_at": user.get("last_login_at", ""),
    }


@router.post("/login")
async def login(request: Request, payload: LoginRequest, response: Response):
    """Verify corporate-mailbox credentials and open a session cookie."""
    try:
        user = auth.login(payload.email, payload.password.get_secret_value())
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    request.session[SESSION_USER_KEY] = {"email": user["email"]}
    response.set_cookie(
        "auth_session",
        request.session.get(SESSION_USER_KEY, {}).get("email", ""),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return {"user": _public_user(user)}


@router.post("/logout")
async def logout(request: Request, response: Response):
    request.session.pop(SESSION_USER_KEY, None)
    response.delete_cookie("auth_session")
    return {"status": "ok"}


@router.get("/me")
async def me(request: Request):
    from api.security import require_user

    user = require_user(request)
    return {"user": _public_user(user)}


@router.get("/users")
async def list_users(request: Request):
    from api.security import require_admin

    require_admin(request)
    return [{"email": u["email"], "role": u["role"], "active": u["active"],
             "api_key": u["api_key"], "created_at": u["created_at"],
             "last_login_at": u["last_login_at"]} for u in auth.list_users()]


@router.post("/users")
async def create_user(request: Request, payload: LoginRequest, role: str = "member"):
    """Admin creates a user by verifying their mailbox credentials once."""
    from api.security import require_admin

    require_admin(request)
    try:
        user = auth.create_user(payload.email, role=role, verify_password=payload.password.get_secret_value())
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=401, detail="Mailbox rejected these credentials; user not created.")
    return {"user": _public_user(user)}


@router.delete("/users/{email}")
async def delete_user(request: Request, email: str):
    from api.security import require_admin

    require_admin(request)
    if not auth.delete_user(email):
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "ok"}


@router.post("/users/{email}/role")
async def set_role(request: Request, email: str, role: str):
    from api.security import require_admin

    require_admin(request)
    try:
        user = auth.set_role(email, role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": _public_user(user)}


@router.post("/users/{email}/reset-api-key")
async def reset_api_key(request: Request, email: str):
    from api.security import require_admin

    require_admin(request)
    user = auth.reset_api_key(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": _public_user(user)}
