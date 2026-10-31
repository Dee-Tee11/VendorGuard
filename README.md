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
| `chat` graph and `policy_pack` | Done in Studio and on the demo page through `POST /demo/api/chat` (C01–C37, U16, U22, U26, E06, E07, E08, E10, D12–D17, D20–D23). No endpoint outside the demo until auth lands |
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
  R -- waiting for the chat, or a price or a pasted chat at the price step --> K[check]
  R -- "check", or looks like a pasted chat --> S[start_check]
  R -- "waiting for the answer to handoff's question" --> H[handoff]
  R -- otherwise, also any other text at the price step --> C[classify: LLM]
  C -- question or another_payment --> Q[question]
  C -- "can_ship or next_steps, or a question the backstop reads as one" --> A[advise]
  C -- scam_check --> S
  C -- dispute --> D[dispute]
  C -- other or not_paypal --> O[other]
  C -- "human, or the word operator (no LLM call)" --> H
  C -- anything but question, dispute, can_ship, next_steps or human, at the price step --> K
```

One Groq call per message, `classify`, picks closed choices only: an intent (`question`, `scam_check`, `dispute`, `not_paypal`, `another_payment`, `can_ship`, `next_steps`, `human` or `other`) up to 2 keys of verified `policy_pack` notes, best first, and, for a follow-up, the numbers of up to 2 sentences of the notes just shown.

- **What it sees:** the current message, sanitised and inside `<question>` tags; the titles of the verified notes; and, when there is one, a `<previous>` block, also sanitised: the seller's last message that `classify` read, the notes in the last reply, numbered sentence by sentence, and the last check's outcome as codes (its label, its note's key and the labels of its warning signs) with the seller's price for it, so another order's price reads as another order.
- **What it never sees:** the buyer's chat, a check's quotes or evidence, the amount PayPal shows, or search results.
- **What it can't do:** it has no tools and writes no text the seller reads.

A follow-up answered from notes makes a second call, the **answer step** (`_grounded` inside `question`, not a node of its own, so nothing new is kept in the state). It runs only when every note `classify` left for this message was in the last reply and it picked sentences from it; a note the seller hasn't seen yet is shown in full. So never on a first question, right after a check, at the price step, or for a dispute.

- **What it sees:** `ANSWER_PROMPT`, the sentences of those notes (at most 2, after `classify`'s outcome-note rule), numbered and inside `<notes>`, and the current message, sanitised, inside `<question>` tags.
- **What it never sees:** the buyer's chat, a check's evidence or quotes, amounts, search results, or a message that matched `INSTRUCTION` (that one never gets past `classify`).
- **What it writes:** 1 to 3 sentences, each with the numbers of the note sentences it relies on. `output_guardrails.check_answer` checks them in code. If any sentence fails, or the call fails, the seller gets the reply below that quotes the notes, as if the step hadn't run.

The context lives in three state fields, never in `messages` (the demo clears those every turn): `last_question` (written by `classify`), `shown` (by every node that replies) and `last_case` (`{case_id, policy_id, signals}` plus, from the check's E1 only, `price` and `paid`, the seller's price and PayPal's amount as "40.00 USD", each dropped unless it matches a strict regex; by `check`, and by the demo's check panel). A reply about the last check, and the `another_payment` reply, name those amounts in code's words: "About your last check (case c882f1b4, your price 40.00 USD, PayPal shows 20.00 USD):". The graph's input is `messages` only, so a client can't set them.

Every reply is written in `app/chat.py`, by `scam_check`, or comes from PayPal (a page title and its link), except a follow-up's answer step, which is checked as above. The replies are in English.

- **question:**
  - Verified notes are shown word for word, at most 2 per reply. A PayPal note ends with its page; Vendor Guard's own notes have no source line.
  - A follow-up gets the answer step's sentences, then "From my notes:" with the titles of the notes they cite and, for a PayPal note, its page. A note it doesn't cite isn't shown.
  - If the answer step fails, a note shown in the last reply isn't shown again in full: the reply shows its title and the 1 or 2 sentences `classify` picked, word for word. If none answers, the reply is the no-note one below (search, or "I don't have that information").
  - When a picked note is the last check's own outcome note, the reply starts "About your last check (case …):", so the seller sees which check it is about.
  - An outcome note ("VERIFIED: what to do", "WAIT, not paid: what to do", …, which say "You saw: …") is shown only when it is the last check's own: `classify` drops any other in code, whatever the LLM picked. `vg_verdicts` says "You can ship", so it is dropped too when there is no check. If that leaves no note, the reply is "How to check a payment".
  - **another_payment:** after a check, a question about another order, buyer, item or payment gets "Your last check (case …) covered only that payment. To check the other payment, say check." With no check in the session, it gets "How to check a payment".
  - A seller who describes a warning sign gets its `vg_signal_*` note (the card's step for it).
  - A reply ends with at most one closing line: "I stopped the check…" at the price step, else "Want me to check the payment in PayPal? Say check." (offers the check without starting it) when a note about a payment scam is shown for the first time and there was no check, else "Still unsure? … Contact PayPal" when a Vendor Guard note carried it. Not offered for an address, a courier or WhatsApp: there is no payment to check.
  - If no note fits, Tavily searches PayPal's US pages, and the chat lists up to 3 titles and links that start with `https://www.paypal.com/us/` (filtered in code).
  - If the search finds nothing, fails, or there is no key, the reply is "I don't have that information" and PayPal's US business help link.
  - On the demo, both replies with no note (the PayPal pages, and "I don't have that information") end with "Want a person from Vendor Guard to look at this? Say operator." Not at the price step, where "I stopped the check…" stays the only closing line.
