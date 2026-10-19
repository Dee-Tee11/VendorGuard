import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import db, main, paypal

HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PAYPAL-TRANSMISSION-ID": "t1",
    "PAYPAL-TRANSMISSION-SIG": "sig",
    "PAYPAL-TRANSMISSION-TIME": "2026-10-19T10:00:00Z",
}
EVENT = {"id": "WH-EVT-1", "event_type": "PAYMENT.CAPTURE.COMPLETED"}
client = TestClient(main.app)  # no `with`: skips lifespan, so no DB needed


@pytest.fixture(autouse=True)
def fresh_token():
    paypal._token.update(value=None, exp=0.0)


@pytest.fixture
def api():
    with respx.mock(base_url=paypal.BASE, assert_all_called=False) as m:
        m.post("/v1/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        yield m


@pytest.fixture
def stored(monkeypatch):
    ids = []

    def claim(event):
        new = event["id"] not in ids
        ids.append(event["id"])
        return new

    monkeypatch.setattr(db, "claim_event", claim)
    return ids


def verify(api, status):
    return api.post("/v1/notifications/verify-webhook-signature").mock(
        return_value=httpx.Response(200, json={"verification_status": status})
    )


def post(headers=HEADERS):
    return client.post("/webhooks/paypal", content=json.dumps(EVENT), headers=headers)


def test_u11_failed_signature_is_400_and_nothing_stored(api, stored):
    verify(api, "FAILURE")
    assert post().status_code == 400
    assert stored == []


def test_u12_same_event_twice_is_stored_once(api, stored):
    verify(api, "SUCCESS")
    assert post().json() == {"status": "stored"}
    assert post().json() == {"status": "duplicate"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {**HEADERS, "PAYPAL-CERT-URL": "https://evil.example.com/cert"},
        {k: v for k, v in HEADERS.items() if k != "PAYPAL-TRANSMISSION-SIG"},
    ],
)
def test_s07_forged_webhook_rejected_before_calling_paypal(api, stored, headers):
    route = verify(api, "SUCCESS")
    assert post(headers).status_code == 400
    assert not route.called
