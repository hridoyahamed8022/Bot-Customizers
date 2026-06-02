"""Block banned users; show group link so they can contact admins."""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    Message,
    TelegramObject,
)

from ..config import settings
from ..db import db


def _remaining_text(expires_at: float | None) -> str:
    if expires_at is None:
        return ""
    remaining = int(expires_at - time.time())
    if remaining <= 0:
        return ""
    if remaining < 3600:
        return f" আরও {remaining // 60} মিনিট।"
    if remaining < 86400:
        h = remaining // 3600
        m = (remaining % 3600) // 60
        return f" আরও {h} ঘন্টা {m} মিনিট।"
    d = remaining // 86400
    return f" আরও {d} দিন।"


async def _group_kb() -> InlineKeyboardMarkup | None:
    """Discussion group লিঙ্ক বাটন।"""
    try:
        disc_url = await db.get_discussion_url()
        if disc_url:
            return InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📢 আমাদের গ্রুপে যোগাযোগ করুন", url=disc_url)
            ]])
    except Exception:
        pass
    return None


class BanMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or user.is_bot:
            return await handler(event, data)
        if user.id in settings.admin_ids:
            return await handler(event, data)

        ban_row = await db.get_ban_info(user.id)
        if ban_row is not None:
            expires_at = ban_row["expires_at"] if "expires_at" in ban_row.keys() else None
            rem = _remaining_text(expires_at)
            if expires_at:
                msg = (
                    f"⛔ <b>আপনাকে সাময়িকভাবে বট ব্যবহার থেকে বিরত রাখা হয়েছে।</b>{rem}\n\n"
                    f"ব্যান তুলতে আমাদের গ্রুপে যোগাযোগ করুন।"
                )
            else:
                msg = (
                    "⛔ <b>আপনাকে এই বট থেকে ব্যান করা হয়েছে।</b>\n\n"
                    "ব্যান তুলতে আমাদের গ্রুপে যোগাযোগ করুন।"
                )
            kb = await _group_kb()
            try:
                if isinstance(event, CallbackQuery):
                    plain = msg.replace("<b>", "").replace("</b>", "")
                    await event.answer(plain[:200], show_alert=True)
                elif isinstance(event, Message):
                    from aiogram.enums import ParseMode
                    await event.answer(msg, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif isinstance(event, InlineQuery):
                    await event.answer([], cache_time=1, is_personal=True)
            except Exception:
                pass
            return None

        # is_blocked=1 মানে আগে বটকে ব্লক করেছিল — এখন ফিরে এসেছে
        try:
            user_row = await db.fetch_one(
                "SELECT is_blocked FROM users WHERE user_id=?", (user.id,)
            )
            if user_row and user_row["is_blocked"]:
                await db.mark_blocked(user.id, False)
                disc_url = await db.get_discussion_url()
                kb = None
                if disc_url:
                    kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="📢 গ্রুপে যোগাযোগ করুন", url=disc_url)
                    ]])
                notice = (
                    "⚠️ <b>আপনি আগে এই বটকে ব্লক করে রেখেছিলেন।</b>\n\n"
                    "তাই কিছু নোটিফিকেশন মিস হয়ে থাকতে পারে।\n"
                    "এখন বট স্বাভাবিকভাবে ব্যবহার করতে পারবেন। 🎬\n\n"
                    "যেকোনো সমস্যায় গ্রুপে যোগাযোগ করুন।"
                )
                if isinstance(event, Message):
                    from aiogram.enums import ParseMode
                    try:
                        await event.answer(notice, reply_markup=kb, parse_mode=ParseMode.HTML)
                    except Exception:
                        pass
        except Exception:
            pass

        return await handler(event, data)
