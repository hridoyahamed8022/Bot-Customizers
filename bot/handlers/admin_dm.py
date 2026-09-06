"""অ্যাডমিন-ওনলি কমান্ড — শুধু পরিচিতিমূলক, লগইন এখন ওয়েব প্যানেলে।"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..config import settings

router = Router(name="admin_dm")
log = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.message(Command("panel"))
async def cmd_panel(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ এই কমান্ড শুধুমাত্র অ্যাডমিনদের জন্য।")
        return
    url = settings.public_url or "http://localhost:5000"
    await message.answer(
        "🛠 <b>অ্যাডমিন প্যানেল</b>\n"
        "নিচের লিংকে লগইন করুন:\n\n"
        f"<a href='{url}/login'>{url}/login</a>"
    )


@router.message(Command("login"))
async def cmd_login(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ এই কমান্ড শুধুমাত্র অ্যাডমিনদের জন্য।")
        return
    url = settings.public_url or "http://localhost:5000"
    await message.answer(
        "🔑 <b>অ্যাডমিন লগইন</b>\n"
        "নিচের লিংকে ইউজারনেম-পাসওয়ার্ড দিন:\n\n"
        f"<a href='{url}/login'>{url}/login</a>"
    )
