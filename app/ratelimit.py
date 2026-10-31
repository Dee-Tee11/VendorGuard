"""Per-IP limits for the open endpoints: each check costs Groq tokens and PayPal calls, and nobody logs in."""
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

LIMITS = {"check": 10, "screenshot": 3, "sale": 10, "session": 20, "message": 30, "price": 5,
          "wrong token": 10}  # per IP per WINDOW
WINDOW = 3600.0
# ponytail: in memory, one instance; resets when Render sleeps or restarts. Move to Postgres if it runs on several
_hits: dict[tuple[str, str], deque] = defaultdict(deque)
_lock = threading.Lock()  # sync routes run in the threadpool


def client_ip(request: Request) -> str:
    """The caller's address. Render's proxy appends it to X-Forwarded-For, after anything the caller sent itself.
    Unverified on Render (RECHECK #23): a forged header must not change the result."""
    if forwarded := request.headers.get("x-forwarded-for"):
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def hit(request: Request, *kinds: str, count: bool = True) -> None:
    """Counts one call of each kind for the caller's IP, or none of them and 429 when any is at its limit.
    count=False only checks: for a kind counted on failure (a wrong token), checked before the attempt."""
    ip, now = client_ip(request), time.monotonic()
    with _lock:
        for kind in kinds:
            q = _hits[(ip, kind)]
            while q and now - q[0] > WINDOW:
                q.popleft()
            if len(q) >= LIMITS[kind]:
                retry = int(WINDOW - (now - q[0])) + 1
                raise HTTPException(429, f"limit of {LIMITS[kind]} {kind}s an hour reached, try again later",
                                    headers={"Retry-After": str(retry)})
        for kind in kinds if count else ():
            _hits[(ip, kind)].append(now)
