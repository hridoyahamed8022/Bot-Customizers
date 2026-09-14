"""DM সার্চ — শুধু সঠিক ইংরেজি বানানে। প্রথমবার ভেরিফিকেশন বাধ্যতামূলক।"""
from __future__ import annotations

import asyncio
import logging
import re

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..db import db
from ..movie_links import movie_web_url
from ..utils import esc, schedule_delete, MSG_TTL
from .common import watch_now_button

router = Router(name="search")
log = logging.getLogger(__name__)

PAGE = 8

_ENGLISH_RE = re.compile(r"^[A-Za-z0-9 .,'\"!?\-:;()\[\]/&_+#@]+$")
_LETTER_RE = re.compile(r"[A-Za-z]")


def is_english_query(q: str) -> bool:
    if not q or not _ENGLISH_RE.match(q):
        return False
    return bool(_LETTER_RE.search(q))


# ============================================================ #
# Verification
# ============================================================ #
async def _verify_channels():
    rows = await db.list_force_join()
    return rows[:2]


def verify_kb(channels) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for ch in channels:
        url = ch["invite_url"] or (
            f"https://t.me/{ch['username']}" if ch["username"] else None
        )
        if url:
            label = ch["title"] or ch["username"] or "চ্যানেল"
            kb.button(text=f"📢 {label}-এ জয়েন করুন", url=url)
    kb.button(text="✅ ভেরিফাই করুন", callback_data="verify:check")
    kb.button(text="❓ কেন জয়েন করতে হবে?", callback_data="ai:topic:verify")
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    kb.adjust(1)
    return kb.as_markup()


VERIFY_TEXT = (
    "🔐 <b>ভেরিফিকেশন প্রয়োজন</b>\n"
    "চ্যানেলে জয়েন করে <b>✅ ভেরিফাই</b> চাপুন।\n"
    "শুধু একবার করতে হবে।"
)


async def _send_verify_prompt(target) -> None:
    channels = await _verify_channels()
    if not channels:
        await db.mark_verified(target.from_user.id, True)
        return

    kb = verify_kb(channels)
    bot = target.bot
    chat_id = target.from_user.id

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(VERIFY_TEXT, reply_markup=kb)
            sent = target.message
        except TelegramBadRequest:
            sent = await target.message.answer(VERIFY_TEXT, reply_markup=kb)
        await target.answer()
    else:
        sent = await target.answer(VERIFY_TEXT, reply_markup=kb)

    asyncio.create_task(schedule_delete(bot, chat_id, sent.message_id, MSG_TTL))


async def _check_membership(bot, user_id: int) -> tuple[bool, list]:
    pending = []
    for ch in await _verify_channels():
        try:
            member = await bot.get_chat_member(ch["chat_id"], user_id)
            status = getattr(member.status, "value", member.status)
            # Telegram can return `restricted` for a user who is not
            # actually a member.  Treat that case as pending as well.
            restricted_not_member = (
                status == "restricted" and getattr(member, "is_member", True) is False
            )
            if status in ("left", "kicked") or restricted_not_member:
                pending.append(ch)
        except Exception:
            pending.append(ch)
    return (len(pending) == 0, pending)


@router.callback_query(F.data == "verify:check")
async def cb_verify_check(callback: CallbackQuery) -> None:
    ok, pending = await _check_membership(callback.bot, callback.from_user.id)
    if not ok:
        names = ", ".join((c["title"] or c["username"] or "চ্যানেল") for c in pending)
        await callback.answer(f"⚠️ এখনো জয়েন করেননি: {names}", show_alert=True)
        return
    await db.mark_verified(callback.from_user.id, True)
    await db.log_event("verified", {"user_id": callback.from_user.id})
    await callback.answer("✅ ভেরিফিকেশন সম্পন্ন!", show_alert=True)
    q = await db.get_setting(f"lastq:{callback.from_user.id}", "")
    if q:
        await _do_search(callback, q, 0)
    else:
        try:
            await callback.message.edit_text(
                "✅ <b>ভেরিফিকেশন সম্পন্ন!</b>\n\n"
                "এবার মুভির নাম <b>ইংরেজিতে</b> লিখুন।\n"
                "<i>উদাহরণ: </i><code>3 idiots</code>"
            )
            asyncio.create_task(
                schedule_delete(callback.bot, callback.from_user.id,
                                callback.message.message_id, MSG_TTL)
            )
        except TelegramBadRequest:
            pass


