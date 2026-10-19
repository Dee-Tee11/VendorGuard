import os

import psycopg
from psycopg.types.json import Jsonb

SCHEMA = """CREATE TABLE IF NOT EXISTS webhook_events (
    id text PRIMARY KEY,
    event_type text NOT NULL,
    body jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now())"""


def init() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        c.execute(SCHEMA)


# ponytail: one connection per call, add psycopg_pool if request rate matters
def claim_event(event: dict) -> bool:
    """True if the event id is new (now stored), False if already seen."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        cur = c.execute(
            "INSERT INTO webhook_events (id, event_type, body) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (event["id"], event["event_type"], Jsonb(event)),
        )
        return cur.rowcount == 1
