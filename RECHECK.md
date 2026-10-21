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

## LLM output

- [ ] **15. With signals unknown, the explain step invents a warning sign.** [app/scam_check.py:296](app/scam_check.py#L296)
  Found by the eval `verified, signals unknown` on 2026-10-21, not by the review. When `detect_signals` fails, `signals` is `None` and the verdict is `VERIFIED_WITH_WARNINGS`, but `_explain_input` never says the chat check failed. The model then cites an `E2` that does not exist ("A warning sign labeled {{E2.label}} could not be verified"). The guardrails reject it, so it is safe, but the seller gets the template instead of an explanation.
  *Fix:* when `signals is None`, add a line such as "Chat check: could not be done" to `_explain_input`, so the model can write an `ADMIT_MISSING` sentence on `E1`. Then remove the `xfail` on that eval case.
  *Next:* build a LangSmith dataset from the eval cases and score the explanations with an LLM-as-judge (does each sentence match the evidence and verdict?). The current evals only check the rules in `output_guardrails`, not whether the wording is true.

- [ ] **16. Nobody measures whether `detect_signals` catches real scams.** [app/scam_check.py:183](app/scam_check.py#L183)
  Added 2026-10-21, from a design discussion, not the review. The evals in `tests/test_eval_output_guardrails.py` only test the explanation. The signals never decide `VERIFIED` (PayPal does), but they decide `SCAM_LIKELY` and the warnings, and their recall and false-alarm rate are unknown.
  *Fix:* an opt-in eval (`pytest -m eval`) with 30–40 made-up but realistic chats in PT and EN: OLX/Vinted scams, MB WAY, "paguei a mais", own courier, fake PayPal emails, plus honest buyers. Check the expected signal types and `confirmed` per chat, and report recall and false alarms per signal. A second LLM reviewing the first is not the fix: it reads the same buyer text, so the same injection fools both.

- [ ] **17. Consider Jev (TypeSafe AI) for `detect_signals`.**
  Added 2026-10-21, an idea, not a finding. Jev does not generate text: it takes a state and typed questions (yes/no, rubric, multiple choice) and returns probabilities. That matches `detect_signals` (one yes/no per signal type) and would give a tunable threshold instead of a quote or null. It can also act as the judge for #15 *Next*. It is reachable from Pydantic AI (`typesafe:jev-latest`), DeepEval and Promptfoo.
  *Before deciding:* run it on the #16 eval and compare it with the current Groq model. Check pricing and data handling in TypeSafe's own docs, because buyer chats would be sent to a new vendor. Pin a version (`jev-1.13.0`-style) if a threshold is tuned. It returns no quote, so the `confirmed` check that stops invented quotes would need a replacement.

## Tests

- [ ] **7. Tests don't cover the real SQL.** [tests/test_webhook.py:50](tests/test_webhook.py#L50)
  `test_u12` uses a hand-written fake `claim`, so a bug in `ON CONFLICT DO NOTHING`, the `rowcount == 1` check or the schema would pass. The same goes for `claim_payment` and `save_case`.
  *Fix:* add a real-Postgres test, skipped when no database is reachable.
