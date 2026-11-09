import logging
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.logging_config import configure_logging
from app.observability import (
    REQUEST_ID_HEADER,
    error_body_middleware,
    request_context_middleware,
    unhandled_exception_handler,
)
from app.security import auth_enabled, rate_limit_middleware, require_api_token
from app.routers import (
    academy_cluster,
    agent,
    analyze,
    dashboard,
    decide,
    floorplan,
    franchise,
    geo,
    goals,
    industry_profiles,
    lease_feasibility,
    parcel_structure,
    planned_site,
    profitability,
    property_market,
    real_competition,
    simulate,
    site_score,
    street_grade,
    undervalued_areas,
    undervalued_parcels,
    upjong,
    translate,
)
from app.services.sgis_grid import is_grid_data_available, query_grid_radius
from app.services import telegram_agent_bot

# Before app creation, so startup hooks and every module-level log below land in
# the configured format rather than logging's last-resort handler. (Imports
# above have already run; anything they log at import time still misses it.)
configure_logging()

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# MVP coverage is a 서대문구 (Seodaemun-gu) extract — Sinchon station, the
# same sample location used in the README's demo address/tests.
_GRID_WARMUP_LAT = 37.5596
_GRID_WARMUP_LNG = 126.9424


def resolve_static_dir() -> Path | None:
    configured = os.getenv("STATIC_DIR", "").strip()
    candidates = [
        Path(configured) if configured else None,
        BACKEND_ROOT / "static",
        BACKEND_ROOT.parent / "frontend",
    ]

    for candidate in candidates:
        if candidate and candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate

    return None


STATIC_DIR = resolve_static_dir()

app = FastAPI(title="Space Syntax Web Analyzer", version="1.0.0")

# 가장 먼저 등록 => 가장 안쪽. 라우터에서 새는 예외를 여기서 500 본문으로
# 바꿔야 그 응답이 아래 CORSMiddleware를 통과해 CORS 헤더를 받는다. 안 그러면
# 브라우저가 500 본문을 읽지 못하고 fetch가 통째로 실패한 것처럼 보인다.
app.middleware("http")(error_body_middleware)

app.middleware("http")(rate_limit_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # So browsers can actually read the id the middleware echoes back; without
    # this CORS strips it from cross-origin responses.
    expose_headers=[REQUEST_ID_HEADER],
)

# Registered last => outermost. Every request gets an id and exactly one
# completion line, including the ones the rate limiter rejects with a 429, and
# the measured duration covers the full stack rather than just the handler.
app.middleware("http")(request_context_middleware)

# MCP — 분석 엔진을 Claude·Cursor 등이 도구로 직접 부르게 노출한다
# (GROWTHFACTOR_GAP P3). import 실패(의존성 미설치)로 REST까지 죽으면 안
# 되므로 방어적으로 감싼다 — MCP는 부가 기능이지 본체가 아니다.
try:
    from app.mcp_server import build_mcp_app, server as _mcp_server

    app.mount("/mcp", build_mcp_app())

    # 마운트된 서브앱의 lifespan은 starlette가 실행해 주지 않는다 — 세션
    # 매니저를 여기서 직접 열고 닫지 않으면 첫 요청이 "Task group is not
    # initialized"로 500이 난다(실측).
    from contextlib import AsyncExitStack

    _mcp_stack = AsyncExitStack()

    @app.on_event("startup")
    async def _start_mcp_session_manager() -> None:
        await _mcp_stack.enter_async_context(_mcp_server.session_manager.run())
        logger.info(
            "MCP: mounted at /mcp (tools: score_site·judge_lease·"
            "compare_candidates·find_similar_areas)"
        )

    @app.on_event("shutdown")
    async def _stop_mcp_session_manager() -> None:
        await _mcp_stack.aclose()
except Exception as exc:  # pragma: no cover - 환경 문제는 로그로만
    logger.warning("MCP mount 실패 — REST는 정상 동작: %s", exc)

# Last-resort net: without this an unhandled error is a bare 500 with no log
# line and no traceback. FastAPI's HTTPException/validation handlers are
# separate and keep running first, so 4xx responses are unaffected.
app.add_exception_handler(Exception, unhandled_exception_handler)

# Applied at include time so every current and future route on these routers is
# covered; /api/health and / stay open (they're declared on the app directly).
_protected = [Depends(require_api_token)]

