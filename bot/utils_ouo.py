"""অ্যাড শর্টলিংক ইন্টিগ্রেশন — অ্যাড লিংক থেকে ইনকাম করার জন্য।

Primary: ShrinkMe.io — API server থেকে কল করলে সাধারণত কাজ করে।
Fallback: Ouo.io — Cloudflare bot-protection এর কারণে server-to-server কল
প্রায়ই ব্লক হয়ে যায়, তাই শুধু ব্যাকআপ হিসেবে রাখা হলো।
কোনো শর্টনার কাজ না করলে original লিংক ফিরিয়ে দেয় — ফাইল ডেলিভারি কখনো
ব্লক হবে না।
"""
from __future__ import annotations

import logging

import aiohttp

from .config import settings as cfg

log = logging.getLogger(__name__)

_SHRINKME_API = "https://shrinkme.io/api"
_OUO_API_BASE = "https://ouo.io/api/{key}"


async def _shrinkme(long_url: str) -> str | None:
    api_key = cfg.shrinkme_api_key
    if not api_key:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _SHRINKME_API,
                params={"api": api_key, "url": long_url},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning("shrinkme.io API non-200 status: %s", resp.status)
                    return None
                data = await resp.json(content_type=None)
                if data.get("status") == "success" and data.get("shortenedUrl"):
                    return data["shortenedUrl"]
                log.warning("shrinkme.io API unexpected response: %r", data)
                return None
    except Exception:
        log.exception("shrinkme.io shorten failed")
        return None


async def _ouo(long_url: str) -> str | None:
    api_key = cfg.ouo_api_key
    if not api_key:
        return None
    url = _OUO_API_BASE.format(key=api_key)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params={"s": long_url}, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    log.warning("ouo.io API non-200 status: %s", resp.status)
                    return None
                short = (await resp.text()).strip()
                if short.startswith("http"):
                    return short
                log.warning("ouo.io API unexpected response: %r", short[:200])
                return None
    except Exception:
        log.exception("ouo.io shorten failed")
        return None


async def shorten_url(long_url: str) -> str:
    """ShrinkMe.io দিয়ে চেষ্টা করে, ব্যর্থ হলে Ouo.io, তারপরও ব্যর্থ হলে original লিংক।"""
    for fn in (_shrinkme, _ouo):
        short = await fn(long_url)
        if short:
            return short
    return long_url
