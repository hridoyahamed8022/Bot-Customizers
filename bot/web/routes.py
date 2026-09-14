"""All admin panel HTTP routes."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import io
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl

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
from ..utils import esc, schedule_delete, MSG_TTL
from ..utils_ouo import shorten_url
from .auth import (
    check_credentials,
    clear_session_cookie,
    require_admin,
    set_session_cookie,
)

log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"
_MINIAPP_DIR = _STATIC_DIR / "miniapp"


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


def _js_redirect(url: str) -> web.Response:
    """Replit proxy-তে HTTPFound redirect কাজ করে না, JS দিয়ে redirect করি।"""
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<script>window.location.replace({json.dumps(url)});</script>
</head><body>রিডাইরেক্ট হচ্ছে...</body></html>"""
    return web.Response(text=html, content_type="text/html")


# ──────────────────────────────────────────────────────────────── #
# Public routes
# ──────────────────────────────────────────────────────────────── #
async def health(request: web.Request) -> web.Response:
    try:
        db_ok = (await db.fetch_value("SELECT 1")) == 1
    except Exception:
        db_ok = False
    return web.json_response({
        "ok": db_ok,
        "db": db_ok,
        "ts": time.time(),
        "service": "Moviex Hub Bot",
    }, status=200 if db_ok else 503, headers={"Cache-Control": "no-store"})


async def login_get(request: web.Request) -> web.StreamResponse:
    if request.get("is_admin"):
        return _js_redirect("/")
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
    await db.log_event("login_ok", {"username": username})
    # SameSite=None cookie + JS redirect — Replit proxy-তে HTTPFound redirect কাজ করে না
    html = """<!doctype html><html><head><meta charset="utf-8">
<script>window.location.replace('/');</script>
</head><body>লগইন হচ্ছে...</body></html>"""
    response = web.Response(
        text=html,
        content_type="text/html",
    )
    set_session_cookie(response, session_token)
    return response


async def logout(request: web.Request) -> web.Response:
    token = request.cookies.get("admin_session")
    if token:
        await db.revoke_session(token)
    response = _js_redirect("/login")
    clear_session_cookie(response)
    return response


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
        return _js_redirect("/movies")
    await db.delete_movie(mid)
    await db.log_event("movie_delete", {"id": mid, "by": request["user_id"]})
    return _js_redirect("/movies" + _flash(request, "মুভি মুছে ফেলা হয়েছে।", "ok"))


@require_admin
async def movie_edit_form(request: web.Request) -> web.Response:
    try:
        mid = int(request.query.get("id", "0"))
    except (TypeError, ValueError):
        return _js_redirect("/movies")
    movie = await db.get_movie(mid)
    if not movie:
        return _js_redirect("/movies" + _flash(request, "মুভিটি পাওয়া যায়নি।", "err"))
    return aiohttp_jinja2.render_template(
        "movie_edit.html",
        request,
        {"movie": movie, "flash": _read_flash(request)},
    )


@require_admin
async def movie_edit_save(request: web.Request) -> web.Response:
    data = await request.post()
    try:
        mid = int(data.get("id", "0"))
    except (TypeError, ValueError):
        return _js_redirect("/movies")
    title = (data.get("title") or "").strip()
    caption = (data.get("caption") or "").strip()
    category = (data.get("category") or "মুভি").strip()
    poster_url = (data.get("poster_url") or "").strip()
    if not title:
        return _js_redirect(f"/movies/edit?id={mid}" + _flash(request, "শিরোনাম খালি রাখা যাবে না।", "err"))
    await db.update_movie(mid, title, caption, category, poster_url)
    await db.log_event("movie_edit", {"id": mid, "title": title, "by": request["user_id"]})
    return _js_redirect("/movies" + _flash(request, f"✅ মুভি আপডেট হয়েছে: {title}", "ok"))


# ──────────────────────────────────────────────────────────────── #
# Telegram Mini App
# ──────────────────────────────────────────────────────────────── #
def _miniapp_init_data(request: web.Request) -> str:
    return (
        request.headers.get("X-Telegram-Init-Data", "").strip()
        or request.headers.get("X-Telegram-WebApp-Init-Data", "").strip()
        or (
            request.headers.get("Authorization", "").strip()[4:]
            if request.headers.get("Authorization", "").strip().lower().startswith("tma ")
            else ""
        )
        or request.query.get("init_data", "").strip()
    )


def _miniapp_user_profile(request: web.Request) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Validate Telegram WebApp initData and return the trusted user profile."""
    raw = _miniapp_init_data(request)
    if not raw:
        return None
    try:
        values = dict(parse_qsl(raw, keep_blank_values=True))
        received_hash = values.pop("hash", "")
        auth_date = int(values.get("auth_date", "0"))
        if not received_hash or not auth_date or time.time() - auth_date > 86400:
            return None
        check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
        secret_key = hmac.new(
            b"WebAppData", settings.bot_token.encode(), hashlib.sha256
        ).digest()
        expected_hash = hmac.new(
            secret_key, check_string.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(received_hash, expected_hash):
            return None
        user = json.loads(values.get("user", "{}"))
        user_id = int(user.get("id", 0))
        return (user_id, user) if user_id else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _miniapp_user_id(request: web.Request) -> Optional[int]:
    profile = _miniapp_user_profile(request)
    return profile[0] if profile else None


def _miniapp_actor(request: web.Request) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Use verified Telegram identity, or a stable guest identity for ratings/comments."""
    profile = _miniapp_user_profile(request)
    if profile:
        return profile
    if _miniapp_init_data(request):
        return None
    fingerprint = f"{request.remote or 'browser'}|{request.headers.get('User-Agent', '')}"
    guest_id = -(int(hashlib.sha256(fingerprint.encode()).hexdigest()[:12], 16) % 2_000_000_000 + 1)
    return guest_id, {"first_name": "Guest"}


async def miniapp_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_MINIAPP_DIR / "index.html")


async def _bot_identity(request: web.Request) -> Tuple[str, str]:
    """Return the live Telegram display name and username, with safe fallback."""
    cached = request.app.get("bot_identity")
    if cached:
        return cached
    name = (await db.get_setting("bot_name", "Moviex Hub Team") or "Moviex Hub Team").strip()
    username = await db.get_setting("bot_username", "Moviex_hub_bot")
    try:
        me = await request.app["bot"].get_me()
        username = me.username or username
    except Exception:
        log.exception("Could not load live bot identity")
    request.app["bot_identity"] = (name, username)
    return name, username


