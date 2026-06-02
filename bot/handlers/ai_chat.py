"""AI চ্যাটবট — বটের ডেটাবেজ সার্চ + মুভি বিশেষজ্ঞ + বট গাইড।"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

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

log = logging.getLogger(__name__)
router = Router(name="ai_chat")

_OPENAI_BASE_URL = os.getenv("AI_INTEGRATIONS_OPENAI_BASE_URL", "").strip()
_OPENAI_API_KEY  = os.getenv("AI_INTEGRATIONS_OPENAI_API_KEY", "sk-dummy").strip()

_TODAY = date.today().isoformat()

_SYSTEM_PROMPT = f"""তুমি "Moviex Hub" বটের AI সহকারী — নাম "সিনে-বাবু"। আজ: {_TODAY}

বাংলায় সংক্ষিপ্ত ও সরাসরি উত্তর দাও। emoji মাঝেমধ্যে।

🎬 Moviex Hub: মুভি/সিরিজ/নাটক ডাউনলোড বট।
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
        lines = [f"✅ '{q}' — {total}টি ফাইল পাওয়া গেছে। নিচের বাটনে ক্লিক করো:"]
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


async def _ask_openai(
    history: List[Dict[str, Any]],
    user_message: str,
) -> Tuple[str, List[Any]]:
    found_rows: List[Any] = []

    if not _OPENAI_BASE_URL:
        return "দুঃখিত, AI সার্ভিস এই মুহূর্তে পাওয়া যাচ্ছে না।", found_rows
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=_OPENAI_API_KEY, base_url=_OPENAI_BASE_URL)

        messages: List[Dict[str, Any]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
        messages.extend(history[-(_MAX_HISTORY * 2):])
        messages.append({"role": "user", "content": user_message})

        last_content = ""

        for _ in range(4):
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=_TOOLS,
                tool_choice="auto",
                max_completion_tokens=600,
                temperature=0.65,
            )
            choice = resp.choices[0]
            last_content = choice.message.content or ""

            if choice.finish_reason != "tool_calls":
                return last_content or "উত্তর পাওয়া যায়নি।", found_rows

            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": last_content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in (choice.message.tool_calls or [])
                ],
            }
            messages.append(assistant_msg)

            tool_tasks = []
            tool_call_ids = []
            for tc in (choice.message.tool_calls or []):
                fn = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                tool_call_ids.append(tc.id)
                if fn == "search_movies_in_db":
                    tool_tasks.append(_exec_search(args.get("query", ""), found_rows))
                elif fn == "get_bot_stats":
                    tool_tasks.append(_exec_bot_stats())
                else:
                    async def _unknown():
                        return "অজানা ফাংশন।"
                    tool_tasks.append(_unknown())

            results = await asyncio.gather(*tool_tasks)
            for tc_id, result in zip(tool_call_ids, results):
                messages.append({"role": "tool", "tool_call_id": tc_id, "content": result})

        return last_content or "উত্তর পাওয়া যায়নি।", found_rows

    except Exception as exc:
        log.exception("OpenAI call failed: %s", exc)
        return "😔 একটু সমস্যা হচ্ছে। কিছুক্ষণ পর আবার চেষ্টা করো।", found_rows


class AIChatState(StatesGroup):
    chatting = State()


def _exit_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ চ্যাট শেষ করুন", callback_data="ai:chat:exit")
    kb.button(text="🏠 হোম", callback_data="home")
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
        kb.button(text=f"{emoji} {title}", callback_data=f"m:get:{r['id']}")
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(text="🎬 রিকোয়েস্ট করুন", callback_data="req:from_search"),
        InlineKeyboardButton(text="❌ চ্যাট বন্ধ", callback_data="ai:chat:exit"),
    )
    return kb.as_markup()


def _chat_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔍 সার্চ করুন", callback_data="search:start")
    kb.button(text="🎬 রিকোয়েস্ট", callback_data="req:from_search")
    kb.button(text="❌ চ্যাট বন্ধ", callback_data="ai:chat:exit")
    kb.adjust(2, 1)
    return kb.as_markup()


_WELCOME_MSG = (
    "🎬 <b>সিনে-বাবু এখানে!</b>\n\n"
    "মুভির নাম বলো — বটে আছে কিনা খুঁজে বাটন দেব। "
    "মুভির তথ্য, রিলিজ ডেট, গল্প — সব বলতে পারব।\n\n"
    "💬 বাংলায় বা ইংরেজিতে লেখো:"
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
