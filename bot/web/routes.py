"""All admin panel HTTP routes."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp_jinja2
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiohttp import web

from ..config import settings
from ..db import db
from ..utils import esc
from .auth import (
    check_credentials,
    clear_session_cookie,
    require_admin,
    set_session_cookie,
)

log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


# ──────────────────────────────────────────────────────────────── #
# Editable bot messages configuration
# ──────────────────────────────────────────────────────────────── #
BOT_MESSAGES_CONFIG = [
    {
        "key": "home",
        "label": "🏠 হোম / স্টার্ট মেসেজ (/start এ দেখায়)",
        "default": (
            "👋 <b>স্বাগতম!</b>\n\n"
            "আমি একটি <b>মুভি / ওয়েব সিরিজ / নাটক</b> সার্চ বট। যেকোনো মুভির নাম "
            "<b>ইংরেজিতে</b> লিখে পাঠালেই আমি আপনাকে ফাইল পাঠিয়ে দেব।\n\n"
            "📌 <b>মনে রাখবেন:</b>\n"
            "• সার্চ অবশ্যই <b>ইংরেজি</b> অক্ষরে করতে হবে।\n"
            "• বানান অবশ্যই <b>সঠিক</b> হতে হবে — ভুল বানানে কিছু আসবে না।\n"
            "• প্রথমবার ব্যবহারের আগে আমাদের চ্যানেলগুলোতে জয়েন করতে হবে।\n\n"
            "👇 নিচের বাটন থেকে শুরু করুন।"
        ),
    },
    {
        "key": "verify",
        "label": "🔐 ভেরিফিকেশন মেসেজ (প্রথম সার্চের আগে দেখায়)",
        "default": (
            "🔐 <b>প্রথমবার ব্যবহারের আগে ভেরিফিকেশন প্রয়োজন</b>\n\n"
            "বট ব্যবহার করতে হলে আপনাকে আমাদের <b>২টি অফিসিয়াল চ্যানেলে</b> জয়েন "
            "করতে হবে — একটি <b>হিন্দি ডাবিং</b> চ্যানেল, আরেকটি <b>বাংলা ডাবিং</b> চ্যানেল।\n\n"
            "👇 নিচের বাটনগুলো থেকে দুইটি চ্যানেলেই জয়েন করুন। তারপর "
            '<b>"✅ ভেরিফাই করুন"</b> বাটনে ক্লিক করুন।\n\n'
            "⚠️ এটি <b>একবারই</b> করতে হবে — পরে আর কখনো লাগবে না।"
        ),
    },
    {
        "key": "maintenance",
        "label": "🔧 মেইনটেন্যান্স মেসেজ (মোড চালু থাকলে দেখায়)",
        "default": (
            "🔧 <b>বট এই মুহূর্তে রক্ষণাবেক্ষণে আছে</b>\n\n"
            "কিছুক্ষণ পর আবার চেষ্টা করুন। অসুবিধার জন্য দুঃখিত। 🙏"
        ),
    },
    {
        "key": "no_results",
        "label": "😕 সার্চে কিছু না পেলে (no results) যে মেসেজ দেখায়",
        "default": (
            "😕 <b>কিছু পাওয়া যায়নি</b>\n\n"
            "🔎 <b>কারণ হতে পারে:</b>\n"
            "• বানান ভুল — সঠিক <b>ইংরেজি</b> বানানে আবার চেষ্টা করুন।\n"
            "• মুভিটি এখনো লাইব্রেরিতে যোগ হয়নি।\n\n"
            "<b>উদাহরণ:</b> <code>3 idiots</code>, <code>kgf chapter 2</code>"
        ),
    },
    {
        "key": "rules",
        "label": "📜 নিয়মাবলী (AI সাহায্যে দেখায়)",
        "default": (
            "• বটে স্প্যাম করবেন না — অটোমেটিক ব্যান হয়ে যাবেন।\n"
            "• প্রতি কয়েক সেকেন্ডে একবারের বেশি মেসেজ পাঠাবেন না।\n"
            "• আমাদের চ্যানেল থেকে লিভ করলে ভেরিফিকেশন হারাতে পারেন।\n"
            "• ফাইল অন্য কোথাও আপলোড করলে ক্রেডিট দেবেন।"
        ),
    },
]


# ──────────────────────────────────────────────────────────────── #
# Helpers
# ──────────────────────────────────────────────────────────────── #
def _parse_buttons(raw: str) -> Optional[InlineKeyboardMarkup]:
    """Each line: `Text - https://url`  (or `Text | https://url`).
    Multiple buttons in one line: `A - https://a.com && B - https://b.com`.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    rows: List[List[InlineKeyboardButton]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        cells = []
        for cell in line.split("&&"):
            cell = cell.strip()
            if not cell:
                continue
            if " - " in cell:
                text, url = cell.split(" - ", 1)
            elif " | " in cell:
                text, url = cell.split(" | ", 1)
            else:
                continue
            text = text.strip()
            url = url.strip()
            if not text or not url:
                continue
            if not url.startswith(("http://", "https://", "tg://")):
                url = "https://" + url
            cells.append(InlineKeyboardButton(text=text[:64], url=url))
        if cells:
            rows.append(cells)
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_to_chat(
    bot,
    chat_id: int,
    *,
    text: str,
    media_bytes: Optional[bytes],
    media_filename: Optional[str],
    media_kind: str,
    reply_markup: Optional[InlineKeyboardMarkup],
):
    text = text or ""
    caption = text if len(text) <= 1024 else text[:1020] + "…"

    if media_kind == "none" or not media_bytes:
        return await bot.send_message(
            chat_id=chat_id,
            text=text or "—",
            reply_markup=reply_markup,
            disable_web_page_preview=False,
        )

    file = BufferedInputFile(media_bytes, filename=media_filename or "file")
    if media_kind == "photo":
        return await bot.send_photo(chat_id=chat_id, photo=file, caption=caption, reply_markup=reply_markup)
    if media_kind == "video":
        return await bot.send_video(chat_id=chat_id, video=file, caption=caption, reply_markup=reply_markup)
    return await bot.send_document(chat_id=chat_id, document=file, caption=caption, reply_markup=reply_markup)


def _flash(request: web.Request, msg: str, kind: str = "info") -> str:
    from urllib.parse import quote
    return f"?flash={quote(msg)}&kind={kind}"


def _read_flash(request: web.Request) -> Optional[Dict[str, str]]:
    msg = request.query.get("flash")
    if not msg:
        return None
    return {"msg": msg, "kind": request.query.get("kind", "info")}


# ──────────────────────────────────────────────────────────────── #
# Public routes
# ──────────────────────────────────────────────────────────────── #
async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "ts": time.time()})


