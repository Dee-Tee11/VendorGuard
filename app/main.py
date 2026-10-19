import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app import db, paypal


@asynccontextmanager
async def lifespan(_):
    db.init()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"ok": True}


def _handle(headers, body: bytes):
    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(400, "invalid json")
    if not paypal.verify_webhook(headers, event):
        raise HTTPException(400, "bad signature")
    return {"status": "stored" if db.claim_event(event) else "duplicate"}


@app.post("/webhooks/paypal")
async def webhook(request: Request):
    return await run_in_threadpool(_handle, request.headers, await request.body())
