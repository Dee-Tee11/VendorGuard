import base64
import binascii
import json
import logging
import os
from contextlib import asynccontextmanager, contextmanager, nullcontext
from contextvars import Context
from decimal import Decimal
from functools import partial
from typing import Annotated

import anyio
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import AfterValidator, BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

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


# the biggest body we take: a 3 MB screenshot as 4 000 000 base64 chars, plus 8 000 chars of text escaped in JSON (up
# to 12 bytes each) and the keys. Checked on Content-Length before reading, and on the bytes read (chunked): 413
MAX_BODY = 4_200_000

app = FastAPI(lifespan=lifespan)
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_BODY)  # RECHECK #3
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


# capped as base64, before decoding: 4 MB of base64 is 3 MB of image, the cap older Groq docs gave for a base64
# image (Groq took 3.06 MB on 2026-10-21); a phone screenshot is far smaller
Screenshot = Annotated[str, Field(max_length=4_000_000), AfterValidator(_b64)]


class ScamCheckIn(BaseModel):
    text: str = Field("", max_length=scam_check.MAX_CHARS)
    image: Screenshot | None = None
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


@contextmanager
def _slot():
    """One of SCAM_LIMIT's slots for a check, or 503 when none is free."""
    try:
        SCAM_LIMIT.acquire_nowait()  # take the slot now: check-then-wait lets a whole burst through to queue
    except anyio.WouldBlock:
        raise HTTPException(503, "busy, try again")
    try:
        yield
    finally:
        SCAM_LIMIT.release()


@app.post("/scam-check")
async def scam_check_route(body: ScamCheckIn, request: Request):
    _admit(body, request)
    with _slot():
        try:
            run = partial(scam_check.run, body.text, str(body.expected_amount), body.expected_currency, body.image)
            return await anyio.to_thread.run_sync(run)
        except scam_check.Unreadable as e:
            raise HTTPException(422, str(e))


def _line(kind: str, **fields) -> bytes:
    return (json.dumps({"type": kind, **fields}, default=lambda o: o.model_dump() if isinstance(o, BaseModel) else str(o))
            + "\n").encode()


async def _stream_check(body: ScamCheckIn, session_id: str):
    """The demo's check, as NDJSON: each PayPal call and each node's update as it happens, then the result.

    Shows the tester what they sent (the sanitized chat, the screenshot's text, the quotes), which /scam-check never
    returns. The session's turn and the slot are taken here, not in the route: the route returns before the stream
    ends, and a generator that never starts would never release them.
    """
    try:
        with demo.busy(session_id):
            async for line in _checked(body, session_id):
                yield line
    except HTTPException as e:  # a chat message of this session is running
        yield _line("error", message=e.detail)


async def _checked(body: ScamCheckIn, session_id: str):
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
        out = scam_check.result(final)
        await anyio.to_thread.run_sync(demo.remember_case, session_id, out)
        yield _line("result", **out, persisted=final["persisted"])
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


class PriceIn(BaseModel):
    image: Screenshot


@app.post("/demo/api/price", dependencies=[Depends(demo.enabled)])
def demo_price(body: PriceIn, request: Request, session_id: str = Depends(demo.session)):
    """The amount a screenshot shows, for the seller to confirm as their price; nulls when it shows none."""
    ratelimit.hit(request, "price")  # one vision call each
    return demo.read_price(body.image) or {"amount": None, "currency": None}


class ChatIn(BaseModel):
    message: str = Field("", max_length=scam_check.MAX_CHARS)
    image: Screenshot | None = None  # the buyer's chat as a screenshot, read only by the check

    @model_validator(mode="after")
    def _message_or_image(self):
        if not self.message and self.image is None:
            raise ValueError("send a message, a screenshot, or both")
        if self.image is not None and not llm.image_type(self.image):
            raise ValueError("the screenshot must be a PNG or JPEG")
        return self


@app.post("/demo/api/chat", dependencies=[Depends(demo.enabled)])
async def demo_chat(body: ChatIn, request: Request, session_id: str = Depends(demo.turn, scope="function")):
    """One message to the chat graph. A message that runs a scam check (the buyer's chat, after the price) counts as
    a check and takes a slot, as chat.runs_scam_check asks. demo.turn runs one message at a time per session, so the
    thread can't change between that decision and the turn."""
    runs_check = await anyio.to_thread.run_sync(demo.runs_check, session_id, body.message, body.image)
    ratelimit.hit(request, "message", *((["check"] + (["screenshot"] if body.image else [])) if runs_check else []))
    with _slot() if runs_check else nullcontext():
        return await anyio.to_thread.run_sync(demo.chat_turn, session_id, body.message, body.image)
