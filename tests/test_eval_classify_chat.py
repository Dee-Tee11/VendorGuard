"""Evals: the real Groq model routing seller messages through the chat graph (E06). Opt-in, like the other evals:

    pytest -m eval -v -s tests/test_eval_classify_chat.py  EVAL_REPS=3 runs each case 3 times

The cases are tests/chat_dataset.jsonl. Each run saves every reply, with what was expected and what each check said,
to tests/eval_results/chat-<UTC time>.jsonl, and the KPIs next to it in a .txt. Made-up messages only: they are all
sent to Groq.
"""
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import groq
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app import chat, llm
from tests.test_eval_output_guardrails import KEY, real_llm  # noqa: F401  (real_llm is an autouse fixture)

pytestmark = [pytest.mark.eval, pytest.mark.skipif(not KEY, reason="needs GROQ_API_KEY in .env")]

HERE = Path(__file__).parent
CASES = [json.loads(line) for line in (HERE / "chat_dataset.jsonl").read_text().splitlines() if line.strip()]
REPS = int(os.getenv("EVAL_REPS", "1"))
ROWS = []
GRAPH = chat._build(InMemorySaver())  # one graph: each case and rep has its own thread_id


def kpis(rows) -> str:
    def rate(name, check, keep=lambda r: True):
        done = [r["checks"][check] for r in rows if keep(r) and r["checks"][check] is not None]
        return f"{name:<28}{sum(done)}/{len(done)}" + (f"  {sum(done) / len(done):.0%}" if done else "")

    cases = defaultdict(list)  # a case passes only if every repetition passed
    for r in rows:
        cases[r["id"]].append(all(v is not False for v in r["checks"].values()))
    return "\n".join([
        rate("routing", "route"),
        rate("intent", "intent"),
        rate("recall scam_check", "intent", lambda r: r["expected"]["intent"] == "scam_check"),
        rate("recall dispute", "intent", lambda r: r["expected"]["intent"] == "dispute"),
        rate("recall not_paypal", "intent", lambda r: r["expected"]["intent"] == "not_paypal"),
        rate("injection", "intent", lambda r: r["kind"] == "injection"),
        rate("required notes", "must"),
        rate("no wrong note shown", "notes"),
        rate("no wrong note, no-note qs", "notes", lambda r: r["expected"]["route"] == "question"
             and not r["expected"]["must"]),
        rate("LLM calls", "calls"),
        rate("ran without error", "ran"),
        f"{'cases passing every rep':<28}{sum(map(all, cases.values()))}/{len(cases)}",
        f"{'rate-limited, retried':<28}{sum(r['rate_limited'] for r in rows)}",
        f"{'tokens':<28}{sum(r['tokens'] for r in rows)}",
    ])


@pytest.fixture(scope="module", autouse=True)
def results():
    yield
    if ROWS:
        (out := HERE / "eval_results").mkdir(exist_ok=True)
        path = out / f"chat-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ROWS))
        path.with_suffix(".txt").write_text(f"{(report := kpis(ROWS))}\n")
        print(f"\n\n{report}\nreplies saved to {path}")


@pytest.fixture
def calls(monkeypatch):
    """Each completion's total tokens, each 429 waited out, and the type of each failed llm.ask (classify then falls
    back to other). No SDK retries, as in the app: a 429 waits a minute, the free tier's window, and tries once more."""
    seen, limited, failed = [], [], []
    monkeypatch.setattr(llm, "client", llm.client.with_options(max_retries=0))  # after real_llm (autouse)
    create, ask = llm.client.chat.completions.create, llm.ask

    def record(**kwargs):
        try:
            r = create(**kwargs)
        except groq.RateLimitError:
            limited.append(1)
            time.sleep(60)
            r = create(**kwargs)
        seen.append(r.usage.total_tokens)
        return r

    def record_ask(*args, **kwargs):  # also catches what fails after a completion: a cut-off or invalid answer
        try:
            return ask(*args, **kwargs)
        except Exception as e:
            failed.append(type(e).__name__)
            raise

    monkeypatch.setattr(llm.client.chat.completions, "create", record)
    monkeypatch.setattr(llm, "ask", record_ask)
    return seen, limited, failed


@pytest.mark.parametrize("i, rep", [(i, rep) for i in range(1, len(CASES) + 1) for rep in range(REPS)],
                         ids=[f"{i}-{c['metadata']['kind']}-r{rep}" for i, c in enumerate(CASES, 1) for rep in range(REPS)])
def test_eval_classify_chat(monkeypatch, calls, i, rep):
    monkeypatch.setattr(chat, "TAVILY_KEY", "")  # routing only: a question must not search the web here
    case, (seen, limited, failed) = CASES[i - 1], calls
    want, (message,) = case["outputs"], case["inputs"]["messages"]  # one user message per case
    config = {"configurable": {"thread_id": f"{i}-{rep}"}}
    nodes, state, error = [None], {}, None
    try:
        nodes = [node for update in GRAPH.stream({"messages": [message]}, config, stream_mode="updates")
                 for node in update] or [None]
        state = GRAPH.get_state(config).values
    except Exception as e:  # still a row, scored as a failure, so the case stays in every KPI's count
        error = f"{type(e).__name__}: {e}"
    intent = state.get("intent") if "classify" in nodes else None
    entries = state.get("entries", []) if "classify" in nodes else []
    shown = entries if nodes[-1] == "question" else []  # only the question node shows notes to the seller
    llm_case = want["intent"] is not None
    checks = {
        "route": nodes[-1] == want["route"],
        "intent": intent == want["intent"] if llm_case else None,
        "must": set(want["must"]) <= set(shown) if want["route"] == "question" else None,
        "notes": set(shown) <= set(want["must"] + want["ok"]) if llm_case else None,
        "calls": len(seen) == llm_case and not failed,
    }
    checks = {k: v and error is None if v is not None else None for k, v in checks.items()} | {"ran": error is None}
    ROWS.append({"id": i, "rep": rep, "kind": case["metadata"]["kind"], "lang": case["metadata"]["lang"],
                 "message": message["content"], "expected": want, "route": nodes[-1], "intent": intent,
                 "entries": entries, "reply": state["messages"][-1].content if state else None,
                 "llm_calls": len(seen), "rate_limited": len(limited), "errors": failed, "error": error,
                 "tokens": sum(seen), "checks": checks})
    assert all(v is not False for v in checks.values()), ROWS[-1]
