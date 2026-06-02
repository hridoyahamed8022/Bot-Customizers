"""Wire up every router the bot uses."""
from __future__ import annotations

from aiogram import Dispatcher

from . import admin_dm, ai_chat, callbacks, indexer, request_movie, search, start


def register_routers(dp: Dispatcher) -> None:
    dp.include_router(start.router)
    dp.include_router(admin_dm.router)
    dp.include_router(ai_chat.router)   # AI chat FSM — before callbacks
    dp.include_router(callbacks.router)
    dp.include_router(request_movie.router)
    dp.include_router(indexer.router)
    dp.include_router(search.router)  # text search must come last