# ============================================================ #
# Channel join buttons
# ============================================================ #
async def _channel_buttons() -> list[InlineKeyboardButton]:
    buttons: list[InlineKeyboardButton] = []
    try:
        channels = await db.list_channels()
        for ch in channels:
            url = ch["invite_url"] or (
                f"https://t.me/{ch['username']}" if ch["username"] else None
            )
            if url:
                label = ch["title"] or ch["username"] or "আমাদের চ্যানেল"
                emoji = "🔔" if ch["type"] == "backup" else (
                    "🗣" if ch["type"] == "group" else "📢"
                )
                buttons.append(InlineKeyboardButton(text=f"{emoji} {label}", url=url))
    except Exception:
        log.exception("_channel_buttons failed")
    return buttons


# ============================================================ #
# Keyboards
# ============================================================ #
async def _results_kb(rows, page: int, total: int, user_id: int,
                      channel_btns: list | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for r in rows:
        emoji = {
            "video": "🎬", "document": "📁", "audio": "🎵",
            "animation": "🎞", "voice": "🎙", "video_note": "🎥", "photo": "🖼",
        }.get(r["file_type"], "📦")
        title = r["title"][:55]
        url = await movie_web_url(user_id, r["id"])
        kb.button(
            text=f"{emoji} {title}",
            url=url or None,
            callback_data=None if url else f"m:view:{r['id']}",
        )
    kb.adjust(1)

    pages = max(1, (total + PAGE - 1) // PAGE)
    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️ আগের", callback_data=f"s:p:{page - 1}")
    nav.button(text=f"📄 {page + 1}/{pages}", callback_data="noop")
    if page < pages - 1:
        nav.button(text="পরের ➡️", callback_data=f"s:p:{page + 1}")
    nav.adjust(3)
    kb.attach(nav)

    # রিকোয়েস্ট বাটন — req:start ব্যবহার করো (pre-fill নয়, সরাসরি টাইপ করাবে)
    kb.row(
        InlineKeyboardButton(text="📝 রিকোয়েস্ট", callback_data="req:start"),
        InlineKeyboardButton(text="🏠 হোম", callback_data="home"),
    )
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    if channel_btns:
        for btn in channel_btns:
            kb.row(btn)
    return kb.as_markup()


async def _no_results_kb(suggestions=None, query: str = "", user_id: int = 0,
                   channel_btns: list | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if suggestions:
        for r in suggestions:
            emoji = {
                "video": "🎬", "document": "📁", "audio": "🎵",
                "animation": "🎞",
            }.get(r["file_type"], "📦")
            url = await movie_web_url(user_id, r["id"])
            kb.button(
                text=f"{emoji} {r['title'][:50]}",
                url=url or None,
                callback_data=None if url else f"m:view:{r['id']}",
            )
        kb.adjust(1)
    # "not found" page থেকে request করলে lastq-ই সঠিক movie name
    kb.button(text="📝 এই মুভিটি রিকোয়েস্ট করুন", callback_data="req:from_search")
    kb.button(text="🔍 আবার সার্চ করুন", callback_data="search:start")
    kb.button(text="🏠 হোম", callback_data="home")
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    kb.adjust(1)
    if channel_btns:
        for btn in channel_btns:
            kb.row(btn)
    return kb.as_markup()


# ============================================================ #
# Core search
# ============================================================ #
async def _do_search(message_or_cb, query: str, page: int = 0) -> None:
    user_id = message_or_cb.from_user.id
    bot = message_or_cb.bot
    await db.set_setting(f"lastq:{user_id}", query)

    rows = await db.search_movies(query, limit=PAGE, offset=page * PAGE)
    total = await db.count_search(query)
    channel_btns = await _channel_buttons()

    if not rows:
        suggestions = await db.suggest_similar(query, limit=3)

        no_results_msg = await db.get_bot_message("no_results", "")
        if no_results_msg:
            text = f"{no_results_msg}\n\n🔎 <code>{esc(query)}</code>"
        else:
            text = (
                f"😕 <b>'{esc(query)}'</b> — পাওয়া যায়নি\n"
                f"বানান দেখুন বা রিকোয়েস্ট করুন।"
            )

        if suggestions:
            text += (
                f"\n\n💡 <b>এগুলো কি?</b>\n"
                + "\n".join(f"• <i>{esc(r['title'])}</i>" for r in suggestions)
            )
        else:
            text += f"\n\n📝 নিচে রিকোয়েস্ট করুন।"

        if channel_btns:
            text += "\n\n📢 আমাদের চ্যানেল"

        kb = await _no_results_kb(suggestions, query, user_id, channel_btns)
        if isinstance(message_or_cb, CallbackQuery):
            try:
                await message_or_cb.message.edit_text(text, reply_markup=kb)
                sent = message_or_cb.message
            except TelegramBadRequest:
                sent = await message_or_cb.message.answer(text, reply_markup=kb)
            await message_or_cb.answer()
        else:
            sent = await message_or_cb.answer(text, reply_markup=kb)

        asyncio.create_task(schedule_delete(bot, user_id, sent.message_id, MSG_TTL))
        return

    pages = max(1, (total + PAGE - 1) // PAGE)
    text = (
        f"🔍 <b>{total}টি ফলাফল</b> — <code>{esc(query)}</code>\n"
        f"পৃষ্ঠা {page + 1}/{pages} · মুভি বাছুন"
    )
    if channel_btns:
        text += "\n\n📢 আমাদের চ্যানেল"

    kb = await _results_kb(rows, page, total, user_id, channel_btns)
    if isinstance(message_or_cb, CallbackQuery):
        try:
            await message_or_cb.message.edit_text(text, reply_markup=kb)
            sent = message_or_cb.message
        except TelegramBadRequest:
            sent = await message_or_cb.message.answer(text, reply_markup=kb)
        await message_or_cb.answer()
    else:
        sent = await message_or_cb.answer(text, reply_markup=kb)

    asyncio.create_task(schedule_delete(bot, user_id, sent.message_id, MSG_TTL))


async def _gate_search(message_or_cb, query: str, page: int = 0) -> None:
    user_id = message_or_cb.from_user.id
    bot = message_or_cb.bot

    if not is_english_query(query):
        text = (
            "⚠️ <b>ইংরেজিতে সার্চ করুন</b>\n\n"
            "উদাহরণ: <code>3 idiots</code>"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="ℹ️ কীভাবে সার্চ করব?", callback_data="ai:topic:howto")
        kb.button(text="🏠 হোম", callback_data="home")
        watch = watch_now_button()
        if watch:
            kb.row(watch)
        kb.adjust(2)
        if isinstance(message_or_cb, CallbackQuery):
            await message_or_cb.answer("ইংরেজিতে সার্চ করুন", show_alert=True)
            try:
                sent = await message_or_cb.message.answer(text, reply_markup=kb.as_markup())
                asyncio.create_task(schedule_delete(bot, user_id, sent.message_id, MSG_TTL))
            except TelegramBadRequest:
                pass
        else:
            sent = await message_or_cb.answer(text, reply_markup=kb.as_markup())
            asyncio.create_task(schedule_delete(bot, user_id, sent.message_id, MSG_TTL))
        return

    if not await db.is_verified(user_id):
        await db.set_setting(f"lastq:{user_id}", query)
        await _send_verify_prompt(message_or_cb)
        return

    await _do_search(message_or_cb, query, page)


# ============================================================ #
# Entrypoints
# ============================================================ #
@router.message(Command("search"))
async def cmd_search(message: Message, command: CommandObject) -> None:
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, message.message_id, MSG_TTL)
    )
    q = (command.args or "").strip()
    if not q:
        sent = await message.answer(
            "ব্যবহার: <code>/search avengers</code>"
        )
        asyncio.create_task(
            schedule_delete(message.bot, message.from_user.id, sent.message_id, MSG_TTL)
        )
        return
    await _gate_search(message, q, 0)


@router.message(F.chat.type == ChatType.PRIVATE, F.text & ~F.text.startswith("/"))
async def on_private_text(message: Message) -> None:
    q = (message.text or "").strip()
    if not q:
        return
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, message.message_id, MSG_TTL)
    )
    if len(q) < 2:
        sent = await message.answer("কমপক্ষে ২টি অক্ষর দিন।")
        asyncio.create_task(
            schedule_delete(message.bot, message.from_user.id, sent.message_id, MSG_TTL)
        )
        return
    await _gate_search(message, q, 0)


@router.callback_query(F.data == "search:start")
async def cb_search_start(callback: CallbackQuery) -> None:
    text = (
        "🔍 <b>মুভি সার্চ করুন</b>\n"
        "নাম ইংরেজিতে লিখুন।\n"
        "উদাহরণ: <code>3 idiots</code> · <code>kgf</code>"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="📝 রিকোয়েস্ট করুন", callback_data="req:start")
    kb.button(text="🏠 হোম", callback_data="home")
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    kb.adjust(2)
    try:
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        sent = callback.message
    except TelegramBadRequest:
        sent = await callback.message.answer(text, reply_markup=kb.as_markup())
    await callback.answer()
    asyncio.create_task(
        schedule_delete(callback.bot, callback.from_user.id, sent.message_id, MSG_TTL)
    )


@router.callback_query(F.data.startswith("s:p:"))
async def cb_paginate(callback: CallbackQuery) -> None:
    try:
        page = int(callback.data.split(":")[2])
    except Exception:
        page = 0
    q = await db.get_setting(f"lastq:{callback.from_user.id}", "")
    if not q:
        await callback.answer("সার্চ মুছে গেছে — আবার নাম লিখুন।", show_alert=True)
        return
    await _do_search(callback, q, page)
