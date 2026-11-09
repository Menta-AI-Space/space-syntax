"""Request-scoped observability: request ids, timing, and unhandled-error logging."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi.middleware.cors import CORSMiddleware

from app.observability import (
    REQUEST_ID_HEADER,
    error_body_middleware,
    request_context_middleware,
    unhandled_exception_handler,
)


def _observed_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(request_context_middleware)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/ok")
    def ok():
        return {"ok": True}

    @app.get("/boom")
    def boom():
        raise RuntimeError("db exploded: password=hunter2")

    return app


def test_request_id_auto_generated_when_absent():
    client = TestClient(_observed_app())

    resp = client.get("/ok")

    assert resp.status_code == 200
    assert resp.headers[REQUEST_ID_HEADER]


def test_request_id_honours_inbound_header():
    client = TestClient(_observed_app())

    resp = client.get("/ok", headers={REQUEST_ID_HEADER: "caller-supplied-id"})

    assert resp.headers[REQUEST_ID_HEADER] == "caller-supplied-id"


def test_request_id_strips_control_characters():
    """Regression test: an inbound id with a newline must not be able to forge
    a fake log line — only safe characters survive.
    """
    client = TestClient(_observed_app())

    resp = client.get(
        "/ok", headers={REQUEST_ID_HEADER: "abc\n2026-01-01 FAKE admin login"}
    )

    returned = resp.headers[REQUEST_ID_HEADER]
    assert "\n" not in returned
    assert " " not in returned
    assert returned == "abc2026-01-01FAKEadminlogin"[:64]


def test_request_id_truncated_to_64_chars():
    client = TestClient(_observed_app())
    long_id = "a" * 200

    resp = client.get("/ok", headers={REQUEST_ID_HEADER: long_id})

    assert len(resp.headers[REQUEST_ID_HEADER]) == 64


def test_unhandled_exception_returns_generic_body_not_traceback():
    client = TestClient(_observed_app(), raise_server_exceptions=False)

    resp = client.get("/boom")

    assert resp.status_code == 500
    assert "hunter2" not in resp.text
    assert "RuntimeError" not in resp.text
    assert resp.headers[REQUEST_ID_HEADER]


def _cors_app() -> FastAPI:
    """main.py와 같은 순서로 쌓는다: error_body가 가장 먼저(=가장 안쪽), CORS가 그 위."""
    app = FastAPI()
    app.middleware("http")(error_body_middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://app.example"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(request_context_middleware)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/boom")
    def boom():
        raise RuntimeError("db exploded")

    return app


def test_unhandled_error_response_still_carries_cors_headers():
    """500에도 CORS 헤더가 붙어야 브라우저가 본문을 읽는다.

    add_exception_handler만 두면 그 핸들러는 starlette의 ServerErrorMiddleware가
    부르는데 그건 CORSMiddleware **바깥**이라, 500 응답에
    Access-Control-Allow-Origin이 안 붙는다. 그러면 브라우저는 본문을 보지 못한
    채 fetch를 TypeError로 거부하고, 프론트에는 "분석 서버에 연결하지
    못했습니다"라는 연결 실패 문구만 뜬다 — 정작 서버가 500을 냈다는 사실은
    사용자도 개발자도 화면에서 알 수 없다(2026-08-24 실측: 후속 질문이 통째로
    '메시지가 안 보내진다'로 보였다).
    """
    client = TestClient(_cors_app(), raise_server_exceptions=False)

    resp = client.get("/boom", headers={"Origin": "https://app.example"})

    assert resp.status_code == 500
    assert resp.headers["access-control-allow-origin"] == "https://app.example"
    # 본문은 여전히 일반화된 문구다 — 트레이스백은 로그에만 남는다.
    assert "서버 내부 오류" in resp.json()["detail"]
