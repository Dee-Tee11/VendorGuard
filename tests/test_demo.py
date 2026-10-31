"""The live demo, offline: PayPal mocked with respx, Groq stubbed like llm_stub, the demo tables replaced by a dict."""
import base64
import itertools
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import chat, db, demo, main, paypal, ratelimit, scam_check
from tests.conftest import BEARER
from tests.test_chat import choice  # noqa: F401  (a fixture)
from tests.test_scam_check import (  # noqa: F401  (claimed is an autouse fixture, llm_stub a fixture)
    PAID, SCAM_CHAT, TID, TWO_SIGNALS, capture, claimed, llm_stub,
)

client = TestClient(main.app)  # no `with`: skips lifespan, so no DB needed
USD_PAID = {"id": TID, "status": "COMPLETED", "amount": {"value": "45.00", "currency_code": "USD"},
            "create_time": "2099-01-01T00:00:00Z", "links": [{"href": "https://x", "rel": "self"}]}
CHECK = {"text": PAID, "expected_amount": "45.00", "expected_currency": "USD"}
NODES = ["ingest_input", "read_screenshot", "sanitize_untrusted", "detect_signals", "extract_claims",
         "verify_payment", "compute_verdict", "explain_and_reply", "validate_output", "persist_case"]


@pytest.fixture
def demo_db(monkeypatch):
    """The demo on, with fake demo tables: sessions -> {"sales": {order_id: row}, "cases": [case_id],
    "handoffs": [row]}, the handoffs as the SQL keeps them: one open per session, answered once (tests/test_db.py)."""
    monkeypatch.setattr(demo, "ENABLED", True)
    monkeypatch.setenv("DEMO_BUYER_EMAIL", "buyer@example.com")
    monkeypatch.setenv("DEMO_BUYER_PASSWORD", "sandbox-pass")
    sessions, ids = {}, itertools.count(1)
    monkeypatch.setattr(db, "new_demo_session",
                        lambda sid: sessions.setdefault(sid, {"sales": {}, "cases": [], "handoffs": []}))

    def add_handoff(sid, transcript, case):
        rows = sessions[sid]["handoffs"]
        if not rows or rows[-1]["status"] != "open":
            rows.append({"id": next(ids), "status": "open", "reply": None, "shared_at": "2026-10-30T12:00:00+00:00"})
        rows[-1].update(transcript=transcript, last_case=case)

    def answer_handoff(hid, reply):
        for sid, s in sessions.items():
            for row in s["handoffs"]:
                if row["id"] == hid and row["status"] == "open":
                    row.update(status="answered", reply=reply)
                    return sid
        return None

    monkeypatch.setattr(db, "add_handoff", add_handoff)
    monkeypatch.setattr(db, "answer_handoff", answer_handoff)
    monkeypatch.setattr(db, "open_handoffs", lambda: [
        {k: r[k] for k in ("id", "transcript", "last_case", "shared_at")}
        for s in sessions.values() for r in s["handoffs"] if r["status"] == "open"])
    monkeypatch.setattr(db, "touch_demo_session", lambda sid: sid in sessions)
    monkeypatch.setattr(db, "end_demo_sessions", lambda sid=None: [sid] if sid and sessions.pop(sid, None) else [])
    monkeypatch.setattr(db, "add_demo_sale", lambda sid, oid, item, amount: sessions[sid]["sales"].update(
        {oid: {"order_id": oid, "item": item, "amount": amount}}))
    monkeypatch.setattr(db, "demo_sale_exists", lambda sid, oid: oid in sessions[sid]["sales"])
    monkeypatch.setattr(db, "set_demo_capture", lambda oid, cid, status: None)
    monkeypatch.setattr(db, "add_demo_case", lambda sid, case_id: sessions[sid]["cases"].append(case_id))
    monkeypatch.setattr(db, "demo_state", lambda sid: {
        "sales": list(sessions[sid]["sales"].values()), "events": [],
        "handoffs": [{k: r[k] for k in ("id", "status", "reply")} for r in sessions[sid]["handoffs"]]})
    monkeypatch.setattr(db, "save_case", lambda case_id, verdict, result: None)
    return sessions


