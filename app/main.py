import json
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from functools import partial

import anyio
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import db, paypal, scam_check


REQUIRED_ENV = ("DATABASE_URL", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID")


@asynccontextmanager
async def lifespan(_):
    # fail at boot, not on the first webhook: a missing value would otherwise pass /healthz and 500 every call
    if missing := [k for k in REQUIRED_ENV if not os.getenv(k, "").strip()]:
        raise RuntimeError(f"missing env vars: {', '.join(missing)}")
    db.init()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz():  # async: never waits for a worker thread, so busy checks can't fail Render's health check
    return {"ok": True}


def _handle(headers, body: bytes):
    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(400, "invalid json")
    if not paypal.verify_webhook(headers, body):
        raise HTTPException(400, "bad signature")
    return {"status": "stored" if db.claim_event(event) else "duplicate"}


@app.post("/webhooks/paypal")
async def webhook(request: Request):
    return await run_in_threadpool(_handle, request.headers, await request.body())


class ScamCheckIn(BaseModel):
    text: str = Field(min_length=1, max_length=scam_check.MAX_CHARS)
    expected_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)  # the seller's price, never the buyer's
    expected_currency: str = Field(pattern=r"^[A-Z]{3}$")


# ponytail: one global cap of 2 checks at a time; per-seller rate limit once auth lands (RECHECK #3)
SCAM_LIMIT = anyio.CapacityLimiter(2)


@app.post("/scam-check")
async def scam_check_route(body: ScamCheckIn):
    if len(scam_check.sanitize(body.text)) > scam_check.MAX_CHARS:  # NFKC can make text longer
        raise HTTPException(422, "chat too long")
    try:
        SCAM_LIMIT.acquire_nowait()  # take the slot now: check-then-wait lets a whole burst through to queue
    except anyio.WouldBlock:
        raise HTTPException(503, "busy, try again")
    try:
        run = partial(scam_check.run, body.text, str(body.expected_amount), body.expected_currency)
        return await anyio.to_thread.run_sync(run)
    finally:
        SCAM_LIMIT.release()