async def miniapp_config(request: web.Request) -> web.Response:
    name, username = await _bot_identity(request)
    wait_seconds = max(5, int(await db.get_setting("ad_wait_seconds", "10") or 10))
    return web.json_response(
        {
            "ok": True,
            "brand_name": name,
            "bot_username": username,
            "ad_wait_seconds": wait_seconds,
        },
        headers={"Cache-Control": "no-store"},
    )


def _miniapp_movie_payload(row: Any) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "category": row["category"] or "মুভি",
        "poster_url": row["poster_url"] or f"/api/miniapp/posters/{row['id']}",
        "caption": row["caption"] or "",
        "file_type": row["file_type"],
        "file_name": row["file_name"] or "",
        "hits": int(row["hits"] or 0),
        "size_bytes": int(row["size_bytes"] or 0),
        "duration": int(row["duration"] or 0),
        "added_at": float(row["added_at"] or 0),
    }


async def miniapp_poster(request: web.Request) -> web.Response:
    """Serve a Telegram thumbnail without exposing the bot token to the browser."""
    try:
        movie_id = int(request.match_info["movie_id"])
    except (KeyError, TypeError, ValueError):
        return web.Response(status=404)
    movie = await db.get_movie(movie_id)
    if not movie:
        return web.Response(status=404)

    poster_file_id = movie["poster_file_id"]
    bot = request.app["bot"]

    # Backfill old indexed movies from their original message thumbnail. This
    # runs only when a poster is actually viewed and caches the file_id.
    if not poster_file_id and movie["file_type"] != "photo":
        try:
            if settings.admin_chat_id and movie["source_chat_id"] and movie["source_message_id"]:
                forwarded = await bot.forward_message(
                    chat_id=settings.admin_chat_id,
                    from_chat_id=movie["source_chat_id"],
                    message_id=movie["source_message_id"],
                    disable_notification=True,
                )
                media = getattr(forwarded, "video", None) or getattr(forwarded, "document", None)
                if media:
                    thumbnail = getattr(media, "thumbnail", None)
                    poster_file_id = getattr(thumbnail, "file_id", None)
                try:
                    await bot.delete_message(settings.admin_chat_id, forwarded.message_id)
                except Exception:
                    pass
                if poster_file_id:
                    await db.set_movie_poster_file_id(movie_id, poster_file_id)
        except Exception:
            log.exception("Could not backfill poster thumbnail for movie %s", movie_id)

    if not poster_file_id and movie["file_type"] == "photo":
        poster_file_id = movie["file_id"]
    if not poster_file_id:
        # Some old Telegram posts have no thumbnail at all. Return a real
        # image response so the UI never logs a broken-image 404.
        title = html.escape(str(movie["title"] or "Moviex Hub"))[:42]
        initials = html.escape("".join(part[:1] for part in str(movie["title"] or "MB").split()[:2]).upper())
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="420" viewBox="0 0 720 420">'
            '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#402080"/><stop offset=".58" stop-color="#e63f7d"/>'
            '<stop offset="1" stop-color="#10243d"/></linearGradient></defs>'
            '<rect width="720" height="420" fill="url(#g)"/>'
            f'<text x="36" y="320" fill="#fff" opacity=".9" font-size="86" font-family="Arial" font-weight="700">{initials}</text>'
            f'<text x="36" y="370" fill="#fff" opacity=".72" font-size="20" font-family="Arial">{title}</text>'
            "</svg>"
        )
        return web.Response(
            body=svg.encode(), content_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    try:
        file_info = await bot.get_file(poster_file_id)
        output = io.BytesIO()
        await bot.download_file(file_info.file_path, output)
        data = output.getvalue()
        if not data:
            return web.Response(status=404)
        return web.Response(
            body=data,
            content_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception:
        log.exception("Could not download poster thumbnail for movie %s", movie_id)
        return web.Response(status=404)


async def miniapp_movies(request: web.Request) -> web.Response:
    query = (request.query.get("q") or "").strip()
    category = (request.query.get("category") or "").strip()
    try:
        limit = max(1, min(1200, int(request.query.get("limit", "1200"))))
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        limit, offset = 1200, 0

    if query:
        rows = await db.search_movies(query, limit=limit, offset=offset)
        recent_rows, trending_rows = [], []
    else:
        rows = await db.list_movies(limit, offset)
        recent_rows = await db.list_movies(12, 0)
        trending_rows = await db.get_popular_movies(12)
    if category:
        rows = [row for row in rows if (row["category"] or "মুভি") == category]
        recent_rows = [row for row in recent_rows if (row["category"] or "মুভি") == category]
        trending_rows = [row for row in trending_rows if (row["category"] or "মুভি") == category]

    categories = await db.fetch_all(
        "SELECT category, COUNT(*) AS count FROM movies GROUP BY category ORDER BY category"
    )
    return web.json_response(
        {
            "ok": True,
            "movies": [_miniapp_movie_payload(row) for row in rows],
            "recent": [_miniapp_movie_payload(row) for row in recent_rows],
            "trending": [_miniapp_movie_payload(row) for row in trending_rows],
            "total": len(rows),
            "categories": [
                {"name": row["category"] or "মুভি", "count": int(row["count"])}
                for row in categories
            ],
        },
        headers={"Cache-Control": "no-store"},
    )


async def miniapp_movie_detail(request: web.Request) -> web.Response:
    try:
        movie_id = int(request.match_info["movie_id"])
    except (KeyError, TypeError, ValueError):
        return web.json_response({"ok": False, "error": "মুভিটি পাওয়া যায়নি।"}, status=404)
    movie = await db.get_movie(movie_id)
    if not movie:
        return web.json_response({"ok": False, "error": "মুভিটি পাওয়া যায়নি।"}, status=404)
    user_id = _miniapp_user_id(request)
    average, rating_count = await db.get_movie_avg_rating(movie_id)
    return web.json_response(
        {
            "ok": True,
            "movie": _miniapp_movie_payload(movie),
            "rating": {"average": average, "count": rating_count},
            "user_rating": await db.get_user_rating(user_id, movie_id) if user_id else None,
            "favorite": await db.is_favorite(user_id, movie_id) if user_id else False,
        },
        headers={"Cache-Control": "no-store"},
    )


async def miniapp_rate_movie(request: web.Request) -> web.Response:
    actor = _miniapp_actor(request)
    if not actor:
        return web.json_response({"ok": False, "error": "Telegram session যাচাই করা যায়নি। Mini App আবার খুলুন।"}, status=401)
    user_id = actor[0]
    try:
        body = await request.json()
        movie_id = int(body.get("movie_id", 0))
        rating = int(body.get("rating", 0))
    except (TypeError, ValueError, json.JSONDecodeError):
        return web.json_response({"ok": False, "error": "সঠিক rating দিন।"}, status=400)
    if not await db.get_movie(movie_id) or rating < 1 or rating > 5:
        return web.json_response({"ok": False, "error": "সঠিক rating দিন।"}, status=400)
    await db.rate_movie(user_id, movie_id, rating)
    average, count = await db.get_movie_avg_rating(movie_id)
    return web.json_response({"ok": True, "average": average, "count": count, "user_rating": rating})


async def miniapp_toggle_favorite(request: web.Request) -> web.Response:
    user_id = _miniapp_user_id(request)
    if not user_id:
        return web.json_response({"ok": False, "error": "Telegram থেকে Mini App খুলে আবার চেষ্টা করুন।"}, status=401)
    try:
        body = await request.json()
        movie_id = int(body.get("movie_id", 0))
    except (TypeError, ValueError, json.JSONDecodeError):
        return web.json_response({"ok": False, "error": "সঠিক মুভি নির্বাচন করুন।"}, status=400)
    if not await db.get_movie(movie_id):
        return web.json_response({"ok": False, "error": "মুভিটি পাওয়া যায়নি।"}, status=404)
    if await db.is_favorite(user_id, movie_id):
        await db.remove_favorite(user_id, movie_id)
        favorite = False
    else:
        await db.add_favorite(user_id, movie_id)
        favorite = True
    return web.json_response({"ok": True, "favorite": favorite})


async def miniapp_profile(request: web.Request) -> web.Response:
    actor = _miniapp_actor(request)
    if not actor:
        return web.json_response({"ok": False, "error": "Telegram session যাচাই করা যায়নি।"}, status=401)
    user_id, user_data = actor
    stats = await db.get_user_stats(user_id)
    favorite_rows, recent_rows = await asyncio.gather(
        db.get_favorites(user_id, limit=40),
        db.get_user_recent_downloads(user_id, limit=8),
    )
    user = stats["user"]
    return web.json_response(
        {
            "ok": True,
            "profile": {
                "user_id": user_id,
                "name": (
                    (user["first_name"] if user else None)
                    or user_data.get("first_name")
                    or user_data.get("username")
                    or "Guest"
                ),
                "username": (user["username"] if user else None) or user_data.get("username") or "",
                "is_guest": user is None,
            },
            "stats": {
                "downloads": stats["downloads"],
                "requests": stats["requests"],
                "favorites": stats["favorites"],
                "warnings": stats["warnings"],
                "subscribed": stats["subscribed"],
            },
            "favorites": [_miniapp_movie_payload(row) for row in favorite_rows],
            "recent_downloads": [_miniapp_movie_payload(row) for row in recent_rows],
        },
        headers={"Cache-Control": "no-store"},
    )


async def miniapp_maya(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        text = str(body.get("message", "")).strip()[:1000]
        history = body.get("history") or []
        if not isinstance(history, list):
            history = []
        history = [
            {"role": str(item.get("role", "")), "content": str(item.get("content", ""))[:1000]}
            for item in history[-8:]
            if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
        ]
    except (TypeError, ValueError, json.JSONDecodeError):
        return web.json_response({"ok": False, "error": "Maya-কে একটি message দিন।"}, status=400)
    if not text:
        return web.json_response({"ok": False, "error": "Maya-কে কী জানতে চান লিখুন।"}, status=400)

    try:
        from ..handlers.ai_chat import _ask_openai
        reply, found = await _ask_openai(history, text)
    except Exception:
        log.exception("Mini App Maya failed")
        reply, found = "", []

    # AI integration may be unavailable; Maya still works as a movie search assistant.
    if not found:
        found = await db.search_movies(text, limit=8, offset=0)
        if found and (not reply or "পাওয়া যাচ্ছে না" in reply):
            reply = f"✅ “{text}” নামে {len(found)}টি movie পেয়েছি। নিচে বেছে নিন।"
    if not reply:
        reply = "মুভির নাম বা কোনো প্রশ্ন লিখুন—Maya সাহায্য করার চেষ্টা করবে।"
    return web.json_response(
        {
            "ok": True,
            "reply": reply,
            "movies": [_miniapp_movie_payload(row) for row in found[:8]],
        },
        headers={"Cache-Control": "no-store"},
    )


def _upcoming_payload(row: Any) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "category": row["category"] or "Upcoming",
        "release_date": row["release_date"] or "",
        "poster_url": row["poster_url"] or "",
        "description": row["description"] or "",
    }


async def miniapp_upcoming(request: web.Request) -> web.Response:
    rows = await db.list_upcoming_movies(100)
    return web.json_response(
        {"ok": True, "upcoming": [_upcoming_payload(row) for row in rows]},
        headers={"Cache-Control": "no-store"},
    )


async def miniapp_comments(request: web.Request) -> web.Response:
    try:
        movie_id = int(request.match_info["movie_id"])
    except (KeyError, TypeError, ValueError):
        return web.json_response({"ok": False, "error": "মুভিটি পাওয়া যায়নি।"}, status=404)
    if not await db.get_movie(movie_id):
        return web.json_response({"ok": False, "error": "মুভিটি পাওয়া যায়নি।"}, status=404)
    rows = await db.list_movie_comments(movie_id)
    return web.json_response(
        {
            "ok": True,
            "comments": [
                {
                    "name": row["display_name"] or "Movie fan",
                    "comment": row["comment"],
                    "created_at": float(row["created_at"] or 0),
                }
                for row in rows
            ],
        },
        headers={"Cache-Control": "no-store"},
    )


async def miniapp_add_comment(request: web.Request) -> web.Response:
    actor = _miniapp_actor(request)
    if not actor:
        return web.json_response({"ok": False, "error": "Telegram session যাচাই করা যায়নি। Mini App আবার খুলুন।"}, status=401)
    user_id, user = actor
    try:
        body = await request.json()
        movie_id = int(body.get("movie_id", 0))
        comment = str(body.get("comment", "")).strip()
    except (TypeError, ValueError, json.JSONDecodeError):
        return web.json_response({"ok": False, "error": "Comment সঠিক নয়।"}, status=400)
    if not comment or len(comment) > 500:
        return web.json_response({"ok": False, "error": "Comment ১ থেকে ৫০০ অক্ষরের মধ্যে দিন।"}, status=400)
    if not await db.get_movie(movie_id):
        return web.json_response({"ok": False, "error": "মুভিটি পাওয়া যায়নি।"}, status=404)
    display_name = user.get("first_name") or user.get("username") or "Guest"
    await db.add_movie_comment(user_id, display_name, movie_id, comment)
    return web.json_response({"ok": True, "name": display_name, "comment": comment})


# ──────────────────────────────────────────────────────────────── #
# Upcoming admin manager
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def upcoming_page(request: web.Request) -> web.Response:
    rows = await db.list_upcoming_movies(100)
    channels = await db.list_channels()
    return aiohttp_jinja2.render_template(
        "upcoming.html",
        request,
        {
            "upcoming": rows,
            "channels": channels,
            "request_group_id": settings.request_group_id,
            "flash": _read_flash(request),
        },
    )


@require_admin
async def upcoming_add(request: web.Request) -> web.Response:
    data = await request.post()
    title = (data.get("title") or "").strip()
    if not title:
        return _js_redirect("/upcoming" + _flash(request, "শিরোনাম দিতে হবে।", "err"))
    await db.add_upcoming_movie(
        title=title,
        category=(data.get("category") or "Upcoming").strip(),
        release_date=(data.get("release_date") or "").strip(),
        poster_url=(data.get("poster_url") or "").strip(),
        description=(data.get("description") or "").strip(),
    )
    await db.log_event("upcoming_add", {"title": title, "by": request["user_id"]})
    return _js_redirect("/upcoming" + _flash(request, "✅ Upcoming movie যোগ হয়েছে।", "ok"))


@require_admin
async def upcoming_delete(request: web.Request) -> web.Response:
    data = await request.post()
    try:
        upcoming_id = int(data.get("id", "0"))
    except (TypeError, ValueError):
        return _js_redirect("/upcoming")
    await db.delete_upcoming_movie(upcoming_id)
    return _js_redirect("/upcoming" + _flash(request, "Upcoming movie মুছে ফেলা হয়েছে।", "ok"))


@require_admin
async def upcoming_post(request: web.Request) -> web.Response:
    data = await request.post()
    try:
        upcoming_id = int(data.get("id", "0"))
        chat_id = int(data.get("chat_id") or settings.request_group_id or 0)
    except (TypeError, ValueError):
        return _js_redirect("/upcoming" + _flash(request, "সঠিক chat ID দিন।", "err"))
    item = await db.get_upcoming_movie(upcoming_id)
    if not item or not chat_id:
        return _js_redirect("/upcoming" + _flash(request, "Upcoming movie বা chat ID পাওয়া যায়নি।", "err"))
    bot = request.app["bot"]
    caption = f"🎬 <b>{esc(item['title'])}</b>"
    if item["release_date"]:
        caption += f"\n🗓 মুক্তি: {esc(item['release_date'])}"
    if item["description"]:
        caption += f"\n\n{esc(item['description'])}"
    try:
        if item["poster_url"]:
            await bot.send_photo(chat_id, photo=item["poster_url"], caption=caption, parse_mode="HTML")
        else:
            await bot.send_message(chat_id, caption, parse_mode="HTML")
    except Exception:
        log.exception("Upcoming post failed")
        return _js_redirect("/upcoming" + _flash(request, "পোস্ট করা যায়নি—poster URL বা chat ID যাচাই করুন।", "err"))
    await db.log_event("upcoming_post", {"id": upcoming_id, "chat_id": chat_id, "by": request["user_id"]})
    return _js_redirect("/upcoming" + _flash(request, "✅ Upcoming movie poster সহ পোস্ট হয়েছে।", "ok"))


async def miniapp_claim(request: web.Request) -> web.Response:
    user_id = _miniapp_user_id(request)
    if not user_id:
        return web.json_response(
            {"ok": False, "error": "Telegram থেকে Mini App খুলে আবার চেষ্টা করুন।"},
            status=401,
        )
    try:
        body = await request.json()
        movie_id = int(body.get("movie_id", 0))
    except (TypeError, ValueError, json.JSONDecodeError):
        movie_id = 0
    movie = await db.get_movie(movie_id)
    if not movie:
        return web.json_response({"ok": False, "error": "মুভিটি পাওয়া যায়নি।"}, status=404)
    if not settings.public_url:
        return web.json_response({"ok": False, "error": "Mini App URL কনফিগার করা নেই।"}, status=503)

    wait_seconds = max(5, int(await db.get_setting("ad_wait_seconds", "10") or 10))
    token = await db.create_ad_token(movie_id, user_id, wait_seconds=wait_seconds)
    raw_url = f"{settings.public_url.rstrip('/')}/ad/{token}"
    ad_url = await shorten_url(raw_url)
    return web.json_response(
        {
            "ok": True,
            "title": movie["title"],
            "ad_url": ad_url,
            "wait_seconds": wait_seconds,
        },
        headers={"Cache-Control": "no-store"},
    )


async def miniapp_complete_ad(request: web.Request) -> web.Response:
    """Deliver the file from the ad page itself; deep-links remain only a fallback."""
    token = (request.match_info.get("token") or "").strip()
    row = await db.get_ad_token(token)
    if not row:
        return web.json_response({"ok": False, "error": "লিংকটি আর কার্যকর নেই।"}, status=404)
    if row["used"] == 1:
        return web.json_response({"ok": True, "already_sent": True})
    if row["used"] == -1:
        return web.json_response({"ok": True, "processing": True})
    if time.time() > row["expires_at"]:
        return web.json_response({"ok": False, "error": "লিংকের মেয়াদ শেষ হয়ে গেছে।"}, status=410)

    if not await db.mark_ad_completed(token):
        return web.json_response({"ok": True, "processing": True})

    from ..handlers.callbacks import deliver_movie

    class _DirectTarget:
        def __init__(self, bot: Any, chat_id: int):
            self.bot = bot
            self.chat = type("Chat", (), {"id": chat_id})()
            self.from_user = type("User", (), {"id": chat_id})()

        async def answer(self, text: str, **kwargs: Any):
            return await self.bot.send_message(self.chat.id, text, **kwargs)

    if not await db.claim_ad_token(token):
        return web.json_response({"ok": True, "processing": True})
    try:
        delivered = await deliver_movie(
            _DirectTarget(request.app["bot"], row["user_id"]), row["movie_id"]
        )
    except Exception:
        log.exception("Ad delivery crashed: token=%s", token[:8])
        delivered = False
    if not delivered:
        await db.release_ad_token(token)
        return web.json_response({"ok": False, "error": "ফাইল পাঠানো যায়নি।"}, status=502)
    await db.mark_ad_token_used(token)
    log.info("Ad delivery completed: token=%s user=%s movie=%s", token[:8], row["user_id"], row["movie_id"])
    return web.json_response({"ok": True, "sent": True})


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
            return _js_redirect("/post" + _flash(request, "Chat id অবশ্যই সংখ্যা হতে হবে।", "err"))
    elif settings.request_group_id:
        target_chat = settings.request_group_id
    else:
        return _js_redirect("/post" + _flash(request, "কোনো চ্যানেল/গ্রুপ নির্বাচন করুন।", "err"))

    kb = _parse_buttons(buttons_raw)

    try:
        await _send_to_chat(
            bot, target_chat,
            text=text, media_bytes=media_bytes, media_filename=media_filename,
            media_kind=media_kind, reply_markup=kb,
        )
        await db.log_event("panel_post", {"chat_id": target_chat, "by": request["user_id"], "kind": media_kind})
        return _js_redirect("/post" + _flash(request, "✅ পোস্ট পাঠানো হয়েছে!", "ok"))
    except web.HTTPFound:
        raise
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        return _js_redirect("/post" + _flash(request, f"Telegram error: {exc}", "err"))
    except Exception as exc:
        log.exception("post failed")
        return _js_redirect("/post" + _flash(request, f"ত্রুটি: {exc}", "err"))


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
        msg = await bot.send_message(user_id, text, reply_markup=kb)
        asyncio.create_task(schedule_delete(bot, user_id, msg.message_id, MSG_TTL))
    except TelegramForbiddenError:
        await db.mark_blocked(user_id, True)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after + 1)
        try:
            msg = await bot.send_message(user_id, text, reply_markup=kb)
            asyncio.create_task(schedule_delete(bot, user_id, msg.message_id, MSG_TTL))
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
            msg = await _send_to_chat(
                bot, uid,
                text=text, media_bytes=media_bytes, media_filename=media_filename,
                media_kind=media_kind, reply_markup=kb,
            )
            if msg:
                asyncio.create_task(schedule_delete(bot, uid, msg.message_id, MSG_TTL))
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
            try:
                msg = await _send_to_chat(
                    bot, uid,
                    text=text, media_bytes=media_bytes, media_filename=media_filename,
                    media_kind=media_kind, reply_markup=kb,
                )
                if msg:
                    asyncio.create_task(schedule_delete(bot, uid, msg.message_id, MSG_TTL))
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
        return _js_redirect("/broadcast" + _flash(request, "ব্রডকাস্ট চলছে — শেষ হওয়া পর্যন্ত অপেক্ষা করুন।", "err"))

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
        return _js_redirect("/broadcast" + _flash(request, "মেসেজ বা মিডিয়া দিতে হবে।", "err"))

    targets = await db.all_user_ids()
    if not targets:
        return _js_redirect("/broadcast" + _flash(request, "কোনো ইউজার নেই।", "err"))

    kb = _parse_buttons(buttons_raw)
    _active_broadcasts[user_id] = {
        "started_at": time.time(), "total": len(targets),
        "sent": 0, "blocked": 0, "failed": 0, "progress": 0, "finished": False,
    }
    asyncio.create_task(
        _broadcast_worker(bot, user_id, targets, text, media_bytes, media_filename, media_kind, kb)
    )
    return _js_redirect("/broadcast")


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
        return _js_redirect("/requests")

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
            # ✅ প্রথম নোটিফিকেশন + repeating reminder চালু করো
            asyncio.create_task(db.create_movie_notification(user_id, title, req_id))
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            search_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🔍 এখনই সার্চ করুন",
                    callback_data="search:start",
                )
            ]])
            text = (
                f"🎬 <b>{esc(title)}</b> আপলোড হয়েছে!\n"
                f"বটে নামটি লিখে সার্চ করুন। 🍿\n"
                f"⏳ আরও 4 বার মনে করানো হবে।"
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
    return _js_redirect("/requests" + _flash(request, f"রিকোয়েস্ট #{req_id} → {label} (নোটিফিকেশন পাঠানো হচ্ছে…)", "ok"))


@require_admin
async def requests_delete(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    data = await request.post()
    try:
        req_id = int(data.get("id"))
    except (TypeError, ValueError):
        return _js_redirect("/requests")

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

    return _js_redirect("/requests" + _flash(request, f"রিকোয়েস্ট #{req_id} ডিলিট হয়েছে (নোটিফিকেশন পাঠানো হচ্ছে…)।", "ok"))


@require_admin
async def requests_dm(request: web.Request) -> web.Response:
    """রিকোয়েস্ট প্যানেল থেকে নির্দিষ্ট ইউজারকে কাস্টম DM পাঠাও।"""
    bot = request.app["bot"]
    data = await request.post()
    try:
        req_id = int(data.get("req_id", 0))
        target_user_id = int(data.get("user_id", 0))
    except (TypeError, ValueError):
        return _js_redirect("/requests" + _flash(request, "অবৈধ রিকোয়েস্ট।", "err"))

    text = (data.get("text") or "").strip()
    if not text:
        return _js_redirect("/requests" + _flash(request, "মেসেজ খালি রাখা যাবে না।", "err"))

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from aiogram.enums import ParseMode
    search_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔍 সার্চ করুন", callback_data="search:start")
    ]])
    from bot.utils import schedule_delete, MSG_TTL
    try:
        msg = await bot.send_message(
            target_user_id,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=search_kb,
        )
        asyncio.create_task(schedule_delete(bot, target_user_id, msg.message_id, MSG_TTL))
        return _js_redirect("/requests" + _flash(request, f"✅ রিকোয়েস্ট #{req_id} — ইউজারকে মেসেজ পাঠানো হয়েছে।", "ok"))
    except Exception as e:
        log.warning("requests_dm failed for user %d: %s", target_user_id, e)
        return _js_redirect("/requests" + _flash(request, f"❌ মেসেজ পাঠানো যায়নি: {e}", "err"))


