import json
import os
import re
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

import httpx

BASE = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
# the cert must live on PayPal's API host for the same environment as BASE: no sandbox cert while live
CERT_URL = re.compile(r"^https://api(-m)?\." + (r"sandbox\." if ".sandbox." in BASE else "") + r"paypal\.com/")
SIG_HEADERS = ("auth-algo", "cert-url", "transmission-id", "transmission-sig", "transmission-time")
_http = httpx.Client(timeout=10)  # one client: reuses TLS connections instead of a handshake per call
_token = {"value": None, "exp": 0.0}
_token_lock = threading.Lock()
# set by the demo's live panel: each call's method, path, status and response body. Never headers or the token
trace: ContextVar[list | None] = ContextVar("paypal_trace", default=None)
TRACE_BODY = 4000  # chars of JSON per call; a bigger body is shown cut, as text


def access_token(stale: str | None = None) -> str:
    """The cached token. Pass `stale` (a token PayPal rejected) to refresh it, unless another thread already did."""
    with _token_lock:  # one refresh at a time; the others wait and reuse it
        if time.time() < _token["exp"] and _token["value"] != stale:
            return _token["value"]
        r = _http.post(
            f"{BASE}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
        )
        r.raise_for_status()
        j = r.json()
        _token.update(value=j["access_token"], exp=time.time() + j["expires_in"] - 60)
        return _token["value"]


def verify_webhook(headers, body: bytes) -> bool:
    """False if forged or malformed; raises if PayPal is unreachable or fails (so PayPal retries)."""
    sig = {k: headers.get(f"paypal-{k}") for k in SIG_HEADERS}  # Starlette headers are case-insensitive
    if not all(sig.values()) or not CERT_URL.match(sig["cert-url"]):
        return False
    fields = {k.replace("-", "_"): v for k, v in sig.items()} | {"webhook_id": os.environ["PAYPAL_WEBHOOK_ID"]}
    # the signature covers a CRC32 of the exact bytes PayPal sent, so splice them in instead of re-serializing
    payload = json.dumps(fields)[:-1].encode() + b', "webhook_event": ' + body + b"}"
    r = _call("POST", "/v1/notifications/verify-webhook-signature", content=payload,
              headers={"Content-Type": "application/json"})
    if r.status_code in (400, 422):  # PayPal rejected the payload: forged input, not an outage
        return False
    r.raise_for_status()
    return r.json().get("verification_status") == "SUCCESS"


def _call(method: str, path: str, headers: dict | None = None, **kwargs) -> httpx.Response:
    """A request with the app token. A 401 refreshes the token and retries once (token revoked before exp)."""
    token = access_token()
    for attempt in (1, 2):
        r = _http.request(method, f"{BASE}{path}", headers={"Authorization": f"Bearer {token}", **(headers or {})},
                          **kwargs)
        if r.status_code != 401 or attempt == 2:
            break
        token = access_token(stale=token)
    if (calls := trace.get()) is not None:
        calls.append({"method": method, "path": path, "params": kwargs.get("params"), "status": r.status_code,
                      "body": _traced_body(r)})
    return r


def _traced_body(r: httpx.Response):
    try:
        body = r.json()
    except ValueError:
        return r.text[:TRACE_BODY]
    if isinstance(body, dict):
        body.pop("links", None)  # HATEOAS links: long and not evidence
    text = json.dumps(body)
    return body if len(text) <= TRACE_BODY else text[:TRACE_BODY] + "…"


def get_capture(capture_id: str) -> dict | None:
    """The seller's capture, or None on 404. Raises on anything else: unavailable is not 'not found'."""
    r = _call("GET", f"/v2/payments/captures/{capture_id}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def search_transaction(transaction_id: str) -> list[dict]:
    """Transaction Search rows for an id in the last 30 days (API max is 31). Can lag up to 3 h."""
    end = datetime.now(timezone.utc).replace(microsecond=0)
    r = _call(
        "GET",
        "/v1/reporting/transactions",
        params={
            "transaction_id": transaction_id,
            "start_date": (end - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "balance_affecting_records_only": "N",  # Y would hide pending rows
        },
        headers={"PayPal-Enforce-ISO8601-Format": "true"},
    )
    r.raise_for_status()
    return [d["transaction_info"] for d in r.json().get("transaction_details", [])]