async def login_get(request: web.Request) -> web.StreamResponse:
    if request.get("is_admin"):
        raise web.HTTPFound("/")
    return aiohttp_jinja2.render_template("login.html", request, {"error": None})


async def login_post(request: web.Request) -> web.StreamResponse:
    data = await request.post()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not (settings.admin_username and settings.admin_password):
        return aiohttp_jinja2.render_template(
            "login.html", request,
            {"error": "অ্যাডমিন ক্রেডেনশিয়াল কনফিগার করা নেই। Replit Secrets-এ ADMIN_USERNAME ও ADMIN_PASSWORD সেট করুন।"},
            status=503,
        )

    if not check_credentials(username, password):
        await db.log_event("login_failed", {"username": username[:32], "ip": request.remote})
        return aiohttp_jinja2.render_template(
            "login.html", request, {"error": "ভুল ইউজারনেম বা পাসওয়ার্ড।"}, status=401
        )

    session_token = await db.issue_session(user_id=1, ttl_seconds=10 * 365 * 24 * 3600)
    response = web.HTTPFound("/")
    set_session_cookie(response, session_token)
    await db.log_event("login_ok", {"username": username})
    raise response


async def logout(request: web.Request) -> web.Response:
    token = request.cookies.get("admin_session")
    if token:
        await db.revoke_session(token)
    response = web.HTTPFound("/login")
    clear_session_cookie(response)
    raise response


# ──────────────────────────────────────────────────────────────── #
# Dashboard
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def dashboard(request: web.Request) -> web.Response:
    movies = await db.count_movies()
    user_ids = await db.all_user_ids()
    hits = await db.total_hits()
    bans = len(await db.list_bans())
    fj = await db.list_force_join()
    pending_req = await db.count_movie_requests("pending")
    recent_movies = await db.list_movies(limit=10, offset=0)
    recent_logs = await db.recent_logs(limit=15)
    maintenance_mode = await db.is_maintenance()
    return aiohttp_jinja2.render_template(
        "dashboard.html",
        request,
        {
            "stats": {
                "movies": movies,
                "users": len(user_ids),
                "hits": hits,
                "bans": bans,
                "fj_count": len(fj),
                "pending_requests": pending_req,
            },
            "recent_movies": recent_movies,
            "recent_logs": recent_logs,
            "request_group_id": settings.request_group_id,
            "maintenance_mode": maintenance_mode,
            "flash": _read_flash(request),
        },
    )


