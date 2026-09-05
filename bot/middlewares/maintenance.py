"""Maintenance mode middleware — বট রক্ষণাবেক্ষণ মোডে থাকলে সব ইউজার ব্লক।"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from ..config import settings
from ..db import db
from ..handlers.common import watch_now_button

log = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────────

def _fmt_remaining(secs: float) -> str:
    s = max(0, int(secs))
    if s <= 0:
        return "শেষ হয়ে গেছে"
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    if h > 0:
        return f"{h}ঘণ্টা {m}মিনিট"
    if m > 0:
        return f"{m}মিনিট {ss:02d}সেকেন্ড"
    return f"{ss}সেকেন্ড"


def _progress_bar(remaining: float, total: float, width: int = 10) -> str:
    if total <= 0:
        return "▱" * width + "  0%"
    ratio = max(0.0, min(1.0, 1.0 - remaining / total))
    filled = round(ratio * width)
    empty  = width - filled
    pct    = int(ratio * 100)
    bar    = "▰" * filled + "▱" * empty
    return f"{bar} {pct}%"


def build_maint_text(remaining: float, total: float, disc_url: str) -> str:
    if remaining > 0:
        time_str  = _fmt_remaining(remaining)
        bar_str   = _progress_bar(remaining, total)
        time_line = f"⏱ <b>{time_str}</b> বাকি"
        bar_line  = f"<code>{bar_str}</code>"
    else:
        time_line = "⏳ <b>কাজ চলছে…</b>"
        bar_line  = "<code>" + "▱" * 10 + " —</code>"

    disc_line = (
        "\n💬 জরুরি প্রয়োজনে আমাদের গ্রুপে জানান।"
        if disc_url else ""
    )

    return (
        "🔧 <b>রক্ষণাবেক্ষণ চলছে</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚙️ বটকে আরও উন্নত করা হচ্ছে।\n\n"
        f"{time_line}\n"
        f"{bar_line}\n"
        "↑ প্রতি ৫ সেকেন্ডে আপডেট হয়\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🙏 একটু অপেক্ষা করুন — শীঘ্রই ফিরছি!"
        f"{disc_line}"
    )


def build_maint_kb(disc_url: str) -> InlineKeyboardMarkup:
    rows: list = []
    if disc_url:
        rows.append([
            InlineKeyboardButton(text="💬 আমাদের গ্রুপে যোগ দিন", url=disc_url)
        ])
    watch = watch_now_button()
    if watch:
        rows.append([watch])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── live auto-updater (প্রতি ৫ সেকেন্ডে) ──────────────────────────────────────

async def _live_updater(
    bot: Any,
    chat_id: int,
    msg_id: int,
    until: float,
    total: float,
    disc_url: str,
) -> None:
    """প্রতি ৫ সেকেন্ডে message edit করো — মেইনটেন্যান্স শেষ না হওয়া পর্যন্ত।"""
    kb = build_maint_kb(disc_url)
    last_text = ""

    while True:
        await asyncio.sleep(5)

        now = time.time()

        # মেইনটেন্যান্স শেষ হলে থামো
        try:
            still_on = await db.is_maintenance()
        except Exception:
            still_on = False

        if not still_on:
            break
        if until > 0 and now >= until:
            break

        remaining = (until - now) if until > 0 else 0.0
        new_text = build_maint_text(remaining, total, disc_url)

        # একই text থাকলে edit করার দরকার নেই
        if new_text == last_text:
            continue

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=new_text,
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
            last_text = new_text
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest:
            break
        except Exception as exc:
            log.debug("live_updater edit failed: %s", exc)
            break


# ── middleware ──────────────────────────────────────────────────────────────────

class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)

        # অ্যাডমিন সবসময় পাস
        if user and user.id in settings.admin_ids:
            return await handler(event, data)

        # মেইনটেন্যান্স চলছে কিনা চেক করো
        try:
            is_on = await db.is_maintenance()
        except Exception:
            log.exception("db.is_maintenance() failed")
            return await handler(event, data)

        if not is_on:
            return await handler(event, data)

        # টাইমার এক্সপায়ার হয়েছে কিনা চেক করো
        try:
            until = await db.get_maintenance_until()
        except Exception:
            until = 0.0

        now = time.time()
        if until > 0 and now >= until:
            try:
                await db.set_maintenance_timed(False)
            except Exception:
                pass
            return await handler(event, data)

        # DB থেকে বাকি তথ্য আনো
        try:
            total_secs, disc_url = await asyncio.gather(
                db.get_maintenance_total(),
                db.get_discussion_url(),
            )
        except Exception:
            total_secs = 0.0
            disc_url = ""

        remaining = (until - now) if until > 0 else 0.0
        text = build_maint_text(remaining, total_secs, disc_url)
        kb   = build_maint_kb(disc_url)

        if isinstance(event, Message):
            try:
                sent = await event.answer(
                    text,
                    reply_markup=kb,
                    parse_mode=ParseMode.HTML,
                )
                log.debug(
                    "Maintenance message sent to user=%s (%.0fs remaining)",
                    getattr(user, "id", "?"), remaining,
                )
                # background live-updater চালু করো
                if sent:
                    asyncio.create_task(
                        _live_updater(
                            event.bot, event.chat.id, sent.message_id,
                            until, total_secs, disc_url,
                        )
                    )
            except Exception as exc:
                log.exception("Failed to send maintenance message: %s", exc)

        elif isinstance(event, CallbackQuery):
            try:
                short = (
                    "🔧 রক্ষণাবেক্ষণ চলছে। "
                    + (_fmt_remaining(remaining) + " বাকি।" if remaining > 0
                       else "কিছুক্ষণ অপেক্ষা করুন।")
                )
                await event.answer(short[:200], show_alert=True)
            except Exception as exc:
                log.debug("Maintenance callback answer failed: %s", exc)

        return None
