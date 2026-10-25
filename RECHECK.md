# Recheck

Open findings from the `/code-review` run on 2026-10-19, with their original numbers. The review read the code only. It did not run anything. Points from #15 on came later and say where they came from.

This file is a to-do list: when a point is fixed, delete it and note the fix in `CHANGES.md`. If a point turns out to be wrong or no longer relevant, write the reason next to it instead of deleting it.

Authentication and authorization are still being built, so some of these may no longer apply once that lands. Where I think a point depends on the auth work, I tagged it **(depends on auth)**. That tag is my guess, not the reviewer's.

## Config and deploy

- [ ] **14. No `ipAllowList` on the free Postgres, and the free tier is not durable.** [render.yaml:1](render.yaml#L1)
  **Unverified:** the reviewer could not check this and was going from memory. Confirm against Render's docs first. Omitting `ipAllowList` may leave the database reachable from any IP, and Render's free Postgres has historically expired after about 30 days, which would delete stored events.
  *Fix:* set `ipAllowList: []` and use a paid plan for anything that must keep payment events.
  *2026-10-21, no longer applies to Render:* commit 3f93433 removed the Render database, and `DATABASE_URL` now points to Neon. The same questions (who can connect, how long the free tier keeps data) still need checking against Neon's docs.

## Security hardening

- [ ] **2. No freshness or replay check on `PAYPAL-TRANSMISSION-TIME`.** [app/paypal.py:35](app/paypal.py#L35) **(depends on auth)**
  Captured headers can be replayed indefinitely. PayPal's integrity binding is only a CRC32 of the body, and dedupe is keyed on an `id` inside that body, so a crafted body with a new `id` could be stored as genuine. Harmless today because nothing consumes the table.
  *Fix:* reject old `transmission_time`, and re-fetch the event from PayPal's API by id before acting on it.

- [ ] **3. Unauthenticated callers can trigger blocking PayPal calls.** [app/main.py:45](app/main.py#L45) **(depends on auth)**
  There is no body-size limit, rate limit or concurrency cap, and the same 40-thread pool serves `/healthz`. A flood of plausible-looking requests can exhaust it, fail Render's health check and restart the service.
  *Fix:* cap body size, add cheap pre-checks, a concurrency limit and a rate limit.
  *2026-10-20:* `/scam-check` adds a bigger target: each call costs 2 Groq calls and up to 6 PayPal calls. It now has a global limit of 2 concurrent checks (503 above that), and `/healthz` is `async`, so it no longer waits for a pool thread. Body size and per-caller rate limits are still open.
  *2026-10-21:* screenshots make a normal body up to about 4 MB, and a screenshot adds a third Groq call. The 3 MB image cap is checked only after the whole body has been read, so the body-size cap matters more now.
  *2026-10-22:* the image is now capped as base64, before it is decoded. The whole body is still read and parsed as JSON first, whatever its size.
  *2026-10-24:* `/scam-check` and the demo now have per-IP hourly limits (`app/ratelimit.py`), in memory. The body-size cap is still open.

- [ ] **18. Screenshots go to Groq with their metadata.** [app/llm.py](app/llm.py)
  Added 2026-10-21 with the screenshot input, not from the review. The image bytes are sent as uploaded. A phone photo of a screen can carry EXIF data (GPS position, device), and so can a JPEG. With `LANGSMITH_TRACING=true` the image is also stored in the traces.
  *Fix:* before real cases, re-encode the image without metadata (Pillow would then be a runtime dependency), or reject images that carry EXIF.

- [ ] **23. The per-IP limit trusts the last `X-Forwarded-For` entry.** [app/ratelimit.py](app/ratelimit.py)
  Added 2026-10-24 with the live demo. `client_ip()` takes the last entry, assuming Render's proxy appends the caller's address after anything the caller sent. Not checked on Render. If Render puts another proxy in front, the last entry could be that proxy, and every tester would share one limit. If it doesn't append at all, a caller could forge its address.
  *Fix:* on the first deploy, send `curl -H "X-Forwarded-For: 1.2.3.4" https://<app>/demo/api/session` a few times and log what `client_ip()` sees. Then fix the rule in `client_ip()` only.

- [ ] **24. Ending a demo session during a check can leave rows behind.** [app/db.py](app/db.py)
  Added 2026-10-24. If "End demo" runs while a check of the same session is running, the session's `demo_cases` row is deleted first, and the check then saves its `scam_cases` and `claimed_payments` rows, which nothing deletes. They hold no chat and no quotes.
  *Fix:* if it matters, sweep `scam_cases` and `claimed_payments` rows whose case has no session, or let `/demo/api/end` wait for the session's running check.

## LLM output

- [ ] **15. With signals unknown, the explain step invents a warning sign.** [app/scam_check.py:296](app/scam_check.py#L296)
  Found by the eval `verified, signals unknown` on 2026-10-21, not by the review. When `detect_signals` fails, `signals` is `None` and the verdict is `VERIFIED_WITH_WARNINGS`, but `_explain_input` never says the chat check failed. The model then cites an `E2` that does not exist ("A warning sign labeled {{E2.label}} could not be verified"). The guardrails reject it, so it is safe, but the seller gets the template instead of an explanation.
  *Fix:* when `signals is None`, add a line such as "Chat check: could not be done" to `_explain_input`, so the model can write an `ADMIT_MISSING` sentence on `E1`. Then remove the `xfail` on that eval case.
  *Next:* build a LangSmith dataset from the eval cases and score the explanations with an LLM-as-judge (does each sentence match the evidence and verdict?). The current evals only check the rules in `output_guardrails`, not whether the wording is true.

- [ ] **25. The explanation reads badly around signal labels.** [app/scam_check.py](app/scam_check.py)
  Added 2026-10-24, from a live run of the demo's fake-email chat. The labels are verb phrases written for "the buyer …" ("offers an email or screenshot as proof…"), and the model put them where a noun goes: "The buyer provides FAKE_PAYMENT_PROOF which is a offers an email…", "The communication includes a says the money is released only after…". The guardrails pass it (it is grounded), so the seller sees it. It also wrote "PayPal shows the payment status as UNAVAILABLE" when Transaction Search answered 403, which reads as if the payment itself were unavailable.
  *Fix:* a graph change, so plan it first: either tell `EXPLAIN_PROMPT` that `{{Ek.label}}` follows "the buyer", or give each signal a noun-phrase label for the LLM. Then add an eval case that checks the wording, as #15's *Next* suggests.

- [ ] **16. Nobody measures whether `detect_signals` catches real scams.** [app/scam_check.py:183](app/scam_check.py#L183)
  Added 2026-10-21, from a design discussion, not the review. The evals in `tests/test_eval_output_guardrails.py` only test the explanation. The signals never decide `VERIFIED` (PayPal does), but they decide `SCAM_LIKELY` and the warnings, and their recall and false-alarm rate are unknown.
  *Fix:* an opt-in eval (`pytest -m eval`) with 30–40 made-up but realistic chats in PT and EN: OLX/Vinted scams, MB WAY, "paguei a mais", own courier, fake PayPal emails, plus honest buyers. Check the expected signal types and `confirmed` per chat, and report recall and false alarms per signal. A second LLM reviewing the first is not the fix: it reads the same buyer text, so the same injection fools both.
  *2026-10-21:* screenshots add a copy step before `detect_signals`. With a screenshot, `confirmed` means "in the model's copy", not "in the image". Run the same chats as screenshots too: `screenshot()` in `tests/test_eval_screenshot.py` draws them. Add real phone screenshots (WhatsApp, OLX, Vinted, dark mode) and photos of a screen, since the drawn ones are clean.

- [ ] **17. Consider Jev (TypeSafe AI) for `detect_signals`.**
  Added 2026-10-21, an idea, not a finding. Jev does not generate text: it takes a state and typed questions (yes/no, rubric, multiple choice) and returns probabilities. That matches `detect_signals` (one yes/no per signal type) and would give a tunable threshold instead of a quote or null. It can also act as the judge for #15 *Next*. It is reachable from Pydantic AI (`typesafe:jev-latest`), DeepEval and Promptfoo.
  *Before deciding:* run it on the #16 eval and compare it with the current Groq model. Check pricing and data handling in TypeSafe's own docs, because buyer chats would be sent to a new vendor. Pin a version (`jev-1.13.0`-style) if a threshold is tuned. It returns no quote, so the `confirmed` check that stops invented quotes would need a replacement.

## Chat

- [ ] **19. The chat thread keeps the buyer's chat.** [app/chat.py](app/chat.py)
  Added 2026-10-22 with the `chat` graph, not from the review. In the Chat tab every message enters `messages` before any node runs, so the checkpointer stores the buyer's chat (in `langgraph dev`: `.langgraph_api/`). `/scam-check` never stores it. The chat's LLM never reads it again, but it is personal data at rest. `RemoveMessage` only clears the latest state, and deleting a thread in `langgraph dev` leaves the message blobs on disk.
  *Fix:* before the chat gets an API endpoint, take the buyer's chat in a field that is not a message, or run the check on a thread with no checkpointer.

- [ ] **20. Names can reach Tavily.** [app/chat.py](app/chat.py)
  Added 2026-10-22. The search query drops self-introductions ("I'm Amanda"), emails, links, phone numbers and IDs, but not other names ("my buyer John wants F&F"). Tavily's terms give it a perpetual licence over queries.
  *Fix:* none planned yet. Turn off "Allow Use of Query Data" in the Tavily account, and revisit if real cases start.

- [ ] **21. The PayPal notes cover only part of PayPal's rules.** [app/policy_pack.py](app/policy_pack.py)
  Added 2026-10-22, when the 17 PayPal notes were verified. A question outside them gets Tavily links only, and `dispute_defense` (1–7 Nov) will need more than they give.
  *To find and verify, each from a `https://www.paypal.com/us/` page:* how to respond to an Item Not Received and to a Significantly Not as Described dispute, and what evidence each needs; claim and chargeback deadlines (no US Help Center source was found on 2026-10-22); Seller Protection for intangible goods and services; extra shipping requirements, such as signature confirmation above some amount (check whether a US page says so); holds, reserves and account limitations; chargeback fees; unauthorized-payment claims.
  *Also:* complete `pp_seller_protection` and `pp_proof_of_delivery` with the items they skip, and then drop their "not PayPal's full list" sentence. Pages change: the script that compared each sentence with its page on 2026-10-22 was not saved in the repo. Make it an opt-in eval (`pytest -m eval`) and rerun it before the demo.

- [ ] **22. Vendor Guard's own notes cite the README.** [app/policy_pack.py:17](app/policy_pack.py#L17)
  Added 2026-10-24, from the E06 eval. The `vg_*` notes end with "Source: README.md#graph-1-scam_check", which a seller can't open. A PayPal link would be a false source: these notes describe Vendor Guard's rules, not PayPal's.
  *Fix:* with the frontend, a page for the policy pack, such as `GET /help`, built from the same entries with one anchor per note, and `source_url="/help#<key>"`. Update the `README.md#` check in `tests/test_chat.py`. Also move the "different transaction IDs" sentence out of `vg_why_wait`, so `pp_different_ids` answers it with PayPal's link.