# ──────────────────────────────────────────────────────────────── #
# Movies
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def movies_list(request: web.Request) -> web.Response:
    q = (request.query.get("q") or "").strip()
    try:
        page = max(0, int(request.query.get("page", "0")))
    except ValueError:
        page = 0
    per_page = 25
    rows = (
        await db.search_movies(q, limit=per_page, offset=page * per_page)
        if q
        else await db.list_movies(limit=per_page, offset=page * per_page)
    )
    total = await db.count_search(q) if q else await db.count_movies()
    pages = max(1, (total + per_page - 1) // per_page)
    return aiohttp_jinja2.render_template(
        "movies.html",
        request,
        {"movies": rows, "q": q, "page": page, "pages": pages, "total": total, "flash": _read_flash(request)},
    )


@require_admin
async def movie_delete(request: web.Request) -> web.Response:
    data = await request.post()
    try:
        mid = int(data.get("id"))
    except (TypeError, ValueError):
        raise web.HTTPFound("/movies")
    await db.delete_movie(mid)
    await db.log_event("movie_delete", {"id": mid, "by": request["user_id"]})
    raise web.HTTPFound("/movies" + _flash(request, "মুভি মুছে ফেলা হয়েছে।", "ok"))


# ──────────────────────────────────────────────────────────────── #
# Post to group/channel
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def post_form(request: web.Request) -> web.Response:
    channels = await db.list_channels()
    return aiohttp_jinja2.render_template(
        "post.html",
        request,
        {
            "request_group_id": settings.request_group_id,
            "channels": channels,
            "flash": _read_flash(request),
        },
    )


@require_admin
async def post_submit(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    reader = await request.multipart()

    text = ""
    chat_id_raw = ""
    buttons_raw = ""
    media_kind = "none"
    media_bytes: Optional[bytes] = None
    media_filename: Optional[str] = None

    async for part in reader:
        if part.name == "text":
            text = (await part.text()) or ""
        elif part.name == "chat_id":
            chat_id_raw = (await part.text()) or ""
        elif part.name == "buttons":
            buttons_raw = (await part.text()) or ""
        elif part.name == "media_kind":
            media_kind = (await part.text()) or "none"
        elif part.name == "media" and part.filename:
            data = await part.read(decode=False)
            if data:
                media_bytes = data
                media_filename = part.filename

    chat_id_raw = chat_id_raw.strip()
    target_chat: Optional[int] = None
    if chat_id_raw:
        try:
            target_chat = int(chat_id_raw)
        except ValueError:
            raise web.HTTPFound("/post" + _flash(request, "Chat id অবশ্যই সংখ্যা হতে হবে।", "err"))
    elif settings.request_group_id:
        target_chat = settings.request_group_id
    else:
        raise web.HTTPFound("/post" + _flash(request, "কোনো চ্যানেল/গ্রুপ নির্বাচন করুন।", "err"))

    kb = _parse_buttons(buttons_raw)

    try:
        await _send_to_chat(
            bot, target_chat,
            text=text, media_bytes=media_bytes, media_filename=media_filename,
            media_kind=media_kind, reply_markup=kb,
        )
        await db.log_event("panel_post", {"chat_id": target_chat, "by": request["user_id"], "kind": media_kind})
        raise web.HTTPFound("/post" + _flash(request, "✅ পোস্ট পাঠানো হয়েছে!", "ok"))
    except web.HTTPFound:
        raise
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        raise web.HTTPFound("/post" + _flash(request, f"Telegram error: {exc}", "err"))
    except Exception as exc:
        log.exception("post failed")
        raise web.HTTPFound("/post" + _flash(request, f"ত্রুটি: {exc}", "err"))


# ──────────────────────────────────────────────────────────────── #
# Broadcast
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def broadcast_form(request: web.Request) -> web.Response:
    user_count = len(await db.all_user_ids())
    return aiohttp_jinja2.render_template(
        "broadcast.html",
        request,
        {"user_count": user_count, "flash": _read_flash(request)},
    )


_active_broadcasts: Dict[int, Dict[str, Any]] = {}


# ──────────────────────────────────────────────────────────────── #
# Silent background notification helpers
# ──────────────────────────────────────────────────────────────── #
async def _silent_send(bot, user_id: int, text: str, kb=None) -> None:
    try:
        await bot.send_message(user_id, text, reply_markup=kb)
    except TelegramForbiddenError:
        await db.mark_blocked(user_id, True)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after + 1)
        try:
            await bot.send_message(user_id, text, reply_markup=kb)
        except Exception:
            pass
    except Exception:
        pass


async def _broadcast_notification(bot, text: str, kb=None) -> None:
    """Send a notification to ALL active users in the background (fire-and-forget)."""
    user_ids = await db.all_user_ids()
    total = len(user_ids)
    for i, uid in enumerate(user_ids, start=1):
        await _silent_send(bot, uid, text, kb)
        if i % 30 == 0:
            await asyncio.sleep(1)
    await db.log_event("req_notification_sent", {"total": total})


async def _broadcast_worker(
    bot,
    user_id: int,
    targets: List[int],
    text: str,
    media_bytes: Optional[bytes],
    media_filename: Optional[str],
    media_kind: str,
    kb: Optional[InlineKeyboardMarkup],
) -> None:
    state = _active_broadcasts[user_id]
    sent = blocked = failed = 0
    total = len(targets)
    for i, uid in enumerate(targets, start=1):
        try:
            await _send_to_chat(
                bot, uid,
                text=text, media_bytes=media_bytes, media_filename=media_filename,
                media_kind=media_kind, reply_markup=kb,
            )
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
            try:
                await _send_to_chat(
                    bot, uid,
                    text=text, media_bytes=media_bytes, media_filename=media_filename,
                    media_kind=media_kind, reply_markup=kb,
                )
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            blocked += 1
            try:
                await db.mark_blocked(uid, True)
            except Exception:
                pass
        except Exception:
            failed += 1
        state.update({"sent": sent, "blocked": blocked, "failed": failed, "progress": i})
        if i % 25 == 0:
            await asyncio.sleep(1)
    state["finished"] = True
    state["finished_at"] = time.time()
    await db.log_event("broadcast", {"sent": sent, "blocked": blocked, "failed": failed, "by": user_id})


@require_admin
async def broadcast_submit(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    user_id = request["user_id"]
    if user_id in _active_broadcasts and not _active_broadcasts[user_id].get("finished"):
        raise web.HTTPFound("/broadcast" + _flash(request, "ব্রডকাস্ট চলছে — শেষ হওয়া পর্যন্ত অপেক্ষা করুন।", "err"))

    reader = await request.multipart()
    text = ""
    buttons_raw = ""
    media_kind = "none"
    media_bytes: Optional[bytes] = None
    media_filename: Optional[str] = None
    async for part in reader:
        if part.name == "text":
            text = (await part.text()) or ""
        elif part.name == "buttons":
            buttons_raw = (await part.text()) or ""
        elif part.name == "media_kind":
            media_kind = (await part.text()) or "none"
        elif part.name == "media" and part.filename:
            data = await part.read(decode=False)
            if data:
                media_bytes = data
                media_filename = part.filename

    if not (text.strip() or media_bytes):
        raise web.HTTPFound("/broadcast" + _flash(request, "মেসেজ বা মিডিয়া দিতে হবে।", "err"))

    targets = await db.all_user_ids()
    if not targets:
        raise web.HTTPFound("/broadcast" + _flash(request, "কোনো ইউজার নেই।", "err"))

    kb = _parse_buttons(buttons_raw)
    _active_broadcasts[user_id] = {
        "started_at": time.time(), "total": len(targets),
        "sent": 0, "blocked": 0, "failed": 0, "progress": 0, "finished": False,
    }
    asyncio.create_task(
        _broadcast_worker(bot, user_id, targets, text, media_bytes, media_filename, media_kind, kb)
    )
    raise web.HTTPFound("/broadcast")


@require_admin
async def broadcast_status(request: web.Request) -> web.Response:
    state = _active_broadcasts.get(request["user_id"])
    if not state:
        return web.json_response({"running": False})
    return web.json_response({"running": not state["finished"], **state})


# ──────────────────────────────────────────────────────────────── #
# Movie Requests
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def requests_page(request: web.Request) -> web.Response:
    status_filter = request.query.get("status", "all")
    reqs = await db.list_movie_requests(status=status_filter, limit=100)
    pending_count = await db.count_movie_requests("pending")
    return aiohttp_jinja2.render_template(
        "requests.html",
        request,
        {
            "requests": reqs,
            "status_filter": status_filter,
            "pending_count": pending_count,
            "flash": _read_flash(request),
        },
    )


@require_admin
async def requests_resolve(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    data = await request.post()
    try:
        req_id = int(data.get("id"))
    except (TypeError, ValueError):
        raise web.HTTPFound("/requests")

    status = data.get("status", "done")
    if status not in ("done", "rejected"):
        status = "done"

    # Fetch request BEFORE modifying (we need title + user_id)
    req_row = await db.fetch_one("SELECT * FROM movie_requests WHERE id=?", (req_id,))
    await db.resolve_movie_request(req_id, status)

    if req_row:
        title = req_row["title"] or "?"
        user_id = req_row["user_id"]
        correct_name = (data.get("correct_name") or "").strip()

        if status == "done":
            # ✅ শুধু রিকোয়েস্টকারীকে জানাও
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            search_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🔍 এখনই সার্চ করুন",
                    callback_data="search:start",
                )
            ]])
            text = (
                f"✅ <b>আপলোড সম্পন্ন!</b>\n\n"
                f"🎬 <b>{esc(title)}</b> বটে যোগ করা হয়েছে।\n\n"
                f"এখনই বটে গিয়ে নামটি লিখে সার্চ করুন। 🍿"
            )
            asyncio.create_task(_silent_send(bot, user_id, text, search_kb))

        elif status == "rejected":
            # ❌ বানান ভুল — সঠিক নাম সহ জানাও
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            if correct_name:
                search_kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔍 সার্চ করুন",
                        callback_data="search:start",
                    )
                ]])
                text = (
                    f"⚠️ <b>বানান ভুল ছিল!</b>\n\n"
                    f"আপনি লিখেছিলেন: <s>{esc(title)}</s>\n"
                    f"✅ সঠিক নাম: <b>{esc(correct_name)}</b>\n\n"
                    f"এই সঠিক নামটি বটে পাঠান — মুভিটি পেয়ে যাবেন। 🎬"
                )
            else:
                search_kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔍 আবার সার্চ করুন",
                        callback_data="search:start",
                    )
                ]])
                text = (
                    f"⚠️ <b>বানান ভুল!</b>\n\n"
                    f"<b>{esc(title)}</b> — এই নামে মুভিটি আমাদের বটে আছে।\n\n"
                    f"সঠিক বানানে আবার সার্চ করুন। 🔍"
                )
            asyncio.create_task(_silent_send(bot, user_id, text, search_kb))

    label = "✅ সম্পন্ন" if status == "done" else "❌ বাতিল"
    raise web.HTTPFound("/requests" + _flash(request, f"রিকোয়েস্ট #{req_id} → {label} (নোটিফিকেশন পাঠানো হচ্ছে…)", "ok"))


