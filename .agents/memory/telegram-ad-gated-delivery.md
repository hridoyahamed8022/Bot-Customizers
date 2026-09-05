---
name: Telegram bot ad-gated delivery pattern
description: Pattern for gating file/content delivery in a Telegram bot behind a timed ad-view web page, using a token and server-side completion.
---

To monetize a Telegram bot via ad views without trusting a client-side redirect, use a self-hosted token + countdown page + server-side completion:

1. On the delivery-triggering callback, generate a random token and store `(token, resource_id, user_id, created_at, expires_at, used)` in a DB table with a short-medium expiry (e.g. 2h).
2. Send the user a link to a **public, unauthenticated** web route `/ad/{token}` (must not require the admin panel's login).
3. That page renders a client-side countdown (seconds configurable via an admin setting). Only after the countdown elapses does it POST to `/ad/{token}/complete`; the server loads the token's stored Telegram user ID and sends the resource directly to that inbox.
4. Claim the token atomically before sending and mark it used only after a successful send. Keep the validated `https://t.me/{bot_username}?start=get_{token}` handler as a fallback for browsers where direct completion fails.

**Why:** This avoids depending on external ad-shortlink providers, keeps all state in the bot's own DB, and prevents replay/sharing since tokens are single-use and user-bound. The client-side timer is only UX; the server-side token state and expiry are the security boundary.

**How to apply:** Reuse this whenever a Telegram bot needs a "watch this / wait N seconds" step before granting a file, code, or link — make the wait/enable toggle admin-configurable rather than hardcoded.
