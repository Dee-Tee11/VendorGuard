"""The live demo: a tester makes a sale, pays it in PayPal's sandbox checkout with a shared buyer account, then checks
the buyer's chat and watches every node and PayPal call (the streamed check is main.demo_check).

On only with DEMO_MODE=true, and only against PayPal's sandbox (main.lifespan refuses to start otherwise). Creating and
capturing orders are the app's only PayPal writes. They play the buyer's purchase and stay out of the graphs.
The session is anonymous: the page keeps its id in sessionStorage, and everything goes when it ends.
"""
import os
import secrets
import uuid
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import db, paypal, ratelimit, scam_check

ENABLED = os.getenv("DEMO_MODE", "").strip().lower() == "true"
REQUIRED_ENV = ("DEMO_BUYER_EMAIL", "DEMO_BUYER_PASSWORD")
CURRENCY = "USD"  # a US sandbox business account, as in the README's Seller Protection notes
PAGE = Path(__file__).parent / "static" / "demo.html"


def enabled():
    if not ENABLED:
        raise HTTPException(404)


def session(x_demo_session: str = Header("", max_length=64)) -> str:
    """The caller's demo session, kept alive by this request. 401 once it ended or went idle."""
    if not x_demo_session or not db.touch_demo_session(x_demo_session):
        raise HTTPException(401, "this demo session has ended, reload the page to start a new one")
    return x_demo_session


router = APIRouter(dependencies=[Depends(enabled)])


class SaleIn(BaseModel):
    item: str = Field(min_length=1, max_length=60)
    amount: Decimal = Field(ge=1, le=1000, max_digits=6, decimal_places=2)


@router.get("/demo", include_in_schema=False)
def page():
    return FileResponse(PAGE)


@router.post("/demo/api/session")
def new_session(request: Request):
    ratelimit.hit(request, "session")
    db.end_demo_sessions()  # ponytail: idle sessions are swept here and at boot; a cron job if traffic ever needs it
    session_id = secrets.token_urlsafe(24)
    db.new_demo_session(session_id)
    return {"session_id": session_id, "client_id": os.environ["PAYPAL_CLIENT_ID"], "currency": CURRENCY,
            "buyer_email": os.environ["DEMO_BUYER_EMAIL"], "buyer_password": os.environ["DEMO_BUYER_PASSWORD"]}


def _paypal_error(r, what: str) -> HTTPException:
    """502 with PayPal's own issue code (INSTRUMENT_DECLINED, ORDER_NOT_APPROVED…), so the tester sees why."""
    try:
        body = r.json()
        issue = (body.get("details") or [{}])[0].get("issue") or body.get("name")
    except (ValueError, AttributeError, IndexError):
        issue = None
    return HTTPException(502, f"PayPal refused the {what} ({r.status_code}{', ' + issue if issue else ''})")


@router.post("/demo/api/orders")
def create_order(body: SaleIn, request: Request, session_id: str = Depends(session)):
    ratelimit.hit(request, "sale")
    item, amount = scam_check.sanitize(body.item) or "Item", f"{body.amount:.2f}"
    r = paypal._call("POST", "/v2/checkout/orders", headers={"PayPal-Request-Id": str(uuid.uuid4())}, json={
        "intent": "CAPTURE",
        "purchase_units": [{"description": item, "amount": {"currency_code": CURRENCY, "value": amount}}],
    })
    if r.is_error:
        raise _paypal_error(r, "order")
    order_id = r.json()["id"]
    db.add_demo_sale(session_id, order_id, item, amount)
    return {"order_id": order_id}


@router.post("/demo/api/orders/{order_id}/capture")
def capture_order(order_id: str, session_id: str = Depends(session)):
    if not db.demo_sale_exists(session_id, order_id):  # the id comes from the browser: only this session's sales
        raise HTTPException(404, "no such sale in this demo session")
    # a fixed request id: if the browser retries, PayPal returns the first capture instead of capturing again
    r = paypal._call("POST", f"/v2/checkout/orders/{order_id}/capture",
                     headers={"PayPal-Request-Id": f"capture-{order_id}"}, json={})
    if r.is_error:
        raise _paypal_error(r, "capture")
    try:
        cap = r.json()["purchase_units"][0]["payments"]["captures"][0]
        out = {"capture_id": cap["id"], "status": cap["status"], "amount": cap["amount"]["value"],
               "currency": cap["amount"]["currency_code"]}
    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, "PayPal's capture response had no capture in it")
    db.set_demo_capture(order_id, out["capture_id"], out["status"])
    return out


@router.get("/demo/api/state")
def state(session_id: str = Depends(session)):
    return db.demo_state(session_id)


@router.post("/demo/api/end")
def end(session_id: str = Depends(session)):
    db.end_demo_sessions(session_id)
    return {"ended": True}