# ──────────────────────────────────────────────────────────────── #
# Bot Messages Editor
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def bot_messages_page(request: web.Request) -> web.Response:
    msgs = []
    for cfg in BOT_MESSAGES_CONFIG:
        val = await db.get_bot_message(cfg["key"], cfg["default"])
        msgs.append({"key": cfg["key"], "label": cfg["label"], "value": val})
    welcome_photo_file_id = await db.get_setting("welcome_photo_file_id", "")
    welcome_photo_caption = await db.get_setting("welcome_photo_caption", "")
    return aiohttp_jinja2.render_template(
        "bot_messages.html",
        request,
        {
            "messages": msgs,
            "flash": _read_flash(request),
            "welcome_photo_file_id": welcome_photo_file_id or "",
            "welcome_photo_caption": welcome_photo_caption or "",
        },
    )


@require_admin
async def bot_messages_welcome_photo(request: web.Request) -> web.Response:
    """ওয়েলকাম ছবি আপলোড করে bot-এ পাঠিয়ে file_id সেভ করে।"""
    bot = request.app["bot"]
    reader = await request.multipart()
    photo_bytes: Optional[bytes] = None
    photo_filename: Optional[str] = None
    caption = ""
    async for part in reader:
        if part.name == "photo" and part.filename:
            data_bytes = await part.read(decode=False)
            if data_bytes:
                photo_bytes = data_bytes
                photo_filename = part.filename
        elif part.name == "caption":
            caption = (await part.text()) or ""

    if not photo_bytes:
        return _js_redirect("/bot-messages" + _flash(request, "কোনো ছবি নির্বাচন করা হয়নি।", "err"))

    admin_chat_id = settings.admin_chat_id
    if not admin_chat_id:
        return _js_redirect("/bot-messages" + _flash(request, "ADMIN_CHAT_ID সেট করা নেই — ছবি সেভ করা যাচ্ছে না।", "err"))

    try:
        from aiogram.types import BufferedInputFile as BIF
        photo_file = BIF(photo_bytes, filename=photo_filename or "welcome.jpg")
        sent = await bot.send_photo(admin_chat_id, photo=photo_file, caption=caption or None)
        file_id = sent.photo[-1].file_id
        await db.set_setting("welcome_photo_file_id", file_id)
        await db.set_setting("welcome_photo_caption", caption.strip())
        await db.log_event("welcome_photo_set", {"by": request["user_id"]})
        return _js_redirect("/bot-messages" + _flash(request, "✅ ওয়েলকাম ছবি সেট হয়েছে!", "ok"))
    except Exception as exc:
        log.exception("welcome photo upload failed")
        return _js_redirect("/bot-messages" + _flash(request, f"ত্রুটি: {exc}", "err"))