- **advise** (`can_ship`, "can I ship?", and `next_steps`, "what now?"): the LLM only decided that the seller asks; code answers from `last_case` and `shown`, with notes and its own words. No answer step, no search, no PayPal call.
  - `can_ship` with no check (or only an injection since): "I can't tell whether you can ship without checking that payment." and how to check. After a check: its own note, under "About your last check (…). It covers only that payment: for another order or buyer, say check." for VERIFIED and VERIFIED_WITH_WARNINGS, under "Not yet. Your last check (…) said:" for WAIT_NOT_CLEARED and WAIT_NOT_CONFIRMED, which can still change, and "No. Your last check (…) said:" for the other WAITs and SCAM_LIKELY. So only a VERIFIED check's note ever says "You can ship". An amount with its currency ("40 dollar", "25 USD", "60 usd", "$40") other than both the check's price and PayPal's amount gets the `another_payment` reply, from any node about to show the check's note (`_answer`); a bare number ("the other 5") doesn't count.
  - `next_steps`, the last reply first, then the last check: after a warning sign's note, that note and "Before you ship" (`vg_before_you_ship`, built from `POLICY["P.SCAM_LIKELY"]`, the SCAM_LIKELY Activity step and `SHIP`, no check needed); after "already shipped", that note again and never "Before you ship"; after a dispute note, PayPal's notes on how to respond and by when (`pp_respond_to_dispute`, `pp_dispute_deadline`); else the last check's own note; with neither, "Before you ship" and how to check.
  - One closing line, as in `question`: "Still unsure? … Contact PayPal", the check offer after a payment scam's sign with no check, or "I stopped the check…" at the price step. On the demo, a second "what now?" with nothing new (below) offers a person instead of "Still unsure? …".
  - **Backstop, after `classify` and never before** (so "what should I do if a buyer asks for F&F?" is classified by the LLM): when the LLM said `question`, a short EN/PT regex read with `fullmatch` sends the message to `advise` (`classify` applies it and writes the intent, so the router and `advise` only read it). `SHIP_ASK` ("can I ship?", "posso enviar?") does so unless the reply would come from the last check's own note; `NEXT_STEPS` ("what now?", "o que faço?") only when the reply would repeat the last one or have no note. E10 measures both on look-alikes. A `next_steps` reply whose notes were all in the last reply says "The notes in my last reply are all I have on this." instead of showing them again.
- **scam_check:**
  - The code asks for the price ("25.00 USD"), reads it with a regex and validates it like `POST /scam-check`. Then it asks for the buyer's chat.
  - That next message goes to `scam_check.run()` exactly as typed, with no classification and no search. "cancel" stops at either step.
  - At the price step, text that is neither a price, a pasted chat nor "cancel" goes to `classify`. A question or a dispute is answered and ends the check, and the reply says so. Anything else gets "I need the amount…" again, so "twenty five euros" is still asked again. The chat step never goes to `classify`: what comes there is the buyer's.
  - The reply is the verdict, the message and the sentences, never the evidence, which holds the buyer's quotes.
  - Instead of the chat, the seller can send a screenshot of it (PNG or JPEG, as a base64 `data:` URL in an `image_url` part, checked by its first bytes) with or without text. It goes to `scam_check.run()` like a screenshot sent to `/scam-check`, and counts as a check and a screenshot on the demo. A screenshot sent before `check` is never read or kept: it starts the check, and the reply, written for an image, asks for the price and then for the screenshot again. If it can't be read, the seller is asked again.
