---
name: Telegram bot ad-gated delivery pattern
description: Pattern for gating file/content delivery in a Telegram bot behind a timed ad-view web page, using a token + deep link handoff.
---

To monetize a Telegram bot via ad views without a third-party shortlink service, use a self-hosted token + countdown page + deep-link pattern:

1. On the delivery-triggering callback, generate a random token and store `(token, resource_id, user_id, created_at, expires_at, used)` in a DB table with a short-medium expiry (e.g. 2h).
2. Send the user a link to a **public, unauthenticated** web route `/ad/{token}` (must not require the admin panel's login).
3. That page renders a client-side countdown (seconds configurable via an admin setting). Only after the countdown elapses does it reveal a button linking to `https://t.me/{bot_username}?start=get_{token}`.
4. The bot's `/start` handler checks for a `get_` prefixed payload, loads the token row, and validates: exists, not used, not expired, and `user_id` matches the requester — only then delivers the resource and marks the token used.

**Why:** This avoids depending on external ad-shortlink providers, keeps all state in the bot's own DB, and prevents replay/sharing since tokens are single-use and user-bound. The countdown must be enforced server-side conceptually (via the deep-link only appearing after the wait) — the client-side timer alone is not the security boundary, the token's per-user/per-use check is.

**How to apply:** Reuse this whenever a Telegram bot needs a "watch this / wait N seconds" step before granting a file, code, or link — make the wait/enable toggle admin-configurable rather than hardcoded.