@require_admin
async def bot_messages_welcome_photo_delete(request: web.Request) -> web.Response:
    await db.set_setting("welcome_photo_file_id", "")
    await db.set_setting("welcome_photo_caption", "")
    await db.log_event("welcome_photo_remove", {"by": request["user_id"]})
    return _js_redirect("/bot-messages" + _flash(request, "ওয়েলকাম ছবি সরানো হয়েছে।", "ok"))


@require_admin
async def bot_messages_save(request: web.Request) -> web.Response:
    data = await request.post()
    key = (data.get("key") or "").strip()
    text = data.get("text") or ""
    valid_keys = {c["key"] for c in BOT_MESSAGES_CONFIG}
    if key not in valid_keys:
        return _js_redirect("/bot-messages" + _flash(request, "অবৈধ মেসেজ কী।", "err"))
    await db.set_bot_message(key, text)
    await db.log_event("bot_msg_edit", {"key": key, "by": request["user_id"]})
    return _js_redirect("/bot-messages" + _flash(request, f"✅ '{key}' মেসেজ সংরক্ষিত হয়েছে।", "ok"))


@require_admin
async def bot_messages_reset(request: web.Request) -> web.Response:
    data = await request.post()
    key = (data.get("key") or "").strip()
    valid = {c["key"]: c["default"] for c in BOT_MESSAGES_CONFIG}
    if key not in valid:
        return _js_redirect("/bot-messages" + _flash(request, "অবৈধ মেসেজ কী।", "err"))
    await db.set_bot_message(key, valid[key])
    return _js_redirect("/bot-messages" + _flash(request, f"✅ '{key}' ডিফল্টে ফেরানো হয়েছে।", "ok"))


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
    if ch_type not in ("channel", "group", "backup", "verify"):
        ch_type = "channel"

    try:
        chat_id = int(chat_raw)
    except ValueError:
        return _js_redirect("/channels" + _flash(request, "Chat ID অবশ্যই সংখ্যা হতে হবে।", "err"))

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

    if ch_type == "verify" and not (invite or username):
        return _js_redirect(
            "/channels"
            + _flash(
                request,
                "ভেরিফিকেশন চ্যানেলের জন্য একটি পাবলিক ইউজারনেম বা ইনভাইট লিংক দিতে হবে।",
                "err",
            )
        )

    await db.add_channel(chat_id, title=title, username=username, invite_url=invite, type=ch_type)
    if ch_type == "verify":
        await db.add_force_join(
            chat_id, title=title, username=username, invite_url=invite
        )
    await db.log_event("channel_add", {"chat_id": chat_id, "type": ch_type, "by": request["user_id"]})
    return _js_redirect("/channels" + _flash(request, f"✅ চ্যানেল/গ্রুপ যোগ হয়েছে: {title or chat_id}", "ok"))