@require_admin
async def requests_delete(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    data = await request.post()
    try:
        req_id = int(data.get("id"))
    except (TypeError, ValueError):
        raise web.HTTPFound("/requests")

    # Fetch BEFORE deleting
    req_row = await db.fetch_one("SELECT * FROM movie_requests WHERE id=?", (req_id,))
    await db.delete_movie_request(req_id)

    if req_row:
        title = req_row["title"] or "?"
        user_id = req_row["user_id"]
        # শুধু রিকোয়েস্টকারীকে জানাও
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        home_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏠 হোম", callback_data="home")
        ]])
        text = (
            f"ℹ️ <b>রিকোয়েস্ট আপডেট</b>\n\n"
            f"🎬 <b>{esc(title)}</b>\n\n"
            f"এই মুভিটি এখন আমাদের কাছে নেই।\n"
            f"সংগ্রহ হলে আপলোড করে দেওয়া হবে। 🙏"
        )
        asyncio.create_task(_silent_send(bot, user_id, text, home_kb))

    raise web.HTTPFound("/requests" + _flash(request, f"রিকোয়েস্ট #{req_id} ডিলিট হয়েছে (নোটিফিকেশন পাঠানো হচ্ছে…)।", "ok"))


# ──────────────────────────────────────────────────────────────── #
# Bot Messages Editor
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def bot_messages_page(request: web.Request) -> web.Response:
    msgs = []
    for cfg in BOT_MESSAGES_CONFIG:
        val = await db.get_bot_message(cfg["key"], cfg["default"])
        msgs.append({"key": cfg["key"], "label": cfg["label"], "value": val})
    return aiohttp_jinja2.render_template(
        "bot_messages.html",
        request,
        {"messages": msgs, "flash": _read_flash(request)},
    )


