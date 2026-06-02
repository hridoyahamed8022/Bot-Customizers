"""Bot + Dispatcher factory with global error handling and middleware wiring."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, ErrorEvent

from .config import settings
from .handlers import register_routers
from .middlewares.ban import BanMiddleware
from .middlewares.maintenance import MaintenanceMiddleware
from .middlewares.throttle import ThrottlingMiddleware

log = logging.getLogger(__name__)


def build_bot() -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )


def build_dispatcher(bot: Bot) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    # Middleware order: maintenance → ban → throttle
    maintenance_mw = MaintenanceMiddleware()
    ban_mw = BanMiddleware()
    throttle_mw = ThrottlingMiddleware(rate_limit=settings.rate_limit_seconds)

    dp.message.middleware(maintenance_mw)
    dp.callback_query.middleware(maintenance_mw)

    dp.message.middleware(ban_mw)
    dp.callback_query.middleware(ban_mw)
    dp.inline_query.middleware(ban_mw)

    dp.message.middleware(throttle_mw)
    dp.callback_query.middleware(throttle_mw)

    register_routers(dp)

    @dp.error()
    async def on_error(event: ErrorEvent) -> bool:
        log.exception(
            "Unhandled error while processing update %s: %s",
            getattr(event.update, "update_id", "?"),
            event.exception,
        )
        return True

    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)
    return dp


async def _on_startup(bot: Bot) -> None:
    me = await bot.get_me()
    log.info("Bot ready: @%s (%s)", me.username, me.id)
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="শুরু করুন / মেনু"),
                BotCommand(command="help", description="সাহায্য"),
                BotCommand(command="search", description="মুভি সার্চ করুন"),
                BotCommand(command="request", description="মুভি রিকোয়েস্ট করুন"),
                BotCommand(command="cancel", description="বাতিল করুন"),
            ],
            scope=BotCommandScopeAllPrivateChats(),
        )
    except Exception:
        log.exception("set_my_commands failed (continuing)")


async def _on_shutdown(bot: Bot) -> None:
    log.info("Bot shutdown.")