def start(ip="1.1.1.1"):
    r = client.post("/demo/api/session", headers={"X-Forwarded-For": ip})
    assert r.status_code == 200
    return {"X-Demo-Session": r.json()["session_id"], "X-Forwarded-For": ip}


def lines(r):
    return [json.loads(x) for x in r.text.splitlines()]


def test_d01_off_by_default():
    for method, path in [("GET", "/demo"), ("POST", "/demo/api/session"), ("POST", "/demo/api/check"),
                         ("POST", "/demo/api/chat"), ("POST", "/demo/api/price"), ("GET", "/demo/api/operator/handoffs")]:
        assert client.request(method, path, json=CHECK).status_code == 404


def test_d02_never_starts_against_live_paypal_or_without_the_buyer_account(demo_db, monkeypatch):
    monkeypatch.delenv("DEMO_BUYER_PASSWORD")
    with pytest.raises(RuntimeError, match="DEMO_BUYER_PASSWORD"), TestClient(main.app):
        pass
    monkeypatch.setenv("DEMO_BUYER_PASSWORD", "sandbox-pass")
    monkeypatch.setattr(paypal, "BASE", "https://api-m.paypal.com")
    with pytest.raises(RuntimeError, match="sandbox"), TestClient(main.app):
        pass


def test_d03_session_gives_the_page_what_it_needs(demo_db):
    r = client.post("/demo/api/session").json()
    assert r["client_id"] == "test-id" and r["buyer_email"] == "buyer@example.com" and r["currency"] == "USD"
    assert "CANARY" not in json.dumps(r)  # the client id is public; the secret never leaves the server
    assert client.get("/demo").headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("headers", [{}, {"X-Demo-Session": "gone"}], ids=["none", "ended"])
def test_d04_no_session_is_401(demo_db, headers):
    assert client.get("/demo/api/state", headers=headers).status_code == 401
    assert client.post("/demo/api/check", json=CHECK, headers=headers).status_code == 401


def test_d05_a_sale_is_created_and_captured_once(api, demo_db):
    h = start()
    order = api.post("/v2/checkout/orders").mock(return_value=httpx.Response(201, json={"id": "ORDER1"}))
    cap = api.post("/v2/checkout/orders/ORDER1/capture").mock(return_value=httpx.Response(201, json={
        "purchase_units": [{"payments": {"captures": [{"id": TID, "status": "COMPLETED",
                                                       "amount": {"value": "45.00", "currency_code": "USD"}}]}}]}))
    assert client.post("/demo/api/orders", json={"item": "Camera", "amount": "45"}, headers=h).json() == \
        {"order_id": "ORDER1"}
    sent = json.loads(order.calls[0].request.content)["purchase_units"][0]
    assert sent["amount"] == {"currency_code": "USD", "value": "45.00"} and sent["description"] == "Camera"
    r = client.post("/demo/api/orders/ORDER1/capture", headers=h)
    assert r.json() == {"capture_id": TID, "status": "COMPLETED", "amount": "45.00", "currency": "USD"}
    assert cap.calls[0].request.headers["PayPal-Request-Id"] == "capture-ORDER1"  # a retry can't capture twice


def test_d06_another_sessions_order_is_never_captured(api, demo_db):
    mine, theirs = start(), start()
    api.post("/v2/checkout/orders").mock(return_value=httpx.Response(201, json={"id": "ORDER1"}))
    cap = api.post("/v2/checkout/orders/ORDER1/capture")
    client.post("/demo/api/orders", json={"item": "Camera", "amount": "45"}, headers=theirs)
    assert client.post("/demo/api/orders/ORDER1/capture", headers=mine).status_code == 404
    assert not cap.called


@pytest.mark.parametrize("amount", ["0.50", "1000.01", "12.345"])
def test_d07_prices_stay_between_1_and_1000_usd(demo_db, amount):
    assert client.post("/demo/api/orders", json={"item": "Camera", "amount": amount}, headers=start()).status_code == 422


