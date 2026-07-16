"""Async SQLite wrapper backing the bot.

Schema:
    movies          — indexed media
    movies_fts      — FTS5 virtual table
    users           — every user that ever interacted with the bot
    bans            — user_id of banned users
    web_sessions    — active web sessions
    force_join      — channels users must join before using the bot
    movie_requests  — missing movie requests from users
    settings        — k/v settings store
    logs            — append-only event log
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, Iterable, List, Optional

import aiosqlite

from .config import settings

log = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    file_unique_id    TEXT UNIQUE NOT NULL,
    file_id           TEXT NOT NULL,
    file_type         TEXT NOT NULL,
    file_name         TEXT,
    mime_type         TEXT,
    size_bytes        INTEGER DEFAULT 0,
    duration          INTEGER DEFAULT 0,
    title             TEXT NOT NULL,
    caption           TEXT,
    source_chat_id    INTEGER,
    source_message_id INTEGER,
    added_at          REAL NOT NULL,
    hits              INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS movies_added_at ON movies(added_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS movies_fts USING fts5(
    title, caption, file_name,
    content='movies', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS movies_ai AFTER INSERT ON movies BEGIN
  INSERT INTO movies_fts(rowid, title, caption, file_name)
  VALUES (new.id, new.title, COALESCE(new.caption,''), COALESCE(new.file_name,''));
END;

CREATE TRIGGER IF NOT EXISTS movies_ad AFTER DELETE ON movies BEGIN
  INSERT INTO movies_fts(movies_fts, rowid, title, caption, file_name)
  VALUES('delete', old.id, old.title, COALESCE(old.caption,''), COALESCE(old.file_name,''));
END;

CREATE TRIGGER IF NOT EXISTS movies_au AFTER UPDATE ON movies BEGIN
  INSERT INTO movies_fts(movies_fts, rowid, title, caption, file_name)
  VALUES('delete', old.id, old.title, COALESCE(old.caption,''), COALESCE(old.file_name,''));
  INSERT INTO movies_fts(rowid, title, caption, file_name)
  VALUES (new.id, new.title, COALESCE(new.caption,''), COALESCE(new.file_name,''));
END;

CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    first_name   TEXT,
    last_name    TEXT,
    language     TEXT,
    joined_at    REAL NOT NULL,
    last_seen    REAL NOT NULL,
    is_blocked   INTEGER NOT NULL DEFAULT 0,
    verified     INTEGER NOT NULL DEFAULT 0,
    verified_at  REAL
);
CREATE INDEX IF NOT EXISTS users_last_seen ON users(last_seen DESC);

CREATE TABLE IF NOT EXISTS bans (
    user_id   INTEGER PRIMARY KEY,
    reason    TEXT,
    at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS web_sessions (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    used_at     REAL,
    is_login    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS web_sessions_user ON web_sessions(user_id);

CREATE TABLE IF NOT EXISTS force_join (
    chat_id     INTEGER PRIMARY KEY,
    title       TEXT,
    username    TEXT,
    invite_url  TEXT,
    added_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER UNIQUE NOT NULL,
    title       TEXT,
    username    TEXT,
    invite_url  TEXT,
    type        TEXT NOT NULL DEFAULT 'channel',
    added_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS movie_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    username    TEXT,
    first_name  TEXT,
    title       TEXT NOT NULL,
    note        TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  REAL NOT NULL,
    resolved_at REAL
);
CREATE INDEX IF NOT EXISTS mreq_status ON movie_requests(status, created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE TABLE IF NOT EXISTS logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    payload     TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS logs_created_at ON logs(created_at DESC);

CREATE TABLE IF NOT EXISTS warnings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    reason      TEXT,
    admin_id    INTEGER,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS warnings_user ON warnings(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS favorites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    movie_id    INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    UNIQUE(user_id, movie_id)
);
CREATE INDEX IF NOT EXISTS favorites_user ON favorites(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ratings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    movie_id    INTEGER NOT NULL,
    rating      INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    UNIQUE(user_id, movie_id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id     INTEGER PRIMARY KEY,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS request_votes (
    user_id     INTEGER NOT NULL,
    request_id  INTEGER NOT NULL,
    PRIMARY KEY (user_id, request_id)
);

CREATE TABLE IF NOT EXISTS search_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    query       TEXT NOT NULL,
    results     INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS search_log_created ON search_log(created_at DESC);

CREATE TABLE IF NOT EXISTS ad_tokens (
    token       TEXT PRIMARY KEY,
    movie_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ad_tokens_expires ON ad_tokens(expires_at);

CREATE TABLE IF NOT EXISTS movie_notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    movie_title TEXT NOT NULL,
    request_id  INTEGER,
    created_at  REAL NOT NULL,
    last_sent_at REAL,
    next_send_at REAL NOT NULL,
    fulfilled   INTEGER NOT NULL DEFAULT 0,
    send_count  INTEGER NOT NULL DEFAULT 0,
    max_sends   INTEGER NOT NULL DEFAULT 5
);
CREATE INDEX IF NOT EXISTS movie_notif_next ON movie_notifications(next_send_at, fulfilled);
"""


