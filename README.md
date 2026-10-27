# Vendor Guard

Built for the PayPal AI Hackathon (deadline 12 Nov 2026, 20:00 Lisbon, PayPal Sandbox only).

[LangGraph](https://langchain-ai.github.io/langgraph/) graphs on FastAPI, deployed on Render, that protect a PayPal seller:

1. **`scam_check`: before shipping.** The seller pastes a buyer's chat, email or screenshot. Vendor Guard checks the claimed payment against PayPal and returns a verdict: ship, wait, or don't ship.
2. **`dispute_defense`: after shipping.** A `CUSTOMER.DISPUTE.CREATED` webhook starts a graph that gathers evidence, drafts a response and **stops for the seller's approval** before anything is sent to PayPal.
3. **`chat`: the way in.** The seller writes in plain language (LangGraph Studio's Chat tab today, the UI later). The chat answers from checked notes, runs `scam_check`, or explains disputes.

PayPal is the source of truth in both graphs, and nothing that moves money happens without the seller approving it.

> **Status: week 2 of 4 (`scam_check`).** Webhook intake works end to end. The `scam_check` graph and `POST /scam-check` are built and tested, with PayPal and the LLM mocked. They take pasted text, a screenshot, or both. Seller authentication and authorization are in progress. `dispute_defense` and the board UI are still to build. See [CHANGES.md](CHANGES.md) for what is done and [RECHECK.md](RECHECK.md) for open code-review points.

| Area | Status |
|---|---|
| Webhook intake: signature check and dedupe (`/webhooks/paypal`) | Done (tests U11, U12, S07) |
| Postgres storage, Render blueprint | Done (`render.yaml` written) |
| Seller authentication, single-use approval tokens | In progress |
| `scam_check` graph and `POST /scam-check` | Done, screenshots included (U01–U05, U09–U10, U17–U20, N01–N04, S01–S06) |
| Live sandbox demo (`/demo`): sell, pay as a test buyer, check the chat, ask the chat, watch every step | Built, and run locally against the sandbox with a real payment (D01–D17). Needs a Render deploy |
| `chat` graph and `policy_pack` | Done in Studio and on the demo page through `POST /demo/api/chat` (C01–C17, U16, E06, D12–D17). No endpoint outside the demo until auth lands |
| `dispute_defense` graph, approval screen, Bryntum board | Planned, 1–7 Nov |
| Evaluation set, published numbers, demo video | Planned, 8–12 Nov |

## Trust boundaries

Everything that comes from the buyer (chat, email, screenshot, dispute messages) is **untrusted data**. The LLM has **no credentials and no tools**. Only the `execute_action` node writes to PayPal, and only after the seller approves. The one exception is the live demo (`DEMO_MODE`, sandbox only): code outside the graphs creates and captures orders there, to play the buyer's purchase.

```
Seller UI ──► FastAPI (Render) ──► LangGraph ──► PayPal REST APIs
 paste chat     /scam-check            │           payments, transaction search,
 approve        /disputes/{id}/decision├──► LLM     disputes, tracking,
                /webhooks/paypal       │   (structured output, no tools)
 PayPal ──────► verify → dedupe        └──► Postgres
 Sandbox         DISPUTE.* CAPTURE.*        checkpoints · evidence vault · audit_log
                                     Cron job: deadline_watch, hourly
```

## Graph 1: `scam_check`

```mermaid
flowchart TD
  A[ingest_input] --> R[read_screenshot] --> B[sanitize_untrusted]
  B --> C[detect_signals]
  B --> D[extract_claims] --> E[verify_payment]
  C --> F[compute_verdict]
  E --> F
  F --> G[explain_and_reply] --> H{validate_output}
  H -- ok --> I[persist_case]
  H -- fail --> J[safe_template] --> I
```

`read_screenshot` runs only when the seller sends an image. A Groq vision model (`qwen/qwen3.8-27b`) copies its text and labels chat bubbles `Buyer:` (left) or `Seller:` (right). It writes `[?]` for words it can't read instead of guessing them. The copy is untrusted buyer text. It is joined to any pasted text and sanitised like a paste, so every later node works as before. If the copy fails, there is no verdict and no case: the API answers 422 and asks for another try or the pasted text.

A copy can still be wrong, so the quote check confirms a quote against the copy, not against the image. That cannot approve a payment, because PayPal still has to find the ID. A misread ID gives `WAIT`. The worst cases are a false warning, or a missed warning on a real payment, where the address rule is shown anyway.

`extract_claims` takes up to three 17-character transaction IDs from the chat, using a regex and no LLM. `verify_payment` tries each one against the capture API (`GET /v2/payments/captures/{id}`), then Transaction Search (which can lag by hours).

The webhook-fed ledger is not consulted yet. Stored bodies are only CRC32-bound (RECHECK #2), and refund events don't carry the capture ID, so a ledger hit could approve a refunded or forged payment.

A PayPal 403, 429, 5xx, timeout or token failure counts as "unavailable", never as "not found". The amount is compared only to the seller's `expected_amount` and `expected_currency`, never to what the buyer claims.

`compute_verdict` is a pure function with no LLM, and it is the only node that writes `state.verdict`.

| Payment (API) | Signals | Verdict | Message to seller |
|---|---|---|---|
| Found, `COMPLETED`, amount matches | none (checked) | `VERIFIED` | You can ship. Ship only to the address on this payment's Transaction Details page in PayPal. |
| Found, `COMPLETED`, amount matches | any, or the check failed | `VERIFIED_WITH_WARNINGS` | Real payment. Ship only to the address on its Transaction Details page in PayPal, and don't refund differences outside PayPal. |
| Found, `DECLINED`, `FAILED`, `REFUNDED`, `PARTIALLY_REFUNDED` or `REVERSED` | any | `WAIT` | Don't ship. This payment was declined, failed or refunded. |
| Found, `PENDING`, or amount differs | any | `WAIT` | Don't ship yet. |
| Not found | ≥ 2 confirmed | `SCAM_LIKELY` | Don't ship the item unless the payment shows as Completed in your PayPal Activity. |
| Not found otherwise, API unavailable, or no ID in the chat | any | `WAIT` | Not confirmed. Check your PayPal Activity for this payment before you ship. |

The table is applied top-down. When the chat asks the seller to send money, pay a fee or follow a payment link, the message also says: "Never send money, pay a fee or enter card details to receive a payment."

"Not found" alone never means scam. PayPal gives the buyer and the seller different transaction IDs for the same payment ([PayPal Help](https://www.paypal.com/us/cshelp/article/why-are-there-different-buyer-and-seller-transaction-ids-for-the-same-transaction-ts2265)), so the ID a genuine buyer quotes is often unknown to the capture API, and Transaction Search can lag. Activity search in PayPal does find the buyer's ID.

`detect_signals` looks for nine closed patterns: overpayment refund, fake payment proof, money held until tracking, third-party shipping, upfront fee, urgency, moving off-platform, unprotected payment (Friends and Family, gift cards, MB WAY requests) and payment links.

Each hit carries the buyer's exact words. A hit is *confirmed* only when those words are really in the chat, compared on letters and digits only (casefold, unaccented). An unconfirmed hit still blocks `VERIFIED` but never counts toward `SCAM_LIKELY`.

## Graph 2: `dispute_defense`

```mermaid
flowchart TD
  W[webhook DISPUTE.CREATED] --> L[load_dispute] --> C{classify_case}
  C -- out of scope --> N[notify_manual]
  C --> P[load_policy]
  P --> T[fetch_transaction]
  P --> K[fetch_tracking]
  P --> H[fetch_history]
  T & K & H --> S[score_evidence] --> D[draft_response] --> V{validate_draft}
  V -- fail, max 2x --> D
  V -- ok --> A{{human_approval · interrupt}}
  A -- edit --> V
  A -- approve --> X[execute_action] --> U[audit_log]
  A -- reject --> U
```

- `thread_id` is the dispute id. The graph can wait days for the seller and resume from its Postgres checkpoint.
- Reason codes map fixed: `MERCHANDISE_OR_SERVICE_NOT_RECEIVED` → INR, `..._NOT_AS_DESCRIBED` → SNAD, `UNAUTHORISED` → UNAUTH. Anything else goes to `notify_manual`.
- `score_evidence` for INR (0–100): verifiable delivered tracking 40, address matches the transaction details page 25, tracking linked to the transaction 15, shipped before the dispute 10, conversation saved 10. At 70 or above it recommends contesting. For SNAD it marks `seller_protection=false`.
- `human_approval` uses `interrupt()` and issues a single-use approval token bound to the draft's hash. `execute_action` checks the token and the dispute's HATEOAS link, then calls `provide-evidence`, `make-offer` or `accept-claim` with a `PayPal-Request-Id` so a retry never double-sends.

## Graph 3: `chat`

```mermaid
flowchart TD
  M[seller message] --> R{route_in: no LLM}
  R -- waiting for the price or the chat --> K[check]
  R -- "check", or looks like a pasted chat --> S[start_check]
  R -- otherwise --> C[classify: LLM]
  C -- question --> Q[question]
  C -- scam_check --> S
  C -- dispute --> D[dispute]
  C -- other or not_paypal --> O[other]
```

One Groq call per message, `classify`, picks closed choices only: an intent (`question`, `scam_check`, `dispute`, `not_paypal` or `other`) and up to 3 keys of verified `policy_pack` notes.

- **What it sees:** only the current message, sanitised and inside `<question>` tags, and the titles of the verified notes.
- **What it never sees:** earlier messages, the buyer's chat, a check's result or search results.
- **What it can't do:** it has no tools, writes no text the seller reads and stores no memories.

Every reply is written in `app/chat.py`, by `scam_check`, or comes from PayPal (a page title and its link). The replies are in English.

- **question:**
  - Verified notes are shown word for word, with their source.
  - If no note fits, Tavily searches PayPal's US pages, and the chat lists up to 3 titles and links that start with `https://www.paypal.com/us/` (filtered in code).
  - If the search finds nothing, fails, or there is no key, the reply is "I don't have that information" and PayPal's US business help link.
- **scam_check:**
  - The code asks for the price ("25.00 USD"), reads it with a regex and validates it like `POST /scam-check`. Then it asks for the buyer's chat.
  - That next message goes to `scam_check.run()` exactly as typed, with no classification and no search. "cancel" stops at either step.
  - The reply is the verdict, the message and the sentences, never the evidence, which holds the buyer's quotes.
  - Instead of the chat, the seller can send a screenshot of it (PNG or JPEG, as a base64 `data:` URL in an `image_url` part, checked by its first bytes) with or without text. It goes to `scam_check.run()` like a screenshot sent to `/scam-check`, and counts as a check and a screenshot on the demo. A screenshot sent at any other step is never read: it starts the check, like a pasted chat. If it can't be read, the seller is asked again.
- **dispute:** fixed text, plus the verified dispute notes. Once `dispute_defense` exists, this is where the chat will ask for the dispute ID.
- **other:** what Vendor Guard does and how to start a check. The word `notes`, sent alone, gets the titles of the verified notes instead, with no `classify` call.
- **not_paypal:** a check or a dispute that isn't on PayPal (a bank transfer, another app, another marketplace) goes to the same node, which says Vendor Guard only works with PayPal.

Things that are easy to get wrong:

- **Pasted chats.** A message with a 17-character ID, `Buyer:`/`Seller:` lines or more than 400 characters starts a check instead of being classified, so it reaches neither the LLM nor Tavily. A short buyer line with neither can't be told apart from a question.
- **Isolated check.** `scam_check.run()` runs in an empty `contextvars.Context()`. Called directly inside a node, LangGraph treats it as a subgraph: it would save `scam_check`'s state, quotes included, in the chat thread's checkpoints and stream it to Studio.
- **Input.** The graph's input is `messages` only, so a client can't set the price or the intent. The router on `START` still reads the raw input: a crafted `awaiting` reaches `check`, which then asks for the price.
- **One request.** `classify` makes a single request (`llm.ask(..., attempts=1)`). On any failure the seller gets the introduction and nothing is searched. The message with the buyer's chat makes no `classify` call; `scam_check.run()` makes its usual two.
- **Prompt detail.** Without "Reply with the JSON object only." at the end of the prompt, `gpt-oss-120b` sometimes wrote its reasoning as the answer, and Groq returned a 400 (`output_parse_failed`). That happened in 5 of 8 tries on one message.
- **No API yet.** The chat has no endpoint. When it gets one, a message that runs a check must take the same `SCAM_LIMIT` slot as `POST /scam-check`.

### Tavily

`TAVILY_API_KEY` is optional. The call is `POST https://api.tavily.com/search` with httpx:

- a 5 s timeout and no retry; any error gives the help link;
- `search_depth: basic`, which costs 1 credit (the free plan has 1 000 a month);
- `include_domains: ["www.paypal.com"]`;
- no LLM answer and no page text.

The query is only the seller's current message, after this cleanup:

- sanitised;
- without self-introductions ("I'm Amanda"), emails, links, phone numbers or 17-character IDs;
- cut to 400 characters.

Other names stay in the query (RECHECK #20).

What Tavily does with queries, according to its own pages:

- The [Privacy Policy](https://tavily.com/privacy) (24 Nov 2025) says Tavily:
  - keeps query data "for the period necessary to provide and improve the Services";
  - may use parts of it to improve future responses;
  - may share it with third-party search index providers such as Google;
  - asks users not to put personal information in queries.
- The [Platform Terms](https://www.tavily.com/terms) (4 May 2026, §9.2) give Tavily a perpetual, irrevocable licence over queries.
- No DPA is published, and zero data retention is not in the standard terms.
- **Turn off "Allow Use of Query Data"** in the Tavily account settings: it stops queries from being stored or used for improvements.
- Tavily is a US company (AlphaAI Technologies Inc., New York). Nebius announced an agreement to acquire it on 10 Feb 2026.

The app starts without the key, unlike `GROQ_API_KEY`:

- Without Groq, every check silently loses signal detection and the explanation, and every chat message fails.
- Without Tavily, only a question with no note loses the search, and its answer is the same honest "I don't have that information" that a Tavily outage gives.
- Refusing to start would take down the webhook and `/scam-check` for an optional feature. `app/chat.py` logs a warning when the key is missing.

## How hallucinations are prevented

The LLM chooses the words and the code supplies the facts.

1. **Closed context.** Each call sees only this case's evidence and the one `policy_pack` entry chosen by `load_policy`. No web text reaches the LLM, and no other cases.
2. **Sentences with sources.** Output is a list of `Sentence` (closed `intent`, at least one source: `E…` for evidence, `P.…` for policy).
3. **Facts by placeholder.** The model writes `{{E2.delivered_at}}`, never a date. The code fills in the real value, and policy text is inserted verbatim by `policy_id`.
4. **Deterministic validation.** Every source must exist, every placeholder must belong to a cited source, and no numbers, dates, emails, URLs or phone numbers may appear outside placeholders. Banned phrases ("guaranteed", "you will win", legal or tax advice, other platforms) are rejected. The sentence about the payment always comes from code, a fixed template that shows the status and both amounts, because a grounded sentence could still contradict the verdict ("{{E1.amount}}, in line with {{E1.expected_amount}}"). The model sees only the warning signs and writes 1 to 3 sentences about them. With no warning signs, it isn't called.
5. **Abstaining is valid.** Missing proof goes in `missing` with an `ADMIT_MISSING` sentence. Inventing it is a failure.
6. **Safe fallback.** Two failures in a row fall back to a fixed template per verdict or reason, with no LLM, and the case is flagged for review.

Fields in the LangGraph state are owned by the node that writes them (an `@owns(...)` decorator), so an LLM node cannot write the verdict.

`app/policy_pack.py` is a Python dict of notes, looked up by key and never by similarity.

- Each entry has `title`, `text`, `source_url`, `retrieved_at` and `verified`.
- An entry is shown, or offered to the LLM, only once a person has read its source page and set `verified: True` (test U16).
- There are two kinds of entry:
  - **Vendor Guard notes:** the verdicts, why WAIT, how to ask for a check. They are built from `scam_check`'s own texts and are verified.
  - **PayPal notes:** 17 of them, each from one `https://www.paypal.com/us/` page, with the page's sentences word for word. The only edit is "we" → "PayPal". All are verified (2026-10-22). Two quote only part of a list of requirements (`pp_seller_protection`, `pp_proof_of_delivery`), so they end with a sentence of Vendor Guard's own: "This is not PayPal's full list", with the page's link.
- The region is set once, in `REGION`.
- No PayPal US page says refunds must go only through PayPal, so the refund note says only how to refund in PayPal. Response deadlines are given only where a US Help Center page states them (10 days for a dispute). Seller Protection depends on region, and for the demo it assumes a US sandbox business account. It does not cover "Significantly Not as Described", so the agent never promises protection on a SNAD dispute.

## Live sandbox demo (`/demo`)

A public page where anyone can try Vendor Guard against PayPal's sandbox, and see what it does:

1. **Make a sale**: an item and a price in USD (1–1 000).
2. **Pay as the test buyer**: PayPal's own checkout (JS SDK) with a shared sandbox buyer account, whose email and password the page shows. The server creates the order and captures it after approval.
3. **Check the buyer's chat**: start from an honest buyer, an overpayment, a fake PayPal email or a prompt injection, edit it, or add a screenshot.

Below the steps, **Ask Vendor Guard** runs the `chat` graph: questions answered from the verified notes, "check" to verify a payment over three messages, disputes, and a "PayPal only" reply for other services. Each demo session has its own conversation, kept in memory (`InMemorySaver`, thread id = session id) and forgotten when the session ends or the service restarts. The live panel shows how each message was routed: the intent and notes the model picked, or that the code routed it with no model call. At the chat step, the Screenshot button sends the buyer's chat as an image instead of text.

Each paid sale has "Buyer's receipt" and "Seller's receipt" buttons: PNGs of the real sandbox capture, one for each side, drawn in the browser and labelled as the demo's, to try screenshots with. With a screenshot and no price, step 3 reads the amount from the screenshot and fills it in for you to confirm: it never checks with it unconfirmed, since a receipt shows what was paid, not what you asked.

Next to the steps, a live panel shows each `scam_check` node as it runs and what it wrote, every PayPal call with its HTTP status and response body, and the verified webhooks about the tester's sales. The panel shows the tester what they sent (the cleaned chat, the screenshot's text, the quotes), which `/scam-check` never returns.

- **Anonymous sessions.** The page keeps the session id in `sessionStorage`, so closing the tab forgets it and a reload keeps it. "End demo" deletes the session at once, and a session idle for 30 minutes is deleted at the next new session or at boot. Deleting removes its sales, cases, claimed payments and the webhook events about its sales. The payments stay in PayPal's sandbox, which can't delete them.
- **Limits per IP, per hour:** 10 checks (3 with a screenshot), 10 sales, 20 sessions, 30 chat messages. The same check limit applies to `/scam-check`, and a chat message that runs a check counts as one and needs a free slot (503 when busy). Counters are in memory, so they reset when Render sleeps.
- **Off by default.** Only `DEMO_MODE=true` turns it on, and then the app refuses to start unless `PAYPAL_BASE_URL` is the sandbox and `DEMO_BUYER_EMAIL` and `DEMO_BUYER_PASSWORD` are set.
- Order creation and capture are the app's only PayPal writes outside `execute_action`. They live in `app/demo.py`, never in a graph. A capture is sent with `PayPal-Request-Id: capture-<order id>`, so a retry can't capture twice, and only for an order of the caller's session.
- Groq's free tier fits about 2 checks a minute in total. With several testers at once, checks fail closed (signals unknown, the fixed template) and the panel says so.

## Interface (planned)

- **Dispute board** in Bryntum Scheduler: one row per open dispute, colored by graph state, with a diamond at `seller_response_due_date`. The agent adds an "approve by" block 48 h before the deadline, and `deadline_watch` turns anything under 24 h red. Board events are written by code from graph state, never by the LLM.
- **Scam checker**: paste a chat or drop a screenshot, get the verdict with cited sentences. Built for now as the demo page (`/demo`).
- **Approval screen**: the draft, its evidence with IDs, and three buttons: approve, edit, reject.
- FastAPI serves the pages and `GET /api/board`. Jinja2, HTMX and vanilla JS, no React.

## Endpoints

| Endpoint | Status | Response |
|---|---|---|
| `GET /healthz` | Built | `{"ok": true}` |
| `POST /webhooks/paypal` | Built | `{"status": "stored"}` or `{"status": "duplicate"}`. 400 on invalid JSON or a bad signature. A 5xx if PayPal is unreachable, so PayPal retries. |
| `POST /scam-check` | Built | Body `{text, image, expected_amount, expected_currency}`. Send `text`, `image` or both. `image` is a base64 PNG or JPEG with no `data:` prefix, up to 3 MB. The chat may be up to 8 000 chars after sanitising, counting the screenshot's text. Returns `{case_id, verdict, message, sentences, evidence, template, signals_checked, persisted}`, never the chat or the screenshot's text. 422 on bad input or a screenshot that can't be read, 429 over 10 checks (3 with a screenshot) an hour per IP, 503 when two checks are already running. Not authenticated yet (RECHECK #3). |
| `GET /demo` | Built, `DEMO_MODE` only | The demo page. Every `/demo*` route is 404 without `DEMO_MODE=true`. |
| `POST /demo/api/session` | Built, `DEMO_MODE` only | `{session_id, client_id, currency, buyer_email, buyer_password}`. The other demo routes need the id in `X-Demo-Session`, and answer 401 once the session ended or went idle. |
| `POST /demo/api/orders`, `POST /demo/api/orders/{id}/capture` | Built, `DEMO_MODE` only | Create a USD order for `{item, amount}`, then capture it: `{capture_id, status, amount, currency}`. 404 for another session's order, 502 when PayPal refuses. |
| `POST /demo/api/check` | Built, `DEMO_MODE` only | The `/scam-check` body. Streams NDJSON lines: `paypal` (method, path, status, body), `node` (name, update), then `result` (as `/scam-check`) or `error`. |
| `POST /demo/api/chat` | Built, `DEMO_MODE` only | Body `{message, image}`, a message up to 8 000 chars, a screenshot (as `/scam-check`'s `image`) or both. One message to the `chat` graph on the session's thread: `{reply, route, intent, notes, awaiting}`. `route` is the node that answered; `intent` and `notes` are set only when the model classified the message. A message that runs a check counts as a check, and as a screenshot if it has one. 503 when the message would run a check and two are running, 409 while the session's previous message is still running. |
| `GET /demo/api/state`, `POST /demo/api/end` | Built, `DEMO_MODE` only | The session's sales and the webhook events about them; ending deletes the session and all it left (409 while a chat message is running). |
| `POST /disputes/{id}/decision` | Planned | Approve, edit or reject a draft |
| `GET /api/board` | Planned | Only the authenticated seller's disputes, with no buyer messages or contacts |

### How the webhook endpoint works today

1. The five `PAYPAL-*` signature headers must be present and `PAYPAL-CERT-URL` must be a `paypal.com` API host, otherwise 400 without calling PayPal.
2. The event is sent to PayPal's `verify-webhook-signature` endpoint. Anything but `SUCCESS` is a 400.
3. The event is inserted into `webhook_events` with `ON CONFLICT DO NOTHING`, so a redelivery is acknowledged but processed once.

## Layout

```
app/main.py         FastAPI app, /healthz, /webhooks/paypal, /scam-check, the demo's streamed check and chat
app/demo.py         the live demo's routes: anonymous session, sandbox orders and captures, state, end; the chat's thread per session
app/ratelimit.py    per-IP hourly limits for the open endpoints
app/static/demo.html  the demo page: plain HTML, CSS and JS, no build
app/paypal.py       signature-header checks, OAuth token cache, verify-webhook-signature, capture and Transaction Search lookups
app/db.py           schema bootstrap, claim_event() (idempotent insert), save_case(), the demo's tables and cleanup
app/scam_check.py   the scam_check graph: @owns, sanitize, the 11 nodes, verdict table, policy texts
app/chat.py         the chat graph: route_in, classify, the check flow, the Tavily search
app/policy_pack.py  the notes the chat shows: Vendor Guard's and PayPal's, with sources and `verified`
app/output_guardrails.py  Sentence model, validate() and render() for LLM-written sentences
app/llm.py          the Groq calls, no tools: ask() with a strict JSON schema, read_image() for screenshots
tests/              pytest + respx (PayPal and Groq are mocked, no database needed); tests/test_db.py runs the real SQL
langgraph.json      serves the scam_check and chat graphs to `langgraph dev` and LangGraph Studio
render.yaml         Render blueprint: the web service (Postgres is on Neon, set by DATABASE_URL)
```

## Stack

| Layer | Tool | Installed today |
|---|---|---|
| Core | Python 3.12, FastAPI, Uvicorn, httpx, psycopg | yes |
| Core | LangGraph, Pydantic v2, Groq (`openai/gpt-oss-120b`, strict JSON-schema output; `qwen/qwen3.8-27b` reads screenshots, a preview model) | yes |
| Core | `langgraph-checkpoint-postgres` | planned |
| Search | Tavily Search API through httpx, no SDK (`chat` only) | yes, optional |
| PayPal | Sandbox (US test accounts): Payments v2, Transaction Search, Disputes v1, Tracking, `verify-webhook-signature` | webhook verification only |
| Infra | Render (web service, cron job), Neon Postgres, cloudflared or ngrok for local webhooks | blueprint written |
| Interface | Bryntum Scheduler (trial), Jinja2, HTMX, vanilla JS | planned |
| Tests | pytest, respx, Pillow (draws the eval screenshots); pytest-asyncio, RAGAS and promptfoo later | pytest, respx, Pillow |
| DevSecOps | GitHub Actions, Bandit or Semgrep, pip-audit | planned |
| Observability | LangSmith (`@traceable` on the Groq calls, EU region, off unless `LANGSMITH_TRACING=true`, made-up chats only) | yes, optional |

## Configuration

Copy `.env.example` to `.env` (it is gitignored).

| Variable | Notes |
|---|---|
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | Sandbox REST app credentials from developer.paypal.com |
| `PAYPAL_WEBHOOK_ID` | ID of the webhook registered in PayPal. It needs a public URL. |
| `PAYPAL_BASE_URL` | Defaults to `https://api-m.sandbox.paypal.com`. Set to `https://api-m.paypal.com` for live, in `.env` and `render.yaml`. |
| `DATABASE_URL` | Postgres connection string. The tables are created on startup. |
| `GROQ_API_KEY` | From console.groq.com. Required: the app does not start without it. The free tier (8K tokens/min) fits about 2 checks a minute. |
| `LLM_MODEL` | Defaults to `openai/gpt-oss-120b`. Must be a Groq model with strict JSON-schema output. |
| `TAVILY_API_KEY` | From app.tavily.com. Optional: without it the app starts, and a chat question with no note gets PayPal's help link. Turn off "Allow Use of Query Data" in the Tavily account (see Graph 3). |
| `DEMO_MODE` | `true` turns on the live demo (`/demo`). Sandbox only: the app refuses to start with it against live PayPal. Off when unset. |
| `DEMO_BUYER_EMAIL`, `DEMO_BUYER_PASSWORD` | A US sandbox personal account that testers log in with at PayPal's checkout. The page shows both. Required with `DEMO_MODE`. Keep a second account ready: a tester can change this one's password. |
| `VISION_MODEL` | Defaults to `qwen/qwen3.8-27b`, which reads screenshots. Must be a Groq model with image input that accepts `reasoning_effort="none"`. It is a preview model and can be withdrawn at short notice. A screenshot costs about 2 000 tokens of its own per-minute limit, separate from `LLM_MODEL`'s. |

Set `LANGSMITH_TRACING=true` only while testing with made-up chats: it sends every chat and LLM reply to LangSmith. Never set it on Render or with real cases. For `/scam-check` to find payments by ID, tick "Transaction Search" on the sandbox REST app. A new permission can take up to 9 hours to reach new tokens.

## Run locally

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
docker run -d -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres   # matches .env.example
uvicorn app.main:app --reload --env-file .env
```

PayPal needs a public URL to deliver webhooks, so use a tunnel (cloudflared or ngrok) and register `https://<tunnel>/webhooks/paypal` in the PayPal dashboard. Events from the webhook simulator do not pass signature verification, so test with real sandbox payments.

### Try `scam_check` in LangGraph Studio

```sh
langgraph dev --studio-url https://eu.smith.langchain.com   # the LangSmith account is in the EU region
```

This opens Studio on the `scam_check` graph (log in to LangSmith). Paste this into the input, in JSON mode:

```json
{"text": "Buyer: Já paguei, o ID é 3Y662965014333303", "expected_amount": "25.00", "expected_currency": "EUR"}
```

Studio shows each node's state, and you can pause before any node. The things to know:

- Studio runs the graph directly, so the `/scam-check` validation and the limit of 2 checks at a time do not apply.
- Screenshots can't be tried here: `image` is bytes and Studio sends JSON. Use `POST /scam-check` or `pytest -m eval`.
- `verify_payment` and `persist_case` need Postgres with its tables, which are created when `uvicorn` first starts. Without them, a real payment gives `WAIT` ("not confirmed").
- An ID that already approved a case gives `P.WAIT_REUSED` the next time. To try it again, run `psql "$DATABASE_URL" -c "TRUNCATE claimed_payments"`.
- Groq and PayPal Sandbox are called for real. With `LANGSMITH_TRACING=true` every run is traced, so use made-up chats only.

### Try `chat` in LangGraph Studio

Pick the `chat` graph and switch to Chat mode. Each message is one run on the thread. Try:

1. "Hi, I am Amanda and I have a few questions regarding a possible scam, can you help me?"
2. "What does WAIT mean?"
3. "check", then "25.00 USD", then a made-up chat.
4. "check", then "cancel".
5. "I got a dispute".

- The buyer's chat you paste stays in the thread's history, saved in `.langgraph_api/`. Deleting the thread in `langgraph dev` leaves the messages on disk. To wipe them, stop the server and delete `.langgraph_api/` (RECHECK #19).
- Text attachments (.txt, .md, .json) arrive as text, so a chat export works. Images at the chat step go to the check if they arrive as an `image_url` data URL; in any other form they get "I can only read text here for now". Not tried in Studio yet.
- The 2-checks-at-a-time limit doesn't apply in Studio, as with `scam_check`.

## Tests

```sh
pytest              # offline: PayPal and Groq mocked
pytest -m eval -v    # evals: the real Groq model, key from .env, made-up chats only
```

The plan has 61 test cases in six layers, plus C01–C17 for the chat and D01–D17 for the demo, tracked by ID:

| Layer | IDs | What it covers | Where it runs |
|---|---|---|---|
| Unit | U01–U21 | `compute_verdict`, `classify_case`, `score_evidence`, `@owns`, webhook, `execute_action`, `validate_draft` | CI, PayPal mocked |
| Node (mock) | N01–N10 | LLM and API nodes against simulated PayPal errors (429, 503, `ERRDIS…`) | CI |
| Sandbox | G01–G12 | End to end on PayPal Sandbox, including resume after restart | manual scripts |
| Security | S01–S11 | Prompt injection, forged webhooks, token replay, no tools on LLM nodes | pytest + promptfoo |
| Chat | C01–C17 | `app/chat.py`: routing, the check flow, screenshots, Tavily, links, personal data | CI, Groq and Tavily mocked |
| Demo | D01–D17 | `/demo`: off by default, sandbox only, sessions, orders, the streamed check, the chat, per-IP limits | CI, PayPal and Groq mocked |
| Evaluation | E01–E05 | 60 labelled chats, 20 sandbox disputes, 30 injection variants, 50 drafts | scripts + RAGAS |
| Spike | P01–P02 | Which payment source feeds the ledger, and whether simulator events verify | manual |

**Passing today:**

- Webhook: U11 (a failed signature returns 400 and stores nothing), U12 (the same event twice is stored once) and S07 (a forged webhook is rejected before PayPal is called).
- `scam_check`:
  - U01–U05: the verdict table.
  - U09–U10: `@owns`.
  - U17–U20: the output guardrails (tests/test_output_guardrails.py).
  - Evals (`pytest -m eval`): 3 injection chats run through the whole graph must not put buyer text in the reply (tests/test_eval_output_guardrails.py).
  - E07 (tests/test_eval_seller.py, `pytest -m eval -s`): 35 made-up conversations of a seller with Vendor Guard, in tests/seller_dataset.jsonl, in English. Each one has 1 to 4 turns.
    - The scenarios are questions about PayPal, suspicions described in words, a check run in the chat followed by questions about its result, and disputes, other payment services, small talk and injections.
    - PayPal is mocked and Tavily is off. The chat graph runs with the real Groq model.
    - Each turn is scored by rules: the node that answered, the notes it had to show, and the verdict.
    - An LLM judge then scores each whole conversation against a reference written from `policy_pack`'s notes: correct, next steps, safe, covers the reference, and 1–5. The judge is the app's own model, so read the conversations too.
    - Each run saves every conversation to tests/eval_results/, with the KPIs per rule, per criterion and per scenario. The first run (15/35 passing, judge mean 3.89) is kept in tests/eval_baselines/ to compare later runs against.
  - Screenshot evals (tests/test_eval_screenshot.py): drawn PT and EN chats, one with an injection, must be copied word for word with the ID and the right `Buyer:`/`Seller:` labels. A blurred chat must not be guessed, and a screenshot alone run through the whole graph must give `SCAM_LIKELY`.
  - Screenshots, offline: a screenshot alone, a screenshot plus text, bad uploads, a failed copy (no verdict, no case, PayPal not called), and no tools or secrets in the vision call (S03).
  - N01–N04: PayPal 404, 403, 429, 503, timeouts, token failure, Transaction Search rows, LLM and database failures.
  - S01–S06: prompt injection, unverifiable quotes, no tools and no secrets in prompts, no ship wording from the LLM, Unicode and tag tricks.
- `chat` (tests/test_chat.py):
  - C01–C17: every intent, the price and the chat flow, a screenshot at the chat step, and cancel.
  - The buyer's chat never reaches the chat's LLM or Tavily, and no quotes appear in the reply or in the thread.
  - Pasted chats, the tag injection, the link filter, Tavily failures, personal data in the search, one Groq request per message, no tools or secrets.
  - U16: the `policy_pack` sources, and that unverified notes are never offered or shown.
  - E06 (tests/test_eval_classify_chat.py, `pytest -m eval -s`): the 39 cases in tests/chat_dataset.jsonl, all in English, the real Groq model. Each run saves every reply and the KPIs (routing, intent, notes shown, LLM calls) to tests/eval_results/, which git ignores. Each call uses about 830–890 tokens, about 1,600 for a dispute. No SDK retries, as in the app: a 429 waits 60 s and tries once more.
- Demo (tests/test_demo.py): D01–D17. The routes are 404 when off, the app won't start against live PayPal, another session's order is never captured, the stream lists every node and PayPal call (no token) and ends with the same verdict as `/scam-check`, a busy check is an error line and every check gives its slot back, and the per-IP limits ignore forged `X-Forwarded-For` entries. The chat answers from a verified note, runs a check over three messages on its own session's thread (counted as a check, its case tied to the session), refuses that message with 503 when no slot is free, takes one message at a time per session (409), counts a chat screenshot as a check and a screenshot, and forgets the thread when the session ends or is swept.

The tests mock PayPal and Groq and replace the database calls, so they need neither credentials nor Postgres. `tests/test_db.py` runs the real SQL (the claims, saving a case, the demo's tables and its cleanup) against a throwaway Postgres, and is skipped unless `TEST_DATABASE_URL` is set:

```sh
docker run -d --rm --name vg-test-pg -e POSTGRES_PASSWORD=test -p 55432:5432 postgres
TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres pytest tests/test_db.py
```

Test IDs follow this README's grouping; there is no separate test-plan document.

## Build order

| Dates | Phase | Covers |
|---|---|---|
| 17–24 Oct | Foundations: FastAPI, Postgres, Render deploy, sandbox OAuth, webhook verification and dedupe | P01–P02, U11–U12 |
| 25–31 Oct | `scam_check`: nodes, verdict policy, paste UI, output guardrails | U01–U05, U09–U10, U17–U20, N01–N04, G01–G03, S01–S06 |
| 1–7 Nov | `dispute_defense`: fan-out, scoring, draft, `interrupt()`, approval screen, verified `policy_pack`, board | U06–U08, U13–U16, U21, N05–N10, G04–G12, S03, S07–S11 |
| 8–12 Nov | Run the evaluation set, publish the numbers, record a real demo | E01–E05 |

## Deploy

`render.yaml` defines the web service only; the database is a Neon Postgres. Set `DATABASE_URL` (Neon's direct connection, not the pooler), `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID` and `GROQ_API_KEY` in the Render dashboard (they are `sync: false`). `TAVILY_API_KEY` is optional and not in the blueprint, so add it by hand. For the live demo, also set `DEMO_MODE=true`, `DEMO_BUYER_EMAIL` and `DEMO_BUYER_PASSWORD`, and subscribe the webhook to `PAYMENT.CAPTURE.*` and `CHECKOUT.ORDER.APPROVED` (and `CUSTOMER.DISPUTE.*` for later). Dispute creation, `require-evidence` and `adjudicate` exist only in the sandbox. Always check a dispute's HATEOAS links before calling an action.
