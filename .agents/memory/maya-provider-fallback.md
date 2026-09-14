---
name: Maya provider fallback
description: Environment-specific provider availability and the required local assistant behavior for Maya.
---

Maya should remain useful when an external model is unavailable: search the movie database locally, support popular/upcoming/stats intents, and state the ad-gated download flow clearly.

**Why:** The configured Gemini credential returned HTTP 403 in this environment, and the OpenAI integration was not available; returning provider errors made the assistant appear broken.

**How to apply:** Prefer a live provider when it is healthy, but fall back to the local assistant and never expose provider tool-call traces, credentials, or raw HTTP errors to users.