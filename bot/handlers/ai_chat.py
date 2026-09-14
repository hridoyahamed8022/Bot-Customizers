"""AI চ্যাটবট — বটের ডেটাবেজ সার্চ + মুভি বিশেষজ্ঞ + বট গাইড।"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..db import db
from ..utils import schedule_delete, MSG_TTL
from .common import watch_now_button

log = logging.getLogger(__name__)
router = Router(name="ai_chat")

_OPENAI_BASE_URL = os.getenv("AI_INTEGRATIONS_OPENAI_BASE_URL", "").strip()
_OPENAI_API_KEY  = os.getenv("AI_INTEGRATIONS_OPENAI_API_KEY", "sk-dummy").strip()
_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
_GEMINI_MODEL = "gemini-2.5-flash"
_GEMINI_UNAVAILABLE = False

_TODAY = date.today().isoformat()

_SYSTEM_PROMPT = f"""তুমি "Moviex Hub Team" বটের AI সহকারী — নাম "সিনে-বাবু"। আজ: {_TODAY}

বাংলায় সংক্ষিপ্ত ও সরাসরি উত্তর দাও। emoji মাঝেমধ্যে।

🎬 Moviex Hub Team: মুভি/সিরিজ/নাটক ডাউনলোড বট।
- ইংরেজিতে নাম লিখলে ফাইল পাওয়া যায়।
- প্রথমবার ২টি চ্যানেলে জয়েন করতে হয়।
- সঠিক: "KGF Chapter 2" | ভুল: "কেজিএফ"