def test_d08_the_check_streams_every_node_and_paypal_call_then_the_same_result(api, llm_stub, demo_db):
    h = start()
    capture(api, httpx.Response(200, json=USD_PAID))
    out = lines(client.post("/demo/api/check", json=CHECK, headers=h))
    nodes = [x["node"] for x in out if x["type"] == "node"]
    assert sorted(nodes) == sorted(NODES) and nodes[0] == "ingest_input" and nodes[-1] == "persist_case"
    call = next(x for x in out if x["type"] == "paypal")
    assert call["path"] == f"/v2/payments/captures/{TID}" and call["status"] == 200 and "links" not in call["body"]
    assert out.index(call) < next(i for i, x in enumerate(out) if x.get("node") == "verify_payment")
    result = out[-1]
    assert result["type"] == "result" and result["verdict"] == "VERIFIED"
    case_id = next(x["update"]["case_id"] for x in out if x.get("node") == "ingest_input")
    assert demo_db[h["X-Demo-Session"]]["cases"] == [case_id]  # stored before the check ends, so ending deletes it
    assert BEARER not in json.dumps(out) and "CANARY" not in json.dumps(out)


def test_d08_the_stream_shows_signals_and_the_rule(api, llm_stub, demo_db):
    llm_stub.signals = TWO_SIGNALS
    capture(api, httpx.Response(404))
    api.get("/v1/reporting/transactions").mock(return_value=httpx.Response(200, json={"transaction_details": []}))
    out = lines(client.post("/demo/api/check", json=CHECK | {"text": SCAM_CHAT}, headers=start()))
    update = {x["node"]: x["update"] for x in out if x["type"] == "node"}
    assert {s["type"] for s in update["detect_signals"]["signals"]} == {"URGENCY_PRESSURE", "OFF_PLATFORM"}
    assert update["compute_verdict"]["policy_id"] == "P.SCAM_LIKELY" and out[-1]["verdict"] == "SCAM_LIKELY"
    assert [x["status"] for x in out if x["type"] == "paypal"] == [404, 200]


def test_d09_an_unreadable_screenshot_is_an_error_line(api, llm_stub, demo_db):
    llm_stub.transcript = RuntimeError("vision down")
    out = lines(client.post("/demo/api/check", json=CHECK | {"text": "", "image": "iVBORw0KGgoAAAAAAAAAAA=="},
                            headers=start()))
    assert out[-1]["type"] == "error" and "screenshot" in out[-1]["message"]


class Full:
    def acquire_nowait(self):
        raise main.anyio.WouldBlock

    def release(self):
        raise AssertionError("released a slot it never took")


def test_d10_busy_is_an_error_line_and_every_check_gives_its_slot_back(api, llm_stub, demo_db, monkeypatch):
    capture(api, httpx.Response(200, json=USD_PAID))
    monkeypatch.setattr(main, "SCAM_LIMIT", Full())
    assert lines(client.post("/demo/api/check", json=CHECK, headers=start()))[-1] == \
        {"type": "error", "message": "busy, try again in a moment"}
    monkeypatch.setattr(main, "SCAM_LIMIT", main.anyio.CapacityLimiter(1))
    for _ in range(2):  # a slot that was not given back would make the second check busy
        assert lines(client.post("/demo/api/check", json=CHECK, headers=start()))[-1]["type"] == "result"


def test_d11_ten_checks_an_hour_per_ip(monkeypatch):
    monkeypatch.setattr(scam_check, "run", lambda *args: {"verdict": "WAIT"})
    post = lambda xff: client.post("/scam-check", json=CHECK, headers={"X-Forwarded-For": xff})  # noqa: E731
    assert [post(f"6.6.6.{i}, 9.9.9.9").status_code for i in range(11)] == [200] * 10 + [429]  # forged entries ignored
    assert post("8.8.8.8").status_code == 200


def test_d11_three_screenshots_an_hour_per_ip(monkeypatch):
    monkeypatch.setattr(scam_check, "run", lambda *args: {"verdict": "WAIT"})
    shot = CHECK | {"image": "iVBORw0KGgoAAAAAAAAAAA=="}
    assert [client.post("/scam-check", json=shot).status_code for _ in range(4)] == [200, 200, 200, 429]
    assert client.post("/scam-check", json=CHECK).status_code == 200  # the refused one counted for nothing
    assert len(ratelimit._hits[("testclient", "check")]) == 4


def say(h, message):
    return client.post("/demo/api/chat", json={"message": message}, headers=h)


