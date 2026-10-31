import os

import psycopg
from psycopg.types.json import Jsonb

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS webhook_events (
    id text PRIMARY KEY,
    event_type text NOT NULL,
    body jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS scam_cases (
    id text PRIMARY KEY,
    verdict text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS claimed_payments (
    transaction_id text PRIMARY KEY,
    case_id text NOT NULL,
    claimed_at timestamptz NOT NULL DEFAULT now())""",
    # the live demo (app/demo.py): an anonymous session, its sales and its checks, all deleted when it ends
    """CREATE TABLE IF NOT EXISTS demo_sessions (
    id text PRIMARY KEY,
    last_seen timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS demo_sales (
    order_id text PRIMARY KEY,
    session_id text NOT NULL REFERENCES demo_sessions ON DELETE CASCADE,
    item text NOT NULL,
    amount text NOT NULL,
    capture_id text,
    capture_status text,
    created_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS demo_cases (
    case_id text PRIMARY KEY,
    session_id text NOT NULL REFERENCES demo_sessions ON DELETE CASCADE)""",
    # a session's conversation, shared with a person from Vendor Guard after the seller's yes (app/chat.py handoff)
    """CREATE TABLE IF NOT EXISTS handoffs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id text NOT NULL REFERENCES demo_sessions ON DELETE CASCADE,
    transcript jsonb NOT NULL,
    last_case jsonb,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered')),
    reply text,
    shared_at timestamptz NOT NULL DEFAULT now(),
    answered_at timestamptz)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS handoffs_one_open ON handoffs (session_id) WHERE status = 'open'",
)
DEMO_IDLE = "30 minutes"  # a demo session with no request for this long is over
# the PayPal ids of a session's sales: webhook events about them have one of these as resource.id
SALE_IDS = ("SELECT order_id FROM demo_sales WHERE session_id = ANY(%(ids)s) "
            "UNION SELECT capture_id FROM demo_sales WHERE session_id = ANY(%(ids)s) AND capture_id IS NOT NULL")
CASE_IDS = "SELECT case_id FROM demo_cases WHERE session_id = ANY(%(ids)s)"


def _connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5)  # default waits 130 s


def init() -> None:
    with _connect() as c:
        for ddl in SCHEMA:
            c.execute(ddl)


def claim_event(event: dict) -> bool:
    """True if the event id is new (now stored), False if already seen."""
    with _connect() as c:
        cur = c.execute(
            "INSERT INTO webhook_events (id, event_type, body) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (event["id"], event["event_type"], Jsonb(event)),
        )
        return cur.rowcount == 1


def claim_payment(transaction_id: str, case_id: str) -> bool:
    """True if no earlier case approved this payment (now claimed for case_id), False if one did."""
    with _connect() as c:
        cur = c.execute(
            "INSERT INTO claimed_payments (transaction_id, case_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (transaction_id, case_id),
        )
        return cur.rowcount == 1


def save_case(case_id: str, verdict: str, result: dict) -> None:
    """One scam_check result. `result` holds no raw chat and no buyer quotes."""
    with _connect() as c:
        c.execute(
            "INSERT INTO scam_cases (id, verdict, result) VALUES (%s, %s, %s)",
            (case_id, verdict, Jsonb(result)),
        )


def new_demo_session(session_id: str) -> None:
    with _connect() as c:
        c.execute("INSERT INTO demo_sessions (id) VALUES (%s)", (session_id,))


def touch_demo_session(session_id: str) -> bool:
    """True, and the session's clock restarts, if it exists and has not been idle for DEMO_IDLE."""
    with _connect() as c:
        cur = c.execute(
            "UPDATE demo_sessions SET last_seen = now() WHERE id = %s AND last_seen > now() - %s::interval",
            (session_id, DEMO_IDLE),
        )
        return cur.rowcount == 1


def add_demo_sale(session_id: str, order_id: str, item: str, amount: str) -> None:
    with _connect() as c:
        c.execute("INSERT INTO demo_sales (order_id, session_id, item, amount) VALUES (%s, %s, %s, %s)",
                  (order_id, session_id, item, amount))


def demo_sale_exists(session_id: str, order_id: str) -> bool:
    with _connect() as c:
        return c.execute("SELECT 1 FROM demo_sales WHERE order_id = %s AND session_id = %s",
                         (order_id, session_id)).fetchone() is not None


def set_demo_capture(order_id: str, capture_id: str, status: str) -> None:
    with _connect() as c:
        c.execute("UPDATE demo_sales SET capture_id = %s, capture_status = %s WHERE order_id = %s",
                  (capture_id, status, order_id))


def add_demo_case(session_id: str, case_id: str) -> None:
    with _connect() as c:
        c.execute("INSERT INTO demo_cases (case_id, session_id) VALUES (%s, %s)", (case_id, session_id))


def add_handoff(session_id: str, transcript: list[dict], last_case: dict | None) -> None:
    """The session's open handoff now holds this transcript: a new one, or the open one updated (one open per
    session, by handoffs_one_open). After an answer, a new one."""
    with _connect() as c:
        c.execute(
            "INSERT INTO handoffs (session_id, transcript, last_case) VALUES (%s, %s, %s) "
            "ON CONFLICT (session_id) WHERE status = 'open' "
            "DO UPDATE SET transcript = EXCLUDED.transcript, last_case = EXCLUDED.last_case, shared_at = now()",
            (session_id, Jsonb(transcript), Jsonb(last_case) if last_case else None),
        )


def open_handoffs() -> list[dict]:
    """Every session's open handoff, oldest first, for the operator. Never the session id: it is the session's key."""
    with _connect() as c:
        rows = c.execute("SELECT id, transcript, last_case, shared_at FROM handoffs WHERE status = 'open' "
                         "ORDER BY shared_at").fetchall()
    return [{"id": r[0], "transcript": r[1], "last_case": r[2], "shared_at": r[3].isoformat()} for r in rows]


def answer_handoff(handoff_id: int, reply: str) -> str | None:
    """The handoff's session id if it was open (now answered with this reply), None if it was answered already or is
    gone. The id stays on the server: it is the session's key."""
    with _connect() as c:
        row = c.execute("UPDATE handoffs SET status = 'answered', reply = %s, answered_at = now() "
                        "WHERE id = %s AND status = 'open' RETURNING session_id", (reply, handoff_id)).fetchone()
    return row[0] if row else None


def demo_state(session_id: str) -> dict:
    """The session's sales, the verified webhook events about them, and its handoffs (never their transcript)."""
    ids = {"ids": [session_id]}
    with _connect() as c:
        sales = c.execute(
            "SELECT order_id, item, amount, capture_id, capture_status FROM demo_sales "
            "WHERE session_id = ANY(%(ids)s) ORDER BY created_at", ids).fetchall()
        events = c.execute(
            "SELECT id, event_type, body->>'summary', body->'resource'->>'id', body->'resource'->>'status', received_at "
            f"FROM webhook_events WHERE body->'resource'->>'id' IN ({SALE_IDS}) ORDER BY received_at", ids).fetchall()
        handoffs = c.execute("SELECT id, status, reply FROM handoffs WHERE session_id = ANY(%(ids)s) ORDER BY id",
                             ids).fetchall()
    return {
        "sales": [dict(zip(("order_id", "item", "amount", "capture_id", "capture_status"), r)) for r in sales],
        "events": [{"id": e[0], "event_type": e[1], "summary": e[2], "resource_id": e[3], "status": e[4],
                    "received_at": e[5].isoformat()} for e in events],
        "handoffs": [dict(zip(("id", "status", "reply"), r)) for r in handoffs],
    }


def end_demo_sessions(session_id: str | None = None) -> list[str]:
    """Deletes one session, or every idle one, with all it left: cases, claimed payments and webhook events.
    Returns the ids it deleted."""
    with _connect() as c:  # one transaction: all of it goes, or none
        if session_id:
            ids = [session_id]
        else:
            ids = [r[0] for r in c.execute("SELECT id FROM demo_sessions WHERE last_seen <= now() - %s::interval",
                                           (DEMO_IDLE,))]
        if not ids:
            return []
        p = {"ids": ids}
        c.execute(f"DELETE FROM claimed_payments WHERE case_id IN ({CASE_IDS})", p)
        c.execute(f"DELETE FROM scam_cases WHERE id IN ({CASE_IDS})", p)
        c.execute(f"DELETE FROM webhook_events WHERE body->'resource'->>'id' IN ({SALE_IDS})", p)
        # cascades to sales, cases and handoffs
        gone = c.execute("DELETE FROM demo_sessions WHERE id = ANY(%(ids)s) RETURNING id", p)
        return [r[0] for r in gone]
