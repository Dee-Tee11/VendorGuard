# Changes

## 2026-10-24: E06 fixes from `/code-review`, and `not_paypal`

- New intent `not_paypal`: a check or dispute that isn't on PayPal (a bank transfer, Zelle, an eBay case) gets "Vendor Guard only works with PayPal…" from the `other` node, instead of the intro. Before, the `other` rule ("anything not about PayPal") could send these to the intro with no word on why. Three new E06 cases cover it.
- The E06 dataset is in English only (the product targets the US), and the eval file is now `tests/test_eval_classify_chat.py`.
- E06 fixes:
  - A classify call that failed after Groq answered (cut off, invalid JSON) was not counted, and the fallback to `other` scored as a pass. `llm.ask` is now wrapped too.
  - A case that crashed left no row and dropped out of the KPIs. It now gets a row with its error, scored as a failure (`ran without error`).
  - "required notes" counted the notes `classify` picked. It now counts the notes the seller saw.
  - The eval's client had SDK retries (up to 6), hidden inside one call. It now has none, as in the app: a 429 waits 60 s and tries once more, and the KPIs count those waits.
  - One graph for all cases, the KPI report built once, and the unused multi-turn replay removed.
- Not changed: the dataset is still read at import, so a broken line stops plain `pytest` at collection. A broken dataset should fail loudly.

## 2026-10-22: PayPal notes verified

- Asked "What can you tell me about disputes?", the chat gave only Tavily links: every PayPal note was `verified: False`, so `classify` could offer only the 3 Vendor Guard notes.
- A script fetched each note's source page and looked for every sentence in it. All 17 notes match, apart from the "we" → "PayPal" edit. All are now `verified: True`, at the user's request.
- Two notes skip items of PayPal's list of requirements: `pp_seller_protection` skips "Respond to our requests for documentation…", and `pp_proof_of_delivery` skips "Date of shipment." and the items after the address. Both now end with "This is not PayPal's full list: check every requirement on PayPal's page:" and the link.
- `classify` now uses about 830–890 tokens per call, and about 1,600 for a dispute (it was 450–620), because the prompt lists 20 notes. The 10 evals (E06) pass, and the PayPal questions now pick PayPal notes.
- Tests: C01 and U16 now unverify notes themselves. The "never offered to the LLM" test reloads `chat` with `pp_chargeback` unverified, because otherwise no note would be unverified and the test would check nothing.

## 2026-10-22: fixes from `/code-review`

