"""aiohttp web application factory — admin panel + healthcheck."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

import aiohttp_jinja2
import jinja2
from aiogram import Bot
from aiohttp import web

from ..db import db
from .auth import auth_middleware
from .routes import setup_routes

log = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


async def _global_context(request: web.Request) -> Dict[str, Any]:
    """Inject global template variables (nav badge counts, etc)."""
    ctx: Dict[str, Any] = {}
    if request.get("is_admin"):
        try:
            ctx["pending_req_count"] = await db.count_movie_requests("pending")
        except Exception:
            ctx["pending_req_count"] = 0
    else:
        ctx["pending_req_count"] = 0
    return ctx


def build_web_app(bot: Bot) -> web.Application:
    app = web.Application(
        client_max_size=200 * 1024 * 1024,
        middlewares=[auth_middleware],
    )
    app["bot"] = bot

    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=True,
        context_processors=[aiohttp_jinja2.request_processor, _global_context],
    )
    env = aiohttp_jinja2.get_env(app)
    env.filters["humansize"] = _humansize
    env.filters["fmtdate"] = _fmtdate

    setup_routes(app)
    return app


def _humansize(num: int | float | None) -> str:
    try:
        n = float(num or 0)
    except Exception:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _fmtdate(ts: float | int | None) -> str:
    from datetime import datetime, timezone
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "—"
