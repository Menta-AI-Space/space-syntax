"""space-syntax 분석 엔진을 MCP 도구로 노출한다 (GROWTHFACTOR_GAP P3).

⚠ mcp는 1.12.4에 고정한다. mcp 2.0은 starlette>=1.x를 끌어와 fastapi
0.115(starlette<0.42)와 충돌한다 — 실제로 설치 순간 백엔드 전체가 안 떴다.
올리려면 fastapi를 함께 올려야 한다.

GrowthFactor의 "the first MCP in commercial real estate"와 같은 발상:
Claude·ChatGPT·Cursor가 이 엔진의 **진짜 계산**(점수·판정·유사상권)을 도구로
직접 부른다 — 프롬프트로 흉내낼 수 있는 게 아니라 서울 PostGIS 11GB 위에서
도는 실제 엔진이다.

설계 규칙:
- **REST와 같은 함수를 부른다.** 도구마다 로직을 새로 쓰면 두 경로가
  갈라진다 — 여기는 얇은 어댑터일 뿐이다.
- **인증은 REST와 같은 키.** FastAPI 의존성은 mount된 ASGI 앱을 지나치므로
  (라우터에만 걸린다), 여기서 같은 X-API-Key를 직접 검사한다. 키가 설정돼
  있지 않으면 REST와 마찬가지로 열린다.
- stateless — 세션 상태를 서버에 두지 않아 재시작·복제에 안전하다.

접속(claude 예):
    claude mcp add --transport http space-syntax \
        https://space-syntax-production.up.railway.app/mcp \
        --header "X-API-Key: <API_ACCESS_TOKEN 값>"
"""

# (from __future__ import annotations 를 쓰지 않는다 — 어노테이션이 문자열이
#  되면 FastMCP의 파라미터 검사(issubclass)가 TypeError로 죽는다. 실측.)

import asyncio
import secrets
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.config import settings
from app.models.schemas import SiteScoreRequest
from app.routers.decide import CandidateIn, DecideRequest, decide as run_decide
from app.routers.goals import LeaseVerdictRequest, lease_verdict as run_lease_verdict
from app.routers.site_score import site_score as run_site_score
from app.services.analogs import AnalogsError, find_analogs
from app.services.trdar_sales_local import trdar_for_point

# stateless: 세션 상태를 서버에 안 둔다 — 재시작·복제에 안전하고,
# 클라이언트도 세션 협상 없이 바로 도구를 부를 수 있다.
# streamable_http_path="/": FastMCP는 내부에서도 기본 경로가 /mcp라, main이
# /mcp에 mount하면 실제 주소가 /mcp/mcp가 된다(307→404 실측). 내부를 루트로.
server = FastMCP(name="space-syntax", stateless_http=True, streamable_http_path="/")


def _dump(result: Any) -> dict[str, Any]:
    """pydantic 응답을 JSON 호환 dict로 — REST 응답과 같은 모양을 유지한다."""
    return result if isinstance(result, dict) else result.model_dump(mode="json")


@server.tool()
async def score_site(
    address: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    business_type: str = "general",
    radius_m: int = 500,
) -> dict[str, Any]:
    """서울 안 한 지점의 입지 점수를 계산한다 (도로망 구문분석 + 배후수요 +
    경쟁 + 대중교통). address 또는 lat/lng 중 하나는 필수. business_type 예:
    cafe, convenience, restaurant, clinic, academy, beauty, fitness.
    응답에는 총점, 세부 지표(서울 상위 %), 상권 매출 벤치마크가 담긴다."""
    result = await run_site_score(SiteScoreRequest(
        address=address, lat=lat, lng=lng,
        business_type=business_type, radius_m=radius_m,
        include_ai_explanation=False,
    ))
    return _dump(result)


@server.tool()
async def judge_lease(
    lat: float,
    lng: float,
    monthly_rent_man: float,
    business_type: str = "general",
    deposit_man: float | None = None,
    area_pyeong: float | None = None,
) -> dict[str, Any]:
    """월세 조건이 감당 가능한지 판정한다 — 예상 월매출(범위 포함), 손익분기,
    적정 월세 밴드, 협상 조건까지. 좌표는 score_site로 먼저 얻을 수 있다."""
    result = await run_lease_verdict(LeaseVerdictRequest(
        lat=lat, lng=lng, business_type=business_type,
        monthly_rent_man=monthly_rent_man,
        deposit_man=deposit_man, area_pyeong=area_pyeong,
    ))
    return _dump(result)


@server.tool()
async def compare_candidates(
    candidates: list[dict[str, Any]],
    business_type: str = "general",
    priority: str = "balanced",
) -> dict[str, Any]:
    """후보지 2~5곳을 같은 기준으로 비교해 순위와 계약 가능 월세를 낸다.
    candidates 항목: {"label": str, "lat": float, "lng": float,
    "monthly_rent_man"?: float}. priority: revenue|stability|balanced."""
    cands = [
        CandidateIn(
            id=str(c.get("id") or f"c{i + 1}"),
            label=str(c.get("label") or f"후보 {i + 1}"),
            lat=float(c["lat"]), lng=float(c["lng"]),
            monthly_rent_man=c.get("monthly_rent_man"),
            area_pyeong=c.get("area_pyeong"),
        )
        for i, c in enumerate(candidates)
    ]
    result = await run_decide(DecideRequest(
        candidates=cands, priority=priority,  # type: ignore[arg-type]
    ))
    return _dump(result)


@server.tool()
async def find_similar_areas(
    lat: float,
    lng: float,
    business_type: str = "cafe",
) -> dict[str, Any]:
    """이 지점과 장사 구조(직장인 규모·점포 밀도·주말 비중·프랜차이즈 비율)가
    비슷한 서울 상권 3곳과 그곳의 점포당 매출을 비교한다. 유사도는 원인
    변수로만 재고 매출은 비교만 한다 — "같은 구조인데 왜 성과가 다른가"를
    보는 도구다."""
    match = await asyncio.to_thread(trdar_for_point, lat, lng)
    if match is None:
        return {"error": "이 지점은 서울시 지정 상권 밖이라 비교할 수 없습니다."}
    try:
        return await asyncio.to_thread(find_analogs, match.trdar_cd, business_type)
    except AnalogsError as exc:
        return {"error": str(exc)}


class _ApiKeyGuard:
    """mount된 MCP 앱에 REST와 같은 X-API-Key를 요구하는 ASGI 래퍼.

    FastAPI의 Depends(require_api_token)는 라우터에만 걸리고 app.mount()로
    붙인 ASGI 앱은 지나친다 — 여기서 안 막으면 REST에 자물쇠를 채워 놓고
    MCP 문을 열어 두는 꼴이 된다.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and settings.api_access_token:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            supplied = headers.get("x-api-key", "")
            if not supplied or not secrets.compare_digest(supplied, settings.api_access_token):
                await send({
                    "type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"error":"X-API-Key required"}',
                })
                return
        await self.app(scope, receive, send)


def build_mcp_app() -> Any:
    """main.py가 mount할 ASGI 앱."""
    return _ApiKeyGuard(server.streamable_http_app())
