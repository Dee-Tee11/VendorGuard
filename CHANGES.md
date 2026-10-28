# Changes

## 2026-10-27: follow-ups quote the note, one closing line, small talk once

A manual review of E07 run seller-20261027T103751Z found four reply-quality problems.

**Chat graph** (`app/chat.py`, planned first; same nodes and routes, still one `classify` request per message)
- A note shown in the last reply is no longer named with "…: see my last answer". `<previous>` lists that reply's notes sentence by sentence, numbered, and `classify` picks up to 2 numbers in a new `sentences` field (`list[int]`, a new state field it owns). The reply shows the note's title and those sentences word for word. If none answers, the reply is the no-note one: search, or "I don't have that information" and PayPal's link (C19). This brings back the quoting removed earlier the same day, with the first version's failures designed out: see RISKS.md.
- Vendor Guard's own notes no longer show "Source: README.md#…". PayPal's notes keep their link.
- A reply ends with at most one closing line: `CHECK_STOPPED` at the price step, else `OFFER_CHECK`, else `SUPPORT` (cut from the end of the `vg_*` notes in the chat). The check is offered only when a note about a payment scam is shown in full for the first time and there was no check: not for an address, a courier or WhatsApp, not on a repeat, not after a check (C21).
- At most 2 notes per reply, in the classifier's order ("best first" in the prompt). A dispute reply shows the 2 notes `classify` ranked first (it now picks notes for `dispute` too), or the first 2 dispute notes.
- `other`: the introduction once per thread (new state field `greeted`, owned by `other`: the demo clears `messages` every turn, so they can't tell). After that, one line on what Vendor Guard can help with, or "Glad I could help. Bye!" for thanks and goodbyes (E07 case 34).
- The hard line breaks in case 2's note were in the reflowed `.txt` only: the run's `.jsonl` and the note's text have none.

**E07**, before (seller-20261027T223642Z) and after (seller-20261028T205351Z), 56 cases, 1 rep: 28 → 30 cases passing everything, judge mean 4.20 → 4.16, safe 54 → 55, no Groq 400s, tokens 151K → 160K. No "see my last answer" and no "README.md#" in the after run. Known gaps: a note with one sentence is quoted whole, so case 16's follow-up repeats it; a wrong first note gets quoted on the follow-up (case 43, the hold note).

## 2026-10-27: no verdict note without a check

E07 cases 35 and 36 showed "VERIFIED: what to do" ("You saw: You can ship") with no check in the conversation, case 18 showed "WAIT, not paid" ("Don't ship") to a seller who had already shipped, and case 37 answered a question about another order with shipping notes. The only guard was the prompt.

**Chat graph** (`app/chat.py`, planned first)
- `classify` keeps an outcome note (`VERDICT_NOTES`, the "You saw: …" notes) only when it is the last check's own, so it always comes with "About your last check (case …):". `vg_verdicts` says "You can ship" and is dropped when there is no check, until it gets a descriptive text. If nothing is left, the reply is "How to check a payment" (C22).
- A message that tries to instruct Vendor Guard (`INSTRUCTION`) gets the introduction, with no `classify` call and no context kept for the next message (C23).
- New intent `another_payment`: after a check, a question about another order gets "Your last check (case …) covered only that payment. To check the other payment, say check." (C24).

**Evals**
- E07: a rule fails any turn that shows an outcome note's title or "You can ship" with no check of that payment (no `last_case`, or a turn marked `new_payment`, as case 37's last turn).
- E06: "What verdicts can Vendor Guard give?" now expects "How to check a payment", and the pasted WAIT card no longer expects its outcome note. Instruction messages expect no LLM call.

## 2026-10-27: the chat follows the conversation, after the E07 review