@require_admin
async def channels_del(request: web.Request) -> web.Response:
    data = await request.post()
    try:
        chat_id = int(data.get("chat_id"))
    except (TypeError, ValueError):
        return _js_redirect("/channels")
    channel = await db.fetch_one("SELECT type FROM channels WHERE chat_id=?", (chat_id,))
    await db.remove_channel(chat_id)
    if channel and channel["type"] == "verify":
        await db.remove_force_join(chat_id)
    await db.log_event("channel_delete", {"chat_id": chat_id, "by": request["user_id"]})
    return _js_redirect("/channels" + _flash(request, "চ্যানেল/গ্রুপ সরানো হয়েছে।", "ok"))


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
        return _js_redirect("/users" + _flash(request, "ভুল ইউজার আইডি।", "err"))

    action = (data.get("action") or "ban").strip()
    if action == "unban":
        await db.unban(target_uid)
        await db.log_event("user_unban", {"uid": target_uid, "by": request["user_id"]})
        return _js_redirect("/users" + _flash(request, f"✅ ইউজার #{target_uid}-এর ব্যান তুলে নেওয়া হয়েছে।", "ok"))

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

    return _js_redirect("/users" + _flash(request, msg, "ok"))


@require_admin
async def user_vip_toggle(request: web.Request) -> web.Response:
    data = await request.post()
    try:
        target_uid = int(data.get("user_id"))
    except (TypeError, ValueError):
        return _js_redirect("/users" + _flash(request, "ভুল ইউজার আইডি।", "err"))
    action = (data.get("action") or "").strip()
    vip = action == "add"
    await db.set_vip(target_uid, vip)
    await db.log_event("user_vip", {"uid": target_uid, "vip": vip, "by": request["user_id"]})
    msg = f"⭐ ইউজার #{target_uid}-কে VIP করা হয়েছে।" if vip else f"ইউজার #{target_uid}-এর VIP সরানো হয়েছে।"
    return _js_redirect("/users" + _flash(request, msg, "ok"))


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
        return _js_redirect("/users" + _flash(request, "ভুল ইউজার আইডি।", "err"))

    text = text.strip()
    if not text and not photo_bytes:
        return _js_redirect("/users" + _flash(request, "মেসেজ বা ছবি দিতে হবে।", "err"))

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
        return _js_redirect("/users" + _flash(request, f"✅ ইউজার #{target_uid}-কে মেসেজ পাঠানো হয়েছে।", "ok"))
    except web.HTTPFound:
        raise
    except TelegramForbiddenError:
        return _js_redirect("/users" + _flash(request, "ইউজার বটকে ব্লক করেছে — মেসেজ পাঠানো যায়নি।", "err"))
    except Exception as exc:
        return _js_redirect("/users" + _flash(request, f"ত্রুটি: {exc}", "err"))


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
        "🔧 <b>রক্ষণাবেক্ষণ শুরু হয়েছে</b>\n"
        f"⏳ সময়: {duration_mins} মিনিট\n"
        "একটু অপেক্ষা করুন। 🙏"
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
            msg = await bot.send_message(uid, text, reply_markup=kb)
            asyncio.create_task(schedule_delete(bot, uid, msg.message_id, MSG_TTL))
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
        return _js_redirect("/settings" + _flash(request, "✅ মেইনটেনেন্স মোড বন্ধ করা হয়েছে।", "ok"))

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

    return _js_redirect("/settings" + _flash(request, f"🔧 মেইনটেনেন্স মোড চালু — {duration_mins} মিনিটের জন্য। সব ইউজারকে জানানো হচ্ছে।", "warn"))