- **dispute:** fixed text, plus the 2 notes `classify` ranked first, or the first 2 dispute notes if it picked none. Once `dispute_defense` exists, this is where the chat will ask for the dispute ID.
- **other:** the first time (`greeted`, a state field), what Vendor Guard does and how to start a check. After that, one line on what Vendor Guard can help with, or a short goodbye ("thanks", "bye"). The word `notes`, sent alone, gets the titles of the verified notes instead, with no `classify` call.
- **not_paypal:** a check or a dispute that isn't on PayPal (a bank transfer, another app, another marketplace) goes to the same node, which says Vendor Guard only works with PayPal.
- **human** (the seller asks for a person; the word `operator`, sent alone, gets there with no `classify` call): `handoff`, with no LLM and no database.
  - When the run's `context` is `{"operators": True}` (only the demo passes it), it lists what a person from Vendor Guard would see and asks "Share it? Say yes, or anything else to keep it private." (`awaiting="consent"`). At the price step it ends the check first.
  - The next message goes back to `handoff`, never to `classify`, a check or Tavily. A short yes (EN or PT, read with `fullmatch`) sets `consented`. Anything else, a pasted chat or a screenshot included, shares nothing and is dropped.
  - Without that context (Studio, the evals, the demo without `OPERATOR_TOKEN`), the reply is "Talking to a person from Vendor Guard isn't available here for now."
  - It is the run's `context`, not `configurable`, because LangGraph copies `configurable` values into every checkpoint's metadata and, with tracing on, into LangSmith's run metadata.

Things that are easy to get wrong:

