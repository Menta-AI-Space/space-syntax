"""주변 매매 실거래 comparable API — 투자 수익성 분석의 시장 컨텍스트."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    ComparableTradeOut,
    PropertyMarketRequest,
    PropertyMarketResponse,
)
from app.services.geocoding import GeocodingError, geocode_address
from app.services.property_market import fetch_market_band

router = APIRouter(prefix="/api", tags=["property-market"])


@router.post("/property-market/comparables", response_model=PropertyMarketResponse)
async def property_market_comparables(request: PropertyMarketRequest):
    warnings: list[str] = []

    if request.lat is not None and request.lng is not None:
        lat, lng = request.lat, request.lng
    else:
        try:
            lat, lng = await geocode_address(request.address or "")
        except GeocodingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    band = fetch_market_band(
        lat,
        lng,
        request.property_type,  # type: ignore[arg-type]
        building_type=request.building_type,
        floor=request.floor,
        area_sqm=request.area_sqm,
        months_back=request.months_back,
        subject_price_manwon=request.purchase_price_manwon,
    )

    if band.insufficient_sample:
        warnings.append(
            f"조건에 맞는 표본이 {band.n}건뿐입니다 (기준 {8}건). "
            "밴드는 참고용으로만 사용하세요."
        )
    if band.relaxations:
        warnings.append("표본 확보를 위해 완화됨: " + ", ".join(band.relaxations))

    return PropertyMarketResponse(
        lat=lat,
        lng=lng,
        property_type=band.property_type,
        building_type=band.building_type,
        n=band.n,
        radius_m=band.radius_m,
        months_back=band.months_back,
        p25=band.p25,
        p50=band.p50,
        p75=band.p75,
        min_deal_date=band.min_deal_date,
        max_deal_date=band.max_deal_date,
        relaxations=band.relaxations,
        insufficient_sample=band.insufficient_sample,
        excluded=band.excluded,
        comparables=[ComparableTradeOut(**vars(c)) for c in band.comparables],
        subject_price_per_sqm=band.subject_price_per_sqm,
        subject_percentile=band.subject_percentile,
        time_adjusted=band.time_adjusted,
        as_of=band.as_of,
        data_source=band.data_source,
        warnings=warnings,
    )
