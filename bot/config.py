"""Centralised, validated configuration loaded from environment variables."""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from typing import List, Optional

try:  # python-dotenv is optional but loaded if present.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

log = logging.getLogger(__name__)


def _csv_ints(value: str) -> List[int]:
    out: List[int] = []
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            log.warning("Ignoring non-integer admin id: %r", part)
    return out


def _public_url() -> str:
    """Best-guess public HTTPS URL of this Replit, used for login links."""
    domains = (os.getenv("REPLIT_DOMAINS") or "").split(",")
    domains = [d.strip() for d in domains if d.strip()]
    if domains:
        return f"https://{domains[0]}"
    dev = os.getenv("REPLIT_DEV_DOMAIN")
    if dev:
        return f"https://{dev}"
    return ""


@dataclass
class Settings:
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", "").strip())
    admin_ids: List[int] = field(
        default_factory=lambda: _csv_ints(os.getenv("ADMIN_IDS", ""))
    )
    request_group_id: Optional[int] = field(
        default_factory=lambda: int(os.getenv("REQUEST_GROUP_ID"))
        if (os.getenv("REQUEST_GROUP_ID") or "").lstrip("-").isdigit()
        else None
    )
    backup_channel_id: Optional[int] = field(
        default_factory=lambda: int(os.getenv("BACKUP_CHANNEL_ID"))
        if (os.getenv("BACKUP_CHANNEL_ID") or "").lstrip("-").isdigit()
        else None
    )
    session_secret: str = field(
        default_factory=lambda: os.getenv("SESSION_SECRET")
        or secrets.token_urlsafe(32)
    )
    admin_username: str = field(
        default_factory=lambda: (os.getenv("ADMIN_USERNAME") or "").strip()
    )
    admin_password: str = field(
        default_factory=lambda: os.getenv("ADMIN_PASSWORD") or ""
    )
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "5000")))
    web_host: str = field(default_factory=lambda: os.getenv("WEB_HOST", "0.0.0.0"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/bot.sqlite3"))
    public_url: str = field(default_factory=_public_url)
    ouo_api_key: str = field(default_factory=lambda: (os.getenv("OUO_API_KEY") or "").strip())
    shrinkme_api_key: str = field(default_factory=lambda: (os.getenv("SHRINKME_API_KEY") or "").strip())
    admin_chat_id: Optional[int] = field(
        default_factory=lambda: int(os.getenv("ADMIN_CHAT_ID"))
        if (os.getenv("ADMIN_CHAT_ID") or "").lstrip("-").isdigit()
        else None
    )

    # Anti-spam tunables
    rate_limit_seconds: float = field(
        default_factory=lambda: float(os.getenv("RATE_LIMIT_SECONDS", "0.6"))
    )

    def validate(self) -> None:
        if not self.bot_token:
            raise RuntimeError(
                "BOT_TOKEN is missing — set it as a Replit secret before starting."
            )
        if not self.admin_ids:
            log.warning("ADMIN_IDS is empty — no admin DM features will work.")
        if not (self.admin_username and self.admin_password):
            log.warning(
                "ADMIN_USERNAME / ADMIN_PASSWORD missing — web panel login disabled."
            )
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)


settings = Settings()
