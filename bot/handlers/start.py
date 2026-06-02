"""/start, /help, /cancel, AI সাহায্য মেনু — সব মেসেজ বাংলায়।"""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..db import db
from ..utils import esc, schedule_delete, MSG_TTL

router = Router(name="start")
log = logging.getLogger(__name__)


# ============================================================ #
# Helpers
# ============================================================ #
async def track_user(message_or_cb) -> bool:
    """ইউজার রেজিস্ট্রেশন করে। নতুন হলে True ফেরত দেয়।"""
    user = message_or_cb.from_user
    if not user:
        return False
    is_new = await db.upsert_user(
        user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language=user.language_code,
    )
    return is_new


DEFAULT_HOME_TEXT = (
    "🎬 <b>Moviex Hub</b> — মুভি সার্চ বট\n\n"
    "মুভির নাম <b>ইংরেজিতে</b> লিখুন, ফাইল পাবেন।\n"
    "<i>উদাহরণ: </i><code>3 idiots</code>, <code>kgf</code>"
)

NEW_USER_WELCOME = (
    "🎉 <b>স্বাগতম Moviex Hub-এ!</b>\n\n"
    "মুভির নাম <b>ইংরেজিতে</b> লিখুন — আমি ফাইল পাঠিয়ে দেব। 🍿\n\n"
    "<i>প্রথমবার চ্যানেলে জয়েন করতে হবে (একবারই)।</i>"
)


async def get_home_text() -> str:
    val = await db.get_bot_message("home", "")
    return val if val else DEFAULT_HOME_TEXT


def home_kb(popular=None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔍 মুভি সার্চ করুন", callback_data="search:start")
    kb.button(text="🎬 মুভি রিকোয়েস্ট করুন", callback_data="req:start")
    kb.button(text="📊 জনপ্রিয় মুভি", callback_data="popular:show")
    kb.button(text="🤖 সাহায্য", callback_data="ai:menu")
    kb.adjust(1)
    return kb.as_markup()


def new_user_kb(popular=None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if popular:
        for r in popular:
            emoji = {"video": "🎬", "document": "📁"}.get(r["file_type"], "🎥")
            kb.button(text=f"{emoji} {r['title'][:50]}", callback_data=f"m:get:{r['id']}")
        kb.adjust(1)
    kb.button(text="🔍 মুভি সার্চ করুন", callback_data="search:start")
    kb.button(text="ℹ️ কীভাবে ব্যবহার করব?", callback_data="ai:topic:howto")
    kb.adjust(1)
    return kb.as_markup()


async def render_home(target) -> None:
    text = await get_home_text()
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=home_kb())
        except TelegramBadRequest:
            await target.message.answer(text, reply_markup=home_kb())
        await target.answer()
    else:
        await target.answer(text, reply_markup=home_kb())


# ============================================================ #
# Commands
# ============================================================ #
@router.message(CommandStart())
async def cmd_start(
    message: Message, command: CommandObject, state: FSMContext
) -> None:
    await state.clear()
    is_new = await track_user(message)
    await db.log_event("start", {"user_id": message.from_user.id, "is_new": is_new})

    payload = (command.args or "").strip()
    if payload.startswith("movie_"):
        try:
            mid = int(payload.split("_", 1)[1])
        except ValueError:
            mid = 0
        if mid:
            from .callbacks import deliver_movie
            await deliver_movie(message, mid)
            return

    if is_new:
        popular = await db.get_popular_movies(limit=5)
        sent = await message.answer(NEW_USER_WELCOME, reply_markup=new_user_kb(popular))
        asyncio.create_task(
            schedule_delete(message.bot, message.from_user.id, sent.message_id, MSG_TTL)
        )
    else:
        await render_home(message)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await ai_render_menu(message)


@router.message(Command("ai"))
async def cmd_ai(message: Message) -> None:
    await ai_render_menu(message)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    sent = await message.answer("✅ বাতিল করা হয়েছে।")
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, sent.message_id, MSG_TTL)
    )


