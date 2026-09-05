"""Shared Telegram UI helpers used by every user-facing keyboard."""
from __future__ import annotations

from typing import Optional

from aiogram.types import InlineKeyboardButton, WebAppInfo

from ..config import settings


def watch_now_button() -> Optional[InlineKeyboardButton]:
    """Open the Moviex Hub Mini App from any bot keyboard."""
    if not settings.public_url:
        return None
    return InlineKeyboardButton(
        text="🎬 Watch Now",
        web_app=WebAppInfo(url=f"{settings.public_url.rstrip('/')}/miniapp/"),
    )