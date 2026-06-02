"""Cookie-based session for the admin panel."""
from __future__ import annotations

import hmac
from typing import Optional

from aiohttp import web

from ..config import settings
from ..db import db

COOKIE_NAME = "admin_session"
SESSION_USER_ID = 0  # symbolic — we use one logical "admin" account


def check_credentials(username: str, password: str) -> bool:
    """Constant-time check against ADMIN_USERNAME / ADMIN_PASSWORD."""
    if not (settings.admin_username and settings.admin_password):
        return False
    u_ok = hmac.compare_digest(
        (username or "").strip(), settings.admin_username
    )
    p_ok = hmac.compare_digest(password or "", settings.admin_password)
    return u_ok and p_ok


async def get_session(request: web.Request) -> Optional[int]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    user_id = await db.session_user(token)
    return user_id


@web.middleware
async def auth_middleware(request: web.Request, handler):
    user_id = await get_session(request)
    request["user_id"] = user_id
    request["is_admin"] = user_id is not None
    return await handler(request)


def require_admin(handler):
    async def wrapper(request: web.Request):
        if not request.get("is_admin"):
            raise web.HTTPFound("/login")
        return await handler(request)

    wrapper.__name__ = handler.__name__
    return wrapper


def set_session_cookie(response: web.StreamResponse, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=10 * 365 * 24 * 3600,  # 10 years — effectively permanent
        httponly=True,
        secure=False,  # Replit terminates TLS at the proxy
        samesite="Lax",
        path="/",
    )


def clear_session_cookie(response: web.StreamResponse) -> None:
    response.del_cookie(COOKIE_NAME, path="/")
