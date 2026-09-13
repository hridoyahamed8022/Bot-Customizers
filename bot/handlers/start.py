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
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import settings
from ..db import db
from ..utils import esc, schedule_delete, MSG_TTL
from .common import watch_now_button

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
    "🎬 <b>Moviex Hub Team</b>\n"
    "ইংরেজিতে মুভির নাম লিখুন।\n"
    "উদাহরণ: <code>3 idiots</code>, <code>kgf</code>"
)

NEW_USER_WELCOME = (
    "🎉 <b>Moviex Hub Team-এ স্বাগতম!</b>\n"
    "ইংরেজিতে মুভির নাম লিখুন—ফাইল পাবেন। 🍿\n"
    "প্রথমবার চ্যানেলে জয়েন করতে হবে।"
)


async def get_home_text() -> str:
    val = await db.get_bot_message("home", "")
    return val if val else DEFAULT_HOME_TEXT


def home_kb(popular=None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    kb.button(text="🔍 মুভি সার্চ করুন", callback_data="search:start")
    kb.button(text="🎬 মুভি রিকোয়েস্ট করুন", callback_data="req:start")
    kb.button(text="📊 জনপ্রিয় মুভি", callback_data="popular:show")
    kb.button(text="🤖 সাহায্য", callback_data="ai:menu")
    kb.adjust(1)
    return kb.as_markup()


def new_user_kb(popular=None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    watch = watch_now_button()
    if watch:
        kb.row(watch)
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

    # Ad system: /start get_{token} → deliver movie after countdown
    if payload.startswith("get_"):
        token = payload[4:]
        from .callbacks import deliver_movie_by_token
        await deliver_movie_by_token(message, token)
        return

    if payload.startswith("movie_"):
        try:
            mid = int(payload.split("_", 1)[1])
        except ValueError:
            mid = 0
        if mid:
            from .callbacks import deliver_movie
            await deliver_movie(message, mid)
            return

    # ওয়েলকাম ছবি সেট থাকলে নতুন + পুরনো উভয় ইউজারই ছবিসহ মেসেজ পাবে
    welcome_photo_file_id = await db.get_setting("welcome_photo_file_id", "")
    welcome_photo_caption = await db.get_setting("welcome_photo_caption", "")

    if welcome_photo_file_id:
        popular = await db.get_popular_movies(limit=5)
        home_text = await get_home_text()
        caption_text = (
            welcome_photo_caption.strip()
            if welcome_photo_caption and welcome_photo_caption.strip()
            else home_text
        )
        if len(caption_text) > 1024:
            caption_text = caption_text[:1020] + "…"
        sent = await message.answer_photo(
            photo=welcome_photo_file_id,
            caption=caption_text,
            caption_entities=None,
            parse_mode="HTML",
            reply_markup=home_kb(),
        )
        asyncio.create_task(
            schedule_delete(message.bot, message.from_user.id, sent.message_id, MSG_TTL)
        )
    elif is_new:
        popular = await db.get_popular_movies(limit=5)
        sent = await message.answer(
            NEW_USER_WELCOME,
            parse_mode="HTML",
            reply_markup=new_user_kb(popular),
        )
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
    watch = watch_now_button()
    if watch:
        kb.row(watch)

    text = (
        "🔥 <b>জনপ্রিয় মুভি</b>\n"
        "বেশি ডাউনলোড হওয়া মুভি\n\n"
        "👇 পছন্দের মুভিতে চাপুন।"
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
            "নাম ইংরেজিতে লিখুন:\n"
            "<code>3 idiots</code> · <code>avengers endgame</code>\n\n"
            "রেজাল্টে চাপুন। না পেলে রিকোয়েস্ট করুন।"
        ),
    },
    "verify": {
        "title": "✅ ভেরিফিকেশন কী?",
        "body": (
            "প্রথম সার্চের আগে চ্যানেলে জয়েন করুন।\n"
            "তারপর <b>✅ ভেরিফাই করুন</b> চাপুন।\n"
            "শুধু একবার।"
        ),
    },
    "language": {
        "title": "🔤 কোন ভাষায় সার্চ করব?",
        "body": (
            "সার্চ <b>ইংরেজিতে</b> করুন।\n\n"
            "✅ সঠিক: <code>kgf chapter 2</code>\n"
            "❌ ভুল: <code>কেজিএফ</code>\n\n"
            "রিপ্লাই বাংলায় পাবেন।"
        ),
    },
    "nofile": {
        "title": "❓ মুভি পাচ্ছি না কেন?",
        "body": (
            "• বানান ভুল\n"
            "• লাইব্রেরিতে নেই\n"
            "• বাংলায় সার্চ\n\n"
            "নিচে রিকোয়েস্ট করুন।"
        ),
    },
    "request": {
        "title": "📝 রিকোয়েস্ট কীভাবে করব?",
        "body": (
            "রিকোয়েস্ট বাটনে চাপুন।\n"
            "নাম ইংরেজিতে লিখুন।\n"
            "চাইলে সিজন/ভাষা/বছর দিন।"
        ),
    },
    "rules": {
        "title": "📜 নিয়মাবলী",
        "body": (
            "• স্প্যাম করলে ব্যান\n"
            "• একসাথে অনেক মেসেজ নয়\n"
            "• চ্যানেল ছাড়লে ভেরিফিকেশন যাবে\n"
            "• শেয়ারে ক্রেডিট দিন"
        ),
    },
}


def ai_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, t in AI_TOPICS.items():
        kb.button(text=t["title"], callback_data=f"ai:topic:{key}")
    kb.button(text="💬 সিনে-বাবুকে সরাসরি জিজ্ঞেস করুন", callback_data="ai:chat:start")
    kb.button(text="🏠 হোম", callback_data="home")
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    kb.adjust(1)
    return kb.as_markup()


def ai_topic_kb(topic_key: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ পেছনে", callback_data="ai:menu")
    kb.button(text="💬 AI-কে জিজ্ঞেস করুন", callback_data="ai:chat:start")
    kb.button(text="🔍 সার্চ করুন", callback_data="search:start")
    kb.button(text="🎬 রিকোয়েস্ট করুন", callback_data="req:start")
    kb.button(text="🏠 হোম", callback_data="home")
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    kb.adjust(2)
    return kb.as_markup()


AI_MENU_TEXT = (
    "🤖 <b>সাহায্য কেন্দ্র</b>\n"
    "নিচে একটি বিষয় বেছে নিন:"
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
    text = f"<b>{topic['title']}</b>\n\n{topic['body']}"
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
