# Recheck

The 14 findings from the `/code-review` run on 2026-10-19, kept in the original order and numbering. The review read the code only. It did not run anything.

Authentication and authorization are still being built, so some of these may no longer apply once that lands. Tick a box when it is fixed. If a point turns out to be wrong or no longer relevant, write the reason next to it instead of deleting it.

Line numbers are from the review date and will drift. Where I think a point depends on the auth work, I tagged it **(depends on auth)**. That tag is my guess, not the reviewer's.

## Correctness

- [ ] **1. Verify is sent the re-serialized event, not the raw body.** [app/paypal.py:36](app/paypal.py#L36)
  PayPal's signature covers a CRC32 of the exact bytes it sent. `json.loads` followed by httpx's `json=` can change whitespace, escapes and float formatting, and httpx's JSON encoding has changed between releases. A genuine event can then fail verification and be dropped after retries. The tests mock PayPal, so this is never exercised.
  *Fix:* build the request body with the raw bytes spliced in, `content=b'{...,"webhook_event":' + body + b'}'`.

- [ ] **4. A PayPal 4xx on the verify call becomes a 500.** [app/paypal.py:38](app/paypal.py#L38)
  `raise_for_status()` raises on any 4xx. A body like `{"id": NaN}` makes httpx raise `ValueError`, and a payload PayPal rejects raises `HTTPStatusError`. Forged input then produces 5xx noise and error logs.
  *Fix:* only network errors and 5xx should raise. Map a verify 4xx to `return False` or a 400.

- [ ] **8. Cached token is never dropped on 401, and refresh has no lock.** [app/paypal.py:13](app/paypal.py#L13)
  If PayPal revokes the token before `exp`, webhooks return 500 until it expires. When the cache is empty, a burst of threads each fetches a token.
  *Fix:* clear `_token` on 401 and retry once. Guard the refresh with a `threading.Lock`.

## Config and deploy

- [ ] **5. `PAYPAL_BASE_URL` is undocumented and defaults to sandbox.** [render.yaml:13](render.yaml#L13)
  It is in neither `render.yaml` nor `.env.example`. A live deploy keeps the sandbox host, the OAuth call returns 401, and every webhook returns 500.
  *Fix:* add it to both files, or require it explicitly instead of defaulting to sandbox.

- [ ] **6. Required env vars are only read at request time.** [app/main.py:11](app/main.py#L11)
  `lifespan` checks only the database. A missing `PAYPAL_*` value lets the service boot and pass `/healthz`, then every webhook fails with a `KeyError` 500.
  *Fix:* check all required variables in `lifespan` before `yield`.

- [ ] **11. Cert URL allowlist is hard-coded and ignores `BASE`.** [app/paypal.py:8](app/paypal.py#L8)
  `CERT_URL` allows `api.` and `api.sandbox.` hosts, while `BASE` defaults to `api-m.`. It would reject a legitimate cert URL on another host such as `api-m.paypal.com`, and it accepts a sandbox cert URL while `BASE` is live. PayPal validates the cert itself.
  *Fix:* derive the host from `BASE`, or drop the check.

- [ ] **14. No `ipAllowList` on the free Postgres, and the free tier is not durable.** [render.yaml:1](render.yaml#L1)
  **Unverified:** the reviewer could not check this and was going from memory. Confirm against Render's docs first. Omitting `ipAllowList` may leave the database reachable from any IP, and Render's free Postgres has historically expired after about 30 days, which would delete stored events.
  *Fix:* set `ipAllowList: []` and use a paid plan for anything that must keep payment events.

## Security hardening

- [ ] **2. No freshness or replay check on `PAYPAL-TRANSMISSION-TIME`.** [app/paypal.py:26](app/paypal.py#L26) **(depends on auth)**
  Captured headers can be replayed indefinitely. PayPal's integrity binding is only a CRC32 of the body, and dedupe is keyed on an `id` inside that body, so a crafted body with a new `id` could be stored as genuine. Harmless today because nothing consumes the table.
  *Fix:* reject old `transmission_time`, and re-fetch the event from PayPal's API by id before acting on it.

- [ ] **3. Unauthenticated callers can trigger blocking PayPal calls.** [app/main.py:36](app/main.py#L36) **(depends on auth)**
  There is no body-size limit, rate limit or concurrency cap, and the same 40-thread pool serves `/healthz`. A flood of plausible-looking requests can exhaust it, fail Render's health check and restart the service.
  *Fix:* cap body size, add cheap pre-checks, a concurrency limit and a rate limit.

## Performance

- [ ] **9. `httpx.post` builds a new client on every call.** [app/paypal.py:32](app/paypal.py#L32)
  Each webhook pays for a fresh TLS handshake and CA-bundle load, twice when the token is stale.
  *Fix:* one module-level `httpx.Client(timeout=...)`.

## Tests

- [ ] **7. Tests don't cover the real SQL or the PayPal request.** [tests/test_webhook.py:64](tests/test_webhook.py#L64)
  `test_u12` uses a hand-written fake `claim`, so a bug in `ON CONFLICT DO NOTHING`, the `rowcount == 1` check or the schema would pass. The PayPal mock accepts any body, so a wrong field name, a missing `webhook_id` or a re-serialized event goes unnoticed. Also untested: invalid JSON, PayPal 4xx/5xx, token failure, token caching and expiry, and `/healthz`.
  *Fix:* add a real-Postgres test or a fake connection, and assert the verify request payload.

## Housekeeping

- [ ] **10. Dependencies are unpinned.** [requirements.txt:1](requirements.txt#L1)
  A redeploy can pick up a new httpx, which changes the bytes sent to PayPal (see #1), or break the test suite. The test run today already prints a Starlette warning that `httpx` with `TestClient` is deprecated in favor of `httpx2`.
  *Fix:* pin versions or use a lock file.

- [ ] **12. `app/` has no `__init__.py`.** [app/main.py:7](app/main.py#L7)
  It works as an implicit namespace package, but any regular `app` package elsewhere on `sys.path` would shadow it.
  *Fix:* add an empty `app/__init__.py`.

- [ ] **13. Needless header-map copy.** [app/paypal.py:28](app/paypal.py#L28)
  `{k.upper(): v ...}` rebuilds the dict on every request. Starlette's `Headers` is already case-insensitive, so `headers.get('paypal-auth-algo')` does the same job.
