# Vendor Guard

Built for the PayPal AI Hackathon (deadline 12 Nov 2026, 20:00 Lisbon, PayPal Sandbox only).

Two [LangGraph](https://langchain-ai.github.io/langgraph/) graphs on FastAPI, deployed on Render, that protect a PayPal seller:

1. **`scam_check`: before shipping.** The seller pastes a buyer's chat, email or screenshot. Vendor Guard checks the claimed payment against PayPal and returns a verdict: ship, wait, or don't ship.
2. **`dispute_defense`: after shipping.** A `CUSTOMER.DISPUTE.CREATED` webhook starts a graph that gathers evidence, drafts a response and **stops for the seller's approval** before anything is sent to PayPal.

PayPal is the source of truth in both graphs, and nothing that moves money happens without the seller approving it.

> **Status: week 1 of 4 (Foundations).** Webhook intake works end to end. Seller authentication and authorization are in progress. Both graphs and the board UI are still to build. See [CHANGES.md](CHANGES.md) for what is done and [RECHECK.md](RECHECK.md) for open code-review points.

| Area | Status |
|---|---|
| Webhook intake: signature check and dedupe (`/webhooks/paypal`) | Done (tests U11, U12, S07) |
| Postgres storage, Render blueprint | Done (`render.yaml` written) |
| Seller authentication, single-use approval tokens | In progress |
| `scam_check` graph | Planned, 25–31 Oct |
| `dispute_defense` graph, approval screen, Bryntum board | Planned, 1–7 Nov |
| Evaluation set, published numbers, demo video | Planned, 8–12 Nov |

## Trust boundaries

Everything that comes from the buyer (chat, email, screenshot, dispute messages) is **untrusted data**. The LLM has **no credentials and no tools**. Only the `execute_action` node writes to PayPal, and only after the seller approves.

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
  A[ingest_input] --> B[sanitize_untrusted]
  B --> C[detect_signals]
  B --> D[extract_claims] --> E[verify_payment]
  C --> F[compute_verdict]
  E --> F
  F --> G[explain_and_reply] --> H{validate_output}
  H -- ok --> I[persist_case]
  H -- fail --> J[safe_template] --> I
```

`verify_payment` tries, in order: the capture API (`GET /v2/payments/captures/{id}`), the webhook-fed `payment_ledger`, then Transaction Search (which can lag by hours). `compute_verdict` is a pure function with no LLM, and it is the only node that writes `state.verdict`.

| Payment (API) | Signals | Verdict | Message to seller |
|---|---|---|---|
| Found, amount matches, `COMPLETED` | 0 | `VERIFIED` | You can ship. |
| Found, amount matches, `COMPLETED` | ≥ 1 | `VERIFIED_WITH_WARNINGS` | Real payment. Don't refund differences outside PayPal. |
| Found but `PENDING`, or amount differs | any | `WAIT` | Don't ship yet. |
| Not found, or API unavailable | 0–1 | `WAIT` | Not confirmed. Wait and check again. |
| Not found | ≥ 2 | `SCAM_LIKELY` | Don't ship the item. |

"Not found" alone never means scam: Transaction Search can lag, so a real payment made minutes ago may not show yet.

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

## How hallucinations are prevented

The LLM chooses the words and the code supplies the facts.

1. **Closed context.** Each call sees only this case's evidence and the one `policy_pack` entry chosen by `load_policy`. No web search, no other cases.
2. **Sentences with sources.** Output is a list of `Sentence` (closed `intent`, at least one source: `E…` for evidence, `P.…` for policy).
3. **Facts by placeholder.** The model writes `{{E2.delivered_at}}`, never a date. The code fills in the real value, and policy text is inserted verbatim by `policy_id`.
4. **Deterministic validation.** Every source must exist, every placeholder must belong to a cited source, and no numbers, dates, emails, URLs or phone numbers may appear outside placeholders. Banned phrases ("guaranteed", "you will win", legal or tax advice, other platforms) are rejected.
5. **Abstaining is valid.** Missing proof goes in `missing` with an `ADMIT_MISSING` sentence. Inventing it is a failure.
6. **Safe fallback.** Two failures in a row fall back to a fixed template per verdict or reason, with no LLM, and the case is flagged for review.

Fields in the LangGraph state are owned by the node that writes them (an `@owns(...)` decorator), so an LLM node cannot write the verdict.

`policy_pack.yaml` holds one entry per PayPal rule, looked up by key and never by similarity. Each entry needs `source_url`, `retrieved_at` and `verified: true` before it is used (CI fails otherwise, test U16). Seller Protection depends on region, and for the demo it assumes a US sandbox business account. It does not cover "Significantly Not as Described", so the agent never promises protection on a SNAD dispute.

## Interface (planned)

- **Dispute board** in Bryntum Scheduler: one row per open dispute, colored by graph state, with a diamond at `seller_response_due_date`. The agent adds an "approve by" block 48 h before the deadline, and `deadline_watch` turns anything under 24 h red. Board events are written by code from graph state, never by the LLM.
- **Scam checker**: paste a chat or drop a screenshot, get the verdict with cited sentences.
- **Approval screen**: the draft, its evidence with IDs, and three buttons: approve, edit, reject.
- FastAPI serves the pages and `GET /api/board`. Jinja2, HTMX and vanilla JS, no React.

## Endpoints

| Endpoint | Status | Response |
|---|---|---|
| `GET /healthz` | Built | `{"ok": true}` |
| `POST /webhooks/paypal` | Built | `{"status": "stored"}` or `{"status": "duplicate"}`. 400 on invalid JSON or a bad signature. A 5xx if PayPal is unreachable, so PayPal retries. |
| `POST /scam-check` | Planned | Verdict and cited reply |
| `POST /disputes/{id}/decision` | Planned | Approve, edit or reject a draft |
| `GET /api/board` | Planned | Only the authenticated seller's disputes, with no buyer messages or contacts |

### How the webhook endpoint works today

1. The five `PAYPAL-*` signature headers must be present and `PAYPAL-CERT-URL` must be a `paypal.com` API host, otherwise 400 without calling PayPal.
2. The event is sent to PayPal's `verify-webhook-signature` endpoint. Anything but `SUCCESS` is a 400.
3. The event is inserted into `webhook_events` with `ON CONFLICT DO NOTHING`, so a redelivery is acknowledged but processed once.

## Layout

```
app/main.py      FastAPI app, /healthz, /webhooks/paypal
app/paypal.py    signature-header checks, OAuth token cache, verify-webhook-signature call
app/db.py        schema bootstrap and claim_event() (idempotent insert)
tests/           pytest + respx (PayPal is mocked, no database needed)
render.yaml      Render blueprint: web service + free Postgres
```

## Stack

| Layer | Tool | Installed today |
|---|---|---|
| Core | Python 3.12, FastAPI, Uvicorn, httpx, psycopg | yes |
| Core | LangGraph + `langgraph-checkpoint-postgres`, Pydantic v2, PyYAML, an LLM with structured output and vision | planned |
| PayPal | Sandbox (US test accounts): Payments v2, Transaction Search, Disputes v1, Tracking, `verify-webhook-signature` | webhook verification only |
| Infra | Render (web service, Postgres, cron job), cloudflared or ngrok for local webhooks | blueprint written |
| Interface | Bryntum Scheduler (trial), Jinja2, HTMX, vanilla JS | planned |
| Tests | pytest, respx; pytest-asyncio, RAGAS and promptfoo later | pytest, respx |
| DevSecOps | GitHub Actions, Bandit or Semgrep, pip-audit | planned |
| Observability | Langfuse or LangSmith (optional) | planned |

## Configuration

Copy `.env.example` to `.env` (it is gitignored).

| Variable | Notes |
|---|---|
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | Sandbox REST app credentials from developer.paypal.com |
| `PAYPAL_WEBHOOK_ID` | ID of the webhook registered in PayPal. It needs a public URL. |
| `PAYPAL_BASE_URL` | Defaults to `https://api-m.sandbox.paypal.com`. Not yet in `.env.example` or `render.yaml` (see RECHECK #5). |
| `DATABASE_URL` | Postgres connection string. The table is created on startup. |

## Run locally

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
docker run -d -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres   # matches .env.example
uvicorn app.main:app --reload --env-file .env
```

PayPal needs a public URL to deliver webhooks, so use a tunnel (cloudflared or ngrok) and register `https://<tunnel>/webhooks/paypal` in the PayPal dashboard. Events from the webhook simulator do not pass signature verification, so test with real sandbox payments.

## Tests

```sh
pytest
```

The plan has 61 test cases in six layers, tracked by ID:

| Layer | IDs | What it covers | Where it runs |
|---|---|---|---|
| Unit | U01–U21 | `compute_verdict`, `classify_case`, `score_evidence`, `@owns`, webhook, `execute_action`, `validate_draft` | CI, PayPal mocked |
| Node (mock) | N01–N10 | LLM and API nodes against simulated PayPal errors (429, 503, `ERRDIS…`) | CI |
| Sandbox | G01–G12 | End to end on PayPal Sandbox, including resume after restart | manual scripts |
| Security | S01–S11 | Prompt injection, forged webhooks, token replay, no tools on LLM nodes | pytest + promptfoo |
| Evaluation | E01–E05 | 60 labelled chats, 20 sandbox disputes, 30 injection variants, 50 drafts | scripts + RAGAS |
| Spike | P01–P02 | Which payment source feeds the ledger, and whether simulator events verify | manual |

**Passing today:** U11 (failed signature returns 400 and stores nothing), U12 (same event twice is stored once) and S07 (forged webhook rejected before calling PayPal). The tests mock PayPal and replace the database call, so they need neither credentials nor Postgres. They do not exercise the real SQL in `db.claim_event` (see RECHECK #7).

## Build order

| Dates | Phase | Covers |
|---|---|---|
| 17–24 Oct | Foundations: FastAPI, Postgres, Render deploy, sandbox OAuth, webhook verification and dedupe | P01–P02, U11–U12 |
| 25–31 Oct | `scam_check`: nodes, verdict policy, paste UI, grounding validator | U01–U05, U09–U10, U17–U20, N01–N04, G01–G03, S01–S06 |
| 1–7 Nov | `dispute_defense`: fan-out, scoring, draft, `interrupt()`, approval screen, verified `policy_pack`, board | U06–U08, U13–U16, U21, N05–N10, G04–G12, S03, S07–S11 |
| 8–12 Nov | Run the evaluation set, publish the numbers, record a real demo | E01–E05 |

## Deploy

`render.yaml` defines the web service and a free Postgres. Set `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and `PAYPAL_WEBHOOK_ID` in the Render dashboard (they are `sync: false`). Dispute creation, `require-evidence` and `adjudicate` exist only in the sandbox. Always check a dispute's HATEOAS links before calling an action.
