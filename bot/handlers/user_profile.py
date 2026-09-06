"""ইউজার প্রোফাইল — /mystats, /myrequests, /history, /subscribe"""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..db import db
from ..utils import esc, schedule_delete, MSG_TTL
from .common import watch_now_button

router = Router(name="user_profile")
log = logging.getLogger(__name__)

_STATUS_EMOJI = {"pending": "⏳", "done": "✅", "rejected": "❌"}


# ──────────────────────────────────────────────────────────────── #
# /mystats  (#15)
# ──────────────────────────────────────────────────────────────── #
@router.message(Command("mystats"))
async def cmd_mystats(message: Message) -> None:
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, message.message_id, MSG_TTL)
    )
    await _show_stats(message, message.from_user.id)


@router.callback_query(F.data == "profile:stats")
async def cb_mystats(callback: CallbackQuery) -> None:
    await _show_stats(callback, callback.from_user.id)


async def _show_stats(target, user_id: int) -> None:
    stats = await db.get_user_stats(user_id)
    user = stats["user"]
    if not user:
        if isinstance(target, CallbackQuery):
            await target.answer("ডেটা পাওয়া যায়নি।", show_alert=True)
        return

    joined = time.strftime("%d/%m/%Y", time.localtime(user["joined_at"]))
    sub_text = "✅ সক্রিয়" if stats["subscribed"] else "❌ বন্ধ"

    text = (
        f"📊 <b>প্রোফাইল</b>\n"
        f"👤 {esc(user['first_name'] or 'অজানা')} · <code>{user_id}</code>\n"
        f"📅 যোগ: {joined}\n"
        f"🎬 মুভি: {stats['downloads']} · 📝 রিকোয়েস্ট: {stats['requests']}\n"
        f"❤️ ফেভারিট: {stats['favorites']} · ⚠️ সতর্কতা: {stats['warnings']}\n"
        f"🔔 নোটিফিকেশন: {sub_text}"
    )

    kb = InlineKeyboardBuilder()
    sub_label = "🔕 নোটিফিকেশন বন্ধ করুন" if stats["subscribed"] else "🔔 নোটিফিকেশন চালু করুন"
    kb.button(text=sub_label, callback_data="subscribe:toggle")
    kb.button(text="📋 আমার রিকোয়েস্ট", callback_data="profile:requests")
    kb.button(text="📥 ডাউনলোড ইতিহাস", callback_data="profile:history")
    kb.button(text="❤️ ফেভারিট লিস্ট", callback_data="fav:list")
    kb.button(text="🏠 হোম", callback_data="home")
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    kb.adjust(1)

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb.as_markup())
            sent = target.message
        except TelegramBadRequest:
            sent = await target.message.answer(text, reply_markup=kb.as_markup())
        await target.answer()
    else:
        sent = await target.answer(text, reply_markup=kb.as_markup())

    asyncio.create_task(schedule_delete(target.bot, user_id, sent.message_id, MSG_TTL))


# ──────────────────────────────────────────────────────────────── #
# /myrequests  (#9)
# ──────────────────────────────────────────────────────────────── #
@router.message(Command("myrequests"))
async def cmd_myrequests(message: Message) -> None:
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, message.message_id, MSG_TTL)
    )
    await _show_requests(message, message.from_user.id)


@router.callback_query(F.data == "profile:requests")
async def cb_myrequests(callback: CallbackQuery) -> None:
    await _show_requests(callback, callback.from_user.id)


