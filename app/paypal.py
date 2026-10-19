import os
import re
import time

import httpx

BASE = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
CERT_URL = re.compile(r"^https://api(\.sandbox)?\.paypal\.com/")
SIG_HEADERS = ("AUTH-ALGO", "CERT-URL", "TRANSMISSION-ID", "TRANSMISSION-SIG", "TRANSMISSION-TIME")
_token = {"value": None, "exp": 0.0}


def access_token() -> str:
    if time.time() >= _token["exp"]:
        r = httpx.post(
            f"{BASE}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
        )
        r.raise_for_status()
        j = r.json()
        _token.update(value=j["access_token"], exp=time.time() + j["expires_in"] - 60)
    return _token["value"]


def verify_webhook(headers, event: dict) -> bool:
    """False if forged or malformed; raises if PayPal is unreachable (so PayPal retries)."""
    h = {k.upper(): v for k, v in headers.items()}
    sig = {k: h.get(f"PAYPAL-{k}") for k in SIG_HEADERS}
    if not all(sig.values()) or not CERT_URL.match(sig["CERT-URL"]):
        return False
    r = httpx.post(
        f"{BASE}/v1/notifications/verify-webhook-signature",
        headers={"Authorization": f"Bearer {access_token()}"},
        json={k.lower().replace("-", "_"): v for k, v in sig.items()}
        | {"webhook_id": os.environ["PAYPAL_WEBHOOK_ID"], "webhook_event": event},
    )
    r.raise_for_status()
    return r.json()["verification_status"] == "SUCCESS"
