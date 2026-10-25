import json
import os

import httpx
import pytest
import respx

# tests never see real credentials; the secret values are canaries that must never reach a prompt
os.environ.update(
    PAYPAL_CLIENT_ID="test-id",
    PAYPAL_CLIENT_SECRET="CANARY-PP-SECRET",
    PAYPAL_WEBHOOK_ID="WH-TEST",
    PAYPAL_BASE_URL="https://api-m.sandbox.paypal.com",
    DATABASE_URL="postgresql://unused",
    GROQ_API_KEY="gsk_CANARY",  # app.llm builds its client at import, so this must be set first
)

from app import db, paypal, ratelimit  # noqa: E402

BEARER = "A21AA-CANARY"


@pytest.fixture(autouse=True)
def fresh_token():
    paypal._token.update(value=None, exp=0.0)


@pytest.fixture(autouse=True)
def fresh_limits():
    ratelimit._hits.clear()  # every test client calls from the same address


@pytest.fixture
def api():
    with respx.mock(base_url=paypal.BASE, assert_all_called=False) as m:
        m.post("/v1/oauth2/token", name="token").mock(
            return_value=httpx.Response(200, json={"access_token": BEARER, "expires_in": 3600})
        )
        yield m


@pytest.fixture
def saved(monkeypatch):
    """Fake db.save_case that fails, like Jsonb would, on anything that is not plain JSON."""
    rows = []
    monkeypatch.setattr(db, "save_case", lambda case_id, verdict, result: rows.append(json.loads(json.dumps(result))))
    return rows
