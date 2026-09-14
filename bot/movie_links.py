"""User-specific web links for the movie detail and ad-gated delivery flow."""
from __future__ import annotations

from urllib.parse import urlencode

from .config import settings as cfg
from .db import db


async def movie_web_url(user_id: int, movie_id: int) -> str:
    """Create a long-lived ad token and return the direct movie web page URL."""
    if not cfg.public_url:
        return ""
    wait_seconds = max(5, int(await db.get_setting("ad_wait_seconds", "10") or 10))
    token = await db.create_ad_token(movie_id, user_id, wait_seconds)
    query = urlencode({"movie": movie_id, "token": token})
    return f"{cfg.public_url.rstrip('/')}/miniapp/?{query}"