def test_d12_a_question_is_routed_and_answered_from_a_verified_note(demo_db, choice):
    key = "vg_why_wait"
    choice.intent, choice.entries = "question", [key]
    r = say(start(), "What does WAIT mean?").json()
    assert r["route"] == "question" and r["intent"] == "question" and r["notes"] == [key]
    assert chat.VERIFIED[key]["text"] in r["reply"] and len(choice.calls) == 1


def test_d13_the_chat_runs_a_check_over_three_messages_in_its_own_session(api, demo_db, choice):
    capture(api, httpx.Response(200, json=USD_PAID))
    h, other = start(), start("2.2.2.2")
    assert say(h, "check").json() == {"reply": chat.ASK_PRICE, "route": "start_check", "intent": None, "notes": [],
                                      "awaiting": "price"}
    assert say(h, "45.00 USD").json()["awaiting"] == "chat"
    r = say(h, PAID).json()
    assert r["route"] == "check" and r["reply"].startswith("VERIFIED: ") and r["awaiting"] is None
    case_id = r["reply"].rsplit("Case ", 1)[1]
    assert demo_db[h["X-Demo-Session"]]["cases"] == [case_id]  # so ending the session deletes it
    assert choice.calls == []  # the check flow never calls classify
    assert say(other, "45.00 USD").json()["route"] != "check"  # another session's thread knows nothing of the price
    assert len(ratelimit._hits[("1.1.1.1", "check")]) == 1  # the buyer's chat counted as a check


def test_d14_the_message_that_runs_a_check_needs_a_free_slot(api, demo_db, choice, monkeypatch):
    h = start()
    say(h, "check")
    say(h, "45.00 USD")
    monkeypatch.setattr(main, "SCAM_LIMIT", Full())
    assert say(h, PAID).status_code == 503
    assert demo.runs_check(h["X-Demo-Session"], PAID)  # nothing ran: the chat can be sent again
    assert say(h, " ").json()["reply"] == chat.BAD_CHAT  # check() runs no scam_check for it: no slot, not counted
    assert say(start(), "hello").status_code == 200  # other messages don't need a slot
    assert len(ratelimit._hits[("1.1.1.1", "check")]) == 1  # the chat refused for want of a slot, not the blank one


def awaiting(h):
    return demo.chat_graph.get_state(demo._thread(h["X-Demo-Session"])).values.get("awaiting")


def test_d15_ending_or_sweeping_the_session_forgets_its_conversation(demo_db, choice, monkeypatch):
    ended, idle = start(), start()
    for h in (ended, idle):
        say(h, "check")
    assert awaiting(ended) == awaiting(idle) == "price"
    client.post("/demo/api/end", headers=ended)
    assert demo.chat_graph.get_state(demo._thread(ended["X-Demo-Session"])).values == {}
    monkeypatch.setattr(db, "end_demo_sessions", lambda sid=None: [] if sid else [idle["X-Demo-Session"]])
    start()  # sweeps the idle session
    assert demo.chat_graph.get_state(demo._thread(idle["X-Demo-Session"])).values == {}


def test_d16_a_session_takes_one_message_at_a_time(demo_db, choice):
    h = start()
    demo._busy.add(h["X-Demo-Session"])  # a message still running
    assert say(h, "hello").status_code == 409
    assert client.post("/demo/api/end", headers=h).status_code == 409  # can't delete the thread under it
    demo._busy.discard(h["X-Demo-Session"])
    assert say(h, "hello").status_code == 200 and say(h, "hello").status_code == 200  # given back every time
    assert client.post("/demo/api/end", headers=h).status_code == 200 and not demo._busy
    assert say(h, "hello").status_code == 401


def test_d17_a_screenshot_in_the_chat_runs_the_check_and_counts_as_one(demo_db, choice, monkeypatch):
    ran = []
    monkeypatch.setattr(scam_check, "run", lambda *args: ran.append(args) or {
        "case_id": "c1", "verdict": "WAIT", "policy_id": "P.WAIT_NOT_CONFIRMED", "message": "m", "sentences": [],
        "steps": ["s"], "support": "help", "evidence": {"E1": {}}, "persisted": True})
    h, png = start(), "iVBORw0KGgoAAAAAAAAAAA=="
    early = client.post("/demo/api/chat", json={"message": "", "image": png}, headers=h).json()
    assert early["route"] == "start_check" and not ran  # before the price: not read, not a check
    say(h, "45.00 USD")
    r = client.post("/demo/api/chat", json={"message": "", "image": png}, headers=h).json()
    assert r["route"] == "check" and ran == [("", "45.00", "USD", base64.b64decode(png))]
    assert [len(ratelimit._hits[("1.1.1.1", k)]) for k in ("check", "screenshot")] == [1, 1]
    for bad in ({"message": ""}, {"message": "", "image": "R0lGODlhAQABAAAAACw="}, {"image": "data:image/png;base64,x"}):
        assert client.post("/demo/api/chat", json=bad, headers=h).status_code == 422


