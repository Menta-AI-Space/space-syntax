"""API access control: shared-secret auth + per-client rate limiting."""

import time

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import security
from app.security import (
    SlidingWindowLimiter,
    _MAX_TRACKED_KEYS,
    client_key,
    require_api_token,
)


def _protected_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_api_token)])
    def protected():
        return {"ok": True}

    return app


# --- require_api_token -------------------------------------------------


def test_auth_disabled_by_default_allows_request(monkeypatch):
    monkeypatch.setattr(security.settings, "api_access_token", "")
    client = TestClient(_protected_app())

    resp = client.get("/protected")

    assert resp.status_code == 200


def test_missing_key_rejected_when_auth_enabled(monkeypatch):
    monkeypatch.setattr(security.settings, "api_access_token", "secret123")
    client = TestClient(_protected_app())

    resp = client.get("/protected")

    assert resp.status_code == 401


def test_wrong_key_rejected_when_auth_enabled(monkeypatch):
    monkeypatch.setattr(security.settings, "api_access_token", "secret123")
    client = TestClient(_protected_app())

    resp = client.get("/protected", headers={"X-API-Key": "wrong"})

    assert resp.status_code == 401
    # The submitted value must never be echoed back.
    assert "wrong" not in resp.text


def test_correct_key_allowed_when_auth_enabled(monkeypatch):
    monkeypatch.setattr(security.settings, "api_access_token", "secret123")
    client = TestClient(_protected_app())

    resp = client.get("/protected", headers={"X-API-Key": "secret123"})

    assert resp.status_code == 200


# --- SlidingWindowLimiter ------------------------------------------------


def test_limiter_blocks_once_limit_exceeded():
    limiter = SlidingWindowLimiter()
    limit = 3

    for _ in range(limit):
        assert limiter.allow("client-a", limit, window_s=60.0) is True
    assert limiter.allow("client-a", limit, window_s=60.0) is False


def test_limiter_tracks_keys_independently():
    limiter = SlidingWindowLimiter()

    assert limiter.allow("client-a", 1) is True
    assert limiter.allow("client-a", 1) is False
    assert limiter.allow("client-b", 1) is True


def test_limit_zero_disables_limiting():
    limiter = SlidingWindowLimiter()

    for _ in range(50):
        assert limiter.allow("client-a", 0) is True


def test_window_expiry_allows_request_after_time_passes(monkeypatch):
    limiter = SlidingWindowLimiter()
    current = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: current[0])

    assert limiter.allow("client-a", 1, window_s=60.0) is True
    assert limiter.allow("client-a", 1, window_s=60.0) is False

    current[0] += 60.1
    assert limiter.allow("client-a", 1, window_s=60.0) is True


def test_prune_drops_stale_buckets(monkeypatch):
    limiter = SlidingWindowLimiter()
    current = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: current[0])

    limiter.allow("client-a", 5, window_s=60.0)
    assert "client-a" in limiter._hits

    current[0] += 120.0
    limiter.prune(window_s=60.0)

    assert "client-a" not in limiter._hits


def test_hits_dict_is_bounded_under_unique_keys(monkeypatch):
    """Regression test for the X-Forwarded-For rate-limit bypass: a burst of
    requests each carrying a distinct key must not grow _hits without bound.
    """
    limiter = SlidingWindowLimiter()

    for i in range(_MAX_TRACKED_KEYS + 500):
        limiter.allow(f"spoofed-{i}", 100)

    assert len(limiter._hits) <= _MAX_TRACKED_KEYS


# --- client_key ------------------------------------------------------------


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    def __init__(self, headers: dict[str, str] | None = None, client_host: str | None = "10.0.0.5"):
        self.headers = headers or {}
        self.client = _FakeClient(client_host) if client_host else None


def test_client_key_ignores_forwarded_for_by_default(monkeypatch):
    """Regression test: X-Forwarded-For must be ignored unless the deployment
    explicitly opts in via trust_forwarded_for, otherwise any caller can mint
    a fresh rate-limit bucket per request just by spoofing the header.
    """
    monkeypatch.setattr(security.settings, "trust_forwarded_for", False)
    request = _FakeRequest(headers={"x-forwarded-for": "1.2.3.4"}, client_host="10.0.0.5")

    assert client_key(request) == "10.0.0.5"


def test_client_key_uses_forwarded_for_when_trusted(monkeypatch):
    monkeypatch.setattr(security.settings, "trust_forwarded_for", True)
    request = _FakeRequest(headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1"}, client_host="10.0.0.5")

    assert client_key(request) == "1.2.3.4"


def test_client_key_falls_back_to_unknown_with_no_client(monkeypatch):
    monkeypatch.setattr(security.settings, "trust_forwarded_for", False)
    request = _FakeRequest(headers={}, client_host=None)

    assert client_key(request) == "unknown"


# --- rate_limit_middleware (integration) -----------------------------------


def _rate_limited_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(security.rate_limit_middleware)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/chat")
    def chat():
        return {"ok": True}

    @app.get("/api/other")
    def other():
        return {"ok": True}

    return app


def test_public_path_bypasses_rate_limit(monkeypatch):
    monkeypatch.setattr(security.settings, "rate_limit_per_minute", 1)
    monkeypatch.setattr(security, "_limiter", SlidingWindowLimiter())
    client = TestClient(_rate_limited_app())

    for _ in range(5):
        assert client.get("/api/health").status_code == 200


def test_general_limit_returns_429_once_exceeded(monkeypatch):
    monkeypatch.setattr(security.settings, "rate_limit_per_minute", 2)
    monkeypatch.setattr(security.settings, "chat_rate_limit_per_minute", 100)
    monkeypatch.setattr(security, "_limiter", SlidingWindowLimiter())
    client = TestClient(_rate_limited_app())

    assert client.get("/api/other").status_code == 200
    assert client.get("/api/other").status_code == 200
    resp = client.get("/api/other")

    assert resp.status_code == 429
    assert resp.json()["detail"]


def test_paid_path_has_its_own_tighter_budget(monkeypatch):
    monkeypatch.setattr(security.settings, "rate_limit_per_minute", 100)
    monkeypatch.setattr(security.settings, "chat_rate_limit_per_minute", 1)
    monkeypatch.setattr(security, "_limiter", SlidingWindowLimiter())
    client = TestClient(_rate_limited_app())

    assert client.get("/api/chat").status_code == 200
    resp = client.get("/api/chat")

    assert resp.status_code == 429
    # The general path must be unaffected by the paid path's tighter budget.
    assert client.get("/api/other").status_code == 200
