---
name: Ad token delivery locking
description: Prevent duplicate or lost movie sends when multiple ad completion paths race.
---

Use one atomic token state transition for every delivery path: unused → sending → used, with sending → unused on failure.

**Why:** The Mini App completion endpoint, Telegram callback countdown, and `/start get_` fallback can all finish for the same token. Without a shared claim, users can receive duplicates or a failed path can incorrectly consume the token.

**How to apply:** Keep claim/release/mark-used in the database layer and require every new ad delivery entry point to use them before calling Telegram.