@require_admin
async def bot_messages_save(request: web.Request) -> web.Response:
    data = await request.post()
    key = (data.get("key") or "").strip()
    text = data.get("text") or ""
    valid_keys = {c["key"] for c in BOT_MESSAGES_CONFIG}
    if key not in valid_keys:
        raise web.HTTPFound("/bot-messages" + _flash(request, "অবৈধ মেসেজ কী।", "err"))
    await db.set_bot_message(key, text)
    await db.log_event("bot_msg_edit", {"key": key, "by": request["user_id"]})
    raise web.HTTPFound("/bot-messages" + _flash(request, f"✅ '{key}' মেসেজ সংরক্ষিত হয়েছে।", "ok"))


@require_admin
async def bot_messages_reset(request: web.Request) -> web.Response:
    data = await request.post()
    key = (data.get("key") or "").strip()
    valid = {c["key"]: c["default"] for c in BOT_MESSAGES_CONFIG}
    if key not in valid:
        raise web.HTTPFound("/bot-messages" + _flash(request, "অবৈধ মেসেজ কী।", "err"))
    await db.set_bot_message(key, valid[key])
    raise web.HTTPFound("/bot-messages" + _flash(request, f"✅ '{key}' ডিফল্টে ফেরানো হয়েছে।", "ok"))


# ──────────────────────────────────────────────────────────────── #
# Channels / Groups Manager
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def channels_page(request: web.Request) -> web.Response:
    channels = await db.list_channels()
    return aiohttp_jinja2.render_template(
        "channels.html",
        request,
        {"channels": channels, "flash": _read_flash(request)},
    )


@require_admin
async def channels_add(request: web.Request) -> web.Response:
    data = await request.post()
    chat_raw = (data.get("chat_id") or "").strip()
    title = (data.get("title") or "").strip() or None
    username = (data.get("username") or "").strip().lstrip("@") or None
    invite = (data.get("invite_url") or "").strip() or None
    ch_type = (data.get("type") or "channel").strip()
    if ch_type not in ("channel", "group", "backup"):
        ch_type = "channel"

    try:
        chat_id = int(chat_raw)
    except ValueError:
        raise web.HTTPFound("/channels" + _flash(request, "Chat ID অবশ্যই সংখ্যা হতে হবে।", "err"))

    bot = request.app["bot"]
    try:
        info = await bot.get_chat(chat_id)
        if not title:
            title = info.title or info.full_name
        if not username:
            username = info.username
        if not invite:
            try:
                invite = await bot.export_chat_invite_link(chat_id)
            except Exception:
                pass
    except Exception:
        pass

    await db.add_channel(chat_id, title=title, username=username, invite_url=invite, type=ch_type)
    await db.log_event("channel_add", {"chat_id": chat_id, "type": ch_type, "by": request["user_id"]})
    raise web.HTTPFound("/channels" + _flash(request, f"✅ চ্যানেল/গ্রুপ যোগ হয়েছে: {title or chat_id}", "ok"))


@require_admin
async def channels_del(request: web.Request) -> web.Response:
    data = await request.post()
    try:
        chat_id = int(data.get("chat_id"))
    except (TypeError, ValueError):
        raise web.HTTPFound("/channels")
    await db.remove_channel(chat_id)
    raise web.HTTPFound("/channels" + _flash(request, "চ্যানেল/গ্রুপ সরানো হয়েছে।", "ok"))