# ============================================================ #
# Callbacks
# ============================================================ #
@router.callback_query(F.data == "home")
async def cb_home(callback: CallbackQuery) -> None:
    await render_home(callback)


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# জনপ্রিয় মুভি
@router.callback_query(F.data == "popular:show")
async def cb_popular(callback: CallbackQuery) -> None:
    rows = await db.get_popular_movies(limit=10)
    if not rows:
        await callback.answer("এখনো কোনো মুভি সার্চ করা হয়নি।", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for r in rows:
        emoji = {"video": "🎬", "document": "📁", "audio": "🎵"}.get(r["file_type"], "📦")
        kb.button(
            text=f"{emoji} {r['title'][:50]}",
            callback_data=f"m:get:{r['id']}",
        )
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="🏠 হোম", callback_data="home"))

    text = (
        "🔥 <b>জনপ্রিয় মুভি</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>সবচেয়ে বেশিবার ডাউনলোড করা মুভিগুলো:</i>\n\n"
        "👇 বাটনে ক্লিক করলেই ফাইল পাবেন।"
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


# ============================================================ #
# AI FAQ মেনু
# ============================================================ #
AI_TOPICS = {
    "howto": {
        "title": "🎬 কীভাবে মুভি সার্চ করব?",
        "body": (
            "<b>সার্চের নিয়ম:</b>\n\n"
            "১️⃣ মুভির নাম <b>ইংরেজিতে</b> টাইপ করুন\n"
            "    <code>3 idiots</code> · <code>avengers endgame</code>\n\n"
            "২️⃣ বানান <b>সঠিক</b> হতে হবে\n\n"
            "৩️⃣ পুরো নাম মনে না থাকলে প্রথম কয়েকটি শব্দ লিখুন\n\n"
            "৪️⃣ রেজাল্টে ক্লিক করুন — ফাইল আসবে!\n\n"
            "💡 <i>না পেলে নিচে রিকোয়েস্ট করুন।</i>"
        ),
    },
    "verify": {
        "title": "✅ ভেরিফিকেশন কী?",
        "body": (
            "প্রথমবার সার্চের আগে আমাদের <b>২টি চ্যানেলে</b> জয়েন করতে হবে।\n\n"
            "🇮🇳 <b>হিন্দি ডাবিং</b> চ্যানেল\n"
            "🇧🇩 <b>বাংলা ডাবিং</b> চ্যানেল\n\n"
            'জয়েন করে <b>"✅ ভেরিফাই করুন"</b> বাটনে ক্লিক করুন।\n'
            "<i>এটি একবারই করতে হবে।</i>"
        ),
    },
    "language": {
        "title": "🔤 কোন ভাষায় সার্চ করব?",
        "body": (
            "সার্চ অবশ্যই <b>ইংরেজি</b> অক্ষরে করতে হবে।\n\n"
            "✅ সঠিক: <code>kgf chapter 2</code>\n"
            "❌ ভুল: <code>কেজিএফ</code>\n\n"
            "<i>বটের রিপ্লাই বাংলায় হবে — সার্চ ইংরেজিতে।</i>"
        ),
    },
    "nofile": {
        "title": "❓ মুভি পাচ্ছি না কেন?",
        "body": (
            "কারণ হতে পারে:\n\n"
            "• <b>বানান ভুল</b> — ইংরেজিতে আবার চেষ্টা করুন\n"
            "• মুভিটি এখনো লাইব্রেরিতে <b>নেই</b>\n"
            "• <b>বাংলায়</b> সার্চ করেছেন\n\n"
            "💡 <i>রিকোয়েস্ট করুন — আমরা যোগ করব।</i>"
        ),
    },
    "request": {
        "title": "📝 রিকোয়েস্ট কীভাবে করব?",
        "body": (
            "<b>মুভি রিকোয়েস্ট করার নিয়ম:</b>\n\n"
            "১️⃣ হোম থেকে <b>🎬 মুভি রিকোয়েস্ট করুন</b> বাটনে ক্লিক করুন\n\n"
            "২️⃣ মুভির নাম <b>ইংরেজিতে</b> লিখুন\n"
            "    <code>pushpa 2</code> · <code>leo 2023</code>\n\n"
            "৩️⃣ চাইলে সিজন/ভাষা/বছর উল্লেখ করুন\n\n"
            "৪️⃣ রিকোয়েস্ট নম্বর পাবেন — আপলোড হলে সার্চ করলেই পাবেন।"
        ),
    },
    "rules": {
        "title": "📜 নিয়মাবলী",
        "body": (
            "• বটে <b>স্প্যাম</b> করবেন না — অটো ব্যান হবেন\n"
            "• কয়েক সেকেন্ডে একের বেশি মেসেজ পাঠাবেন না\n"
            "• চ্যানেল ছেড়ে গেলে ভেরিফিকেশন হারাতে পারেন\n"
            "• ফাইল শেয়ার করলে <b>ক্রেডিট দিন</b>"
        ),
    },
}


def ai_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, t in AI_TOPICS.items():
        kb.button(text=t["title"], callback_data=f"ai:topic:{key}")
    kb.button(text="💬 সিনে-বাবুকে সরাসরি জিজ্ঞেস করুন", callback_data="ai:chat:start")
    kb.button(text="🏠 হোম", callback_data="home")
    kb.adjust(1)
    return kb.as_markup()


def ai_topic_kb(topic_key: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ পেছনে", callback_data="ai:menu")
    kb.button(text="💬 AI-কে জিজ্ঞেস করুন", callback_data="ai:chat:start")
    kb.button(text="🔍 সার্চ করুন", callback_data="search:start")
    kb.button(text="🎬 রিকোয়েস্ট করুন", callback_data="req:start")
    kb.button(text="🏠 হোম", callback_data="home")
    kb.adjust(2)
    return kb.as_markup()


AI_MENU_TEXT = (
    "🤖 <b>সাহায্য কেন্দ্র</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "<i>নিচে বিষয় বেছে নিন:</i>"
)


async def ai_render_menu(target) -> None:
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(AI_MENU_TEXT, reply_markup=ai_menu_kb())
            sent = target.message
        except TelegramBadRequest:
            sent = await target.message.answer(AI_MENU_TEXT, reply_markup=ai_menu_kb())
        await target.answer()
        asyncio.create_task(
            schedule_delete(target.bot, target.from_user.id, sent.message_id, MSG_TTL)
        )
    else:
        sent = await target.answer(AI_MENU_TEXT, reply_markup=ai_menu_kb())
        asyncio.create_task(
            schedule_delete(target.bot, target.from_user.id, sent.message_id, MSG_TTL)
        )


async def ai_show_topic(target, key: str) -> None:
    topic = AI_TOPICS.get(key)
    if not topic:
        if isinstance(target, CallbackQuery):
            await target.answer("টপিক পাওয়া যায়নি।", show_alert=True)
        return
    text = f"<b>{topic['title']}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{topic['body']}"
    kb = ai_topic_kb(key)
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
            sent = target.message
        except TelegramBadRequest:
            sent = await target.message.answer(text, reply_markup=kb)
        await target.answer()
        asyncio.create_task(
            schedule_delete(target.bot, target.from_user.id, sent.message_id, MSG_TTL)
        )
    else:
        sent = await target.answer(text, reply_markup=kb)
        asyncio.create_task(
            schedule_delete(target.bot, target.from_user.id, sent.message_id, MSG_TTL)
        )


@router.callback_query(F.data == "ai:menu")
async def cb_ai_menu(callback: CallbackQuery) -> None:
    await ai_render_menu(callback)


@router.callback_query(F.data.startswith("ai:topic:"))
async def cb_ai_topic(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 2)[2]
    await ai_show_topic(callback, key)


# ai:chat:start is handled by ai_chat.py router (registered before callbacks)
