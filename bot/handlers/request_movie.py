"""মুভি রিকোয়েস্ট হ্যান্ডলার — ইউজার মুভির নাম রিকোয়েস্ট করতে পারবে।"""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..db import db
from ..utils import esc, schedule_delete, MSG_TTL

router = Router(name="request_movie")
log = logging.getLogger(__name__)


class RequestState(StatesGroup):
    waiting_title = State()
    waiting_note  = State()


def _cancel_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ বাতিল", callback_data="req:cancel")
    return kb.as_markup()


# ──────────────────────────── Normal start ──────────────────────────── #
@router.callback_query(F.data == "req:start")
async def cb_req_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RequestState.waiting_title)
    text = (
        "📝 <b>মুভি রিকোয়েস্ট</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "মুভির নাম <b>ইংরেজিতে</b> লিখে পাঠান:\n\n"
        "<i>উদাহরণ: </i><code>3 idiots</code>\n"
        "<code>money heist season 5</code>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=_cancel_kb())
        sent = callback.message
    except TelegramBadRequest:
        sent = await callback.message.answer(text, reply_markup=_cancel_kb())
    await callback.answer()
    asyncio.create_task(
        schedule_delete(callback.bot, callback.from_user.id, sent.message_id, MSG_TTL)
    )


# ───────── Pre-filled from "not found" search ─────────── #
@router.callback_query(F.data == "req:from_search")
async def cb_req_from_search(callback: CallbackQuery, state: FSMContext) -> None:
    """সার্চে না পেলে সরাসরি lastq দিয়ে রিকোয়েস্ট — কিন্তু confirm করাই হবে।"""
    user_id = callback.from_user.id
    title = (await db.get_setting(f"lastq:{user_id}", "")) or ""
    title = title.strip()

    if not title:
        # lastq নেই — নতুন রিকোয়েস্ট শুরু করো
        await cb_req_start(callback, state)
        return

    # সঠিক নাম কিনা confirm করাও — pre-fill দেখাও
    await state.update_data(title=title)
    await state.set_state(RequestState.waiting_note)

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ হ্যাঁ, এটাই রিকোয়েস্ট করুন", callback_data="req:skip_note")
    kb.button(text="✏️ নাম বদলাই", callback_data="req:change_title")
    kb.button(text="❌ বাতিল", callback_data="req:cancel")
    kb.adjust(1)

    text = (
        f"📝 <b>মুভি রিকোয়েস্ট — নিশ্চিত করুন</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎬 <b>মুভির নাম:</b> <code>{esc(title)}</code>\n\n"
        f"<i>এটাই রিকোয়েস্ট করবেন? নাকি নাম বদলাবেন?</i>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        sent = callback.message
    except TelegramBadRequest:
        sent = await callback.message.answer(text, reply_markup=kb.as_markup())
    await callback.answer()
    asyncio.create_task(
        schedule_delete(callback.bot, callback.from_user.id, sent.message_id, MSG_TTL)
    )


# ─────────────── নাম বদলাতে চাইলে ─────────────────── #
@router.callback_query(F.data == "req:change_title")
async def cb_req_change_title(callback: CallbackQuery, state: FSMContext) -> None:
    """নাম বদলে নতুন টাইপ করতে বলো।"""
    await state.set_state(RequestState.waiting_title)
    text = (
        "✏️ <b>নতুন নাম লিখুন</b>\n\n"
        "মুভির সঠিক নাম <b>ইংরেজিতে</b> পাঠান:"
    )
    try:
        await callback.message.edit_text(text, reply_markup=_cancel_kb())
        sent = callback.message
    except TelegramBadRequest:
        sent = await callback.message.answer(text, reply_markup=_cancel_kb())
    await callback.answer()
    asyncio.create_task(
        schedule_delete(callback.bot, callback.from_user.id, sent.message_id, MSG_TTL)
    )


# ──────────────────────────── Cancel ────────────────────────────── #
@router.callback_query(F.data == "req:cancel")
async def cb_req_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.edit_text("❌ <i>রিকোয়েস্ট বাতিল করা হয়েছে।</i>")
        asyncio.create_task(
            schedule_delete(callback.bot, callback.from_user.id,
                            callback.message.message_id, MSG_TTL)
        )
    except TelegramBadRequest:
        pass
    await callback.answer("বাতিল হয়েছে")


# ─────────────────────── Title received ─────────────────────────── #
@router.message(RequestState.waiting_title, F.text)
async def req_title_received(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, message.message_id, MSG_TTL)
    )
    if len(title) < 2:
        sent = await message.answer("কমপক্ষে ২ অক্ষরের নাম দিন।")
        asyncio.create_task(
            schedule_delete(message.bot, message.from_user.id, sent.message_id, MSG_TTL)
        )
        return
    if len(title) > 200:
        sent = await message.answer("নাম বেশি লম্বা — ২০০ অক্ষরের মধ্যে রাখুন।")
        asyncio.create_task(
            schedule_delete(message.bot, message.from_user.id, sent.message_id, MSG_TTL)
        )
        return

    await state.update_data(title=title)
    await state.set_state(RequestState.waiting_note)

    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ স্কিপ করুন", callback_data="req:skip_note")
    kb.button(text="❌ বাতিল", callback_data="req:cancel")
    kb.adjust(2)
    sent = await message.answer(
        f"✅ <b>নাম পেয়েছি:</b> <code>{esc(title)}</code>\n\n"
        "<i>চাইলে বিস্তারিত লিখুন (সিজন/ভাষা/বছর) — অথবা স্কিপ করুন।</i>",
        reply_markup=kb.as_markup(),
    )
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, sent.message_id, MSG_TTL)
    )


