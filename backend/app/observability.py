"""Request-scoped observability: request ids, timing, and unhandled-error logging.

Kept out of security.py so access control stays that module's only concern.
"""

import logging
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# Anything outside this set is stripped from an inbound request id before it's
# used: it's echoed into every log line for the request, and an unfiltered
# value (e.g. containing \n) would let a caller forge log lines.
_SAFE_REQUEST_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]")

# Read by RequestIdFilter so every line emitted while handling a request carries
# its id, without threading the id through every call site.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Stamp the current request id onto every record the handler formats."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


def _resolve_request_id(request: Request) -> str:
    # Honour an upstream id (proxy / frontend) so one id spans the whole hop.
    inbound = request.headers.get(REQUEST_ID_HEADER, "").strip()
    if inbound:
        # Stripped and bounded: it lands in every log line and in a response
        # header, so control characters (e.g. \n) can't forge a log line.
        cleaned = _SAFE_REQUEST_ID_CHARS.sub("", inbound)[:64]
        if cleaned:
            return cleaned
    return uuid.uuid4().hex[:8]


async def request_context_middleware(request: Request, call_next):
    request_id = _resolve_request_id(request)
    # request.state survives past this middleware, so the exception handler
    # (which runs outside it, in ServerErrorMiddleware) can still read the id.
    request.state.request_id = request_id
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            # ServerErrorMiddleware sits outside this middleware and owns the
            # 500 response, so log the completion line here — otherwise the
            # timing of failed requests is lost — then let the exception through.
            logger.error(
                "%s %s -> 500 duration_ms=%.1f",
                request.method,
                request.url.path,
                (time.perf_counter() - started) * 1000,
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        threshold = settings.slow_request_ms
        slow = threshold > 0 and duration_ms > threshold
        logger.log(
            logging.WARNING if slow else logging.INFO,
            "%s %s -> %s duration_ms=%.1f%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            f" SLOW (>{threshold}ms)" if slow else "",
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
    finally:
        request_id_var.reset(token)


async def error_body_middleware(request: Request, call_next):
    """핸들러에서 새는 예외를 **CORS 미들웨어 안쪽에서** 500 본문으로 바꾼다.

    ``app.add_exception_handler(Exception, ...)``만 두면 그 핸들러는 starlette의
    ServerErrorMiddleware가 부르는데, 그건 사용자 미들웨어 **바깥**이라 응답이
    CORSMiddleware를 통과하지 않는다. 그래서 500에는
    ``Access-Control-Allow-Origin``이 안 붙고, 브라우저는 본문을 읽기 전에 막아
    fetch를 TypeError로 거부한다 — 프론트에는 "분석 서버에 연결하지
    못했습니다"(연결 실패 문구)만 뜨고 정작 서버가 500을 냈다는 사실은 어디에도
    안 보인다. 실제로 이것 때문에 두 번째 질문부터 "메시지가 안 보내진다"로
    보였다.

    이 미들웨어는 **가장 먼저 등록**해야 한다(= 가장 안쪽). 그래야 라우터를
    감싸고, 여기서 만든 500 응답이 CORSMiddleware를 통과해 헤더를 받는다.
    HTTPException·검증 오류는 starlette의 ExceptionMiddleware가 더 안쪽에서 이미
    응답으로 바꿔 놓으므로 여기까지 오지 않는다.
    """
    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001 - 마지막 그물이 목적이다
        return await unhandled_exception_handler(request, exc)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the traceback that a bare FastAPI 500 would otherwise discard.

    Only reached for exceptions nothing else handled: FastAPI's own
    HTTPException/validation handlers are registered separately and still run
    first, so 4xx responses keep their intended detail.
    """
    request_id = getattr(request.state, "request_id", "-")
    logger.exception(
        "Unhandled exception on %s %s (request_id=%s)",
        request.method,
        request.url.path,
        request_id,
        extra={"request_id": request_id},
    )
    # Generic body: the traceback goes to the log, never to the caller.
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "서버 내부 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."},
        headers={REQUEST_ID_HEADER: request_id},
    )


@contextmanager
def timed_step(step_logger: logging.Logger, name: str) -> Iterator[None]:
    """Time one step of a request so a >5s response can be attributed to it.

    Logs on the way out either way, so a step that raises still reports how long
    it burned before failing.
    """
    started = time.perf_counter()
    try:
        yield
    finally:
        step_logger.info(
            "step=%s duration_ms=%.1f", name, (time.perf_counter() - started) * 1000
        )
