"""Capture every media post in the request group / configured channels.

Runs on:
  * channel_post — when bot is admin of a channel
  * group/supergroup messages — when bot is in the group

Also notifies backup channel (BACKUP_CHANNEL_ID or DB channels with type='backup')
whenever a NEW movie is indexed.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from ..config import settings
from ..db import db
from ..utils import derive_title

router = Router(name="indexer")
log = logging.getLogger(__name__)

# Bot username cache
_bot_username: str = ""


async def _get_bot_link(bot) -> str:
    global _bot_username
    if not _bot_username:
        try:
            me = await bot.get_me()
            _bot_username = me.username or ""
        except Exception:
            pass
    return f"https://t.me/{_bot_username}" if _bot_username else ""


def _extract_media(message: Message):
    if message.video:
        v = message.video
        return ("video", v.file_id, v.file_unique_id, v.file_name, v.mime_type, v.file_size or 0, v.duration or 0,
                getattr(getattr(v, "thumbnail", None), "file_id", None))
    if message.document:
        d = message.document
        return ("document", d.file_id, d.file_unique_id, d.file_name, d.mime_type, d.file_size or 0, 0,
                getattr(getattr(d, "thumbnail", None), "file_id", None))
    if message.audio:
        a = message.audio
        return ("audio", a.file_id, a.file_unique_id, a.file_name or a.title, a.mime_type, a.file_size or 0, a.duration or 0,
                getattr(getattr(a, "thumbnail", None), "file_id", None))
    if message.animation:
        an = message.animation
        return ("animation", an.file_id, an.file_unique_id, an.file_name, an.mime_type, an.file_size or 0, an.duration or 0,
                getattr(getattr(an, "thumbnail", None), "file_id", None))
    if message.voice:
        vo = message.voice
        return ("voice", vo.file_id, vo.file_unique_id, None, vo.mime_type, vo.file_size or 0, vo.duration or 0, None)
    if message.video_note:
        vn = message.video_note
        return ("video_note", vn.file_id, vn.file_unique_id, None, None, vn.file_size or 0, vn.duration or 0,
                getattr(getattr(vn, "thumbnail", None), "file_id", None))
    if message.photo:
        p = message.photo[-1]
        return ("photo", p.file_id, p.file_unique_id, None, "image/jpeg", p.file_size or 0, 0)
        return ("photo", p.file_id, p.file_unique_id, None, "image/jpeg", p.file_size or 0, 0, p.file_id)
    return None


_TYPE_EMOJI = {
    "video":      "🎬",
    "document":   "📁",
    "audio":      "🎵",
    "animation":  "🎞",
    "photo":      "🖼",
    "voice":      "🎙",
    "video_note": "🎥",
}

_TYPE_LABEL = {
    "video":      "ভিডিও",
    "document":   "ডকুমেন্ট",
    "audio":      "অডিও",
    "animation":  "অ্যানিমেশন",
    "photo":      "ছবি",
    "voice":      "ভয়েস",
    "video_note": "ভিডিও নোট",
}


async def _notify_backup(bot, title: str, file_type: str, movie_id: int = 0) -> None:
    """Send a rich notification to all backup channels when a new movie is indexed."""
    backup_ids: set = set()

    if settings.backup_channel_id:
        backup_ids.add(settings.backup_channel_id)

    try:
        channels = await db.list_channels()
        for ch in channels:
            if ch["type"] == "backup":
                backup_ids.add(ch["chat_id"])
    except Exception:
        pass

    if not backup_ids:
        return

    emoji  = _TYPE_EMOJI.get(file_type, "📦")
    label  = _TYPE_LABEL.get(file_type, file_type)
    bot_link = await _get_bot_link(bot)

    # Deep-link সরাসরি এই মুভিতে (movie_id থাকলে)
    if movie_id and bot_link:
        search_line = (
            f"\n🔗 <a href='{bot_link}?start=movie_{movie_id}'>সরাসরি ডাউনলোড করুন</a>"
            f"  |  <a href='{bot_link}'>বটে যান</a>"
        )
    elif bot_link:
        search_line = f"\n🔗 <a href='{bot_link}'>বটে গিয়ে সার্চ করুন</a>"
    else:
        search_line = ""

    # মুভি কাউন্ট
    try:
        total = await db.fetch_value("SELECT COUNT(*) FROM movies", ())
        count_line = f"📚 লাইব্রেরিতে এখন <b>{total}</b>টি কন্টেন্ট"
    except Exception:
        count_line = ""

    text = (
        f"{emoji} <b>নতুন মুভি যোগ হয়েছে!</b>\n"
        f"🎯 <b>{title}</b> · {label}\n"
        + (f"\n{count_line}" if count_line else "")
        + f"\n{search_line}\n"
        f"🔍 বটে নাম লিখে সার্চ করুন"
        + (f"\n👉 {bot_link}" if bot_link else "")
    )

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb_rows = []
    if bot_link:
        row = []
        if movie_id:
            row.append(InlineKeyboardButton(
                text=f"{emoji} সরাসরি ডাউনলোড",
                url=f"{bot_link}?start=movie_{movie_id}"
            ))
        row.append(InlineKeyboardButton(text="🔍 বটে সার্চ করুন", url=bot_link))
        kb_rows.append(row)
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None

    for cid in backup_ids:
        try:
            await bot.send_message(cid, text, reply_markup=kb)
        except Exception as e:
            log.warning("backup notify failed for %s: %s", cid, e)


async def _index(message: Message) -> None:
    media = _extract_media(message)
    if not media:
        return
    file_type, file_id, file_unique_id, file_name, mime, size, duration, poster_file_id = media
    caption = message.caption or message.text or ""
    title = derive_title(caption, file_name)

    inserted = await db.upsert_movie({
        "file_type":        file_type,
        "file_id":          file_id,
        "file_unique_id":   file_unique_id,
        "file_name":        file_name,
        "mime_type":        mime,
        "size_bytes":       size,
        "duration":         duration,
        "title":            title,
        "caption":          caption,
        "poster_file_id":   poster_file_id,
        "source_chat_id":   message.chat.id,
        "source_message_id": message.message_id,
    })
    if inserted:
        await db.log_event("indexed", {"title": title, "file_type": file_type, "chat": message.chat.id})
        # movie_id পেতে হলে DB থেকে আনতে হবে
        try:
            movie_id = await db.fetch_value(
                "SELECT id FROM movies WHERE file_unique_id=?", (file_unique_id,)
            ) or 0
        except Exception:
            movie_id = 0
        try:
            await _notify_backup(message.bot, title, file_type, int(movie_id))
        except Exception:
            log.exception("_notify_backup failed")


@router.channel_post(F.content_type.in_({"video", "document", "audio", "animation", "voice", "video_note", "photo"}))
async def on_channel_post(message: Message) -> None:
    await _index(message)


@router.edited_channel_post(F.content_type.in_({"video", "document", "audio", "animation", "voice", "video_note", "photo"}))
async def on_channel_edit(message: Message) -> None:
    await _index(message)


@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
                F.content_type.in_({"video", "document", "audio", "animation", "voice", "video_note", "photo"}))
async def on_group_post(message: Message) -> None:
    await _index(message)


@router.edited_message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
                       F.content_type.in_({"video", "document", "audio", "animation", "voice", "video_note", "photo"}))
async def on_group_edit(message: Message) -> None:
    await _index(message)
