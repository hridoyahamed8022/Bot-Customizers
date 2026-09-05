"""ফাইল ডেলিভারি কলব্যাক — সব মেসেজ বাংলায়।"""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..db import db
from ..config import settings as cfg
from ..middlewares.maintenance import build_maint_text, build_maint_kb
from ..utils import esc, schedule_delete, movie_delete_countdown, MOVIE_TTL, MSG_TTL
from ..utils_ouo import shorten_url

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


async def deliver_movie(target, movie_id: int) -> bool:
    movie = await db.get_movie(movie_id)
    if not movie:
        if isinstance(target, CallbackQuery):
            await target.answer("⚠️ এই ফাইলটি লাইব্রেরিতে আর নেই।", show_alert=True)
        else:
            await target.answer("⚠️ এই ফাইলটি লাইব্রেরিতে আর নেই।")
        return False

    bot = target.bot
    chat_id = (
        target.from_user.id if isinstance(target, CallbackQuery) else target.chat.id
    )
    method_name = _SENDERS.get(movie["file_type"], "send_document")
    method = getattr(bot, method_name)

    caption = (
        f"🎬 <b>{esc(movie['title'])}</b>\n\n"
        f"⏳ <i>এই ফাইলটি <b>{MOVIE_TTL} সেকেন্ড</b> পর মুছে যাবে।\n"
        f"💡 এখনই কোনো বন্ধুকে ফরওয়ার্ড করে রাখুন!\n"
        f"আবার পেতে বটে নাম লিখে সার্চ করুন।</i>"
    )
    if movie["caption"]:
        caption = (
            f"🎬 <b>{esc(movie['title'])}</b>\n\n"
            f"{esc(movie['caption'])}\n\n"
            f"⏳ <i>ফাইলটি <b>{MOVIE_TTL} সেকেন্ড</b> পর মুছে যাবে — বন্ধুকে ফরওয়ার্ড করুন!</i>"
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
        # ইউজার মুভি পেয়েছে — তার সব pending upload notifications বন্ধ করো
        asyncio.create_task(db.fulfill_user_notifications(chat_id))

        if isinstance(target, CallbackQuery):
            await target.answer("✅ পাঠানো হয়েছে", show_alert=False)

        # মুভি ফাইল কয়েক সেকেন্ড পর ডিলিট, প্রতি সেকেন্ডে সতর্কবার্তা সহ
        if sent:
            asyncio.create_task(
                movie_delete_countdown(bot, chat_id, sent.message_id, MOVIE_TTL)
            )
        return True

    except TelegramBadRequest as exc:
        log.warning("Failed to deliver movie %s: %s", movie_id, exc)
        msg = "❌ দুঃখিত, ফাইলটি এখন পাঠানো যাচ্ছে না।"
        if isinstance(target, CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return False
    except Exception:
        log.exception("Unexpected delivery failure")
        if isinstance(target, CallbackQuery):
            await target.answer("❌ ফাইল পাঠানো যায়নি।", show_alert=True)
        else:
            try:
                await target.answer("❌ ফাইল পাঠানো যায়নি।")
            except Exception:
                pass
        return False


def _countdown_text(title: str, remaining: int, total: int, ad_url: str) -> str:
    bar_filled = round((total - remaining) / total * 10)
    bar = "🟣" * bar_filled + "⚫" * (10 - bar_filled)
    return (
        f"🎬 <b>{esc(title)}</b>\n\n"
        f"📺 বিজ্ঞাপন লিংক খুলুন, তারপর এখানেই মুভি আসবে!\n\n"
        f"{bar}\n"
        f"⏳ <b>{remaining}</b> সেকেন্ড বাকি...\n\n"
        f"<i>💡 বাটনে ক্লিক করে বিজ্ঞাপনটি দেখুন — {remaining} সেকেন্ড পর মুভি এখানে অটো আসবে।</i>"
    )


async def _ad_countdown_deliver(
    bot,
    chat_id: int,
    msg_id: int,
    token: str,
    movie_id: int,
    ad_url: str,
    title: str,
    total: int,
) -> None:
    """বাটন ক্লিকের পর বটে কাউন্টডাউন দেখায় এবং শেষে মুভি ডেলিভার করে।"""
    kb = InlineKeyboardBuilder()
    kb.button(text="📺 বিজ্ঞাপন দেখুন", url=ad_url)
    kb.adjust(1)

    for remaining in range(total, 0, -1):
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=_countdown_text(title, remaining, total, ad_url),
                reply_markup=kb.as_markup(),
                parse_mode="HTML",
            )
        except Exception:
            pass
        await asyncio.sleep(1)

    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass

    token_row = await db.get_ad_token(token)
    if not token_row or token_row["used"] == 1:
        return
    if not await db.claim_ad_token(token):
        return

    class _FakeMessage:
        def __init__(self, b, cid):
            self.bot = b
            self.chat = type("C", (), {"id": cid})()
            self.from_user = type("U", (), {"id": cid})()
        async def answer(self, text, **kw):
            return await self.bot.send_message(self.chat.id, text, **kw)

    fake = _FakeMessage(bot, chat_id)
    if await deliver_movie(fake, movie_id):
        await db.mark_ad_token_used(token)
    else:
        await db.release_ad_token(token)


async def send_ad_link(callback: CallbackQuery, movie_id: int) -> None:
    """Ad system চালু থাকলে বটেই কাউন্টডাউন শুরু করে, শেষে মুভি দেয়।"""
    movie = await db.get_movie(movie_id)
    if not movie:
        await callback.answer("⚠️ এই ফাইলটি লাইব্রেরিতে আর নেই।", show_alert=True)
        return

    wait_secs = max(5, int(await db.get_setting("ad_wait_seconds", "20") or 20))

    public_url = cfg.public_url
    token = await db.create_ad_token(movie_id, callback.from_user.id, wait_secs)
    raw_url = f"{public_url}/ad/{token}"
    ad_url = await shorten_url(raw_url)

    kb = InlineKeyboardBuilder()
    kb.button(text="📺 বিজ্ঞাপন দেখুন", url=ad_url)
    kb.adjust(1)

    init_text = _countdown_text(movie["title"], wait_secs, wait_secs, ad_url)

    try:
        await callback.message.edit_text(init_text, reply_markup=kb.as_markup())
        sent = callback.message
    except TelegramBadRequest:
        sent = await callback.message.answer(init_text, reply_markup=kb.as_markup())
    await callback.answer()

    asyncio.create_task(
        _ad_countdown_deliver(
            callback.bot,
            callback.from_user.id,
            sent.message_id,
            token,
            movie_id,
            ad_url,
            movie["title"],
            wait_secs,
        )
    )


async def deliver_movie_by_token(message: Message, token: str) -> None:
    """Ad countdown শেষে /start get_{token} থেকে movie পাঠায়।"""
    row = await db.get_ad_token(token)
    if not row:
        await message.answer("❌ লিংকটি আর কার্যকর নেই। আবার সার্চ করুন।")
        return
    if row["used"] == 1:
        await message.answer("⚠️ এই লিংকটি আগেই ব্যবহার হয়েছে। আবার সার্চ করুন।")
        return
    if row["used"] == -1:
        await message.answer("⏳ ফাইল পাঠানো হচ্ছে—একটু পরে আপনার inbox দেখুন।")
        return
    if time.time() > row["expires_at"]:
        await message.answer("⏰ লিংকের মেয়াদ শেষ হয়ে গেছে (২ ঘণ্টা)। আবার সার্চ করুন।")
        return
    if row["user_id"] != message.from_user.id:
        await message.answer("⛔ এই লিংকটি আপনার জন্য নয়।")
        return
    if not await db.claim_ad_token(token):
        await message.answer("⏳ এই ফাইলটি ইতিমধ্যে পাঠানো হচ্ছে বা পাঠানো হয়েছে।")
        return
    if await deliver_movie(message, row["movie_id"]):
        await db.mark_ad_token_used(token)
    else:
        await db.release_ad_token(token)


@router.callback_query(F.data.startswith("m:get:"))
async def cb_get_movie(callback: CallbackQuery) -> None:
    try:
        movie_id = int(callback.data.split(":")[2])
    except Exception:
        await callback.answer("ভুল আইডি।", show_alert=True)
        return

    ad_enabled = await db.get_setting("ad_enabled", "0")
    vip = await db.is_vip(callback.from_user.id)
    if ad_enabled == "1" and cfg.public_url and not vip:
        await send_ad_link(callback, movie_id)
    else:
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