async def _show_requests(target, user_id: int) -> None:
    rows = await db.get_user_requests(user_id, limit=15)
    if not rows:
        text = (
            "📋 <b>আমার রিকোয়েস্ট</b>\n"
            "এখনো কোনো রিকোয়েস্ট নেই।"
        )
    else:
        lines = ["📋 <b>আমার রিকোয়েস্ট</b>"]
        for r in rows:
            emoji = _STATUS_EMOJI.get(r["status"], "❓")
            date_str = time.strftime("%d/%m", time.localtime(r["created_at"]))
            vote_text = f" 👍{r['vote_count']}" if r.get("vote_count") else ""
            lines.append(
                f"{emoji} <b>{esc(r['title'])}</b>{vote_text}\n"
                f"   <i>#{r['id']} · {date_str} · {r['status']}</i>"
            )
        text = "\n\n".join(lines)

    kb = InlineKeyboardBuilder()
    kb.button(text="🎬 নতুন রিকোয়েস্ট", callback_data="req:start")
    kb.button(text="📊 আমার পরিসংখ্যান", callback_data="profile:stats")
    kb.button(text="🏠 হোম", callback_data="home")
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    kb.adjust(1)

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb.as_markup())
            sent = target.message
        except TelegramBadRequest:
            sent = await target.message.answer(text, reply_markup=kb.as_markup())
        await target.answer()
    else:
        sent = await target.answer(text, reply_markup=kb.as_markup())

    asyncio.create_task(schedule_delete(target.bot, user_id, sent.message_id, MSG_TTL))


# ──────────────────────────────────────────────────────────────── #
# /history  (#12)
# ──────────────────────────────────────────────────────────────── #
@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, message.message_id, MSG_TTL)
    )
    await _show_history(message, message.from_user.id)


@router.callback_query(F.data == "profile:history")
async def cb_history(callback: CallbackQuery) -> None:
    await _show_history(callback, callback.from_user.id)


async def _show_history(target, user_id: int) -> None:
    rows = await db.get_download_history(user_id, limit=15)
    if not rows:
        text = (
            "📥 <b>ডাউনলোড ইতিহাস</b>\n"
            "এখনো কোনো ডাউনলোড নেই।"
        )
    else:
        lines = ["📥 <b>ডাউনলোড ইতিহাস</b>"]
        for r in rows:
            date_str = time.strftime("%d/%m %H:%M", time.localtime(r["created_at"]))
            title = esc(r["title"] or "অজানা")
            movie_id = r["movie_id"]
            if movie_id:
                lines.append(f"🎬 <a href='tg://resolve?domain=&start=movie_{movie_id}'>{title}</a> <i>({date_str})</i>")
            else:
                lines.append(f"🎬 {title} <i>({date_str})</i>")
        text = "\n".join(lines)

    kb = InlineKeyboardBuilder()
    kb.button(text="📊 আমার পরিসংখ্যান", callback_data="profile:stats")
    kb.button(text="🏠 হোম", callback_data="home")
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    kb.adjust(2)

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
            sent = target.message
        except TelegramBadRequest:
            sent = await target.message.answer(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
        await target.answer()
    else:
        sent = await target.answer(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)

    asyncio.create_task(schedule_delete(target.bot, user_id, sent.message_id, MSG_TTL))


# ──────────────────────────────────────────────────────────────── #
# নতুন মুভি নোটিফিকেশন সাবস্ক্রিপশন (#13)
# ──────────────────────────────────────────────────────────────── #
@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message) -> None:
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, message.message_id, MSG_TTL)
    )
    user_id = message.from_user.id
    is_sub = await db.is_subscribed(user_id)
    await db.set_subscription(user_id, not is_sub)
    if not is_sub:
        text = "🔔 নোটিফিকেশন চালু হয়েছে!"
    else:
        text = "🔕 <b>নোটিফিকেশন বন্ধ করা হয়েছে।</b>"
    sent = await message.answer(text)
    asyncio.create_task(schedule_delete(message.bot, user_id, sent.message_id, MSG_TTL))


@router.callback_query(F.data == "subscribe:toggle")
async def cb_subscribe_toggle(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    is_sub = await db.is_subscribed(user_id)
    await db.set_subscription(user_id, not is_sub)
    if not is_sub:
        await callback.answer("🔔 নোটিফিকেশন চালু হয়েছে!", show_alert=True)
    else:
        await callback.answer("🔕 নোটিফিকেশন বন্ধ হয়েছে।", show_alert=True)
    await _show_stats(callback, user_id)


# Home button shortcut
@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, message.message_id, MSG_TTL)
    )
    await _show_stats(message, message.from_user.id)
