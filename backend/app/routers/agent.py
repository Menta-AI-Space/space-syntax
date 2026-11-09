"""POST /api/agent/ask — 자연어 한 줄로 입지를 분석하는 에이전트.

흐름: 사용자가 자유롭게 치면 (1) 문장에서 **장소·업종·수치(월세·매입가·현재
매출·평수·층)** 를 뽑고 → (2) 질문이 요구하는 **도구를 골라** 돌린 뒤 → (3) 실제로
실행한 도구 트레이스와 결과, 한 문단 추천을 돌려준다.

도구는 이미 다 구현돼 있다. 이 파일은 라우터 함수를 **그대로 호출**할 뿐 다른
라우터를 고치지 않는다:
  - 기본            : /api/site-score        그 지점이 그 업종에 좋은지
  - 계약해도 될까    : /api/lease-verdict     월세가 있을 때
  - 매입해도 될까    : /api/purchase-verdict  매입가+면적이 있을 때
  - 투자 수익 나올까 : /api/profitability/analyze  매입가+월세가 있을 때
  - 어디가 나은가    : /api/decide            지점이 둘 이상일 때
  - 자리 찾아줘      : /api/area-search       "N곳"처럼 후보를 요구할 때
  - 왜 매출이 낮지   : /api/diagnose          현재 매출이 있을 때

라우팅은 **문장에서 실제로 뽑힌 파라미터**가 결정한다(특정 문구 분기가 아니다).
도구마다 필요한 값이 정해져 있고, 그게 다 있을 때만 그 도구로 간다. 없으면
무엇이 더 필요한지 알려 주고 기본 분석으로 안전하게 내려간다.

원칙은 이 프로젝트 전체와 같다: **숫자·점수·판정은 엔진이 결정론적으로 계산**하고,
LLM은 (a) 자연어에서 장소·업종·의도를 뽑는 것과 (b) 이미 나온 숫자를 문장으로
서술하는 것만 한다. LLM이 꺼져 있거나 실패해도 규칙 폴백으로 동작한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Container

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.models.schemas import (
    AcademyClusterRequest,
    AreaInsightsRequest,
    LeaseFeasibilityRequest,
    ProfitabilityAnalyzeRequest,
    ProfitabilityMarketContextIn,
    ProfitabilityPurchaseIn,
    ProfitabilityRentalIn,
    DashboardPointRequest,
    PropertyMarketRequest,
    RealCompetitionRequest,
    SiteScoreRequest,
    SiteScoreResponse,
)
from app.routers.academy_cluster import run_academy_cluster_analysis
from app.routers.dashboard import area_insights as run_area_insights
from app.routers.dashboard import cannibalization as run_cannibalization
from app.routers.dashboard import isochrone as run_isochrone
from app.routers.dashboard import territory as run_territory
from app.routers.dashboard import trends as run_trends
from app.routers.decide import (
    CandidateIn,
    DecideConstraints,
    DecideRequest,
    decide as run_decide,
)
from app.routers.franchise import (
    franchise_brand_detail as run_franchise_brand_detail,
    franchise_brands as run_franchise_brands,
)
from app.routers.goals import (
    AreaSearchRequest,
    DiagnoseRequest,
    LeaseVerdictRequest,
    PurchaseVerdictRequest,
    area_search as run_area_search,
    diagnose as run_diagnose,
    lease_verdict as run_lease_verdict,
    purchase_verdict as run_purchase_verdict,
)
from app.routers.lease_feasibility import lease_feasibility as run_lease_feasibility
from app.routers.profitability import profitability_analyze as run_profitability
from app.routers.property_market import property_market_comparables as run_property_market
from app.routers.real_competition import real_competition as run_real_competition
from app.routers.site_score import site_score as run_site_score
from app.services.dong_center import resolve_unique_dong
from app.services.geocoding import GeocodingError, geocode_address
from app.services.analogs import AnalogsError, find_analogs
from app.services.trdar_sales_local import trdar_for_point
from app.services.industry_profiles import get_adaptive_profile
from app.services.isochrone import MODE_LABEL_KO as _REACH_MODE_LABEL_KO
from app.services.land_analysis_source import analyze_parcel_at, dong_adm_cd_at
from app.services.parcel_undervalued import list_top_parcels_in_dong
from app.services.undervalued_areas import list_top_undervalued
from app.services.agent_loop import Observation, run_agent_loop, stream_agent_loop
from app.services.area_extras import area_extra_lines
from app.services.agent_talk import CAPABILITIES, talk_answer
from app.services.llm_client import complete_json, complete_text
from app.services.personal_fit import MAX_PRIORITIES
from app.services.profile_resolver import ProfileResolver
from app.services.real_competition import resolve_vertical as _resolve_competition_vertical
from app.services.sbiz_upjong import (
    BUSINESS_TYPE_PRESETS,
    archetype_for_mcls,
    get_upjong_tree,
    search_upjong,
)
from app.services.decision import expected_monthly_sales, max_acceptable_rent
from app.services.founder_verdict import founder_verdict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

# site_score가 받는 프리셋 업종. LLM/규칙 추출 결과를 여기로 좁힌다.
# 아래 값들은 industry-profiles/**/*.json 의 profile id와 일치해야 site_score의
# ProfileResolver가 해당 업종 분석으로 연결된다("beauty"→미용, "leisure"→오락 등).
_BUSINESS_TYPES = {
    "cafe", "convenience", "restaurant", "clinic", "academy",
    "beauty", "fitness", "leisure", "lodging",
    "gas_station", "ev_charging", "professional_office",
    "general",
}

# LLM이 없을 때 문장에서 업종을 유추하는 최소 키워드 표. 위에서부터 먼저 맞는
# 것을 쓰므로(첫 매치 우선) 순서가 중요하다 — "미용학원"이 beauty가 아니라
# academy로 가도록 학원을 미용보다 먼저 둔다. "피부"는 피부과(의원)와 겹치므로
# beauty에는 "피부관리"만 넣는다.
#
# 여기엔 **업종을 통칭하는 말만** 둔다. 세부 메뉴어(치킨·분식·피자…)를 넣으면
# restaurant 프리셋이 한식(I201)으로 고정돼 있어서 "홍제동 치킨집"이 '동일업종
# 한식 71곳'으로 집계됐다 — 치킨은 기타 간이 음식점업(I210)이라 사실이 아니다.
# 그런 말은 아래 _match_upjong이 업종 표에서 정확한 소분류로 직접 찾아 준다.
_BUSINESS_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("카페", "커피", "coffee", "cafe"), "cafe"),
    (("편의점", "convenience"), "convenience"),
    # "치킨집" 같은 구체 메뉴어는 여기서 restaurant로 뭉개지 않고 정확한
    # 소분류 표로 보낸다(의도적 설계, 아래 test_keyword_business_type_maps_common_words
    # 참고). "샐러드"는 그 소분류 경로에서도 안 걸려 general로 새면서 답변이
    # 통째로 비어버렸던 실제 버그라 여기서만 최소한으로 구제한다.
    (("식당", "음식점", "restaurant", "밥집", "샐러드", "salad"), "restaurant"),
    (("병원", "의원", "클리닉", "치과", "clinic"), "clinic"),
    (("학원", "교습소", "academy"), "academy"),
    (("미용실", "미용", "헤어샵", "헤어", "네일", "왁싱", "피부관리", "속눈썹", "에스테틱", "살롱", "beauty", "hair", "nail"), "beauty"),
    (("헬스장", "헬스", "피트니스", "필라테스", "요가", "크로스핏", "gym", "fitness"), "fitness"),
    (("노래방", "노래연습장", "pc방", "피시방", "오락실", "당구장", "볼링장", "스크린골프", "게임장"), "leisure"),
    (("호텔", "모텔", "숙박", "게스트하우스", "펜션", "여관", "hotel", "motel"), "lodging"),
    (("주유소", "주유", "gas"), "gas_station"),
    (("전기차충전", "충전소", "ev충전", "충전기"), "ev_charging"),
    (("공인중개", "부동산중개", "부동산", "중개사", "변호사", "세무사", "회계사", "법무사", "노무사", "변리사"), "professional_office"),
]

_BUSINESS_LABELS = {
    "cafe": "카페",
    "convenience": "편의점",
    "restaurant": "음식점",
    "clinic": "병의원",
    "academy": "학원",
    "beauty": "미용실",
    "fitness": "운동시설",
    "leisure": "오락·여가",
    "lodging": "숙박",
    "gas_station": "주유소",
    "ev_charging": "전기차충전",
    "professional_office": "부동산·전문서비스",
    "general": "일반",
}


class AgentHistoryTurn(BaseModel):
    """직전 대화 턴 하나 — 후속 질문이 이어받을 문맥.

    프론트가 직전에 받은 AgentAskResponse에서 그대로 뽑아 다음 요청에 실어
    보낸다(추가 조회 없음). "신촌 카페 어때" 다음에 "월세 300이면 계약해도
    될까?"처럼 장소·업종을 다시 말하지 않는 후속 질문이 실제 대화에서 가장
    흔한데, 지금까지는 매 요청이 완전히 독립이라 장소를 다시 못 대면 "어느
    지역인지 알려 주세요"만 반복해서 사용자가 앞 대화를 계속할 수 없었다.
    """

    query: str = ""
    address: str = ""
    business_type: str = "general"
    # 사람이 읽는 업종 이름("카페"). 이 필드가 없어서 /ask는 history가 한 건이라도
    # 실리면 AttributeError로 500이 났다 — 화면에서는 "분석 서버에 연결하지
    # 못했습니다"로 보여(500 응답에 CORS 헤더가 없어 브라우저가 본문을 못 읽는다)
    # **두 번째 질문부터 아무것도 안 보내지는** 것처럼 됐다. 프론트는 이 값을
    # 처음부터 실어 보내고 있었고(lib/api.ts AgentHistoryTurn), 백엔드도 두
    # 곳에서 읽고 있었다(_talk_response·agent_ask) — 모델에만 빠져 있었다.
    business_label: str = ""
    # 프리셋 밖 업종(꽃집·세탁소 등)은 business_type만으로 이어받을 수 없다 —
    # SBIZ 코드가 있어야 같은 업종으로 계속 분석한다. AgentAskResponse.upjong을
    # 그대로 담아 보내면 된다.
    upjong: dict[str, Any] | None = None
    # 그 턴에서 뽑힌 수치(월세·매입가 등). 이번 턴에서 새로 안 나온 값은 여기서
    # 이어받는다("월세 300 신촌 카페" 다음 "15평이야"에도 월세 300이 유지된다).
    params: dict[str, float] = Field(default_factory=dict)
    # 2026-11-06: 그 턴에 화면이 받은 답 본문. 종전엔 장소·업종만 넘겨서, 후속 질문
    # ("그럼 거기서 디저트 카페는?")에 모델이 앞에서 무슨 말을 했는지 몰랐다.
    # 길면 서버가 자른다(_history_messages).
    answer: str = ""


class AgentPendingContext(BaseModel):
    """직전 턴에서 값이 모자라 되묻고 끝난 도구의 스냅샷.

    이전 응답의 ``pending``을 다음 ``/ask`` 요청의 ``context``로 그대로 돌려보내면,
    "월세 300이요"처럼 그 값 하나만 담은 대답도 지점·업종을 다시 말하지 않고
    원래 하려던 도구(계약 판정 등)로 이어진다. 프론트가 아직 이 필드를 보내지
    않아도 기존 흐름은 그대로 동작한다(``context``가 없으면 무시된다).
    """

    tool: str
    address: str
    business_type: str = "general"
    upjong: dict[str, Any] | None = None
    params: dict[str, float] = Field(default_factory=dict)
    brand: str | None = None
    property_type: str = "commercial"
    target_model: str | None = None
    priorities: list[str] = Field(default_factory=list)
    reach_minutes: int | None = None
    reach_mode: str = "walk"
    franchise_brand: str | None = None


class AgentAskRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=400, description="자연어 요청")
    radius_m: int = Field(500, description="분석 반경 (500 또는 1000)")
    include_answer: bool = Field(
        True,
        description="False면 LLM 서술을 건너뛰고 분석만 돌려준다(화면을 먼저 그리기 위함)",
    )
    # 오래된 순으로 이전 턴들. 비어 있으면(첫 질문) 지금까지와 동일하게 동작한다.
    history: list[AgentHistoryTurn] = Field(
        default_factory=list,
        description="직전 대화 턴들(오래된 순) — 있으면 이번 질문에 빠진 장소·업종·수치를 이어받는다.",
    )
    # 직전 응답의 pending을 그대로 돌려보내면, 이번 문장이 그 값 하나만 담고
    # 있어도(예: "월세 300이요") 원래 하려던 도구로 이어서 계산한다.
    context: AgentPendingContext | None = None
    # 2026-10-24: 결정 카드만 먼저. True면 도구 루프(LLM이 도구를 고르는 9~26초)와
    # 곁들이 도구를 건너뛰고 site_score만 돌린다 — 실측 2초 안팎. 화면은 이걸로
    # 결정 카드를 바로 그리고, 같은 질문을 fast=False로 한 번 더 보내 「자세히 보기」를 채운다.
    fast: bool = False
    # 2026-10-26: False면 도구 루프(LLM이 도구를 고르고 서술까지 쓰는 18~19초)를 건너뛰고
    # 규칙이 고른 도구를 site_score와 나란히 돌린다(경쟁 구조 5초가 가장 느리다). 서술은
    # 화면이 /answer(brief)로 따로 받는다.
    loop: bool = True
    # 2026-11-06: False면 위치를 특정하지 못한 질문에 LLM을 부르지 않고 빈 talk 응답을
    # 바로 돌려준다. 화면이 같은 질문을 /ask/stream으로도 보내고 있어서, 글 답은 그쪽이
    # 쓴다(같은 질문에 답을 두 번 쓰지 않게).
    talk: bool = True


class AgentAnswerRequest(BaseModel):
    """이미 계산된 분석 결과로 서술만 생성한다 — 엔진을 다시 돌리지 않는다."""

    site: SiteScoreResponse
    place: str = ""
    business_label: str = ""
    radius_m: int = 500
    # 사용자가 실제로 물은 문장. 이게 없으면 서술이 질문과 무관해진다 — '야간 장사'
    # 라고 물어도 시간대 얘기가 한 줄도 없고, '20대 여성 타깃'이라고 물은 자리에서
    # 40~50대가 주력인데도 그 어긋남을 짚지 않았다.
    query: str = ""
    # /ask가 돌린 주 도구와 그 결과(맨 앞 도구). 있으면 서술의 중심이 그 판정이 된다.
    tool: str = "site_score"
    tool_result: dict[str, Any] | None = None
    # 질문 하나가 여러 판정을 요구해 도구를 둘 이상 돌렸으면 전부 여기 실린다
    # (tool/tool_result는 그중 첫 번째와 같다 — 기존 호출자를 위한 하위호환).
    # 비어 있으면(기본값) 위 tool/tool_result만 본다.
    tool_results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # 앞 대화 턴들(오래된 순). /ask는 이걸 받아 장소·업종을 이어받았는데 서술
    # 단계에는 없어서, 좌표는 이어지고 말은 안 이어졌다 — 후속 질문마다 앞에서
    # 이미 말한 것을 처음부터 되풀이했다. 비어 있으면 종전과 동일하게 동작한다.
    history: list[AgentHistoryTurn] = Field(default_factory=list)
    # 2026-10-26: 짧게 빨리. 「자세히 보기」가 33초 걸려 상용화가 힘들다(소유자). True면
    # 350자 안팎(결론·이유·할 일)으로, 파싱용 작은 모델과 낮은 추론 강도로 쓴다.
    brief: bool = False


class AgentAnswerResponse(BaseModel):
    answer: str
    answer_source: str
    # 답변 끝에 이어 물을 법한 질문 2~3개. 이미 계산한 도구는 다시 권하지
    # 않는다(_suggested_questions) — 숫자를 새로 만들지 않고 "무엇을 더 물을 수
    # 있는지"만 안내하므로 없는 값을 지어내는 문제와 무관하다.
    suggested_questions: list[str] = Field(default_factory=list)


class AgentStep(BaseModel):
    key: str
    label: str
    status: str = "done"


class IndustryProfileKpiOut(BaseModel):
    key: str
    label: str


class IndustryProfileChecklistOut(BaseModel):
    """industry-profiles/**/*.json의 UI 설정 중 체크리스트로 쓸 부분만 추린 응답.

    이 업종을 분석할 때 어떤 지표를 보는지에 대한 **설정**이지 이 자리의 실측값이
    아니다 — 실제 점수는 site_score.industry_kpis에 이미 있으니 헷갈리지 않게
    분리해 둔다.
    """

    industry_id: str
    industry_label: str
    user_type: str
    summary_kpis: list[IndustryProfileKpiOut] = []


class AgentAskResponse(BaseModel):
    query: str
    address: str
    business_type: str
    business_label: str
    steps: list[AgentStep]
    # 2026-08-24: 도구를 못 돌린 질문에도 답하게 되면서 선택 필드가 됐다.
    # mode="talk"이면 None이다 — 화면은 그때 보고서를 그리지 않고 글만 보여 준다.
    site: SiteScoreResponse | None = None
    answer: str
    answer_source: str = "rule"  # "llm" | "rule"
    # 질의를 그대로 처리하지 못하고 좁혀서 분석했을 때의 한 줄 고지(비교 질의 등).
    notice: str = ""
    # notice가 "값이 모자라서" 생긴 것이면, 그 도구를 다시 완성하는 데 필요한
    # 스냅샷. 다음 /ask 요청에 그대로 실어 보내면(context) 지점·업종을 다시
    # 말하지 않고도 모자란 값 하나만으로 이어서 계산한다. 값이 다 있어서 도구가
    # 바로 실행됐으면 None이다.
    pending: AgentPendingContext | None = None
    # 아래 셋은 나중에 붙은 것이라 전부 선택 필드다(기존 호출자는 무시하면 된다).
    tool: str = "site_score"  # 실제로 실행한 주 도구(맨 앞 도구)
    tool_result: dict[str, Any] | None = None  # 그 도구의 응답 그대로
    # 질문 하나가 여러 판정을 요구해 도구를 둘 이상 돌렸으면 실행 순서대로 전부
    # 실린다(성공한 것만). 하나만 돌았으면 [tool]과 같다. site_score만 돌았으면
    # 빈 리스트다 — 기존 호출자는 이 필드가 없어도 tool/tool_result로 그대로 동작한다.
    tools: list[str] = Field(default_factory=list)
    tool_results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    parameters: dict[str, float] = Field(default_factory=dict)  # 문장에서 뽑은 수치
    # "더 중요하게" 류 강조로 뽑힌 personal_fit 우선순위. 결과는 site.personal_fit에
    # 있다 — 이 필드는 무엇을 강조로 읽었는지 그대로 보여 주기 위한 것이다.
    priorities: list[str] = Field(default_factory=list)
    # 프리셋 밖 업종(꽃집 등)의 SBIZ 코드. business_type만으로는 "일반"인지
    # "꽃집"인지 구분이 안 되므로, 후속 질문이 업종을 이어받으려면 이 값이
    # 그대로 다음 요청의 AgentHistoryTurn.upjong에 실려 돌아와야 한다.
    upjong: dict[str, Any] | None = None
    # 답변 끝에 이어 물을 법한 질문 2~3개(AgentAnswerResponse와 같은 규칙).
    suggested_questions: list[str] = Field(default_factory=list)
    # "analyze" = 엔진을 돌려 보고서까지 낸 답. "talk" = 위치를 특정할 수 없어
    # 도구 없이 글로만 답한 것(site는 None). 화면이 이 값으로 렌더를 가른다.
    mode: str = "analyze"
    # 창업자 결정 카드(신호등 + 월세 상한). site가 있을 때만 — 규칙은
    # services/founder_verdict.py, 기획은 docs/SPEC_ANSWER_DECISION.md.
    founder: dict[str, Any] | None = None


def _shorten(text: str, limit: int = 30) -> str:
    """에러 메시지에 되돌려 줄 사용자 입력 — 길면 잘라서 화면·로그를 덮지 않게."""
    t = (text or "").strip()
    return t if len(t) <= limit else t[:limit] + "…"


#: 프리셋으로 보내되, 업종 표에 더 정확한 소분류가 있으면 양보하는 말.
#:
#: 위 키워드 표는 **업종 통칭만** 두고 세부 메뉴어는 소분류 표로 보내는 게
#: 설계다. '샐러드'는 그 소분류 경로가 예전에 안 걸려서 답변이 통째로 비었던
#: 탓에 restaurant로 구제해 둔 하나뿐인 예외다. 그런데 restaurant 프리셋은
#: 소분류를 I201(한식)로 **고정으로** 박기 때문에, 구제가 곧 오분류가 됐다 —
#: '홍대입구샐러드집'이 "한식 음식점업"으로 나오고 동일업종 경쟁도 한식으로
#: 집계됐다. 표에는 I21005(토스트/샌드위치/샐러드)가 정확히 있다.
#:
#: 그래도 표에서 빼지는 않는다. 표 조회가 실패하면(DB 지연 등) 답변이 통째로
#: 비는 것보다 대충이라도 음식점으로 보는 게 낫다 — 그게 구제가 들어간 이유다.
#: 소분류를 찾으면 그걸 쓰고, 못 찾으면 프리셋으로 남는다.
#:
#: 통칭('식당', '음식점')은 넣지 않는다. 넣으면 '강남 식당'이 별칭 '구내식당'에
#: 걸려 "구내식당 및 뷔페"가 된다 — 뭉개진 한식보다 더 틀리다.
_UPJONG_TABLE_WINS_OVER_PRESET = ("샐러드", "salad")


def _specific_upjong_beats_preset(
    btype: str, tree: list[dict[str, Any]] | None, query: str
) -> tuple[dict[str, Any], str] | None:
    """문장에 프리셋보다 정확한 소분류가 있으면 그것.

    general이면 애초에 소분류를 찾아야 한다. 프리셋으로 갔더라도 구제어 때문에
    간 것이면 표를 먼저 본다.
    """
    low = (query or "").lower()
    if btype != "general" and not any(k in low for k in _UPJONG_TABLE_WINS_OVER_PRESET):
        return None
    return _rule_upjong_from_query(tree or [], query)


def _keyword_business_type(query: str) -> str:
    low = query.lower()
    for keys, btype in _BUSINESS_KEYWORDS:
        if any(k in low for k in keys):
            return btype
    return "general"


# LLM이 없을 때(프로덕션에 OPENAI 키 미설정 등) 문장에서 지명만 남기려고
# 걷어내는 것들. 조사·문장부호를 떼고 비교한다.
_CATEGORY_WORDS = {k for keys, _ in _BUSINESS_KEYWORDS for k in keys}
_FILLER_WORDS = {
    "점수", "매겨줘", "매겨", "매겨줄래", "자리", "어때", "어떄", "어떤가", "어떤",
    "분석", "분석해줘", "해줘", "해", "줘", "알려줘", "알려", "근처", "주변", "좀",
    "여기", "이곳", "평가", "평가해줘", "봐줘", "봐", "괜찮을까", "괜찮", "될까",
    "점포", "가게", "창업", "내도", "낼까", "낼", "해도", "돼", "되나", "싶어",
    "싶", "말이야", "부탁", "부탁해", "매길", "매기면",
    # 도구를 요구하는 말들 — 지명이 아니다("신촌에 월세 300 카페 계약해도 될까"에서
    # '계약해도'가 지명에 섞여 지오코딩이 실패했다).
    "계약", "계약해도", "계약해", "계약할까", "계약하면", "오픈", "개업", "운영",
    "운영해도", "차릴까", "차려도", "열까", "열면", "나올까", "있을까", "받으면",
    "상가", "매물", "수익", "우리", "왜", "낮아", "낮은지", "추천해줘", "추천좀",
    "찾아줘", "찾아", "골라줘", "나왔는데", "너무", "비싼가", "적당한가",
    # 비교 질의의 이음말 — 지명이 아니다("연남동이랑 신촌 중에 어디가 나아").
    "중에", "중", "중에서", "둘", "하기", "하려면", "나아", "나은지", "어디가",
    "어디", "어느", "비교", "비교해줘", "vs",
}
# 낱말 목록(_FILLER_WORDS)만으로는 끝이 없다 — "강남역 편의점 낼 만할까?"의
# '만할까'가 목록에 없어서 지명이 '강남역 만할까'가 되고 좌표를 못 찾았다.
# 목록 대신 **서술어 꼬리**를 규칙으로 잡는다. 한국어 의문·서술 어미로 끝나는
# 낱말은 지명이 아니다.
_PREDICATE_TAIL_RE = re.compile(
    r"(할까요?|일까요?|될까요?|을까요?|만할까|만한가|만해|만한지|한가요?|한지|"
    r"는지|는데|나요|가요|군요|겠다|겠어|겠지|해도됨?|좋을까요?|좋은가|좋나|"
    r"어때요?|어떨까|어떤지|어떠냐|어떠니|맞나|맞을까|괜찮나|괜찮아|괜찮은가|"
    r"괜찮을지|될지|할래|해볼까|해봐도)$"
)
# '면'으로 끝나는 서술어는 지명 접미사('양평면')와 겹쳐서 규칙으로 못 가른다 —
# 실제로 쓰이는 동사형만 적어 둔다. 여기서 놓쳐도 _resolve_point의 사다리가
# 뒤 낱말을 떼며 다시 찾으므로 분석이 멈추지는 않는다.
_PREDICATE_MYEON = {
    "하면", "되면", "차리면", "열면", "내면", "받으면", "바꾸면", "옮기면",
    "지으면", "산다면", "빌리면", "계약하면", "창업하면",
}
# 지명 접미사로 끝나면 서술어 규칙을 적용하지 않는다. '면'·'가'·'로'는 어미로도
# 쓰이고 지명 끝글자로도 쓰인다("종로", "신도림3가", "양평면") — 지명 쪽을 살린다.
_PLACE_TAIL_RE = re.compile(r"(역|동|구|로|길|가|읍|면|리|시|군|천|대로|타운|스퀘어)$")

# 조사만 뗀다. '로·가·도'는 조사이기도 하지만 지명 접미사이기도 해서
# ('을지로', '종로3가', '영등포') 떼면 지명이 망가진다 — '을지로'가 '을지'가 돼
# 지오코딩이 실패했다. 이 도메인에서는 지명 쪽이 압도적으로 흔하므로 두고 본다.
_PARTICLE_RE = re.compile(r"(에서|에게|에|의|을|를|은|는|이|과|와|랑|만)$")
_TRAILING_PUNCT_RE = re.compile(r"[?!.,~\s]+$")

_NEED_PLACE_DETAIL = (
    "어느 지역인지 함께 알려 주세요 — 예: 「신촌에 카페 자리 어때」, "
    "「강남역 근처 편의점」."
)
# 도로망·점포 DB가 서울만 적재돼 있다. '부산 서면 카페'처럼 지역을 정확히 말한
# 질의에 "어느 지역인지 알려 주세요"라고만 되물으면, 사용자는 무엇이 잘못됐는지
# 알 길이 없어 같은 질문을 반복하게 된다.
_SEOUL_ONLY = "지금은 서울 안의 지역만 분석할 수 있어요."

# 업종 낱말 뒤에 붙는 상호 접미사. "곱창집"·"떡볶이집"처럼 붙여 쓰면 업종 표에
# 없으므로 떼고 한 번 더 찾는다. 접미사 자체("가게")로는 찾지 않는다 — '무인
# 아이스크림 가게'에서 '가게'가 '반찬가게' 별칭에 걸려 엉뚱한 업종이 잡혔다.
_SHOP_SUFFIXES = ("전문점", "가게", "매장", "집", "점", "샵", "숍", "방")


def _word_forms(token: str) -> list[str]:
    """낱말과, 상호 접미사를 뗀 형태들. "치킨집" → ["치킨집", "치킨"].

    업종어에 접미사가 붙으면("치킨집", "미용샵") 표에 있는 "치킨"·"미용"과
    글자가 달라 업종어로 인식되지 못하고 지명에 그대로 섞여 들어갔다
    ("홍제동 치킨집 될까" → 분석 지점을 '홍제동 치킨집'에서 찾으려 함).
    """
    forms = [token]
    for suffix in _SHOP_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            forms.append(token[: -len(suffix)])
    return forms


def _rule_extract_address(query: str, *, drop: Container[str] = ()) -> str:
    """문장에서 업종·요청어를 걷어내고 지명만 남긴다(LLM 폴백).

    예: "신촌에 카페 자리 점수 매겨줘" → "신촌". 아무것도 안 남으면 원문 그대로
    (카카오 키워드 검색이 '신촌 스타벅스' 같은 장소명도 처리한다).

    ``drop``은 업종 표에서 실제로 걸린 낱말("꽃집", "세탁방")이다. 프리셋 13종에
    없는 업종어는 _CATEGORY_WORDS에 없어서 지명에 그대로 섞여 들어갔다.
    """
    kept: list[str] = []
    for tok in query.split():
        t = _TRAILING_PUNCT_RE.sub("", tok)
        # 조사만 떼되, 떼고 나서 2글자 이상 남을 때만 — "종로"의 "로"까지
        # 지우지 않도록.
        stripped = _PARTICLE_RE.sub("", t)
        if len(stripped) >= 2:
            t = stripped
        if not t:
            continue
        # 금액·조건 낱말은 지명이 아니다. '신촌에 월세 300만원 카페'에서
        # '월세 300만원'까지 지오코딩에 실려 가면 위치를 못 찾는다.
        # 숫자·조건 낱말은 지명이 아니다. '신촌에 월세 300만원 카페'에서
        # '월세 300만원'까지 지오코딩에 실려 가면 위치를 못 찾는다.
        if t[0].isdigit() or any(p.search(t) for p, _ in _PARAM_PATTERNS):
            continue
        forms = _word_forms(t)
        if any(
            f.lower() in _CATEGORY_WORDS or f in _FILLER_WORDS or f in drop
            for f in forms
        ):
            continue
        # 서술어 꼬리로 끝나면 지명이 아니다. 단 지명 접미사(역·동·로…)로 끝나는
        # 낱말은 건드리지 않는다 — '양평면'을 '하면'과 같은 규칙으로 지우면 안 된다.
        if t in _PREDICATE_MYEON:
            continue
        if not _PLACE_TAIL_RE.search(t) and _PREDICATE_TAIL_RE.search(t):
            continue
        kept.append(t)
    return " ".join(kept).strip() or query.strip()


def _match_upjong(tree: list[dict[str, Any]], word: str) -> dict[str, Any] | None:
    """업종 낱말 하나를 SBIZ 업종 분류(대>중>소)에 맞춘다.

    프리셋 13종은 서울 업종의 극히 일부다. 나머지(세탁소·꽃집·정육점·독서실·
    주점…)를 'general'로 흘리면 엔진이 동일업종을 **아예 세지 않고** count=0을
    주고, 서술은 "일반 업종"이라며 아무 판단도 못 한다. 업종 표는 이미
    backend/data/sbiz_upjong.json에 1,000여 개가 들어 있으므로 그걸 그대로 쓴다.

    중분류 이름이 통째로 걸리면 중분류를 먼저 쓴다("주점"→주점업 전체). 소분류로
    좁히면 '술집' 한 마디가 '일반 유흥 주점' 하나로 찍혀 실제보다 좁게 세진다.
    """
    w = (word or "").strip().lower()
    if len(w) < 2 or w in _SHOP_SUFFIXES:
        return None

    for large in tree:
        for mid in large.get("middle") or []:
            name = str(mid.get("name") or "")
            if name and w in name.lower():
                return {
                    "inds_lcls_cd": large.get("code"),
                    "inds_mcls_cd": mid.get("code"),
                    "inds_scls_cd": None,
                    "label": name,
                }

    # 소분류는 이름·별칭이 실제로 걸릴 때만 쓴다. search_upjong은 부모 중분류
    # 이름만 스쳐도 결과를 주므로("커피" → 중분류 매치), 그 느슨한 매치는 버린다.
    for hit in search_upjong(tree, w, limit=5):
        if w in str(hit.get("label", "")).lower() or hit.get("matched_alias"):
            return {
                "inds_lcls_cd": hit.get("inds_lcls_cd"),
                "inds_mcls_cd": hit.get("inds_mcls_cd"),
                "inds_scls_cd": hit.get("inds_scls_cd"),
                "label": hit.get("label", ""),
            }
    return None


def _rule_upjong_from_query(
    tree: list[dict[str, Any]], query: str
) -> tuple[dict[str, Any], str] | None:
    """문장의 낱말을 업종 표에 직접 맞춰 본다 — (업종, 그 낱말) 또는 None.

    가장 긴 낱말이 이긴다. 짧은 낱말일수록 아무 업종이나 부분일치로 걸리기
    때문이다. 지명은 업종 표에 없으므로 '신촌'·'성수동'·'가로수길' 같은 낱말이
    업종으로 오인되지 않는다(전체 질의로 검증).
    """
    best: tuple[dict[str, Any], str] | None = None
    for tok in query.split():
        t = _TRAILING_PUNCT_RE.sub("", tok)
        stripped = _PARTICLE_RE.sub("", t)
        if len(stripped) >= 2:
            t = stripped
        if not t or t in _FILLER_WORDS:
            continue
        # 조건을 가리키는 낱말은 업종어가 아니다. '사도 되나'의 '사도'가
        # 별칭 검색에 걸려 '고용 알선업'으로 분석된 적이 있다.
        if any(p.search(t) for p, _ in _PARAM_PATTERNS):
            continue
        for cand in _word_forms(t):
            hit = _match_upjong(tree, cand)
            if hit is not None:
                if best is None or len(cand) > len(best[1]):
                    best = (hit, t)
                break
    if best is not None:
        return best
    return _alias_in_text(tree, query)


#: 별칭 폴백에서 무시할 짧은 별칭. 두 글자 별칭은 지명·일반어에 우연히 박혀
#: 엉뚱한 업종을 찍는다("연남동"의 '남동'처럼). 세 글자부터만 본다.
_ALIAS_FALLBACK_MIN_LEN = 3


def _alias_in_text(
    tree: list[dict[str, Any]], query: str
) -> tuple[dict[str, Any], str] | None:
    """낱말이 안 걸릴 때, 문장 안에 업종 별칭이 통째로 박혀 있는지 본다.

    "홍대입구샐러드집"처럼 지명과 업종이 붙어 오면 낱말 단위 매칭이 전부
    실패해 general로 새고, 그러면 restaurant 프리셋(한식 고정)으로 떨어져
    샐러드집이 "한식 음식점업"으로 분석됐다. 별칭 표에는 '샐러드'가 이미
    I21005(토스트/샌드위치/샐러드)로 정확히 들어 있으므로 그걸 살려 준다.

    가장 긴 별칭이 이긴다 — 짧은 것부터 잡으면 더 정확한 분류를 놓친다.
    """
    low = (query or "").lower()
    if not low:
        return None
    best: tuple[dict[str, Any], str] | None = None
    for large in tree:
        for mid in large.get("middle") or []:
            for small in mid.get("small") or []:
                for alias in small.get("aliases") or []:
                    a = str(alias).strip().lower()
                    if len(a) < _ALIAS_FALLBACK_MIN_LEN or a not in low:
                        continue
                    if best is None or len(a) > len(best[1]):
                        best = (
                            {
                                "inds_lcls_cd": large.get("code"),
                                "inds_mcls_cd": mid.get("code"),
                                "inds_scls_cd": small.get("code"),
                                "label": str(small.get("name") or ""),
                            },
                            a,
                        )
    return best


def _looks_like_place(text: str) -> bool:
    """요청어를 걷어낸 잔여물이 지명일 법한가.

    LLM이 "지명 없음"이라고 답했는데도 이 잔여물을 지명 자리에 밀어 넣는 바람에
    "'안녕하세요 좋은 추천해 주실 수 있나요'에서 분석할 위치를 찾지 못했어요"
    같은 문장이 사용자에게 나갔다. 지명은 길어야 두 낱말이다('신사동 가로수길').
    """
    t = (text or "").strip()
    return len(t) >= 2 and len(t.split()) <= 2


@dataclass
class _Intent:
    """자연어에서 뽑아낸 분석 대상."""

    address: str
    business_type: str = "general"
    # 프리셋으로 못 맞추는 업종은 SBIZ 코드로 직접 넘긴다(엔진이 그대로 받는다).
    upjong: dict[str, Any] | None = None
    # 비교 질의에서 두 번째 이후 지점("연남동이랑 신촌" → ['연남동','신촌']).
    places: list[str] = field(default_factory=list)
    # 문장에서 뽑은 수치(월세·매입가·현재 매출·평수·층…). 만원/평/층 단위.
    params: dict[str, float] = field(default_factory=dict)
    # LLM이 본 의도. 규칙 라우팅과 어긋나면 필요한 값이 있는 쪽을 따른다.
    llm_tool: str = ""
    # 잠식 분석에서 '내 다른 매장'을 가려낼 브랜드명. 뚜렷하지 않으면 None —
    # 지어내면 실제로 없는 매장을 자기잠식으로 잘못 집계한다.
    brand: str | None = None
    # 실거래 비교(property_market)가 어느 물건 종류를 볼지. "오피스텔"이 없으면
    # 이 프로젝트의 기본 대상인 상가(commercial)로 본다 — PropertyMarketRequest의
    # 기본값과 같은 규칙.
    property_type: str = "commercial"
    # 학원 클러스터 분석(academy_cluster)이 볼 과목. 문장에서 못 뽑으면 None —
    # 지어내면 엉뚱한 과목의 경쟁·시너지 관계로 계산된다(academy_cluster_analysis의
    # target_model은 필수값이라 없으면 도구를 부르지 않는다).
    target_model: str | None = None
    # "유동인구를 더 중요하게 봐줘"처럼 강조된 조건. site_score의 personal_fit
    # 재가중에 그대로 실린다 — 강조 표현이 없으면 빈 리스트(기본 점수만 나온다).
    priorities: list[str] = field(default_factory=list)
    # 프랜차이즈 가맹정보(창업비용·폐업률) 조회가 찾아볼 브랜드명. "이디야
    # 창업비용 얼마야"처럼 창업비용·폐업률류 낱말 바로 앞에 붙은 낱말만 신뢰한다
    # — 뚜렷하지 않으면 None(franchise_brand_detail을 아예 부르지 않는다). 실재
    # 하지 않는 브랜드명이 잡혀도 franchise_brand_detail이 404를 주면 그 값을
    # 지어내지 않고 기본 분석으로 내려간다.
    franchise_brand: str | None = None
    # 도달권역(isochrone) 분석이 볼 시간(분)·이동수단. 문장에 시간이 없으면
    # None — 지어내지 않고 도구 쪽 기본 밴드(5/10/15분)를 쓴다.
    reach_minutes: int | None = None
    reach_mode: str = "walk"


# 지명에는 안 나오고 문장에만 나오는 어미·의문 표지. 추출기가 걷어낼 게 없으면
# 문장을 **통째로** 주소로 돌려주는데("월세 300이면 계약해도 될까?"), 그걸 장소로
# 믿으면 직전 턴을 이어받지 못하고 지오코딩까지 가서 400으로 죽는다.
# 지명 꼬리. 토큰이 이걸로 끝나면 지명으로 본다.
# 읍·면·리는 뺐다 — 서울에 없을 뿐 아니라 "300이면"·"얼마면"의 '면'을 지명으로
# 오인한다. '가'는 숫자 뒤에서만 인정한다(영등포동5가). 안 그러면 "저평가"가
# 걸린다.
_PLACE_SUFFIX_RE = re.compile(
    r"(?:동|구|시|군|로|길|역|천|산|공원|대교|타워|센터|시장|사거리|네거리)$"
    r"|\d\s*가$"
)
# 지명에는 안 나오고 문장에만 나오는 표지. 꼬리 검사만으로는 "이 지역"의 '역'처럼
# 단음절 꼬리가 우연히 맞는 경우를 못 걸러서 함께 본다.
_SENTENCE_MARK_RE = re.compile(
    r"[?？]"
    r"|할까|될까|일까|좋을까|괜찮을까|어떨까|어때"
    r"|인가요|나요|까요|는데요|이야|이에요|예요|입니다"
    r"|이면|라면|해도|해야|하면"
    r"|^\s*(?:여기|거기|저기|그곳|이곳|그럼|그러면|그거)"
    r"|얼마|몇\s|어디|언제|어떻게"
    r"|\d\s*(?:평|만원|억|원|층)"
)
# 꼬리가 없어도 지명인 것들(신촌·홍대·강남). 짧은 한 덩어리면 지명으로 본다.
_BARE_PLACE_MAX_LEN = 5
# 이 서비스에서 자주 나오지만 지명이 아닌 말. '지역'은 '역' 꼬리에, '저평가'는
# 짧은 한 덩어리 규칙에 걸려 지명으로 새어 나간다.
# 2026-11-06: 광역어(서울·수도권·전국 …)를 더했다. 「서울에서 월세 200 안쪽 샐러드
# 가게, 어느 동네가 좋아?」의 '서울'을 장소로 잡아 서울시청을 분석하고 종각
# 지하역사를 추천했다(사용자 테스트, SPEC_ANSWER_DECISION §9-2). 광역어만 있는 질문은
# 「어디가 좋냐」는 질문이지 한 자리의 판정이 아니다 — 상담이 답한다.
_BROAD_REGION_WORDS = frozenset(
    {"서울", "서울시", "서울특별시", "서울시내", "서울권", "수도권", "전국", "한국", "국내", "시내", "경기", "경기도"}
)
_NOT_PLACE_WORDS = frozenset(
    {"지역", "저평가", "고평가", "시세", "매물", "가격", "땅", "부지", "필지", "공시지가"}
) | _BROAD_REGION_WORDS


_PARTICLE_TAIL_RE = re.compile(r"(?:에서|에서는|에선|에|의|은|는|이|가|로|으로)$")


def _is_broad_region(address: str) -> bool:
    """「서울」·「서울에서」·「수도권 전체」처럼 광역어로만 된 장소인가."""
    tokens = [_PARTICLE_TAIL_RE.sub("", t) for t in (address or "").split()]
    tokens = [t for t in tokens if t and t not in {"전체", "전역", "안", "내", "쪽"}]
    return bool(tokens) and all(t in _BROAD_REGION_WORDS for t in tokens)


def _has_place(address: str) -> bool:
    """뽑힌 address가 실제 지명인가, 걷어낼 게 없어 남은 찌꺼기인가.

    두 가지를 걸러낸다.

    1. 업종만 친 질의("카페")는 추출기가 업종어를 그대로 주소로 돌려준다
       (agent_ask의 _CATEGORY_WORDS 검사와 같은 기준).
    2. 지명이 아예 없는 후속 질문("월세 300이면 계약해도 될까?", "저평가된 땅
       있어?")은 추출기가 걷어낼 게 없어 **문장을 통째로** 주소로 돌려준다.
       이걸 장소로 믿으면 직전 턴의 장소를 이어받지 못해 "어느 지역인지 알려
       주세요"만 반복한다 — 후속 질문은 실제 대화에서 가장 흔한 형태라 이게 곧
       대화가 끊기는 것이다.

    **어미 블랙리스트가 아니라 지명 생김새로 판단한다.** 처음엔 의문·서술 어미를
    나열했는데, 추출기가 어미를 먼저 걷어내는 탓에("저평가된 땅 있어?" →
    "저평가된 땅 있어") 잡히지 않는 문장이 계속 나와 두더지잡기가 됐다. 한국어
    지명은 꼴이 좁아서 이쪽이 훨씬 안정적이다.
    """
    text = (address or "").strip()
    if not text or text.lower() in _CATEGORY_WORDS:
        return False
    if _SENTENCE_MARK_RE.search(text):
        return False
    tokens = [t for t in text.split() if t not in _NOT_PLACE_WORDS]
    if not tokens:
        return False
    if any(_PLACE_SUFFIX_RE.search(t) for t in tokens):
        return True
    # 꼬리가 없으면 "짧은 한 덩어리"만 지명으로 인정한다. 문장은 거의 항상
    # 여러 토큰이거나 길다. 길이는 **걸러낸 뒤 남은 토큰**으로 잰다 — 원문으로
    # 재면 '이 지역'에서 '지역'을 걷어내고 남은 '이'가 4자 지명으로 통과한다.
    return len(tokens) == 1 and 2 <= len(tokens[0]) <= _BARE_PLACE_MAX_LEN


def _continue_from_history(intent: _Intent, history: list["AgentHistoryTurn"]) -> _Intent:
    """이번 질문에 빠진 장소·업종·수치를 직전 대화 턴에서 이어받는다.

    "신촌 카페 어때" 다음에 "월세 300이면 계약해도 될까?"처럼 장소를 다시
    말하지 않는 후속 질문이 실제 대화에서 가장 흔하다. 이번 문장에서 지명이
    전혀 안 뽑혔을 때만 가장 최근 턴의 장소를 그대로 쓴다 — 이번 문장이 새
    장소를 댔으면(비교 질의 등) 그쪽이 우선이라 손대지 않는다.

    업종도 이번 문장에 업종 신호가 전혀 없을 때만(일반으로 새고 SBIZ 매치도
    없을 때) 이어받는다 — 업종을 새로 말했으면 그 업종이 우선이다. 수치는
    라벨이 다르면 서로 안 겹치므로 항상 직전 값 위에 이번 값을 얹어 병합한다
    (이번 문장 값이 같은 라벨이면 그 값이 이긴다).
    """
    if not history:
        return intent
    last = history[-1]
    if _has_place(intent.address):
        # 반대 방향: 직전 턴이 업종만 말하고 위치를 되물었는데("카페" → "어느 지역인지
        # 알려 주세요") 이번에 위치만 답하면("신촌") 그 업종을 잇는다. 전엔 업종이
        # 사라져 「신촌 · 일반」으로 분석했다 (2026-11-08 UX 자동 개선).
        if (
            intent.business_type == "general"
            and intent.upjong is None
            and not _has_place(last.address)
            and (last.business_type != "general" or last.upjong is not None)
        ):
            return replace(intent, business_type=last.business_type, upjong=last.upjong)
        return intent
    if not _has_place(last.address):
        return intent
    business_type = intent.business_type
    upjong = intent.upjong
    if business_type == "general" and upjong is None:
        business_type = last.business_type
        upjong = last.upjong
    params = dict(last.params)
    params.update(intent.params)
    return replace(
        intent,
        address=last.address,
        places=[last.address],
        business_type=business_type,
        upjong=upjong,
        params=params,
    )


# 지점을 둘 이상 늘어놓는 접속. '연남동이랑 신촌', '홍대, 연남동', 'A vs B'.
_PLACE_SPLIT_RE = re.compile(r"\s*(?:이랑|랑|하고|와|과|vs|VS|versus|,|·|/)\s*")


def _split_places(address: str) -> list[str]:
    """한 문자열에 뭉쳐 온 지점들을 나눈다.

    '연남동, 신촌'을 한 주소로 지오코딩하면 둘 다 아닌 엉뚱한 점이 잡힌다.
    """
    parts = [p.strip() for p in _PLACE_SPLIT_RE.split(address or "") if p.strip()]
    # 접속사가 없으면 원문 그대로 한 곳.
    return parts if len(parts) > 1 else ([address.strip()] if address.strip() else [])


def _rule_places(query: str, *, drop: Container[str] = ()) -> list[str]:
    """LLM 없이 문장에서 지점들을 뽑는다.

    접속사를 **원문에서** 먼저 자른 뒤 조각마다 요청어를 걷어낸다. 정리된 주소
    문자열에서 자르면 이미 조사가 떨어져('연남동이랑'→'연남동이') 접속사가
    사라진 뒤라 두 곳으로 나뉘지 않았다.
    """
    chunks = [c for c in _PLACE_SPLIT_RE.split(query or "") if c.strip()]
    if len(chunks) < 2:
        return _split_places(_rule_extract_address(query, drop=drop))
    places: list[str] = []
    for chunk in chunks:
        cleaned = _rule_extract_address(chunk, drop=drop).strip()
        # 조각 안에서 아무것도 못 걷어냈으면 지명 조각이 아니다.
        if cleaned and cleaned != chunk.strip() or (cleaned and len(cleaned.split()) == 1):
            places.append(cleaned)
    return places if len(places) > 1 else _split_places(
        _rule_extract_address(query, drop=drop)
    )


def _confident_rule_intent(
    query: str, tree: list[dict[str, Any]] | None = None
) -> _Intent | None:
    """규칙만으로 확신이 서면 _Intent, 아니면 None.

    업종이 실제로 잡히고("카페"→cafe, "꽃집"→G21901) 요청어를 걷어낸 뒤 지명이
    **한 낱말로 깔끔히** 남았을 때만 확신으로 본다. 이때 LLM 파서를 건너뛰어
    왕복 한 번(~1.5초)을 통째로 아낀다 — 응답 예산 5초에서 가장 큰 단일 절감이다.

    낱말이 여럿 남으면("신촌 어딘가 커피숍" → "신촌 어딘가") 문장이 모호하다는
    뜻이므로 LLM에 맡긴다 — 그쪽은 '서울 서대문구 신촌로 90'처럼 정식 주소로
    정규화해 준다.
    """
    btype = _keyword_business_type(query)
    upjong: dict[str, Any] | None = None
    drop: set[str] = set()
    found = _specific_upjong_beats_preset(btype, tree, query)
    if found is not None:
        upjong, word = found
        drop.add(word)
    elif btype == "general":
        # 업종을 못 찾았는데 프리셋도 없으면 문장을 이해했다고 볼 수 없다.
        # 뭉개진 프리셋(restaurant 등)은 그대로 두고 진행한다.
        return None

    stripped = _rule_extract_address(query, drop=drop).strip()
    # 아무것도 못 걷어냈으면(원문 그대로) 문장 구조를 이해했다고 볼 수 없다.
    if len(stripped) < 2 or stripped == query.strip():
        return None
    if len(stripped.split()) != 1:
        return None
    return _Intent(
        address=stripped,
        business_type=btype,
        upjong=upjong,
        places=[stripped],
        params=_extract_parameters(query),
        brand=_extract_brand(query),
        property_type=_extract_property_type(query),
        target_model=_extract_target_model(query),
        priorities=_extract_priorities(query),
        reach_minutes=_extract_reach_minutes(query),
        reach_mode=_extract_reach_mode(query),
        franchise_brand=_extract_franchise_brand(query),
    )


async def _extract_intent(query: str, tree: list[dict[str, Any]] | None = None) -> _Intent:
    """자연어 → 분석 대상(_Intent). LLM 실패 시 규칙 폴백.

    폴백은 주소를 문장 그대로 두고(카카오 키워드 검색이 '신촌 스타벅스' 같은
    장소명도 처리한다) 업종만 키워드·업종표로 유추한다.
    """
    tree = tree or []
    confident = _confident_rule_intent(query, tree)
    if confident is not None:
        if confident.franchise_brand is None:
            confident.franchise_brand = await _fallback_franchise_brand(query)
        return confident

    rule_btype = _keyword_business_type(query)
    rule_upjong = _specific_upjong_beats_preset(rule_btype, tree, query)
    drop = {rule_upjong[1]} if rule_upjong else set()
    rule_address = _rule_extract_address(query, drop=drop)

    params = _extract_parameters(query)

    parsed = await complete_json(
        system=(
            "너는 한국 상권분석 도우미의 파서다. 사용자 문장에서 분석할 '장소'와 "
            "'업종', '의도'만 뽑아 JSON으로만 답한다. 설명 금지.\n"
            'JSON 형식: {"places": ["<지명이나 주소, 여러 곳을 비교하면 나온 순서대로 '
            '모두. 문장에 지명이 없으면 빈 배열>"], '
            '"business_type": "cafe|convenience|restaurant|clinic|academy|'
            'beauty|fitness|leisure|lodging|gas_station|ev_charging|'
            'professional_office|general", '
            '"business_word": "<업종을 가리키는 표준 한국어 업종명 한 낱말, '
            '문장에 업종이 없으면 빈 문자열>", '
            '"tool": "site_score|lease_verdict|purchase_verdict|profitability|'
            'decide|area_search|diagnose|trends|cannibalization|area_insights|'
            'territory|real_competition|property_market|academy_cluster|'
            'lease_feasibility|isochrone|franchise_brand_detail|industry_profile"}\n'
            "업종 힌트: beauty=미용실·헤어·네일·피부관리, fitness=헬스·필라테스·요가, "
            "leisure=노래방·오락실·당구장·볼링, lodging=호텔·모텔·숙박, "
            "gas_station=주유소, ev_charging=전기차충전소, "
            "professional_office=부동산·공인중개·변호사·세무사. "
            "위 어디에도 안 맞으면 business_type은 general로 두되, business_word에는 "
            "구어·신조어를 통계청 업종 분류에서 쓸 법한 표준 명칭으로 바꿔 적는다.\n"
            "도구 뜻: site_score=그 자리가 이 업종에 좋은지, "
            "lease_verdict=제시된 월세로 임차 계약해도 되는지, "
            "purchase_verdict=제시된 값에 상가를 매입해도 되는지, "
            "profitability=매입 후 임대 수익률이 나오는지, "
            "decide=여러 후보지 중 어디가 나은지, "
            "area_search=동네 안에서 좋은 자리를 여러 곳 찾아 달라, "
            "analogs=이 자리·동네와 비슷한(유사한·닮은) 상권이 어디인지 찾아 "
            "성과를 비교해 달라(자리를 새로 찾는 게 아니라 비교가 목적), "
            "diagnose=이미 장사 중인데 왜 매출이 낮은지, "
            "trends=이 동네·업종이 뜨는지 지는지(분기 추이·성장 신호), "
            "cannibalization=새 자리가 근처 동일업종·같은 브랜드 매장의 매출을 "
            "얼마나 잠식(자기잠식 포함)할지, "
            "area_insights=주변 아파트 실거래 시세와 생활인구(유동인구) 시간대·"
            "성별·연령 분포가 어떤지, "
            "territory=새로 들어가면 주변 기존 브랜드들의 배달권역(Before/After)이 "
            "겹치거나 비거나 흡수되는지, "
            "real_competition=이 자리·업종이 직접·대체·시간대별 경쟁 구조에서 "
            "얼마나 치열한 위치인지와 어떤 사업 모델(가격대·컨셉)이 이 자리에 맞는지, "
            "property_market=주변 상가·오피스텔 매매 실거래가와 비교했을 때 이 "
            "가격(또는 시세)이 비싼지 적정한지, "
            "academy_cluster=학원 자리에서 같은·인접 과목 학원들과 직접 경쟁·"
            "시너지·학부모 안전이 어떤지(과목이 문장에 있어야 함), "
            "lease_feasibility=제시된 월세·운영조건으로 손익분기·적정 임차비·"
            "업종 평균 매출 대비 타당성까지 상세히 따져 달라는 것(간단한 계약 "
            "가부만 물으면 lease_verdict를 쓴다), "
            "isochrone=도보·자전거·차량 등 이동수단으로 특정 시간 안에 도달하는 "
            "권역(등시선) 안에 몇 명이 사는지·업소가 몇 곳인지 묻는 것(예: '도보 "
            "10분 안에 몇 명 살아'), "
            "franchise_brand_detail=특정 프랜차이즈 브랜드의 창업비용·폐업률이 "
            "얼마인지 묻는 것(브랜드명이 문장에 뚜렷이 있어야 함), "
            "industry_profile=이 자리의 점수가 아니라 그 업종을 분석할 때 "
            "일반적으로 무엇을 확인해야 하는지(체크리스트) 묻는 것(예: '카페 "
            "창업할 때 뭘 챙겨야 해')."
        ),
        user=query,
        max_tokens=200,
        timeout_s=8.0,
    )
    brand = _extract_brand(query)
    property_type = _extract_property_type(query)
    target_model = _extract_target_model(query)
    priorities = _extract_priorities(query)
    reach_minutes = _extract_reach_minutes(query)
    reach_mode = _extract_reach_mode(query)
    franchise_brand = _extract_franchise_brand(query) or await _fallback_franchise_brand(query)
    if not parsed:
        places = _rule_places(query, drop=drop)
        return _Intent(
            address=places[0] if places else rule_address,
            business_type=rule_btype,
            upjong=rule_upjong[0] if rule_upjong else None,
            places=places,
            params=params,
            brand=brand,
            property_type=property_type,
            target_model=target_model,
            priorities=priorities,
            reach_minutes=reach_minutes,
            reach_mode=reach_mode,
            franchise_brand=franchise_brand,
        )

    raw_places = parsed.get("places")
    places = [str(p).strip() for p in raw_places if str(p).strip()] if isinstance(raw_places, list) else []
    # 예전 형식(address 한 개)도 받아 준다.
    if not places:
        single = str(parsed.get("address") or "").strip()
        places = _split_places(single) if single else []
    if not places:
        # LLM이 "지명 없음"이라고 답한 것 자체가 정보다. 잔여물이 지명일 법할
        # 때만 폴백을 쓰고, 문장 찌꺼기면 지명을 되묻는다.
        places = _rule_places(query, drop=drop) if _looks_like_place(rule_address) else []

    btype = str(parsed.get("business_type") or "").strip().lower()
    if btype not in _BUSINESS_TYPES:
        btype = rule_btype
    elif btype == "general" and rule_btype != "general":
        # 규칙이 아는 구체 업종을 LLM의 'general'이 덮지 못하게 한다. '스터디카페',
        # '키즈카페'처럼 합성어를 LLM이 general로 흘려보내면 경쟁 집계가 통째로
        # 꺼져 "판단 불가"만 남았다. 구체적인 쪽이 항상 더 많은 정보를 준다.
        btype = rule_btype

    upjong: dict[str, Any] | None = None
    if btype == "general":
        word = str(parsed.get("business_word") or "").strip()
        upjong = (_match_upjong(tree, word) if word else None) or (
            rule_upjong[0] if rule_upjong else None
        )
    elif rule_upjong is not None:
        # 프리셋으로 갔더라도 문장에 더 정확한 소분류가 있으면 그쪽을 쓴다.
        # rule_upjong은 _specific_upjong_beats_preset이 이미 그 조건으로만
        # 채워 주므로 여기서 다시 걸러낼 게 없다.
        #
        # 이 분기를 빼먹어서 '샐러드집'이 라이브에서 계속 한식으로 나왔다.
        # 규칙 경로만 고치면 LLM이 붙은 요청(answer_source=llm)은 그대로 샌다 —
        # 실제 사용자 요청은 대부분 그쪽으로 간다.
        upjong = rule_upjong[0]

    llm_tool = str(parsed.get("tool") or "").strip().lower()
    return _Intent(
        address=places[0] if places else "",
        business_type=btype,
        upjong=upjong,
        places=places,
        params=params,
        llm_tool=llm_tool if llm_tool in _TOOLS else "",
        brand=brand,
        property_type=property_type,
        target_model=target_model,
        priorities=priorities,
        reach_minutes=reach_minutes,
        reach_mode=reach_mode,
        franchise_brand=franchise_brand,
    )


# ─────────────────────────── 수치 파라미터 추출 ───────────────────────────
#
# 지금까지는 문장에서 주소·업종만 뽑고 "월세 300", "10억", "월매출 800" 같은
# 수치를 통째로 버렸다. 그래서 "월세 300만원에 계약해도 될까?"에도 그냥 입지
# 점수만 나왔다. 아래는 한국어 금액 표기를 만원 단위 수치로 바꾸고, 어떤 값인지
# (월세·보증금·매입가·현재매출…) 라벨로 붙이는 층이다. 특정 문장이 아니라
# **단위·라벨 사전**이므로 처음 보는 문장에도 그대로 통한다.

# 만원 기준 배수. '5천'은 부동산 화법에서 5천만원(=5000만원)이다.
_MONEY_UNITS: dict[str, float] = {
    "억": 10_000.0,
    "천만": 1_000.0,
    "백만": 100.0,
    "천": 1_000.0,
    "백": 100.0,
    "십만": 10.0,
    "만": 1.0,
}
# 돈이 아닌 숫자는 잡지 않는다. '12억 30평 매입'에서 '매입' 바로 앞의 '30평'을
# 매입가로 읽어 12억을 놓쳤다 — 뒤에 평·층·곳 같은 단위가 붙으면 금액이 아니다.
_NON_MONEY_UNITS = r"평|층|곳|개|군데|대|시간|시|명|년|일|%|㎡|m2|m²|번|호|분"
# '3억5천'처럼 단위가 이어지는 표기를 한 덩어리로 읽는다. 따로 읽으면 뒤쪽('5천')
# 만 남아 3억5천이 5천만원이 됐다. 앞뒤 (?<!\d)/(?!\d)는 '30평'에서 '3'만 떼어
# 금액으로 읽던 부분 매치를 막는다.
_MONEY_TOKEN_RE = re.compile(
    r"(?<!\d)(?:\d[\d,]*(?:\.\d+)?\s*(?:억|천만|백만|십만|천|백|만)?\s*)+원?"
    r"(?!\s*(?:" + _NON_MONEY_UNITS + r"))(?!\d)"
)
# 단위가 붙은 금액('8억')은 맨숫자('350')보다 금액일 가능성이 높다.
_HAS_MONEY_UNIT_RE = re.compile(r"억|천만|백만|십만|천|백|만|원")
_MONEY_PART_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|천만|백만|십만|천|백|만)?")


def _money_to_man(text: str) -> float | None:
    """한국어 금액 표기 → 만원 단위 수치.

    '10억'→100,000 / '3백만원'→300 / '보증금 5천'→5,000 / '월세 300'→300 /
    '1억2천'→12,000. 단위가 없으면 만원으로 본다(상권 대화의 기본 단위).
    """
    cleaned = re.sub(r"[,\s]", "", text or "")
    if not cleaned:
        return None
    total = 0.0
    found = False
    for m in _MONEY_PART_RE.finditer(cleaned):
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        total += value * _MONEY_UNITS.get(m.group(2) or "", 1.0)
        found = True
    return total if found else None


# 라벨 → 파라미터. 위에서부터 먼저 맞는 것을 쓰므로 구체적인 말이 위에 온다
# ('월매출'이 '월세'보다 먼저 걸려야 월세로 잘못 잡히지 않는다). 매입은 어미가
# 계속 달라져서('사면/사서/사려고/살까') 낱말을 나열하지 않고 어간으로 잡는다.
_PARAM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"보증금|전세금|임차보증금"), "deposit_man"),
    (re.compile(r"권리금|바닥권리"), "key_money_man"),
    (re.compile(r"관리비"), "maintenance_man"),
    (re.compile(r"월\s*매출|매출|매상"), "current_monthly_sales_man"),
    (re.compile(r"월세|임대료|차임|월\s*임대|렌트"), "monthly_rent_man"),
    (
        # '사-'는 어미가 끝없이 갈리므로 어간으로 잡되, 낱말 첫 글자일 때만 본다
        # ('회사도'의 '사도'가 매입으로 잡히면 안 된다).
        re.compile(r"매입|매매|매수|구입|분양|호가|매물가|(?:^|(?<=[\s\d]))사(?=[서면려고도야])|(?:^|(?<=[\s\d]))살(?=까)"),
        "price_manwon",
    ),
]


def _other_label_between(segment: str, own: re.Pattern[str]) -> bool:
    """이 구간에 **다른** 파라미터 라벨이 있는가.

    있으면 그 숫자는 그쪽 라벨의 것이다. 이 검사가 없으면 '보증금 5천에 월세
    300인'에서 월세를 5,000만원으로 읽고, '8억에 사서 월세 350'에서 월세를
    8억으로 읽었다.
    """
    return any(p.search(segment) for p, _ in _PARAM_PATTERNS if p is not own)
# 라벨 없이 '월 300'처럼 쓰는 어순. 바로 뒤가 숫자일 때만 월세로 본다
# ('월매출'·'월요일'이 걸리지 않게).
_BARE_MONTHLY_RE = re.compile(r"월\s*(\d[\d,]*(?:\.\d+)?\s*(?:억|천만|백만|천|백|만)?\s*원?)")
_PYEONG_RE = re.compile(r"(\d+(?:\.\d+)?)\s*평")
_SQM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:㎡|m2|m²|제곱미터)")
_FLOOR_RE = re.compile(r"(지하\s*)?(\d+)\s*층")
_COUNT_RE = re.compile(r"(\d+)\s*(?:곳|개|군데|자리)")
# 라벨 주변에서 숫자를 찾는 창. 너무 넓히면 옆 문장의 숫자를 끌어온다.
_LABEL_WINDOW = 16


def _labelled_amount(text: str, label: re.Pattern[str]) -> float | None:
    """라벨 뒤(없으면 앞)에서 가장 가까운 금액을 만원 단위로 읽는다.

    뒤를 먼저 보는 이유: '월세 300'이 기본 어순이다. 앞도 보는 이유: '10억에
    사면'처럼 숫자가 라벨 앞에 오는 말도 흔하다.
    """
    for hit in label.finditer(text):
        # (금액, 단위가 붙었는가, 라벨에서 떨어진 글자 수) 후보를 모아 고른다.
        # 뒤만 보면 '8억에 사서 월세 350'에서 매입가를 350으로 읽고, 앞만 보면
        # '월세 300'을 못 읽는다.
        options: list[tuple[float, bool, int]] = []
        after = text[hit.end() : hit.end() + _LABEL_WINDOW]
        m = _MONEY_TOKEN_RE.search(after)
        if m is not None and not _other_label_between(after[: m.start()], label):
            value = _money_to_man(m.group())
            if value:
                options.append((value, bool(_HAS_MONEY_UNIT_RE.search(m.group())), m.start()))
        before = text[max(0, hit.start() - _LABEL_WINDOW) : hit.start()]
        found = list(_MONEY_TOKEN_RE.finditer(before))
        if found:
            last = found[-1]
            # 그 숫자가 이미 다른 라벨의 것이면(앞에 다른 라벨이 붙어 있거나,
            # 사이에 다른 라벨이 끼어 있으면) 가져오지 않는다.
            claimed = _other_label_between(
                before[: last.start()], label
            ) or _other_label_between(before[last.end() :], label)
            value = _money_to_man(last.group()) if not claimed else None
            if value:
                options.append(
                    (value, bool(_HAS_MONEY_UNIT_RE.search(last.group())), len(before) - last.end())
                )
        if options:
            # 단위가 붙은 쪽 우선, 같으면 라벨에 가까운 쪽.
            best = max(options, key=lambda o: (o[1], -o[2]))
            return best[0]
    return None


def _extract_parameters(query: str) -> dict[str, float]:
    """문장에서 도구에 넘길 수치를 뽑는다. 라벨이 붙은 숫자만 쓴다.

    라벨을 요구하는 이유: 문장에는 '24시간', '20대'처럼 돈이 아닌 숫자가 섞여
    있어서, 아무 숫자나 집으면 엉뚱한 값이 도구로 넘어간다.
    """
    params: dict[str, float] = {}
    for pattern, name in _PARAM_PATTERNS:
        value = _labelled_amount(query, pattern)
        if value:
            params.setdefault(name, value)

    if "monthly_rent_man" not in params:
        m = _BARE_MONTHLY_RE.search(query)
        if m is not None:
            value = _money_to_man(m.group(1))
            if value:
                params["monthly_rent_man"] = value

    m = _PYEONG_RE.search(query)
    if m is not None:
        params["area_pyeong"] = float(m.group(1))
    m = _SQM_RE.search(query)
    if m is not None:
        params["area_sqm"] = float(m.group(1))
    m = _FLOOR_RE.search(query)
    if m is not None:
        floor = int(m.group(2))
        params["floor"] = float(-floor if m.group(1) else floor)
    m = _COUNT_RE.search(query)
    if m is not None:
        count = int(m.group(1))
        if 1 <= count <= 20:
            params["max_candidates"] = float(count)
    return params


# 잠식 분석에서 '내 다른 매장'을 가리기 위한 브랜드명. "스타벅스 2호점"처럼 호점
# 앞에 바로 붙은 상호만 신뢰한다 — 그 밖의 자리에서 아무 명사나 브랜드로 읽으면
# 실재하지 않는 매장을 자기잠식으로 잘못 집계한다.
_BRAND_HOJEOM_RE = re.compile(r"([가-힣A-Za-z0-9&]{2,20})\s*\d+\s*호점")


def _extract_brand(query: str) -> str | None:
    """문장에서 잠식 분석용 브랜드명을 뽑는다. 뚜렷하지 않으면 None.

    "여기 2호점 내면 매출 뺏겨?"처럼 호점 앞에 브랜드가 없으면 그 앞 낱말은
    "여기"·"우리" 같은 요청어일 뿐이다 — 업종어·요청어면 브랜드로 보지 않는다.
    """
    m = _BRAND_HOJEOM_RE.search(query)
    if not m:
        return None
    word = m.group(1).strip()
    if len(word) < 2 or word in _FILLER_WORDS or word.lower() in _CATEGORY_WORDS:
        return None
    return word


# 프랜차이즈 가맹정보(창업비용·폐업률·가맹점수)를 묻는 트리거 낱말들.
_FRANCHISE_TRIGGER_RE = re.compile(r"창업\s*비용|폐업\s*률|폐업\s*율|가맹점\s*수")

# 프랜차이즈 가맹정보 조회용 브랜드명. "이디야 창업비용 얼마야"처럼 위 트리거
# 바로 앞에 붙은 상호만 신뢰한다 — 이 값은 franchise_brands 테이블 조회의
# 검색어일 뿐이라 실재하지 않는 이름이 잡혀도 franchise_brand_detail이 404를
# 주면 그대로 기본 분석으로 내려간다(지어낸 값을 화면에 내보내지 않는다).
_FRANCHISE_BRAND_RE = re.compile(
    r"([가-힣A-Za-z0-9&]{2,20})\s*(?:은|는|이|가|의)?\s*(?:"
    + _FRANCHISE_TRIGGER_RE.pattern
    + r")"
)


def _extract_franchise_brand(query: str) -> str | None:
    """문장에서 프랜차이즈 가맹정보 조회용 브랜드명을 뽑는다. 뚜렷하지 않으면 None.

    "창업비용 얼마야"처럼 트리거 낱말 앞에 상호가 없으면 그 앞 낱말은 요청어일
    뿐이다 — 업종어·요청어면 브랜드로 보지 않는다(_extract_brand와 같은 규칙).
    """
    m = _FRANCHISE_BRAND_RE.search(query)
    if not m:
        return None
    word = m.group(1).strip()
    if len(word) < 2 or word in _FILLER_WORDS or word.lower() in _CATEGORY_WORDS:
        return None
    return word


# 위 정규식이 못 뽑는 경우("이디야 커피 창업비용" — 트리거 바로 앞 낱말이
# 업종어 "커피"라 걸러짐)를 위한 폴백. 트리거 낱말이 문장에 있는데도 규칙
# 추출이 실패했을 때만 시도한다 — 엉뚱한 문장의 아무 낱말이나 검색에 태우지
# 않는다. /api/franchise/brands 검색이 실제로 있는 행을 찾아야만 값을 쓰므로
# (빈 결과면 그대로 None) 지어낸 값이 아니다 — franchise_brand_detail의 404
# 폴백(_run_tool 참고)과 같은 원칙.
async def _fallback_franchise_brand(query: str) -> str | None:
    """규칙 추출이 실패했을 때 /api/franchise/brands 검색으로 브랜드를 찾는다.

    트리거 낱말을 걷어낸 나머지 낱말들을 문장에 나온 순서대로 하나씩 검색해,
    실제 franchise_brands 테이블에 있는 브랜드가 나오면 그 값을 쓴다.
    """
    if not _FRANCHISE_TRIGGER_RE.search(query):
        return None
    remainder = _FRANCHISE_TRIGGER_RE.sub(" ", query)
    tried: set[str] = set()
    for tok in remainder.split():
        word = _TRAILING_PUNCT_RE.sub("", tok)
        stripped = _PARTICLE_RE.sub("", word)
        if len(stripped) >= 2:
            word = stripped
        if len(word) < 2 or word in _FILLER_WORDS or word.lower() in _CATEGORY_WORDS:
            continue
        if word in tried:
            continue
        tried.add(word)
        result = await run_franchise_brands(q=word, limit=1)
        if result.brands:
            return result.brands[0].brand_nm
    return None


def _extract_property_type(query: str) -> str:
    """실거래 비교(property_market)가 볼 물건 종류. "오피스텔"이 없으면 상가."""
    return "officetel" if "오피스텔" in query else "commercial"


# 학원 클러스터 분석(academy_cluster)이 받는 과목. academy_cluster_analysis.
# VALID_TARGET_MODELS·academy_competitor_rules.json의 target_models와 같은
# 8개다 — 위에서부터 먼저 맞는 것을 쓴다("수학"과 "과학"이 한 문장에 같이 있으면
# 앞쪽 과목을 기준으로 본다).
_TARGET_MODEL_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("수학",), "math"),
    (("영어",), "english"),
    (("국어", "논술"), "korean"),
    (("과학",), "science"),
    (("입시", "보습"), "exam_cram"),
    (("예체능", "미술", "음악", "무용", "발레", "피아노", "체육", "태권도"), "arts"),
    (("코딩", "프로그래밍"), "coding"),
    (("성인", "자격증"), "adult"),
]


def _extract_target_model(query: str) -> str | None:
    """문장에서 학원 과목을 뽑는다. 못 뽑으면 None — 지어내지 않는다."""
    for words, model in _TARGET_MODEL_KEYWORDS:
        if any(w in query for w in words):
            return model
    return None


# "도보 10분 안에 몇 명 살아" 류 도달권역(isochrone) 질문. 이동수단+시간이 붙어야
# 시간 기준 질문으로 보고("10분"만으로는 다른 맥락일 수 있다), 인구·거주 낱말이
# 함께 있어야 이 도구로 간다 — 그냥 "도보 10분 거리"는 위치 설명일 뿐 도달권역
# 계산을 요구하는 게 아니다.
_ISOCHRONE_TIME_RE = re.compile(
    r"(?:도보|걸어서|차로|운전해서|자동차로|자전거로?|오토바이로|배달)\s*(\d{1,2})\s*분|"
    r"(\d{1,2})\s*분\s*(?:거리|이내|안에|안|컷|권)"
)
_ISOCHRONE_POPULATION_RE = re.compile(
    r"몇\s*명|인구|거주|주민|사는|살아|생활\s*인구|배후\s*(?:수요|인구)"
)

# 이동수단 낱말 → services/isochrone.py의 mode 값. 아무것도 없으면 도보(기본).
_REACH_MODE_KEYWORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"차로|운전해서|자동차로|드라이브"), "drive"),
    (re.compile(r"자전거로?\s*(타고)?"), "bike"),
    (re.compile(r"오토바이로|배달\s*(거리|권|기준)"), "moto"),
    (re.compile(r"도보로?|걸어서"), "walk"),
]


def _extract_reach_mode(query: str) -> str:
    """도달권역 계산에 쓸 이동수단. 못 찾으면 도보가 기본이다."""
    for pattern, mode in _REACH_MODE_KEYWORDS:
        if pattern.search(query):
            return mode
    return "walk"


def _extract_reach_minutes(query: str) -> int | None:
    """문장에 붙은 도달 시간(분). 못 뽑으면 None — 도구 쪽 기본 밴드를 쓴다."""
    m = _ISOCHRONE_TIME_RE.search(query)
    if not m:
        return None
    raw = m.group(1) or m.group(2)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 30 else None


# personal_fit(우선순위 재가중)이 반응하는 강조 표현. "유동인구가 많다"처럼
# 사실을 묻기만 한 문장까지 재가중으로 해석하면 물어보지도 않은 personal_fit이
# 매번 켜져 화면에 나타난다 — 이 강조 표현이 있을 때만 아래 키워드를 우선순위로
# 본다. site_score.PRIORITIES는 이미 personal_fit.py에 있으므로 그대로 쓴다.
_PRIORITY_EMPHASIS_RE = re.compile(
    r"더\s*중요|중요하게|우선순위|위주|비중\s*(있게|높게|크게)|가중치|"
    r"최우선|가장\s*중요|우선\s*고려|신경\s*써|무게를?\s*두"
)

# 강조 표현과 함께 나오는 낱말 → personal_fit.PRIORITIES 키. 앞쪽일수록 문장에서
# 먼저 매칭을 시도하지만, 실제로 고르는 순서는 문장에 나온 순서를 따른다(아래
# _extract_priorities).
_PRIORITY_KEYWORDS: list[tuple[str, str]] = [
    ("유동인구", "high_footfall"),
    ("유동 인구", "high_footfall"),
    ("통행량", "high_footfall"),
    ("매출", "high_sales"),
    ("경쟁", "low_competition"),
    ("배후 주거인구", "strong_residential"),
    ("주거인구", "strong_residential"),
    ("거주인구", "strong_residential"),
    ("직장인", "strong_workers"),
    ("회사원", "strong_workers"),
    ("20대", "youth"),
    ("이십대", "youth"),
    ("10대", "teen"),
    ("청소년", "teen"),
    ("십대", "teen"),
    ("30대", "age_30s"),
    ("삼십대", "age_30s"),
    ("40대", "age_40s"),
    ("사십대", "age_40s"),
    ("50대", "age_50plus"),
    ("오십대", "age_50plus"),
    ("시니어", "age_50plus"),
    ("여성", "female"),
    ("여자", "female"),
    ("남성", "male"),
    ("남자", "male"),
    ("외국인", "foreign"),
    ("관광객", "foreign"),
    ("배달", "delivery"),
    ("안정적", "stability"),
    ("장기 운영", "stability"),
    ("임대료", "low_rent"),
    ("월세", "low_rent"),
    ("주차", "parking"),
    ("성장 가능성", "growth"),
    ("성장성", "growth"),
    ("뜨는 동네", "growth"),
]


def _extract_priorities(query: str) -> list[str]:
    """"유동인구를 더 중요하게 봐줘"처럼 강조된 조건을 personal_fit 우선순위로 뽑는다.

    강조 표현(_PRIORITY_EMPHASIS_RE)이 없으면 아무 것도 뽑지 않는다 — 그냥
    지나가며 언급한 낱말까지 재가중으로 읽으면 안 된다. 문장에 나온 순서대로
    최대 personal_fit.MAX_PRIORITIES개까지 담는다(그 서비스가 어차피 넘는
    개수는 잘라 쓰지만, 어떤 것을 먼저 볼지는 문장 순서가 더 자연스럽다).
    """
    if not _PRIORITY_EMPHASIS_RE.search(query):
        return []
    hits: list[tuple[int, str]] = []
    seen: set[str] = set()
    for keyword, key in _PRIORITY_KEYWORDS:
        if key in seen:
            continue
        idx = query.find(keyword)
        if idx == -1:
            continue
        hits.append((idx, key))
        seen.add(key)
    hits.sort(key=lambda h: h[0])
    return [key for _, key in hits[:MAX_PRIORITIES]]


# ─────────────────────────────── 도구 목록 ───────────────────────────────


@dataclass(frozen=True)
class _ToolSpec:
    """도구 하나와, 그 도구를 부르려면 문장에서 무엇이 나와야 하는지.

    라우팅을 '필요한 값이 다 있는가'로 결정하면 특정 문구에 기대지 않아도 되고,
    값이 모자랄 때 무엇을 더 물어야 하는지도 자동으로 나온다.
    """

    key: str
    label: str  # 실행 트레이스에 보일 이름
    required: tuple[str, ...] = ()
    missing_hint: str = ""
    # 이 도구에 허용하는 시간(초, 도구 선택 이후 기준).
    #
    # 아무 조건 없이 "여기 어때"라고 묻는 기본 경로(site_score)는 5초 하드룰을
    # 그대로 지킨다 — 도구를 부르지 않으므로 여기 값과 무관하다.
    #
    # 반면 조건이 붙은 질문("월세 300에 계약해도 될까", "두 곳 중 어디")은
    # 도구를 못 돌리면 **다른 질문에 답하게 된다**. 라이브에서 이 도구들이
    # 5초 경계에 걸려 반은 성공하고 반은 버려졌고, 버려진 쪽은 질문과 무관한
    # 입지 점수만 나갔다. 틀린 답을 빨리 주는 것보다 맞는 답을 늦게 주는 편이
    # 낫다고 보고, 도구가 붙는 질문에만 예산을 더 준다(프론트 요청 타임아웃은
    # 330초라 UI가 깨지지 않고, 서술은 여전히 2단계로 뒤따라온다).
    budget_s: float = 8.0


_TOOLS: dict[str, _ToolSpec] = {
    # 기본 경로는 도구를 부르지 않는다(예산 값은 쓰이지 않음). 5초 하드룰 그대로.
    "site_score": _ToolSpec("site_score", "종합 점수 산출", budget_s=5.0),
    # 값 판정 셋(임차·매입·수익성)은 예산을 decide와 같게 준다. 예산은 요청
    # 시작부터 재는데 LLM 파싱+기본 분석이 먼저 6~9초를 먹으므로, 기본 8초로는
    # 정작 질문의 본론인 판정이 "계산이 오래 걸려" 버려지고 입지 점수만 나갔다
    # (2026-08-09 실측 3연속). 월세를 말한 사람에게 월세 얘기가 빠진 답을 빨리
    # 주는 것보다, 몇 초 늦어도 물은 것에 답하는 게 맞다.
    "lease_verdict": _ToolSpec(
        "lease_verdict",
        "임차 계약 판정",
        ("monthly_rent_man",),
        "월세가 얼마인지 알려 주시면 계약 판단까지 해 드려요.",
        budget_s=15.0,
    ),
    "purchase_verdict": _ToolSpec(
        "purchase_verdict",
        "매입 판정",
        ("price_manwon", "area_sqm"),
        "매입가와 전용면적(평 또는 ㎡)을 함께 알려 주시면 매입 판단까지 해 드려요.",
        budget_s=15.0,
    ),
    "profitability": _ToolSpec(
        "profitability",
        "투자 수익성 계산",
        ("price_manwon", "monthly_rent_man"),
        "매입가와 받을 월세를 함께 알려 주시면 수익률까지 계산해 드려요.",
        budget_s=15.0,
    ),
    "decide": _ToolSpec(
        "decide",
        "후보지 비교",
        ("candidates",),
        "비교할 곳을 두 군데 이상 알려 주세요.",
        budget_s=15.0,
    ),
    "area_search": _ToolSpec("area_search", "동네 안 후보 자리 탐색", budget_s=25.0),
    # 성장 신호는 분기 매출·점포 추이라 지점만 있으면 낼 수 있다 — 별도 입력이
    # 필요 없어서 required가 비어 있다.
    "trends": _ToolSpec("trends", "성장 신호·업종 추이", budget_s=8.0),
    # 잠식·자기잠식도 지점(+업종)만 있으면 계산된다. 브랜드가 뚜렷하면 함께
    # 넘기지만(_Intent.brand), 없어도 동일업종 전반의 잠식 위험은 낼 수 있다.
    "cannibalization": _ToolSpec(
        "cannibalization", "잠식·자기잠식 위험 분석", budget_s=10.0
    ),
    # 시세·생활인구도 지점만 있으면 된다 — 업종·수치 입력이 필요 없다. 둘 다
    # 로컬 PostGIS 조회(RTMS·서울 생활인구)라 다른 도구보다 가볍다.
    "area_insights": _ToolSpec("area_insights", "시세·생활인구 조회"),
    # "이 자리랑 비슷한 동네 어디야?" — 구조(직장인·점포밀도·주말성격·프랜차이즈
    # 비율)가 닮은 상권을 찾아 그곳의 성과를 비교한다. 로컬 테이블 전량 조회
    # 한 번이라 가볍다.
    # 계산 자체는 0.4초(전 상권 로드 포함)지만 예산은 요청 시작부터 재므로,
    # 기본 분석(파싱+site_score)이 먼저 먹는 시간을 감안해야 한다 — 기본 8초로는
    # analogs 차례에 남는 시간이 0.2초 미만이라 항상 버려졌다(실측 2026-08-08).
    "analogs": _ToolSpec("analogs", "비슷한 상권 찾기", budget_s=15.0),
    # 권역 Before/After도 지점(+업종)만 있으면 계산된다 — 새 매장이 들어가면
    # 기존 브랜드들의 배달권역이 겹침·공백·흡수로 어떻게 바뀌는지 본다. 배달권역은
    # 원반(disc) 근사라 도로망 전체를 다시 불러오지는 않지만 권역 폴리곤을 여러
    # 개 만들므로 cannibalization보다 예산을 조금 더 준다.
    "territory": _ToolSpec("territory", "권역 Before/After 분석", budget_s=12.0),
    # 경쟁 구조·모델 적합도도 지점(+업종)만 있으면 계산된다. 다만 내부적으로
    # 도보 15분 권역 그래프를 다시 불러오고 site_score까지 한 번 더 돌려 모델
    # 적합도를 내므로(real_competition.py의 include_model_fit 기본값 True)
    # territory·cannibalization보다 무겁다 — decide급 예산을 준다. 업종이 경쟁규칙
    # 프로필에 없으면(운동시설·숙박 등) 라우터가 400을 주는데, 이건 _run_tool
    # 호출부의 공용 예외 처리로 site_score 폴백된다(테스트로 검증).
    "real_competition": _ToolSpec(
        "real_competition", "경쟁 구조·모델 적합도 분석", budget_s=15.0
    ),
    # 실거래 비교도 지점만 있으면 된다 — 업종·수치 입력이 필요 없다. 매입가·평수·층이
    # 문장에서 잡혔으면 함께 넘겨 그 값이 주변 실거래 중 어디쯤인지도 계산하지만,
    # 없어도 주변 실거래 밴드(P25/P50/P75) 자체는 낼 수 있다. area_insights와 같은
    # 로컬 PostGIS(real_estate_trades) 조회라 가볍다.
    "property_market": _ToolSpec("property_market", "실거래 비교"),
    # "도보 10분 안에 몇 명 살아" 류 도달권역 질문도 지점만 있으면 계산된다 —
    # 시간·이동수단이 문장에 없으면 도보 5/10/15분 기본 밴드를 낸다(_Intent.
    # reach_minutes/reach_mode). 도보·자전거는 도로망을 다시 불러와(Dijkstra)
    # 실측 도달권을 만들므로 territory·real_competition급 예산을 준다 — 차·오토
    # 바이는 dashboard.isochrone 내부에서 직선거리 근사로 가볍게 처리된다.
    "isochrone": _ToolSpec("isochrone", "도달권역(등시선) 분석", budget_s=12.0),
    "diagnose": _ToolSpec(
        "diagnose",
        "매출 원인 진단",
        ("current_monthly_sales_man",),
        "현재 월매출을 알려 주시면 왜 낮은지 진단해 드려요.",
    ),
    # 학원 과목 클러스터도 지점만으로는 부족하다 — academy_cluster_analysis의
    # target_model이 필수값이라(없으면 서비스가 ValueError) 과목이 문장에서
    # 뽑혀야만 부른다. real_competition과 마찬가지로 내부에서 site_score를 한 번
    # 더 돌려 모델 적합도까지 내므로 같은 예산을 준다.
    "academy_cluster": _ToolSpec(
        "academy_cluster",
        "학원 과목 클러스터 분석",
        ("target_model",),
        "학원 어떤 과목(수학·영어·국어·과학·입시·예체능·코딩·성인)인지 알려 "
        "주시면 학원가 클러스터 분석까지 해 드려요.",
        budget_s=15.0,
    ),
    # 임차 타당성 상세 분석은 lease_verdict와 같은 필수값(월세)만 있으면 된다 —
    # 인건비·객단가 등 나머지는 LeaseFeasibilityRequest의 기본값을 그대로 쓴다.
    # site_score·잠식·매출 벤치마크를 함께 돌려 lease_verdict보다 무겁다.
    "lease_feasibility": _ToolSpec(
        "lease_feasibility",
        "임차 타당성 상세 분석",
        ("monthly_rent_man",),
        "월세가 얼마인지 알려 주시면 임차 타당성 분석까지 해 드려요.",
        budget_s=15.0,
    ),
    # 프랜차이즈 가맹정보(창업비용·폐업률)는 지점과 무관하다 — franchise_brands
    # 테이블(공정위 가맹정보공개서 스냅샷)을 브랜드명으로만 조회한다. 브랜드가
    # 문장에서 뚜렷이 안 뽑히면 부르지 않는다(franchise_brand가 필수) — 아무
    # 프랜차이즈나 골라 잘못된 창업비용을 보여주면 안 된다. area_insights와 같은
    # 로컬 조회라 가볍다.
    "franchise_brand_detail": _ToolSpec(
        "franchise_brand_detail",
        "가맹정보 창업비용·폐업률",
        ("franchise_brand",),
        "어느 프랜차이즈 브랜드인지 알려 주시면 창업비용·폐업률까지 찾아 드려요.",
    ),
    # 신설도로 시뮬레이션(/simulate)은 새 도로의 좌표(폴리라인)나 막을 도로의 OSM
    # ID가 있어야 계산할 수 있는데, 그건 지도에서 직접 그리거나 선택해야 나오는
    # 값이라 채팅 문장만으로는 절대 뽑을 수 없다(지어내면 그리지도 않은 도로를
    # 분석한 척하게 된다). 그래서 이 required는 의도적으로 영원히 채워지지
    # 않는다 — _extract_intent가 "virtual_road"를 채우는 경로 자체가 없다. 질문은
    # 이 도구로 인식하되, missing_hint로 지도 화면을 안내하고 site_score로
    # 폴백한다(위 franchise_brand_detail·territory와 같은 "찾지 못했다" 안내
    # 경로를 그대로 탄다).
    "simulate": _ToolSpec(
        "simulate",
        "신설도로 시뮬레이션",
        ("virtual_road",),
        "신설도로 시뮬레이션은 채팅으로는 계산할 수 없어요 — 시뮬레이션 화면 "
        "지도에서 새로 낼 도로를 직접 그리거나 막을 도로를 선택해 주세요.",
    ),
    # 업종별 체크리스트(backend/industry-profiles/**/*.json)는 이 지점의 점수가
    # 아니라 "이 업종을 분석할 때 무엇을 보는지"를 묻는 질문에 답한다("카페
    # 창업할 때 뭘 챙겨야 해"). site_score.industry_kpis는 이미 같은 프로필로
    # 실측값을 계산해 주지만, 그건 가리키는 게 다르다 — 값이 아니라 그 업종의
    # 체크포인트 자체를 원하는 질문이라 따로 둔다. required는 업종이 15개 프로필
    # 중 하나로 실제 해석될 때만 채워진다(_tool_ready의 industry_profile_id 계산) —
    # 프로필이 없는 업종("일반")에서는 지어낼 체크리스트가 없어 부르지 않는다.
    # 땅값 도구 셋 — **범위가 셋이라 도구도 셋이다**(필지 하나 · 한 동 · 전역).
    # 필요한 값은 셋 다 "어디"뿐이고 그건 라우터가 이미 해소하므로 required가
    # 비어 있다. 장소가 없으면 도구 선택 전에 _NEED_PLACE_DETAIL로 되묻는다.
    #
    # 셋 다 미리 계산된 표를 읽는다(배치가 채운 판정 + 공시지가 + 토지특성) —
    # 요청 시점에 계산하지 않으므로 가볍다. 그래서 예산은 기본값을 쓴다.
    "land_parcel": _ToolSpec("land_parcel", "필지 하나 땅값 판정"),
    "land_dong": _ToolSpec("land_dong", "이 동네 값싼 필지 찾기"),
    "land_ranking": _ToolSpec("land_ranking", "서울 전역 동 순위"),
    "industry_profile": _ToolSpec(
        "industry_profile",
        "업종별 체크리스트",
        ("industry_profile_id",),
        "어떤 업종인지 알려 주시면 그 업종 체크리스트를 보여 드려요(예: 카페·"
        "미용실·병원 등).",
    ),
}
_TOOL_KEYS = tuple(_TOOLS)

# 후보 자리를 찾아 달라는 요구. "5곳"처럼 개수가 붙으면 그것만으로 충분하지만,
# 개수 없이 "자리 추천해줘"라고만 하는 말도 흔하다.
_AREA_SEARCH_RE = re.compile(r"추천|찾아|골라|어디에|후보|자리\s*몇")

# 뜨는지 지는지를 묻는 말. 값으로 갈리지 않는 의도라 area_search와 같은 방식으로
# 표현을 본다. 다만 "뜨는 자리 추천해줘"처럼 자리를 찾아 달라는 쪽이 섞이면 그건
# 탐색이 맞으므로, 규칙에서 area_search를 먼저 본다.
_TRENDS_RE = re.compile(r"뜨는|뜨고|성장|추이|트렌드|전망|늘고\s*있|줄고\s*있|지는\s*(동네|상권)")

# 잠식·자기잠식을 묻는 말. "2호점 내면 1호점 매출 뺏겨?"처럼 값으로 갈리지 않는
# 의도라 trends와 같은 방식으로 표현을 본다. "\d+호점"은 매장 확장 얘기의 강한
# 신호라 브랜드명이 안 잡혀도 이 규칙만으로 도구를 고를 수 있다.
_CANNIBALIZATION_RE = re.compile(r"잠식|뺏[기겨길]|나눠\s*먹|매출\s*나눠|\d+\s*호점")

# 시세·생활인구를 묻는 말. "아파트"만으로는 흔한 지명 서술("아파트 상가")과
# 안 갈리므로, 시세·가격류 낱말이나 "생활인구"·"유동인구" 같은 전용 용어가
# 붙었을 때만 이 도구로 본다.
# "비슷한 동네/유사 상권"은 다른 신호와 안 겹친다 — 성과 비교가 목적이라
# trends(추이)와도 구별된다.
_ANALOGS_RE = re.compile(r"비슷한\s*(동네|상권|곳|데)|유사\s*상권|어디랑\s*비슷|닮은\s*(동네|상권)")

_AREA_INSIGHTS_RE = re.compile(
    r"시세|집값|생활\s*인구|유동\s*인구|아파트\s*(가격|시세|값)"
)

# 권역 Before/After를 묻는 말. "여기 들어가면 누구 상권을 뺏어?"처럼 매출
# 잠식(cannibalization)이 아니라 배달권역·상권 경계가 어떻게 바뀌는지를 묻는
# 의도라 위 잠식 규칙과 구분한다. "권역"이 가장 뚜렷한 전용 신호다.
_TERRITORY_RE = re.compile(
    r"권역|배달\s*(범위|반경)|상권\s*(을|이)?\s*(뺏|나눠|가져가|차지)"
)

# 경쟁 구조·모델 적합도를 묻는 말. "어떤 형태로 하면 맞아?"처럼 얼마나
# 잠식당하는지(cannibalization)가 아니라 이 자리에 어떤 사업 모델·컨셉이
# 맞는지를 묻는 의도라 위 잠식 규칙과 구분한다. "\d+호점"이 없는 일반적인
# 경쟁 문의도 여기로 오도록 "경쟁"을 포함하되, cannibalization 규칙이 먼저
# 걸리는 표현(잠식·뺏김·호점)은 그쪽이 우선한다(아래 _rule_tool의 순서).
_REAL_COMPETITION_RE = re.compile(
    r"경쟁\s*(구조|강도|치열)|모델\s*적합|사업\s*모델|"
    r"(어떤|무슨)\s*(형태|모델|컨셉|콘셉트)|"
    r"(형태|모델|컨셉|콘셉트)(가|이)?\s*맞"
)

# 실거래 비교를 묻는 말. "이 가격 비싼 거야?"처럼 매매가·시세가 주변 실거래 대비
# 어느 수준인지 묻는 의도라, 아파트 시세·생활인구(area_insights)와는 다르다 —
# area_insights의 "시세"는 배후 소비력을 보는 아파트 얘기지 이 물건 자체의 값을
# 묻는 게 아니다. "아파트"가 없는 "시세"·"실거래"·"비싸/비싼"은 상가·오피스텔
# 실거래 비교로 보고, area_insights 규칙보다 먼저 검사해 "시세" 한 낱말이
# area_insights로 먼저 흡수되지 않게 한다.
_PROPERTY_MARKET_RE = re.compile(
    r"실거래|적정가|매매가|매물\s*시세|(상가|오피스텔)\s*시세|비싸|비싼"
)

# 임차 타당성 상세 분석을 묻는 말. 월세만 던지고 "계약해도 될까"라고 물으면
# 여전히 lease_verdict(간단 판정)로 간다 — "타당성"·"손익분기"처럼 인건비·객단가
# 까지 딸린 상세 분석을 뚜렷이 요구할 때만 이 도구로 갈라진다.
_LEASE_FEASIBILITY_RE = re.compile(r"타당성|손익분기|임차\s*타당")

# 업종별 체크리스트·핵심지표(industry_profiles.get_adaptive_profile)를 묻는
# 말. "이 업종 체크리스트 뭐야"·"핵심지표 알려줘"처럼 수치 계산이 아니라 그
# 업종의 분석 기준(KPI·체크리스트) 자체를 물을 때만 이 도구로 본다 — 업종이
# "general"이면(_tool_ready가 검사) 전용 프로필이 없어 site_score로 폴백한다.
_INDUSTRY_PROFILE_RE = re.compile(
    r"체크리스트|체크\s*사항|핵심\s*지표|주요\s*지표|kpi|업종\s*(프로필|가이드)",
    re.IGNORECASE,
)

# 신설도로 시뮬레이션(/simulate)을 묻는 말. "여기 길 뚫리면 어떻게 돼?"처럼 도로를
# 새로 내거나 막았을 때 공간구조 지표(통합도·선택도)가 어떻게 바뀌는지 묻는
# 의도다. "길"·"도로" 뒤 8자 안에 그 변화를 가리키는 동사가 있으면 매칭한다.
_SIMULATE_RE = re.compile(
    r"(길|도로)[^,.?!]{0,8}(뚫|신설|막으면|막아|차단|이어지면|연결되면|생기면)|"
    r"신설\s*도로|가상\s*도로|도로\s*시뮬레이션"
)

# 업종별 체크리스트를 묻는 말. "카페 창업할 때 뭘 챙겨야 해"처럼 이 자리의
# 점수가 아니라 그 업종을 분석할 때 일반적으로 무엇을 보는지(체크포인트) 자체를
# 묻는 의도다. 값 기반 도구(계약·매입·수익성 등)가 이미 위에서 먼저 걸러지므로
# 여기 걸리는 문장은 순수하게 "무엇을 확인해야 하는지"를 묻는 경우로 좁혀진다.
_INDUSTRY_PROFILE_RE = re.compile(
    r"체크리스트|뭘\s*(챙기|챙겨|준비|봐야|확인)|무엇을\s*(챙기|준비|확인|봐야)|"
    r"뭐(를|)\s*(챙기|준비|확인)|창업\s*(전에|준비)|주의(할|사항)|"
    r"어떤\s*(점|걸|지표)(를|을)?\s*(봐야|확인)"
)


# 한 질문에 한꺼번에 엮어 돌리는 도구 수의 상한. 신호가 문장에 아무리 많이
# 섞여도 하드 5초 룰 근방의 예산을 지키려면 무한정 붙일 수 없다 — 넘친 나머지는
# 조용히 버리지 않고 notice로 무엇을 못 돌렸는지 밝힌다(agent_ask 참고).
_MAX_CHAINED_TOOLS = 3

# 순수 입지 질문("이 자리 어때?")에 곁들이는 도구 — 사용자가 직접 물은 게
# 아니라 분석 깊이를 위해 자동으로 붙인 것이라, 이것만 성공했을 땐 주 도구
# (tool)를 여전히 site_score로 본다(agent_ask의 tool 결정부 참고). 사용자가
# "경쟁 구조 봐줘"·"뜨는 자리야?"처럼 직접 물으면 _rule_tools가 이 경로가
# 아니라 신호 매칭으로 골라내므로 그때는 이 필터를 타지 않는다.
_DEPTH_ONLY_TOOLS = frozenset({"trends", "real_competition"})


# ── 땅값 질문의 범위 ─────────────────────────────────────────────────────
#
# 세 도구를 가르는 것은 **질문이 무엇을 가리키는가**다: 필지 하나인지, 한 동인지,
# 서울 전역인지. 값(월세·매입가)이 아니라 범위가 슬롯이므로 정규식으로 읽는다.
#
# ⚠️ 이 도구들은 상가 입지 분석과 대상이 다르다("이 자리 카페 어때"가 아니라
# "이 땅 싸냐"). 땅·토지·필지·공시지가 낱말이 없으면 절대 걸리지 않게 한다 —
# 걸리면 카페 입지를 물은 사람에게 공시지가 순위가 나간다.
# "저평가"를 함께 넣는다. "서울에서 가장 저평가된 동네"처럼 땅 낱말이 없는데도
# 땅값 질문인 경우가 있고(테스트로 잡았다), 이 제품에서 '저평가'는 땅값 말고
# 쓰이는 데가 없다.
_LAND_SUBJECT_RE = re.compile(r"땅|토지|필지|공시지가|지가|저평가")
# 싸다/저평가 계열. "저평가"는 지명 꼬리 검사에 걸리는 낱말이라(_PLACE_SUFFIX_RE
# 주석 참고) 여기서 명시적으로 다룬다.
_LAND_CHEAP_RE = re.compile(r"싼|싸[다냐게]|저평가|저렴|낮은|헐값")
# 필지 하나를 가리키는 표현. 지번(12-3)이 붙었으면 그것만으로도 필지 질문이다.
_LAND_PARCEL_RE = re.compile(r"이 ?(?:땅|토지|필지)|여기 ?(?:땅|토지)|그 ?(?:땅|토지|필지)|\d+-\d+")
# 전역 순위. "서울에서 제일 싼 데"처럼 범위가 시 전체인 질문.
_LAND_RANKING_RE = re.compile(r"(?:서울|전역|전체|어느 ?동네?|어디).{0,12}(?:제일|가장|최고|top|상위|순위)|(?:제일|가장).{0,6}(?:싼|저평가)")


def _rule_tools(
    query: str,
    params: dict[str, float],
    place_count: int,
    business_type: str = "general",
    target_model: str | None = None,
    franchise_brand: str | None = None,
) -> list[str]:
    """LLM 없이 도구(들)를 고른다 — **문장에서 뽑힌 값들**이 결정한다.

    한 문장이 값 판정과 구조 분석을 동시에 요구할 수 있다("월세 300에 계약해도
    되고 경쟁 구조도 봐줘"). 그래서 첫 신호에서 멈추지 않고, 서로 겹치지 않는
    신호는 전부 모아 순서대로(가장 확실한 것 먼저) 돌려준다 — 맨 앞이 답의
    중심이 될 주 도구다.

    다만 **같은 문구를 두고 경합하는 한 쌍**은 여전히 하나만 고른다(elif로
    표시): '시세'라는 낱말 하나가 property_market과 area_insights 둘 다의
    트리거라 같이 넣으면 같은 신호를 중복 계산하는 꼴이고, academy_cluster는
    real_competition이 학원 업종에서 내부적으로 위임하는 같은 분석이라 둘 다
    돌리면 중복이며, area_search·trends는 "자리 찾기"가 섞이면 탐색이 맞다는
    기존 제품 판단을 그대로 따른다(추이 안내로 답을 대신하지 않는다).
    """
    if place_count >= 2:
        # 비교 질의는 decide 하나로 완결된다 — 다른 도구는 지점 하나만 받으므로
        # 여러 후보를 동시에 넘길 수 없다.
        return ["decide"]

    tools: list[str] = []

    # 브랜드명이 창업비용·폐업률류 낱말과 함께 뚜렷이 뽑혔으면 그게 가장 구체적인
    # 신호다 — 지점과 무관해 다른 도구와 자유롭게 엮인다.
    if franchise_brand:
        tools.append("franchise_brand_detail")
    if "current_monthly_sales_man" in params:
        tools.append("diagnose")
    if "price_manwon" in params:
        tools.append("profitability" if "monthly_rent_man" in params else "purchase_verdict")
    elif "monthly_rent_man" in params:
        tools.append(
            "lease_feasibility" if _LEASE_FEASIBILITY_RE.search(query) else "lease_verdict"
        )

    # 땅값 질문 — 범위 하나만 고른다(셋이 같은 대상을 다른 크기로 보는 것이라
    # 둘 이상 돌리면 같은 답을 크기만 바꿔 두 번 낸다). 넓은 것부터 확인한다:
    # "서울에서 제일 싼 땅"은 '땅'이라는 낱말 때문에 필지 질문으로도 읽힌다.
    if _LAND_SUBJECT_RE.search(query):
        if _LAND_RANKING_RE.search(query):
            tools.append("land_ranking")
        elif _LAND_PARCEL_RE.search(query):
            tools.append("land_parcel")
        elif _LAND_CHEAP_RE.search(query):
            tools.append("land_dong")
        else:
            # "이 땅 공시지가 얼마야"처럼 싸다·순위 표현이 없는 질문. 가리키는
            # 것은 필지 하나다.
            tools.append("land_parcel")

    # 체크리스트·핵심지표를 물으면 계산이 아니라 그 업종의 분석 기준 자체가
    # 궁금한 것이다. 값 도구와 신호가 안 겹치므로 자유롭게 엮인다 — "월세
    # 300에 체크리스트도 같이"면 lease_verdict가 주 도구, 이건 곁들이로 돈다.
    if _INDUSTRY_PROFILE_RE.search(query):
        tools.append("industry_profile")

    # 학원 업종 + 과목이 둘 다 문장에서 뽑혔을 때만 academy_cluster로 간다
    # ("학원"만으로는 어떤 과목 기준인지 알 수 없어 서비스가 거부한다). 그 경우
    # real_competition은 같은 분석을 내부에서 한 번 더 도는 것이라 건너뛴다.
    academy_selected = business_type == "academy" and bool(target_model)
    if academy_selected:
        tools.append("academy_cluster")
    elif _REAL_COMPETITION_RE.search(query):
        tools.append("real_competition")

    # 이동수단+시간과 인구·거주 낱말이 함께 있어야 도달권역 질문으로 본다.
    # "도보 10분 거리"만으로는 위치 설명일 뿐이다.
    if _ISOCHRONE_TIME_RE.search(query) and _ISOCHRONE_POPULATION_RE.search(query):
        tools.append("isochrone")
    if _CANNIBALIZATION_RE.search(query):
        tools.append("cannibalization")
    if _TERRITORY_RE.search(query):
        tools.append("territory")
    # '시세' 한 낱말이 둘의 트리거를 동시에 건드린다 — 상가·오피스텔 실거래처럼
    # 더 구체적인 신호가 있으면 그쪽을 쓰고, 없을 때만 아파트·생활인구 쪽으로 본다.
    if _PROPERTY_MARKET_RE.search(query):
        tools.append("property_market")
    elif _AREA_INSIGHTS_RE.search(query):
        tools.append("area_insights")
    if _ANALOGS_RE.search(query):
        tools.append("analogs")
    if _SIMULATE_RE.search(query):
        tools.append("simulate")
    # 자리를 찾아 달라는 쪽이 섞이면 탐색이 맞다 — 추이를 보여줘도 답이 아니다.
    # "비슷한 동네 어디야?"의 '동네 어디'가 자리 탐색으로도 읽힌다 — 유사 상권
    # 질문은 자리를 찾아 달라는 게 아니라 비교해 달라는 것이므로 analogs가
    # 이긴다(문서화된 경합 쌍과 같은 규칙).
    if "max_candidates" in params or (
        _AREA_SEARCH_RE.search(query) and not _ANALOGS_RE.search(query)
    ):
        tools.append("area_search")
    elif _TRENDS_RE.search(query):
        tools.append("trends")

    if not tools:
        # 다른 신호가 하나도 안 잡힌 순수 입지 질문("이 자리 어때?")도 점수 하나만
        # 던지면 그 점수가 오르는 흐름인지 알 길이 없다 — trends(성장 신호)는
        # 지점·업종만 있으면 항상 낼 수 있어 곁들인다. real_competition(경쟁
        # 구조)은 지원 업종에서만 계산되므로 여기서는 붙이지 않는다 — 이 함수는
        # 문장만 보고 업종 코드 체계를 모른다. agent_ask가 코드로 vertical을
        # 확인한 뒤(_real_competition_supported) 지원 업종일 때만 덧붙인다.
        return ["site_score", "trends"]

    # 순서를 지키며 중복만 제거한다(예: 프리셋·규칙이 같은 도구를 두 번 넣을 일은
    # 없지만, 방어적으로 남겨 둔다).
    seen: set[str] = set()
    ordered: list[str] = []
    for t in tools:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def _rule_tool(
    query: str,
    params: dict[str, float],
    place_count: int,
    business_type: str = "general",
    target_model: str | None = None,
    franchise_brand: str | None = None,
) -> str:
    """LLM 없이 도구를 하나 고른다 — 여럿이 뽑히면 그중 가장 확실한 것(맨 앞).

    단일 도구만 필요한 호출부(및 기존 테스트)를 위한 얇은 래퍼다. 실제 라우팅은
    `_rule_tools`가 한다.
    """
    return _rule_tools(
        query, params, place_count,
        business_type=business_type, target_model=target_model,
        franchise_brand=franchise_brand,
    )[0]


# 비교 질의("A랑 B 중 어디가 나아?")를 한 지점 분석으로 뭉개면, 답변은 두 곳을
# 한 곳인 양 서술하고 사용자가 물은 비교에는 답하지 않는다. 엔진은 한 번에 한
# 지점만 계산하므로, 좁혀서 봤다는 사실을 숨기지 않고 알린다.
_COMPARISON_PATTERNS = (
    re.compile(r"둘\s*중|두\s*(곳|군데|지역|업종)\s*중|중에\s*(어디|어느|뭐|무엇)"),
    re.compile(r"어(디|느)\s*(쪽|곳|지역)?\s*가?\s*(더|나아|나은|낫|좋)"),
    re.compile(r"(?<![0-9A-Za-z])vs(?![0-9A-Za-z])", re.IGNORECASE),
    re.compile(r"비교"),
    re.compile(r"(랑|이랑|하고|와|과)\s*\S+\s*중"),
)


def _comparison_notice(query: str, place: str) -> str:
    """비교를 물었으면 실제로 무엇만 분석했는지 한 줄로 밝힌다."""
    if not any(p.search(query) for p in _COMPARISON_PATTERNS):
        return ""
    return (
        f"한 번에 한 곳·한 업종만 분석할 수 있어요. 이번 결과는 '{place}' 하나를 "
        "본 것이니, 비교하려면 다른 쪽도 따로 물어봐 주세요."
    )


def _build_steps(site: SiteScoreResponse) -> list[AgentStep]:
    """실제로 site_score가 계산한 것만 트레이스로 노출한다(거짓 단계 금지)."""
    steps = [
        AgentStep(key="geocode", label="주소 좌표 변환"),
        AgentStep(key="roads", label="도로망 구조 분석"),
    ]
    if site.demand is not None:
        steps.append(AgentStep(key="demand", label="배후 인구·수요 조회"))
    if site.transit is not None:
        steps.append(AgentStep(key="transit", label="대중교통 접근성"))
    if site.competition is not None:
        steps.append(AgentStep(key="competition", label="경쟁 업종 탐색"))
    steps.append(AgentStep(key="score", label="종합 점수 산출"))
    # 우선순위가 실제로 site_score에 실려 personal_fit이 계산됐을 때만 추가한다
    # (getattr 기본값 None — 이 필드가 아직 없는 옛 응답 객체도 그대로 받는다).
    if getattr(site, "personal_fit", None) is not None:
        steps.append(AgentStep(key="personal_fit", label=_TOOLS["personal_fit"].label))
    return steps


# ─────────────────────────── 도구 실행 ───────────────────────────
#
# 여기서 부르는 것들은 전부 이미 있는 라우터 함수다. 이 파일은 호출만 하고
# 그쪽 파일을 고치지 않는다.

_PYEONG_TO_SQM = 3.305785


@dataclass
class _Point:
    lat: float
    lng: float
    label: str
    # 이 좌표가 **행정동 대표점**(점포 무게중심)인가. 지점 좌표와 구분해야 하는
    # 이유: 사용자가 "신촌 샐러디집"처럼 점포를 대면 그 자리가 중심이어야 하는데,
    # 행정동으로 낮춰 잡히면 중심이 수백 m 벗어난 채 답이 나간다. 서술이 무엇을
    # 중심으로 계산했는지 밝힐 수 있어야 사용자가 어긋남을 알아챈다.
    from_dong: bool = False


async def _resolve_one_point(place: str) -> _Point | None:
    """지명 한 덩어리 → 좌표. 동네 이름이면 상권 중심, 아니면 지오코딩."""
    place = (place or "").strip()
    if not place:
        return None
    dong = await run_in_threadpool(resolve_unique_dong, place)
    if dong is not None:
        return _Point(lat=dong.lat, lng=dong.lng, label=dong.name, from_dong=True)
    try:
        lat, lng = await geocode_address(place)
    except (GeocodingError, HTTPException, Exception):  # noqa: B014 - 폴백이 목적
        return None
    return _Point(lat=lat, lng=lng, label=place)


async def _resolve_point(place: str) -> _Point | None:
    """지명 → 좌표. 실패하면 뒤 낱말을 하나씩 떼며 다시 찾는다.

    추출기가 문장의 잔여물을 지명에 남기는 일을 완전히 막을 수는 없다(한국어
    어미는 목록으로 다 못 적는다). 그럴 때 "강남역 만할까"처럼 앞은 맞고 뒤가
    쓰레기인 문자열이 남는데, 종전엔 통째로 실패해 "위치를 찾지 못했어요"가 났다.
    앞에서부터가 지명일 확률이 높으므로 뒤부터 떼며 최대 세 번 더 시도한다.
    (앞을 떼지는 않는다 — '서울 강남역'에서 '서울'을 떼면 다른 곳이 잡힌다.)
    """
    place = (place or "").strip()
    if not place:
        return None
    tokens = place.split()
    # 시도 횟수를 둘로 묶는다. 후보 하나마다 동네 조회 + 카카오 주소·키워드 검색이
    # 붙어서, 넉넉히 잡으면 실패하는 질의 하나가 카카오를 열 번 넘게 부르고 7초를
    # 쓴다(실측). 잔여물은 보통 한두 낱말이다.
    for cut in range(0, min(2, max(0, len(tokens) - 1)) + 1):
        candidate = " ".join(tokens[: len(tokens) - cut]) if cut else place
        point = await _resolve_one_point(candidate)
        if point is not None:
            return point
    return None


def _codes(intent: _Intent) -> dict[str, str | None]:
    """도구들이 공통으로 받는 업종 코드.

    프리셋(cafe·convenience…)일 때 코드를 비워 두면 decide·area_search가 업종을
    아예 모르게 된다 — 그 둘은 site_score와 달리 business_type 필드가 없어서
    프리셋을 받을 길이 없다. 실제로 "연남동이랑 신촌 중 카페" 비교에서 예상
    월매출이 양쪽 다 None으로 나왔다(매출 벤치마크가 업종 없이는 못 붙는다).
    프리셋도 같은 코드 체계로 풀어서 넘긴다.
    """
    up = intent.upjong or {}
    codes = {
        "inds_lcls_cd": up.get("inds_lcls_cd"),
        "inds_mcls_cd": up.get("inds_mcls_cd"),
        "inds_scls_cd": up.get("inds_scls_cd"),
    }
    if any(codes.values()):
        return codes
    preset = BUSINESS_TYPE_PRESETS.get(intent.business_type)
    if preset:
        return {
            "inds_lcls_cd": preset.get("inds_lcls_cd"),
            "inds_mcls_cd": preset.get("inds_mcls_cd"),
            "inds_scls_cd": preset.get("inds_scls_cd"),
        }
    return codes


def _area_sqm(params: dict[str, float]) -> float | None:
    if params.get("area_sqm"):
        return params["area_sqm"]
    if params.get("area_pyeong"):
        return params["area_pyeong"] * _PYEONG_TO_SQM
    return None


def _resolve_industry_profile_id(intent: _Intent) -> str | None:
    """이 의도가 industry-profiles/**/*.json 15종 중 무엇으로 잡히는지.

    site_score.py가 ProfileResolver로 같은 업종 코드를 프로필에 연결하는 것과
    똑같은 경로(코드)를 그대로 쓴다 — 여기서 다르게 판단하면 체크리스트가
    가리키는 업종과 site_score.industry_kpis가 실제로 계산한 업종이 어긋난다.
    프로필이 없는 업종("일반")은 None — 지어낼 체크리스트가 없다.
    """
    codes = _codes(intent)
    mcls = codes.get("inds_mcls_cd")
    return ProfileResolver.load().resolve(
        inds_lcls_cd=codes.get("inds_lcls_cd"),
        inds_mcls_cd=mcls,
        inds_scls_cd=codes.get("inds_scls_cd"),
        business_type=intent.business_type,
        archetype=archetype_for_mcls(mcls) if mcls else None,
    )


def _real_competition_supported(intent: _Intent) -> bool:
    """순수 입지 질문에 real_competition을 곁들일 수 있는 업종인지.

    이 도구는 competitor_rules_{vertical}.json이 있는 업종(카페·음식점·병의원·
    학원·베이커리·편의점·미용실 등)에서만 계산된다 — 그 밖의 업종엔 400을 준다
    (real_competition.py의 resolve_vertical). 값 신호가 하나도 없는 순수 입지
    질문마다 무조건 붙이면 지원 안 하는 업종에서는 매번 "대상이 아니다"는
    알림만 뜬다 — real_competition.py가 실제로 쓰는 것과 같은 코드 경로
    (resolve_vertical)로 미리 걸러 조용한 노이즈를 없앤다. 학원은 과목
    (target_model)까지 있어야 academy_cluster로 위임되므로 그것도 함께 본다.
    """
    codes = _codes(intent)
    mcls = codes.get("inds_mcls_cd")
    vertical = _resolve_competition_vertical(
        archetype=archetype_for_mcls(mcls) if mcls else None,
        inds_mcls_cd=mcls,
        inds_scls_cd=codes.get("inds_scls_cd"),
    )
    if vertical is None:
        return False
    if vertical == "academy":
        return bool(intent.target_model)
    return True


def _tool_ready(tool: str, intent: _Intent, points: list[_Point]) -> tuple[bool, str]:
    """그 도구를 부를 수 있는지와, 못 부르면 무엇이 모자란지."""
    spec = _TOOLS.get(tool)
    if spec is None:
        return False, ""
    have = dict(intent.params)
    if _area_sqm(intent.params) is not None:
        have["area_sqm"] = 1.0
    if len(points) >= 2:
        have["candidates"] = 1.0
    if intent.target_model:
        have["target_model"] = 1.0
    if intent.franchise_brand:
        have["franchise_brand"] = 1.0
    if "industry_profile_id" in spec.required and _resolve_industry_profile_id(intent):
        have["industry_profile_id"] = 1.0
    missing = [key for key in spec.required if key not in have]
    if missing:
        return False, spec.missing_hint
    return True, ""


def _merge_pending_context(intent: _Intent, context: AgentPendingContext) -> _Intent:
    """직전 턴에서 모자랐던 지점·업종을, 이번 문장에서 새로 뽑힌 값과 합친다.

    "신촌에 카페 계약해도 될까"(월세 없음) 다음에 "월세 300이요"만 오면 이번
    문장 혼자서는 지점·업종을 다시 알 수 없다 — pending에 남겨 둔 것과 합쳐야
    계약 판정을 낼 수 있다. 이번 문장에서 뭔가 새로 뽑혔으면 그쪽을 우선한다
    (사용자가 지점이나 업종을 정정했을 수 있으므로).
    """
    address = intent.address
    # "300이요"처럼 라벨 없이 숫자만 던지면, 지명이 없을 때 원문을 그대로
    # 돌려주는 규칙 추출기 폴백 때문에 그 숫자 자체가 지명 자리에 남는다
    # (_rule_extract_address). 숫자로 시작하는 잔여물은 지명이 아니라 값에 대한
    # 대답이므로 pending의 지점으로 되돌린다.
    if context.address and (not address or address[:1].isdigit()):
        address = context.address
    return replace(
        intent,
        address=address,
        business_type=(
            intent.business_type if intent.business_type != "general" else context.business_type
        ),
        upjong=intent.upjong or context.upjong,
        params={**context.params, **intent.params},
        brand=intent.brand or context.brand,
        property_type=(
            intent.property_type if intent.property_type != "commercial" else context.property_type
        ),
        target_model=intent.target_model or context.target_model,
        priorities=intent.priorities or context.priorities,
        reach_minutes=(
            intent.reach_minutes if intent.reach_minutes is not None else context.reach_minutes
        ),
        reach_mode=intent.reach_mode if intent.reach_mode != "walk" else context.reach_mode,
        franchise_brand=intent.franchise_brand or context.franchise_brand,
        # 이번 문장이 스스로 도구를 알아내지 못했으면(llm_tool 없음), 직전에
        # 되묻던 도구를 후보로 들고 간다 — 아래 agent_ask의 기존 llm_tool 우선
        # 처리 경로를 그대로 타면서, 값이 다 찼을 때만 실제로 그 도구로 간다.
        llm_tool=intent.llm_tool or context.tool,
    )


# 라벨 없이 툭 던진 숫자 하나를 받아들이는 것은 pending이 무엇을 묻는지 알 때만
# (_apply_bare_fallback) 안전하다 — 그렇지 않으면 "24시간" 같은 무관한 숫자까지
# 금액으로 읽는다. 대상은 전부 돈 단위 필수값이다.
_MONEY_PARAM_KEYS = {
    "monthly_rent_man", "price_manwon", "current_monthly_sales_man",
    "deposit_man", "key_money_man", "maintenance_man",
}


def _bare_query_money(query: str) -> float | None:
    """문장 전체에서 라벨 없는 금액 하나를 읽는다. 없으면 None."""
    stripped = re.sub(r"[,\s]", "", query or "")
    m = _MONEY_TOKEN_RE.search(stripped)
    if m is None:
        return None
    return _money_to_man(m.group())


def _apply_bare_fallback(
    intent: _Intent, context: AgentPendingContext, query: str
) -> _Intent:
    """직전 턴이 되묻던 값 하나를, 라벨 없이 툭 던진 대답에서 채운다.

    "월세가 얼마인지 알려 주세요"에 "300이요"라고만 답하면 라벨("월세")이 없어
    _extract_parameters가 이 숫자를 버린다. pending이 가리키는 값이 정확히
    하나 남았을 때만 라벨 없는 숫자·짧은 낱말도 그 값으로 받아들인다 — 모자란
    게 둘 이상이면 어느 것에 답한 말인지 알 수 없으므로 손대지 않는다.
    """
    spec = _TOOLS.get(context.tool)
    if spec is None:
        return intent
    have = dict(intent.params)
    if _area_sqm(intent.params) is not None:
        have["area_sqm"] = 1.0
    if intent.target_model:
        have["target_model"] = 1.0
    if intent.franchise_brand:
        have["franchise_brand"] = 1.0
    missing = [key for key in spec.required if key not in have]
    if len(missing) != 1:
        return intent
    key = missing[0]
    if key in _MONEY_PARAM_KEYS:
        value = _bare_query_money(query)
        if value:
            return replace(intent, params={**intent.params, key: value})
    elif key == "target_model":
        model = _extract_target_model(query)
        if model:
            return replace(intent, target_model=model)
    elif key == "franchise_brand":
        cleaned = _rule_extract_address(query).strip()
        if cleaned and len(cleaned) >= 2 and len(cleaned.split()) <= 2:
            return replace(intent, franchise_brand=cleaned)
    return intent


async def _run_tool(
    tool: str, intent: _Intent, points: list[_Point], radius_m: int
) -> tuple[str, Any]:
    """고른 도구를 실제로 부른다. (도구키, 응답모델) — 실패는 위에서 잡는다."""
    p = intent.params
    codes = _codes(intent)
    primary = points[0]

    if tool == "lease_verdict":
        return tool, await run_lease_verdict(
            LeaseVerdictRequest(
                lat=primary.lat, lng=primary.lng, radius_m=radius_m, **codes,
                monthly_rent_man=p["monthly_rent_man"],
                maintenance_man=p.get("maintenance_man", 0.0),
                deposit_man=p.get("deposit_man"),
                key_money_man=p.get("key_money_man"),
                area_pyeong=p.get("area_pyeong"),
                floor=int(p["floor"]) if "floor" in p else None,
                label=primary.label,
            )
        )

    if tool == "purchase_verdict":
        return tool, await run_purchase_verdict(
            PurchaseVerdictRequest(
                lat=primary.lat, lng=primary.lng, radius_m=radius_m, **codes,
                price_manwon=p["price_manwon"],
                area_sqm=_area_sqm(p) or 0.0,
                floor=int(p["floor"]) if "floor" in p else None,
                monthly_rent_man=p.get("monthly_rent_man"),
                deposit_man=p.get("deposit_man"),
                label=primary.label,
            )
        )

    if tool == "profitability":
        return tool, await run_profitability(
            ProfitabilityAnalyzeRequest(
                purchase=ProfitabilityPurchaseIn(price_manwon=p["price_manwon"]),
                rental=ProfitabilityRentalIn(
                    deposit_manwon=p.get("deposit_man", 0.0),
                    monthly_rent_manwon=p["monthly_rent_man"],
                ),
                market_context=ProfitabilityMarketContextIn(
                    lat=primary.lat, lng=primary.lng, area_sqm=_area_sqm(p)
                ),
            )
        )

    if tool == "decide":
        return tool, await run_decide(
            DecideRequest(
                radius_m=radius_m, **codes,
                candidates=[
                    CandidateIn(
                        id=f"c{i}", label=pt.label, lat=pt.lat, lng=pt.lng,
                        monthly_rent_man=p.get("monthly_rent_man"),
                        deposit_man=p.get("deposit_man"),
                        area_pyeong=p.get("area_pyeong"),
                    )
                    for i, pt in enumerate(points)
                ],
                constraints=(
                    DecideConstraints(max_rent_man=p["monthly_rent_man"])
                    if "monthly_rent_man" in p
                    else None
                ),
            )
        )

    if tool == "area_search":
        return tool, await run_area_search(
            AreaSearchRequest(
                radius_m=radius_m, **codes,
                region_query=primary.label,
                lat=primary.lat, lng=primary.lng,
                budget_max_rent_man=p.get("monthly_rent_man"),
                max_candidates=int(p.get("max_candidates", 5)),
            )
        )

    if tool == "trends":
        return tool, await run_trends(
            DashboardPointRequest(
                lat=primary.lat, lng=primary.lng, radius_m=radius_m, **codes,
                business_type=intent.business_type or "general",
            )
        )

    if tool == "cannibalization":
        return tool, await run_cannibalization(
            DashboardPointRequest(
                lat=primary.lat, lng=primary.lng, radius_m=radius_m, **codes,
                brand=intent.brand,
            )
        )

    if tool == "territory":
        return tool, await run_territory(
            DashboardPointRequest(
                lat=primary.lat, lng=primary.lng, radius_m=radius_m, **codes,
            )
        )

    # ── 땅값 셋 ───────────────────────────────────────────────────────
    #
    # 셋 다 **미리 계산된 표만 읽는다.** 없으면 None을 돌려 "찾지 못했다" 경로를
    # 탄다(analogs와 같은 규약) — 지어낸 판정을 내보내지 않는다.
    if tool == "land_parcel":
        return tool, await asyncio.to_thread(
            analyze_parcel_at, primary.lat, primary.lng
        )

    if tool == "land_dong":
        dong = await asyncio.to_thread(dong_adm_cd_at, primary.lat, primary.lng)
        if dong is None:
            return tool, None
        adm_cd, adm_nm = dong
        parcels = await asyncio.to_thread(list_top_parcels_in_dong, adm_cd, limit=10)
        if not parcels:
            return tool, None
        return tool, {
            "adm_cd": adm_cd,
            "adm_nm": adm_nm,
            "parcels": [
                {
                    "address": f"{(p_.get('ldong_name') or '').strip()} {(p_.get('jibun') or '').strip()}".strip(),
                    "gap_score": p_.get("gap_score"),
                    "official_price_per_sqm": p_.get("price_per_sqm"),
                    "area_sqm": p_.get("area_sqm"),
                    "road_side_name": p_.get("road_side_name"),
                    "stratum_size": p_.get("stratum_size"),
                }
                for p_ in parcels
            ],
        }

    if tool == "land_ranking":
        dongs = await asyncio.to_thread(list_top_undervalued, 10)
        if not dongs:
            return tool, None
        return tool, {"dongs": dongs}

    if tool == "area_insights":
        return tool, await run_area_insights(
            AreaInsightsRequest(lat=primary.lat, lng=primary.lng, radius_m=radius_m)
        )

    if tool == "analogs":
        # 상권 코드는 지점에서, 업종 프로필은 intent에서. 상권 밖(주택가 한복판)
        # 이거나 프로필이 없으면 None을 돌려 "찾지 못함" 경로를 탄다 — 지어내지
        # 않는다.
        profile_id = _resolve_industry_profile_id(intent)
        if profile_id is None:
            return tool, None
        match = await asyncio.to_thread(trdar_for_point, primary.lat, primary.lng)
        if match is None:
            return tool, None
        try:
            return tool, await asyncio.to_thread(find_analogs, match.trdar_cd, profile_id)
        except AnalogsError:
            return tool, None

    if tool == "real_competition":
        return tool, await run_real_competition(
            RealCompetitionRequest(
                lat=primary.lat, lng=primary.lng, radius_m=radius_m, **codes,
                # 학원 업종은 real_competition 내부에서 academy_cluster로 위임되는데,
                # 거기서 target_model이 필수라(없으면 400) 여기서 안 넘기면 문장에서
                # 과목을 뽑아 놓고도 매번 실패한다.
                target_model=intent.target_model,
            )
        )

    if tool == "property_market":
        return tool, await run_property_market(
            PropertyMarketRequest(
                lat=primary.lat, lng=primary.lng,
                property_type=intent.property_type,
                # 매입가·평수·층이 문장에서 잡혔으면 그대로 넘겨, 그 값이 주변
                # 실거래 중 어디쯤인지(subject_percentile)도 함께 계산한다. 없으면
                # 밴드(P25/P50/P75)만 나온다 — 없는 값을 지어내 채우지 않는다.
                purchase_price_manwon=int(p["price_manwon"]) if "price_manwon" in p else None,
                area_sqm=_area_sqm(p),
                floor=str(int(p["floor"])) if "floor" in p else None,
            )
        )

    if tool == "isochrone":
        return tool, await run_isochrone(
            DashboardPointRequest(
                lat=primary.lat, lng=primary.lng, radius_m=radius_m, **codes,
                mode=intent.reach_mode,
                # 문장에서 시간을 못 뽑았으면 요청 모델의 기본 밴드(5/10/15분)를
                # 그대로 쓴다 — 값을 지어내지 않는다.
                minutes=[intent.reach_minutes] if intent.reach_minutes else [5, 10, 15],
            )
        )

    if tool == "diagnose":
        return tool, await run_diagnose(
            DiagnoseRequest(
                lat=primary.lat, lng=primary.lng, radius_m=radius_m, **codes,
                current_monthly_sales_man=p["current_monthly_sales_man"],
                monthly_rent_man=p.get("monthly_rent_man"),
            )
        )

    if tool == "academy_cluster":
        return tool, await run_academy_cluster_analysis(
            AcademyClusterRequest(
                lat=primary.lat, lng=primary.lng, radius_m=radius_m, **codes,
                target_model=intent.target_model or "",
            )
        )

    if tool == "lease_feasibility":
        return tool, await run_lease_feasibility(
            LeaseFeasibilityRequest(
                lat=primary.lat, lng=primary.lng, radius_m=radius_m, **codes,
                monthly_rent_man=int(p["monthly_rent_man"]),
                maintenance_man=int(p.get("maintenance_man", 0.0)),
                deposit_man=int(p["deposit_man"]) if "deposit_man" in p else None,
                key_money_man=int(p["key_money_man"]) if "key_money_man" in p else None,
                area_pyeong=p.get("area_pyeong"),
            )
        )

    if tool == "franchise_brand_detail":
        # 지점과 무관한 브랜드명 조회라 points를 쓰지 않는다. 실재하지 않는
        # 브랜드명이 잡히면 franchise_brand_detail이 404를 준다 — territory가
        # "해당 없음"일 때처럼 그대로 던지지 않고 None으로 알려, 위 호출부가
        # 값을 지어내지 않고 site_score로 안전하게 내려가게 한다.
        try:
            return tool, await run_franchise_brand_detail(name=intent.franchise_brand or "")
        except HTTPException as exc:
            if exc.status_code == 404:
                return tool, None
            raise

    if tool == "industry_profile":
        # 지점과 무관한 업종 설정 조회라 points를 쓰지 않는다 — site_score가
        # industry_kpis를 계산할 때 쓰는 것과 같은 ProfileResolver 경로로 이미
        # _tool_ready에서 확인한 프로필 id를 그대로 다시 구한다.
        profile_id = _resolve_industry_profile_id(intent)
        if profile_id is None:
            return tool, None
        # 프랜차이즈 창업비용을 물은 흐름이면(franchise_brand) 프랜차이즈용
        # 체크리스트(잠식·신규 커버리지 등)를, 아니면 개인 창업자용을 준다.
        user_type = "franchise" if intent.franchise_brand else "entrepreneur"
        try:
            raw = get_adaptive_profile(profile_id, user_type)
        except (KeyError, ValueError):
            return tool, None
        kpis = [
            IndustryProfileKpiOut(key=str(k["key"]), label=str(k.get("label") or k["key"]))
            for k in (raw.get("ui", {}).get("summaryKpis") or [])
            if k.get("key")
        ]
        return tool, IndustryProfileChecklistOut(
            industry_id=str(raw.get("industryId") or profile_id),
            industry_label=str(raw.get("name") or profile_id),
            user_type=user_type,
            summary_kpis=kpis,
        )

    raise ValueError(f"unknown tool: {tool}")


# 도구 응답에서 서술에 쓸 값과 사람이 읽을 이름. 도구마다 응답 모양이 다르지만
# 이름은 겹치지 않아서 한 표로 다룰 수 있다. 전부 넘기면 프롬프트가 길어져 5초
# 예산과 답변 길이를 함께 해치므로 결론에 필요한 것만 고른다.
_TOOL_FACT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("headline", "도구 판정", "text"),
    ("verdict_label", "판정", "text"),
    ("expected_monthly_sales_man", "예상 월매출", "man"),
    ("break_even_monthly_man", "손익분기 월매출", "man"),
    ("rent_burden_pct", "매출 대비 임차비 비중", "pct"),
    ("appropriate_rent_low_man", "적정 월세 하한", "man"),
    ("appropriate_rent_high_man", "적정 월세 상한", "man"),
    ("rent_gap_man", "제시 월세와 적정선 차이", "man"),
    ("key_money_payback_months", "권리금 회수 개월", "num"),
    ("cap_rate_pct", "수익률", "pct"),
    ("dscr", "부채상환비율", "num"),
    ("payback_years", "회수 기간(년)", "num"),
    ("max_acceptable_price_manwon", "적정 매입가 상한", "man"),
    ("price_premium_pct", "시세 대비", "pct"),
    ("monthly_noi_man", "월 순영업이익", "man"),
    ("gap_man", "기대치와의 매출 격차", "man"),
    ("gap_pct", "매출 격차", "pct"),
    ("comparison_note", "비교 요약", "text"),
)


def _fact_value(raw: Any, kind: str) -> str:
    if kind == "text":
        return str(raw).strip()
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return ""
    if kind == "man":
        return _won(number * 10_000)  # 만원 단위 → 사람이 읽는 금액
    if kind == "pct":
        return f"{number:.1f}%"
    return f"{number:,.1f}".rstrip("0").rstrip(".")


def _item_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("label") or item.get("text") or item.get("title") or "").strip()
    return str(item).strip()


_FOOTFALL_SLOTS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("출근(7~9시)", (7, 8, 9)),
    ("점심(11~13시)", (11, 12, 13)),
    ("저녁(18~20시)", (18, 19, 20)),
    ("심야(22~1시)", (22, 23, 0, 1)),
)


def _footfall_slot_line(footfall: dict[str, Any]) -> str:
    """시간대별 생활인구(24칸)를 하루 평균 대비 배율로 요약한다."""
    weekday = footfall.get("weekday_hourly") or []
    if len(weekday) != 24 or not all(isinstance(v, (int, float)) for v in weekday):
        return ""
    mean = sum(weekday) / 24
    if mean <= 0:
        return ""
    parts = [
        f"{name} {sum(weekday[h] for h in hours) / len(hours) / mean:.1f}배"
        for name, hours in _FOOTFALL_SLOTS
    ]
    line = "평일 시간대별 생활인구(하루 평균=1배): " + ", ".join(parts)
    weekend = footfall.get("weekend_hourly") or []
    if len(weekend) == 24 and all(isinstance(v, (int, float)) for v in weekend):
        line += f" · 주말 총량은 평일의 {sum(weekend) / sum(weekday):.2f}배"
    return line


def _tool_facts_from_dict(tool: str, data: dict[str, Any]) -> str:
    """도구 응답(dict)에서 서술에 쓸 사실만 뽑는다."""
    spec = _TOOLS.get(tool)
    lines = [f"실행한 도구: {spec.label if spec else tool}"]
    for key, name, kind in _TOOL_FACT_FIELDS:
        raw = data.get(key)
        if raw is None or raw == "":
            continue
        text = _fact_value(raw, kind)
        if text:
            lines.append(f"{name}: {text}")

    for key, name in (
        ("strengths", "강점"),
        ("risks", "위험"),
        ("causes", "원인"),
        ("actions", "할 일"),
    ):
        texts = [t for t in (_item_text(i) for i in (data.get(key) or [])[:3]) if t]
        if texts:
            lines.append(f"{name}: " + ", ".join(texts))

    # 예측 범위 — 점 추정만 서술에 넘기면 LLM도 확정치처럼 말한다.
    sales_range = data.get("expected_monthly_sales_range_man")
    if isinstance(sales_range, list) and len(sales_range) == 2:
        lines.append(
            f"예상 월매출 범위(최근 분기 변동): {sales_range[0]:,.0f}~{sales_range[1]:,.0f}만원"
        )

    # 비슷한 상권(analogs) — 구조가 닮은 곳들의 성과를 서술에 넘긴다. 숫자를
    # 문장으로 만들 재료라 만원 단위로 줄여 준다.
    analogs = data.get("analogs")
    if isinstance(analogs, list) and analogs:
        subject = data.get("subject") or {}
        sub_per = subject.get("per_store_monthly_won")
        if isinstance(sub_per, (int, float)):
            lines.append(
                f"이 상권({subject.get('trdar_nm', '')}) 점포당 월매출: "
                f"{sub_per / 10_000:,.0f}만원 (동일업종 {subject.get('store_count')}곳)"
            )
        parts = []
        for a in analogs[:3]:
            if not isinstance(a, dict):
                continue
            per = a.get("per_store_monthly_won")
            per_txt = f"{per / 10_000:,.0f}만원" if isinstance(per, (int, float)) else "매출 미상"
            parts.append(f"{a.get('trdar_nm')}(유사도 {a.get('similarity')}, 점포당 {per_txt})")
        if parts:
            lines.append("구조가 비슷한 상권: " + ", ".join(parts))
            lines.append(f"유사도 기준: {data.get('basis', '')}")

    # 추이 지표는 series 안에 들어 있어 평면 필드 훑기로는 안 잡힌다.
    series = data.get("series")
    if isinstance(series, dict):
        for key, name in (
            ("category_growth_pct", "업종 점포 증감"),
            ("total_growth_pct", "전체 점포 증감"),
        ):
            value = series.get(key)
            if isinstance(value, (int, float)):
                lines.append(f"{name}: {value:+.1f}%")
        quarters = series.get("quarters") or []
        if quarters:
            lines.append(f"추이 구간: {quarters[0]}~{quarters[-1]} ({len(quarters)}분기)")

    signals = data.get("street_signals") or []
    if signals:
        parts = []
        for sig in signals[:4]:
            if not isinstance(sig, dict):
                continue
            name = str(sig.get("name") or "").strip()
            if not name:
                continue
            label = str(sig.get("label") or "").strip()
            growth = sig.get("category_growth_pct")
            if not isinstance(growth, (int, float)):
                growth = sig.get("store_growth_pct")
            tail = f"({growth:+.1f}%)" if isinstance(growth, (int, float)) else ""
            parts.append(f"{name} {label}{tail}".strip())
        if parts:
            lines.append("성장 신호: " + ", ".join(parts))

    ranked = data.get("ranked") or []
    if ranked:
        lines.append(
            "후보 순위: "
            + ", ".join(
                f"{idx + 1}위 {r.get('label') or r.get('id') or ''}"
                + (f"({r['score']:.0f}점)" if isinstance(r.get("score"), (int, float)) else "")
                for idx, r in enumerate(ranked[:4])
                if isinstance(r, dict)
            )
        )
    candidates = data.get("candidates") or []
    if candidates:
        lines.append(
            "찾은 자리: "
            + ", ".join(
                f"{c.get('label') or c.get('address') or ''}"
                + (f"({c['score']:.0f}점)" if isinstance(c.get("score"), (int, float)) else "")
                for c in candidates[:5]
                if isinstance(c, dict)
            )
        )

    # 잠식 응답은 위 표에 없는 전용 필드(risk_score·overlap_pct 등)를 쓴다.
    # risk_score는 "높을수록 나쁜" 지표라 원점수만 주면 LLM이 좋은 쪽으로 읽을 수
    # 있으므로 엔진이 정한 risk_label(높음/중간/낮음)을 항상 함께 붙인다.
    if tool == "cannibalization":
        risk_score = data.get("risk_score")
        risk_label = data.get("risk_label")
        if isinstance(risk_score, (int, float)) and risk_label:
            lines.append(f"잠식 위험도: {risk_score:.0f}점 ({risk_label}, 높을수록 위험)")
        total_count = data.get("total_count")
        if isinstance(total_count, int):
            lines.append(f"도달권 내 동일업종 경쟁 {total_count}곳")
        overlap_pct = data.get("overlap_pct")
        overlap_with = data.get("overlap_with")
        if isinstance(overlap_pct, (int, float)) and overlap_with:
            lines.append(f"최근접 경쟁점({overlap_with})과 도달권 겹침 {overlap_pct}%")
        # self_total_count는 브랜드가 실제로 주어져 자기 매장을 찾았을 때만 0보다
        # 크다. 0이라고 "자기잠식 없음"이라 말하면 안 된다 — 브랜드를 못 읽어
        # 애초에 찾지 않은 경우와 구분이 안 되기 때문이다(찾았는데 없는 것 vs
        # 안 찾은 것). 그래서 0일 때는 이 줄 자체를 만들지 않는다.
        self_total = data.get("self_total_count")
        if isinstance(self_total, int) and self_total > 0:
            self_line = f"같은 브랜드 매장 {self_total}곳(자기잠식 후보)"
            self_overlap = data.get("self_overlap_pct")
            if isinstance(self_overlap, (int, float)):
                self_line += f" — 가장 가까운 곳과 도달권 겹침 {self_overlap}%"
            lines.append(self_line)

    # 시세·생활인구 응답도 위 표에 없는 전용 필드다. 셋(아파트·평일 피크·성비)
    # 다 반경 안에 데이터가 없으면 None이라, 값이 실제로 있을 때만 줄을 만든다
    # — 없는 값을 '0'이나 평균으로 지어내지 않는다.
    # ── 땅값 셋 ───────────────────────────────────────────────────────
    #
    # ⚠️ 서술에 넘기는 표현을 여기서 정한다. **"시세"라고 쓰지 않는다** — 이 값은
    # 개별공시지가이고, 서술 단계에서 시세로 바뀌면 사용자는 거래가로 읽는다.
    # 음의 갭도 "고평가"로 넘기지 않는다(낙인 표기 금지).
    if tool == "land_parcel":
        verdict = data.get("verdict") or {}
        lines.append(f"판정: {verdict.get('headline', '')}")
        official = data.get("official_price") or {}
        if official.get("per_sqm"):
            lines.append(
                f"개별공시지가: {official['per_sqm']:,.0f}원/㎡ "
                f"({official.get('base_year')}년) — 시세가 아니다"
            )
        peers = data.get("peer_group") or {}
        if peers.get("description"):
            lines.append(f"비교군: {peers['description']}")
        if peers.get("median_official_price_per_sqm"):
            lines.append(
                f"비교군 공시지가 중앙값: {peers['median_official_price_per_sqm']:,.0f}원/㎡"
            )
        position = data.get("peer_position") or {}
        if position.get("gap_score") is not None:
            lines.append(
                f"구조 대비 가격 갭: {position['gap_score']:+.0f} "
                f"(양수면 길 구조 순위가 가격 순위보다 위)"
            )
        factors = data.get("factors") or []
        if factors:
            lines.append(
                "조건: "
                + ", ".join(
                    f"{f.get('label')}({'가점' if f.get('direction') == 'positive' else '감점'})"
                    for f in factors[:4]
                )
            )
        narrative = (data.get("narrative") or {}).get("paragraphs") or []
        if narrative:
            lines.append("정리: " + " ".join(narrative[:2]))
        return "\n".join(lines)

    if tool == "land_dong":
        lines.append(f"동: {data.get('adm_nm', '')}")
        for p_ in (data.get("parcels") or [])[:5]:
            price = p_.get("official_price_per_sqm")
            price_text = f"{price:,.0f}원/㎡" if isinstance(price, (int, float)) else "공시지가 미확인"
            lines.append(
                f"- {p_.get('address')}: 갭 {p_.get('gap_score', 0):+.0f} · {price_text}"
                f" · 같은 조건 {p_.get('stratum_size') or 0:,}필지 중"
            )
        lines.append(
            "갭은 같은 조건 필지들 사이의 순위 차이이지 시세 대비 할인율이 아니다."
        )
        return "\n".join(lines)

    if tool == "land_ranking":
        for d in (data.get("dongs") or [])[:8]:
            lines.append(
                f"- {d.get('adm_nm', '')}: 갭 {d.get('gap_score', 0):+.0f}"
                f" (구조 {d.get('structure_percentile', 0):.0f} / 가격 {d.get('price_percentile', 0):.0f})"
            )
        lines.append("동 순위는 후보 구역을 좁히는 용도다 — 답은 그 안의 필지에서 나온다.")
        return "\n".join(lines)

    if tool == "area_insights":
        apartment = data.get("apartment")
        if isinstance(apartment, dict):
            per_pyeong = apartment.get("median_per_pyeong_manwon")
            trade_count = apartment.get("trade_count")
            if isinstance(per_pyeong, (int, float)) and isinstance(trade_count, int):
                scope_note = (
                    "인근 실거래 없어 자치구 전체 중앙값"
                    if apartment.get("scope_kind") == "gu"
                    else "반경 내 실거래"
                )
                lines.append(
                    f"아파트 시세(3.3㎡당): {per_pyeong:,.0f}만원 — "
                    f"{scope_note}(거래 {trade_count}건)"
                )
        footfall = data.get("footfall")
        if isinstance(footfall, dict):
            peak_hour = footfall.get("weekday_peak_hour")
            if isinstance(peak_hour, int):
                lines.append(f"평일 생활인구가 가장 많은 시간대: {peak_hour}시")
            # 2026-11-06: 피크 한 시각만 넘겨서 "점심 장사가 되나, 밤 장사가 되나"에
            # 답할 재료가 없었다. 시간대별 비중과 주말/평일 비를 같이 준다.
            slot_line = _footfall_slot_line(footfall)
            if slot_line:
                lines.append(slot_line)
        breakdown = data.get("footfall_breakdown")
        if isinstance(breakdown, dict) and isinstance(breakdown.get("total"), int) and breakdown["total"] > 0:
            male_pct = breakdown.get("male_pct")
            female_pct = breakdown.get("female_pct")
            if isinstance(male_pct, (int, float)) and isinstance(female_pct, (int, float)):
                lines.append(f"유동인구 성비: 남 {male_pct:.0f}% · 여 {female_pct:.0f}%")
            ages = [a for a in (breakdown.get("ages") or []) if isinstance(a, dict)]
            top_age = max(
                (a for a in ages if isinstance(a.get("value"), (int, float))),
                key=lambda a: a["value"],
                default=None,
            )
            if top_age:
                pct = top_age.get("pct")
                tail = f"({pct:.0f}%)" if isinstance(pct, (int, float)) else ""
                lines.append(f"주 연령대: {top_age.get('label')}{tail}")

    # 권역 응답도 위 표에 없는 전용 필드다. brand_fit_headline은 이미 엔진이
    # 내린 적합도 판정이라 그대로 쓰고, zone_type(gap·overlap·absorbed)은 내부
    # 코드값을 그대로 노출하지 않도록 사람이 읽는 이름을 붙인다.
    if tool == "territory":
        headline = str(data.get("brand_fit_headline") or "").strip()
        if headline:
            lines.append(f"권역 적합도 판정: {headline}")
        bullets = [b for b in (data.get("brand_fit_bullets") or []) if str(b).strip()]
        if bullets:
            lines.append("적합도 근거: " + ", ".join(str(b).strip() for b in bullets[:3]))
        narrative = [n for n in (data.get("narrative") or []) if str(n).strip()]
        if narrative:
            lines.append("권역 변화 요약: " + " ".join(str(n).strip() for n in narrative[:3]))
        zone_names = {"gap": "공백 권역", "overlap": "겹침 권역", "absorbed": "흡수 가능 권역"}
        by_type: dict[str, float] = {}
        for z in data.get("zones") or []:
            if not isinstance(z, dict):
                continue
            zt, area = z.get("zone_type"), z.get("area_m2")
            if isinstance(zt, str) and isinstance(area, (int, float)):
                by_type[zt] = by_type.get(zt, 0.0) + area
        if by_type:
            lines.append(
                "권역 구성: " + ", ".join(
                    f"{zone_names.get(zt, zt)} {area / 10_000:.1f}만㎡"
                    for zt, area in by_type.items()
                )
            )
        demand = data.get("demand")
        if isinstance(demand, dict) and isinstance(demand.get("population"), int):
            lines.append(f"권역 내 배후 인구: {demand['population']:,}명")

    # 경쟁 구조·모델 적합도 응답은 competition·model_fit·synergy·academy_cluster가
    # 전부 선택 필드다(vertical에 따라 일부만 채워진다 — 학원은 academy_cluster,
    # 미용실은 model_fit 없이 competition만). total_intensity는 "높을수록 나쁜"
    # 지표라 원점수만 주면 좋은 쪽으로 읽힐 수 있으므로 엔진이 정한 라벨을 항상
    # 함께 붙인다(cannibalization의 risk_label과 같은 이유).
    if tool == "real_competition":
        competition = data.get("competition")
        if isinstance(competition, dict):
            total_score = competition.get("total_weighted_score")
            total_intensity = competition.get("total_intensity")
            if isinstance(total_score, (int, float)) and total_intensity:
                lines.append(
                    f"경쟁 강도: {total_score:.1f}점 ({total_intensity}, 높을수록 경쟁 치열)"
                )
            one_liner = str(competition.get("summary_one_liner") or "").strip()
            if one_liner:
                lines.append(f"경쟁 요약: {one_liner}")
            buckets = [b for b in (competition.get("buckets") or []) if isinstance(b, dict)]
            total_count = sum(
                b["count"] for b in buckets if isinstance(b.get("count"), int)
            )
            if buckets:
                lines.append(f"경쟁 유형 {len(buckets)}개 구간 · 경쟁점 총 {total_count}곳")

        model_fit = data.get("model_fit")
        if isinstance(model_fit, dict):
            headline = str(model_fit.get("headline") or "").strip()
            if headline:
                lines.append(f"모델 적합도 판정: {headline}")
            models = [m for m in (model_fit.get("models") or []) if isinstance(m, dict)]
            top = max(
                (m for m in models if isinstance(m.get("score"), (int, float))),
                key=lambda m: m["score"],
                default=None,
            )
            if top:
                label = str(top.get("label") or "").strip()
                verdict = str(top.get("verdict") or "").strip()
                if label:
                    lines.append(
                        f"가장 적합한 모델: {label}"
                        + (f" ({verdict}, {top['score']:.0f}점)" if verdict else f" ({top['score']:.0f}점)")
                    )

        synergy = data.get("synergy")
        if isinstance(synergy, dict):
            headline = str(synergy.get("headline") or "").strip()
            if headline:
                lines.append(f"주변 업종 시너지: {headline}")

        academy_cluster = data.get("academy_cluster")
        if isinstance(academy_cluster, dict):
            headline = str(academy_cluster.get("headline") or "").strip()
            if headline:
                lines.append(f"학원 클러스터 판정: {headline}")

    # 실거래 비교 응답도 위 표에 없는 전용 필드다. n=0(표본 없음)이면 밴드
    # 자체가 없으므로(p50 등이 None) 그 사실만 밝히고 값을 지어내지 않는다.
    if tool == "property_market":
        n = data.get("n")
        p50 = data.get("p50")
        if isinstance(n, int) and n > 0 and isinstance(p50, (int, float)):
            type_label = "오피스텔" if data.get("property_type") == "officetel" else "상가"
            months_back = data.get("months_back")
            line = (
                f"주변 {type_label} 매매 실거래 {n}건"
                + (f"(최근 {months_back}개월)" if isinstance(months_back, int) else "")
                + f" — ㎡당 중앙값 {_won(p50)}"
            )
            if data.get("insufficient_sample"):
                line += "(표본이 적어 참고용)"
            lines.append(line)
            p25, p75 = data.get("p25"), data.get("p75")
            if isinstance(p25, (int, float)) and isinstance(p75, (int, float)):
                lines.append(f"실거래 가격대(㎡당): 하위 25% {_won(p25)} ~ 상위 25% {_won(p75)}")
            subject_pct = data.get("subject_percentile")
            subject_pps = data.get("subject_price_per_sqm")
            if isinstance(subject_pct, (int, float)) and isinstance(subject_pps, (int, float)):
                lines.append(
                    f"제시 가격 ㎡당 {_won(subject_pps)} — 주변 실거래 중 하위 "
                    f"{subject_pct:.0f}%(낮을수록 저렴한 축)"
                )
        elif isinstance(n, int) and n == 0:
            lines.append("주변 실거래 비교 표본 없음 — 이 정보로는 가격 수준을 판단할 수 없음")

    # 도달권역 응답도 위 표에 없는 전용 필드다(bands가 리스트라 평면 필드 훑기로는
    # 안 잡힌다). population_estimate는 행정동 생활인구를 도달권 면적 비율로 나눈
    # 근사치라 없으면(수요 데이터 자체가 없는 지점) 그 밴드 줄을 만들지 않는다 —
    # 0이나 평균으로 지어내지 않는다. start_connected가 False면 그 지점이 도로망
    # 고립 구간이라 밴드 자체가 비어 있을 수 있으므로 값 대신 그 사실을 밝힌다.
    if tool == "isochrone":
        mode_label = _REACH_MODE_LABEL_KO.get(str(data.get("mode") or ""), str(data.get("mode") or ""))
        if data.get("start_connected") is False:
            lines.append(f"{mode_label} 도달권역: 분석 위치 주변 도로망을 찾지 못해 계산할 수 없음")
        else:
            for band in data.get("bands") or []:
                if not isinstance(band, dict):
                    continue
                minutes = band.get("minutes")
                if not isinstance(minutes, int):
                    continue
                parts: list[str] = []
                pop = band.get("population_estimate")
                if isinstance(pop, int):
                    parts.append(f"거주인구 약 {pop:,}명")
                store_count = band.get("store_count")
                if isinstance(store_count, int):
                    parts.append(f"업소 {store_count}곳")
                # business_type이 general이면 업종 코드 자체를 안 넘겨 이 값이
                # 기계적으로 항상 0이 된다(cannibalization의 self_total_count와
                # 같은 이유) — 0일 때는 "집계했더니 0곳"인지 "애초에 업종을
                # 안 넘겨 안 찾은 것"인지 구분이 안 되므로 줄을 만들지 않는다.
                same_count = band.get("same_category_count")
                if isinstance(same_count, int) and same_count > 0:
                    parts.append(f"동일업종 {same_count}곳")
                if parts:
                    lines.append(f"{mode_label} {minutes}분 도달권: " + ", ".join(parts))
        warnings = [str(w).strip() for w in (data.get("warnings") or []) if str(w).strip()]
        if warnings:
            lines.append("도달권역 계산 참고: " + " ".join(warnings[:2]))

    # 학원 클러스터 응답(위 real_competition 안에 묻힌 요약과 달리, 이 도구는
    # AcademyClusterResponse 전체가 최상위 dict다). headline은 공용 표의
    # "도구 판정"으로 이미 잡히므로 여기선 표에 없는 세 점수·안전 등급·모델
    # 적합도만 더한다.
    if tool == "academy_cluster":
        scores = data.get("scores")
        if isinstance(scores, dict):
            pressure = scores.get("direct_competition_pressure")
            synergy = scores.get("cluster_synergy")
            safety = scores.get("parent_safety")
            if isinstance(pressure, (int, float)):
                lines.append(f"직접 경쟁 압력: {pressure:.1f}점(높을수록 경쟁 치열)")
            if isinstance(synergy, (int, float)):
                lines.append(f"클러스터 시너지: {synergy:.1f}점")
            if isinstance(safety, (int, float)):
                lines.append(f"학부모 안전 점수: {safety:.1f}점")
        detail = data.get("parent_safety_detail")
        if isinstance(detail, dict):
            level = str(detail.get("level") or "").strip()
            if level:
                lines.append(f"학부모 안전 등급: {level}")
        model_fit = data.get("model_fit")
        if isinstance(model_fit, dict):
            headline = str(model_fit.get("headline") or "").strip()
            if headline:
                lines.append(f"모델 적합도 판정: {headline}")

    # 임차 타당성 응답도 전용 필드다(verdict가 다른 판정 도구의 verdict_label과
    # 이름이 달라 공용 표에 안 잡힌다). benchmark_monthly_revenue_man은 실제
    # 서울시 업종 매출 통계 기반이라 지어낸 값이 아니다 — 없으면(n 부족 등) 그냥
    # 생략한다.
    if tool == "lease_feasibility":
        verdict = str(data.get("verdict") or "").strip()
        if verdict:
            lines.append(f"임차 타당성 판정: {verdict}")
        benchmark = data.get("benchmark_monthly_revenue_man")
        if isinstance(benchmark, (int, float)):
            scope = str(data.get("benchmark_scope") or "").strip()
            lines.append(
                f"업종 평균 월매출 벤치마크: {_fact_value(benchmark, 'man')}"
                + (f"({scope} 기준)" if scope else "")
            )
        band = str(data.get("appropriate_rent_band") or "").strip()
        if band:
            lines.append(f"적정 임차비 밴드: {band}")

    # 프랜차이즈 가맹정보 응답도 전용 필드다(공정위 가맹정보공개서 스냅샷을 그대로
    # 옮긴 것이라 다른 도구의 verdict_label 같은 공용 이름이 없다). churn_pct는
    # 이미 서비스가 (계약종료+계약해지)/가맹점수로 계산해 둔 값을 그대로 쓴다 —
    # 여기서 다시 계산하지 않는다(값이 어긋나면 화면·서술이 서로 다른 숫자를
    # 말하게 된다).
    if tool == "franchise_brand_detail":
        brand_nm = str(data.get("brand_nm") or "").strip()
        if brand_nm:
            yr = str(data.get("yr") or "").strip()
            hq = str(data.get("hq_conm") or "").strip()
            industry = str(data.get("industry_mid") or "").strip()
            tail = " · ".join(t for t in (industry, hq) if t)
            lines.append(
                f"가맹 브랜드: {brand_nm}"
                + (f"({tail})" if tail else "")
                + (f" — {yr}년 가맹정보공개서 기준" if yr else "")
            )
        frcs_cnt = data.get("frcs_cnt")
        if isinstance(frcs_cnt, int):
            lines.append(f"가맹점수: {frcs_cnt}곳")
        startup_cost = data.get("startup_cost_total_manwon")
        if isinstance(startup_cost, (int, float)):
            lines.append(f"총 창업비용: {_fact_value(startup_cost, 'man')}")
        churn_pct = data.get("churn_pct")
        if isinstance(churn_pct, (int, float)):
            detail_parts = []
            end_cnt = data.get("contract_end_cnt")
            cancel_cnt = data.get("contract_cancel_cnt")
            if isinstance(end_cnt, int):
                detail_parts.append(f"계약종료 {end_cnt}곳")
            if isinstance(cancel_cnt, int):
                detail_parts.append(f"계약해지 {cancel_cnt}곳")
            lines.append(
                f"폐업률: {churn_pct:.1f}%"
                + (f"({', '.join(detail_parts)})" if detail_parts else "")
            )
        new_open_cnt = data.get("new_open_cnt")
        if isinstance(new_open_cnt, int):
            lines.append(f"신규 개점: {new_open_cnt}곳")

    # 업종별 체크리스트 응답도 전용 필드다. summary_kpis는 값이 아니라 "이
    # 업종을 분석할 때 보는 지표 목록"(설정)이라 다른 도구의 verdict_label과
    # 성격이 다르다 — 실측값은 site_score.industry_kpis 쪽에 이미 있다.
    if tool == "industry_profile":
        industry_label = str(data.get("industry_label") or "").strip()
        kpi_labels = [
            str(k.get("label") or "").strip()
            for k in (data.get("summary_kpis") or [])
            if isinstance(k, dict) and str(k.get("label") or "").strip()
        ]
        if industry_label:
            if kpi_labels:
                lines.append(
                    f"'{industry_label}' 업종 체크리스트 지표: " + ", ".join(kpi_labels)
                )
            else:
                lines.append(f"'{industry_label}' 업종 체크리스트")

    return "\n".join(lines)


def _g(obj, name: str, default=None):
    """속성이 없으면 default. 응답 스키마가 커지는 동안 이 함수가 두 번이나 500을
    냈다(.summary 없음, None subscore 정렬). 있는 값만 쓰고 없으면 조용히 건너뛴다."""
    return getattr(obj, name, default)


def _won(value) -> str:
    """원 단위 정수를 사람이 읽는 한국어 금액으로.

    '5,421,847,864원'을 그대로 LLM에 넘기면 답변에도 그대로 나와서, 상담문이
    아니라 회계 원장처럼 읽힌다. 자릿수를 세어야 크기를 알 수 있는 숫자는 비교
    기준으로 쓸모가 없다 — 억/만원으로 접어 준다.
    """
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    if abs(n) >= 100_000_000:
        return f"{n / 100_000_000:.1f}억원"
    if abs(n) >= 10_000:
        return f"{n / 10_000:,.0f}만원"
    return f"{n:,.0f}원"


def _demand_scope_note(data_scope: str) -> str:
    """배후 인구 수치가 반경 내 실측이 아니라 행정동 통계로 근사한 값이면 밝힌다.

    demand_radius.scale_demand_profile_to_radius가 SGIS 격자 데이터를 못 찾으면
    행정동 밀도×원 면적으로 반경 인구를 추정한다(_radius_data_scope, method=
    "density"/"area_ratio"인 경우 문구에 "추정"이 들어간다). 격자 합산일 때는
    반경 내 실측 합이라 이 안내가 필요 없다. 원문 그대로 옮기면 "SGIS 2024
    행정동 밀도×πr²" 같은 내부 용어가 노출되므로 짧은 안내문으로 바꾼다.
    """
    if "추정" in data_scope:
        return " (반경 내 실측 아님 — 행정동 통계 기반 추정치)"
    return ""


def _strength(score: float) -> str:
    """subscore 원점수 → 강·약 판정어.

    원점수를 그대로 넘기면 LLM이 '바로 연결된 길 수 31점'처럼 내부 점수를 답변에
    그대로 인용한다. 사용자에게 31점은 아무 의미가 없고(무엇 대비 31점인지 모름),
    프로젝트 원칙상 판정은 엔진이 해야 한다. 그래서 문턱값을 여기서 고정한다.
    """
    if score >= 85:
        return "매우 좋음"
    if score >= 70:
        return "좋음"
    if score >= 50:
        return "무난"
    return "약함"


def _rank_verdict(top_pct: float) -> str:
    """서울 상위 % → 많다/적다 판정.

    같은 '상위 68%'를 어떤 답변은 '적은 편은 아니고', 다른 답변은 '아주 강하진
    않지만'이라고 썼다. 판정을 LLM에 맡기면 질의마다 결론이 흔들린다.
    """
    if top_pct <= 10:
        return "매우 많은 편"
    if top_pct <= 30:
        return "많은 편"
    if top_pct <= 60:
        return "보통"
    return "적은 편"


# 이 항목만 점수의 방향이 반대다. site_score.competition_density_display_score는
# "higher = more competition"이라 100점이 최악인데, 점수 내림차순으로 '강한 항목'을
# 뽑으면 경쟁이 가장 심한 자리에서 "강한 항목: 경쟁 밀도 100점"이 나간다. 실제로
# LLM이 이걸 "경쟁 밀도 100점 = 조건이 강하게 형성됐다는 뜻"이라고 서술하면서
# 같은 문단의 '경쟁강도 매우 높음'과 정면으로 모순되는 답변이 나왔다. 경쟁은
# 아래 '경쟁:' 줄에서 따로 말하므로 강·약 순위에서는 뺀다.
_INVERTED_SUBSCORE_KEYS = {"competition"}


def _personal_fit_facts(fit) -> list[str]:
    """personal_fit(우선순위 재가중) 결과 → 사실 줄 목록.

    site_score가 intent.priorities를 받으면 이미 계산해 준 값이다(agent.py는
    다시 계산하지 않는다) — 여기서는 그 결과를 사실 자료로 옮겨 적을 뿐이다.
    LLM 서술(_facts_for_answer)과 규칙 폴백(_rule_answer)이 함께 쓴다 — 그래야
    LLM이 꺼져 있어도 사용자가 강조한 우선순위가 답변에서 사라지지 않는다.
    """
    lines = [
        f"맞춤 점수(우선순위 반영): {fit.personal_score:.0f}점 "
        f"(기본 점수 {fit.base_score:.0f}점)"
    ]
    applied = [p.label for p in fit.priorities if p.applied]
    if applied:
        lines.append("반영된 우선순위: " + ", ".join(applied))
    skipped = [f"{p.label}({p.note})" for p in fit.priorities if not p.applied and p.note]
    if skipped:
        lines.append("반영 안 된 우선순위: " + ", ".join(skipped))
    if fit.strengths:
        lines.append("맞춤 강점: " + ", ".join(fit.strengths))
    if fit.cautions:
        lines.append("맞춤 주의: " + ", ".join(fit.cautions))
    return lines


def _competition_line(c) -> str:
    """경쟁 사실 한 줄. 집계가 꺼져 있으면 '0곳'이라고 말하지 않는다.

    업종 세부분류가 연결되지 않은 업종(노래방·주유소·부동산 등)은 엔진이
    match_method='disabled'로 동일업종 집계를 **아예 하지 않고** count=0을 준다.
    이걸 그대로 '동일업종 0곳'이라고 넘겼더니 LLM이 "이태원에 노래방 0곳,
    경쟁 매우 낮아 진입 유리"라고 답했다 — 사실과 정반대인 데다 창업을 부추기는
    쪽이라 가장 위험한 결함이었다. 집계 안 한 것과 정말 0곳인 것을 구분한다.
    """
    disabled = _g(c, "match_method") == "disabled"
    parts: list[str] = []
    if disabled:
        # 문구는 그대로 인용돼도 읽히게 쓴다. 'match_method=disabled', '업종
        # 세부분류 미연결' 같은 내부 사정을 넣었더니 LLM이 답변에 그대로 옮겨
        # 적어서, 사용자에게는 뜻 모를 용어가 노출됐다.
        parts.append("동일업종 점포 수는 이 업종에서 아직 집계되지 않음 — 경쟁 수준 판단 불가")
    else:
        parts.append(f"동일업종 {c.same_category_count}곳")
    if _g(c, "total_store_count") is not None:
        parts.append(f"반경 내 전체 점포 {c.total_store_count}곳")
    if not disabled:
        if _g(c, "density_per_km2") is not None:
            parts.append(f"밀도 {c.density_per_km2:.0f}곳/km²")
        if _g(c, "intensity_label"):
            parts.append(f"경쟁강도 {c.intensity_label}")
    return "경쟁: " + ", ".join(parts)


def _comparison_rows(site: SiteScoreResponse) -> list[tuple[str, str, str]]:
    """이 동네 핵심 숫자를 서울 중앙값·상위%와 나란히 — (지표, 이 동네, 서울 비교).

    답변에 넣을 표는 여기서 만든 값만 쓴다. LLM에게 표까지 쓰게 하면 서술 중
    숫자를 옮겨 적다 바꿔 쓸 위험이 있다(품질 기준: 화면 값과 계산값이 달라선
    안 됨) — 엔진이 이미 site_score에 계산해 둔 필드를 그대로 옮기기만 하고
    새 계산은 하지 않는다. 두 값(이 동네·서울 비교)이 모두 있는 지표만 넣는다
    — 비교 기준 없이 절대값만 있는 줄은 표의 목적(비교)에 안 맞는다.
    """
    rows: list[tuple[str, str, str]] = []
    d = _g(site, "demand")
    if d is not None:
        rank = _g(d, "seoul_rank")
        if _g(d, "population") is not None and _g(rank, "population_top_pct") is not None:
            rows.append(
                ("거주인구", f"{d.population:,}명", f"서울 상위 {rank.population_top_pct}%")
            )
        if _g(d, "workers") is not None and _g(rank, "workers_top_pct") is not None:
            rows.append(
                ("직장인", f"{d.workers:,}명", f"서울 상위 {rank.workers_top_pct}%")
            )
        # 10~20대 비중은 demand_benchmark.youth_pct_from_age_bands와 같은 계산
        # (age_bands에서 '10대'·'20대' pct 합)이다 — youth_top_pct도 그 값의
        # 서울 내 순위이므로 같은 정의를 여기서 다시 계산해야 짝이 맞는다.
        if _g(rank, "youth_top_pct") is not None:
            age_bands = _g(d, "age_bands") or []
            youth_pct = sum(
                b.pct for b in age_bands if _g(b, "band") in ("10대", "20대")
            )
            if youth_pct:
                rows.append(
                    (
                        "10~20대 인구 비중",
                        f"{youth_pct:.1f}%",
                        f"서울 상위 {rank.youth_top_pct}%",
                    )
                )
        apt = _g(d, "apartment_market")
        if (
            apt is not None
            and _g(apt, "median_price_per_sqm")
            and _g(apt, "seoul_median_per_sqm")
        ):
            seoul_bit = f"서울 중앙값 {apt.seoul_median_per_sqm:,.0f}만원/㎡"
            if _g(apt, "vs_seoul_pct") is not None:
                seoul_bit += f" ({apt.vs_seoul_pct:+d}%)"
            rows.append(
                ("아파트 시세(㎡당)", f"{apt.median_price_per_sqm:,.0f}만원", seoul_bit)
            )
    sb = _g(site, "sales_benchmark")
    if (
        sb is not None
        and _g(sb, "per_store_monthly_won")
        and _g(sb, "citywide_median_per_store_won")
    ):
        diff_pct = round(
            (sb.per_store_monthly_won / sb.citywide_median_per_store_won - 1) * 100
        )
        rows.append(
            (
                "점포당 월매출",
                _won(sb.per_store_monthly_won),
                f"서울 중앙값 {_won(sb.citywide_median_per_store_won)} ({diff_pct:+d}%)",
            )
        )
    return rows


def _comparison_table_md(site: SiteScoreResponse) -> str:
    """비교할 값이 하나도 없으면 빈 문자열(표를 억지로 만들지 않는다)."""
    rows = _comparison_rows(site)
    if not rows:
        return ""
    lines = ["| 지표 | 이 동네 | 서울 비교 |", "|---|---|---|"]
    lines.extend(f"| {label} | {local} | {seoul} |" for label, local, seoul in rows)
    return "\n".join(lines)


def _facts_for_answer(
    site: SiteScoreResponse,
    *,
    place: str = "",
    business_label: str = "",
    radius_m: int | None = None,
    question: str = "",
    tool: str = "site_score",
    tool_result: dict[str, Any] | None = None,
    # 질문 하나가 여러 판정을 요구해 도구를 둘 이상 돌렸을 때 전부 여기로 온다.
    # 주어지지 않으면 위 tool/tool_result 한 쌍으로 만든다(기존 호출자 하위호환).
    tool_results: dict[str, dict[str, Any]] | None = None,
    # 앞 대화 턴들 — 후속 질문을 후속 질문으로 답하게 만드는 유일한 재료다.
    history: list["AgentHistoryTurn"] | None = None,
) -> str:
    # 분석 지점과 업종이 사실 블록에 아예 없었다. 첫 줄이
    # f"장소: {upjong_breadcrumb or business_label} 기준"이었는데, 이 둘은 둘 다
    # 업종 값이고 노래방·주유소 등에서는 빈 문자열이라 "장소:  기준"만 나갔다.
    # 그래서 LLM은 어디를 무슨 업종으로 분석하는지 모른 채 답을 썼고, 주유소
    # 질의에 "메뉴 구성·테이크아웃", 헬스장 질의에 "업종을 먼저 검토하세요"처럼
    # 질문과 무관한 조언이 나왔다.
    lines: list[str] = []
    # 사용자가 실제로 물은 문장. 없으면 서술이 질문과 무관해진다 — '야간 장사'라고
    # 물어도 시간대 얘기가 한 줄도 없었고, '20대 여성 타깃'이라고 물은 자리에서
    # 40~50대가 주력인데도 그 어긋남을 짚지 않고 "적합도 높음"이라고 답했다.
    # 앞 대화가 있으면 맨 위에 둔다 — 이번 질문이 후속 질문인지 첫 질문인지
    # 모델이 판단할 수 있어야 "앞서 말씀드린 대로" 같은 이어짐이 가능해진다.
    if history:
        prev = [t for t in history if (t.query or "").strip()][-3:]
        if prev:
            lines.append("앞 대화(오래된 순):")
            for t in prev:
                where = f" [{t.address}]" if t.address else ""
                lines.append(f"- 사용자: {_shorten(t.query, 80)}{where}")
            lines.append("")
    if question:
        lines.append(f"사용자 질문: {_shorten(question, 200)}")
    if place:
        head = f"분석 지점: {place}"
        if radius_m:
            head += f" (반경 {radius_m}m)"
        lines.append(head)
    label = business_label or site.business_label
    if label:
        detail = site.upjong_breadcrumb
        lines.append(
            f"업종: {label}" + (f" ({detail})" if detail and detail != label else "")
        )
    lines.append(f"종합 점수: {site.total_score:.0f}점 ({site.grade})")
    if _g(site, "verdict_one_liner"):
        lines.append(f"엔진 판정: {site.verdict_one_liner}")

    # 일부 업종은 분석 파이프라인이 아직 미완성이라 subscore.score가 None으로 온다.
    # None과 float은 정렬·포맷이 안 되므로(TypeError → 500) 점수 있는 항목만 쓴다.
    scored = [
        s for s in site.subscores
        if s.score is not None and _g(s, "key") not in _INVERTED_SUBSCORE_KEYS
    ]

    def _item(s) -> str:
        pct = _g(s, "rank_top_pct")
        if pct is not None:
            return f"{s.label}(상위 {pct:.0f}%)"
        return f"{s.label}({_strength(s.score)})"

    ordered = sorted(scored, key=lambda s: s.score, reverse=True)
    if ordered:
        lines.append("강한 항목: " + ", ".join(_item(s) for s in ordered[:3]))
    if len(ordered) > 3:
        lines.append("약한 항목: " + ", ".join(_item(s) for s in ordered[-2:]))

    d = getattr(site, "demand", None)
    if d is not None:
        if _g(d, "adm_name"):
            lines.append(f"행정동: {d.adm_name}")
        # 절대수 + 서울 내 위치를 같이 준다 ('28,503명'만으로는 판단이 불가능).
        who: list[str] = []
        if _g(d, "population") is not None:
            who.append(f"거주인구 {d.population:,}명")
        if _g(d, "households") is not None:
            who.append(f"가구 {d.households:,}세대")
        if _g(d, "workers") is not None:
            who.append(f"종사자(직장인) {d.workers:,}명")
        if who:
            lines.append(
                "배후 규모: " + ", ".join(who)
                + _demand_scope_note(str(_g(d, "data_scope") or ""))
            )
        rank = _g(d, "seoul_rank")
        if rank is not None:
            # 상위 %만 주면 LLM이 매번 다른 문턱으로 판정한다(같은 상위 68%를
            # 어떤 답은 '적은 편은 아니고', 다른 답은 '아주 강하진 않지만'이라
            # 썼다). 판정어까지 엔진이 붙여 서술만 맡긴다.
            ranks: list[str] = []
            for attr, name in (
                ("population_top_pct", "거주인구"),
                ("workers_top_pct", "직장인"),
                ("youth_top_pct", "청년인구"),
            ):
                pct = _g(rank, attr)
                if pct is not None:
                    ranks.append(f"{name} 서울 상위 {pct}%({_rank_verdict(float(pct))})")
            if ranks:
                lines.append("서울 내 위치: " + ", ".join(ranks))
        if _g(d, "avg_age") is not None:
            lines.append(f"평균 연령: {d.avg_age:.0f}세")
        gender = _g(d, "gender")
        if gender is not None:
            lines.append(
                f"성비: 남 {gender.male_pct:.0f}% / 여 {gender.female_pct:.0f}%"
            )
        top_bands = sorted(_g(d, "age_bands", []) or [], key=lambda b: b.pct, reverse=True)[:3]
        if top_bands:
            lines.append(
                "주요 연령대: "
                + ", ".join(f"{b.band} {b.pct:.0f}%" for b in top_bands)
            )
        if _g(d, "demographic_fit_label"):
            lines.append(f"업종-인구 적합도: {d.demographic_fit_label}")
        apt = _g(d, "apartment_market")
        if apt is not None and _g(apt, "median_price_per_sqm"):
            # 아파트 시세는 배후 소비력의 대리지표. 값의 단위는 만원/㎡다
            # (housing_market.fetch_apartment_market이 med_manwon을 넣는다).
            # scope가 "구 단위 (...)"면 동 단위 거래가 3건 미만이라 구 전체
            # 중앙값으로 메운 값이다(fetch_apartment_market) — 그 정밀도 차이를
            # 밝히지 않으면 "이 동네 아파트 시세"로 오독된다(sales_benchmark의
            # 행정동 폴백 안내와 같은 이유).
            apt_scope = str(_g(apt, "scope") or "")
            apt_notes: list[str] = []
            if apt_scope.startswith("구 단위"):
                apt_notes.append("동 단위 거래 부족해 구 전체 중앙값")
            apt_sample = _g(apt, "sample_count")
            if isinstance(apt_sample, int) and apt_sample > 0:
                apt_notes.append(f"거래 {apt_sample}건")
            apt_note = f" — {', '.join(apt_notes)}" if apt_notes else ""
            lines.append(
                f"배후 소비력(아파트 시세 중앙값): "
                f"{apt.median_price_per_sqm:,.0f}만원/㎡{apt_note}"
            )

    # 2026-11-06: 유동인구·지역 소비는 site_score가 계산해(demand.market_context)
    # 화면 카드에만 그리고 상담 모델에는 넘기지 않았다. 소유자 지적 — "유동인구·
    # 외국인 데이터를 넣어 뒀는데 답은 길 구조 얘기뿐이다."
    mc = _g(d, "market_context") if d is not None else None
    if mc is not None:
        flow: list[str] = []

        def _rel(rel, fmt) -> str:
            pct = _g(rel, "top_pct")
            return fmt(rel.value) + (f"(서울 행정동 상위 {pct}%)" if pct is not None else "")

        if _g(mc, "footfall_daily") is not None:
            flow.append(_rel(mc.footfall_daily, lambda v: f"하루 유동인구 {v:,.0f}명"))
        if _g(mc, "female_pct") is not None:
            flow.append(_rel(mc.female_pct, lambda v: f"여성 비중 {v:.0f}%"))
        ages = sorted(
            ((band, rel) for band, rel in (_g(mc, "age_pcts", {}) or {}).items() if rel is not None),
            key=lambda item: item[1].value,
            reverse=True,
        )[:2]
        if ages:
            flow.append("주 연령대 " + ", ".join(f"{band} {rel.value:.0f}%" for band, rel in ages))
        if flow:
            quarter = f", {mc.quarter}" if _g(mc, "quarter") else ""
            lines.append(f"유동인구({mc.dong_name} 행정동 기준{quarter}): " + ", ".join(flow))
        if _g(mc, "local_spend_monthly") is not None:
            lines.append(
                "지역 카드 소비(월): "
                + _rel(mc.local_spend_monthly, lambda v: _won(v))
                + " — 그 지역에서 쓰인 돈이며 주민 구매력과는 다르다"
            )
    foreign = _g(site, "foreign_share_pct")
    if isinstance(foreign, (int, float)):
        lines.append(f"생활인구 중 단기체류 외국인 비중: {foreign:.1f}% (반경 기준)")

    transit = getattr(site, "transit", None)
    if transit is not None and _g(transit, "station_name"):
        lines.append(
            f"대중교통: {transit.station_name} {_g(transit, 'distance_m', 0):.0f}m"
        )

    c = getattr(site, "competition", None)
    if c is not None:
        lines.append(_competition_line(c))

    sb = getattr(site, "sales_benchmark", None)
    if sb is not None:
        sales: list[str] = []
        if _g(sb, "monthly_sales_won") is not None:
            # 지정 상권 밖이면 행정동 평균으로 메운 값이다. 그걸 "상권 ○○동"이라고
            # 부르면 골목상권 매출과 같은 정밀도로 읽힌다 — 단위를 그대로 밝힌다.
            is_dong = _g(sb, "scope", "trdar") == "dong"
            unit = "행정동" if is_dong else "상권"
            sales.append(
                f"{unit} {_g(sb, 'trdar_nm', '')} 월매출 {_won(sb.monthly_sales_won)}"
                + ("(지정 상권 밖이라 행정동 평균)" if is_dong else "")
            )
        if _g(sb, "per_store_monthly_won"):
            sales.append(f"점포당 {_won(sb.per_store_monthly_won)}")
        # 시 전체 중앙값과 나란히 둬야 '높다/낮다'를 말할 수 있다.
        if _g(sb, "citywide_median_per_store_won"):
            sales.append(
                f"서울 점포당 중앙값 {_won(sb.citywide_median_per_store_won)}"
            )
        if _g(sb, "weekend_share_pct") is not None:
            sales.append(f"주말 매출 비중 {sb.weekend_share_pct:.0f}%")
        if sales:
            lines.append("매출: " + ", ".join(sales))

    kpis = [
        k for k in (getattr(site, "industry_kpis", []) or [])
        if _g(k, "display") and k.display != "—"
    ]
    if kpis:
        lines.append(
            "업종 지표: " + ", ".join(f"{k.label} {k.display}" for k in kpis[:4])
        )

    fit = getattr(site, "personal_fit", None)
    if fit is not None:
        lines.extend(_personal_fit_facts(fit))

    # 질문이 요구한 도구를 실제로 돌렸으면 그 판정(들)이 답의 중심이다. 입지
    # 점수는 근거로 남기고, 계약·매입·비교·진단 결론을 앞세운다. 질문 하나가
    # 여러 판정을 요구해 도구를 둘 이상 돌렸으면 전부 재료로 접는다 — 하나만
    # 넘기면 나머지는 계산해 놓고도 서술에서 사라진다.
    results = tool_results if tool_results else (
        {tool: tool_result} if tool != "site_score" and tool_result else {}
    )
    for t, result in results.items():
        lines.append(_tool_facts_from_dict(t, result))

    return "\n".join(lines)


#: 이미 계산한 도구는 다시 권하지 않는다 — 순서가 우선순위다. 채팅 문장만으로
#: 값을 못 뽑아 항상 site_score로 폴백하는 도구(simulate·franchise_brand_detail)와
#: 업종이 좁아 대부분 질문에 안 맞는 도구(academy_cluster 등)는 뺐다. 각 문장은
#: 그 자체로 답이 아니라 "다음에 물을 질문"이라 숫자를 담지 않는다 — 담으면
#: 실행하지 않은 도구의 값을 지어내는 셈이 된다.
# 2026-11-06: 순서를 창업 준비자 기준으로 바꿨다(사용자 테스트 §9-2) — 「2호점을 내면」·
# 「다른 상권 손님」은 이미 가게가 있는 사람의 질문이라 맨 뒤로.
_SUGGESTION_TEMPLATES: dict[str, str] = {
    "trends": "이 업종 최근 매출·점포 수 추이는 어때요?",
    "real_competition": "이 자리엔 프랜차이즈랑 개인 창업 중 어느 쪽이 더 잘 맞을까요?",
    "cannibalization": "여기 2호점을 내면 기존 매장 매출을 얼마나 가져갈까요?",
    "decide": "근처 다른 동네랑 비교하면 여기가 나을까요?",
    "area_insights": "시간대별로 손님이 언제 많아요?",
    "territory": "여기 들어가면 다른 상권 손님을 얼마나 가져올 수 있을까요?",
    "property_market": "지금 나온 매물 가격이 주변 실거래가에 비해 괜찮은 걸까요?",
    "profitability": "이 자리에서 예상 수익률은 어느 정도인가요?",
    "lease_verdict": "월세 얼마까지면 여기 임차해도 괜찮을까요?",
    "purchase_verdict": "이 자리를 매입한다면 적정 가격은 얼마일까요?",
}
_SUGGESTION_PRIORITY: tuple[str, ...] = (
    "lease_verdict", "area_insights", "trends", "decide", "real_competition",
    "property_market", "profitability", "purchase_verdict", "territory", "cannibalization",
)
_SUGGESTION_COUNT = 3


def _suggested_questions(executed: Container[str]) -> list[str]:
    """이번 답변에 이미 실은 도구를 뺀, 이어 물을 법한 질문 최대 3개.

    ``executed``에 없는 도구만 후보라 이미 답한 걸 또 권하지 않는다. 값은
    고정 문구뿐이라(place·숫자 보간 없음) 계산 안 한 값을 지어낼 여지가 없다.
    """
    out: list[str] = []
    for key in _SUGGESTION_PRIORITY:
        if key in executed:
            continue
        out.append(_SUGGESTION_TEMPLATES[key])
        if len(out) >= _SUGGESTION_COUNT:
            break
    return out


def _rule_answer(site: SiteScoreResponse) -> str:
    if site.verdict_one_liner:
        # verdict_one_liner는 거의 항상 채워져 있어(build_verdict_one_liner) 이
        # 이른 반환이 아래 personal_fit 문단까지 늘 가로챈다 — 우선순위를
        # 강조해 물었는데 LLM이 꺼져 규칙 폴백으로 떨어지면 맞춤 점수가 화면에서
        # 통째로 사라졌다. 한 줄 결론은 그대로 두고 맞춤 점수만 이어 붙인다.
        fit = _g(site, "personal_fit")
        if fit is None:
            return site.verdict_one_liner
        return (
            site.verdict_one_liner
            + "\n\n**맞춤 점수**\n"
            + "; ".join(_personal_fit_facts(fit))
            + "."
        )
    top = max(
        (s for s in site.subscores if s.score is not None),
        key=lambda s: s.score,
        default=None,
    )
    lead = f"종합 {site.total_score:.0f}점({site.grade})"
    if top:
        lead = f"{lead} — 가장 강한 항목은 {top.label}입니다."

    # LLM이 없거나 실패해도 최소한 '좋고 나쁨'은 읽히도록 근거를 붙인다.
    parts = [f"**한 줄 결론**\n{_g(site, 'verdict_one_liner') or lead}"]

    d = _g(site, "demand")
    if d is not None:
        rank = _g(d, "seoul_rank")
        bits: list[str] = []
        if _g(d, "population") is not None:
            text = f"거주인구 {d.population:,}명"
            if rank is not None and _g(rank, "population_top_pct") is not None:
                text += f" (서울 상위 {rank.population_top_pct}%)"
            bits.append(text)
        if _g(d, "workers") is not None:
            text = f"직장인 {d.workers:,}명"
            if rank is not None and _g(rank, "workers_top_pct") is not None:
                text += f" (서울 상위 {rank.workers_top_pct}%)"
            bits.append(text)
        if bits:
            note = _demand_scope_note(str(_g(d, "data_scope") or ""))
            parts.append("**배후 수요**\n" + ", ".join(bits) + "입니다" + note + ".")

    sb = _g(site, "sales_benchmark")
    if sb is not None and _g(sb, "per_store_monthly_won"):
        text = f"점포당 월매출 {_won(sb.per_store_monthly_won)}"
        # sales_benchmark.scope가 "dong"이면 지정 상권 밖이라 행정동 평균으로
        # 메운 값이다(_facts_for_answer의 같은 필드 처리와 같은 이유) — LLM이
        # 꺼져 이 규칙 폴백으로 떨어져도 정밀도 차이를 숨기면 안 된다.
        if _g(sb, "scope", "trdar") == "dong":
            text += "(지정 상권 밖이라 행정동 평균)"
        median = _g(sb, "citywide_median_per_store_won")
        if median:
            higher = sb.per_store_monthly_won >= median
            text += (
                f"으로 서울 중앙값({_won(median)})보다 "
                f"{'높습니다' if higher else '낮습니다'}"
            )
        parts.append("**소비력·매출**\n" + text + ".")

    c = _g(site, "competition")
    if c is not None:
        if _g(c, "match_method") == "disabled":
            # 집계를 안 한 것을 '0곳'이라고 말하면 안 된다 — _competition_line 주석 참고.
            text = "이 업종은 동일업종 점포 수가 아직 집계되지 않아 경쟁 수준을 판단할 수 없습니다"
        else:
            text = f"도보 5분 내 동일업종 {c.same_category_count}곳"
            if _g(c, "intensity_label"):
                text += f", 경쟁강도는 {c.intensity_label}"
            text += "입니다"
        parts.append("**경쟁**\n" + text + ".")

    fit = _g(site, "personal_fit")
    if fit is not None:
        parts.append("**맞춤 점수**\n" + "; ".join(_personal_fit_facts(fit)) + ".")

    return "\n\n".join(parts)


async def _write_answer(
    site: SiteScoreResponse,
    *,
    place: str = "",
    business_label: str = "",
    radius_m: int | None = None,
    question: str = "",
    tool: str = "site_score",
    tool_result: dict[str, Any] | None = None,
    tool_results: dict[str, dict[str, Any]] | None = None,
    history: list["AgentHistoryTurn"] | None = None,
    brief: bool = False,
) -> tuple[str, str]:
    """엔진이 낸 숫자를 근거로 한 상담 답변. LLM 실패 시 규칙 폴백.

    brief=True(2026-10-26): 결론·이유·할 일 350자 안팎. 결정 카드가 이미 답의 맨 위에
    서 있으므로 서술은 그 판단을 사람 말로 풀어 주는 몇 문장이면 된다. 긴 서술(1,200자)은
    gpt-5.4로 14초였다 — 상용 서비스로 기다릴 수 없는 시간이다.

    사실을 그대로 나열하지 말고 **좋고 나쁨을 판정**해 주도록 섹션을 지정한다.
    절대수(예: '거주인구 28,503명')는 그 자체로 의미가 없으므로 항상 상위 %·
    중앙값 같은 비교 기준과 함께 말하게 한다.

    2026-08-24: 길이 제한을 풀었다(500자·700토큰 → 1,200자·1800토큰). 종전 주석은
    "5초 예산을 지키려고 조인다"였는데, 화면은 이미 **2단계**로 부른다 —
    /ask?include_answer=False로 분석을 먼저 그리고 /answer로 서술만 나중에 채운다
    (3787행). 즉 이 호출이 길어져도 사용자가 보는 첫 화면은 늦어지지 않는다.
    500자 제한은 5초를 지켜 주지 못하면서 답의 깊이만 깎고 있었다(소유자 지적:
    "일반 클로드보다 답변의 질이 떨어진다").

    include_answer=True로 한 번에 부르는 경로(3741행)에서는 이 완화가 그대로 지연이
    된다. 화면은 그 경로를 쓰지 않는다.
    """
    # 2026-11-06: brief는 이제 스트리밍 상담(/ask/stream)이 실패했을 때의 폴백이다.
    # 고정 3칸(결론·왜·할 일)을 풀고 질문이 묻는 것에 맞춰 쓰게 했다.
    structure = (
        "형식: 사용자 질문이 실제로 묻는 것에 첫 문장부터 답한다. 고정된 소제목을 쓰지 "
        "않는다. 판단을 가른 근거와 이 업종에 맞는 구체적 조언을 담되 600자 안팎으로. "
        "필요하면 소제목 한 줄을 **굵게**, 문단 사이는 빈 줄. 창업 실무의 일반 지식은 "
        "'일반적으로'라고 밝혀서 써도 된다(특정 장소의 수치는 아래 사실만).\n\n"
    ) if brief else (
        "구성(각 섹션은 빈 줄로 구분, 제목은 **굵게**, 각 섹션 2~4문장, 전체 1,200자 내외):\n"
        "**한 줄 결론** — 이 자리에 이 업종을 열지 말지 판단이 서는 한 문장\n"
        "**배후 수요** — 누가 얼마나 있는지. 괄호 안 판정어(많은 편/적은 편)를 그대로 따른다\n"
        "**소비력·매출** — 점포당 매출을 서울 중앙값과 비교해 높고 낮음을 말한다\n"
        "**경쟁** — 동일업종 수·밀도·경쟁강도로 진입 난이도를 말한다\n"
        "**그래서 이렇게 하세요** — 이 업종에 맞는 구체적 조언 2가지\n"
        "**확인하지 못한 것** — 이 분석에 들어오지 않은 것 중 이 결정에 중요한 "
        "1~3가지(자료가 없어 계산하지 못한 항목, 사용자가 물었는데 답하지 못한 "
        "부분). 없으면 이 섹션을 생략한다\n\n"
    )
    text = await complete_text(
        system=(
            "너는 한국 상권분석 상담사다. 아래 '이미 계산된 사실'만 근거로 한국어 "
            "상담 답변을 쓴다. 사실 맨 위의 '분석 지점'과 '업종'이 실제로 분석한 "
            "장소·업종이다. 반드시 그 업종을 전제로 쓰고, 업종을 새로 고르라고 "
            "권하지 않는다.\n"
            + structure
            + "규칙:\n"
            "- 주어진 숫자만 쓰고 새 숫자를 지어내지 않는다. 자료에 없는 항목은 그 "
            "섹션을 통째로 생략한다(없다는 사실을 길게 변명하지 않는다).\n"
            "- 단, **사용자가 물은 것**이 자료에 없으면 생략하지 말고 '이 분석으로는 "
            "확인되지 않는다'고 분명히 말한다. 무엇이 없어서 답하지 못하는지 한 줄로 "
            "밝히고, 그 자리를 다른 지표로 채워 답한 척하지 않는다. 모르는 것을 "
            "말하지 않는 답이 더 나쁘다.\n"
            "- 자료로 확인된 것과 추정·평균으로 미룬 것을 뭉개지 않는다. 확신의 "
            "정도를 문장에 남긴다(확인됨 / 추정 / 확인 불가).\n"
            "- '경쟁 수준 판단 불가'라고 적혀 있으면 경쟁이 낮다·없다·유리하다고 "
            "절대 쓰지 말고, 집계되지 않아 확인이 필요하다고만 쓴다.\n"
            "- 숫자 뒤에 '추정치'·'행정동 평균'·'행정동 통계 기반'·'표본'처럼 "
            "정밀도를 밝힌 괄호가 있으면 그 사실을 해당 문장에 짧게 남긴다 "
            "(예: '~로 추정됩니다', '행정동 평균 기준입니다') — 실측처럼 "
            "단정하지 않는다.\n"
            "- 항목 뒤의 '상위 N%'는 그대로 인용해도 되지만, 내부 점수(N점)는 "
            "인용하지 않는다.\n"
            "- 맨 위 '사용자 질문'에 조건(영업시간·타깃 연령·임대료 등)이 있으면, "
            "아래 자료로 확인되는 만큼만 그 조건에 맞춰 답한다. 자료가 조건과 "
            "어긋나면 어긋난다고 말하고, 자료에 아예 없는 조건은 '이 분석에 "
            "반영되지 않았다'고 한 번만 짧게 밝힌다(길게 변명하지 않는다).\n"
            "- 업종이 '일반'이면 말한 업종을 아직 분류하지 못해 일반 기준으로 "
            "분석한 것이다. 그 사실을 한 줄 결론에서 한 번 밝힌다.\n"
            "- 자료 아래에 '실행한 도구'와 그 판정이 있으면 **그 판정(들)이 답의 "
            "중심**이다. '실행한 도구'가 한 번 이상 나오면 질문이 여러 판단을 "
            "요구했다는 뜻이니 각 판정을 빠짐없이 반영해 한 줄 결론에서 함께 "
            "말하고, 입지 점수는 근거로만 쓴다. 도구가 없으면 이 규칙은 무시한다.\n"
            "- 도구 결과가 없는데 두 곳이나 두 업종을 비교해 달라는 질문이면 자료는 "
            "한 곳 것뿐이다. 비교하지 말고 분석한 그 한 곳만 말한다.\n"
            "- 자료에 '맞춤 점수(우선순위 반영)'가 있으면 사용자가 강조한 조건이다 "
            "— 한 줄 결론이나 그래서 이렇게 하세요에서 기본 점수와 맞춤 점수가 "
            "왜 다른지, 반영 안 된 우선순위가 있으면 그 이유(자료의 note)를 "
            "짧게 짚는다.\n"
            "- 투자 확정 단정은 피하고 근거 중심으로 쓴다.\n"
            "- 자료 맨 위에 '앞 대화'가 있으면 이번 질문은 후속 질문이다. 같은 "
            "장소·업종을 처음부터 다시 소개하지 말고 이번 질문이 새로 묻는 것에 "
            "답한다. 앞 답과 판단이 달라졌으면 무엇이 달라졌는지 한 줄 결론에서 "
            "짚고, 달라진 게 없으면 그렇다고 말한다. '앞 대화'가 없으면 이 "
            "규칙은 무시한다."
        ),
        user=_facts_for_answer(
            site,
            place=place,
            business_label=business_label,
            radius_m=radius_m,
            question=question,
            tool=tool,
            tool_result=tool_result,
            tool_results=tool_results,
            history=history,
        ),
        max_tokens=1500 if brief else 1800,
        timeout_s=15.0 if brief else 25.0,
        fast=brief,
    )
    # 비교 표는 LLM 서술과 규칙 폴백 둘 다에 똑같이 붙인다. LLM에게 표까지
    # 쓰게 하지 않는 이유는 위 _comparison_table_md 참고 — 여기서 붙이면 어느
    # 경로로 답이 나가든 표의 숫자는 항상 엔진 값 그대로다.
    table = _comparison_table_md(site)

    def _with_table(body: str) -> str:
        if not table:
            return body
        return body + "\n\n**서울 비교**\n" + table

    if text:
        return _with_table(text), "llm"
    return _with_table(_rule_answer(site)), "rule"


async def _talk_response(
    request: "AgentAskRequest",
    *,
    fallback_detail: str,
    fallback_status: int = 400,
    business_type: str = "general",
) -> AgentAskResponse:
    """위치를 특정할 수 없는 질문에 도구 없이 답한다.

    종전엔 여기서 400과 한 문장("위치를 찾지 못했어요")으로 끝났다. 그런데 이
    자리에 실제로 들어오는 말은 대개 위치를 빠뜨린 분석 요청이 아니라 다른 종류의
    질문("공시지가랑 실거래가 차이가 뭐야?")이다. LLM이 꺼져 있거나 실패하면
    종전 400 안내로 돌아간다.
    """
    if not request.talk:
        # 업종만 말한 질문("카페")이면 호출부가 그 업종을 넘긴다 — 화면이 이 응답으로 대화
        # 기록을 쌓으므로, 이어서 위치만 답해도("신촌") 업종이 이어진다 (2026-11-08 UX 자동
        # 개선). 개념 질문("부동산 공시지가가 뭐야")엔 넘기지 않아 엉뚱한 업종이 안 남는다.
        return AgentAskResponse(
            query=request.query,
            address="",
            business_type=business_type,
            business_label=_BUSINESS_LABELS.get(business_type, "") if business_type != "general" else "",
            steps=[AgentStep(key="talk", label="질문 이해", detail="위치를 특정하지 않고 답변")],
            site=None,
            answer="",
            answer_source="pending",
            mode="talk",
        )
    history_summary = ""
    if request.history:
        last = request.history[-1]
        parts = [p for p in (last.address, last.business_label) if p]
        if parts:
            history_summary = " · ".join(parts)
    text = await talk_answer(request.query, history_summary=history_summary)
    if not text:
        # LLM이 꺼져 있거나 실패 — 종전 안내로 돌아간다. 상태 코드도 원래 것을
        # 그대로 쓴다(엔진 404를 400으로 바꾸면 호출부의 분기가 흐트러진다).
        raise HTTPException(status_code=fallback_status, detail=fallback_detail)
    return AgentAskResponse(
        query=request.query,
        address="",
        business_type="general",
        business_label="",
        steps=[AgentStep(key="talk", label="질문 이해", detail="위치를 특정하지 않고 답변")],
        site=None,
        answer=text,
        answer_source="llm",
        mode="talk",
        # 위치를 물어 다시 분석으로 이어 가도록, 뒤이을 질문을 함께 준다.
        # 2026-11-06: 남의 예시(신촌역)가 아니라 질문 틀이다 — 화면은 누르면 입력창에
        # 틀만 채우고 보내지 않는다(사용자가 ○○를 자기 장소·업종으로 바꾼다).
        suggested_questions=[
            "○○역 근처에 ○○ 열어도 괜찮을까?",
            "○○동 30평 월세 300이면 계약해도 될까?",
        ],
    )


# ─────────────────────── 도구 루프 경로 (settings.agent_loop_enabled) ───────────────────────
#
# 종전 경로는 도구를 요청 시작에 한 번 정하고 끝이다. 여기서는 모델이 analyze를
# 직접 부르고, 결과를 보고 한 번 더 고칠 수 있다(services/agent_loop.py). 실행
# 자체는 기존 함수(_resolve_point·run_site_score·_run_tool)를 그대로 쓴다 —
# 숫자를 내는 곳은 하나로 유지한다.

# 모델에게 고르게 할 도구. 값이 없으면 못 도는 것(decide·simulate처럼 입력이
# 복잡한 것)은 아직 노출하지 않는다 — 인자를 지어내면 판정이 틀린다.
_LOOP_TOOLS = (
    "lease_verdict",
    "purchase_verdict",
    "profitability",
    "trends",
    "real_competition",
    "cannibalization",
    "area_insights",
    "property_market",
    "isochrone",
    "lease_feasibility",
    "industry_profile",
)


async def _loop_analyze(args: dict[str, Any], *, question: str) -> Observation:
    """모델이 부른 analyze 한 건 — 좌표 확정 → 입지 점수 → 고른 판정들."""
    place = str(args.get("place") or "").strip()
    if not place:
        return Observation(facts="", error="place가 비어 있다")
    business_word = str(args.get("business") or "").strip()
    radius_m: RadiusM = 1000 if int(args.get("radius_m") or 500) == 1000 else 500
    params: dict[str, float] = {}
    for key, value in (args.get("params") or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            params[str(key)] = float(value)
    wanted = [t for t in (args.get("tools") or []) if t in _TOOLS and t != "site_score"]

    point = await _resolve_point(place)
    if point is None:
        return Observation(
            facts="",
            error=f"'{_shorten(place)}'의 좌표를 찾지 못했다. 서울 안의 지명·행정동·역 이름만 된다.",
        )

    tree = await get_upjong_tree()
    business_type = _keyword_business_type(business_word) if business_word else "general"
    upjong: dict[str, Any] | None = None
    hit = _specific_upjong_beats_preset(business_type, tree, business_word) if business_word else None
    if hit is not None:
        upjong = hit[0]
    business_label = (
        (upjong or {}).get("label") or _BUSINESS_LABELS.get(business_type) or business_word or "일반"
    )
    intent = _Intent(
        address=point.label,
        business_type=business_type,
        upjong=upjong,
        places=[point.label],
        params=params,
    )

    site = await run_site_score(
        SiteScoreRequest(
            lat=point.lat,
            lng=point.lng,
            business_type=business_type,
            inds_lcls_cd=(upjong or {}).get("inds_lcls_cd"),
            inds_mcls_cd=(upjong or {}).get("inds_mcls_cd"),
            inds_scls_cd=(upjong or {}).get("inds_scls_cd"),
            radius_m=radius_m,
            include_ai_explanation=False,
        )
    )

    tool_results: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    for tool in wanted[:3]:
        spec = _TOOLS[tool]
        missing = [need for need in spec.required if need not in params]
        if missing:
            # 값이 없으면 부르지 않는다. 모델이 무엇이 없어서 못 돌았는지 알아야
            # 사용자에게 되물을 수 있다.
            notes.append(f"{tool}: {', '.join(missing)} 값이 없어 실행하지 못했다")
            continue
        try:
            _, result = await _run_tool(tool, intent, [point], radius_m)
        except Exception as exc:  # noqa: BLE001 - 도구 하나가 죽어도 나머지는 낸다
            notes.append(f"{tool}: 실행 실패({str(exc)[:80]})")
            continue
        tool_results[tool] = result.model_dump() if hasattr(result, "model_dump") else dict(result or {})

    facts = _facts_for_answer(
        site,
        place=point.label,
        business_label=business_label,
        radius_m=radius_m,
        question=question,
        tool=(next(iter(tool_results)) if tool_results else "site_score"),
        tool_results=tool_results or None,
    )
    # 무엇을 중심으로 계산했는지 모델에게 밝힌다. 이게 없어서 "신촌 샐러디집에
    # 카페 차려볼까"라는 질문에 모델이 place="신촌동"으로 낮춰 부르고도 그 사실을
    # 몰랐고, 답에는 "신촌동 반경 500m 평균 데이터라 그 자리는 확인되지 않는다"는
    # 변명만 남았다(소유자 지적). 대표점이면 다시 부를 수 있다고 알려 준다.
    facts += (
        f"\n\n분석 중심: '{point.label}' 행정동 대표점(그 동네 점포 무게중심)이다. "
        "사용자가 특정 점포·건물을 기준으로 물었다면 이 중심은 그 자리가 아니다 — "
        "그 상호·건물명을 place에 그대로 넣어 한 번 더 부르면 그 자리를 중심으로 "
        "다시 계산한다."
        if point.from_dong
        else f"\n\n분석 중심: '{point.label}' 지점 좌표(반경 {radius_m}m)."
    )
    # 2026-11-06: 적재만 해 두고 안 쓰던 표들(개폐업·상권변화·집객시설·공실·앵커·
    # 건축허가·정비구역·역 규모). 섹션마다 따로 실패하고, 하나도 없으면 줄이 없다.
    transit = getattr(site, "transit", None)
    extras = await run_in_threadpool(
        area_extra_lines,
        point.lat,
        point.lng,
        radius_m,
        station_name=(transit.station_name if transit is not None else None),
    )
    if extras:
        facts += "\n\n주변 상권 동태(추가 데이터):\n" + "\n".join(f"- {line}" for line in extras)
    if notes:
        facts += "\n\n실행하지 못한 것:\n" + "\n".join(f"- {n}" for n in notes)
    return Observation(
        facts=facts,
        payload={
            "site": site,
            "tool_results": tool_results,
            "address": point.label,
            "business_type": business_type,
            "business_label": business_label,
            "upjong": upjong,
            "params": params,
        },
    )


async def _loop_response(request: "AgentAskRequest", result: Any) -> AgentAskResponse | None:
    """루프 결과를 응답으로. 답이 비었으면 None(라우터가 기존 경로로 간다)."""
    answer = (result.answer or "").strip()
    if not answer:
        return None
    used = [o for o in result.observations if not o.error and o.payload]
    last = used[-1].payload if used else None
    steps = [AgentStep(key="loop", label="질문 이해", detail=f"도구 선택 {result.rounds}회")]
    if last is None:
        return AgentAskResponse(
            query=request.query,
            address="",
            business_type="general",
            business_label="",
            steps=steps,
            site=None,
            answer=answer,
            answer_source="llm",
            mode="talk",
        )
    tool_results = last["tool_results"]
    return AgentAskResponse(
        query=request.query,
        address=last["address"],
        business_type=last["business_type"],
        business_label=last["business_label"],
        steps=steps + [AgentStep(key="site_score", label="종합 점수 산출")],
        site=last["site"],
        answer=answer,
        answer_source="llm",
        mode="analyze",
        tool=(next(iter(tool_results)) if tool_results else "site_score"),
        tool_result=(next(iter(tool_results.values())) if tool_results else None),
        tools=list(tool_results),
        tool_results=tool_results,
        parameters=last["params"],
        upjong=last["upjong"],
    )


def _founder_card(site: SiteScoreResponse, params: dict[str, float]) -> dict[str, Any] | None:
    """site_score 결과에서 창업자 결정 카드를 만든다. 엔진 숫자만 쓴다 — LLM 없음.

    월세 상한은 후보 비교(decision.evaluate_candidate)와 같은 길로 낸다:
    상권 점포당 매출 × 입지보정 → 업종별 부담률·손익 역산 중 낮은 쪽.
    """
    try:
        bench = site.sales_benchmark
        expected, _ = expected_monthly_sales(
            per_store_monthly_won=bench.per_store_monthly_won if bench else None,
            trdar_nm=bench.trdar_nm if bench else "",
            integration_top_pct=site.segment.integration_top_pct,
            scope=bench.scope if bench else "trdar",
        )
        rent_cap, _ = max_acceptable_rent(
            expected_sales_man=expected,
            archetype=archetype_for_mcls(site.inds_mcls_cd),
        )
        comp = site.competition
        verdict = founder_verdict(
            integration_top_pct=site.segment.integration_top_pct,
            choice_top_pct=site.segment.choice_top_pct,
            competition_intensity=(comp.intensity_label or None) if comp else None,
            same_category_count=comp.same_category_count if comp else None,
            rent_cap_man=rent_cap,
            monthly_rent_man=params.get("monthly_rent_man"),
        )
        card = asdict(verdict)
        if bench is not None and bench.scope == "dong":
            card["cautions"].append("지정 상권 밖이라 행정동 평균 매출로 월세 상한을 냈어요 — 정밀도가 낮아요.")
        return card
    except Exception:  # 카드가 실패해도 답은 나가야 한다.
        logger.exception("창업자 결정 카드 계산 실패")
        return None


# 앞 대화를 모델에 넘길 때의 상한. 턴이 길어지면 매 질문마다 입력 토큰이 쌓인다.
_HISTORY_TURNS = 4
_HISTORY_ANSWER_CHARS = 1500


def _history_messages(history: list[AgentHistoryTurn]) -> list[dict[str, str]]:
    """앞 대화 턴들을 상담 모델의 대화 메시지로(오래된 순, 최근 몇 턴만).

    답 본문이 없는 턴(옛 화면, 답을 못 쓴 턴)은 무엇을 분석했는지만 남긴다.
    """
    messages: list[dict[str, str]] = []
    for turn in history[-_HISTORY_TURNS:]:
        query = (turn.query or "").strip()
        if not query:
            continue
        messages.append({"role": "user", "content": query})
        answer = (turn.answer or "").strip()
        if len(answer) > _HISTORY_ANSWER_CHARS:
            answer = answer[:_HISTORY_ANSWER_CHARS] + "…(이하 생략)"
        if not answer:
            target = " · ".join(p for p in (turn.address, turn.business_label) if p)
            answer = f"({target} 기준으로 분석 결과를 보여 드렸습니다.)" if target else ""
        if answer:
            messages.append({"role": "assistant", "content": answer})
    return messages


def _sse(payload: dict[str, Any]) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


@router.post("/ask/stream")
async def agent_ask_stream(request: AgentAskRequest) -> StreamingResponse:
    """AI 상담 — 모델이 엔진을 도구로 부르며 답을 글자 단위로 흘린다(SSE).

    2026-11-06 개편(소유자 지적: 일반 GPT보다 답을 못하고 정해진 틀로만 답한다). 화면은
    결정 카드(`/ask?fast=1`)와 이 스트림을 동시에 연다. 카드는 2초 안에 서고, 글은 이
    스트림이 쓴다.

    이벤트(`data: {json}`):
      - {"type": "status", "text"}: 지금 하는 일
      - {"type": "delta", "text"}: 답 글자 조각
      - {"type": "reset"}: 지금까지 흘린 글을 지운다
      - {"type": "done", "response": AgentAskResponse | null, "fallback": bool}
        fallback=true면 이 경로를 못 썼다 — 화면이 종전 경로(/answer brief 등)로 쓴다.
    """
    if request.radius_m not in (500, 1000):
        raise HTTPException(status_code=400, detail="radius_m must be 500 or 1000")

    history_summary = ""
    if request.history:
        last_turn = request.history[-1]
        history_summary = " · ".join(p for p in (last_turn.address, last_turn.business_label) if p)

    async def events():
        result = None
        try:
            async for event in stream_agent_loop(
                request.query,
                analyze=lambda a: _loop_analyze(a, question=request.query),
                tool_names=list(_LOOP_TOOLS),
                tool_labels={name: _TOOLS[name].label for name in _LOOP_TOOLS},
                capabilities=CAPABILITIES,
                history=_history_messages(request.history),
                history_summary=history_summary,
            ):
                if event.type == "done":
                    result = event.result
                elif event.type == "reset":
                    yield _sse({"type": "reset"})
                else:
                    yield _sse({"type": event.type, "text": event.text})
        except Exception as exc:  # noqa: BLE001 - 스트림이 죽으면 화면이 폴백한다
            logger.warning("agent stream 실패: %s", exc)
            yield _sse({"type": "done", "response": None, "fallback": True})
            return
        response = await _loop_response(request, result) if result is not None else None
        if response is not None and response.site is not None and response.founder is None:
            response.founder = _founder_card(response.site, response.parameters)
        yield _sse(
            {
                "type": "done",
                "response": response.model_dump(mode="json") if response is not None else None,
                "fallback": response is None,
            }
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/ask", response_model=AgentAskResponse)
async def agent_ask(request: AgentAskRequest) -> AgentAskResponse:
    response = await _agent_ask_core(request)
    if response.site is not None and response.founder is None:
        response.founder = _founder_card(response.site, response.parameters)
    return response


async def _agent_ask_core(request: AgentAskRequest) -> AgentAskResponse:
    if request.radius_m not in (500, 1000):
        raise HTTPException(status_code=400, detail="radius_m must be 500 or 1000")

    # 도구를 루프로 돌리는 경로가 켜져 있으면 먼저 시도한다. 모델이 없거나
    # 도구 호출을 지원하지 않으면 None이 오고, 그때 아래 기존 경로로 내려간다.
    # fast 요청(결정 카드만)은 루프를 타지 않는다.
    if getattr(settings, "agent_loop_enabled", False) and not request.fast and request.loop:
        history_summary = ""
        if request.history:
            last_turn = request.history[-1]
            history_summary = " · ".join(
                p for p in (last_turn.address, last_turn.business_label) if p
            )
        loop = await run_agent_loop(
            request.query,
            analyze=lambda a: _loop_analyze(a, question=request.query),
            tool_names=list(_LOOP_TOOLS),
            tool_labels={name: _TOOLS[name].label for name in _LOOP_TOOLS},
            capabilities=CAPABILITIES,
            history=_history_messages(request.history),
            history_summary=history_summary,
        )
        if loop is not None:
            response = await _loop_response(request, loop)
            if response is not None:
                return response

    # 업종 표는 로컬 JSON에서 한 번 읽고 캐시된다(외부 호출 없음).
    tree = await get_upjong_tree()
    intent = await _extract_intent(request.query, tree)
    if request.context is not None:
        # 직전 턴에서 값이 모자라 되묻고 끝났던 도구를 이어서 완성한다 — 이번
        # 문장이 그 값 하나만 담고 있어도 지점·업종을 다시 말할 필요가 없다.
        intent = _merge_pending_context(intent, request.context)
        intent = _apply_bare_fallback(intent, request.context, request.query)
    # 이번 문장에 장소가 없으면 직전 대화 턴에서 이어받는다("월세 300이면?").
    intent = _continue_from_history(intent, request.history)
    address, business_type = intent.address, intent.business_type
    if not address:
        # 위치가 없다고 되묻고 끝내지 않는다 — 개념·방법·범위를 묻는 질문이
        # 대부분 여기로 들어온다.
        return await _talk_response(request, fallback_detail=_NEED_PLACE_DETAIL)
    # 업종만 친 질의("카페")는 추출기가 걷어낼 게 없어 업종어를 그대로 주소로
    # 넘긴다. 그러면 카카오 키워드 검색이 아무 '카페'나 하나 집어와 엉뚱한 곳을
    # 분석하거나, 도로망을 못 찾아 영어 404("No road segments found...")가 그대로
    # 사용자에게 노출됐다. 지오코딩에 보내기 전에 되돌린다.
    if address.strip().lower() in _CATEGORY_WORDS:
        return await _talk_response(
            request, fallback_detail=_NEED_PLACE_DETAIL, business_type=business_type
        )
    # 광역어뿐인 장소(「서울」, 「서울에서」)는 한 자리가 아니다 — 지오코딩하면
    # 시청 좌표가 나온다. 「어디가 좋냐」는 상담으로 넘긴다(§9-2).
    if _is_broad_region(address):
        return await _talk_response(request, fallback_detail=_NEED_PLACE_DETAIL)

    started = time.monotonic()

    # '신촌'처럼 동네 이름만 물으면 Kakao 주소검색이 행정동 기하 중심을 주는데,
    # 신촌동은 그 지점이 연세대 캠퍼스 안이라 "카페 0곳 · 경쟁 매우 낮음"이라는
    # 정반대 분석이 나왔다. 동네로 특정되는 질의는 자동완성과 같은 상권 중심을
    # 쓴다(services/dong_center). 지번이 붙은 주소나 애매한 이름은 손대지 않고
    # 기존 지오코딩에 맡긴다.
    dong = await run_in_threadpool(resolve_unique_dong, address)
    resolved_label = dong.name if dong else address

    # 도구를 고른다. 규칙(문장에서 뽑힌 값)이 기본이고, LLM 판단은 그 도구가
    # 요구하는 값이 실제로 다 있을 때만 받아들인다 — 없는 값으로 도구를 부르면
    # 422가 나거나 0으로 계산돼 거짓 결론이 된다.
    extra_points: list[_Point] = []
    if len(intent.places) > 1:
        # "연남동, 신촌"을 한 주소로 지오코딩하면 둘 다 아닌 점이 잡힌다.
        resolved = await asyncio.gather(
            *(_resolve_point(p) for p in intent.places[1:4])
        )
        extra_points = [pt for pt in resolved if pt is not None]

    notice = ""
    pending: AgentPendingContext | None = None
    # 문장 하나가 여러 판단을 요구할 수 있다("월세 300에 계약해도 되고 경쟁
    # 구조도 봐줘") — 우선순위 순 후보를 전부 뽑는다. 맨 앞이 주 도구다.
    candidates = _rule_tools(
        request.query,
        intent.params,
        1 + len(extra_points),
        business_type=intent.business_type,
        target_model=intent.target_model,
        franchise_brand=intent.franchise_brand,
    )
    # "site_score"가 candidates에 있다는 건 다른 신호가 하나도 안 잡혀
    # _rule_tools가 fallback으로 돌아왔다는 뜻이다(신호 매칭 분기는 절대
    # "site_score"를 넣지 않는다) — trends·real_competition은 이때 곁들인
    # 것이지 사용자가 직접 물은 게 아니므로, 아래에서 주 도구(tool)를 정할 때
    # 이 둘만 성공했다면 site_score로 남겨 둔다(_DEPTH_ONLY_TOOLS 참고).
    is_plain_site_question = candidates == ["site_score", "trends"]
    # 이 업종이 real_competition을 지원하면 경쟁 구조까지 곁들인다 —
    # _rule_tools는 문장만 보고 업종 코드 체계를 모르므로 여기서 결정한다
    # (_real_competition_supported 참고).
    if is_plain_site_question and _real_competition_supported(intent):
        candidates = [*candidates, "real_competition"]
    # decide는 여러 후보 지점을 한 번에 비교하는 도구라 다른 단일 지점용 도구와
    # 섞이면 안 된다(그 도구들은 points[0]만 본다) — 비교 질의는 그대로 둔다.
    if candidates != ["decide"] and intent.llm_tool and intent.llm_tool != candidates[0]:
        ok, _ = _tool_ready(intent.llm_tool, intent, [_Point(0, 0, "")] + extra_points)
        if ok:
            # LLM이 고른 도구를 앞세우되, 규칙이 찾은 나머지 신호는 버리지 않는다.
            candidates = [intent.llm_tool] + [t for t in candidates if t != intent.llm_tool]

    # 각 후보가 실제로 부를 수 있는지 미리 걸러 둔다(값 부족은 이유를 밝히고
    # 건너뛴다). 예산 안에서 돌 것만 남기고, 넘치는 나머지는 notice로 알린다.
    dummy_points = [_Point(0, 0, "")] + extra_points
    ready_tools: list[str] = []
    missing_hints: list[str] = []
    not_ready: dict[str, str] = {}
    capped_out: list[str] = []
    for t in candidates:
        if t == "site_score":
            continue
        ok, hint = _tool_ready(t, intent, dummy_points)
        if not ok:
            missing_hints.append(hint)
            not_ready[t] = hint
            continue
        if len(ready_tools) >= _MAX_CHAINED_TOOLS:
            capped_out.append(t)
            continue
        ready_tools.append(t)

    # 문장이 실제로 가리키던 도구(LLM이 읽은 의도, 또는 직전 턴에서 되묻고
    # 있던 도구)가 값이 모자라 못 돌면, 다음 문장이 그 값 하나만 담고 와도
    # 이어서 계산할 수 있도록 지금까지 뽑은 것을 pending에 남긴다. 다른 도구가
    # 성공했어도 남긴다 — 이 질문에 답 못한 부분을 조용히 묻지 않는 것과 같은
    # 이유다(missing_hints가 notice로 알리는 것의 짝).
    #
    # not_ready만 보면 안 된다. 규칙(_rule_tools)이 그 도구를 후보로 아예 못
    # 찾은 경우("잘 모르겠어요" 같은 문장) llm_tool·context.tool은 후보 목록
    # 밖에 있어서 not_ready에도 없다 — 그때는 여기서 직접 확인한다.
    for wanted in (intent.llm_tool, request.context.tool if request.context else None):
        if not wanted or wanted not in _TOOLS or wanted == "site_score":
            continue
        if wanted in ready_tools or wanted in capped_out:
            break  # 어차피 돌거나 예산 초과 안내가 나간다 — 되물을 게 없다
        hint = not_ready.get(wanted)
        if hint is None:
            ok, hint = _tool_ready(wanted, intent, dummy_points)
            if ok:
                break  # 돌 수 있는데 후보에 안 든 것 — 되묻기 대상이 아니다
            missing_hints.append(hint)
        pending = AgentPendingContext(
            tool=wanted,
            address=address,
            business_type=intent.business_type,
            upjong=intent.upjong,
            params=intent.params,
            brand=intent.brand,
            property_type=intent.property_type,
            target_model=intent.target_model,
            priorities=intent.priorities,
            reach_minutes=intent.reach_minutes,
            reach_mode=intent.reach_mode,
            franchise_brand=intent.franchise_brand,
        )
        break

    # 도구를 부르려면 좌표가 먼저 있어야 한다. site_score가 끝나기를 기다렸다가
    # 도구를 돌리면 시간이 더해져 5초 예산을 넘긴다(실측 8.0초). 좌표를 먼저
    # 확정해 두 호출을 **나란히** 돌리면 전체 시간은 둘 중 느린 쪽이 된다.
    primary: _Point | None = None
    if dong is not None:
        primary = _Point(lat=dong.lat, lng=dong.lng, label=dong.name)
    elif ready_tools:
        primary = await _resolve_point(address)
        if primary is None:
            # 좌표를 못 찾았다고 끝내지 않고 글로 답한다(서울 밖 질문·개념 질문).
            return await _talk_response(
                request,
                fallback_detail=(
                    f"「{_shorten(address)}」에서 분석할 위치를 찾지 못했어요. "
                    f"{_SEOUL_ONLY} " + _NEED_PLACE_DETAIL
                ),
            )
        resolved_label = primary.label

    tool_tasks: dict[str, asyncio.Task[tuple[str, Any]]] = {}
    # fast 요청은 site_score만 — 도구는 뒤따르는 전체 요청이 돌린다. 되묻기
    # (pending·notice)는 그대로 둔다: 값이 모자란 사정은 빠른 답에도 알려야 한다.
    if request.fast:
        ready_tools = []
    if ready_tools and primary is not None:
        for t in ready_tools:
            tool_tasks[t] = asyncio.ensure_future(
                _run_tool(t, intent, [primary] + extra_points, request.radius_m)
            )

    # 이미 있는 엔진을 그대로 실행 — 지오코딩·도로망·수요·경쟁·점수 일괄.
    # 위치·도로 관련 4xx(HTTPException)는 그대로 올려 사용자에게 정확히 알린다.
    # 그 밖의 예기치 못한 크래시(일부 업종은 분석 파이프라인이 아직 미완성이라
    # 500이 난다)는 삼켜서, 무서운 서버 오류 대신 "아직 지원 안 함"으로 안내한다.
    try:
        site = await run_site_score(
            SiteScoreRequest(
                address=None if primary else address,
                lat=primary.lat if primary else None,
                lng=primary.lng if primary else None,
                business_type=business_type,
                # 프리셋에 없는 업종은 SBIZ 코드로 직접 넘긴다 — 엔진이 같은
                # 경로로 동일업종을 세고 업종 프로파일까지 연결한다.
                inds_lcls_cd=(intent.upjong or {}).get("inds_lcls_cd"),
                inds_mcls_cd=(intent.upjong or {}).get("inds_mcls_cd"),
                inds_scls_cd=(intent.upjong or {}).get("inds_scls_cd"),
                radius_m=request.radius_m,
                include_ai_explanation=False,
                priorities=intent.priorities,
            )
        )
    except HTTPException as exc:
        for task in tool_tasks.values():
            task.cancel()
        # 위치·도로 관련 4xx는 그대로 올리되, 내부 영어 메시지("No road segments
        # found within 500m of the point")를 사용자에게 보여 주지는 않는다.
        # '안녕' 한 마디에 저 문장이 돌아오면 무슨 뜻인지 알 길이 없다.
        if exc.status_code in (400, 404):
            # 엔진이 그 지점을 못 다뤘다(도로망 없음 등). 여기서도 글로 답한다 —
            # 내부 영어 메시지("No road segments found…")를 사용자에게 보이지 않는다.
            return await _talk_response(
                request,
                fallback_status=exc.status_code,
                fallback_detail=(
                    # 추출된 지명을 그대로 되돌려 주는데, '카페'를 60번 붙인 입력에서는
                    # 그 긴 문자열이 통째로 에러 메시지에 실려 화면과 로그를 덮었다.
                    f"「{_shorten(address)}」에서 분석할 위치를 찾지 못했어요. "
                    # 지역을 정확히 말한 질의('부산 서면 카페')에 지역을 되묻기만
                    # 하면 무엇이 잘못됐는지 알 수 없다.
                    f"{_SEOUL_ONLY} " + _NEED_PLACE_DETAIL
                ),
            )
        raise
    except Exception as exc:
        for task in tool_tasks.values():
            task.cancel()
        logger.warning(
            "agent_ask: site_score가 business_type=%s에서 실패 — %s", business_type, exc
        )
        label = (
            (intent.upjong or {}).get("label")
            or _BUSINESS_LABELS.get(business_type)
            or business_type
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{label}' 업종은 아직 분석을 지원하지 않아요. "
                "다른 업종으로 다시 물어봐 주세요."
            ),
        ) from exc

    # 프리셋 밖 업종은 엔진이 붙인 이름을 그대로 쓴다('일반'으로 뭉개지 않는다).
    if intent.upjong:
        business_label = site.business_label or str(intent.upjong.get("label") or "")
    else:
        business_label = _BUSINESS_LABELS.get(business_type, site.business_label)

    # 나란히 돌던 도구들을 각자 예산 안에서 거둔다. 넘기면 그 도구만 버리고
    # 나머지·기본 분석은 그대로 돌려준다 — 도구 하나 때문에 5초 하드룰 전체가
    # 깨지면 안 된다.
    tool_results: dict[str, dict[str, Any]] = {}
    executed_tools: list[str] = []
    tool_steps: list[AgentStep] = []
    dropped_notices: list[str] = []
    for t, task in tool_tasks.items():
        remaining = _TOOLS[t].budget_s - (time.monotonic() - started)
        try:
            # 이미 끝난 작업은 시계와 무관하게 거둔다. 도구들은 나란히 돌므로
            # 가벼운 도구(analogs 0.4초)가 무거운 도구 뒤에서 수확 차례를
            # 기다리다가, 완성돼 앉아 있는 결과를 "예산 초과"로 버리는 일이
            # 실제로 났다(2026-08-08). 예산은 아직 도는 작업을 끊는 기준이지
            # 끝난 결과를 버리는 기준이 아니다.
            if task.done():
                _, result = await task
            elif remaining <= 0.2:
                raise TimeoutError("도구 예산 초과")
            else:
                _, result = await asyncio.wait_for(task, timeout=remaining)
            if result is None:
                # territory(배달권역 없는 업종)와 franchise_brand_detail(브랜드를
                # 못 찾음)만 이 경로로 None을 준다. 시간초과가 아니므로 아래
                # "계산이 오래 걸려" 안내를 쓰면 거짓이 된다. 이유를 정확히 알린다.
                if t == "franchise_brand_detail":
                    dropped_notices.append(
                        f"'{intent.franchise_brand}' 가맹정보를 찾지 못했어요."
                    )
                else:
                    dropped_notices.append(
                        f"이 업종은 '{_TOOLS[t].label}' 대상이 아니에요(예: 학원 등 "
                        "배달권역이 없는 업종)."
                    )
            else:
                # analogs처럼 pydantic 모델이 아니라 dict를 그대로 주는 도구도
                # 있다 — 타입만 다르고 취급은 같다.
                tool_results[t] = (
                    result if isinstance(result, dict) else result.model_dump(mode="json")
                )
                executed_tools.append(t)
                tool_steps.append(AgentStep(key=t, label=_TOOLS[t].label))
        except Exception as exc:  # 라우팅 실패로 500을 내지 않는다
            task.cancel()
            logger.warning("agent_ask: %s 도구 실행 실패 — %r", t, exc)
            dropped_notices.append(
                f"'{_TOOLS[t].label}'은 계산이 오래 걸려 이번 응답에는 담지 못했어요."
            )

    # 성공한 도구가 하나도 없으면 기본 입지 분석(site_score)만 남는다 — 기존
    # 단일 도구 시절과 같은 폴백이다. 하나 이상 성공했으면 맨 앞이 주 도구다
    # (tool/tool_result는 그 하나를 가리켜 기존 호출자와 하위호환을 지킨다).
    # 다만 순수 입지 질문에 곁들인 depth-only 도구(trends·real_competition)만
    # 성공했으면 주 도구는 여전히 site_score다 — 사용자는 그 둘을 물은 적이
    # 없으니 "주로 실행한 도구"라고 내세우면 안 된다(_DEPTH_ONLY_TOOLS 참고).
    primary_tool = next(
        (t for t in executed_tools if not (is_plain_site_question and t in _DEPTH_ONLY_TOOLS)),
        None,
    )
    if primary_tool is not None:
        tool = primary_tool
        tool_result: dict[str, Any] | None = tool_results[tool]
    else:
        tool = "site_score"
        tool_result = None

    notice_parts: list[str] = []
    if missing_hints:
        # 안전 폴백: 부를 수 없는 도구 때문에 실패하지 않는다. 대신 무엇이 더
        # 필요한지 정확히 알려 준다 — 다른 도구가 성공했어도 이 질문에 답 못한
        # 부분은 조용히 묻지 않는다.
        notice_parts.append(missing_hints[0])
    tail = "" if executed_tools else " 아래는 그 지점의 기본 입지 분석입니다."
    notice_parts.extend(f"{msg}{tail}" for msg in dropped_notices)
    # 2026-11-06: 「기본 입지 분석과 함께 성장 신호·업종 추이·…까지 계산해 답했어요」
    # 안내를 뺐다 — 사용자 테스트(SPEC_ANSWER_DECISION §9-2)에서 내부 도구 이름 나열로
    # 읽혔고, 화면은 notice를 경고 색으로 그려 고장처럼 보였다. 무엇을 계산했는지는
    # 근거 접기 안의 카드가 보여 준다.
    if capped_out:
        capped_labels = "·".join(_TOOLS[t].label for t in capped_out)
        notice_parts.append(
            f"{capped_labels}은(는) 한 번에 처리할 수 있는 도구 수(최대 "
            f"{_MAX_CHAINED_TOOLS}개)를 넘어 이번엔 빠졌어요."
        )
    notice = " ".join(p for p in notice_parts if p)

    # 엔진은 2~3초면 끝나는데 LLM 서술이 2~3초를 더 얹어 5초 예산을 넘긴다.
    # include_answer=False로 부르면 분석만 먼저 돌려주고, 화면은 그걸 즉시
    # 그린 뒤 /api/agent/answer로 서술만 따로 받아 채운다.
    if request.include_answer:
        answer, answer_source = await _write_answer(
            site,
            place=resolved_label,
            business_label=business_label,
            radius_m=request.radius_m,
            question=request.query,
            tool=tool,
            tool_result=tool_result,
            tool_results=tool_results,
        )
    else:
        answer, answer_source = "", "pending"

    # 비교 도구가 실제로 두 곳을 본 경우에는 "한 곳만 봤다"고 사과하지 않는다.
    if not notice and tool != "decide":
        notice = _comparison_notice(request.query, resolved_label)

    return AgentAskResponse(
        query=request.query,
        # 동네로 특정됐으면 실제로 분석한 곳('서대문구 신촌동')을 그대로 보여 준다.
        address=resolved_label,
        business_type=business_type,
        business_label=business_label,
        # 실제로 실행한 것만 트레이스에 남긴다 — 도구가 달라지면 단계도 달라진다.
        steps=_build_steps(site) + tool_steps,
        site=site,
        answer=answer,
        answer_source=answer_source,
        notice=notice,
        pending=pending,
        tool=tool,
        tool_result=tool_result,
        tools=executed_tools,
        tool_results=tool_results,
        parameters=intent.params,
        priorities=intent.priorities,
        upjong=intent.upjong,
        suggested_questions=_suggested_questions(executed_tools),
    )


@router.post("/answer", response_model=AgentAnswerResponse)
async def agent_answer(request: AgentAnswerRequest) -> AgentAnswerResponse:
    """분석 결과를 받아 서술만 생성한다 (2단계 렌더의 두 번째 단계).

    /ask?include_answer=False로 받은 분석을 화면에 먼저 그린 뒤, 그 결과를 그대로
    보내 서술을 채운다. 엔진을 다시 돌리지 않으므로 여기 드는 시간은 LLM 몫뿐이다.
    숫자는 이미 엔진이 정한 것이고 이 엔드포인트는 문장만 만든다 — 저장하거나
    판정을 바꾸지 않는다.
    """
    answer, answer_source = await _write_answer(
        request.site,
        place=request.place,
        business_label=request.business_label,
        radius_m=request.radius_m,
        question=request.query,
        tool=request.tool,
        tool_result=request.tool_result,
        tool_results=request.tool_results or None,
        history=request.history or None,
        brief=request.brief,
    )
    executed = set(request.tool_results or {})
    executed.add(request.tool)
    return AgentAnswerResponse(
        answer=answer,
        answer_source=answer_source,
        suggested_questions=_suggested_questions(executed),
    )
