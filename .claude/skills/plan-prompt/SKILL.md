---
name: plan-prompt
description: Turn what we discussed and its results into a prompt to paste into a new session in plan mode.
disable-model-invocation: true
---

# Plan prompt

The user names a part of this conversation (an assessment, a list of priorities, eval results, a review) and wants it as a **hand-off**: a prompt they paste into a fresh Claude Code session in plan mode. That session sees nothing of this conversation, so the prompt is the only brief it gets. With no argument, take the latest assessment or results in this conversation.

## Steps

1. **Gather the source.** List every item in the part the user named: each proposed change, RECHECK point, open decision, excluded item and result. Done when every item has a line in your list.

2. **Sort the facts.** For each fact the plan rests on, re-check it now in the repo (a grep or a short read) and file it as one of:
   - **Checked**: with the `file:line` you just saw;
   - **Not checked**: from memory, docs or an earlier guess, such as a PayPal field name or endpoint. The new session must confirm it, in PayPal's docs or the sandbox, before planning on it.
   Done when every fact is in one of the two lists. A fact that turned out wrong is fixed or dropped.

3. **Write the prompt** in English, the repo's language, with these sections in this order:
   - One line: what to plan, "don't write code yet", and "answer me in Portuguese (PT-PT)".
   - **Read first**: the files the plan touches, by path and function name, plus `RECHECK.md`, `RISKS.md` (the invariants) and `README.md` when they apply.
   - **Ground rules**: only those this work touches:
     - The LLM never gets tools. A "tool" is a function the graph calls: the LLM decides *that* it runs, code decides *with what* and what the seller reads.
     - A graph change (node, intent, state field) needs the user's go on the plan before any code.
     - No commit or push without an explicit go.
     - A fixed RECHECK point is deleted, and its fix goes in `CHANGES.md`.
     - A write to PayPal needs `interrupt()` and the user's approval.
   - **Checked** and **Not checked**: the two lists from step 2.
   - **What to plan, in order**: one lettered item per piece of work, in the order agreed in the conversation. Each one gives what to do, the RECHECK numbers it closes and the decisions for the user. A decision is a question with the options, and the session gives its recommendation.
   - **For each item, the plan gives**: the files and functions it touches; new tests numbered after the existing series; the invariants it touches and how they hold; extra PayPal and Groq calls per check; the E06/E07 cases to rerun (a `classify` change always reruns E07 cases 37, 46 and 51); the edits to RECHECK, CHANGES and README. Then "give the plan item by item and wait for my go on each."
   - **Not for this plan**: each excluded item with its reason (done by hand, needs a dataset, out of scope).
   Done when every item from step 1 is in either "What to plan" or "Not for this plan".

4. **Hand it over.** Reply in Portuguese. Start with one line "Escrito para: uma sessão nova do Claude Code neste repositório, em plan mode, sem nada desta conversa." Put the prompt in a fenced block opened with four backticks and `markdown`, and closed with four backticks, so the backticks inside it survive the copy. After it, at most two lines on what the new session will stop and ask. Keep the prompt in chat. Write it to a file only when the user asks.
