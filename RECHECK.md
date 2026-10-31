# Recheck

Open findings from the `/code-review` run on 2026-10-19, with their original numbers. The review read the code only. It did not run anything. Points from #15 on came later and say where they came from.

This file is a to-do list: when a point is fixed, delete it and note the fix in `CHANGES.md`. If a point turns out to be wrong or no longer relevant, write the reason next to it instead of deleting it.

Authentication and authorization are still being built, so some of these may no longer apply once that lands. Where I think a point depends on the auth work, I tagged it **(depends on auth)**. That tag is my guess, not the reviewer's.

Points marked with \* are important: do them first.

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
  *2026-10-30:* every route now rejects a body over 4.2 MB with 413 (`MAX_BODY` in `app/main.py`), on `Content-Length` before reading, or on the bytes read when chunked. Still open: `/webhooks/paypal` has no per-IP limit, so a forged webhook with plausible headers still costs one `verify-webhook-signature` call; and per-seller limits wait for auth.

- [ ] **18. Screenshots go to Groq with their metadata.** [app/llm.py](app/llm.py)
  Added 2026-10-21 with the screenshot input, not from the review. The image bytes are sent as uploaded. A phone photo of a screen can carry EXIF data (GPS position, device), and so can a JPEG. With `LANGSMITH_TRACING=true` the image is also stored in the traces.
  *Fix:* before real cases, re-encode the image without metadata (Pillow would then be a runtime dependency), or reject images that carry EXIF.
  *2026-10-30:* with the seller's yes to a handoff, the screenshots a check read are also stored in Postgres (`handoffs`), metadata included, until the session ends, for the operator page to show.

- [ ] **23. The per-IP limit trusts the last `X-Forwarded-For` entry.** [app/ratelimit.py](app/ratelimit.py)
  Added 2026-10-24 with the live demo. `client_ip()` takes the last entry, assuming Render's proxy appends the caller's address after anything the caller sent. Not checked on Render. If Render puts another proxy in front, the last entry could be that proxy, and every tester would share one limit. If it doesn't append at all, a caller could forge its address.
  *Fix:* on the first deploy, send `curl -H "X-Forwarded-For: 1.2.3.4" https://<app>/demo/api/session` a few times and log what `client_ip()` sees. Then fix the rule in `client_ip()` only.

- [ ] **33. One shared operator token, no identity or audit.** [app/demo.py](app/demo.py) **(depends on auth)**
  Added 2026-10-30 with the demo's handoff. Whoever holds `OPERATOR_TOKEN` reads every open handoff, buyers' chats and screenshots included, and answers as "Operator". Nothing records who answered, and changing the token means a redeploy.
  *Fix:* once auth lands, operator accounts, and an audit row per answer (who, when, which handoff).

## LLM output

- [ ] **25. Nobody scores how the explanation reads.** [app/scam_check.py](app/scam_check.py)
  Added 2026-10-24. The sentences that read badly ("The buyer mentions says…", "which is a wants another address…", `{{E2.type}}` filled in) are now rejected in code and replaced by the template (see CHANGES.md, 2026-10-27). What passes can still be correct but unclear.
  *To do:* score the E07 cards' sentences with an LLM-as-judge (does each sentence read well and match the evidence?), and count how often the template replaces the model. If it is most of the time, drop the model from the explanation.

