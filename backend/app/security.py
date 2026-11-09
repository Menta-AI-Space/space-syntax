"""API access control: shared-secret auth + per-client rate limiting.

Both are opt-in via settings so local dev and tests keep their current
behaviour: auth is off until `api_access_token` is set, and each rate limit is
off when its per-minute budget is 0.
"""

import logging
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)

# Paths that must stay reachable without a token (liveness probes, docs).
PUBLIC_PATHS = frozenset({"/api/health", "/", "/docs", "/openapi.json", "/redoc"})

# Endpoints that spend money on every call get their own tighter budget.
PAID_PATHS = frozenset({"/api/chat"})


def auth_enabled() -> bool:
    return bool(settings.api_access_token)


async def require_api_token(x_api_key: str | None = Header(default=None)) -> None:
    """Reject requests without the shared secret, once one is configured."""
    if not auth_enabled():
        return

    if not x_api_key or not secrets.compare_digest(x_api_key, settings.api_access_token):
        # Never echo the supplied value back — it lands in logs and responses.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


# Hard ceiling on distinct tracked keys, independent of the time-based prune()
# below (which only runs every _PRUNE_INTERVAL_S and only drops buckets whose
# newest hit has already aged out). Without this, a burst of requests each
# carrying a different client_key() would grow _hits without bound for up to
# _PRUNE_INTERVAL_S seconds.
_MAX_TRACKED_KEYS = 20_000


class SlidingWindowLimiter:
    """In-process sliding-window counter.

    Single-process only: each uvicorn worker keeps its own counters, so the
    effective limit scales with worker count. Adequate as an abuse/cost brake;
    swap for a shared store (Redis) if exact global limits are needed.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: float = 60.0) -> bool:
        if limit <= 0:
            return True

        now = time.monotonic()
        cutoff = now - window_s
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                # New key: bound total memory before growing the dict. Evicting
                # the oldest-inserted bucket is an approximation of LRU, cheap
                # because plain dicts already preserve insertion order.
                if len(self._hits) >= _MAX_TRACKED_KEYS:
                    self._hits.pop(next(iter(self._hits)))
                hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def prune(self, window_s: float = 60.0) -> None:
        """Drop empty buckets so idle clients don't leak memory."""
        cutoff = time.monotonic() - window_s
        with self._lock:
            for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                del self._hits[key]


_limiter = SlidingWindowLimiter()
_last_prune = 0.0
_PRUNE_INTERVAL_S = 300


def client_key(request: Request) -> str:
    """Identify the caller for rate-limiting.

    X-Forwarded-For is caller-supplied unless `settings.trust_forwarded_for` is
    on, which it only should be once a reverse proxy in front of this app
    overwrites (not appends to) the header itself. Trusting it unconditionally
    would let anyone mint a fresh rate-limit bucket per request just by sending
    a different value, defeating the limiter entirely.
    """
    if settings.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_response(key: str, path: str, label: str, message: str) -> JSONResponse:
    logger.warning("Rate limit exceeded (%s) for %s on %s", label, key, path)
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": message},
    )


async def rate_limit_middleware(request: Request, call_next):
    global _last_prune

    path = request.url.path
    if path in PUBLIC_PATHS:
        return await call_next(request)

    key = client_key(request)

    # NOTE: return, never raise. Middleware runs outside Starlette's
    # ExceptionMiddleware, so a raised HTTPException would surface as a 500.
    if not _limiter.allow(f"all:{key}", settings.rate_limit_per_minute):
        return _rate_limit_response(
            key, path, "general", "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
        )

    if path in PAID_PATHS and not _limiter.allow(
        f"paid:{key}", settings.chat_rate_limit_per_minute
    ):
        return _rate_limit_response(
            key, path, "paid", "AI 상담 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
        )

    now = time.monotonic()
    if now - _last_prune > _PRUNE_INTERVAL_S:
        _last_prune = now
        _limiter.prune()

    return await call_next(request)
