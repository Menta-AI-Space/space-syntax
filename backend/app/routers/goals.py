"""에이전트 진입 화면의 나머지 4개 목표 엔드포인트.

  POST /api/lease-verdict   — 이 매물을 계약해도 되는가
  POST /api/purchase-verdict— 이 상가를 매입해도 되는가
  POST /api/diagnose        — 우리 매장 매출이 왜 낮은가
  POST /api/area-search     — 이 동네에서 어디에 열어야 하는가

설계 원칙은 /api/decide(app/routers/decide.py + app/services/decision.py)와
같고, 예외는 없다:
**모든 숫자·순위·판정·임계값은 파이썬이 결정론적으로 계산한다.**
LLM(app/services/llm_client.py)은 이미 결정된 결론을 한국어 문장으로 옮길 뿐이며,
꺼져 있거나 실패하면 같은 결론이 규칙 템플릿으로 나오고 narrative_source가
"rule"이 된다. 같은 입력은 언제나 같은 판정·같은 숫자를 낸다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, BeforeValidator, Field, field_validator

from app.config import settings
from app.services import diagnosis as dx
from app.services.analysis_cache import cap_analysis_segments
from app.services.area_search import (
    MAX_CANDIDATES,
    CandidateSpot,
    disambiguate_labels,
    sample_candidate_spots,
    search_radius_for,
)
from app.services.decision import (
    VERDICT_LABELS,
    CandidateInput,
    compare_candidates,
)
from app.services.decision_narrative import rent_cap_phrase
from app.services.depthmapx_engine import compute_depthmapx_metrics
from app.services.geocoding import GeocodingError, geocode_address, reverse_geocode
from app.services.graph_builder import build_graph
from app.services.lease_feasibility import get_archetype_defaults
from app.services.living_pop import fetch_living_pop_profile
from app.services.profile_resolver import ProfileResolver
from app.services.space_syntax import WALK, compute_metrics
from app.services.spatial_query import fetch_roads_in_radius
from app.services.trdar_sales_local import sales_trend_for_trdar, trdar_for_point
from app.services.verdicts import (
    PYEONG_TO_SQM,
    adjust_lease_verdict,
    analyze_site,
    build_lease_actions,
    build_lease_risks,
    build_purchase_actions,
    build_purchase_risks,
    build_purchase_strengths,
    comparables_payload,
    compute_purchase_economics,
    decide_purchase_verdict,
    fetch_comparables,
    lease_break_even,
    max_acceptable_price,
    narrate,
    negotiation_terms,
    price_premium_pct,
    rule_lease_headline,
    rule_purchase_headline,
)

router = APIRouter(prefix="/api", tags=["goals"])
logger = logging.getLogger(__name__)

#: LLM 서술 대기 상한. 초과하면 규칙 문장으로 대체한다 (decide.py와 동일).
NARRATIVE_TIMEOUT_S = 12.0


def _null_as(default: Any) -> BeforeValidator:
    """비운 선택 항목을 null로 보내는 클라이언트를 받아 준다.

    기본값이 있어도 타입이 nullable이 아니면 pydantic은 명시적 null을 거부한다.
    폼에서 빈 칸을 null로 직렬화하는 건 정상적인 동작이라 서버가 흡수한다
    (안 그러면 관리비 한 칸을 비웠다고 422가 난다).
    """

    def _coerce(value: Any) -> Any:
        return default if value is None else value

    return BeforeValidator(_coerce)


class RadiusBase(BaseModel):
    """분석 반경 필드와 검증을 4개 요청 모델이 공유한다 (/api/decide와 같은 규칙)."""

    radius_m: int = Field(500, description="분석 반경 (500 또는 1000)")

    @field_validator("radius_m")
    @classmethod
    def _validate_radius(cls, v: int) -> int:
        if v not in (500, 1000):
            raise ValueError("radius_m must be 500 or 1000")
        return v


def _site_total_score(row: dict[str, Any]) -> float | None:
    return (row.get("evidence") or {}).get("site_score", {}).get("total_score")


async def _narrate_or_rule(payload: dict[str, Any], fallback: str) -> tuple[str, str, list[str]]:
    """(headline, source, warnings). 지연·실패는 전부 규칙 문장으로 흡수된다."""
    try:
        headline, source = await asyncio.wait_for(
            narrate(payload, fallback), timeout=NARRATIVE_TIMEOUT_S
        )
        return headline, source, []
    except TimeoutError:
        return fallback, "rule", ["AI 서술 응답이 지연되어 규칙 기반 요약으로 대체했습니다."]
    except Exception as exc:  # 서술 실패가 분석 결과를 죽이면 안 된다
        logger.warning("goal narration failed: %s", exc)
        return fallback, "rule", []


# ===========================================================================
# 1. POST /api/lease-verdict
# ===========================================================================


class LeaseVerdictRequest(RadiusBase):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    inds_lcls_cd: str | None = None
    inds_mcls_cd: str | None = None
    inds_scls_cd: str | None = None
    monthly_rent_man: float = Field(..., gt=0, le=100_000)
    maintenance_man: Annotated[float, _null_as(0)] = Field(0, ge=0, le=100_000)
    deposit_man: float | None = Field(None, ge=0, le=1_000_000)
    key_money_man: float | None = Field(None, ge=0, le=1_000_000)
    area_pyeong: float | None = Field(None, gt=0, le=10_000)
    floor: int | None = None
    brand: str | None = Field(None, max_length=40)
    label: str = Field("", max_length=120)


class NegotiationOut(BaseModel):
    target_rent_man: float | None = None
    rent_free_months: int | None = None
    max_key_money_man: float | None = None


class LeaseVerdictResponse(BaseModel):
    verdict: str
    verdict_label: str
    recommend_score: float
    expected_monthly_sales_man: float | None = None
    # 최근 분기 변동폭 기반 범위 [low, high] (만원) — 점 추정의 정직한 불확실성.
    expected_monthly_sales_range_man: list[float] | None = None
    expected_sales_basis: str = ""
    break_even_monthly_man: float | None = None
    rent_burden_pct: float | None = None
    appropriate_rent_low_man: float | None = None
    appropriate_rent_high_man: float | None = None
    rent_gap_man: float | None = None
    negotiation: NegotiationOut
    key_money_payback_months: float | None = None
    strengths: list[str] = []
    risks: list[str] = []
    actions: list[str] = []
    evidence: dict[str, Any] = {}
    headline: str = ""
    narrative_source: str = "rule"
    warnings: list[str] = []


@router.post("/lease-verdict", response_model=LeaseVerdictResponse)
async def lease_verdict(request: LeaseVerdictRequest) -> LeaseVerdictResponse:
    label = request.label or "이 매물"
    lease_cost_man = request.monthly_rent_man + request.maintenance_man

    # 적정 월세 상한은 '월 임차비(월세+관리비)' 기준이라 합계를 넘긴다.
    analysis = await analyze_site(
        lat=request.lat,
        lng=request.lng,
        label=label,
        inds_lcls_cd=request.inds_lcls_cd,
        inds_mcls_cd=request.inds_mcls_cd,
        inds_scls_cd=request.inds_scls_cd,
        radius_m=request.radius_m,
        brand=request.brand,
        monthly_cost_man=lease_cost_man,
        area_pyeong=request.area_pyeong,
        floor=request.floor,
    )
    row = analysis.row
    warnings = list(analysis.warnings)

    expected = row["expected_monthly_sales_man"]
    max_lease_cost = row["max_acceptable_rent_man"]

    metrics = lease_break_even(
        monthly_rent_man=request.monthly_rent_man,
        maintenance_man=request.maintenance_man,
        area_pyeong=request.area_pyeong,
        key_money_man=request.key_money_man,
        archetype=analysis.upjong.archetype,
        expected_sales_man=expected,
    )
    terms = negotiation_terms(
        monthly_rent_man=request.monthly_rent_man,
        maintenance_man=request.maintenance_man,
        max_lease_cost_man=max_lease_cost,
    )
    verdict, adjustments = adjust_lease_verdict(
        base_verdict=row["verdict"],
        monthly_operating_profit_man=metrics.monthly_operating_profit_man,
        key_money_payback_months=metrics.key_money_payback_months,
    )
    if request.key_money_man and metrics.key_money_payback_months is None:
        warnings.append(
            "예상 매출 기준 영업이익이 없거나 0 이하라 권리금 회수기간을 계산하지 못했습니다."
        )

    risks = build_lease_risks(
        metrics=metrics,
        adjustments=adjustments,
        base_risks=row["risks"],
        expected_sales_man=expected,
    )
    actions = build_lease_actions(
        terms=terms,
        metrics=metrics,
        current_rent_man=request.monthly_rent_man,
        key_money_man=request.key_money_man,
        base_actions=row["actions"],
        verdict=verdict,
    )

    evidence = dict(row["evidence"])
    evidence["lease"] = {
        "monthly_rent_man": request.monthly_rent_man,
        "maintenance_man": request.maintenance_man,
        "monthly_lease_cost_man": lease_cost_man,
        "deposit_man": request.deposit_man,
        "key_money_man": request.key_money_man,
        "area_pyeong": request.area_pyeong,
        "floor": request.floor,
        "max_lease_cost_man": max_lease_cost,
        "monthly_fixed_cost_man": metrics.monthly_fixed_cost_man,
        "labor_cost_man": metrics.labor_cost_man,
        "misc_fixed_man": metrics.misc_fixed_man,
        "variable_cost_ratio": metrics.variable_cost_ratio,
        "break_even_daily_man": metrics.break_even_daily_man,
        "orders_per_day": metrics.orders_per_day,
        "rent_per_pyeong_man": metrics.rent_per_pyeong_man,
        "monthly_operating_profit_man": metrics.monthly_operating_profit_man,
        "rent_excess_man": metrics.rent_excess_man,
        "recommended_rent_max_man": metrics.recommended_rent_max_man,
        "base_verdict": row["verdict"],
        "verdict_adjustments": adjustments,
    }

    fallback = rule_lease_headline(
        label=label,
        verdict=verdict,
        expected_sales_man=expected,
        lease_cost_man=lease_cost_man,
        max_lease_cost_man=max_lease_cost,
        rent_gap_man=row["rent_gap_man"],
        break_even_man=metrics.break_even_monthly_man,
        terms=terms,
    )
    headline, source, narrate_warnings = await _narrate_or_rule(
        {
            "목표": "이 매물을 계약해도 되는지",
            "매물": label,
            "판정": VERDICT_LABELS[verdict],
            "추천점수": row["recommend_score"],
            "예상월매출_만원": expected,
            "월임차비_만원": lease_cost_man,
            "감당가능_월임차비상한_만원": max_lease_cost,
            "손익분기_월매출_만원": metrics.break_even_monthly_man,
            "임대료부담률_퍼센트": metrics.rent_burden_pct,
            "목표월세_만원": terms.target_rent_man,
            "렌트프리_개월": terms.rent_free_months,
            "권리금상한_만원": terms.max_key_money_man,
            "권리금회수_개월": metrics.key_money_payback_months,
            "강점": row["strengths"],
            "위험": risks,
            "권고": actions,
        },
        fallback,
    )
    warnings.extend(narrate_warnings)

    return LeaseVerdictResponse(
        verdict=verdict,
        verdict_label=VERDICT_LABELS[verdict],
        recommend_score=row["recommend_score"],
        expected_monthly_sales_man=expected,
        expected_monthly_sales_range_man=row.get("expected_monthly_sales_range_man"),
        expected_sales_basis=row["expected_sales_basis"],
        break_even_monthly_man=metrics.break_even_monthly_man,
        rent_burden_pct=metrics.rent_burden_pct,
        appropriate_rent_low_man=metrics.appropriate_rent_low_man,
        appropriate_rent_high_man=metrics.appropriate_rent_high_man,
        rent_gap_man=row["rent_gap_man"],
        negotiation=NegotiationOut(
            target_rent_man=terms.target_rent_man,
            rent_free_months=terms.rent_free_months,
            max_key_money_man=terms.max_key_money_man,
        ),
        key_money_payback_months=metrics.key_money_payback_months,
        strengths=row["strengths"],
        risks=risks,
        actions=actions,
        evidence=evidence,
        headline=headline,
        narrative_source=source,
        warnings=warnings,
    )


# ===========================================================================
# 2. POST /api/purchase-verdict
# ===========================================================================


class LoanIn(BaseModel):
    principal_manwon: Annotated[float, _null_as(0)] = Field(0, ge=0, le=10_000_000)
    annual_rate: Annotated[float, _null_as(0)] = Field(0, ge=0, le=30, description="연 이자율 %")
    term_years: Annotated[int, _null_as(20)] = Field(20, ge=0, le=50)
    interest_only: Annotated[bool, _null_as(False)] = False


class PurchaseVerdictRequest(RadiusBase):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    inds_lcls_cd: str | None = None
    inds_mcls_cd: str | None = None
    inds_scls_cd: str | None = None
    price_manwon: float = Field(..., gt=0, le=10_000_000)
    area_sqm: float = Field(..., gt=0, le=100_000)
    property_type: Literal["commercial", "officetel"] = "commercial"
    building_type: str = Field("집합", max_length=20)
    floor: int | None = None
    monthly_rent_man: float | None = Field(
        None, ge=0, le=100_000, description="현 임대차 월세 (없으면 입지 기반 추정)"
    )
    deposit_man: float | None = Field(None, ge=0, le=1_000_000)
    loan: LoanIn | None = None
    acquisition_tax_manwon: float | None = Field(None, ge=0, le=1_000_000)
    label: str = Field("", max_length=120)


class MarketBandOut(BaseModel):
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    n: int = 0
    radius_m: int = 0
    insufficient_sample: bool = True
    relaxations: list[str] = []
    data_source: str = ""


class PurchaseVerdictResponse(BaseModel):
    verdict: str
    verdict_label: str
    cap_rate_pct: float | None = None
    dscr: float | None = None
    payback_years: float | None = None
    max_acceptable_price_manwon: float | None = None
    price_premium_pct: float | None = None
    market_band: MarketBandOut
    monthly_noi_man: float | None = None
    strengths: list[str] = []
    risks: list[str] = []
    actions: list[str] = []
    evidence: dict[str, Any] = {}
    headline: str = ""
    narrative_source: str = "rule"
    warnings: list[str] = []


@router.post("/purchase-verdict", response_model=PurchaseVerdictResponse)
async def purchase_verdict(request: PurchaseVerdictRequest) -> PurchaseVerdictResponse:
    label = request.label or "이 상가"
    area_pyeong = request.area_sqm / PYEONG_TO_SQM
    warnings: list[str] = []

    site_task = analyze_site(
        lat=request.lat,
        lng=request.lng,
        label=label,
        inds_lcls_cd=request.inds_lcls_cd,
        inds_mcls_cd=request.inds_mcls_cd,
        inds_scls_cd=request.inds_scls_cd,
        radius_m=request.radius_m,
        monthly_cost_man=None,
        area_pyeong=area_pyeong,
        floor=request.floor,
    )
    band_task = fetch_comparables(
        lat=request.lat,
        lng=request.lng,
        floor=request.floor,
        area_sqm=request.area_sqm,
        purchase_price_man=int(round(request.price_manwon)),
        property_type=request.property_type,
        building_type=request.building_type,
    )
    site_res, band_res = await asyncio.gather(
        site_task, band_task, return_exceptions=True
    )

    if isinstance(site_res, BaseException):
        detail = getattr(site_res, "detail", None) or str(site_res) or type(site_res).__name__
        raise HTTPException(status_code=502, detail=f"입지 분석 실패: {detail}")
    analysis = site_res
    warnings.extend(analysis.warnings)
    row = analysis.row

    band = None
    if isinstance(band_res, BaseException):
        warnings.append(f"주변 실거래 비교 생략: {band_res or type(band_res).__name__}")
    else:
        band = band_res

    # 기대 월세: 입력이 있으면 그대로, 없으면 '이 자리에서 임차인이 감당 가능한
    # 월 임차비 상한'(decision.max_acceptable_rent)을 추정치로 쓴다.
    rent_estimated = request.monthly_rent_man is None
    monthly_rent_man = request.monthly_rent_man
    rent_basis = "입력한 현 임대차 월세"
    if rent_estimated:
        monthly_rent_man = row["max_acceptable_rent_man"]
        rent_basis = (
            f"기대 월세 미입력 — 이 자리 임차인이 감당 가능한 월 임차비 상한으로 추정 "
            f"({row['expected_sales_basis']})"
        )
        if monthly_rent_man is None:
            warnings.append(
                "상권 매출 근거가 없어 기대 월세를 추정하지 못했습니다 — "
                "수익률 지표를 계산할 수 없습니다."
            )

    econ = None
    if monthly_rent_man is not None:
        econ = compute_purchase_economics(
            purchase_price_man=request.price_manwon,
            monthly_rent_man=monthly_rent_man,
            rent_basis=rent_basis,
            loan_amount_man=request.loan.principal_manwon if request.loan else None,
            loan_rate_pct=request.loan.annual_rate if request.loan else None,
            loan_term_years=request.loan.term_years if request.loan else None,
            loan_interest_only=request.loan.interest_only if request.loan else False,
            acquisition_tax_man=request.acquisition_tax_manwon,
            deposit_man=request.deposit_man,
        )

    cap_rate = econ.cap_rate_pct if econ else None
    dscr = econ.result.dscr if econ else None
    premium = price_premium_pct(band)
    site_total = _site_total_score(row)

    verdict = decide_purchase_verdict(
        cap_rate_pct=cap_rate,
        price_premium_pct=premium,
        site_total_score=site_total,
        dscr=dscr,
    )

    max_price, max_price_basis = max_acceptable_price(
        annual_noi_man=econ.result.noi_manwon if econ else None,
        market_p50_per_sqm=band.p50 if band else None,
        area_sqm=request.area_sqm,
    )
    price_gap = (
        round(request.price_manwon - max_price, 1) if max_price is not None else None
    )

    facts: dict[str, Any] = {
        "cap_rate_pct": cap_rate,
        "dscr": dscr,
        "payback_years": econ.payback_years if econ else None,
        "price_premium_pct": premium,
        "comparable_count": band.n if band else 0,
        "insufficient_sample": bool(band.insufficient_sample) if band else True,
        "rent_estimated": rent_estimated,
        "site_total_score": site_total,
        "loan_rate_pct": request.loan.annual_rate if request.loan else None,
        "max_acceptable_price_man": max_price,
        "price_gap_man": price_gap,
    }

    evidence = dict(row["evidence"])
    evidence["purchase"] = {
        "price_manwon": request.price_manwon,
        "area_sqm": request.area_sqm,
        "area_pyeong": round(area_pyeong, 1),
        "property_type": request.property_type,
        "building_type": request.building_type,
        "floor": request.floor,
        "monthly_rent_man": monthly_rent_man,
        "rent_basis": rent_basis,
        "rent_estimated": rent_estimated,
        "max_acceptable_price_manwon": max_price,
        "max_acceptable_price_basis": max_price_basis,
        "price_gap_manwon": price_gap,
        "assumptions": econ.assumptions if econ else [],
        "profitability": (
            {
                "total_cost_manwon": econ.result.total_cost_manwon,
                "equity_manwon": econ.result.equity_manwon,
                "noi_manwon": econ.result.noi_manwon,
                "annual_debt_service_manwon": econ.result.annual_debt_service_manwon,
                "btcf_manwon": econ.result.btcf_manwon,
                "monthly_payment_manwon": econ.result.monthly_payment_manwon,
                "gross_yield_pct": econ.result.gross_yield_pct,
                "cap_rate_noi_pct": econ.result.cap_rate_noi_pct,
                "cap_rate_total_cost_pct": econ.result.cap_rate_total_cost_pct,
                "cash_on_cash_pct": econ.result.cash_on_cash_pct,
                "dscr": econ.result.dscr,
                "break_even_occupancy_pct": econ.result.break_even_occupancy_pct,
                "irr_pct": econ.result.irr_pct,
                "warnings": econ.result.warnings,
            }
            if econ
            else None
        ),
    }
    evidence["comparables"] = comparables_payload(band)

    strengths = build_purchase_strengths(facts)
    risks = build_purchase_risks(facts)
    actions = build_purchase_actions(facts, verdict)

    fallback = rule_purchase_headline(
        label=label,
        verdict=verdict,
        purchase_price_man=request.price_manwon,
        cap_rate_pct=cap_rate,
        monthly_noi_man=econ.monthly_noi_man if econ else None,
        payback_years=econ.payback_years if econ else None,
        max_price_man=max_price,
        price_gap_man=price_gap,
        premium_pct=premium,
        rent_estimated=rent_estimated,
    )
    headline, source, narrate_warnings = await _narrate_or_rule(
        {
            "목표": "이 상가를 매입해도 되는지",
            "물건": label,
            "판정": VERDICT_LABELS[verdict],
            "매입가_만원": request.price_manwon,
            "연수익률_퍼센트": cap_rate,
            "월순수익_만원": econ.monthly_noi_man if econ else None,
            "원리금상환배율_DSCR": dscr,
            "원금회수_년": econ.payback_years if econ else None,
            "적정매입가상한_만원": max_price,
            "주변시세대비_퍼센트": premium,
            "비교표본_건": band.n if band else 0,
            "강점": strengths,
            "위험": risks,
            "권고": actions,
        },
        fallback,
    )
    warnings.extend(narrate_warnings)
    if econ:
        warnings.extend(w for w in econ.result.warnings if w not in warnings)

    return PurchaseVerdictResponse(
        verdict=verdict,
        verdict_label=VERDICT_LABELS[verdict],
        cap_rate_pct=cap_rate,
        dscr=dscr,
        payback_years=econ.payback_years if econ else None,
        max_acceptable_price_manwon=max_price,
        price_premium_pct=premium,
        market_band=MarketBandOut(
            p25=band.p25 if band else None,
            p50=band.p50 if band else None,
            p75=band.p75 if band else None,
            n=band.n if band else 0,
            radius_m=band.radius_m if band else 0,
            insufficient_sample=bool(band.insufficient_sample) if band else True,
            relaxations=band.relaxations if band else [],
            data_source=band.data_source if band else "",
        ),
        monthly_noi_man=econ.monthly_noi_man if econ else None,
        strengths=strengths,
        risks=risks,
        actions=actions,
        evidence=evidence,
        headline=headline,
        narrative_source=source,
        warnings=warnings,
    )


# ===========================================================================
# 3. POST /api/diagnose
# ===========================================================================


class DiagnoseRequest(RadiusBase):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    inds_lcls_cd: str | None = None
    inds_mcls_cd: str | None = None
    inds_scls_cd: str | None = None
    current_monthly_sales_man: float = Field(..., gt=0, le=1_000_000)
    monthly_rent_man: float | None = Field(None, ge=0, le=100_000)
    brand: str | None = Field(None, max_length=40)


class CauseOut(BaseModel):
    key: str
    label: str
    severity: Literal["high", "medium", "low"]
    finding: str
    action: str


class DiagnoseResponse(BaseModel):
    expected_monthly_sales_man: float | None = None
    # 최근 분기 변동폭 기반 범위 [low, high] (만원) — 점 추정의 정직한 불확실성.
    expected_monthly_sales_range_man: list[float] | None = None
    gap_man: float | None = None
    gap_pct: float | None = None
    verdict_label: str
    causes: list[CauseOut] = []
    strengths: list[str] = []
    evidence: dict[str, Any] = {}
    headline: str = ""
    narrative_source: str = "rule"
    warnings: list[str] = []


def _sales_trend(lat: float, lng: float, profile_id: str | None):
    """(TrdarMatch | None, TrdarSalesTrend | None). 실패는 조용히 None.

    match를 통째로 돌려주는 건 match.contains/distance_m(최근접 폴백 여부)를
    호출부가 그대로 신호로 넘길 수 있게 하기 위해서다 — trdar_nm만 넘기면
    "이 상권 안"인지 "300m 이내 가장 가까운 상권"인지가 지워진다.
    """
    if not profile_id:
        return None, None
    try:
        match = trdar_for_point(lat, lng)
        if match is None:
            return None, None
        return match, sales_trend_for_trdar(match.trdar_cd, profile_id)
    except Exception:
        return None, None


@router.post("/diagnose", response_model=DiagnoseResponse)
async def diagnose(request: DiagnoseRequest) -> DiagnoseResponse:
    resolver = ProfileResolver.load()

    analysis_task = analyze_site(
        lat=request.lat,
        lng=request.lng,
        label="현재 매장",
        inds_lcls_cd=request.inds_lcls_cd,
        inds_mcls_cd=request.inds_mcls_cd,
        inds_scls_cd=request.inds_scls_cd,
        radius_m=request.radius_m,
        brand=request.brand,
        monthly_cost_man=request.monthly_rent_man,
    )
    profile_id = resolver.resolve(
        inds_lcls_cd=request.inds_lcls_cd,
        inds_mcls_cd=request.inds_mcls_cd,
        inds_scls_cd=request.inds_scls_cd,
        business_type="general",
        archetype="general",
    )
    living_task = run_in_threadpool(
        fetch_living_pop_profile, request.lat, request.lng, request.radius_m
    )
    trend_task = run_in_threadpool(
        _sales_trend, request.lat, request.lng, profile_id
    )

    analysis_res, living_res, trend_res = await asyncio.gather(
        analysis_task, living_task, trend_task, return_exceptions=True
    )

    if isinstance(analysis_res, BaseException):
        detail = (
            getattr(analysis_res, "detail", None)
            or str(analysis_res)
            or type(analysis_res).__name__
        )
        raise HTTPException(status_code=502, detail=f"입지 분석 실패: {detail}")
    analysis = analysis_res
    row = analysis.row
    warnings = list(analysis.warnings)

    # 업종 프로파일이 없으면 archetype 기준으로 다시 잡는다.
    if not profile_id:
        profile_id = resolver.resolve_by_archetype(analysis.upjong.archetype)

    living = None if isinstance(living_res, BaseException) else living_res
    if living is None:
        warnings.append("생활인구 데이터가 없어 시간대 미스매치는 진단하지 못했습니다.")

    trdar_match, trend = (None, None)
    if not isinstance(trend_res, BaseException):
        trdar_match, trend = trend_res
    if trend is None:
        warnings.append("상권 분기 매출 추세를 확보하지 못해 추세 원인은 진단하지 못했습니다.")
    elif trdar_match is not None and not trdar_match.contains:
        warnings.append(
            f"'{trdar_match.trdar_nm}'은 이 지점을 포함하는 상권이 아니라 "
            f"약 {trdar_match.distance_m:.0f}m 떨어진 가장 가까운 상권입니다 — "
            "상권 매출 추세는 참고용입니다."
        )

    core_hours = dx.core_hours_for(profile_id, analysis.upjong.archetype)
    core_index = dx.core_hour_fit_index(living, core_hours)

    ev = row["evidence"]
    subscores = {s["key"]: s for s in ev["site_score"]["subscores"]}
    cann = ev.get("cannibalization") or {}
    burden_cap = float(
        get_archetype_defaults(analysis.upjong.archetype).get("rent_ratio_high") or 0.0
    )

    signals = dx.DiagnosisSignals(
        expected_monthly_sales_man=row["expected_monthly_sales_man"],
        expected_monthly_sales_range_man=row.get("expected_monthly_sales_range_man"),
        current_monthly_sales_man=request.current_monthly_sales_man,
        integration_top_pct=ev["site_score"]["segment"]["integration_top_pct"],
        choice_top_pct=ev["site_score"]["segment"]["choice_top_pct"],
        cannibalization_risk=cann.get("risk_score"),
        competitor_count=cann.get("total_count"),
        self_store_count=cann.get("self_total_count"),
        core_hour_index=core_index,
        core_hours=core_hours,
        monthly_rent_man=request.monthly_rent_man,
        rent_burden_cap_pct=(burden_cap * 100.0) if burden_cap else None,
        trend_change_pct=trend.change_pct if trend else None,
        trend_quarters=trend.quarters if trend else None,
        trdar_nm=trdar_match.trdar_nm if trdar_match else "",
        trdar_contains=trdar_match.contains if trdar_match else True,
        trdar_distance_m=trdar_match.distance_m if trdar_match else None,
        demand_score=(subscores.get("demand") or {}).get("score"),
        demand_population=(ev["site_score"].get("demand") or {}).get("population"),
        station_name=(ev["site_score"].get("transit") or {}).get("station_name"),
        station_distance_m=(ev["site_score"].get("transit") or {}).get("distance_m"),
    )
    result = dx.diagnose(signals)
    if result.expected_monthly_sales_man is None:
        warnings.append(
            "상권 매출 벤치마크가 없어 입지 대비 적정 매출을 계산하지 못했습니다."
        )

    evidence = dict(ev)
    evidence["diagnosis"] = dict(result.facts)
    evidence["diagnosis"]["current_monthly_sales_man"] = request.current_monthly_sales_man
    evidence["diagnosis"]["industry_profile_id"] = profile_id
    evidence["diagnosis"]["expected_sales_basis"] = row["expected_sales_basis"]
    if trend:
        evidence["diagnosis"]["trend"] = {
            "trdar_nm": trdar_match.trdar_nm if trdar_match else "",
            "trdar_contains": trdar_match.contains if trdar_match else True,
            "trdar_distance_m": trdar_match.distance_m if trdar_match else None,
            "base_quarter": trend.base_quarter,
            "latest_quarter": trend.latest_quarter,
            "base_per_store_won": trend.base_per_store_won,
            "latest_per_store_won": trend.latest_per_store_won,
            "change_pct": trend.change_pct,
        }

    fallback = dx.rule_diagnosis_headline(
        result, current_man=request.current_monthly_sales_man
    )
    headline, source, narrate_warnings = await _narrate_or_rule(
        {
            "목표": "기존 매장의 매출이 낮은 이유",
            "현재월매출_만원": request.current_monthly_sales_man,
            "입지기준_예상월매출_만원": result.expected_monthly_sales_man,
            "차이_만원": result.gap_man,
            "차이_퍼센트": result.gap_pct,
            "판정": result.verdict_label,
            "원인": [
                {
                    "순위": i + 1,
                    "항목": c.label,
                    "심각도": {"high": "높음", "medium": "보통", "low": "낮음"}[c.severity],
                    "근거": c.finding,
                    "해야할일": c.action,
                }
                for i, c in enumerate(result.causes)
            ],
            "유지할_강점": result.strengths,
        },
        fallback,
    )
    warnings.extend(narrate_warnings)

    return DiagnoseResponse(
        expected_monthly_sales_man=result.expected_monthly_sales_man,
        gap_man=result.gap_man,
        gap_pct=result.gap_pct,
        verdict_label=result.verdict_label,
        causes=[
            CauseOut(
                key=c.key,
                label=c.label,
                severity=c.severity,
                finding=c.finding,
                action=c.action,
            )
            for c in result.causes
        ],
        strengths=result.strengths,
        evidence=evidence,
        headline=headline,
        narrative_source=source,
        warnings=warnings,
    )


# ===========================================================================
# 4. POST /api/area-search
# ===========================================================================


class AreaSearchRequest(RadiusBase):
    region_query: str = Field("", max_length=80)
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    inds_lcls_cd: str | None = None
    inds_mcls_cd: str | None = None
    inds_scls_cd: str | None = None
    budget_max_rent_man: float | None = Field(None, ge=0, le=100_000)
    priority: Annotated[
        Literal["revenue", "stability", "balanced"], _null_as("balanced")
    ] = "balanced"
    brand: str | None = Field(None, max_length=40)
    # 후보 1곳당 입지 분석이 한 번씩 돌지만, 재 보면 응답 시간을 지배하는 건
    # 후보 수가 아니라 고정비다 — 라이브에서 후보 1곳 9.4초, 5곳 13.7초
    # (고정비 ~8.5초 + 후보당 ~1초). 고정비는 _extract_spots의 도로망·접근성
    # 계산이고 캐시를 타지 않는다. 자세한 측정은 docs/PERF_AREA_SEARCH.md.
    # 따라서 이 값을 줄여도 5초 안에 못 들어온다(추천 품질만 깎인다).
    max_candidates: Annotated[int, _null_as(MAX_CANDIDATES)] = Field(
        MAX_CANDIDATES, ge=1, le=MAX_CANDIDATES
    )

    @field_validator("region_query")
    @classmethod
    def _region_or_point(cls, v: str) -> str:
        return v.strip()


class CenterOut(BaseModel):
    lat: float
    lng: float
    label: str = ""
    search_radius_m: int = 0


class AreaCandidateOut(BaseModel):
    label: str
    lat: float
    lng: float
    recommend_score: float
    verdict: str
    verdict_label: str
    expected_monthly_sales_man: float | None = None
    # 최근 분기 변동폭 기반 범위 [low, high] (만원) — 점 추정의 정직한 불확실성.
    expected_monthly_sales_range_man: list[float] | None = None
    max_acceptable_rent_man: float | None = None
    strengths: list[str] = []
    risks: list[str] = []
    evidence: dict[str, Any] = {}


class AreaSearchResponse(BaseModel):
    center: CenterOut
    region_label: str = ""
    candidates: list[AreaCandidateOut] = []
    headline: str = ""
    narrative_source: str = "rule"
    warnings: list[str] = []


def _extract_spots(
    lat: float, lng: float, search_radius_m: int, max_candidates: int
) -> tuple[list[CandidateSpot], list[str]]:
    """도로망 → 접근성 → 후보 지점. 전부 동기(CPU/DB)라 threadpool에서 돈다."""
    warnings: list[str] = []
    segments = fetch_roads_in_radius(lng, lat, search_radius_m)
    if not segments:
        raise HTTPException(
            status_code=404,
            detail=f"반경 {search_radius_m}m 안에 도로망이 없습니다.",
        )
    segments, original = cap_analysis_segments(
        segments, lng, lat, settings.interactive_max_segments
    )
    if len(segments) < original:
        warnings.append(
            f"빠른 분석을 위해 가까운 도로 {len(segments):,}개만 사용했습니다 "
            f"(전체 {original:,}개)."
        )

    G, _edge_map = build_graph(segments)
    metrics, error = compute_depthmapx_metrics(G, segments, modes=(WALK,))
    if metrics is None:
        if settings.depthmapx_enabled and settings.depthmapx_strict:
            raise HTTPException(
                status_code=503,
                detail=(
                    "DepthmapX engine failed and strict mode disables the "
                    f"Python fallback: {error or 'unknown error'}"
                ),
            )
        metrics, metric_warnings = compute_metrics(G, segments, modes=(WALK,))
        warnings.extend(metric_warnings)

    spots = sample_candidate_spots(
        segments, metrics[WALK]["integration"], max_candidates=max_candidates
    )
    return spots, warnings


@router.post("/area-search", response_model=AreaSearchResponse)
async def area_search(request: AreaSearchRequest) -> AreaSearchResponse:
    warnings: list[str] = []

    if request.lat is not None and request.lng is not None:
        lat, lng = request.lat, request.lng
    elif request.region_query:
        try:
            lat, lng = await geocode_address(request.region_query)
        except GeocodingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        raise HTTPException(
            status_code=400, detail="region_query 또는 lat/lng 중 하나는 필요합니다."
        )

    search_radius = search_radius_for(request.region_query)
    spots, spot_warnings = await run_in_threadpool(
        _extract_spots, lat, lng, search_radius, request.max_candidates
    )
    warnings.extend(spot_warnings)
    if not spots:
        raise HTTPException(
            status_code=404, detail="후보로 삼을 만한 도로 구간을 찾지 못했습니다."
        )

    # 주소는 지어내지 않는다 — 뽑힌 좌표를 역지오코딩한다.
    labels = await asyncio.gather(
        *[reverse_geocode(s.lat, s.lng) for s in spots], return_exceptions=True
    )
    region_label = request.region_query or "선택 지점"
    resolved: list[str] = []
    for i, raw in enumerate(labels):
        text = raw if isinstance(raw, str) else None
        resolved.append(text or f"{region_label} 후보 {i + 1}")
    if any(not isinstance(raw, str) or not raw for raw in labels):
        warnings.append("일부 후보지는 주소를 확인하지 못해 임시 이름을 붙였습니다.")
    # 대학 캠퍼스처럼 한 지번이 넓으면 후보 여러 곳이 같은 주소로 나온다.
    # 주소를 지어내는 대신 중심 기준 방위·거리를 덧붙여 구별한다.
    resolved = disambiguate_labels(resolved, spots, lat, lng)

    # /api/decide와 완전히 같은 채점 경로 — 목표가 달라도 점수는 같아야 한다.
    result = await compare_candidates(
        [
            CandidateInput(
                id=f"spot{i + 1}",
                label=resolved[i],
                lat=s.lat,
                lng=s.lng,
            )
            for i, s in enumerate(spots)
        ],
        inds_lcls_cd=request.inds_lcls_cd,
        inds_mcls_cd=request.inds_mcls_cd,
        inds_scls_cd=request.inds_scls_cd,
        radius_m=request.radius_m,
        priority=request.priority,
        brand=request.brand,
        budget_max_rent_man=request.budget_max_rent_man,
    )
    warnings.extend(result.warnings)

    by_id = {f"spot{i + 1}": s for i, s in enumerate(spots)}
    candidates: list[AreaCandidateOut] = []
    for r in result.ranked:
        spot = by_id[r.id]
        evidence = dict(r.evidence)
        evidence["spot"] = {
            "osm_id": spot.osm_id,
            "highway": spot.highway,
            "integration_top_pct_in_search_area": spot.integration_top_pct,
            "search_radius_m": search_radius,
            "center_lat": lat,
            "center_lng": lng,
        }
        candidates.append(
            AreaCandidateOut(
                label=r.label,
                lat=spot.lat,
                lng=spot.lng,
                recommend_score=r.recommend_score,
                verdict=r.verdict,
                verdict_label=r.verdict_label,
                expected_monthly_sales_man=r.expected_monthly_sales_man,
                max_acceptable_rent_man=r.max_acceptable_rent_man,
                strengths=r.strengths,
                risks=r.risks,
                evidence=evidence,
            )
        )

    top = candidates[0] if candidates else None
    if top is None:
        fallback = f"'{region_label}'에서 비교할 만한 후보지를 찾지 못했습니다."
    else:
        parts = [
            f"'{region_label}' 반경 {search_radius}m 안에서 도로 접근성 상위 지점 "
            f"{len(candidates)}곳을 뽑아 비교했습니다.",
            f"1순위는 '{top.label}'로 추천점수 {top.recommend_score:.0f}점, "
            f"판정은 '{top.verdict_label}'입니다.",
        ]
        if top.expected_monthly_sales_man:
            parts.append(
                f"예상 월매출은 약 {top.expected_monthly_sales_man:,.0f}만원입니다."
            )
        # `if top.max_acceptable_rent_man`이 상한 0을 걸러내는 바람에, 월세를
        # 한 푼도 낼 수 없는 1순위에서는 임차비 이야기가 통째로 사라졌다.
        if top.max_acceptable_rent_man is not None:
            if top.max_acceptable_rent_man > 0:
                parts.append(
                    f"월 임차비는 {top.max_acceptable_rent_man:,.0f}만원까지가 "
                    "감당 가능한 선입니다."
                )
            else:
                parts.append(
                    "다만 예상 매출로는 인건비·고정비를 빼고 나면 월 임차비를 "
                    "낼 여력이 남지 않습니다."
                )
        drivers = (top.evidence or {}).get("rank_drivers") or []
        lead = next((d for d in drivers if d.get("direction") == "up"), None)
        if lead and lead.get("text"):
            parts.append(f"순위를 가른 건 {lead['text']}입니다.")
        fallback = " ".join(parts)

    headline, source, narrate_warnings = await _narrate_or_rule(
        {
            "목표": "가게를 열 장소 찾기",
            "지역": region_label,
            "검색반경_m": search_radius,
            "후보지": [
                {
                    "순위": i + 1,
                    "이름": c.label,
                    "추천점수": c.recommend_score,
                    "판정": c.verdict_label,
                    "예상월매출_만원": c.expected_monthly_sales_man,
                    # 숫자 0.0을 그대로 넘기면 "월세 상한이 0.0만원이라"처럼
                    # 뜻이 전달되지 않는 문장이 나온다 — 문장으로 넘긴다.
                    "감당가능한월임차비": rent_cap_phrase(c.max_acceptable_rent_man),
                    # 후보들이 같은 상권을 공유하면 예상 월매출이 서로 같은 값으로
                    # 나온다. 그때 순위 차이를 설명할 수 있는 유일한 근거다.
                    "순위를가른요인": [
                        d.get("text")
                        for d in (c.evidence or {}).get("rank_drivers", [])
                    ],
                    "강점": c.strengths,
                    "위험": c.risks,
                }
                for i, c in enumerate(candidates)
            ],
        },
        fallback,
    )
    warnings.extend(narrate_warnings)

    return AreaSearchResponse(
        center=CenterOut(
            lat=lat, lng=lng, label=region_label, search_radius_m=search_radius
        ),
        region_label=region_label,
        candidates=candidates,
        headline=headline,
        narrative_source=source,
        warnings=warnings,
    )


__all__ = ["MAX_CANDIDATES", "router"]
