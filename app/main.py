import json
import logging
import os
from contextlib import asynccontextmanager
from contextvars import Context
from decimal import Decimal
from functools import partial

import base64
import binascii
from typing import Annotated

import anyio
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import AfterValidator, BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from app import db, demo, llm, paypal, ratelimit, scam_check

log = logging.getLogger(__name__)


REQUIRED_ENV = ("DATABASE_URL", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID")


@asynccontextmanager
async def lifespan(_):
    # fail at boot, not on the first webhook: a missing value would otherwise pass /healthz and 500 every call
    required = REQUIRED_ENV + (demo.REQUIRED_ENV if demo.ENABLED else ())
    if missing := [k for k in required if not os.getenv(k, "").strip()]:
        raise RuntimeError(f"missing env vars: {', '.join(missing)}")
    if demo.ENABLED and ".sandbox." not in paypal.BASE:  # the demo pays with a shared account: never with real money
        raise RuntimeError("DEMO_MODE needs PayPal's sandbox (PAYPAL_BASE_URL)")
    db.init()
    if demo.ENABLED:
        db.end_demo_sessions()  # sessions that went idle while the service slept
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(demo.router)


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


def _admit(body: ScamCheckIn, request: Request) -> None:
    """The checks before a scam check runs: 422 for a chat too long, 429 for a caller over its hourly limit."""
    if len(scam_check.sanitize(body.text)) > scam_check.MAX_CHARS:  # NFKC can make text longer
        raise HTTPException(422, "chat too long")
    ratelimit.hit(request, "check", *(["screenshot"] if body.image else []))


@app.post("/scam-check")
async def scam_check_route(body: ScamCheckIn, request: Request):
    _admit(body, request)
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


def _line(kind: str, **fields) -> bytes:
    return (json.dumps({"type": kind, **fields}, default=lambda o: o.model_dump() if isinstance(o, BaseModel) else str(o))
            + "\n").encode()


async def _stream_check(body: ScamCheckIn, session_id: str):
    """The demo's check, as NDJSON: each PayPal call and each node's update as it happens, then the result.

    Shows the tester what they sent (the sanitized chat, the screenshot's text, the quotes), which /scam-check never
    returns. The slot is taken here, not in the route: a generator that never starts would never release it.
    """
    try:
        SCAM_LIMIT.acquire_nowait()
    except anyio.WouldBlock:
        yield _line("error", message="busy, try again in a moment")
        return
    try:
        calls = []
        # every step runs in this one context: Starlette would run each next() in a fresh copy, losing the trace
        ctx = Context()
        ctx.run(paypal.trace.set, calls)
        inputs = {"text": body.text, "image": body.image, "expected_amount": str(body.expected_amount),
                  "expected_currency": body.expected_currency}
        steps = ctx.run(scam_check.graph.stream, inputs, stream_mode=["updates", "values"])
        final = None
        while (step := await anyio.to_thread.run_sync(ctx.run, next, steps, None)) is not None:
            mode, chunk = step
            while calls:  # the calls a node made come before its update
                yield _line("paypal", **calls.pop(0))
            if mode == "values":
                final = chunk
                continue
            for node, update in chunk.items():
                if node == "ingest_input":  # stored first, so ending the session deletes this case too
                    await anyio.to_thread.run_sync(db.add_demo_case, session_id, update["case_id"])
                yield _line("node", node=node, update=update or {})
        yield _line("result", **scam_check.result(final), persisted=final["persisted"])
    except scam_check.Unreadable as e:
        yield _line("error", message=str(e))
    except Exception as e:
        log.warning("demo check failed: %s", type(e).__name__)
        yield _line("error", message="the check could not run, try again")
    finally:
        SCAM_LIMIT.release()


@app.post("/demo/api/check", dependencies=[Depends(demo.enabled)])
def demo_check(body: ScamCheckIn, request: Request, session_id: str = Depends(demo.session)):
    _admit(body, request)
    return StreamingResponse(_stream_check(body, session_id), media_type="application/x-ndjson")