def test_d18_a_screenshot_suggests_a_price_and_never_runs_a_check(demo_db, monkeypatch):
    said = iter(["45.00 USD", "You paid $45.00", "NONE", "12 EUR", "1 USD"])
    monkeypatch.setattr(main.llm, "read_image", lambda prompt, image: next(said))
    monkeypatch.setattr(scam_check, "run", lambda *args: pytest.fail("a price read must not run a check"))
    h, png = start(), {"image": "iVBORw0KGgoAAAAAAAAAAA=="}
    got = [client.post("/demo/api/price", json=png, headers=h).json() for _ in range(5)]
    assert got == [{"amount": "45.00", "currency": "USD"}] + [{"amount": None, "currency": None}] * 2 + [
        {"amount": "12", "currency": "EUR"}, {"amount": "1", "currency": "USD"}]  # only what parse_price accepts
    assert client.post("/demo/api/price", json=png, headers=h).status_code == 429  # 5 an hour: each is a vision call
    assert client.post("/demo/api/price", json=png).status_code == 401


def test_d19_the_panels_check_is_the_chats_last_case(api, llm_stub, demo_db):
    h = start()
    capture(api, httpx.Response(200, json=USD_PAID))
    result = lines(client.post("/demo/api/check", json=CHECK, headers=h))[-1]
    values = demo.chat_graph.get_state(demo._thread(h["X-Demo-Session"])).values
    assert values["last_case"] == {"case_id": result["case_id"], "policy_id": "P.VERIFIED", "signals": [],
                                  "price": "45.00 USD", "paid": "45.00 USD"}
    assert values["shown"] == [] and not demo._busy  # as a chat check leaves it: no note from before the check


def test_d19_the_panel_and_the_chat_take_turns(api, llm_stub, demo_db):
    h = start()
    with demo.busy(h["X-Demo-Session"]):  # a chat message of this session is running
        assert lines(client.post("/demo/api/check", json=CHECK, headers=h)) == [
            {"type": "error", "message": "your last message or check is still running, wait for its answer"}]
    assert not demo._busy


FAKE = {"case_id": "c1", "verdict": "WAIT", "policy_id": "P.WAIT_NOT_CONFIRMED", "message": "m", "sentences": [],
        "steps": ["s"], "support": "help", "evidence": {"E1": {}}, "persisted": True}
TOKEN = "t" * 40