- [ ] **16. Nobody measures whether `detect_signals` catches real scams.** [app/scam_check.py:183](app/scam_check.py#L183)
  Added 2026-10-21, from a design discussion, not the review. No eval scores `detect_signals` on its own. The signals never decide `VERIFIED` (PayPal does), but they decide `SCAM_LIKELY` and the warnings, and their recall and false-alarm rate are unknown.
  *Fix:* an opt-in eval (`pytest -m eval`) with 30–40 made-up but realistic chats in PT and EN: OLX/Vinted scams, MB WAY, "paguei a mais", own courier, fake PayPal emails, plus honest buyers. Check the expected signal types and `confirmed` per chat, and report recall and false alarms per signal. A second LLM reviewing the first is not the fix: it reads the same buyer text, so the same injection fools both.
  *2026-10-21:* screenshots add a copy step before `detect_signals`. With a screenshot, `confirmed` means "in the model's copy", not "in the image". Run the same chats as screenshots too: `screenshot()` in `tests/test_eval_screenshot.py` draws them. Add real phone screenshots (WhatsApp, OLX, Vinted, dark mode) and photos of a screen, since the drawn ones are clean.
  *2026-10-31, E07 case 26* (`E07-seller-20261031T104530Z`): "Here is my payment from last month, ID …" got the email-or-screenshot-as-proof sign, which the chat doesn't contain. The card then told the seller not to trust emails or screenshots.

- [ ] **17. Consider Jev (TypeSafe AI) for `detect_signals`.**
  Added 2026-10-21, an idea, not a finding. Jev does not generate text: it takes a state and typed questions (yes/no, rubric, multiple choice) and returns probabilities. That matches `detect_signals` (one yes/no per signal type) and would give a tunable threshold instead of a quote or null. It can also act as the judge for #25. It is reachable from Pydantic AI (`typesafe:jev-latest`), DeepEval and Promptfoo.
  *Before deciding:* run it on the #16 eval and compare it with the current Groq model. Check pricing and data handling in TypeSafe's own docs, because buyer chats would be sent to a new vendor. Pin a version (`jev-1.13.0`-style) if a threshold is tuned. It returns no quote, so the `confirmed` check that stops invented quotes would need a replacement.

## Chat

- [ ] **19. The chat thread keeps the buyer's chat.** [app/chat.py](app/chat.py)
  Added 2026-10-22 with the `chat` graph, not from the review. In the Chat tab every message enters `messages` before any node runs, so the checkpointer stores the buyer's chat (in `langgraph dev`: `.langgraph_api/`). `/scam-check` never stores it. The chat's LLM never reads it again, but it is personal data at rest. `RemoveMessage` only clears the latest state, and deleting a thread in `langgraph dev` leaves the message blobs on disk.
  *Fix:* before the chat gets an API endpoint, take the buyer's chat in a field that is not a message, or run the check on a thread with no checkpointer.
  *2026-10-25:* the chat now has a demo endpoint (`POST /demo/api/chat`). There the thread is in memory and deleted when the demo session ends or the service restarts, so nothing reaches disk. Still open for any endpoint outside the demo.
  *2026-10-27:* the thread also keeps `last_question`, the seller's last message that `classify` read (never the buyer's chat, which never reaches `classify`), and `last_case`, codes only.
  *2026-10-30:* not fixed by the handoff, which adds to it. With `OPERATOR_TOKEN` set (since 2026-10-31, only then), the demo also keeps each session's whole conversation in memory, outside the graph (`demo._transcripts`), so a person from Vendor Guard can read it: the buyer's chat pasted at the chat step and the screenshots a check read included, never the chat pasted in the check panel. With the seller's yes to `handoff`'s question, it is stored in Postgres (`handoffs`) until the session ends, then deleted with it.

- [ ] **20. Names can reach Tavily.** [app/chat.py](app/chat.py)
  Added 2026-10-22. The search query drops self-introductions ("I'm Amanda"), emails, links, phone numbers and IDs, but not other names ("my buyer John wants F&F"). Tavily's terms give it a perpetual licence over queries.
  *Fix:* none planned yet. Turn off "Allow Use of Query Data" in the Tavily account, and revisit if real cases start.

- [ ] **21. The PayPal notes cover only part of PayPal's rules.** [app/policy_pack.py](app/policy_pack.py)
  Added 2026-10-22, when the 17 PayPal notes were verified. A question outside them gets Tavily links only, and `dispute_defense` (1–7 Nov) will need more than they give.
  *To find and verify, each from a `https://www.paypal.com/us/` page:* how to respond to an Item Not Received and to a Significantly Not as Described dispute, and what evidence each needs; claim and chargeback deadlines (no US Help Center source was found on 2026-10-22); Seller Protection for intangible goods and services; extra shipping requirements, such as signature confirmation above some amount (check whether a US page says so); holds, reserves and account limitations; chargeback fees; unauthorized-payment claims.
  *2026-10-27, from E07 case 2:* "Should I ship while the money is on hold?" No note answers it. PayPal's hold page implies yes (add tracking, the hold is released after delivery), but a note must say it in PayPal's words.
  *Also:* complete `pp_seller_protection` and `pp_proof_of_delivery` with the items they skip, and then drop their "not PayPal's full list" sentence. Pages change: the script that compared each sentence with its page on 2026-10-22 was not saved in the repo. Make it an opt-in eval (`pytest -m eval`) and rerun it before the demo.
  *2026-10-30:* three notes added and verified by the user: how to respond to a dispute and what to send (`pp_respond_to_dispute`), unauthorized-payment claims (`pp_unauthorized_claim`), and proof of shipment with signature confirmation (`pp_proof_of_shipment`). The page check is now an eval, E09 (`tests/test_eval_policy_pack.py`): 20 of 20 notes on their pages that day. It reads the HTML the server sends: `pp_add_tracking`'s page also embeds a variant ("Go to your Activity. Choose “All Transactions.” Find the purchase you want to update, then click Get your money."), which a browser may show instead. Still to find: SNAD responses, chargeback fees, holds and limitations.

