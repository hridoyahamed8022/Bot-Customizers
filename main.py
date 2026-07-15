"""Entry point for the Telegram Movie Bot.

Runs the Aiogram polling loop and the aiohttp admin web panel side by side
in a single asyncio event loop. A self-ping task keeps the process alive on
Replit (prevents the free-tier idle timeout).
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

import aiohttp
from aiohttp import web

from bot.bot_app import build_bot, build_dispatcher
from bot.config import settings
from bot.db import db
from bot.web.app import build_web_app

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("main")


async def _run_polling(bot, dp) -> None:
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        log.exception("delete_webhook failed (continuing)")
    log.info("Telegram polling started.")
    await dp.start_polling(bot, handle_signals=False, allowed_updates=dp.resolve_used_update_types())


async def _run_web(app) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=settings.web_host, port=settings.port)
    await site.start()
    log.info("Web admin panel listening on http://%s:%s", settings.web_host, settings.port)
    return runner


async def _keep_alive() -> None:
    """Ping our own /healthz every 4 minutes to prevent Replit idle timeout."""
    await asyncio.sleep(30)
    url = f"http://127.0.0.1:{settings.port}/healthz"
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    log.debug("keep-alive ping: %s", r.status)
        except Exception as e:
            log.debug("keep-alive ping failed: %s", e)
        await asyncio.sleep(4 * 60)


async def _expire_bans_loop(bot) -> None:
    """Every 5 minutes delete expired bans and notify those users."""
    import time as _t
    await asyncio.sleep(60)
    while True:
        try:
            rows = await db.fetch_all(
                "SELECT user_id FROM bans WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (_t.time(),),
            )
            removed = await db.expire_old_bans()
            if removed:
                log.info("Expired %d ban(s) removed.", removed)
                for row in rows:
                    try:
                        await bot.send_message(
                            row["user_id"],
                            "✅ আপনার ব্যান মেয়াদ শেষ হয়েছে। এখন আবার বট ব্যবহার করতে পারবেন।",
                        )
                    except Exception:
                        pass
        except Exception:
            log.exception("_expire_bans_loop error")
        await asyncio.sleep(5 * 60)


async def _startup_broadcast(bot) -> None:
    """বট চালু/অন হলে সব ইউজারকে অটো নোটিফিকেশন পাঠাও।"""
    from aiogram.enums import ParseMode
    import time as _t
    await asyncio.sleep(5)
    try:
        last_ts = await db.get_setting("last_startup_broadcast_ts", 0)
        now = _t.time()
        if now - float(last_ts or 0) < 300:
            log.info("Skipping startup broadcast (sent recently).")
            return
        await db.set_setting("last_startup_broadcast_ts", now)
        from bot.utils import schedule_delete, MSG_TTL
        user_ids = await db.all_user_ids()
        text = (
            "✅  <b>বট আবার চালু হয়েছে!</b>\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
            "🎬  বট এখন সম্পূর্ণ সচল আছে।\n"
            "🔍  মুভি/ড্রামার নাম ইংরেজিতে লিখে সার্চ করুন — সাথে সাথে ফাইল পেয়ে যাবেন!"
        )
        sent = 0
        for uid in user_ids:
            try:
                msg = await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
                asyncio.create_task(schedule_delete(bot, uid, msg.message_id, MSG_TTL))
                sent += 1
                if sent % 25 == 0:
                    await asyncio.sleep(1)
            except Exception:
                pass
        log.info("Startup broadcast done: %d/%d users notified.", sent, len(user_ids))
    except Exception:
        log.exception("_startup_broadcast error")


async def _maintenance_end_notifier(bot) -> None:
    """মেইনটেন্যান্স সময় শেষ হলে সব ইউজারকে notify করো।"""
    from aiogram.enums import ParseMode
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    import time as _t
    await asyncio.sleep(30)
    while True:
        try:
            is_on = await db.is_maintenance()
            if is_on:
                until = await db.get_maintenance_until()
                now = _t.time()
                if until > 0 and now >= until:
                    # মেইনটেন্যান্স শেষ — DB বন্ধ করো
                    await db.set_maintenance_timed(False)
                    log.info("Maintenance auto-expired. Broadcasting end notification.")

                    disc_url = await db.get_discussion_url()
                    text = (
                        "✅  <b>বট আবার চালু হয়েছে!</b>\n"
                        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
                        "🎬  রক্ষণাবেক্ষণ সফলভাবে সম্পন্ন হয়েছে।\n"
                        "এখন আবার মুভি সার্চ করতে পারবেন! 😊\n\n"
                        "🔍  নামটি ইংরেজিতে লিখুন — ফাইল পেয়ে যাবেন।"
                    )
                    kb = None
                    if disc_url:
                        kb = InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="💬  আমাদের গ্রুপ", url=disc_url)
                        ]])

                    from bot.utils import schedule_delete, MSG_TTL
                    user_ids = await db.all_user_ids()
                    sent = 0
                    for uid in user_ids:
                        try:
                            msg = await bot.send_message(uid, text, reply_markup=kb, parse_mode=ParseMode.HTML)
                            asyncio.create_task(schedule_delete(bot, uid, msg.message_id, MSG_TTL))
                            sent += 1
                            if sent % 25 == 0:
                                await asyncio.sleep(1)
                        except Exception:
                            pass
                    log.info("Maintenance-end broadcast done: %d/%d users.", sent, len(user_ids))
        except Exception:
            log.exception("_maintenance_end_notifier error")
        await asyncio.sleep(30)


async def main() -> None:
    settings.validate()
    await db.init()

    bot = build_bot()
    dp = build_dispatcher(bot)
    app = build_web_app(bot)

    runner = await _run_web(app)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    polling_task = asyncio.create_task(_run_polling(bot, dp))
    keep_alive_task = asyncio.create_task(_keep_alive())
    expire_bans_task = asyncio.create_task(_expire_bans_loop(bot))
    maint_notifier_task = asyncio.create_task(_maintenance_end_notifier(bot))
    startup_broadcast_task = asyncio.create_task(_startup_broadcast(bot))
    stop_task = asyncio.create_task(stop_event.wait())

    try:
        done, pending = await asyncio.wait(
            {polling_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        keep_alive_task.cancel()
        expire_bans_task.cancel()
        maint_notifier_task.cancel()
        startup_broadcast_task.cancel()
        log.info("Shutting down…")
        try:
            await dp.stop_polling()
        except Exception:
            pass
        try:
            await bot.session.close()
        except Exception:
            pass
        try:
            await runner.cleanup()
        except Exception:
            pass
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
        sys.exit(0)
    except Exception:
        log.exception("Fatal error in main loop")
        sys.exit(1)