def test_d20_only_a_yes_hands_the_whole_conversation_over(demo_db, choice, monkeypatch):
    monkeypatch.setattr(demo, "OPERATOR_TOKEN", TOKEN)
    monkeypatch.setattr(chat, "TAVILY_KEY", "")
    monkeypatch.setattr(scam_check, "run", lambda *args: FAKE)
    h, png = start(), "iVBORw0KGgoAAAAAAAAAAA=="
    handoffs = demo_db[h["X-Demo-Session"]]["handoffs"]
    choice.intent = "question"
    say(h, "Can I sell gift cards?")
    client.post("/demo/api/chat", json={"message": "", "image": png}, headers=h)  # before check: never read
    say(h, "45.00 USD")
    client.post("/demo/api/chat", json={"message": "Buyer: paid", "image": png}, headers=h)  # the check reads it
    r = say(h, "operator").json()
    assert r["route"] == "handoff" and r["reply"] == chat.ASK_CONSENT and not handoffs
    assert say(h, "no").json()["reply"] == chat.NOT_SHARED and not handoffs
    say(h, "operator")
    assert say(h, "yes").json()["reply"] == chat.SHARED
    (row,) = handoffs
    assert row["last_case"] == {"case_id": "c1", "policy_id": "P.WAIT_NOT_CONFIRMED", "signals": []}
    assert row["transcript"] == [
        {"who": "seller", "text": "Can I sell gift cards?"},
        {"who": "vendor_guard", "text": f"{chat.DONT_KNOW}\n\n{chat.OFFER_OPERATOR}"},
        {"who": "seller", "text": "[screenshot, not read]"},
        {"who": "vendor_guard", "text": chat.SCREENSHOT_FIRST},
        {"who": "seller", "text": "45.00 USD"},
        {"who": "vendor_guard", "text": chat.ASK_CHAT},
        {"who": "seller", "text": "Buyer: paid", "image": f"data:image/png;base64,{png}"},
        {"who": "vendor_guard", "text": chat.card(FAKE)},
        {"who": "seller", "text": "operator"},
        {"who": "vendor_guard", "text": chat.ASK_CONSENT},
        {"who": "seller", "text": "no"},
        {"who": "vendor_guard", "text": chat.NOT_SHARED},
        {"who": "seller", "text": "operator"},
        {"who": "vendor_guard", "text": chat.ASK_CONSENT},
        {"who": "seller", "text": "yes"},
        {"who": "vendor_guard", "text": chat.SHARED},
    ]
    first = row["transcript"]
    say(h, "operator")
    say(h, "yes")  # a second yes updates the open handoff, as handoffs_one_open makes the SQL do
    assert handoffs == [row] and row["transcript"][:16] == first and len(row["transcript"]) == 20


def test_d21_ending_or_sweeping_the_session_forgets_its_transcript(demo_db, choice, monkeypatch):
    monkeypatch.setattr(demo, "OPERATOR_TOKEN", TOKEN)
    ended, idle = start(), start()
    for h in (ended, idle):
        say(h, "hello")
    assert {ended["X-Demo-Session"], idle["X-Demo-Session"]} <= set(demo._transcripts)
    client.post("/demo/api/end", headers=ended)
    monkeypatch.setattr(db, "end_demo_sessions", lambda sid=None: [] if sid else [idle["X-Demo-Session"]])
    start()  # sweeps the idle session
    assert not {ended["X-Demo-Session"], idle["X-Demo-Session"]} & set(demo._transcripts)


def test_d22_a_handoff_the_database_refuses_leaves_no_sent_behind(demo_db, choice, monkeypatch):
    monkeypatch.setattr(demo, "OPERATOR_TOKEN", TOKEN)
    h = start()
    say(h, "operator")

    def refuse(*args):
        raise RuntimeError("database down")

    monkeypatch.setattr(db, "add_handoff", refuse)
    with pytest.raises(RuntimeError):  # a 500: the seller never sees "Sent"
        say(h, "yes")
    assert demo._transcripts[h["X-Demo-Session"]][-1] == {"who": "vendor_guard", "text": chat.ASK_CONSENT}
    assert not demo._busy and say(h, "operator").json()["reply"] == chat.ASK_CONSENT  # asked again


def test_d23_a_panel_check_joins_the_transcript_as_its_card_never_the_buyers_chat(api, llm_stub, demo_db, monkeypatch):
    monkeypatch.setattr(demo, "OPERATOR_TOKEN", TOKEN)
    h = start()
    capture(api, httpx.Response(200, json=USD_PAID))
    result = lines(client.post("/demo/api/check", json=CHECK, headers=h))[-1]
    assert demo._transcripts[h["X-Demo-Session"]] == [{"who": "check_panel", "text": chat.card(result)}]
    assert PAID not in json.dumps(demo._transcripts[h["X-Demo-Session"]])


OPERATOR = [("GET", "/demo/api/operator/handoffs", None), ("POST", "/demo/api/operator/handoffs/1/reply", {"reply": "Hi"})]