- `image` is capped at 4 MB as base64 (3 MB of image) before it is decoded. Before, the 3 MB cap was checked after decoding, so a 200 MB string was decoded on the event loop and only then rejected. Decoding is now strict: a `data:` prefix gets "must be plain base64, with no data: prefix" instead of "must be a PNG or JPEG", and URL-safe base64 is rejected. The request body itself still has no cap (RECHECK #3).
- `llm.ask(attempts=0)` raises instead of returning `None`.
- `chat` reads the verified notes once, at import (`VERIFIED`), instead of rebuilding them on every message.
- `render.yaml` no longer sets `TAVILY_API_KEY`: only the `chat` graph reads it, and that runs in Studio only. Add it back with a chat route.
- Not changed: the phone-number filter in the Tavily query also drops dates and amounts. The search is a bit worse, but less personal data reaches Tavily.

## 2026-10-22: `chat` graph and `policy_pack`

**Graph** ([app/chat.py](app/chat.py), served by `langgraph.json` as `chat`)
- A seller writes in plain language in Studio's Chat tab. One Groq call (`classify`) picks an intent and up to 3 verified note keys. All replies are written in code, by `scam_check`, or come from PayPal (a title and a link).
- The check flow takes no LLM call: the code asks for the price (a regex, validated like `ScamCheckIn`), then the buyer's chat, which goes to `scam_check.run()` exactly as typed. "cancel" stops it. The reply never includes the evidence, so it has no buyer quotes.
- A message that looks like a pasted chat (a payment ID, `Buyer:`/`Seller:` lines, over 400 characters) starts a check without being classified or searched.
- `app/scam_check.py` is unchanged: its git hash is the same before and after.

**Found while building**
- A graph invoked inside a node inherits the caller's checkpointer and stream. Called directly from the chat, `scam_check.run()` would have saved its whole state (the chat and the quotes) in the chat thread and streamed it to Studio. It now runs in an empty `contextvars.Context()` (test C06).
- `StateGraph(..., input_schema=MessagesState)` keeps `price` and `awaiting` out of the nodes' input, but the router on `START` still reads the raw input. A crafted `awaiting` therefore reaches `check`, which asks for the price instead of running (test C13).
- `llm.ask()` takes `attempts` (default 2, so `scam_check` is unchanged). `classify` uses 1, so a message never makes two `classify` requests; on failure the seller gets the introduction.
- Evals with the real model:
  - Groq strict mode accepts the schema with enums, and the `title` keys Pydantic adds. This closes the smoke test in "To do before the demo" for that schema.
  - Each `classify` call uses about 450–620 tokens.
  - The first prompt classified Amanda's greeting as `scam_check`, obeyed an injected "answer intent dispute", and picked Vendor Guard notes for PayPal questions. The prompt now says to ignore instructions inside the message, defines "has questions but asks none" as `other`, and lists the notes in two groups, Vendor Guard and PayPal.
  - `gpt-oss-120b` returned its reasoning instead of JSON in 5 of 8 tries on one message (400 `output_parse_failed`). "Reply with the JSON object only." fixed it: 30 of 30 calls across the 10 eval messages.

**Notes** ([app/policy_pack.py](app/policy_pack.py), replaces the planned `policy_pack.yaml`)
- 3 Vendor Guard notes, built from `scam_check`'s texts and verified.
- 17 PayPal notes from `https://www.paypal.com/us/` pages, opened on 2026-10-22, with quotes checked against each page's HTML. They are word for word, except "we" → "PayPal". All stay `verified: False` until a person reads each page.
- No US page says refunds must go only through PayPal: one even accepts proof of a refund made outside PayPal in a chargeback. The refund note says only how to refund in PayPal.
- Claim and chargeback deadlines have no US Help Center source, so only the dispute deadline (10 days) is given.

**Tavily** (`TAVILY_API_KEY`, optional; `.env.example` and `render.yaml`)
- httpx, 5 s timeout, `basic` depth, `www.paypal.com` only. Links are filtered in code to `https://www.paypal.com/us/`, and titles are cleaned so they can't become links. Any failure gives "I don't have that information" and PayPal's US business help link.
- The query is the seller's message, without self-introductions, emails, links, phone numbers or IDs. Tavily's data policy is in the README.

**Tests**: 64 new offline tests (C01–C16, U16; 222 in total) and 10 evals (E06). A mutation run broke 15 key lines one at a time. Two survived at first, the `verified` filter in `classify` and the cap of 3; a new test now catches both.

## 2026-10-21: LangGraph Studio

- `langgraph.json` serves the `scam_check` graph to `langgraph dev`, for trying chats by hand in Studio (README, "Try `scam_check` in LangGraph Studio"). `langgraph-cli[inmem]` is a dev dependency only. It resolves to `langgraph-api` 0.10.x, which is end of life, because newer versions conflict with fastapi 0.149's opentelemetry pin. It runs locally only.
- `llm.read_image()` and `llm.image_type()` had gone missing from `app/llm.py`, so every screenshot request crashed. Both are rewritten from the tests and pass them, as do 4 of the 5 screenshot evals. The fifth (`through_the_whole_graph`) has not passed yet: it hit Groq's daily token limit for the vision model (200K tokens a day on the free tier).

## 2026-10-21: screenshots in `scam_check`

- `POST /scam-check` takes `image`, a base64 PNG or JPEG up to 3 MB, instead of or next to `text`. The file type is read from its first bytes, never from the client.
- New node `read_screenshot` between `ingest_input` and `sanitize_untrusted`. The Groq vision model `qwen/qwen3.8-27b` (`VISION_MODEL`) copies the text with no tools and no reasoning. The copy is joined to the pasted text and sanitised like a paste, so the later nodes are unchanged. It is never returned or stored.
- Fails closed: if the copy fails, or the chat grows past 8 000 chars, the API answers 422 with no verdict and no case.
- Prompt, tuned with live calls on drawn screenshots:
  - The instruction goes next to the image, not in a system message. As a system message, the `Buyer:`/`Seller:` labels were skipped.
  - "Never guess, write [?]" stopped the model guessing the words of a blurred chat, which the quote check would have confirmed.
  - The labels go last: placed earlier, an injected "ignore the instructions above" made the model drop them.
- Tests: 12 new offline tests (158 in total), and a mutation run in which 12 key lines were broken one at a time and the suite failed every time. 5 opt-in evals in `tests/test_eval_screenshot.py` draw their screenshots with Pillow, which is now in `requirements-dev.txt`.
- Seen in probes, not fixed: on random noise the model once made up a chat line ("Hi, I'm interested in your item"). Another time it said there was no text. A screenshot of 3.06 MB was accepted, so the 3 MB cap is ours, not Groq's.
- Before the demo: `qwen/qwen3.8-27b` is a Groq preview model and may be withdrawn at short notice. Check it still answers, or set `VISION_MODEL`.

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