# ──────────────────────────────────────────────────────────────── #
# Settings
# ──────────────────────────────────────────────────────────────── #
@require_admin
async def settings_page(request: web.Request) -> web.Response:
    import time as _time
    (
        fj, bans, verified_count, channel_count,
        maintenance_on, maintenance_until, disc_url,
        ad_enabled_val, ad_wait_secs_val, bot_username_val,
    ) = await asyncio.gather(
        db.list_force_join(),
        db.list_bans(),
        db.fetch_value("SELECT COUNT(*) FROM users WHERE verified = 1"),
        db.fetch_value("SELECT COUNT(*) FROM channels"),
        db.is_maintenance(),
        db.get_maintenance_until(),
        db.get_discussion_url(),
        db.get_setting("ad_enabled", "0"),
        db.get_setting("ad_wait_seconds", "10"),
        db.get_setting("bot_username", "Moviex_hub_bot"),
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
            "ad_enabled": (ad_enabled_val == "1"),
            "ad_wait_seconds": int(ad_wait_secs_val or 10),
            "bot_username_setting": bot_username_val or "Moviex_hub_bot",
            "public_url": settings.public_url,
        },
    )


@require_admin
async def settings_discussion_save(request: web.Request) -> web.Response:
    data = await request.post()
    url = (data.get("url") or "").strip()
    await db.set_discussion_url(url)
    await db.log_event("discussion_url_update", {"url": url, "by": request["user_id"]})
    return _js_redirect("/settings" + _flash(request, "✅ ডিসকাশন গ্রুপ লিংক সেভ হয়েছে।", "ok"))