def test_d24_the_operator_api_needs_its_token_in_a_header(demo_db, monkeypatch):
    called = []
    monkeypatch.setattr(db, "open_handoffs", lambda: called.append("open") or [])
    monkeypatch.setattr(db, "answer_handoff", lambda hid, reply: called.append("answer"))
    for method, path, body in OPERATOR:  # no OPERATOR_TOKEN: there is no operator API
        assert client.request(method, path, json=body, headers={"X-Operator-Token": TOKEN}).status_code == 404
    assert client.post("/demo/api/session").json()["operators"] is False
    monkeypatch.setattr(demo, "OPERATOR_TOKEN", TOKEN)
    for method, path, body in OPERATOR:
        for headers in ({}, {"X-Operator-Token": "x" * 40}):
            assert client.request(method, path, json=body, headers=headers).status_code == 401
        assert client.request(method, f"{path}?token={TOKEN}", json=body).status_code == 401  # never in a URL
    assert not called and client.post("/demo/api/session").json()["operators"] is True


def test_d25_a_persons_answer_reaches_the_seller_but_never_the_graph(demo_db, choice, monkeypatch):
    monkeypatch.setattr(demo, "OPERATOR_TOKEN", TOKEN)
    op, h = {"X-Operator-Token": TOKEN}, start()
    say(h, "operator")
    say(h, "yes")
    (shared,) = client.get("/demo/api/operator/handoffs", headers=op).json()
    assert "session_id" not in shared and shared["transcript"][-1] == {"who": "vendor_guard", "text": chat.SHARED}
    url = f"/demo/api/operator/handoffs/{shared['id']}/reply"
    assert client.post(url, json={"reply": "x" * 2001}, headers=op).status_code == 422
    for bad in (0, 2**63):  # out of bigint's range: a 422, never psycopg's 500
        assert client.post(f"/demo/api/operator/handoffs/{bad}/reply", json={"reply": "Hi"}, headers=op).status_code == 422
    answer = "Hi, Ana from Vendor Guard. Don't ship until PayPal shows the payment as Completed."
    assert client.post(url, json={"reply": answer}, headers=op).json() == {"answered": True}
    assert client.post(url, json={"reply": "again"}, headers=op).status_code == 409
    assert client.get("/demo/api/state", headers=h).json()["handoffs"] == [
        {"id": shared["id"], "status": "answered", "reply": answer}]
    assert client.get("/demo/api/operator/handoffs", headers=op).json() == []
    assert demo._transcripts[h["X-Demo-Session"]][-1] == {"who": "operator", "text": answer}
    choice.intent = "other"
    say(h, "thanks")
    values = demo.chat_graph.get_state(demo._thread(h["X-Demo-Session"])).values
    assert "Ana" not in json.dumps(values, default=str) and all("Ana" not in c.user for c in choice.calls)


def test_d26_a_short_operator_token_never_starts(demo_db, monkeypatch):
    monkeypatch.setattr(demo, "OPERATOR_TOKEN", "t" * 31)
    with pytest.raises(RuntimeError, match="OPERATOR_TOKEN"), TestClient(main.app):
        pass


def test_d27_without_an_operator_token_no_person_is_offered_and_nothing_is_kept(api, llm_stub, demo_db, choice,
                                                                                 monkeypatch):
    monkeypatch.setattr(chat, "TAVILY_KEY", "")
    h = start()
    choice.intent = "question"
    assert chat.OFFER_OPERATOR not in say(h, "Can I sell gift cards?").json()["reply"]
    assert chat.DEMO_ONLY in say(h, "operator").json()["reply"]
    assert chat.SHARED not in say(h, "yes").json()["reply"]
    capture(api, httpx.Response(200, json=USD_PAID))
    lines(client.post("/demo/api/check", json=CHECK, headers=h))
    assert h["X-Demo-Session"] not in demo._transcripts and not demo_db[h["X-Demo-Session"]]["handoffs"]


def test_d28_ten_wrong_operator_tokens_lock_the_address_out(demo_db, monkeypatch):
    monkeypatch.setattr(demo, "OPERATOR_TOKEN", TOKEN)
    monkeypatch.setattr(db, "open_handoffs", lambda: [])
    path = "/demo/api/operator/handoffs"
    for _ in range(3):  # the page polls every 4 s: the right token never counts
        assert client.get(path, headers={"X-Operator-Token": TOKEN}).status_code == 200
    for _ in range(10):
        assert client.get(path, headers={"X-Operator-Token": "x" * 40}).status_code == 401
    r = client.get(path, headers={"X-Operator-Token": TOKEN})  # refused before the compare: a guess learns nothing
    assert r.status_code == 429 and "wrong tokens" in r.json()["detail"]
