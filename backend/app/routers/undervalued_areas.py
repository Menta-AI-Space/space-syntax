""""뜰 자리" API — 미리 계산된 표를 읽기만 한다 (5초 룰: 요청 시점엔 계산 안 함).

값은 ``tools/compute_undervalued_areas.py`` 배치가 채운다. 배치를 아직 안 돌렸으면
빈 목록을 반환한다 — 없는 값을 지어내지 않는다.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.models.schemas import UndervaluedAreaOut, UndervaluedAreasResponse
from app.services.undervalued_areas import list_top_undervalued

router = APIRouter(prefix="/api", tags=["undervalued-areas"])


@router.get("/undervalued-areas", response_model=UndervaluedAreasResponse)
async def undervalued_areas(limit: int = Query(20, ge=1, le=100)):
    rows = list_top_undervalued(limit)
    return UndervaluedAreasResponse(
        areas=[
            UndervaluedAreaOut(
                adm_cd=row["adm_cd"],
                adm_nm=row["adm_nm"],
                structure_score=row["structure_score"],
                structure_percentile=row["structure_percentile"],
                price_per_sqm_manwon=row["price_per_sqm_manwon"],
                price_percentile=row["price_percentile"],
                trade_sample_count=row["trade_sample_count"],
                gap_score=row["gap_score"],
                computed_at=row["computed_at"].isoformat(),
            )
            for row in rows
        ]
    )