# ──────────────────────────────────────────────────────────────── #
# Users
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def users_page(request: web.Request) -> web.Response:
    q = (request.query.get("q") or "").strip()
    filt = (request.query.get("filter") or "").strip()  # verified | blocked | ""
    try:
        page = max(0, int(request.query.get("page", "0")))
    except ValueError:
        page = 0
    per_page = 30
    offset = page * per_page

    if filt == "verified":
        now = time.time()
        rows = await db.fetch_all(
            f"""SELECT u.*,
                   (SELECT b.expires_at FROM bans b WHERE b.user_id=u.user_id
                    AND (b.expires_at IS NULL OR b.expires_at > ?) LIMIT 1) AS ban_expires,
                   CASE WHEN EXISTS(SELECT 1 FROM bans b WHERE b.user_id=u.user_id
                                    AND (b.expires_at IS NULL OR b.expires_at > ?))
                        THEN 1 ELSE 0 END AS is_banned
               FROM users u WHERE u.verified=1
               ORDER BY u.last_seen DESC LIMIT ? OFFSET ?""",
            (now, now, per_page, offset),
        )
        total = int(await db.fetch_value("SELECT COUNT(*) FROM users WHERE verified=1") or 0)
    elif filt == "blocked":
        now = time.time()
        rows = await db.fetch_all(
            f"""SELECT u.*,
                   (SELECT b.expires_at FROM bans b WHERE b.user_id=u.user_id
                    AND (b.expires_at IS NULL OR b.expires_at > ?) LIMIT 1) AS ban_expires,
                   CASE WHEN EXISTS(SELECT 1 FROM bans b WHERE b.user_id=u.user_id
                                    AND (b.expires_at IS NULL OR b.expires_at > ?))
                        THEN 1 ELSE 0 END AS is_banned
               FROM users u WHERE u.is_blocked=1
               ORDER BY u.last_seen DESC LIMIT ? OFFSET ?""",
            (now, now, per_page, offset),
        )
        total = int(await db.fetch_value("SELECT COUNT(*) FROM users WHERE is_blocked=1") or 0)
    else:
        rows, total = await asyncio.gather(
            db.list_users(limit=per_page, offset=offset, search=q),
            db.count_users(search=q),
        )

    pages = max(1, (total + per_page - 1) // per_page)
    return aiohttp_jinja2.render_template(
        "users.html",
        request,
        {
            "users": rows,
            "q": q,
            "filter": filt,
            "page": page,
            "pages": pages,
            "total": total,
            "flash": _read_flash(request),
        },
    )


@require_admin
async def user_ban(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    data = await request.post()
    try:
        target_uid = int(data.get("user_id"))
    except (TypeError, ValueError):
        raise web.HTTPFound("/users" + _flash(request, "ভুল ইউজার আইডি।", "err"))

    action = (data.get("action") or "ban").strip()
    if action == "unban":
        await db.unban(target_uid)
        await db.log_event("user_unban", {"uid": target_uid, "by": request["user_id"]})
        raise web.HTTPFound("/users" + _flash(request, f"✅ ইউজার #{target_uid}-এর ব্যান তুলে নেওয়া হয়েছে।", "ok"))

    # ban
    reason = (data.get("reason") or "").strip()
    duration_raw = (data.get("duration") or "0").strip()
    try:
        duration_min = int(duration_raw)
    except ValueError:
        duration_min = 0

    import time as _time
    expires_at = (_time.time() + duration_min * 60) if duration_min > 0 else None
    await db.ban(target_uid, reason=reason, expires_at=expires_at)
    await db.log_event("user_ban", {"uid": target_uid, "duration_min": duration_min, "by": request["user_id"]})

    if duration_min > 0:
        if duration_min < 60:
            label = f"{duration_min} মিনিট"
        elif duration_min < 1440:
            label = f"{duration_min // 60} ঘন্টা"
        else:
            label = f"{duration_min // 1440} দিন"
        msg = f"⛔ ইউজার #{target_uid}-কে {label}ের জন্য ব্যান করা হয়েছে।"
    else:
        msg = f"⛔ ইউজার #{target_uid}-কে স্থায়ীভাবে ব্যান করা হয়েছে।"

    # ব্যানের নোটিফিকেশন পাঠাই
    if duration_min > 0:
        if duration_min < 60:
            dur_text = f"{duration_min} মিনিট"
        elif duration_min < 1440:
            dur_text = f"{duration_min // 60} ঘন্টা"
        else:
            dur_text = f"{duration_min // 1440} দিন"
        notice = f"⛔ আপনাকে {dur_text}ের জন্য সাময়িকভাবে বট ব্যবহার থেকে বিরত রাখা হয়েছে।"
    else:
        notice = "⛔ আপনাকে এই বট থেকে স্থায়ীভাবে ব্যান করা হয়েছে।"
    try:
        await bot.send_message(target_uid, notice)
    except Exception:
        pass

    raise web.HTTPFound("/users" + _flash(request, msg, "ok"))


@require_admin
async def user_send_dm(request: web.Request) -> web.Response:
    bot = request.app["bot"]

    # multipart form (ছবি সহ) অথবা সাধারণ form উভয়ই সাপোর্ট করে
    content_type = request.content_type or ""
    if "multipart" in content_type:
        reader = await request.multipart()
        target_uid_raw = ""
        text = ""
        buttons_raw = ""
        photo_bytes: Optional[bytes] = None
        photo_filename: Optional[str] = None
        async for part in reader:
            if part.name == "user_id":
                target_uid_raw = (await part.text()) or ""
            elif part.name == "text":
                text = (await part.text()) or ""
            elif part.name == "buttons":
                buttons_raw = (await part.text()) or ""
            elif part.name == "photo" and part.filename:
                data_bytes = await part.read(decode=False)
                if data_bytes:
                    photo_bytes = data_bytes
                    photo_filename = part.filename
    else:
        data = await request.post()
        target_uid_raw = str(data.get("user_id") or "")
        text = (data.get("text") or "").strip()
        buttons_raw = ""
        photo_bytes = None
        photo_filename = None

    try:
        target_uid = int(target_uid_raw)
    except (TypeError, ValueError):
        raise web.HTTPFound("/users" + _flash(request, "ভুল ইউজার আইডি।", "err"))

    text = text.strip()
    if not text and not photo_bytes:
        raise web.HTTPFound("/users" + _flash(request, "মেসেজ বা ছবি দিতে হবে।", "err"))

    kb = _parse_buttons(buttons_raw) if buttons_raw.strip() else None

    try:
        if photo_bytes:
            from aiogram.types import BufferedInputFile as BIF
            photo_file = BIF(photo_bytes, filename=photo_filename or "photo.jpg")
            await bot.send_photo(
                target_uid,
                photo=photo_file,
                caption=text or None,
                reply_markup=kb,
            )
        else:
            await bot.send_message(target_uid, text, reply_markup=kb)
        await db.log_event("admin_dm", {"to": target_uid, "by": request["user_id"]})
        raise web.HTTPFound("/users" + _flash(request, f"✅ ইউজার #{target_uid}-কে মেসেজ পাঠানো হয়েছে।", "ok"))
    except web.HTTPFound:
        raise
    except TelegramForbiddenError:
        raise web.HTTPFound("/users" + _flash(request, "ইউজার বটকে ব্লক করেছে — মেসেজ পাঠানো যায়নি।", "err"))
    except Exception as exc:
        raise web.HTTPFound("/users" + _flash(request, f"ত্রুটি: {exc}", "err"))


# ──────────────────────────────────────────────────────────────── #
# Statistics / Analytics
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def stats_page(request: web.Request) -> web.Response:
    (
        total_movies,
        total_hits,
        all_uids,
        verified_count,
        blocked_count,
        bans,
        pending_req,
        done_req,
        rejected_req,
        top_movies,
        recent_movies,
        today_users,
        week_users,
        month_users,
        today_deliveries,
        week_deliveries,
    ) = await asyncio.gather(
        db.count_movies(),
        db.total_hits(),
        db.all_user_ids(),
        db.fetch_value("SELECT COUNT(*) FROM users WHERE verified=1"),
        db.fetch_value("SELECT COUNT(*) FROM users WHERE is_blocked=1"),
        db.list_bans(),
        db.count_movie_requests("pending"),
        db.count_movie_requests("done"),
        db.count_movie_requests("rejected"),
        db.fetch_all("SELECT id, title, file_type, hits FROM movies ORDER BY hits DESC LIMIT 15"),
        db.fetch_all("SELECT id, title, file_type, added_at FROM movies ORDER BY added_at DESC LIMIT 10"),
        db.fetch_value("SELECT COUNT(*) FROM users WHERE joined_at >= strftime('%s','now') - 86400"),
        db.fetch_value("SELECT COUNT(*) FROM users WHERE joined_at >= strftime('%s','now') - 604800"),
        db.fetch_value("SELECT COUNT(*) FROM users WHERE joined_at >= strftime('%s','now') - 2592000"),
        db.fetch_value("SELECT COUNT(*) FROM logs WHERE kind='delivered' AND created_at >= strftime('%s','now') - 86400"),
        db.fetch_value("SELECT COUNT(*) FROM logs WHERE kind='delivered' AND created_at >= strftime('%s','now') - 604800"),
    )

    return aiohttp_jinja2.render_template(
        "stats.html",
        request,
        {
            "total_movies": total_movies,
            "total_hits": int(total_hits or 0),
            "total_users": len(all_uids),
            "verified_count": int(verified_count or 0),
            "blocked_count": int(blocked_count or 0),
            "banned_count": len(bans),
            "pending_req": pending_req,
            "done_req": done_req,
            "rejected_req": rejected_req,
            "top_movies": top_movies,
            "recent_movies": recent_movies,
            "today_users": int(today_users or 0),
            "week_users": int(week_users or 0),
            "month_users": int(month_users or 0),
            "today_deliveries": int(today_deliveries or 0),
            "week_deliveries": int(week_deliveries or 0),
            "flash": _read_flash(request),
        },
    )


# ──────────────────────────────────────────────────────────────── #
# Maintenance Mode
# ──────────────────────────────────────────────────────────────── #
async def _broadcast_maintenance(bot: Any, duration_mins: int) -> None:
    """সব ইউজারকে মেইনটেনেন্স শুরুর নোটিফিকেশন পাঠাও (background)."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    user_ids = await db.all_user_ids()
    disc_url = await db.get_discussion_url()
    disc_line = "\n💬 <b>জরুরি প্রয়োজনে আমাদের গ্রুপে যোগ দিন।</b>" if disc_url else ""
    text = (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔧  <b>রক্ষণাবেক্ষণ শুরু হয়েছে</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ <b>আনুমানিক সময়: {duration_mins} মিনিট</b>\n\n"
        "আমরা বটকে আরও উন্নত করতে কাজ করছি।\n"
        f"একটু অপেক্ষা করুন — শীঘ্রই ফিরে আসছি! 🙏"
        f"{disc_line}"
    )
    kb = None
    if disc_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💬 আমাদের গ্রুপ", url=disc_url)
        ]])

    sent = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, reply_markup=kb)
            sent += 1
            if sent % 25 == 0:
                await asyncio.sleep(1)
        except Exception:
            pass
    log.info("Maintenance broadcast done: %d/%d users notified", sent, len(user_ids))


@require_admin
async def maintenance_toggle(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    data = await request.post()
    action = (data.get("action") or "").strip()

    if action == "off":
        await db.set_maintenance_timed(False)
        await db.log_event("maintenance_toggle", {"on": False, "by": request["user_id"]})
        raise web.HTTPFound("/settings" + _flash(request, "✅ মেইনটেনেন্স মোড বন্ধ করা হয়েছে।", "ok"))

    # Turn ON with duration
    try:
        duration_mins = int(data.get("duration_mins") or 30)
    except (TypeError, ValueError):
        duration_mins = 30
    duration_mins = max(1, min(duration_mins, 480))

    import time as _time
    total_secs = duration_mins * 60.0
    until = _time.time() + total_secs
    await db.set_maintenance_timed(True, until, total_secs)
    await db.log_event("maintenance_toggle", {"on": True, "mins": duration_mins, "by": request["user_id"]})

    # Broadcast in background
    asyncio.create_task(_broadcast_maintenance(bot, duration_mins))

    raise web.HTTPFound("/settings" + _flash(request, f"🔧 মেইনটেনেন্স মোড চালু — {duration_mins} মিনিটের জন্য। সব ইউজারকে জানানো হচ্ছে।", "warn"))


# ──────────────────────────────────────────────────────────────── #
# Settings
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def settings_page(request: web.Request) -> web.Response:
    import time as _time
    fj, bans, verified_count, channel_count, maintenance_on, maintenance_until, disc_url = await asyncio.gather(
        db.list_force_join(),
        db.list_bans(),
        db.fetch_value("SELECT COUNT(*) FROM users WHERE verified = 1"),
        db.fetch_value("SELECT COUNT(*) FROM channels"),
        db.is_maintenance(),
        db.get_maintenance_until(),
        db.get_discussion_url(),
    )
    # Auto-expire check
    now = _time.time()
    if maintenance_on and maintenance_until > 0 and now >= maintenance_until:
        await db.set_maintenance_timed(False)
        maintenance_on = False
        maintenance_until = 0.0

    return aiohttp_jinja2.render_template(
        "settings.html",
        request,
        {
            "force_join": fj,
            "bans": bans,
            "request_group_id": settings.request_group_id,
            "backup_channel_id": settings.backup_channel_id,
            "admin_ids": settings.admin_ids,
            "verified_count": int(verified_count or 0),
            "channel_count": int(channel_count or 0),
            "maintenance_on": maintenance_on,
            "maintenance_until": maintenance_until,
            "discussion_url": disc_url,
            "flash": _read_flash(request),
        },
    )


@require_admin
async def settings_discussion_save(request: web.Request) -> web.Response:
    data = await request.post()
    url = (data.get("url") or "").strip()
    await db.set_discussion_url(url)
    await db.log_event("discussion_url_update", {"url": url, "by": request["user_id"]})
    raise web.HTTPFound("/settings" + _flash(request, "✅ ডিসকাশন গ্রুপ লিংক সেভ হয়েছে।", "ok"))


@require_admin
async def settings_reset_verifications(request: web.Request) -> web.Response:
    await db.reset_all_verifications()
    await db.log_event("verifications_reset", {"by": request["user_id"]})
    raise web.HTTPFound("/settings" + _flash(request, "সব ইউজারের ভেরিফিকেশন রিসেট হয়েছে।", "ok"))


@require_admin
async def settings_fj_add(request: web.Request) -> web.Response:
    data = await request.post()
    chat_raw = (data.get("chat_id") or "").strip()
    title = (data.get("title") or "").strip() or None
    username = (data.get("username") or "").strip().lstrip("@") or None
    invite = (data.get("invite_url") or "").strip() or None
    try:
        chat_id = int(chat_raw)
    except ValueError:
        raise web.HTTPFound("/settings" + _flash(request, "Chat id অবশ্যই সংখ্যা হতে হবে।", "err"))
    bot = request.app["bot"]
    try:
        info = await bot.get_chat(chat_id)
        if not title:
            title = info.title or info.full_name
        if not username:
            username = info.username
        if not invite:
            try:
                invite = await bot.export_chat_invite_link(chat_id)
            except Exception:
                pass
    except Exception:
        pass
    await db.add_force_join(chat_id, title=title, username=username, invite_url=invite)
    raise web.HTTPFound("/settings" + _flash(request, "✅ ভেরিফিকেশন চ্যানেল যোগ হয়েছে।", "ok"))


@require_admin
async def settings_fj_del(request: web.Request) -> web.Response:
    data = await request.post()
    try:
        chat_id = int(data.get("chat_id"))
    except (TypeError, ValueError):
        raise web.HTTPFound("/settings")
    await db.remove_force_join(chat_id)
    raise web.HTTPFound("/settings" + _flash(request, "সরানো হয়েছে।", "ok"))


@require_admin
async def settings_ban(request: web.Request) -> web.Response:
    data = await request.post()
    action = data.get("action")
    try:
        uid = int(data.get("user_id"))
    except (TypeError, ValueError):
        raise web.HTTPFound("/settings")
    if action == "ban":
        await db.ban(uid, reason=(data.get("reason") or "").strip())
    elif action == "unban":
        await db.unban(uid)
    raise web.HTTPFound("/settings" + _flash(request, "আপডেট হয়েছে।", "ok"))


# ──────────────────────────────────────────────────────────────── #
# Route wiring
# ──────────────────────────────────────────────────────────────── #
def setup_routes(app: web.Application) -> None:
    app.router.add_get("/healthz", health)
    app.router.add_get("/login", login_get)
    app.router.add_post("/login", login_post)
    app.router.add_post("/logout", logout)

    app.router.add_get("/", dashboard)

    app.router.add_get("/movies", movies_list)
    app.router.add_post("/movies/delete", movie_delete)

    app.router.add_get("/post", post_form)
    app.router.add_post("/post", post_submit)

    app.router.add_get("/broadcast", broadcast_form)
    app.router.add_post("/broadcast", broadcast_submit)
    app.router.add_get("/broadcast/status", broadcast_status)

    # Movie requests
    app.router.add_get("/requests", requests_page)
    app.router.add_post("/requests/resolve", requests_resolve)
    app.router.add_post("/requests/delete", requests_delete)

    # Bot messages editor
    app.router.add_get("/bot-messages", bot_messages_page)
    app.router.add_post("/bot-messages/save", bot_messages_save)
    app.router.add_post("/bot-messages/reset", bot_messages_reset)

    # Multi-channel/group manager
    app.router.add_get("/channels", channels_page)
    app.router.add_post("/channels/add", channels_add)
    app.router.add_post("/channels/del", channels_del)

    # Maintenance mode toggle
    app.router.add_post("/maintenance/toggle", maintenance_toggle)
    app.router.add_post("/settings/discussion", settings_discussion_save)

    # Users
    app.router.add_get("/users", users_page)
    app.router.add_post("/users/send-dm", user_send_dm)
    app.router.add_post("/users/ban", user_ban)

    # Statistics
    app.router.add_get("/stats", stats_page)

    # Settings
    app.router.add_get("/settings", settings_page)
    app.router.add_post("/settings/fj/add", settings_fj_add)
    app.router.add_post("/settings/fj/del", settings_fj_del)
    app.router.add_post("/settings/ban", settings_ban)
    app.router.add_post("/settings/verify/reset", settings_reset_verifications)

    if _STATIC_DIR.exists():
        app.router.add_static("/static/", _STATIC_DIR, show_index=False)
