"""투자 수익성 계산 API — DB 호출 없는 결정론적 계산.

시장 컨텍스트(주변 실거래)는 /api/property-market/comparables가 담당하고,
이 엔드포인트는 사용자 입력만으로 NOI·Cap rate·CoC·DSCR·시나리오·IRR을 계산한다.
데이터 오류와 계산 오류를 분리하기 위한 의도적 분리다.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from app.models.schemas import (
    ComparableTradeOut,
    ProfitabilityAnalyzeRequest,
    ProfitabilityAnalyzeResponse,
    ProfitabilityRequest,
    ProfitabilityResponse,
    PropertyMarketResponse,
    RebRentTypeOut,
    RentReferenceOut,
    RiskItemOut,
)
from app.services.profitability import (
    ExitInputs,
    LoanInputs,
    PurchaseInputs,
    RentalInputs,
    analyze_profitability,
)
from app.services.profitability_explain import explain_profitability
from app.services.property_market import MarketBand, fetch_market_band
from app.services.officetel_rent import fetch_officetel_rent_band
from app.services.reb_rent import fetch_rent_reference, monthly_rent_band_manwon

router = APIRouter(prefix="/api", tags=["profitability"])


@router.post("/profitability/calculate", response_model=ProfitabilityResponse)
async def profitability_calculate(request: ProfitabilityRequest):
    result = analyze_profitability(
        PurchaseInputs(**request.purchase.model_dump()),
        RentalInputs(**request.rental.model_dump()),
        LoanInputs(**request.loan.model_dump()) if request.loan else None,
        ExitInputs(**request.exit_plan.model_dump()) if request.exit_plan else None,
    )
    return ProfitabilityResponse(**asdict(result))


def _band_to_response(lat: float, lng: float, band: MarketBand) -> PropertyMarketResponse:
    return PropertyMarketResponse(
        lat=lat, lng=lng,
        property_type=band.property_type, building_type=band.building_type,
        n=band.n, radius_m=band.radius_m, months_back=band.months_back,
        p25=band.p25, p50=band.p50, p75=band.p75,
        min_deal_date=band.min_deal_date, max_deal_date=band.max_deal_date,
        relaxations=band.relaxations, insufficient_sample=band.insufficient_sample,
        excluded=band.excluded,
        comparables=[ComparableTradeOut(**vars(c)) for c in band.comparables],
        subject_price_per_sqm=band.subject_price_per_sqm,
        subject_percentile=band.subject_percentile,
        time_adjusted=band.time_adjusted, as_of=band.as_of,
        data_source=band.data_source,
    )


@router.post("/profitability/analyze", response_model=ProfitabilityAnalyzeResponse)
async def profitability_analyze(request: ProfitabilityAnalyzeRequest):
    """계산 + (선택) 주변 실거래 밴드 + 위험 추출·설명을 한 번에."""
    result = analyze_profitability(
        PurchaseInputs(**request.purchase.model_dump()),
        RentalInputs(**request.rental.model_dump()),
        LoanInputs(**request.loan.model_dump()) if request.loan else None,
        ExitInputs(**request.exit_plan.model_dump()) if request.exit_plan else None,
    )

    warnings: list[str] = []
    band: MarketBand | None = None
    market_out: PropertyMarketResponse | None = None
    mc = request.market_context
    if mc is not None:
        try:
            band = fetch_market_band(
                mc.lat, mc.lng, mc.property_type,  # type: ignore[arg-type]
                building_type=mc.building_type,
                floor=mc.floor,
                area_sqm=mc.area_sqm,
                subject_price_manwon=int(request.purchase.price_manwon),
            )
            market_out = _band_to_response(mc.lat, mc.lng, band)
            if band.insufficient_sample:
                warnings.append(
                    f"주변 비교 실거래가 {band.n}건뿐이라 시장가격 검증은 참고용입니다."
                )
        except Exception as exc:  # 시장 데이터 오류가 계산·설명을 막지 않도록 격리
            warnings.append(f"시장 데이터 조회 실패: {exc}")

    # 상가 월세 참고치 (R-ONE 상권 통계). 실거래 미공개 → 참고 밴드로만.
    rent_ref_out: RentReferenceOut | None = None
    if mc is not None and mc.property_type == "commercial":
        try:
            ref = fetch_rent_reference(mc.lat, mc.lng, building_type=mc.building_type)
            if ref.matched:
                band_manwon = (
                    monthly_rent_band_manwon(ref, mc.area_sqm)
                    if mc.area_sqm else None
                )
                rent_ref_out = RentReferenceOut(
                    matched=True,
                    sanggwon_nm=ref.sanggwon_nm,
                    quarter=ref.quarter,
                    types=[RebRentTypeOut(**vars(t)) for t in ref.types],
                    rent_low_krw_per_sqm=ref.rent_low_krw_per_sqm,
                    rent_high_krw_per_sqm=ref.rent_high_krw_per_sqm,
                    monthly_rent_low_manwon=band_manwon[0] if band_manwon else None,
                    monthly_rent_high_manwon=band_manwon[1] if band_manwon else None,
                    vacancy_default_pct=ref.vacancy_default_pct,
                    data_source=ref.data_source,
                )
        except Exception as exc:
            warnings.append(f"임대료 참고치 조회 실패: {exc}")

    # 오피스텔 월세 참고치 (OffiRent 실거래 반경 comparable — 물건 단위).
    elif mc is not None and mc.property_type == "officetel":
        try:
            rb = fetch_officetel_rent_band(mc.lat, mc.lng, area_sqm=mc.area_sqm)
            if rb.n > 0:
                quarter = (
                    f"{rb.min_deal_date}~{rb.max_deal_date}"
                    if rb.min_deal_date else ""
                )
                src = f"{rb.data_source} · 반경 {rb.radius_m}m, {rb.n}건"
                if rb.relaxations:
                    src += " (" + ", ".join(rb.relaxations) + ")"
                rent_ref_out = RentReferenceOut(
                    matched=True,
                    sanggwon_nm="",
                    quarter=quarter,
                    types=[],
                    monthly_rent_low_manwon=rb.monthly_rent_p25_manwon,
                    monthly_rent_high_manwon=rb.monthly_rent_p75_manwon,
                    vacancy_default_pct=None,
                    data_source=src,
                )
            if rb.insufficient_sample:
                warnings.append(
                    f"주변 오피스텔 월세 실거래가 {rb.n}건뿐이라 참고용입니다."
                )
        except Exception as exc:
            warnings.append(f"오피스텔 월세 참고치 조회 실패: {exc}")

    explained = await explain_profitability(result, band)

    return ProfitabilityAnalyzeResponse(
        calculation=ProfitabilityResponse(**asdict(result)),
        market=market_out,
        rent_reference=rent_ref_out,
        headline=explained.headline,
        risks=[RiskItemOut(**vars(r)) for r in explained.risks],
        levers=explained.levers,
        explanation=explained.explanation,
        explanation_source=explained.explanation_source,
        warnings=warnings,
    )