- **Pasted chats.** A message with a 17-character ID, `Buyer:`/`Seller:` lines or more than 400 characters starts a check instead of being classified, so it reaches neither the LLM nor Tavily. A short buyer line with neither can't be told apart from a question.
- **Isolated check.** `scam_check.run()` runs in an empty `contextvars.Context()`. Called directly inside a node, LangGraph treats it as a subgraph: it would save `scam_check`'s state, quotes included, in the chat thread's checkpoints and stream it to Studio.
- **Input.** The graph's input is `messages` only, so a client can't set the price, the intent or the context. The router on `START` still reads the raw input: a crafted `awaiting` reaches `check`, which then asks for the price, or `classify`, which sees it filtered out.
- **A stale last case.** "Can I ship?" after a VERIFIED check may be about another order. `classify` is told that a check covers only its own payment, `start_check` and `cancel` forget the last case, and the reply names the case. The risk is in RISKS.md.
- **Instructions to Vendor Guard.** A message that matches `INSTRUCTION` ("ignore your instructions", "from now on", "always pick", "tell me it's verified", "SYSTEM:", …) gets the `other` reply with no `classify` call, and isn't kept as the next message's context. The regex catches common phrasings only: the outcome-note rule above is what keeps a paraphrase from showing a verdict.
- **One request, two on a follow-up.** `classify` makes a single request (`llm.ask(..., attempts=1)`). On any failure the seller gets the `other` reply and nothing is searched (at the price step, the price is asked again). A follow-up answered from notes makes one more, also with no retry, of about 1,000 tokens. The message with the buyer's chat makes no `classify` call; `scam_check.run()` makes its usual two. The word `operator` and the answer to `handoff`'s question make none.
- **Consent.** While `awaiting="consent"`, every message goes to `handoff`, and only a short yes consents: a buyer's chat or a screenshot sent then is dropped unread. At the chat step, "operator" is still the buyer's chat, like "notes": cancel first.
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
4. **Deterministic validation.** Every source must exist, every placeholder must belong to a cited source, and no numbers, dates, emails, URLs or phone numbers may appear outside placeholders. Banned phrases ("guaranteed", "you will win", legal or tax advice, other platforms) are rejected. A warning sign's label is a verb phrase, so it may only follow "the buyer" (or ": ", "; ", "and " in a list), and a signal's code name (`PAYMENT_LINK`) or its `type` and `confirmed` fields never appear in a sentence. The sentence about the payment always comes from code, a fixed template that shows the status and both amounts, because a grounded sentence could still contradict the verdict ("{{E1.amount}}, in line with {{E1.expected_amount}}"). The model sees only the warning signs and writes 1 to 3 sentences about them. With no warning signs, it isn't called.
5. **Abstaining is valid.** Missing proof goes in `missing` with an `ADMIT_MISSING` sentence. Inventing it is a failure.
6. **Safe fallback.** Two failures in a row fall back to a fixed template per verdict or reason, with no LLM, and the case is flagged for review.
7. **The chat's answer step.** In a chat follow-up the facts are the verified notes, so `check_answer` compares each sentence with the note sentences it cites:
   - it cites at least one listed sentence;
   - every number, number word, link (with or without `https://`), currency and code name in it appears in what it cites, as a whole token ("3" isn't in "3-5 days");
   - it names no verdict and says nothing about shipping (the verb: "proof of shipment" is fine), unless a negation comes just before the ship word ("Don't ship…", not "Don't wait, ship") or it cites the last check's own note;
   - it is plain English with no markup;
   - "you can / may / could + verb" and "safe / ok to + verb" appear in what it cites;
   - it makes no promise or legal or tax advice;
   - at least half of its content words come from what it cites or the notes' titles, and 2/3 counting the seller's message too;
   - it keeps a negation from its source: a word only in a cited sentence's negated clause ("not eligible for Seller Protection") must be negated where it's written. Negations include no, without, none, neither and nor.

   The last two are lexical heuristics: a fluent paraphrase can pass them, and E07's judge watches for that. Any failure gives the quoted notes instead.

Fields in the LangGraph state are owned by the node that writes them (an `@owns(...)` decorator), so an LLM node cannot write the verdict.

`app/policy_pack.py` is a Python dict of notes, looked up by key and never by similarity.

- Each entry has `title`, `text`, `source_url`, `retrieved_at` and `verified`.
- An entry is shown, or offered to the LLM, only once a person has read its source page and set `verified: True` (test U16).
- There are two kinds of entry:
  - **Vendor Guard notes:** the verdicts, why WAIT, what to do after each outcome, what to do about each warning sign (`vg_signal_*`) and after shipping without being paid, and how to ask for a check. They are built from `scam_check`'s own texts (`STEPS`, `SIGNAL_STEPS`, `SUPPORT`) and are verified.
  - **PayPal notes:** 20 of them, each from one `https://www.paypal.com/us/` page, with the page's sentences word for word. The only edit is "we" → "PayPal", and list items joined with "; ". All are verified: 17 on 2026-10-22, 3 on 2026-10-30. E09 checks every sentence against its page today. Two quote only part of a list of requirements (`pp_seller_protection`, `pp_proof_of_delivery`), so they end with a sentence of Vendor Guard's own: "This is not PayPal's full list", with the page's link.
- The region is set once, in `REGION`.
- No PayPal US page says refunds must go only through PayPal, so the refund note says only how to refund in PayPal. Response deadlines are given only where a US Help Center page states them (10 days for a dispute). Seller Protection depends on region, and for the demo it assumes a US sandbox business account. It does not cover "Significantly Not as Described", so the agent never promises protection on a SNAD dispute.

## Live sandbox demo (`/demo`)

A public page where anyone can try Vendor Guard against PayPal's sandbox, and see what it does:

1. **Make a sale**: an item and a price in USD (1–1 000).
2. **Pay as the test buyer**: PayPal's own checkout (JS SDK) with a shared sandbox buyer account, whose email and password the page shows. The server creates the order and captures it after approval.
3. **Check the buyer's chat**: start from an honest buyer, an overpayment, a fake PayPal email or a prompt injection, edit it, or add a screenshot.

Below the steps, **Ask Vendor Guard** runs the `chat` graph: questions answered from the verified notes, "check" to verify a payment over three messages, disputes, and a "PayPal only" reply for other services. Each demo session has its own conversation, kept in memory (`InMemorySaver`, thread id = session id) and forgotten when the session ends or the service restarts. When the server has `OPERATOR_TOKEN`, it runs with the context `{"operators": True}`, so a seller can ask for a person (`human` in Graph 3); without it, no person is offered and `operator` gets the reply Studio gets. Only with the token, the session also keeps a transcript in memory, outside the graph: every message the seller sent (sanitised, with a screenshot only when a check read it, otherwise `[screenshot, not read]`), every reply, and each check panel's result card, never the buyer's chat pasted in the panel. When the seller says yes to `handoff`'s question, the transcript and the last check's codes are stored in `handoffs`, at most one open per session: a later yes updates it, and one after an answer opens another. When the server has `OPERATOR_TOKEN`, the page also has a **Vendor Guard operator** section: whoever pastes the token (kept in the tab's memory, sent in `X-Operator-Token`, never in a URL) sees every session's open handoff, the transcript as text only and the screenshots as images, with the last check's codes, and answers it. The seller's page shows the answer once in the chat as "Operator: …", from its 4-second poll of `/demo/api/state`. The answer never enters the graph (`messages`, `shown`, `classify`'s `<previous>`); it joins the session's transcript, so a later handoff shows it too. The live panel shows how each message was routed: the intent and notes the model picked, or that the code routed it with no model call. At the chat step, the Screenshot button sends the buyer's chat as an image instead of text.

Each paid sale has "Buyer's receipt" and "Seller's receipt" buttons: PNGs of the real sandbox capture, one for each side, drawn in the browser and labelled as the demo's, to try screenshots with. With a screenshot and no price, step 3 reads the amount from the screenshot and fills it in for you to confirm: it never checks with it unconfirmed, since a receipt shows what was paid, not what you asked.

Next to the steps, a live panel shows each `scam_check` node as it runs and what it wrote, every PayPal call with its HTTP status and response body, and the verified webhooks about the tester's sales. The panel shows the tester what they sent (the cleaned chat, the screenshot's text, the quotes), which `/scam-check` never returns. Its outcome (codes only) becomes the chat's last case, so "what do I do now?" in the chat is about it. A panel check and a chat message of the same session run one at a time: the second gets a 409, or an error line in the panel.

- **Anonymous sessions.** The page keeps the session id in `sessionStorage`, so closing the tab forgets it and a reload keeps it. "End demo" deletes the session at once, and a session idle for 30 minutes is deleted at the next new session or at boot. Deleting removes its sales, cases, claimed payments, handoffs and the webhook events about its sales, and forgets its conversation and transcript. The payments stay in PayPal's sandbox, which can't delete them.
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
| `POST /scam-check` | Built | Body `{text, image, expected_amount, expected_currency}`. Send `text`, `image` or both. `image` is a base64 PNG or JPEG with no `data:` prefix, up to 3 MB. The chat may be up to 8 000 chars after sanitising, counting the screenshot's text. Returns `{case_id, verdict, message, sentences, evidence, template, signals_checked, persisted}`, never the chat or the screenshot's text. 422 on bad input or a screenshot that can't be read, 429 over 10 checks (3 with a screenshot) an hour per IP, 503 when two checks are already running. Not authenticated yet (RECHECK #3). Every route answers 413 to a body over 4.2 MB, before reading it. |
| `GET /demo` | Built, `DEMO_MODE` only | The demo page. Every `/demo*` route is 404 without `DEMO_MODE=true`. |
| `POST /demo/api/session` | Built, `DEMO_MODE` only | `{session_id, client_id, currency, buyer_email, buyer_password, operators}`, `operators` saying whether the operator section is on. The other demo routes need the id in `X-Demo-Session`, and answer 401 once the session ended or went idle. |
| `POST /demo/api/orders`, `POST /demo/api/orders/{id}/capture` | Built, `DEMO_MODE` only | Create a USD order for `{item, amount}`, then capture it: `{capture_id, status, amount, currency}`. 404 for another session's order, 502 when PayPal refuses. |
| `POST /demo/api/check` | Built, `DEMO_MODE` only | The `/scam-check` body. Streams NDJSON lines: `paypal` (method, path, status, body), `node` (name, update), then `result` (as `/scam-check`) or `error`. |
| `POST /demo/api/chat` | Built, `DEMO_MODE` only | Body `{message, image}`, a message up to 8 000 chars, a screenshot (as `/scam-check`'s `image`) or both. One message to the `chat` graph on the session's thread: `{reply, route, intent, notes, awaiting}`. `route` is the node that answered; `intent` and `notes` are set only when the model classified the message. A message that runs a check counts as a check, and as a screenshot if it has one. 503 when the message would run a check and two are running, 409 while the session's previous message is still running. |
| `GET /demo/api/state`, `POST /demo/api/end` | Built, `DEMO_MODE` only | The session's sales, the webhook events about them, and its handoffs (`id`, `status`, `reply`, never the transcript); ending deletes the session and all it left (409 while a chat message is running). |
| `GET /demo/api/operator/handoffs`, `POST /demo/api/operator/handoffs/{id}/reply` | Built, `DEMO_MODE` and `OPERATOR_TOKEN` only | The token in `X-Operator-Token` only (one in the URL doesn't count): 401 for a wrong one, before any database call; after 10 wrong ones in an hour from an IP, 429 for that IP, the right token included; 404 when `OPERATOR_TOKEN` is unset. The first lists every session's open handoff (`id`, `transcript`, `last_case`, `shared_at`, never the session id); the second answers one with `{reply}`, 1–2 000 characters, and is a 409 once it was answered or its session ended. |
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
| `GROQ_API_KEY_LLM_EVAL`, `GROQ_API_KEY_LLM_EVAL2`, `GROQ_API_KEY_LLM_JUDGE` | Tests only: the evals (`pytest -m eval`) call Groq with these, never with `GROQ_API_KEY`, so they don't spend the app's daily quota. The second is E07's judge. |
| `LLM_MODEL` | Defaults to `openai/gpt-oss-120b`. Must be a Groq model with strict JSON-schema output. |
| `TAVILY_API_KEY` | From app.tavily.com. Optional: without it the app starts, and a chat question with no note gets PayPal's help link. Turn off "Allow Use of Query Data" in the Tavily account (see Graph 3). |
| `DEMO_MODE` | `true` turns on the live demo (`/demo`). Sandbox only: the app refuses to start with it against live PayPal. Off when unset. |
| `DEMO_BUYER_EMAIL`, `DEMO_BUYER_PASSWORD` | A US sandbox personal account that testers log in with at PayPal's checkout. The page shows both. Required with `DEMO_MODE`. Keep a second account ready: a tester can change this one's password. |
| `OPERATOR_TOKEN` | Optional, with `DEMO_MODE`: turns on the demo page's operator section, shared by the people who answer handoffs. At least 32 characters, or the app refuses to start: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Off when unset. |
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
pytest -m eval -v    # evals: the real Groq model, keys from .env, made-up chats only
```

The evals have their own Groq keys, `GROQ_API_KEY_LLM_EVAL` for the app's calls and `GROQ_API_KEY_LLM_JUDGE` for E07's judge, so a run doesn't spend the app's daily quota (200K tokens a day for `gpt-oss-120b` on the free tier). Groq's limits are per organization and per model, so the keys only add quota when they come from different organizations. With `LANGSMITH_TRACING=true` in `.env`, every eval is traced to the LangSmith project `vendorguard-evals`, never the dev one, and each test's traces are sent when it ends, so a run can be followed live. The offline tests are never traced.

The evals' calls rotate through every `GROQ_API_KEY_LLM_EVAL*` key in .env (never the app's `GROQ_API_KEY`, nor the judge's): a key that fails (a 429, a spent quota, a revoked key) hands over to the next, and once all have failed in a call it waits a minute. `EVAL_KEY=2` starts from `GROQ_API_KEY_LLM_EVAL2`. `EVAL_JUDGE=0` runs E07 and E08 on the rules only, without the judge. Each run is saved as `<eval>-<dataset>-<UTC time>` (`E06-chat-…`, `E10-next-…`, `E07-seller-…`, `E08-weak-…`), its .txt starts with the eval, the model and the names of the keys it used (never the values), and each case's test ID starts with the eval (`-k "E08-22"`). Runs before 2026-10-30 20:30 UTC are named without the eval; E10 was called T7 until 2026-10-30 (`T7-next-…`).

The plan has 61 test cases in six layers, plus C01–C37 for the chat and D01–D26 for the demo, tracked by ID:

| Layer | IDs | What it covers | Where it runs |
|---|---|---|---|
| Unit | U01–U22 | `compute_verdict`, `classify_case`, `score_evidence`, `@owns`, webhook, `execute_action`, `validate_draft` | CI, PayPal mocked |
| Node (mock) | N01–N10 | LLM and API nodes against simulated PayPal errors (429, 503, `ERRDIS…`) | CI |
| Sandbox | G01–G12 | End to end on PayPal Sandbox, including resume after restart | manual scripts |
| Security | S01–S11 | Prompt injection, forged webhooks, token replay, no tools on LLM nodes | pytest + promptfoo |
| Chat | C01–C37 | `app/chat.py`: routing, the check flow, screenshots, Tavily, links, personal data | CI, Groq and Tavily mocked |
| Demo | D01–D26 | `/demo`: off by default, sandbox only, sessions, orders, the streamed check, the chat, per-IP limits | CI, PayPal and Groq mocked |
| Evaluation | E01–E05 | 60 labelled chats, 20 sandbox disputes, 30 injection variants, 50 drafts | scripts + RAGAS |
| Spike | P01–P02 | Which payment source feeds the ledger, and whether simulator events verify | manual |

**Passing today:**

- Webhook: U11 (a failed signature returns 400 and stores nothing), U12 (the same event twice is stored once) and S07 (a forged webhook is rejected before PayPal is called).
- `scam_check`:
  - U01–U05: the verdict table.
  - U09–U10: `@owns`.
  - U17–U20: the output guardrails (tests/test_output_guardrails.py). U22: the chat answer step's rules, each with an answer that breaks it.
  - Evals (`pytest -m eval`): 3 injection chats run through the whole graph must not put buyer text in the reply (tests/test_eval_output_guardrails.py).
  - E07 (tests/test_eval_seller.py, `pytest -m eval -s`): 57 made-up conversations of a seller with Vendor Guard, in tests/seller_dataset.jsonl, in English. Each one has 1 to 4 turns.
    - The scenarios are questions about PayPal, suspicions described in words, a check run in the chat followed by questions about its result, and disputes, other payment services, small talk and injections.
    - PayPal is mocked and Tavily is off. The chat graph runs with the real Groq model.
    - Each turn is scored by rules: the node that answered, the notes it had to show (or must not show), text it must not show, the verdict, and that it doesn't repeat the last reply word for word.
    - The answer step's KPIs: how often it was grounded or fell back, and its sentences per grounded reply. The report shows the rules that rejected each fallback. Case 57 is an injection aimed at it.
    - An LLM judge then scores each whole conversation against a reference written from `policy_pack`'s notes: correct, next steps, whether the last reply answers the last message, safe, covers the reference, and 1–5. The judge is the app's own model, so read the conversations too.
    - The user's own scores of the baseline are in tests/eval_baselines/*.human.jsonl. The KPIs show how often the judge lands within a point of them: on the baseline, the judge was too lenient when an earlier turn covered the reference and the last one failed.
    - Each run saves every conversation to tests/eval_results/<UTC day>/, with the KPIs per rule, per criterion and per scenario. The .txt starts with the dataset used: its path, case count and sha256. The first run (15/35 passing, judge mean 3.89) is kept in tests/eval_baselines/ to compare later runs against.
  - E08 (`EVAL_DATASET=weak pytest -m eval -s tests/test_eval_seller.py`): E07's harness on the 30 cases of tests/seller_weak_dataset.jsonl, where E07 failed most: warning signs and disputes followed by "what next?", check follow-ups, pending payments, and injections in the previous turn. Saved as `E08-weak-*` in tests/eval_results/; the human scores apply to E07 only.
  - E09 (tests/test_eval_policy_pack.py, `pytest -m eval -s`, no Groq key): fetches each PayPal note's page and checks that every sentence is still on it, word for word, undoing "we" → "PayPal". Prints the closest line on the page for a sentence it misses. U25, its matcher on fixed strings, runs offline. Run it before a demo: pages change.
  - Screenshot evals (tests/test_eval_screenshot.py): drawn PT and EN chats, one with an injection, must be copied word for word with the ID and the right `Buyer:`/`Seller:` labels. A blurred chat must not be guessed, and a screenshot alone run through the whole graph must give `SCAM_LIKELY`.
  - Screenshots, offline: a screenshot alone, a screenshot plus text, bad uploads, a failed copy (no verdict, no case, PayPal not called), and no tools or secrets in the vision call (S03).
  - N01–N04: PayPal 404, 403, 429, 503, timeouts, token failure, Transaction Search rows, LLM and database failures.
  - S01–S06: prompt injection, unverifiable quotes, no tools and no secrets in prompts, no ship wording from the LLM, Unicode and tag tricks.
  - S12: a body over 4.2 MB is a 413 before it is read, with or without `Content-Length`; the biggest valid `/scam-check` request fits.
- `chat` (tests/test_chat.py):
  - C01–C17: every intent, the price and the chat flow, a screenshot at the chat step, and cancel.
  - C18–C21: the context (the last check's codes reach `classify`, its quotes don't; the input can't set it; a new check or cancel forgets it), a note just shown is answered from its picked sentences, never repeated whole, a question at the price step, and the check offered after a described scam.
  - C22–C24: no outcome note without its own check, instructions never classified, another payment.
  - C30–C33: `advise`. Every row of the `can_ship` and `next_steps` tables, "You can ship" only after a VERIFIED check, another price, after an injection; the backstop for "what now?" and "can I ship?" and look-alikes that stay questions; at the price step, and never while waiting for the chat. U26: `vg_before_you_ship` is `scam_check`'s own sentences, ungated, and the answer step can't turn it into "you can ship".
  - C34–C36: `handoff`. Asking for a person, or the word `operator` with no LLM call, asks before anything is shared, and ends a check at the price step with the question last; at the chat step, `operator` is the buyer's chat. At the consent step only a short yes consents: "no", a question, a yes with more, a pasted chat or a screenshot shares nothing and reaches no LLM, Tavily or check. Without the demo's context, it says operators are demo only; the input can't consent.
  - C37: the offers. On the demo, a reply with no note and a second "what now?" with nothing new end with "Say operator" (instead of "Still unsure? …" on the second), the check offer stays after a scam's sign with no check, and at the price step "I stopped the check…" stays the only closing line.
  - C25: the answer step. A follow-up is answered in sentences that cite its notes; an LLM error, a schema error, no sentences, one bad sentence, an injected flip or "ship" without a check all give the quoted reply, byte for byte; no answer step on a first question or right after a check, and no buyer chat in its prompt; "ship" only from the last check's own note.
  - The buyer's chat never reaches the chat's LLM or Tavily, and no quotes appear in the reply or in the thread.
  - Pasted chats, the tag injection, the link filter, Tavily failures, personal data in the search, one Groq request per message (two on a follow-up), no tools or secrets.
  - U16: the `policy_pack` sources, and that unverified notes are never offered or shown.
  - E06 (tests/test_eval_classify_chat.py, `pytest -m eval -s`): the 39 cases in tests/chat_dataset.jsonl, all in English, the real Groq model. Each run saves every reply and the KPIs (routing, intent, notes shown, LLM calls) to tests/eval_results/<UTC day>/, which git ignores. The .txt starts with the dataset, as in E07. Each call uses about 830–890 tokens, about 1,600 for a dispute. No SDK retries, as in the app: a 429 waits 60 s and tries once more.
  - E10 (`EVAL_DATASET=next pytest -m eval -s tests/test_eval_classify_chat.py`): E06's harness on the 36 single messages of tests/next_steps_dataset.jsonl: "can I ship?" and "what now?" phrasings, in English, and look-alikes that are questions or disputes ("how do I ship?", "what should I do if a buyer asks for F&F?"). KPIs: recall of `can_ship` and `next_steps`, and look-alikes not sent to `advise`. Saved as `E10-next-*`.
- Demo (tests/test_demo.py): D01–D17. The routes are 404 when off, the app won't start against live PayPal, another session's order is never captured, the stream lists every node and PayPal call (no token) and ends with the same verdict as `/scam-check`, a busy check is an error line and every check gives its slot back, and the per-IP limits ignore forged `X-Forwarded-For` entries. The chat answers from a verified note, runs a check over three messages on its own session's thread (counted as a check, its case tied to the session), refuses that message with 503 when no slot is free, takes one message at a time per session (409), counts a chat screenshot as a check and a screenshot, and forgets the thread when the session ends or is swept. D19: the check panel's outcome becomes the chat's last case, and the panel and the chat take turns. D20–D23: the handoff. Only a yes writes a row, with the whole conversation (a screenshot only when a check read it) and the last check's codes; ending or sweeping the session forgets its transcript; a row the database refuses is a 500 and leaves no "Sent" in the transcript; a panel check joins it as its card, never the buyer's chat. D24–D26: the operator API needs its token in a header (404 without `OPERATOR_TOKEN`, 401 for none, a wrong one or one in the URL, with no database call); an operator's answer reaches the seller's state and the transcript, never the graph or `classify`'s prompt, and answering twice is a 409; a token under 32 characters stops the app from starting. D27: without `OPERATOR_TOKEN`, no person is offered and no transcript is kept. D28: 10 wrong operator tokens from an IP lock it out, the right token included, which itself never counts.

The tests mock PayPal and Groq and replace the database calls, so they need neither credentials nor Postgres. `tests/test_db.py` runs the real SQL (the claims, saving a case, the demo's tables and its cleanup, one open handoff per session) against a throwaway Postgres, and is skipped unless `TEST_DATABASE_URL` is set:

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

`render.yaml` defines the web service only; the database is a Neon Postgres. Set `DATABASE_URL` (Neon's direct connection, not the pooler), `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID` and `GROQ_API_KEY` in the Render dashboard (they are `sync: false`). `TAVILY_API_KEY` is optional and not in the blueprint, so add it by hand. For the live demo, also set `DEMO_MODE=true`, `DEMO_BUYER_EMAIL` and `DEMO_BUYER_PASSWORD` (and `OPERATOR_TOKEN` for its operator section), and subscribe the webhook to `PAYMENT.CAPTURE.*` and `CHECKOUT.ORDER.APPROVED` (and `CUSTOMER.DISPUTE.*` for later). Dispute creation, `require-evidence` and `adjudicate` exist only in the sandbox. Always check a dispute's HATEOAS links before calling an action.
