---
name: Telegram bot ad-gated delivery pattern
description: Pattern for gating file/content delivery in a Telegram bot behind a timed ad-view web page, using a token and server-side completion.
---

To monetize a Telegram bot via ad views without trusting a client-side redirect, use a self-hosted token + countdown page + server-side completion:

1. On the delivery-triggering callback, generate a random token and store `(token, resource_id, user_id, created_at, expires_at, used)` in a DB table with a short-medium expiry (e.g. 2h).
2. Search-result buttons should open a **public, unauthenticated** movie detail page `/miniapp/?movie={id}&token={token}`. Its Download action should go through the configured ad shortener and redirect to `/ad/{token}`.
3. The first valid `/ad/{token}` request starts a server-side wait. Show a countdown, but enable the final Download action only after it ends; never run a bot-side delivery timer.
4. The completion endpoint must reject requests until that server-side wait elapses, then claim the token atomically and send only to its stored Telegram user. Mark it used only after a successful send.
5. Keep the `https://t.me/{bot_username}?start=get_{token}` fallback only if it checks the same `ad_completed` state and user binding.
5. For bot search results that should open a web detail page first, create the user-bound token when building the result keyboard and pass it as `/miniapp/?movie={id}&token={token}`. The page can then open the movie detail automatically and reuse that token for the ad page.

**Why:** This avoids depending on external ad-shortlink providers, keeps all state in the bot's own DB, and prevents replay/sharing since tokens are single-use and user-bound. A bot-side timer can send a file without the user visiting the ad page, so delivery must be triggered only by the server-validated web flow.

**How to apply:** Reuse this whenever a Telegram bot needs a "watch this / wait N seconds" step before granting a file, code, or link — make the wait/enable toggle admin-configurable rather than hardcoded.
