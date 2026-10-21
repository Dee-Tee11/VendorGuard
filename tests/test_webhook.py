import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, main, paypal
from tests.conftest import BEARER

HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PAYPAL-TRANSMISSION-ID": "t1",
    "PAYPAL-TRANSMISSION-SIG": "sig",
    "PAYPAL-TRANSMISSION-TIME": "2026-10-19T10:00:00Z",
}
EVENT = {"id": "WH-EVT-1", "event_type": "PAYMENT.CAPTURE.COMPLETED"}
client = TestClient(main.app)  # no `with`: skips lifespan, so no DB needed


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


def test_verify_gets_the_exact_bytes_paypal_sent(api, stored):
    route = verify(api, "SUCCESS")
    raw = b'{"id":"WH-EVT-2",  "event_type":"X", "amount": 1.10, "note": "caf\\u00e9"}'  # json.dumps would change all of these
    assert client.post("/webhooks/paypal", content=raw, headers=HEADERS).json() == {"status": "stored"}
    sent = route.calls.last.request
    assert sent.content.endswith(b'"webhook_event": ' + raw + b"}")
    assert json.loads(sent.content)["webhook_id"] == "WH-TEST"
    assert sent.headers["authorization"] == f"Bearer {BEARER}"


@pytest.mark.parametrize("status, expected", [(400, 400), (422, 400), (500, 500), (403, 500)])
def test_verify_4xx_is_a_bad_signature_and_an_outage_is_500(api, stored, status, expected):
    api.post("/v1/notifications/verify-webhook-signature").mock(return_value=httpx.Response(status))
    assert TestClient(main.app, raise_server_exceptions=False).post(
        "/webhooks/paypal", content=json.dumps(EVENT), headers=HEADERS).status_code == expected
    assert stored == []


def test_verify_refreshes_a_revoked_token_once(api, stored):
    api.post("/v1/notifications/verify-webhook-signature").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"verification_status": "SUCCESS"})])
    assert post().json() == {"status": "stored"}
    assert api["token"].call_count == 2


def test_invalid_json_is_400_without_calling_paypal(api, stored):
    route = verify(api, "SUCCESS")
    assert client.post("/webhooks/paypal", content=b"{not json", headers=HEADERS).status_code == 400
    assert not route.called


@pytest.mark.parametrize("url, ok", [
    ("https://api.sandbox.paypal.com/v1/notifications/certs/C", True),
    ("https://api-m.sandbox.paypal.com/v1/notifications/certs/C", True),
    ("https://api.paypal.com/v1/notifications/certs/C", False),  # live cert while BASE is sandbox
    ("https://api.sandbox.paypal.com.evil.io/c", False),
])
def test_cert_url_must_match_the_environment(url, ok):
    assert bool(paypal.CERT_URL.match(url)) == ok


def test_healthz():
    assert client.get("/healthz").json() == {"ok": True}


def test_app_does_not_start_without_paypal_settings(monkeypatch):
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID")
    with pytest.raises(RuntimeError, match="PAYPAL_WEBHOOK_ID"), TestClient(main.app):
        pass
