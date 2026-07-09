---
name: Replit proxy breaks aiohttp HTTP redirects
description: aiohttp admin panels behind Replit's shared proxy lose session cookies on classic HTTP 302 redirects; use JS redirects instead.
---

Classic `raise web.HTTPFound(...)` redirects (HTTP 302 + Set-Cookie in the same response) can drop the session cookie before the browser's next request when the app sits behind Replit's shared reverse proxy (mTLS, path-based routing, iframe preview).

**Why:** The proxy's handling of `Set-Cookie` alongside a redirect response, combined with cross-origin iframe preview access, means `SameSite=Strict`/`Lax` cookies set on a 302 response are unreliable. The browser follows the redirect before reliably persisting the cookie in the iframe context.

**How to apply:** For any aiohttp (or similar) admin panel meant to run inside a Replit preview iframe:
- Never `raise web.HTTPFound(...)`. Instead return a `200 OK` HTML body containing `<script>window.location.replace('/target')</script>` (see `_js_redirect()` helper pattern).
- Set session/auth cookies with `SameSite=None; Secure` (not `Strict`/`Lax`), since the proxy serves everything over HTTPS.
- Apply this to login, logout, and every POST-handler redirect in the app — a single missed `raise HTTPFound` reintroduces the bug.