ইউজার মুভি চাইলে → search_movies_in_db কল করো।
বট স্ট্যাটস চাইলে → get_bot_stats কল করো।
অন্য প্রশ্নে → সরাসরি বাংলায় উত্তর দাও।
রাজনীতি/ধর্ম/অন্য পাইরেসি সাইট → এড়িয়ে যাও।"""

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_movies_in_db",
            "description": "বটের ডেটাবেজে মুভি সার্চ। যেকোনো ভাষায় নাম দিলে ইংরেজিতে রূপান্তর করে খোঁজো।",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "ইংরেজি মুভির নাম, যেমন: 'KGF Chapter 2'"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bot_stats",
            "description": "বটের মোট মুভি ও সর্বাধিক ডাউনলোড হওয়া মুভি।",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_MAX_HISTORY = 8


async def _exec_search(query: str, rows_collector: List[Any]) -> str:
    q = (query or "").strip()
    if not q:
        return "কোনো সার্চ কোয়েরি দেওয়া হয়নি।"

    rows, total = await asyncio.gather(
        db.search_movies(q, limit=8, offset=0),
        db.count_search(q),
    )

    if rows:
        for r in rows[:8]:
            if not any(x["id"] == r["id"] for x in rows_collector):
                rows_collector.append(dict(r))
        lines = [f"✅ '{q}' — {total}টি ফাইল। নিচে বাছুন:"]
        if total > 8:
            lines.append(f"(আরো {total - 8}টি সরাসরি সার্চ করলে দেখা যাবে)")
        return "\n".join(lines)

    suggestions = await db.suggest_similar(q, limit=5)
    if suggestions:
        for r in suggestions[:5]:
            if not any(x["id"] == r["id"] for x in rows_collector):
                rows_collector.append(dict(r))
        return f"🔍 '{q}' সরাসরি নেই। এগুলো কি খুঁজছিলে?"

    return f"❌ '{q}' এখনো বটে নেই। রিকোয়েস্ট করলে যোগ করা হবে।"


async def _exec_bot_stats() -> str:
    total, popular = await asyncio.gather(
        db.count_movies(),
        db.get_popular_movies(limit=5),
    )
    lines = [f"📊 মোট মুভি: {total}টি"]
    if popular:
        lines.append("🏆 সর্বাধিক ডাউনলোড:")
        for i, r in enumerate(popular, 1):
            lines.append(f"{i}. {r['title']} ({r['hits']} বার)")
    return "\n".join(lines)


async def _local_maya(
    user_message: str,
) -> Tuple[str, List[Any]]:
    """Useful database assistant when no external model is available."""
    text = (user_message or "").strip()
    search_hint = re.sub(r"[^A-Za-z0-9 .,'!?&():_-]+", " ", text).strip()
    found_rows = await db.search_movies(search_hint or text, limit=8, offset=0)
    if found_rows:
        return (
            f"✅ “{text}” নামে {len(found_rows)}টি movie পেয়েছি।\n"
            "নিচের title-এ চাপুন। File পেতে আগে ad দেখে Download করতে হবে।",
            found_rows,
        )

    lower = text.lower()
    if any(word in lower for word in ("popular", "জনপ্রিয়", "trending", "হিট")):
        rows = await db.get_popular_movies(limit=8)
        return "🔥 এগুলো এখন সবচেয়ে জনপ্রিয়। নিচে movie বেছে নিন।", rows
    if any(word in lower for word in ("upcoming", "আসছে", "নতুন", "release")):
        await db.list_upcoming_movies(8)
        return "✨ সামনে আসছে এমন movie-গুলোর তালিকা Upcoming section-এ দেখুন।", []
    if any(word in lower for word in ("stat", "কত", "মোট", "stats")):
        return await _exec_bot_stats(), []
    return (
        "আমি Maya। Movie-এর English নাম লিখে search করুন—"
        "result-এর title-এ চাপলে detail page খুলবে। File পেতে ad দেখে Download করতে হবে।",
        [],
    )


async def _ask_gemini(
    history: List[Dict[str, Any]],
    user_message: str,
) -> Tuple[str, List[Any]]:
    """Gemini-backed Maya fallback for deployments without the Replit AI proxy."""
    found_rows: List[Any] = []
    global _GEMINI_UNAVAILABLE
    if not _GEMINI_API_KEY or _GEMINI_UNAVAILABLE:
        return await _local_maya(user_message)

    try:
        search_hint = re.sub(r"[^A-Za-z0-9 .,'!?&():_-]+", " ", user_message).strip()
        found_rows = await db.search_movies(search_hint or user_message, limit=8, offset=0)
        context = "কোনো সরাসরি database match নেই।"
        if found_rows:
            context = "Database-এ পাওয়া movie:\n" + "\n".join(
                f"- {row['title']} (id: {row['id']})" for row in found_rows
            )

        contents: List[Dict[str, Any]] = []
        for item in history[-_MAX_HISTORY:]:
            role = "model" if item.get("role") == "assistant" else "user"
            contents.append({
                "role": role,
                "parts": [{"text": str(item.get("content", ""))[:1200]}],
            })
        contents.append({
            "role": "user",
            "parts": [{
                "text": (
                    f"{user_message}\n\n"
                    f"{context}\n\n"
                    "Database match থাকলে শুধু সেগুলো নিয়েই সাহায্য করো। "
                    "ফাইল পেতে ad দেখে Download চাপতে হবে—এটি পরিষ্কার করে বলো।"
                ),
            }],
        })
        payload = {
            "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.65, "maxOutputTokens": 8192},
        }
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{_GEMINI_MODEL}:generateContent?key={_GEMINI_API_KEY}"
        )
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                if response.status >= 400:
                    log.warning("Gemini request returned HTTP %s", response.status)
                    _GEMINI_UNAVAILABLE = True
                    return await _local_maya(user_message)
                data = await response.json()
        candidates = data.get("candidates") or []
        parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
        reply = "".join(str(part.get("text", "")) for part in parts).strip()
        return reply or "উত্তর পাওয়া যায়নি।", found_rows
    except Exception as exc:
        log.warning("Gemini call failed: %s", exc)
        return await _local_maya(user_message)


async def _ask_openai(
    history: List[Dict[str, Any]],
    user_message: str,
) -> Tuple[str, List[Any]]:
    found_rows: List[Any] = []

    if not _OPENAI_BASE_URL:
        return await _ask_gemini(history, user_message)
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=_OPENAI_API_KEY, base_url=_OPENAI_BASE_URL)

        search_hint = re.sub(r"[^A-Za-z0-9 .,'!?&():_-]+", " ", user_message).strip()
        found_rows = await db.search_movies(search_hint or user_message, limit=8, offset=0)
        if found_rows:
            context = "Database-এ পাওয়া movie:\n" + "\n".join(
                f"- {row['title']} (id: {row['id']})" for row in found_rows
            )
        else:
            context = "Database-এ সরাসরি match পাওয়া যায়নি।"

        messages: List[Dict[str, Any]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
        messages.extend(history[-(_MAX_HISTORY * 2):])
        messages.append({
            "role": "user",
            "content": (
                f"{user_message}\n\n{context}\n\n"
                "Database match থাকলে শুধু সেগুলো নিয়ে সাহায্য করো। "
                "ফাইল পেতে ad দেখে Download চাপতে হবে—এটি পরিষ্কার করে বলো।"
            ),
        })
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_completion_tokens=8192,
            temperature=0.65,
        )
        reply = (resp.choices[0].message.content or "").strip()
        return reply or "উত্তর পাওয়া যায়নি।", found_rows

    except Exception as exc:
        log.exception("OpenAI call failed: %s", exc)
        if _GEMINI_API_KEY:
            return await _ask_gemini(history, user_message)
        return await _local_maya(user_message)


class AIChatState(StatesGroup):
    chatting = State()


def _exit_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ চ্যাট শেষ করুন", callback_data="ai:chat:exit")
    kb.button(text="🏠 হোম", callback_data="home")
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    kb.adjust(2)
    return kb.as_markup()


_FILE_EMOJI = {
    "video": "🎬", "document": "📁", "audio": "🎵",
    "animation": "🎞", "voice": "🎙", "video_note": "🎥",
}


def _result_kb(found_rows: List[Any]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for r in found_rows[:8]:
        emoji = _FILE_EMOJI.get(r.get("file_type", ""), "📦")
        title = (r.get("title") or "")[:55]
        kb.button(text=f"{emoji} {title}", callback_data=f"m:view:{r['id']}")
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(text="🎬 রিকোয়েস্ট করুন", callback_data="req:from_search"),
        InlineKeyboardButton(text="❌ চ্যাট বন্ধ", callback_data="ai:chat:exit"),
    )
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    return kb.as_markup()


def _chat_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔍 সার্চ করুন", callback_data="search:start")
    kb.button(text="🎬 রিকোয়েস্ট", callback_data="req:from_search")
    kb.button(text="❌ চ্যাট বন্ধ", callback_data="ai:chat:exit")
    watch = watch_now_button()
    if watch:
        kb.row(watch)
    kb.adjust(2, 1)
    return kb.as_markup()


_WELCOME_MSG = (
    "🎬 <b>সিনে-বাবু এখানে!</b>\n\n"
    "মুভির নাম বা প্রশ্ন লিখুন।\n"
    "বাংলা/ইংরেজি দুটোই চলবে।"
)


@router.callback_query(F.data == "ai:chat:start")
async def cb_ai_chat_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AIChatState.chatting)
    await state.update_data(history=[])
    try:
        await callback.message.edit_text(_WELCOME_MSG, reply_markup=_exit_kb())
        sent = callback.message
    except TelegramBadRequest:
        sent = await callback.message.answer(_WELCOME_MSG, reply_markup=_exit_kb())
    await callback.answer()
    asyncio.create_task(
        schedule_delete(callback.bot, callback.from_user.id, sent.message_id, MSG_TTL)
    )


@router.callback_query(F.data == "ai:chat:exit")
async def cb_ai_chat_exit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    from .start import render_home
    await render_home(callback)


@router.message(AIChatState.chatting, F.text)
async def ai_chat_message(message: Message, state: FSMContext) -> None:
    user_text = (message.text or "").strip()
    if not user_text:
        return

    # ইউজারের মেসেজ ডিলিট + typing indicator একসাথে
    await asyncio.gather(
        message.bot.send_chat_action(message.chat.id, ChatAction.TYPING),
        message.bot.delete_message(message.chat.id, message.message_id),
        return_exceptions=True,
    )

    thinking_msg = await message.answer("⏳")

    data = await state.get_data()
    history: List[Dict[str, Any]] = data.get("history", [])

    answer, found_rows = await _ask_openai(history, user_text)

    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": answer})
    if len(history) > _MAX_HISTORY * 2:
        history = history[-(_MAX_HISTORY * 2):]
    await state.update_data(history=history)

    reply = answer[:4090] + "…" if len(answer) > 4090 else answer
    kb = _result_kb(found_rows) if found_rows else _chat_kb()

    try:
        sent = await thinking_msg.edit_text(reply, reply_markup=kb)
    except TelegramBadRequest:
        try:
            await thinking_msg.delete()
        except Exception:
            pass
        sent = await message.answer(reply, reply_markup=kb)

    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, sent.message_id, MSG_TTL)
    )


@router.message(Command("ai_chat"))
async def cmd_ai_chat(message: Message, state: FSMContext) -> None:
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, message.message_id, MSG_TTL)
    )
    await state.set_state(AIChatState.chatting)
    await state.update_data(history=[])
    sent = await message.answer(_WELCOME_MSG, reply_markup=_exit_kb())
    asyncio.create_task(
        schedule_delete(message.bot, message.from_user.id, sent.message_id, MSG_TTL)
    )
