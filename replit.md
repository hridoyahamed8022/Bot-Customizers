# Moviex Hub — Bengali Movie/Drama Telegram Bot

A Telegram bot (@Moviex_hub_bot) that lets users search for and receive Bengali/Hindi/English movie and drama files, with an admin web panel and an ad-based monetization gate before file delivery.

## Run & Operate

- `bash run_bot.sh` — runs the bot + admin panel (maps `TELEGRAM_BOT_TOKEN` secret → `BOT_TOKEN`, then `python main.py`)
- Workflow: `artifacts/api-server: API Server` runs the above on port 8080 (proxied to `/`)
- Required secrets: `TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `SESSION_SECRET`, `REQUEST_GROUP_ID`
- DB: SQLite at `data/bot.sqlite3` (aiosqlite, WAL mode)

## Stack

- Python 3.11, aiogram 3.x (Telegram bot), aiohttp (admin web panel), aiosqlite
- aiohttp_jinja2 for admin panel templates (Bengali UI)
- openai for the in-bot AI FAQ/chat helper

## Where things live

- `bot/handlers/` — Telegram command/callback handlers (`start.py`, `search.py`, `callbacks.py`, etc.)
- `bot/web/routes.py` — all admin panel routes + public `/ad/{token}` countdown page
- `bot/web/templates/` — Jinja2 templates for admin panel + `ad.html` (public countdown page)
- `bot/db.py` — SQLite schema + all DB access methods
- `bot/config.py` — env-based settings, including `public_url` (used to build ad links)
- `run_bot.sh` — entrypoint used by the workflow

## Architecture decisions

- **Ad monetization flow**: movie delivery is gated behind a countdown ad page instead of a direct file send.
  1. User taps a movie button (`m:get:{id}`) → if `ad_enabled` setting is on, bot creates a row in `ad_tokens` (token, movie_id, user_id, 2h expiry) and sends a link to `{public_url}/ad/{token}`.
  2. `/ad/{token}` (public aiohttp route, no auth) renders a countdown page; after the configured wait, a button opens `https://t.me/{bot_username}?start=get_{token}`.
  3. Bot's `/start get_{token}` handler validates the token (exists, unused, unexpired, matches user) then delivers the movie and marks the token used.
  - Configurable via admin panel Settings page: `ad_enabled`, `ad_wait_seconds` (5–300s), `bot_username` — stored as normal `settings` k/v rows.
  - The `/ad/{token}` URL sent to users is wrapped through a real ad-monetization shortlink service before sending (`bot/utils_ouo.py::shorten_url`) — primary provider ShrinkMe.io, fallback Ouo.io, final fallback is the raw unshortened link so delivery is never blocked. Keys: `SHRINKME_API_KEY`, `OUO_API_KEY` secrets.
- **Admin panel redirects use JS (`window.location.replace`), not HTTP 302** — see gotcha below.
- **Auto broadcast notifications** go out to all users (`db.all_user_ids()`) on three triggers, each fire-and-forget via `asyncio.create_task`:
  1. Bot process startup (`main.py::_startup_broadcast`) — guarded by a `last_startup_broadcast_ts` setting so rapid restarts within 5 minutes don't re-spam users.
  2. Maintenance mode turned ON (`bot/web/routes.py::_broadcast_maintenance`, called from `maintenance_toggle`).
  3. Ad system toggled ON for the first time (`bot/web/routes.py::_broadcast_ad_enabled`, called from `settings_ad_save` only on the off→on transition).

## Product

- Users DM the bot, search movies by (English-only) title, must join force-join channels once, then request a file.
- If ad monetization is on, users must visit a countdown ad page and wait out a timer before the bot delivers the file via a `/start get_{token}` deep link.
- Admin web panel (Bengali UI) manages: movie library, broadcasts, movie requests, bot messages, channels, users/bans, stats, force-join channels, maintenance mode, and ad settings.

## User preferences

- All user-facing bot and admin panel text must stay in Bengali.
- Movie search must be in the movie titled in English; Bengali-script search queries are rejected in the search flow.

## Gotchas

- **Replit's proxy breaks classic HTTP 302 admin-login redirects.** The proxy's cookie/host handling means `Set-Cookie` + `raise web.HTTPFound(...)` redirects can drop the session cookie before the next request. Fix: every admin route returns a `200` with a tiny `<script>window.location.replace(...)</script>` body via the `_js_redirect()` helper in `bot/web/routes.py`, and session cookies are set with `SameSite=None; Secure`. Do not reintroduce `raise web.HTTPFound(...)` in this file.
- `db.get_setting`/`db.set_setting` JSON-encode/decode values automatically — always pass/compare plain Python values (e.g. compare to the string `"1"`, not `'"1"'`).
- The bot's SQLite file is at `data/bot.sqlite3`; use aiosqlite (WAL mode) for any manual inspection to avoid lock conflicts with the running bot.

## Pointers

- See the `pnpm-workspace` skill for general monorepo conventions (this artifact is a standalone Python service, not a pnpm package).
