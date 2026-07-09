"""Ouo.io শর্টলিংক ইন্টিগ্রেশন — অ্যাড লিংক থেকে ইনকাম করার জন্য।"""
from __future__ import annotations

import logging

import aiohttp

from .config import settings as cfg

log = logging.getLogger(__name__)

_API_BASE = "https://ouo.io/api/{key}"


async def shorten_url(long_url: str) -> str:
    """Ouo.io API দিয়ে লিংক শর্ট করে মোনেটাইজড শর্টলিংক রিটার্ন করে।

    API key না থাকলে বা কল ফেইল করলে original লিংক ফিরিয়ে দেয় (ফাইল ডেলিভারি
    কখনো ব্লক হবে না)।
    """
    api_key = cfg.ouo_api_key
    if not api_key:
        return long_url

    url = _API_BASE.format(key=api_key)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params={"s": long_url}, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    log.warning("ouo.io API non-200 status: %s", resp.status)
                    return long_url
                short = (await resp.text()).strip()
                if short.startswith("http"):
                    return short
                log.warning("ouo.io API unexpected response: %r", short[:200])
                return long_url
    except Exception:
        log.exception("ouo.io shorten_url failed; falling back to direct link")
        return long_url
