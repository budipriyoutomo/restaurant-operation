"""Per-client-IP rate limiting (slowapi).

Applied with @limiter.limit(...) on the public auth endpoints only (login,
forgot / reset password). The client IP is the ASGI peer address; behind the
reverse proxy uvicorn sets it from X-Forwarded-For (FORWARDED_ALLOW_IPS).

Counters live in each worker's memory: with N gunicorn workers a client can make
up to N × the limit before every worker refuses it. Good enough to stop password
guessing and email flooding without adding Redis.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import settings

limiter = Limiter(key_func=get_remote_address, enabled=settings.RATE_LIMIT_ENABLED)


def rate_limit_exceeded(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    retry_after = exc.limit.limit.get_expiry() if exc.limit is not None else 60
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many attempts. Please wait a minute and try again."},
        headers={"Retry-After": str(retry_after)},
    )