# ─────────────────────── Note step ──────────────────────────────── #
@router.callback_query(F.data == "req:skip_note", RequestState.waiting_note)
async def cb_req_skip_note(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _save_request(callback, state, data.get("title", ""), note=None)


@router.message(RequestState.waiting_note, F.text)
async def req_note_received(message: Message, state: FSMContext) -> None:
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, message.message_id, MSG_TTL)
    )
    data = await state.get_data()
    note = (message.text or "").strip()[:300]
    await _save_request(message, state, data.get("title", ""), note=note)


# ─────────────────────── Save & confirm ─────────────────────────── #
async def _save_request(target, state: FSMContext, title: str, note=None) -> None:
    if not title:
        if isinstance(target, CallbackQuery):
            await target.answer("মুভির নাম পাওয়া যায়নি।", show_alert=True)
        return

    user = target.from_user
    req_id = await db.add_movie_request(
        user.id,
        title,
        username=user.username,
        first_name=user.first_name,
        note=note,
    )
    await db.log_event("movie_request", {"user_id": user.id, "title": title, "req_id": req_id})
    await state.clear()

    note_line = f"\n📋 <i>{esc(note)}</i>" if note else ""
    text = (
        f"✅ <b>রিকোয়েস্ট পাঠানো হয়েছে!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎬 <b>মুভি:</b> <code>{esc(title)}</code>{note_line}\n"
        f"🔢 <b>রিকোয়েস্ট #</b>{req_id}\n\n"
        f"<i>যত দ্রুত সম্ভব আপলোড করব। আপলোড হলে সার্চ করলেই পাবেন।</i> 🍿"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🔍 এখনই সার্চ করুন", callback_data="search:start")
    kb.button(text="🏠 হোম", callback_data="home")
    kb.adjust(1)

    bot = target.bot
    chat_id = user.id

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb.as_markup())
            sent = target.message
        except TelegramBadRequest:
            sent = await target.message.answer(text, reply_markup=kb.as_markup())
        await target.answer()
    else:
        sent = await target.answer(text, reply_markup=kb.as_markup())

    asyncio.create_task(schedule_delete(bot, chat_id, sent.message_id, MSG_TTL))
