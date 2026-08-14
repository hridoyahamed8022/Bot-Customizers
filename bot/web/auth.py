"""Cookie-based session for the admin panel."""
from __future__ import annotations

import hmac
import json
from typing import Optional

from aiohttp import web

from ..config import settings
from ..db import db

COOKIE_NAME = "admin_session"


def check_credentials(username: str, password: str) -> bool:
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
            # The Replit shared proxy can drop cookies when an aiohttp
            # HTTP 302 is returned.  A normal 200 response with a browser
            # redirect keeps the session cookie flow reliable.
            html = """<!doctype html><html><head><meta charset="utf-8">
<script>window.location.replace(%s);</script>
</head><body>লগইন পেজে নেওয়া হচ্ছে...</body></html>""" % json.dumps("/login")
            return web.Response(
                text=html,
                content_type="text/html",
                headers={"Cache-Control": "no-store"},
            )
        return await handler(request)
    wrapper.__name__ = handler.__name__
    return wrapper


def set_session_cookie(response: web.StreamResponse, token: str) -> None:
    # SameSite=None + Secure=True — Replit HTTPS proxy-র জন্য দরকার
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=10 * 365 * 24 * 3600,
        httponly=True,
        secure=True,
        samesite="None",
        path="/",
    )


def clear_session_cookie(response: web.StreamResponse) -> None:
    response.del_cookie(COOKIE_NAME, path="/")