- [ ] **22. `vg_why_wait` explains different transaction IDs in Vendor Guard's words.** [app/policy_pack.py](app/policy_pack.py)
  Added 2026-10-24, from the E06 eval. *Fix:* move the "different transaction IDs" sentence out of `vg_why_wait`, so `pp_different_ids` answers it with PayPal's link.
  *2026-10-30:* `vg_why_wait` no longer has the sentence: it is built from `STEPS`' labels. The sentence is now a step of `P.WAIT_NOT_CONFIRMED` ([app/scam_check.py:103](app/scam_check.py#L103)), so the card and `vg_wait_not_confirmed` still say it in Vendor Guard's words. The fix now applies there.

- [ ] **27. No dataset for multi-turn chats and screenshots.** [tests/chat_dataset.jsonl](tests/chat_dataset.jsonl)
  Added 2026-10-25, after #26. The 39 cases are all one text message each, so they test `classify`, not how the chat behaves over several turns or with images. #26 was found by hand.
  *To do:* about 30–40 multi-turn cases, scored on the reply and the state (`awaiting`, `price`, whether `scam_check` ran and with what):
  - a screenshot with or without text, at each step (before `check`, at the price, at the chat);
  - "check" written in other ways ("can you verify this?", "é seguro enviar?", "is this payment real?");
  - a price and the chat in one message;
  - cancelling, or asking a question, halfway through;
  - an image that isn't PNG or JPEG, and two images;
  - EN and PT.
  Draw the screenshots with Pillow, as `tests/test_eval_screenshot.py` does, or use the demo's "Buyer's receipt" and "Seller's receipt". Run it like the E06 eval, and check #26's fix against it.
  *2026-10-30, partly covered by E07* (`tests/seller_dataset.jsonl`, 57 cases): most are 2–7 turns, each turn scored on the node, the notes and the verdict. Still missing: real images (no E07 case sends one), PT, other ways to say "check", and the screenshot sent before `check` (#26's case, fixed with a reply written for an image: see CHANGES.md, 2026-10-30).
  *2026-10-30, later:* E08 (`tests/seller_weak_dataset.jsonl`, 30 cases, run with `EVAL_DATASET=weak`) adds multi-turn cases where E07 failed most: warning signs and disputes followed by "what next?", check follow-ups, pending payments and injections. Still no images and no PT.

- [ ] **29. No dataset to see the outcome and the next steps the chat gives.** [app/scam_check.py:59](app/scam_check.py#L59)
  Added 2026-10-25, after `STEPS` and `SIGNAL_STEPS`. U16 and S02 check that every policy id and signal has its steps, not that a real case gets the right ones. #27 scores the chat's flow and #16 the signals; this one scores what the seller is told to do at the end.
  *To do:* a dataset of cases, each a PayPal answer (mocked, as in `tests/test_scam_check.py`), a price and a buyer chat, with the expected policy id, the steps shown and the follow-up in the chat:
  - every policy id at least once, and the wrong amount both higher and lower than the price;
  - each warning sign alone, and two or three together, so the order and number of extra steps can be seen;
  - after the check, the seller asks "what do I do now?", "can I ship?", "o que faço agora?" or pastes the card's sentence: the reply should give the same steps as the card, through the matching `vg_*` note;
  - EN and PT.
  Save each run's verdict, steps, support line and chat reply to `tests/eval_results/`, so the outcomes can be read side by side. Read the first run by hand before scoring anything: are the steps right for the case, in a sensible order, and not too many?
  *2026-10-30, partly covered by E07:* some cases ask "What do I do now?" or "Can I ship?" after a check, and each run is saved to `tests/eval_results/`. Still missing: every policy id and signal combination on purpose, and PT.

- [ ] **30. The chat's last check can be stale.** [app/chat.py](app/chat.py)
  Added 2026-10-27, with the chat's context (RISKS.md). After a VERIFIED check, "can I ship?" about another order could get the VERIFIED note. Mitigated: `classify` is told a check covers only its own payment, a new check or cancel forgets it, and the reply names the case. Not measured beyond E07 case 37.
  *Fix if it shows up:* forget `last_case` after a few turns, or answer a ship question after a VERIFIED case only with "check that payment first".
  *2026-10-30:* it showed up when `classify` also saw the case note's sentences, framed as history. E07 case 51 got the second check's steps for the first order in 2 of 2 runs, and case 37 missed "say check" in 1 of 2, so that was reverted (see CHANGES.md). Any change that puts the last check's content in `classify`'s context must rerun 37 and 51.
  *2026-10-30, later:* `classify`'s context now has the last check's price (", price 40.00 USD"), and the prompt says another price means another order, to fix case 51. Rerun with `EVAL_REPS=2` (`seller-20261030T092037Z.jsonl`, see CHANGES.md): 51 fixed and 37 fine in both reps, but 46 got the shipping-address note instead of `vg_wait_wrong_amount` in 1 of 2. Open until 46 is understood: the price line, the new prompt sentence, or noise.
  *2026-10-30, E08* (`weak-20261030T110519Z`): cases 22 (after WAIT_REUSED, "But it's the same buyer, can't I ship?") and 29 (after an injection, "So can I ship now?") got `pp_ship_to_transaction_address` ("You must ship the item to the shipping address…"), and the judge marked both safe. Planned fix: step 1's `can_ship` intent, answered by code from `last_case`.
  *2026-10-30, later:* `can_ship` and `next_steps` now go to `advise`, which answers from `last_case` in code: a VERIFIED check's own note only for that check, "Not yet" for any other outcome, "I can't tell" with no check, and the `another_payment` reply when the message has another price (see CHANGES.md). Close once E08 22 and 29 and E07 37, 46 and 51 pass on a rerun. E07, same day, `EVAL_REPS=2` (`seller-20261030T195908Z`): 46 passes both; 37 routes right in both (one judge 2 on the first check's card); 51 fails only `answers_last` in both. E08 (`E08-weak-20261030T202757Z`): 22 and 29 pass.
  *2026-10-31, E07 with Claude as the judge* (`E07-seller-20261031T104530Z`): 51 judged 2, the same reply as on 2026-10-30. "Can I ship the first order, the 25 dollar one?" gets "Your last check (… 40.00 USD) covered only that payment. To check the other payment, say check.", but the 25 USD order was already VERIFIED in this chat, and a re-check of it would say "already used" (#36). `last_case` holds only the latest check.

- [ ] **31. A payment lookup without the buyer's chat.** [app/chat.py](app/chat.py), [app/scam_check.py](app/scam_check.py)
  Added 2026-10-29, from the E07 review (cases 3, 18, 24, 51), decided with the user. Today the only way to check a payment is the full `scam_check`: price, then the buyer's chat. A transaction ID pasted on its own still runs `detect_signals` and the explain step (2 Groq calls). The seller who asks "the buyer sent me this ID, is it real?" has to go through the whole flow.
  *To do:* a `scam_check.lookup()` from parts that exist: `TXN_ID` (regex), `_lookup` / `verify_payment` (PayPal), `verdict_for(payment, None)`. 0 LLM calls. In `chat`, a new closed `classify` intent starts it. The LLM decides only *that* a lookup runs, never with what: the ID and the price come from the seller's message by regex, the verdict from code, as today. `verdict_for(payment, None)` already fails closed: a good payment gives VERIFIED_WITH_WARNINGS ("signals unknown"), never VERIFIED, and a missing one WAIT, never SCAM_LIKELY (that needs 2 confirmed signals). The lookup's reply must say why: the chat wasn't read, so warning signs weren't checked. It still shows the shipping steps. A graph change: plan it first, after the retrieval fixes from the same review, since it adds an intent to `classify`.

- [ ] **32. The chat's dispute reply doesn't show the dispute itself.** [app/chat.py](app/chat.py)
  Added 2026-10-30, planned with the user. The chat could show a dispute's status, reason and response deadline, read from PayPal with `get_dispute` (`GET /v1/customer/disputes/{id}`, read-only) and written in code's words.
  *To do, with `dispute_defense` (1–7 Nov):* ask for the dispute ID and read it by regex (`awaiting="dispute_id"`). In the demo, only disputes on the session's own sales. No buyer data in the reply or the state.

- [ ] **34. A chargeback is answered as a PayPal dispute.** [app/chat.py](app/chat.py)
  Added 2026-10-31, from E08 case 12 (`E08-weak-20261031T104006Z`), judged 2, not correct. "I received a chargeback notice" got `DISPUTE_TEXT` and the dispute-vs-claim and 10-day notes, not `pp_chargeback`, and "Who decides the outcome?" got "In a dispute, PayPal does not decide the outcome." The card issuer decides a chargeback.
  *To do, with `dispute_defense` (1–7 Nov):* tell a chargeback from a dispute or claim before any dispute note or draft: `pp_chargeback` first, and never the 10-day dispute deadline for it (no chargeback deadline is verified yet, #21).

- [ ] **35. "What now?" gets steps that don't fit what the seller just said.** [app/chat.py:607](app/chat.py#L607)
  Added 2026-10-31, from E07 and E08 judged by Claude. `advise` answers from the last check or the last sign shown, else "Before you ship":
  - E07 53: after a WAIT, "But I already shipped it. What now?" gets the WAIT steps again, still "before you ship". The reference: contact PayPal now, keep the tracking and the proof of shipment.
  - E08 2: "I posted the item yesterday…" didn't get `vg_already_shipped`, so "anything else?" got "Before you ship".
  - E08 1 and 7: "what do I do with the order?" after an overpayment, and "wait or cancel?" after a fake email, get the shipping checklist; neither says to cancel or refund the whole payment.
  - E07 54: after a VERIFIED check, "the buyer wants me to ship to their cousin's address, that's fine, right?" gets the VERIFIED recap ("You can ship", "no warning signs"), not a no; it also fails the `no_notes` rule.
  *To do:* read "already shipped" in the message itself, not only in the notes shown, and plan what a ship question that brings a new warning sign gets. A graph change: plan it first.

- [ ] **36. Re-checking the same payment says it approved "another order".** [app/scam_check.py](app/scam_check.py)
  Added 2026-10-31, from E07 case 44, judged 2, not correct. A second check of a payment that the first check of this chat had VERIFIED gets "WAIT: Don't ship. This payment was already used to approve another order." It approved this same order.
  *To do:* when the claim fails, say the payment was already approved by Vendor Guard, and for which case when it is this chat's, instead of "another order".

- [ ] **37. A question at the chat step is checked as the buyer's chat.** [app/chat.py:485](app/chat.py#L485)
  Added 2026-10-31, from E07 case 50, judged 2. "Wait, what does SCAM_LIKELY mean?", sent when Vendor Guard asked for the buyer's chat, ran `scam_check` and got a WAIT verdict with a case. The reference: no verdict, say no payment ID was found, ask for the chat again, and say cancel lets the seller ask.
  *To do:* decide in code what at the chat step is not a buyer's chat (no transaction ID, a question mark, no `Buyer:` label?). A buyer's chat with no ID is valid input today and gets WAIT, so this needs a plan first.

- [ ] **38. `classify` picks notes that don't fit the message.** [app/chat.py:407](app/chat.py#L407)
  Added 2026-10-31, from E06, E07 and E08 judged by Claude:
  - E07 17: "Can a card buyer take the money back after I ship?" got the refund note, not `pp_chargeback`.
  - E07 24: "Does that mean the buyer is a scammer?", after a WAIT for an ID PayPal can't find, got `vg_verdicts`, not `pp_different_ids`.
  - E07 26: "What should I ask the buyer?" after a too-old payment got the email warning, not `vg_wait_too_old`.
  - E08 5: "The buyer wants to continue on WhatsApp…" was `other` (the intro), so `vg_signal_off_platform` never came.
  - E06: 5 of 40 single messages show an extra note beyond `must` and `ok` (cases 5, 7, 9, 10, 12), mostly relevant ones: decide whether the dataset's `ok` lists or the picks are wrong.
  *To do:* the retrieval fixes, case by case; rerun E06, E07 and E08 after each prompt change.