class Database:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or settings.db_path
        self._lock = asyncio.Lock()
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(_SCHEMA)
        await self._add_column_if_missing("users", "verified", "INTEGER NOT NULL DEFAULT 0")
        await self._add_column_if_missing("users", "verified_at", "REAL")
        await self._add_column_if_missing("users", "is_vip", "INTEGER NOT NULL DEFAULT 0")
        await self._add_column_if_missing("bans", "expires_at", "REAL")
        await self._add_column_if_missing("movie_requests", "vote_count", "INTEGER NOT NULL DEFAULT 0")
        await self._add_column_if_missing("movie_requests", "notified", "INTEGER NOT NULL DEFAULT 0")
        await self._conn.commit()
        log.info("SQLite ready at %s", self.path)

    async def _add_column_if_missing(self, table: str, column: str, decl: str) -> None:
        assert self._conn is not None
        async with self._conn.execute(f"PRAGMA table_info({table})") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if column not in cols:
            try:
                await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            except Exception:
                log.exception("Migration ALTER TABLE %s ADD %s failed", table, column)

    async def close(self) -> None:
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        assert self._conn is not None
        async with self._lock:
            await self._conn.execute(sql, params)
            await self._conn.commit()

    async def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> None:
        assert self._conn is not None
        async with self._lock:
            await self._conn.executemany(sql, seq)
            await self._conn.commit()

    async def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[aiosqlite.Row]:
        assert self._conn is not None
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> List[aiosqlite.Row]:
        assert self._conn is not None
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchall()

    async def fetch_value(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = await self.fetch_one(sql, params)
        if row is None:
            return None
        return row[0]

    # ------------------------------------------------------------------ #
    # users
    # ------------------------------------------------------------------ #
    async def upsert_user(
        self,
        user_id: int,
        *,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        language: Optional[str] = None,
    ) -> bool:
        """Returns True if this is a brand-new user, False if returning."""
        now = time.time()
        assert self._conn is not None
        async with self._lock:
            existing = await self._conn.execute(
                "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
            )
            row = await existing.fetchone()
            is_new = row is None
            await self._conn.execute(
                """
                INSERT INTO users(user_id, username, first_name, last_name, language, joined_at, last_seen)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    language=excluded.language,
                    last_seen=excluded.last_seen
                """,
                (user_id, username, first_name, last_name, language, now, now),
            )
            await self._conn.commit()
        return is_new

    async def all_user_ids(self) -> List[int]:
        rows = await self.fetch_all("SELECT user_id FROM users WHERE is_blocked = 0")
        return [r["user_id"] for r in rows]

    async def expire_old_bans(self) -> int:
        """Delete bans whose expires_at has passed. Returns count deleted."""
        assert self._conn is not None
        now = time.time()
        async with self._lock:
            cur = await self._conn.execute(
                "DELETE FROM bans WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)
            )
            await self._conn.commit()
            return cur.rowcount

    async def list_users(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str = "",
    ) -> List[aiosqlite.Row]:
        now = time.time()
        base_ban = (
            "SELECT b.expires_at FROM bans b "
            "WHERE b.user_id=u.user_id "
            "AND (b.expires_at IS NULL OR b.expires_at > ?)"
        )
        if search:
            q = f"%{search}%"
            return await self.fetch_all(
                f"""SELECT u.*,
                       ({base_ban} LIMIT 1) AS ban_expires,
                       CASE WHEN ({base_ban} LIMIT 1) IS NOT NULL OR
                                 EXISTS(SELECT 1 FROM bans b WHERE b.user_id=u.user_id AND b.expires_at IS NULL)
                            THEN 1 ELSE 0 END AS is_banned
                   FROM users u
                   WHERE u.first_name LIKE ? OR u.username LIKE ? OR CAST(u.user_id AS TEXT) LIKE ?
                   ORDER BY u.last_seen DESC LIMIT ? OFFSET ?""",
                (now, now, q, q, q, limit, offset),
            )
        return await self.fetch_all(
            f"""SELECT u.*,
                   ({base_ban} LIMIT 1) AS ban_expires,
                   CASE WHEN ({base_ban} LIMIT 1) IS NOT NULL OR
                             EXISTS(SELECT 1 FROM bans b WHERE b.user_id=u.user_id AND b.expires_at IS NULL)
                        THEN 1 ELSE 0 END AS is_banned
               FROM users u ORDER BY u.last_seen DESC LIMIT ? OFFSET ?""",
            (now, now, limit, offset),
        )

    async def count_users(self, search: str = "") -> int:
        if search:
            q = f"%{search}%"
            val = await self.fetch_value(
                "SELECT COUNT(*) FROM users WHERE first_name LIKE ? OR username LIKE ? OR CAST(user_id AS TEXT) LIKE ?",
                (q, q, q),
            )
        else:
            val = await self.fetch_value("SELECT COUNT(*) FROM users")
        return int(val or 0)

    async def mark_blocked(self, user_id: int, blocked: bool = True) -> None:
        await self.execute(
            "UPDATE users SET is_blocked = ? WHERE user_id = ?",
            (1 if blocked else 0, user_id),
        )

    async def is_verified(self, user_id: int) -> bool:
        row = await self.fetch_one("SELECT verified FROM users WHERE user_id = ?", (user_id,))
        return bool(row and row["verified"])

    async def mark_verified(self, user_id: int, verified: bool = True) -> None:
        await self.execute(
            "UPDATE users SET verified = ?, verified_at = ? WHERE user_id = ?",
            (1 if verified else 0, time.time() if verified else None, user_id),
        )

    async def reset_all_verifications(self) -> None:
        await self.execute("UPDATE users SET verified = 0, verified_at = NULL")

    # ------------------------------------------------------------------ #
    # VIP system
    # ------------------------------------------------------------------ #
    async def is_vip(self, user_id: int) -> bool:
        row = await self.fetch_one("SELECT is_vip FROM users WHERE user_id = ?", (user_id,))
        return bool(row and row["is_vip"])

    async def set_vip(self, user_id: int, vip: bool = True) -> None:
        await self.execute(
            "UPDATE users SET is_vip = ? WHERE user_id = ?",
            (1 if vip else 0, user_id),
        )

    async def count_vip_users(self) -> int:
        return int(await self.fetch_value("SELECT COUNT(*) FROM users WHERE is_vip=1") or 0)

    # ------------------------------------------------------------------ #
    # bans
    # ------------------------------------------------------------------ #
    async def is_banned(self, user_id: int) -> bool:
        row = await self.fetch_one(
            "SELECT expires_at FROM bans WHERE user_id = ? LIMIT 1", (user_id,)
        )
        if row is None:
            return False
        expires_at = row["expires_at"] if "expires_at" in row.keys() else None
        if expires_at is not None and float(expires_at) < time.time():
            # মেয়াদ শেষ — স্বয়ংক্রিয় মুছে দাও
            await self.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
            return False
        return True

    async def get_ban_info(self, user_id: int) -> Optional[aiosqlite.Row]:
        row = await self.fetch_one("SELECT * FROM bans WHERE user_id = ?", (user_id,))
        if row is None:
            return None
        expires_at = row["expires_at"] if "expires_at" in row.keys() else None
        if expires_at is not None and float(expires_at) < time.time():
            await self.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
            return None
        return row

    async def ban(self, user_id: int, reason: str = "", expires_at: Optional[float] = None) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO bans(user_id, reason, at, expires_at) VALUES(?,?,?,?)",
            (user_id, reason, time.time(), expires_at),
        )

    async def unban(self, user_id: int) -> None:
        await self.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))

    async def list_bans(self) -> List[aiosqlite.Row]:
        now = time.time()
        # মেয়াদোত্তীর্ণ ব্যান মুছে দাও
        await self.execute("DELETE FROM bans WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
        return await self.fetch_all("SELECT * FROM bans ORDER BY at DESC")

    # ------------------------------------------------------------------ #
    # movies
    # ------------------------------------------------------------------ #
    async def upsert_movie(self, movie: Dict[str, Any]) -> bool:
        existing = await self.fetch_one(
            "SELECT id FROM movies WHERE file_unique_id = ?", (movie["file_unique_id"],)
        )
        if existing:
            await self.execute(
                """
                UPDATE movies SET
                    file_id=?, file_type=?, file_name=?, mime_type=?,
                    size_bytes=?, duration=?, title=?, caption=?,
                    source_chat_id=?, source_message_id=?
                WHERE file_unique_id=?
                """,
                (
                    movie["file_id"], movie["file_type"], movie.get("file_name"),
                    movie.get("mime_type"), movie.get("size_bytes", 0) or 0,
                    movie.get("duration", 0) or 0, movie["title"], movie.get("caption") or "",
                    movie.get("source_chat_id"), movie.get("source_message_id"),
                    movie["file_unique_id"],
                ),
            )
            return False
        await self.execute(
            """
            INSERT INTO movies(file_unique_id, file_id, file_type, file_name,
                               mime_type, size_bytes, duration, title, caption,
                               source_chat_id, source_message_id, added_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                movie["file_unique_id"], movie["file_id"], movie["file_type"],
                movie.get("file_name"), movie.get("mime_type"),
                movie.get("size_bytes", 0) or 0, movie.get("duration", 0) or 0,
                movie["title"], movie.get("caption") or "",
                movie.get("source_chat_id"), movie.get("source_message_id"), time.time(),
            ),
        )
        return True

    async def get_movie(self, movie_id: int) -> Optional[aiosqlite.Row]:
        return await self.fetch_one("SELECT * FROM movies WHERE id = ?", (movie_id,))

    async def delete_movie(self, movie_id: int) -> None:
        await self.execute("DELETE FROM movies WHERE id = ?", (movie_id,))

    async def update_movie(self, movie_id: int, title: str, caption: str) -> None:
        """মুভির শিরোনাম ও ক্যাপশন আপডেট করে (FTS ট্রিগার স্বয়ংক্রিয়ভাবে আপডেট হয়)।"""
        await self.execute(
            "UPDATE movies SET title=?, caption=? WHERE id=?",
            (title.strip(), caption.strip(), movie_id),
        )

    async def increment_hits(self, movie_id: int) -> None:
        await self.execute("UPDATE movies SET hits = hits + 1 WHERE id = ?", (movie_id,))

    @staticmethod
    def _fts_query(query: str) -> Optional[str]:
        q = (query or "").strip()
        if not q:
            return None
        words = [w for w in re.split(r"\W+", q) if w]
        if not words:
            return None
        return " ".join(f'"{w}"*' for w in words)

    async def search_movies(self, query: str, limit: int = 30, offset: int = 0) -> List[aiosqlite.Row]:
        q = (query or "").strip()
        if not q:
            return await self.fetch_all(
                "SELECT * FROM movies ORDER BY added_at DESC LIMIT ? OFFSET ?", (limit, offset)
            )
        fts_q = self._fts_query(q)
        if not fts_q:
            return []
        try:
            return await self.fetch_all(
                """
                SELECT m.* FROM movies_fts f
                JOIN movies m ON m.id = f.rowid
                WHERE movies_fts MATCH ?
                ORDER BY rank
                LIMIT ? OFFSET ?
                """,
                (fts_q, limit, offset),
            )
        except Exception:
            log.exception("FTS query failed for %r", fts_q)
            return []

    async def count_search(self, query: str) -> int:
        q = (query or "").strip()
        if not q:
            return await self.fetch_value("SELECT COUNT(*) FROM movies") or 0
        fts_q = self._fts_query(q)
        if not fts_q:
            return 0
        try:
            v = await self.fetch_value(
                "SELECT COUNT(*) FROM movies_fts WHERE movies_fts MATCH ?", (fts_q,)
            )
            return int(v or 0)
        except Exception:
            return 0

    async def list_movies(self, limit: int = 50, offset: int = 0) -> List[aiosqlite.Row]:
        return await self.fetch_all(
            "SELECT * FROM movies ORDER BY added_at DESC LIMIT ? OFFSET ?", (limit, offset)
        )

    async def count_movies(self) -> int:
        return int(await self.fetch_value("SELECT COUNT(*) FROM movies") or 0)

    async def total_hits(self) -> int:
        return int(await self.fetch_value("SELECT COALESCE(SUM(hits), 0) FROM movies") or 0)

    async def get_popular_movies(self, limit: int = 5) -> List[aiosqlite.Row]:
        """সর্বাধিক ডাউনলোড হওয়া মুভি।"""
        return await self.fetch_all(
            "SELECT * FROM movies ORDER BY hits DESC, added_at DESC LIMIT ?", (limit,)
        )

    async def suggest_similar(self, query: str, limit: int = 3) -> List[aiosqlite.Row]:
        """কোনো রেজাল্ট না পেলে শব্দ ভেঙে কাছাকাছি মুভি খোঁজা।"""
        import re as _re
        words = [w for w in _re.split(r"\W+", (query or "").strip()) if len(w) >= 3]
        if not words:
            return []
        found: dict = {}
        for w in words[:4]:
            fts_q = f'"{w}"*'
            try:
                rows = await self.fetch_all(
                    """
                    SELECT m.* FROM movies_fts f
                    JOIN movies m ON m.id = f.rowid
                    WHERE movies_fts MATCH ?
                    ORDER BY rank LIMIT ?
                    """,
                    (fts_q, limit),
                )
                for r in rows:
                    if r["id"] not in found:
                        found[r["id"]] = r
                    if len(found) >= limit:
                        break
            except Exception:
                pass
            if len(found) >= limit:
                break
        return list(found.values())[:limit]

    # ------------------------------------------------------------------ #
    # web sessions
    # ------------------------------------------------------------------ #
    async def issue_session(self, user_id: int, ttl_seconds: int = 7 * 24 * 3600) -> str:
        import secrets as _secrets
        token = _secrets.token_urlsafe(32)
        now = time.time()
        await self.execute(
            "INSERT INTO web_sessions(token, user_id, created_at, expires_at, is_login) VALUES(?,?,?,?,?)",
            (token, user_id, now, now + ttl_seconds, 0),
        )
        return token

    async def session_user(self, token: str) -> Optional[int]:
        if not token:
            return None
        row = await self.fetch_one("SELECT * FROM web_sessions WHERE token = ?", (token,))
        if not row or row["is_login"] != 0:
            return None
        if row["expires_at"] < time.time():
            return None
        return int(row["user_id"])

    async def revoke_session(self, token: str) -> None:
        if not token:
            return
        await self.execute("DELETE FROM web_sessions WHERE token = ?", (token,))

    async def cleanup_sessions(self) -> None:
        await self.execute("DELETE FROM web_sessions WHERE expires_at < ?", (time.time(),))

    # ------------------------------------------------------------------ #
    # channels (multi-channel/group management)
    # ------------------------------------------------------------------ #
    async def add_channel(self, chat_id: int, *, title: Optional[str] = None,
                           username: Optional[str] = None, invite_url: Optional[str] = None,
                           type: str = "channel") -> None:
        await self.execute(
            """
            INSERT INTO channels(chat_id, title, username, invite_url, type, added_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title=excluded.title, username=excluded.username,
                invite_url=excluded.invite_url, type=excluded.type
            """,
            (chat_id, title, username, invite_url, type, time.time()),
        )

    async def remove_channel(self, chat_id: int) -> None:
        await self.execute("DELETE FROM channels WHERE chat_id=?", (chat_id,))

    async def list_channels(self) -> List[aiosqlite.Row]:
        return await self.fetch_all("SELECT * FROM channels ORDER BY added_at ASC")

    # ------------------------------------------------------------------ #
    # force-join
    # ------------------------------------------------------------------ #
    async def add_force_join(self, chat_id: int, *, title: Optional[str] = None,
                              username: Optional[str] = None, invite_url: Optional[str] = None) -> None:
        await self.execute(
            """
            INSERT INTO force_join(chat_id, title, username, invite_url, added_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title=excluded.title, username=excluded.username, invite_url=excluded.invite_url
            """,
            (chat_id, title, username, invite_url, time.time()),
        )

    async def remove_force_join(self, chat_id: int) -> None:
        await self.execute("DELETE FROM force_join WHERE chat_id = ?", (chat_id,))

    async def list_force_join(self) -> List[aiosqlite.Row]:
        return await self.fetch_all("SELECT * FROM force_join ORDER BY added_at ASC")

    # ------------------------------------------------------------------ #
    # movie requests
    # ------------------------------------------------------------------ #
    async def add_movie_request(self, user_id: int, title: str, *,
                                 username: Optional[str] = None,
                                 first_name: Optional[str] = None,
                                 note: Optional[str] = None) -> int:
        assert self._conn is not None
        async with self._lock:
            cur = await self._conn.execute(
                """
                INSERT INTO movie_requests(user_id, username, first_name, title, note, created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (user_id, username, first_name, title, note, time.time()),
            )
            await self._conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    async def list_movie_requests(self, status: str = "all", limit: int = 100, offset: int = 0) -> List[aiosqlite.Row]:
        if status == "all":
            return await self.fetch_all(
                "SELECT * FROM movie_requests ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return await self.fetch_all(
            "SELECT * FROM movie_requests WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status, limit, offset),
        )

    async def count_movie_requests(self, status: str = "pending") -> int:
        v = await self.fetch_value(
            "SELECT COUNT(*) FROM movie_requests WHERE status=?", (status,)
        )
        return int(v or 0)

    async def resolve_movie_request(self, req_id: int, status: str = "done") -> None:
        await self.execute(
            "UPDATE movie_requests SET status=?, resolved_at=? WHERE id=?",
            (status, time.time(), req_id),
        )

    async def delete_movie_request(self, req_id: int) -> None:
        await self.execute("DELETE FROM movie_requests WHERE id=?", (req_id,))

    # ------------------------------------------------------------------ #
    # settings  (k/v)
    # ------------------------------------------------------------------ #
    async def get_setting(self, key: str, default: Any = None) -> Any:
        row = await self.fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    async def set_setting(self, key: str, value: Any) -> None:
        encoded = json.dumps(value)
        await self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, encoded),
        )

    async def get_bot_message(self, key: str, default: str = "") -> str:
        val = await self.get_setting(f"msg:{key}", default)
        return str(val) if val else default

    async def set_bot_message(self, key: str, text: str) -> None:
        await self.set_setting(f"msg:{key}", text)

    # ------------------------------------------------------------------ #
    # maintenance mode
    # ------------------------------------------------------------------ #
    async def is_maintenance(self) -> bool:
        val = await self.get_setting("maintenance_mode", False)
        return bool(val)

    async def set_maintenance(self, on: bool) -> None:
        await self.set_setting("maintenance_mode", on)

    async def set_maintenance_timed(self, on: bool, until: float = 0.0, total_secs: float = 0.0) -> None:
        """on=True + until=timestamp + total_secs → timed maintenance; on=False → turn off."""
        await self.set_setting("maintenance_mode", on)
        await self.set_setting("maintenance_until", until if on else 0.0)
        await self.set_setting("maintenance_total", total_secs if on else 0.0)

    async def get_maintenance_until(self) -> float:
        val = await self.get_setting("maintenance_until", 0.0)
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    async def get_maintenance_total(self) -> float:
        val = await self.get_setting("maintenance_total", 0.0)
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    async def get_discussion_url(self) -> str:
        val = await self.get_setting("discussion_url", "")
        return str(val) if val else ""

    async def set_discussion_url(self, url: str) -> None:
        await self.set_setting("discussion_url", url.strip())

    # ------------------------------------------------------------------ #
    # logs
    # ------------------------------------------------------------------ #
    async def log_event(self, kind: str, payload: Optional[Dict[str, Any]] = None) -> None:
        try:
            await self.execute(
                "INSERT INTO logs(kind, payload, created_at) VALUES(?,?,?)",
                (kind, json.dumps(payload or {}), time.time()),
            )
        except Exception:
            log.exception("log_event failed")

    async def recent_logs(self, limit: int = 100) -> List[aiosqlite.Row]:
        return await self.fetch_all(
            "SELECT * FROM logs ORDER BY created_at DESC LIMIT ?", (limit,)
        )

    # ------------------------------------------------------------------ #
    # warnings (#26)
    # ------------------------------------------------------------------ #
    async def add_warning(self, user_id: int, reason: str = "", admin_id: int = 0) -> int:
        assert self._conn is not None
        async with self._lock:
            cur = await self._conn.execute(
                "INSERT INTO warnings(user_id, reason, admin_id, created_at) VALUES(?,?,?,?)",
                (user_id, reason, admin_id, time.time()),
            )
            await self._conn.commit()
            return cur.lastrowid or 0

    async def get_warnings(self, user_id: int) -> List[aiosqlite.Row]:
        return await self.fetch_all(
            "SELECT * FROM warnings WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        )

    async def count_warnings(self, user_id: int) -> int:
        v = await self.fetch_value("SELECT COUNT(*) FROM warnings WHERE user_id=?", (user_id,))
        return int(v or 0)

    async def clear_warnings(self, user_id: int) -> None:
        await self.execute("DELETE FROM warnings WHERE user_id=?", (user_id,))

    # ------------------------------------------------------------------ #
    # favorites / watchlist (#11)
    # ------------------------------------------------------------------ #
    async def add_favorite(self, user_id: int, movie_id: int) -> bool:
        try:
            await self.execute(
                "INSERT INTO favorites(user_id, movie_id, created_at) VALUES(?,?,?)",
                (user_id, movie_id, time.time()),
            )
            return True
        except Exception:
            return False

    async def remove_favorite(self, user_id: int, movie_id: int) -> None:
        await self.execute(
            "DELETE FROM favorites WHERE user_id=? AND movie_id=?", (user_id, movie_id)
        )

    async def is_favorite(self, user_id: int, movie_id: int) -> bool:
        row = await self.fetch_one(
            "SELECT id FROM favorites WHERE user_id=? AND movie_id=?", (user_id, movie_id)
        )
        return row is not None

    async def get_favorites(self, user_id: int, limit: int = 20) -> List[aiosqlite.Row]:
        return await self.fetch_all(
            """
            SELECT m.*, f.created_at AS fav_at FROM favorites f
            JOIN movies m ON m.id = f.movie_id
            WHERE f.user_id=?
            ORDER BY f.created_at DESC LIMIT ?
            """,
            (user_id, limit),
        )

    # ------------------------------------------------------------------ #
    # ratings / feedback (#38)
    # ------------------------------------------------------------------ #
    async def rate_movie(self, user_id: int, movie_id: int, rating: int) -> None:
        await self.execute(
            """
            INSERT INTO ratings(user_id, movie_id, rating, created_at) VALUES(?,?,?,?)
            ON CONFLICT(user_id, movie_id) DO UPDATE SET rating=excluded.rating, created_at=excluded.created_at
            """,
            (user_id, movie_id, max(1, min(5, rating)), time.time()),
        )

    async def get_movie_avg_rating(self, movie_id: int) -> tuple:
        row = await self.fetch_one(
            "SELECT AVG(rating) as avg_r, COUNT(*) as cnt FROM ratings WHERE movie_id=?",
            (movie_id,),
        )
        if row is None:
            return (0.0, 0)
        return (round(float(row["avg_r"] or 0), 1), int(row["cnt"] or 0))

    async def get_user_rating(self, user_id: int, movie_id: int) -> Optional[int]:
        row = await self.fetch_one(
            "SELECT rating FROM ratings WHERE user_id=? AND movie_id=?", (user_id, movie_id)
        )
        return int(row["rating"]) if row else None

    # ------------------------------------------------------------------ #
    # subscriptions — নতুন মুভি নোটিফিকেশন (#13)
    # ------------------------------------------------------------------ #
    async def is_subscribed(self, user_id: int) -> bool:
        row = await self.fetch_one(
            "SELECT active FROM subscriptions WHERE user_id=?", (user_id,)
        )
        return bool(row and row["active"])

    async def set_subscription(self, user_id: int, active: bool) -> None:
        await self.execute(
            """
            INSERT INTO subscriptions(user_id, active, created_at) VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET active=excluded.active
            """,
            (user_id, 1 if active else 0, time.time()),
        )

    async def get_subscribed_users(self) -> List[int]:
        rows = await self.fetch_all("SELECT user_id FROM subscriptions WHERE active=1")
        return [r["user_id"] for r in rows]

    # ------------------------------------------------------------------ #
    # request votes (#7)
    # ------------------------------------------------------------------ #
    async def vote_request(self, user_id: int, request_id: int) -> bool:
        try:
            await self.execute(
                "INSERT INTO request_votes(user_id, request_id) VALUES(?,?)",
                (user_id, request_id),
            )
            await self.execute(
                "UPDATE movie_requests SET vote_count = COALESCE(vote_count,0) + 1 WHERE id=?",
                (request_id,),
            )
            return True
        except Exception:
            return False

    async def unvote_request(self, user_id: int, request_id: int) -> None:
        await self.execute(
            "DELETE FROM request_votes WHERE user_id=? AND request_id=?", (user_id, request_id)
        )
        await self.execute(
            "UPDATE movie_requests SET vote_count = MAX(0, COALESCE(vote_count,0) - 1) WHERE id=?",
            (request_id,),
        )

    async def has_voted(self, user_id: int, request_id: int) -> bool:
        row = await self.fetch_one(
            "SELECT 1 FROM request_votes WHERE user_id=? AND request_id=?",
            (user_id, request_id),
        )
        return row is not None

    async def get_request_votes(self, request_id: int) -> int:
        v = await self.fetch_value(
            "SELECT COALESCE(vote_count, 0) FROM movie_requests WHERE id=?", (request_id,)
        )
        return int(v or 0)

    # ------------------------------------------------------------------ #
    # search log (#32, #34)
    # ------------------------------------------------------------------ #
    async def log_search(self, user_id: int, query: str, results: int) -> None:
        try:
            await self.execute(
                "INSERT INTO search_log(user_id, query, results, created_at) VALUES(?,?,?,?)",
                (user_id, query, results, time.time()),
            )
        except Exception:
            pass

    async def get_failed_searches(self, limit: int = 20) -> List[aiosqlite.Row]:
        return await self.fetch_all(
            """
            SELECT query, COUNT(*) as cnt, MAX(created_at) as last_seen
            FROM search_log WHERE results=0
            GROUP BY LOWER(TRIM(query))
            ORDER BY cnt DESC LIMIT ?
            """,
            (limit,),
        )

    async def get_top_searches(self, limit: int = 10) -> List[aiosqlite.Row]:
        return await self.fetch_all(
            """
            SELECT query, COUNT(*) as cnt, MAX(created_at) as last_seen
            FROM search_log
            GROUP BY LOWER(TRIM(query))
            ORDER BY cnt DESC LIMIT ?
            """,
            (limit,),
        )

    # ------------------------------------------------------------------ #
    # daily request count (#8)
    # ------------------------------------------------------------------ #
    async def count_daily_requests(self, user_id: int) -> int:
        today_start = time.time() - (time.time() % 86400)
        v = await self.fetch_value(
            "SELECT COUNT(*) FROM movie_requests WHERE user_id=? AND created_at >= ?",
            (user_id, today_start),
        )
        return int(v or 0)

    async def find_pending_request(self, user_id: int, title: str):
        """একই ইউজারের একই মুভির pending/open রিকোয়েস্ট খোঁজে (case-insensitive)।"""
        return await self.fetch_one(
            """
            SELECT * FROM movie_requests
            WHERE user_id=? AND status='pending'
              AND LOWER(TRIM(title))=LOWER(TRIM(?))
            ORDER BY created_at DESC LIMIT 1
            """,
            (user_id, title),
        )

    async def count_pending_same_title_all_users(self, title: str) -> int:
        """সব ইউজার মিলিয়ে একই মুভির কতটি pending রিকোয়েস্ট আছে।"""
        v = await self.fetch_value(
            "SELECT COUNT(*) FROM movie_requests WHERE status='pending' AND LOWER(TRIM(title))=LOWER(TRIM(?))",
            (title,),
        )
        return int(v or 0)

    async def get_user_requests(self, user_id: int, limit: int = 20) -> List[aiosqlite.Row]:
        return await self.fetch_all(
            "SELECT * FROM movie_requests WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )

    # ------------------------------------------------------------------ #
    # download history (#12)
    # ------------------------------------------------------------------ #
    async def get_download_history(self, user_id: int, limit: int = 20) -> List[aiosqlite.Row]:
        return await self.fetch_all(
            """
            SELECT l.id, l.kind, l.created_at,
                   JSON_EXTRACT(l.payload,'$.movie_id') as movie_id,
                   JSON_EXTRACT(l.payload,'$.title') as title
            FROM logs l
            WHERE l.kind='delivered'
              AND CAST(JSON_EXTRACT(l.payload,'$.user_id') AS INTEGER) = ?
            ORDER BY l.created_at DESC LIMIT ?
            """,
            (user_id, limit),
        )

    # ------------------------------------------------------------------ #
    # user stats (#15)
    # ------------------------------------------------------------------ #
    async def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        user_row, dl_count, req_count, warn_count, fav_count, sub_active = await asyncio.gather(
            self.fetch_one("SELECT * FROM users WHERE user_id=?", (user_id,)),
            self.fetch_value(
                "SELECT COUNT(*) FROM logs WHERE kind='delivered' AND CAST(JSON_EXTRACT(payload,'$.user_id') AS INTEGER)=?",
                (user_id,),
            ),
            self.fetch_value("SELECT COUNT(*) FROM movie_requests WHERE user_id=?", (user_id,)),
            self.fetch_value("SELECT COUNT(*) FROM warnings WHERE user_id=?", (user_id,)),
            self.fetch_value("SELECT COUNT(*) FROM favorites WHERE user_id=?", (user_id,)),
            self.fetch_one("SELECT active FROM subscriptions WHERE user_id=?", (user_id,)),
        )
        return {
            "user": user_row,
            "downloads": int(dl_count or 0),
            "requests": int(req_count or 0),
            "warnings": int(warn_count or 0),
            "favorites": int(fav_count or 0),
            "subscribed": bool(sub_active and sub_active["active"]),
        }

    # ------------------------------------------------------------------ #
    # Ad Token system
    # ------------------------------------------------------------------ #
    async def create_ad_token(self, movie_id: int, user_id: int) -> str:
        import secrets as _sec
        token = _sec.token_urlsafe(24)
        now = time.time()
        expires = now + 7200
        assert self._conn is not None
        await self._conn.execute(
            "INSERT INTO ad_tokens(token,movie_id,user_id,created_at,expires_at) VALUES(?,?,?,?,?)",
            (token, movie_id, user_id, now, expires),
        )
        await self._conn.commit()
        return token

    async def get_ad_token(self, token: str) -> Optional[Dict]:
        row = await self.fetch_one("SELECT * FROM ad_tokens WHERE token=?", (token,))
        return dict(row) if row else None

    async def mark_ad_token_used(self, token: str) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE ad_tokens SET used=1 WHERE token=?", (token,)
        )
        await self._conn.commit()

    async def count_ad_tokens_total(self) -> int:
        val = await self.fetch_value("SELECT COUNT(*) FROM ad_tokens", ())
        return int(val or 0)

    async def count_ad_tokens_used(self) -> int:
        val = await self.fetch_value(
            "SELECT COUNT(*) FROM ad_tokens WHERE used=1", ()
        )
        return int(val or 0)

    # ------------------------------------------------------------------ #
    # Movie upload notifications (repeating reminders)
    # ------------------------------------------------------------------ #
    _NOTIF_INTERVAL = 45 * 60  # ৪৫ মিনিট

    async def create_movie_notification(
        self, user_id: int, movie_title: str, request_id: Optional[int] = None
    ) -> int:
        """আপলোড-সম্পন্ন নোটিফিকেশন তৈরি করো। প্রথম send এখনই হবে।"""
        now = time.time()
        assert self._conn is not None
        async with self._lock:
            cur = await self._conn.execute(
                """INSERT INTO movie_notifications
                   (user_id, movie_title, request_id, created_at, next_send_at, send_count, fulfilled)
                   VALUES (?,?,?,?,?,0,0)""",
                (user_id, movie_title, request_id, now, now),
            )
            await self._conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    async def get_due_notifications(self) -> List[aiosqlite.Row]:
        """এখন পাঠানোর সময় হয়েছে এমন নোটিফিকেশন ফেরত দাও।"""
        now = time.time()
        return await self.fetch_all(
            """SELECT * FROM movie_notifications
               WHERE fulfilled=0 AND send_count < max_sends AND next_send_at <= ?
               ORDER BY next_send_at""",
            (now,),
        )

    async def mark_notification_sent(self, notif_id: int) -> None:
        """send_count বাড়াও এবং পরের send time সেট করো।"""
        now = time.time()
        await self.execute(
            """UPDATE movie_notifications
               SET last_sent_at=?, next_send_at=?, send_count=send_count+1
               WHERE id=?""",
            (now, now + self._NOTIF_INTERVAL, notif_id),
        )

    async def fulfill_user_notifications(self, user_id: int) -> None:
        """ইউজার মুভি ডাউনলোড করলে তার সব pending নোটিফিকেশন বন্ধ করো।"""
        await self.execute(
            "UPDATE movie_notifications SET fulfilled=1 WHERE user_id=? AND fulfilled=0",
            (user_id,),
        )

    async def count_pending_notifications(self) -> int:
        val = await self.fetch_value(
            "SELECT COUNT(*) FROM movie_notifications WHERE fulfilled=0 AND send_count < max_sends"
        )
        return int(val or 0)


db = Database()
