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
)


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
