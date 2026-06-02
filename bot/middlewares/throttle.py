"""Per-user rate limiting + lightweight spam scoring."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from cachetools import TTLCache

from ..config import settings
from ..db import db

log = logging.getLogger(__name__)


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit: float = 0.6, mute_ttl: int = 300) -> None:
        super().__init__()
        self.rate_cache: TTLCache = TTLCache(maxsize=10_000, ttl=rate_limit)
        self.spam_cache: TTLCache = TTLCache(maxsize=10_000, ttl=5)
        self.muted: TTLCache = TTLCache(maxsize=10_000, ttl=mute_ttl)
        self.warned: TTLCache = TTLCache(maxsize=10_000, ttl=60)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or user.is_bot or user.id in settings.admin_ids:
            return await handler(event, data)
        uid = user.id

        if uid in self.rate_cache:
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer("⏳ Slow down…", show_alert=False)
            except Exception:
                pass
            return None
        self.rate_cache[uid] = True

        if uid in self.muted:
            return None

        score = self.spam_cache.get(uid, 0) + 1
        self.spam_cache[uid] = score
        if score >= 12:
            self.muted[uid] = True
            await db.log_event("auto_mute", {"user_id": uid, "score": score})
            try:
                if isinstance(event, Message):
                    await event.answer("🔇 You were muted for 5 minutes for spamming.")
            except Exception:
                pass
            return None
        if score >= 6 and uid not in self.warned:
            self.warned[uid] = True
            try:
                if isinstance(event, Message):
                    await event.answer("⚠️ Please slow down or you'll be muted briefly.")
            except Exception:
                pass

        return await handler(event, data)
