# Changes

## 2026-10-21: RECHECK fixes

- `app/grounding.py` is now `app/output_guardrails.py`, and the `grounding_errors` state key is now `guardrail_errors`.
- Webhook verification sends PayPal the raw body, treats a verify 400/422 as a bad signature, and retries once on 401 (RECHECK #1, #4, #8).
- One shared `httpx.Client` for all PayPal calls (#9). The cert host must match `BASE`'s environment (#11). Headers are read without a copy (#13).
- The app will not start without `DATABASE_URL` and the `PAYPAL_*` values (#6). `PAYPAL_BASE_URL` is in `.env.example` and `render.yaml` (#5).
- All dependencies are pinned (#10), and `app/__init__.py` exists (#12).
- Evals with the real Groq model in `tests/test_eval_output_guardrails.py`, run with `pytest -m eval`. Plain `pytest` skips them.

## 2026-10-20: `scam_check` (phase 2)

**Graph** ([app/scam_check.py](app/scam_check.py))
- Ten nodes as in the README diagram. `compute_verdict` runs once, after both branches. `@owns` stops any node from writing a key it does not own, so only `compute_verdict` writes the verdict.
- Two Groq calls per check: `detect_signals` and `explain_and_reply`. `extract_claims` is a regex: ids come from the text, and amounts come from the seller.
- Fails closed:
  - A failed signal check never gives `VERIFIED` or `SCAM_LIKELY`.
  - PayPal 403/429/5xx, timeouts and token failures are "unavailable", never "not found".
  - A bad or failed explanation falls back to fixed template sentences.
  - A database error still returns the verdict, with `persisted: false`.
- The instruction to the seller is policy text written in code. It now includes the Transaction Details address rule, the PayPal Activity hint and a "never send money" warning. `explain_and_reply` sees no buyer text, and buyer quotes never appear inside sentences.
- Differs from the README plan:
  - The webhook ledger is not read, because of RECHECK #2 and missing refund events.
  - The seller must send `expected_amount` and `expected_currency`.
  - With no ID in the chat the verdict is `WAIT`, never `SCAM_LIKELY`, since PayPal was never asked.

**Output guardrails** ([app/output_guardrails.py](app/output_guardrails.py), renamed from `grounding.py`)
- Validates sources, placeholders, a character whitelist, and digits, currencies, links and ship wording (EN + PT) outside placeholders.
- LLM sentences must be in the third person: "you" and "podes" are rejected, as are "fine to" and "free to". A word list alone let "You can proceed with the order." through.
- Every template sentence is checked against the same rules.

**API** ([app/main.py](app/main.py))
- `POST /scam-check`. At most 2 checks run at once, and the rest get 503.
- `/healthz` is now `async`, so a busy pool cannot fail Render's health check.

**Storage** ([app/db.py](app/db.py))
- `scam_cases` table: the verdict and the result, with no raw chat and no buyer quotes.
- All connections use `connect_timeout=5`.

**Tests**: 119 passing; 114 are new. The IDs are listed in the README. A mutation run, where 25 key lines were broken one at a time, failed the suite every time.

**Fixed after code review** (each reproduced before fixing)
- The "busy" check let a burst of requests through to queue. The slot is now taken with `acquire_nowait()`, so the third concurrent request gets 503.
- An empty `GROQ_API_KEY=` line, as in `.env.example`, let the app start and run every check degraded. The app now refuses to start.
- A malformed PayPal 200 body caused a 500. It now counts as "unavailable", and the verdict is `WAIT`.
- Added tests for Transaction Search failing after a capture 404, and for the 8-character quote rule.

**To do before the demo**
- Tick "Transaction Search" on the sandbox REST app, which can take up to 9 h to reach tokens. Set `GROQ_API_KEY`.
- Smoke-test one real `llm.ask` per schema, to confirm Groq strict mode accepts Pydantic's `title` keys.
- Spike: make one payment through this app's checkout, then look up both the buyer's and the seller's transaction ID with `get_capture` and `search_transaction`. Also check whether another merchant's capture id gives 404 or 403. Record the answer here.

## 2026-10-19: Foundations

Summary of what existed on 2026-10-19, written before the first commit as a snapshot of the working tree.

### Done

**Service** ([app/main.py](app/main.py))
- FastAPI app with a lifespan hook that creates the `webhook_events` table on startup.
- `GET /healthz` for Render's health check.
- `POST /webhooks/paypal`: parses the body, verifies the signature, stores the event, and returns `stored` or `duplicate`. Invalid JSON and bad signatures return 400.
- Blocking work (PayPal call, DB write) runs in the threadpool so it does not stall the event loop.

**PayPal verification** ([app/paypal.py](app/paypal.py))
- Cheap pre-check: all five `PAYPAL-*` signature headers must be present and `PAYPAL-CERT-URL` must match a `paypal.com` API host. Failures return 400 without calling PayPal.
- Signature check through PayPal's `verify-webhook-signature` endpoint.
- OAuth client-credentials token, cached in memory until 60 s before expiry.
- Network errors and PayPal 5xx raise, so PayPal retries the delivery.

**Storage** ([app/db.py](app/db.py))
- `webhook_events` table (`id`, `event_type`, `body jsonb`, `received_at`).
- `claim_event()` inserts with `ON CONFLICT DO NOTHING` and reports whether the event was new, which makes redelivery safe.

**Tests** ([tests/](tests/)): 5 passing as of today.
- U11: failed signature returns 400 and stores nothing.
- U12: the same event sent twice is stored once.
- S07 (3 cases): missing headers, a foreign cert URL, and a missing signature header are rejected before PayPal is called.

**Project setup**
- [render.yaml](render.yaml): Render blueprint with a web service and a free Postgres.
- `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `.env.example`, `.gitignore`.

### Not done yet

- Authentication and authorization (in progress).
- Anything that reads or acts on `webhook_events`. Events are stored and nothing else happens (still true after 2026-10-20: `scam_check` does not read them).
- Open review points: see [RECHECK.md](RECHECK.md).