@require_admin
async def settings_reset_verifications(request: web.Request) -> web.Response:
    await db.reset_all_verifications()
    await db.log_event("verifications_reset", {"by": request["user_id"]})
    return _js_redirect("/settings" + _flash(request, "সব ইউজারের ভেরিফিকেশন রিসেট হয়েছে।", "ok"))


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
        return _js_redirect("/settings" + _flash(request, "Chat id অবশ্যই সংখ্যা হতে হবে।", "err"))
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
    if not (invite or username):
        return _js_redirect(
            "/settings"
            + _flash(
                request,
                "ভেরিফিকেশন চ্যানেলের জন্য একটি পাবলিক ইউজারনেম বা ইনভাইট লিংক দিতে হবে।",
                "err",
            )
        )
    await db.add_force_join(chat_id, title=title, username=username, invite_url=invite)
    await db.log_event("force_join_add", {"chat_id": chat_id, "by": request["user_id"]})
    return _js_redirect("/settings" + _flash(request, "✅ ভেরিফিকেশন চ্যানেল যোগ হয়েছে।", "ok"))


@require_admin
async def settings_fj_del(request: web.Request) -> web.Response:
    data = await request.post()
    try:
        chat_id = int(data.get("chat_id"))
    except (TypeError, ValueError):
        return _js_redirect("/settings")
    await db.remove_force_join(chat_id)
    await db.execute(
        "UPDATE channels SET type='channel' WHERE chat_id=? AND type='verify'",
        (chat_id,),
    )
    await db.log_event("force_join_delete", {"chat_id": chat_id, "by": request["user_id"]})
    return _js_redirect("/settings" + _flash(request, "সরানো হয়েছে।", "ok"))


@require_admin
async def settings_ban(request: web.Request) -> web.Response:
    data = await request.post()
    action = data.get("action")
    try:
        uid = int(data.get("user_id"))
    except (TypeError, ValueError):
        return _js_redirect("/settings")
    if action == "ban":
        await db.ban(uid, reason=(data.get("reason") or "").strip())
    elif action == "unban":
        await db.unban(uid)
    return _js_redirect("/settings" + _flash(request, "আপডেট হয়েছে।", "ok"))


# ──────────────────────────────────────────────────────────────── #
# Ad Page (public — no auth)
# ──────────────────────────────────────────────────────────────── #
import math as _math

async def ad_page(request: web.Request) -> web.Response:
    token = request.match_info["token"]
    row = await db.get_ad_token(token)

    brand_name, bot_username = await _bot_identity(request)
    stored_wait = row["wait_seconds"] if row and "wait_seconds" in row else None
    wait_secs = max(
        5,
        min(
            300,
             int(stored_wait or await db.get_setting("ad_wait_seconds", "10") or 10),
        ),
    )
    circumference = round(2 * _math.pi * 56, 2)

    if not row:
        return aiohttp_jinja2.render_template(
            "ad.html", request,
            {"token_valid": False, "used": False, "expired": False,
             "title": "", "bot_username": bot_username,
             "brand_name": brand_name,
             "wait_secs": wait_secs, "circumference": circumference, "tg_link": "",
             "complete_url": f"/ad/{token}/complete", "token": token},
        )

    import time as _time
    if row["used"]:
        return aiohttp_jinja2.render_template(
            "ad.html", request,
            {"token_valid": True, "used": True, "expired": False,
             "title": "", "bot_username": bot_username,
             "brand_name": brand_name,
             "wait_secs": wait_secs, "circumference": circumference, "tg_link": "",
             "complete_url": f"/ad/{token}/complete", "token": token},
        )

    if _time.time() > row["expires_at"]:
        return aiohttp_jinja2.render_template(
            "ad.html", request,
            {"token_valid": True, "used": False, "expired": True,
             "title": "", "bot_username": bot_username,
             "brand_name": brand_name,
             "wait_secs": wait_secs, "circumference": circumference, "tg_link": "",
             "complete_url": f"/ad/{token}/complete", "token": token},
        )

    movie = await db.get_movie(row["movie_id"])
    title = movie["title"] if movie else "মুভি"
    tg_link = f"https://t.me/{bot_username}?start=get_{token}"

    return aiohttp_jinja2.render_template(
        "ad.html", request,
        {"token_valid": True, "used": False, "expired": False,
         "title": title, "bot_username": bot_username,
         "brand_name": brand_name,
         "wait_secs": wait_secs, "circumference": circumference, "tg_link": tg_link,
         "complete_url": f"/ad/{token}/complete", "token": token},
    )


