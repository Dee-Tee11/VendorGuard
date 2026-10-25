"""The real SQL, against a throwaway Postgres (RECHECK #7). Skipped unless TEST_DATABASE_URL is set:

    docker run -d --rm --name vg-test-pg -e POSTGRES_PASSWORD=test -p 55432:5432 postgres
    TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres pytest tests/test_db.py
"""
import os
import uuid

import pytest

from app import db

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is not set")


@pytest.fixture(autouse=True)
def database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL or "")
    db.init()


def event(resource_id):
    return {"id": f"WH-{uuid.uuid4().hex}", "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "summary": "Payment completed", "resource": {"id": resource_id, "status": "COMPLETED"}}


def demo_session():
    """A session with a captured sale, its webhook events, and a check that claimed the payment."""
    sid, order, cap, case = (uuid.uuid4().hex for _ in range(4))
    db.new_demo_session(sid)
    db.add_demo_sale(sid, order, "Camera", "45.00")
    db.set_demo_capture(order, cap, "COMPLETED")
    db.add_demo_case(sid, case)
    assert db.claim_payment(cap, case)
    db.save_case(case, "VERIFIED", {"verdict": "VERIFIED"})
    assert db.claim_event(event(order)) and db.claim_event(event(cap))
    return sid, order, cap, case


def rows(order, cap, case):
    with db._connect() as c:
        return {
            "demo_sales": c.execute("SELECT count(*) FROM demo_sales WHERE order_id = %s", (order,)).fetchone()[0],
            "demo_cases": c.execute("SELECT count(*) FROM demo_cases WHERE case_id = %s", (case,)).fetchone()[0],
            "scam_cases": c.execute("SELECT count(*) FROM scam_cases WHERE id = %s", (case,)).fetchone()[0],
            "claimed_payments": c.execute("SELECT count(*) FROM claimed_payments WHERE transaction_id = %s",
                                          (cap,)).fetchone()[0],
            "webhook_events": c.execute("SELECT count(*) FROM webhook_events WHERE body->'resource'->>'id' IN (%s, %s)",
                                        (order, cap)).fetchone()[0],
        }


ALL = {"demo_sales": 1, "demo_cases": 1, "scam_cases": 1, "claimed_payments": 1, "webhook_events": 2}
NONE = dict.fromkeys(ALL, 0)


def test_ending_a_session_deletes_all_it_left_and_nothing_else():
    mine, other = demo_session(), demo_session()
    state = db.demo_state(mine[0])
    assert [s["capture_id"] for s in state["sales"]] == [mine[2]] and len(state["events"]) == 2
    db.end_demo_sessions(mine[0])
    assert rows(*mine[1:]) == NONE and not db.touch_demo_session(mine[0])
    assert rows(*other[1:]) == ALL and db.touch_demo_session(other[0])
    db.end_demo_sessions(other[0])


def test_only_idle_sessions_are_swept():
    idle, active = demo_session(), demo_session()
    with db._connect() as c:
        c.execute("UPDATE demo_sessions SET last_seen = now() - interval '31 minutes' WHERE id = %s", (idle[0],))
    assert not db.touch_demo_session(idle[0])  # idle sessions can't be revived
    db.end_demo_sessions()
    assert rows(*idle[1:]) == NONE
    assert rows(*active[1:]) == ALL
    db.end_demo_sessions(active[0])


def test_a_sale_belongs_to_its_session_only():
    mine, other = demo_session(), demo_session()
    assert db.demo_sale_exists(mine[0], mine[1]) and not db.demo_sale_exists(other[0], mine[1])
    db.end_demo_sessions(mine[0])
    db.end_demo_sessions(other[0])


def test_u12_claims_hold_in_the_real_sql():
    e, cap = event(uuid.uuid4().hex), uuid.uuid4().hex
    assert db.claim_event(e) and not db.claim_event(e)  # ON CONFLICT DO NOTHING, rowcount 1 then 0
    assert db.claim_payment(cap, "case-1") and not db.claim_payment(cap, "case-2")
    with db._connect() as c:
        c.execute("DELETE FROM webhook_events WHERE id = %s", (e["id"],))
        c.execute("DELETE FROM claimed_payments WHERE transaction_id = %s", (cap,))