The user scored the 35 E07 conversations by hand (tests/eval_baselines/seller-20261025T193317Z.human.jsonl, mean 3.65 against the judge's 3.85). The main gap: `classify` read only the newest message, so follow-ups like "so what do I do?" got the wrong note or none. The risks of these changes are in RISKS.md.

**Chat graph** (`app/chat.py`, a graph change, planned first)
- `classify` gets a `<previous>` block: the seller's last message it read, the titles of the notes in the last reply, and the last check's outcome as codes. New state fields `last_question`, `shown` and `last_case`, each owned by the nodes that write them. The buyer's chat, quotes and amounts never enter them (C18).
- After a check, "what do I do now?" gets that outcome's note, headed "About your last check (case …):". A new check or cancel forgets the last case.
- A note shown in the last reply is named, not shown again (E07 cases 2, 7 and 11 repeated it word for word).
- Tried and removed the same day: `classify` picking one sentence of a PayPal note to quote ("PayPal says: …"). With the sentences listed in the prompt, a `classify` call grew from about 850 to 4,200 tokens, the model wrote sentence ids where note keys go (Groq's 400 `json_validate_failed`, 9 times in one E07 run, each falling back to the intro), and "Can I ship that one too?" got "PayPal says: You must ship the item…" (E07 cases 36 and 37, judged unsafe).
- At the price step, a question goes to `classify`, is answered, and ends the check (E07 case 18 was stuck asking for the price). Anything else still gets "I need the amount…", and a pasted chat never reaches `classify` (C20).
- "Describing a buyer is not asking for a check" in the prompt, and a reply about a described scam ends with "Want me to check the payment in PayPal? Say check." (C21).

**Notes** (`app/policy_pack.py`)
- `vg_signal_*`, one per warning sign, from `SIGNAL_STEPS` (with the money-out warning where it applies) and `SUPPORT`: a seller who describes WhatsApp, a fee, pressure or a screenshot as proof now gets the card's step (E07 cases 14, 15, 16, 23).
- `vg_already_shipped`: look in the PayPal Activity, contact PayPal if the payment isn't there, keep the tracking (E07 case 18).

**Card** (`app/scam_check.py`, `app/output_guardrails.py`)
- A warning sign's step is left out when the outcome's own steps already say it (`COVERED`): the overpayment step was shown twice on a WAIT for the wrong amount (E07 case 20).
- The result has `policy_id`, so the chat can keep the outcome.
- RECHECK #25, in code: `type` and `confirmed` can't be filled into a sentence, a label must follow "the buyer" (or ": ", "; ", "and "), and a signal's code name is rejected. Such a sentence falls back to the template. The prompt says the same.

**Demo**
- The check panel's outcome becomes the chat's last case. The panel's check now runs under `demo.busy()`, the same per-session lock as a chat message and "End demo", taken inside the stream so it lasts until the check ends: closes RECHECK #24 (D19).

**E07** (`tests/test_eval_seller.py`, `tests/seller_dataset.jsonl`)
- The judge also scores whether the last reply answers the last message (an earlier good turn no longer hides a bad last one), and knows that asking for the price and the buyer's chat is the check, not a request for personal data (it scored case 3 unsafe for that).
- A rule fails a reply identical to the previous one. A turn can list notes that must not be shown (`no_notes`).
- The KPIs show how often the judge is within a point of the user's scores.
- The evals call Groq with their own keys, `GROQ_API_KEY_LLM_EVAL` for the app's calls and `GROQ_API_KEY_LLM_JUDGE` for the judge, never the app's `GROQ_API_KEY`: the first E07 run after these changes used 255K tokens and hit the free tier's 200K a day. The judge's tokens are no longer in the KPIs.
- With `LANGSMITH_TRACING=true` in `.env`, the evals are traced to the LangSmith project `vendorguard-evals` (they weren't traced at all: pytest never loaded those variables, only `langgraph dev` does). Each test's traces are sent when it ends. The offline tests stay untraced.
- Case 20 rewritten (it confused the user), case 30's reference accepts that dispute help isn't ready, and three new cases: an injection in the previous turn, a VERIFIED check followed by a question about another order, and a buyer's line typed at the price step.

## 2026-10-25: chat on the demo, and the first real payment

**Chat API** (`POST /demo/api/chat`, the "Ask Vendor Guard" panel on `/demo`)
- The `chat` graph is reachable over HTTP for the first time. Each demo session has its own thread in an `InMemorySaver`, deleted when the session ends or is swept, so the buyer's chat a seller pastes never reaches disk (RECHECK #19, for the demo only).
- The answer gives the reply and how the message was routed (`route`, `intent`, `notes`), and the live panel shows that.
- When the next message is the buyer's chat, which runs `scam_check`, it counts as a check against the per-IP limit and takes a slot (503 when busy), as `chat.check`'s docstring asks.
- Its case is tied to the session, so ending the session deletes it. The case id is read from the reply's last line ("Case …", written by `chat.check`), because the chat state doesn't hold it.
- No graph changed. A chat check runs `scam_check` in an empty context (C06), so its PayPal calls aren't in the live panel; step 3 shows them.
- `db.end_demo_sessions()` now returns the ids it deleted. The real-SQL test caught a bug there that the fakes missed: `fetchall()` called on the connection instead of the cursor.
- After a code review of the above:
  - **One message at a time per session** (`demo.turn`): a second chat message, or "End demo", while a message is still running is a 409. Before, two overlapping turns could fork the thread: a question sent during a check ran as the buyer's chat (a second check, past the global cap), a turn could return the other's reply and miss its case id, and ending the session mid-check recreated the deleted thread and left its case and claimed payment with no session. The page also sends one message at a time.
  - Whether a message runs a check is decided by `chat.runs_scam_check`, which `chat.check` uses too, so the route can't disagree with the node. A blank message during the check flow no longer counts as a check.
  - Each turn clears the thread's old messages (`RemoveMessage(REMOVE_ALL_MESSAGES)` in the input; no node reads past the newest). `InMemorySaver` stores the messages again in every checkpoint, so a long session grew quadratically; now it grows linearly. The reply is read from the run's own stream, not from a second `get_state`.
  - The slot logic of `/scam-check` and the chat is one helper (`main._slot`).
  - `chat` warns about a missing `TAVILY_API_KEY` when it would search, not at import, since `main` imports it even with the demo off.
  - Page: a refused message goes back in the box, Enter while composing with an IME doesn't send, nothing is sent before the session exists, and "End demo" says why it failed instead of claiming it deleted everything.
- **Screenshots in the chat.** At the chat step, `chat.check` takes a screenshot (one `image_url` part, a PNG or JPEG `data:` URL, typed by its bytes) with or without text, and passes it to `scam_check.run()`. An unreadable one is asked for again. Before and after that step nothing changes: an image starts the check without being read. `POST /demo/api/chat` takes an optional `image` (the same type as `/scam-check`'s), and such a message counts as a check and a screenshot. The Ask panel has a Screenshot button, and `vg_how_to_check` no longer says screenshots aren't supported. No graph's nodes or edges changed. Tests: C17, D17, and three more C16 cases.
- Each paid sale on `/demo` has "Buyer's receipt" and "Seller's receipt" buttons: PNGs of the real sandbox capture, drawn in the browser and labelled as the demo's, to test screenshots with. The buyer's copy says "You paid"; the seller's says "You received" and adds the order ID.
- Step 3 with a screenshot and no price: the page asks `POST /demo/api/price` for the amount the screenshot shows (one vision call, 5 an hour per IP), fills the price field and asks the seller to confirm it. It never checks with that amount on its own: a receipt shows what was paid, not what the seller asked, and a real receipt for 45 USD on a 450 USD item would otherwise come back VERIFIED. The model's answer is kept only if `chat.parse_price` accepts it. Test: D18.
- The intro no longer lists every note title, which made a greeting a wall of text. It says "Say notes", and the word `notes`, matched exactly like `check`, gets the list from `other` with no `classify` call. No graph changed.
- Tests: D12–D16 (248 offline in total), and the real-SQL tests check the returned ids. D15 now covers the idle sweep too, and a real-SQL assertion that could never fail now checks the sweep's own result.

**Wrong-amount explanations show both amounts**
- Found on `/demo`: a COMPLETED payment for the wrong amount got the WAIT card with "the amount differs the expected amount" and no numbers. The model had used `{{E1.amount_check}}`, which fills in the bare word `differs`, and the guardrails only checked that placeholders were allowed, not that the needed ones were there.
- `amount_check` is now in `output_guardrails.NOT_FILLABLE`: the model still sees it, but can't put it in a sentence.
- When `amount_check` is `differs` (any verdict, not only `P.WAIT_WRONG_AMOUNT`), `validate_output` drops the model's sentences that cite E1 and puts the template's E1 sentence first, which shows both amounts. Only checking that the amount placeholders were present was not enough: "{{E1.amount}}, in line with {{E1.expected_amount}}" passed next to a "Don't ship". The model's sentences about warning signs are kept. If nothing of the model's is left, `template` is true. No graph change, and the prompt is unchanged: telling the model which placeholders to use, in the shared prompt, made it write `{{E1.amount}}` for payments that have none.
- Tests: two N04 cases and one U18 case. The "too old" eval fails before and after this change: the model writes "14 days" and `{{Ek.label}}`.

**Every outcome says what to do, and how to reach PayPal** (RECHECK #28, step 1)
- Found on `/demo`: a seller got WAIT for a wrong amount, pasted the card's sentence into the chat and asked what to do. The reply was `vg_why_wait`, every `P.WAIT_*` instruction in one paragraph, with no next step.
- `scam_check.STEPS` gives each of the 9 policy ids a label, what PayPal showed and numbered next steps. `SIGNAL_STEPS` adds one step for each warning sign found, whether its quote was confirmed or not. Facts about PayPal in the steps come from the verified notes in `policy_pack`.
- `SUPPORT` ends every outcome: "Still unsure? … Contact PayPal". It links to PayPal's US Contact Us page (`CONTACT_PAYPAL`, live chat with a person), checked on 2026-10-25.
- `scam_check`'s result has two new fields, `steps` and `support`. They are fixed text, outside the model and the guardrails. The verdict card shows them as "What to do" and a Contact PayPal link, and `chat.check` writes them before the "Case …" line. The stored case keeps them too. No nodes or edges changed.
- In the chat, `vg_why_wait` is now one line per WAIT reason. Each outcome has its own note (`vg_verified`, `vg_verified_warn`, `vg_scam_likely`, `vg_wait_wrong_amount`, …). The note quotes the instruction the seller saw and ends with `SUPPORT`, so `classify` can pick just that one. The notes leave out the steps for each warning sign: the chat doesn't know the seller's case (RECHECK #28, step 2).
- Tests: U16 checks that every policy id and signal has its steps and that each note ends with `SUPPORT`. It also checks that the page's link matches `CONTACT_PAYPAL`. S02 checks the steps for each warning sign. A new eval row holds the seller's message from `/demo`.

**Code writes the payment sentence, the model only the warning signs** (from `/code-review`)
- The wrong-amount fix above was one special case in `validate_output`. It could drop the warning-signs sentence, mark a passing draft as `template`, and give 5 sentences. A grounded model sentence could still contradict a "Don't ship" for the other reasons too ("PayPal shows {{E1.paypal_status}}, so it is complete").
- Now the first sentence is always `PAYMENT_TEMPLATE`'s, written by code. `_explain_input` no longer shows the model E1, and `validate_output` checks its sentences against the signal evidence only, so a sentence that cites E1 fails. The model writes 1 to 3 sentences, so the reply has at most 4 (`output_guardrails.validate` takes `most`). With no warning signs, or signals unknown, the model isn't called. That fixes RECHECK #15: the model can no longer invent an E2.
- `template` is true only when the model's sentences failed the checks. The special case is gone.
- Same nodes and edges. What changed is what `explain_and_reply` writes, and `EXPLAIN_PROMPT` now asks only about the warning signs.
- Tests: N04 now covers an LLM error, an ungrounded draft, a draft about the payment, four sentences, and no LLM call without warning signs. S03 and S04 now use drafts about the signals. A bad-screenshot case that repeated the data-URL test is folded into it (262 offline in total).

**E07: made-up conversations of a seller with Vendor Guard** (`tests/seller_dataset.jsonl`, `tests/test_eval_seller.py`)
- The question is whether Vendor Guard guides a seller well: right answers, clear next steps, safe advice.
- 35 conversations in English, 1 to 4 turns each, in four scenarios:
  - questions about PayPal (9);
  - suspicions the seller describes in words (9);
  - a check run in the chat (check → price → the buyer's chat, PayPal mocked), then a question about the result (11);
  - disputes, other payment services, small talk and injections (6).
- Each turn has the expected node, the notes it must show and, for a check, the verdict. Each conversation has a reference: what good advice covers, written from `policy_pack`'s verified notes.
- The chat graph runs with the real Groq model, and Tavily is off. Each turn is scored by those rules. Then an LLM judge (the app's own model, `llm.MODEL`) scores the whole conversation against the reference: correct, next steps, safe, covers the reference, and 1–5 with a reason.
- Each run saves every conversation to `tests/eval_results/seller-*.jsonl`. The `.txt` holds the KPIs (per rule, per criterion, per scenario) and every conversation as the seller saw it, with the rules it broke and the judge's answer.
- It replaces the 10-case explanation eval in `tests/test_eval_output_guardrails.py`, whose `calls` fixture and result writer the E06 chat eval now shares.
- First run on 2026-10-25 (10 minutes, about 111,000 tokens, 9 rate limits waited out): 15/35 conversations passed everything. It is kept as the baseline in `tests/eval_baselines/seller-20261025T193317Z.txt` and `.jsonl`, since `tests/eval_results/` is ignored by git.
  - Rules: 85/89 turns routed right, 26/28 required notes shown, 11/11 verdicts.
  - Judge: correct 31/35, next steps 20/35, safe 32/35, covers the reference 23/35, mean 3.89.
  - The main failure: a follow-up after a check doesn't know the result (RECHECK #28). "What do I do now?" got the intro, "I don't have that information", or a new check.

**Run for real**
- A payment through the PayPal button worked end to end, with the shared buyer `sb-kfrny…@personal.example.com`:
  - the order was created and captured, COMPLETED, 45.00 USD;
  - `CHECKOUT.ORDER.APPROVED` and `PAYMENT.CAPTURE.COMPLETED` arrived, both with the signature verified by PayPal. This confirms P02: webhooks from real sandbox payments pass verification.
- In the checkout, PayPal offered "Pay over time" (a Synchrony credit line) after login, despite `disable-funding=credit,paylater` on the button. Closing it and paying with the PayPal balance works.
- Through the chat, with real Groq: a greeting got the intro, "What does WAIT mean?" got `vg_why_wait`, and the Friends and Family question got the two PayPal notes. "The buyer paid me by Zelle" got `not_paypal`. "check" → price → chat ran a check.
- An unknown ID now gives `NOT_FOUND` instead of `UNAVAILABLE`: Transaction Search answers, so the permission is active on the sandbox app.

## 2026-10-24: live sandbox demo (`/demo`)

**What it is** ([app/demo.py](app/demo.py), [app/static/demo.html](app/static/demo.html), README "Live sandbox demo")
- A public page where a tester:
  1. makes a sale,
  2. pays it in PayPal's real sandbox checkout with a shared test buyer,
  3. checks a buyer's chat.
- A live panel shows every `scam_check` node, every PayPal call with its status and body, and the verified webhooks about the tester's sales.
- Off unless `DEMO_MODE=true`, and then the app refuses to start against live PayPal or without `DEMO_BUYER_EMAIL` and `DEMO_BUYER_PASSWORD`.
- No graph changed: the git diff of `app/scam_check.py` and `app/chat.py` is empty.

**Sessions.** Anonymous. The page keeps the session id in `sessionStorage`, so closing the tab forgets it and a reload keeps it. "End demo" deletes the session at once, and an idle one is deleted 30 minutes later, at the next new session or at boot. Deleting removes its sales, cases, claimed payments and the webhook events about its sales, in one transaction. The sessions live in Postgres, not memory, because Render's free tier sleeps.

**PayPal writes.** Creating and capturing orders are the app's first PayPal writes. They live in `app/demo.py`, outside the graphs, and play the buyer's purchase. A capture:
- uses `PayPal-Request-Id: capture-<order id>`, so a retry can't capture twice;
- only goes through for an order of the caller's own session.

**Live panel.** `paypal._call` records each call in a context variable (`paypal.trace`), never headers or the token. `POST /demo/api/check` streams NDJSON from `graph.stream`. Every step runs in one `contextvars.Context`, because Starlette runs each step of a generator in a fresh copy and the trace would be lost. The check slot is taken inside the stream, so a stream that never starts can't keep it.

**Per-IP limits** ([app/ratelimit.py](app/ratelimit.py)). Per hour: 10 checks (3 with a screenshot), 10 sales, 20 sessions. The check limit applies to `/scam-check` too. A refused call counts for nothing. The counters are in memory. Which `X-Forwarded-For` entry is the caller on Render is not checked yet (RECHECK #23).

**Tests**
- D01–D11 in `tests/test_demo.py` (16 tests, 243 offline in total).
- `tests/test_db.py` runs the real SQL against a throwaway Postgres: the demo's tables and cleanup, `claim_event`, `claim_payment` and `save_case`. This closes RECHECK #7.

**Run for real** (locally, against the sandbox and Groq, with a throwaway Postgres)
- The fake-email and injection chats both gave `WAIT`. Ending the session deleted its rows, and the session then got 401. Phone width has no horizontal scroll.
- Transaction Search answered 403 `NOT_AUTHORIZED`: "Transaction Search" is still not ticked on the sandbox REST app. Until it is, an ID that isn't a capture of this account is "unavailable", never "not found", so the demo can't reach `SCAM_LIKELY`.
- The first fake-email chat put the signals in a pasted email, and only `URGENCY_PRESSURE` counted, because `detect_signals` counts only what the buyer says. The scenario now has the buyer say it, and all 3 signals are found.
- The explanation reads badly around signal labels (RECHECK #25). That is a graph change, so it is not fixed here.
- Not yet run: a payment through the PayPal button. It needs the real shared buyer account.

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
