import json
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from functools import partial

import base64
import binascii
from typing import Annotated

import anyio
from fastapi import FastAPI, HTTPException, Request
from pydantic import AfterValidator, BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from app import db, llm, paypal, scam_check


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


def _b64(v: str) -> bytes:
    try:  # strict: the lenient decoder drops a data: prefix's letters into the bytes, then calls a real PNG "not a PNG"
        return base64.b64decode(v, validate=True)
    except binascii.Error:
        raise ValueError("the screenshot must be plain base64, with no data: prefix")


class ScamCheckIn(BaseModel):
    text: str = Field("", max_length=scam_check.MAX_CHARS)
    # capped as base64, before decoding: 4 MB of base64 is 3 MB of image, the cap older Groq docs gave for a base64
    # image (Groq took 3.06 MB on 2026-10-21); a phone screenshot is far smaller
    image: Annotated[str, Field(max_length=4_000_000), AfterValidator(_b64)] | None = None
    expected_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)  # the seller's price, never the buyer's
    expected_currency: str = Field(pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def _text_or_image(self):
        if not self.text and self.image is None:
            raise ValueError("send the chat as text, a screenshot, or both")
        if self.image is not None and not llm.image_type(self.image):
            raise ValueError("the screenshot must be a PNG or JPEG")
        return self


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
        run = partial(scam_check.run, body.text, str(body.expected_amount), body.expected_currency, body.image)
        return await anyio.to_thread.run_sync(run)
    except scam_check.Unreadable as e:
        raise HTTPException(422, str(e))
    finally:
        SCAM_LIMIT.release()