app.include_router(agent.router, dependencies=_protected)
app.include_router(analyze.router, dependencies=_protected)
app.include_router(dashboard.router, dependencies=_protected)
app.include_router(decide.router, dependencies=_protected)
app.include_router(goals.router, dependencies=_protected)
app.include_router(lease_feasibility.router, dependencies=_protected)
app.include_router(profitability.router, dependencies=_protected)
app.include_router(property_market.router, dependencies=_protected)
app.include_router(real_competition.router, dependencies=_protected)
app.include_router(academy_cluster.router, dependencies=_protected)
app.include_router(simulate.router, dependencies=_protected)
app.include_router(site_score.router, dependencies=_protected)
app.include_router(undervalued_areas.router, dependencies=_protected)
app.include_router(undervalued_parcels.router, dependencies=_protected)
app.include_router(upjong.router, dependencies=_protected)
app.include_router(industry_profiles.router, dependencies=_protected)
app.include_router(franchise.router, dependencies=_protected)
app.include_router(floorplan.router, dependencies=_protected)
app.include_router(parcel_structure.router, dependencies=_protected)
app.include_router(street_grade.router, dependencies=_protected)
app.include_router(planned_site.router, dependencies=_protected)
app.include_router(translate.router, dependencies=_protected)
# 동네(행정동) 검색은 공개 데이터 — 자동완성에서 바로 부를 수 있게 인증 없이 연다.
app.include_router(geo.router)


def _warm_sgis_grid_cache() -> None:
    """Prime the PostGIS connection + GiST index page cache for the MVP area.

    Paying the first-query cost here, off the request path in a background
    thread, means the first live user request after a (re)start doesn't
    have to eat it.
    """
    try:
        if is_grid_data_available():
            query_grid_radius(_GRID_WARMUP_LAT, _GRID_WARMUP_LNG, 500)
    except Exception:
        pass  # best-effort warm-up; must never block or fail startup


@app.on_event("startup")
def _startup_warm_sgis_grid() -> None:
    threading.Thread(target=_warm_sgis_grid_cache, daemon=True).start()


async def _ask_from_telegram(payload: dict):
    """Adapt the Telegram transport's plain dict to the product Agent model."""

    return await agent.agent_ask(agent.AgentAskRequest(**payload))


@app.on_event("startup")
def _startup_telegram_analysis_agent() -> None:
    # Uses a dedicated token. It must never reuse the autonomous developer bot
    # or Hermes gateway token because Telegram permits only one poller/token.
    telegram_agent_bot.start_if_configured(_ask_from_telegram)


def _sanitized_db_target() -> str:
    """host:port/dbname for the configured DATABASE_URL, credentials stripped."""
    try:
        parsed = urlsplit(settings.database_url)
        return f"{parsed.hostname}:{parsed.port}{parsed.path}"
    except Exception:
        return "<unparseable>"


@app.on_event("startup")
def _startup_report_db_target() -> None:
    # Without this, pointing at the wrong database is silent until the first
    # query fails deep in a request with a bare "relation does not exist".
    logger.info("Database: %s", _sanitized_db_target())


@app.on_event("startup")
def _startup_report_access_control() -> None:
    if auth_enabled():
        logger.info("API auth: ENABLED (X-API-Key required)")
    else:
        logger.warning(
            "API auth: DISABLED (API_ACCESS_TOKEN unset) — every /api route is "
            "open to anyone who can reach this host."
        )
    logger.info(
        "CORS allowlist: %s | rate limits: %s/min general, %s/min on %s",
        settings.cors_origin_list,
        settings.rate_limit_per_minute,
        settings.chat_rate_limit_per_minute,
        "/api/chat",
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    if STATIC_DIR is None:
        return {"message": "Space Syntax Web Analyzer API", "docs": "/docs"}

    index_path = STATIC_DIR / "index.html"
    return FileResponse(index_path)


if STATIC_DIR is not None:
    css_dir = STATIC_DIR / "css"
    js_dir = STATIC_DIR / "js"
    if css_dir.is_dir():
        app.mount("/css", StaticFiles(directory=css_dir), name="css")
    if js_dir.is_dir():
        app.mount("/js", StaticFiles(directory=js_dir), name="js")
