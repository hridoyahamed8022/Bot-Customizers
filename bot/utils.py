"""Generic helpers."""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MSG_TTL = 30            # সাধারণ মেসেজ → ৩০ সেকেন্ড
MOVIE_TTL = 30          # মুভি ফাইল → ৩০ সেকেন্ড

# মুভি কাউন্টডাউন: ৩০ সেকেন্ডে ৬টি মেসেজ, প্রতি ৫ সেকেন্টে একটি
_WARN_INTERVAL = 5      # প্রতি কত সেকেন্ডে সতর্কবার্তা
_WARN_COUNT    = 6      # মোট কতটি সতর্কবার্তা


async def schedule_delete(bot, chat_id: int, message_id: int, delay: int) -> None:
    """নির্দিষ্ট সময় পর মেসেজ ডিলিট করে।"""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass  # মেসেজ আগেই ডিলিট হলে বা permission না থাকলে চুপ থাকো


async def movie_delete_countdown(bot, chat_id: int, movie_message_id: int, seconds: int = MOVIE_TTL) -> None:
    """মুভি পাঠানোর পর প্রতি ৫ সেকেন্ডে একটি সতর্কবার্তা পাঠায়, মোট ৬টি, তারপর মুভি ডিলিট করে।"""
    _EMOJIS = ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣"]
    remaining_secs = seconds
    for i in range(_WARN_COUNT):
        emoji = _EMOJIS[i % len(_EMOJIS)]
        try:
            warn = await bot.send_message(
                chat_id,
                f"{emoji} <b>{remaining_secs} সেকেন্ড পর মুছে যাবে</b>\n"
                "📤 এখনই ফরওয়ার্ড করুন।",
                parse_mode="HTML",
            )
            asyncio.create_task(schedule_delete(bot, chat_id, warn.message_id, MSG_TTL))
        except Exception:
            pass
        await asyncio.sleep(_WARN_INTERVAL)
        remaining_secs -= _WARN_INTERVAL

    try:
        await bot.delete_message(chat_id=chat_id, message_id=movie_message_id)
    except Exception:
        pass


_FILE_EXT_RE = re.compile(r"\.[A-Za-z0-9]{2,5}$")
_QUALITY_RE = re.compile(
    r"\b(\d{3,4}p|HDR|HEVC|x264|x265|WEB[-_ ]?DL|BluRay|HDRip|BRRip|DVDRip|HQ|HD)\b",
    re.IGNORECASE,
)


def esc(text: str | None) -> str:
    """HTML-escape a string safely (None-safe)."""
    if text is None:
        return ""
    return html.escape(str(text))


def fmt_ts(ts: float | int | None) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    except Exception:
        return "—"


def derive_title(caption: str | None, file_name: str | None) -> str:
    """Pick a sensible title from a caption or filename for indexing/search."""
    raw = (caption or file_name or "").strip()
    if not raw:
        return "Untitled"
    # Take first non-empty line — captions often have title on line 1.
    line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), raw)
    # Drop file extension if present.
    line = _FILE_EXT_RE.sub("", line)
    # Trim hashtags / urls
    line = re.sub(r"https?://\S+", "", line)
    line = re.sub(r"[#@]\S+", "", line)
    line = re.sub(r"\s+", " ", line).strip(" -_.|·•")
    return (line or "Untitled")[:200]


def short_caption(caption: str | None, limit: int = 80) -> str:
    if not caption:
        return ""
    text = caption.strip().splitlines()[0] if caption.strip() else ""
    return (text[: limit - 1] + "…") if len(text) > limit else text


def chunked(items, n):
    for i in range(0, len(items), n):
        yield items[i : i + n]