@require_admin
async def settings_ad_save(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    data = await request.post()
    prev_enabled = await db.get_setting("ad_enabled", "0")
    ad_enabled = "1" if data.get("ad_enabled") else "0"
    try:
        wait_secs = max(5, min(300, int(data.get("ad_wait_seconds") or 10)))
    except (ValueError, TypeError):
        wait_secs = 10
    bot_username = (data.get("bot_username") or "Moviex_hub_bot").strip().lstrip("@")
    await db.set_setting("ad_enabled", ad_enabled)
    await db.set_setting("ad_wait_seconds", str(wait_secs))
    await db.set_setting("bot_username", bot_username)

    if prev_enabled != "1" and ad_enabled == "1":
        asyncio.create_task(_broadcast_ad_enabled(bot, wait_secs))

    return _js_redirect("/settings" + _flash(request, "বিজ্ঞাপন সেটিংস সেভ হয়েছে! ✅", "ok"))


async def _broadcast_ad_enabled(bot: Any, wait_secs: int) -> None:
    """অ্যাড সিস্টেম চালু হলে সব ইউজারকে নোটিফিকেশন পাঠাও (background)."""
    user_ids = await db.all_user_ids()
    text = (
        "📢 <b>নতুন নিয়ম</b>\n"
        f"ফাইলের আগে {wait_secs} সেকেন্ডের বিজ্ঞাপন দেখুন।\n"
        "⏳ শেষে ফাইল নিজেই চলে আসবে।"
    )
    sent = 0
    for uid in user_ids:
        try:
            msg = await bot.send_message(uid, text, parse_mode="HTML")
            asyncio.create_task(schedule_delete(bot, uid, msg.message_id, MSG_TTL))
            sent += 1
            if sent % 25 == 0:
                await asyncio.sleep(1)
        except Exception:
            pass
    log.info("Ad-enabled broadcast done: %d/%d users notified", sent, len(user_ids))


# ──────────────────────────────────────────────────────────────── #
# Route wiring
# ──────────────────────────────────────────────────────────────── #
def setup_routes(app: web.Application) -> None:
    app.router.add_get("/healthz", health)
    # Friendly alias for uptime monitors; both endpoints are public and
    # intentionally avoid the admin-session middleware's access check.
    app.router.add_get("/uptime", health)
    app.router.add_get("/miniapp", miniapp_page)
    app.router.add_get("/miniapp/", miniapp_page)
    app.router.add_get("/api/miniapp/config", miniapp_config)
    app.router.add_get("/api/miniapp/movies", miniapp_movies)
    app.router.add_get("/api/miniapp/posters/{movie_id}", miniapp_poster)
    app.router.add_get("/api/miniapp/movies/{movie_id}", miniapp_movie_detail)
    app.router.add_get("/api/miniapp/movies/{movie_id}/comments", miniapp_comments)
    app.router.add_post("/api/miniapp/movies/{movie_id}/comments", miniapp_add_comment)
    app.router.add_get("/api/miniapp/upcoming", miniapp_upcoming)
    app.router.add_get("/api/miniapp/profile", miniapp_profile)
    app.router.add_get("/api/miniapp/favorites", miniapp_profile)
    app.router.add_post("/api/miniapp/maya", miniapp_maya)
    app.router.add_post("/api/miniapp/rating", miniapp_rate_movie)
    app.router.add_post("/api/miniapp/favorite", miniapp_toggle_favorite)
    app.router.add_post("/api/miniapp/claim", miniapp_claim)
    app.router.add_post("/ad/{token}/complete", miniapp_complete_ad)
    app.router.add_get("/ad/{token}/complete", miniapp_complete_ad)
    app.router.add_get("/login", login_get)
    app.router.add_post("/login", login_post)
    app.router.add_post("/logout", logout)

    app.router.add_get("/", dashboard)

    app.router.add_get("/movies", movies_list)
    app.router.add_post("/movies/delete", movie_delete)
    app.router.add_get("/movies/edit", movie_edit_form)
    app.router.add_post("/movies/edit", movie_edit_save)
    app.router.add_get("/upcoming", upcoming_page)
    app.router.add_post("/upcoming/add", upcoming_add)
    app.router.add_post("/upcoming/delete", upcoming_delete)
    app.router.add_post("/upcoming/post", upcoming_post)

    app.router.add_get("/post", post_form)
    app.router.add_post("/post", post_submit)

    app.router.add_get("/broadcast", broadcast_form)
    app.router.add_post("/broadcast", broadcast_submit)
    app.router.add_get("/broadcast/status", broadcast_status)

    # Movie requests
    app.router.add_get("/requests", requests_page)
    app.router.add_post("/requests/resolve", requests_resolve)
    app.router.add_post("/requests/delete", requests_delete)
    app.router.add_post("/requests/dm", requests_dm)

    # Bot messages editor
    app.router.add_get("/bot-messages", bot_messages_page)
    app.router.add_post("/bot-messages/save", bot_messages_save)
    app.router.add_post("/bot-messages/reset", bot_messages_reset)
    app.router.add_post("/bot-messages/welcome-photo", bot_messages_welcome_photo)
    app.router.add_post("/bot-messages/welcome-photo/delete", bot_messages_welcome_photo_delete)

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
    app.router.add_post("/users/vip", user_vip_toggle)

    # Statistics
    app.router.add_get("/stats", stats_page)

    # Settings
    app.router.add_get("/settings", settings_page)
    app.router.add_post("/settings/fj/add", settings_fj_add)
    app.router.add_post("/settings/fj/del", settings_fj_del)
    app.router.add_post("/settings/ban", settings_ban)
    app.router.add_post("/settings/verify/reset", settings_reset_verifications)
    app.router.add_post("/settings/ad", settings_ad_save)

    # Ad page — public, no auth
    app.router.add_get("/ad/{token}", ad_page)

    if _STATIC_DIR.exists():
        app.router.add_static("/static/", _STATIC_DIR, show_index=False)
    if (_MINIAPP_DIR / "assets").exists():
        app.router.add_static("/miniapp/assets/", _MINIAPP_DIR / "assets", show_index=False)
