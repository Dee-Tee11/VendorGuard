"""The live demo, offline: PayPal mocked with respx, Groq stubbed like llm_stub, the demo tables replaced by a dict."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, demo, main, paypal, ratelimit, scam_check
from tests.conftest import BEARER
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
    """The demo on, with fake demo tables: sessions -> {"sales": {order_id: row}, "cases": [case_id]}."""
    monkeypatch.setattr(demo, "ENABLED", True)
    monkeypatch.setenv("DEMO_BUYER_EMAIL", "buyer@example.com")
    monkeypatch.setenv("DEMO_BUYER_PASSWORD", "sandbox-pass")
    sessions = {}
    monkeypatch.setattr(db, "new_demo_session", lambda sid: sessions.setdefault(sid, {"sales": {}, "cases": []}))
    monkeypatch.setattr(db, "touch_demo_session", lambda sid: sid in sessions)
    monkeypatch.setattr(db, "end_demo_sessions", lambda sid=None: sessions.pop(sid, None) if sid else None)
    monkeypatch.setattr(db, "add_demo_sale", lambda sid, oid, item, amount: sessions[sid]["sales"].update(
        {oid: {"order_id": oid, "item": item, "amount": amount}}))
    monkeypatch.setattr(db, "demo_sale_exists", lambda sid, oid: oid in sessions[sid]["sales"])
    monkeypatch.setattr(db, "set_demo_capture", lambda oid, cid, status: None)
    monkeypatch.setattr(db, "add_demo_case", lambda sid, case_id: sessions[sid]["cases"].append(case_id))
    monkeypatch.setattr(db, "demo_state", lambda sid: {"sales": list(sessions[sid]["sales"].values()), "events": []})
    monkeypatch.setattr(db, "save_case", lambda case_id, verdict, result: None)
    return sessions


def start(ip="1.1.1.1"):
    r = client.post("/demo/api/session", headers={"X-Forwarded-For": ip})
    assert r.status_code == 200
    return {"X-Demo-Session": r.json()["session_id"], "X-Forwarded-For": ip}


def lines(r):
    return [json.loads(x) for x in r.text.splitlines()]


def test_d01_off_by_default():
    for method, path in [("GET", "/demo"), ("POST", "/demo/api/session"), ("POST", "/demo/api/check")]:
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
