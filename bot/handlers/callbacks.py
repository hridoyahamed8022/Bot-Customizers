"""ফাইল ডেলিভারি কলব্যাক — সব মেসেজ বাংলায়।"""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from ..db import db
from ..middlewares.maintenance import build_maint_text, build_maint_kb
from ..utils import esc, schedule_delete, MOVIE_TTL, MSG_TTL

router = Router(name="callbacks")
log = logging.getLogger(__name__)


_FIELD = {
    "video": "video",
    "document": "document",
    "audio": "audio",
    "animation": "animation",
    "voice": "voice",
    "video_note": "video_note",
    "photo": "photo",
}
_SENDERS = {
    "video": "send_video",
    "document": "send_document",
    "audio": "send_audio",
    "animation": "send_animation",
    "voice": "send_voice",
    "video_note": "send_video_note",
    "photo": "send_photo",
}


async def deliver_movie(target, movie_id: int) -> None:
    movie = await db.get_movie(movie_id)
    if not movie:
        if isinstance(target, CallbackQuery):
            await target.answer("⚠️ এই ফাইলটি লাইব্রেরিতে আর নেই।", show_alert=True)
        else:
            await target.answer("⚠️ এই ফাইলটি লাইব্রেরিতে আর নেই।")
        return

    bot = target.bot
    chat_id = (
        target.from_user.id if isinstance(target, CallbackQuery) else target.chat.id
    )
    method_name = _SENDERS.get(movie["file_type"], "send_document")
    method = getattr(bot, method_name)

    caption = (
        f"🎬 <b>{esc(movie['title'])}</b>\n\n"
        f"⏳ <i>এই ফাইলটি <b>১০ মিনিট</b> পর মুছে যাবে।\n"
        f"💡 এখনই কোনো বন্ধুকে ফরওয়ার্ড করে রাখুন!\n"
        f"আবার পেতে বটে নাম লিখে সার্চ করুন।</i>"
    )
    if movie["caption"]:
        caption = (
            f"🎬 <b>{esc(movie['title'])}</b>\n\n"
            f"{esc(movie['caption'])}\n\n"
            f"⏳ <i>ফাইলটি <b>১০ মিনিট</b> পর মুছে যাবে — বন্ধুকে ফরওয়ার্ড করুন!</i>"
        )
    if len(caption) > 1024:
        caption = caption[:1020] + "…"

    field = _FIELD.get(movie["file_type"], "document")
    kwargs = {"chat_id": chat_id, field: movie["file_id"]}
    if movie["file_type"] not in {"voice", "video_note"}:
        kwargs["caption"] = caption

    try:
        sent = await method(**kwargs)
        await db.increment_hits(movie_id)
        await db.log_event(
            "delivered",
            {"movie_id": movie_id, "user_id": chat_id, "title": movie["title"]},
        )
        if isinstance(target, CallbackQuery):
            await target.answer("✅ পাঠানো হয়েছে", show_alert=False)

        # মুভি ফাইল ৯০ মিনিট পর ডিলিট
        if sent:
            asyncio.create_task(
                schedule_delete(bot, chat_id, sent.message_id, MOVIE_TTL)
            )

    except TelegramBadRequest as exc:
        log.warning("Failed to deliver movie %s: %s", movie_id, exc)
        msg = "❌ দুঃখিত, ফাইলটি এখন পাঠানো যাচ্ছে না।"
        if isinstance(target, CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
    except Exception:
        log.exception("Unexpected delivery failure")
        if isinstance(target, CallbackQuery):
            await target.answer("❌ ফাইল পাঠানো যায়নি।", show_alert=True)


@router.callback_query(F.data.startswith("m:get:"))
async def cb_get_movie(callback: CallbackQuery) -> None:
    try:
        movie_id = int(callback.data.split(":")[2])
    except Exception:
        await callback.answer("ভুল আইডি।", show_alert=True)
        return
    await deliver_movie(callback, movie_id)


@router.callback_query(F.data == "maint:refresh")
async def cb_maint_refresh(callback: CallbackQuery) -> None:
    """মেইনটেন্যান্স মেসেজের সময় লাইভ আপডেট করো।"""
    on = await db.is_maintenance()
    if not on:
        await callback.answer("✅ বট এখন সচল! রিফ্রেশ করুন।", show_alert=True)
        return

    until, total = await asyncio.gather(
        db.get_maintenance_until(),
        db.get_maintenance_total(),
    )
    now = time.time()

    if until > 0 and now >= until:
        await db.set_maintenance_timed(False)
        await callback.answer("✅ মেইনটেন্যান্স শেষ হয়েছে!", show_alert=True)
        return

    remaining = (until - now) if until > 0 else 0.0
    disc_url = await db.get_discussion_url()
    text = build_maint_text(remaining, total, disc_url)
    kb = build_maint_kb(disc_url)

    try:
        from aiogram.enums import ParseMode
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await callback.answer("🔄 আপডেট হয়েছে")
    except TelegramBadRequest:
        await callback.answer("⚠️ আপডেট করা যায়নি।")
