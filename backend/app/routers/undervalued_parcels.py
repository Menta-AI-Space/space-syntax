"""저평가 필지 API — **2단계(판정)**. 미리 계산된 표를 읽기만 한다 (5초 룰).

값은 ``tools/compute_parcel_undervalued.py`` 배치가 채운다. 배치를 아직 안
돌렸으면 빈 목록을 반환한다 — 없는 값을 지어내지 않는다.

1단계(``undervalued_areas``)는 동을 좁히고, 여기서 그 동 **안의 필지**로
내려간다. 동은 답이 아니라 후보 구역이다 — 동 평균은 같은 동 안 대로변과
이면도로의 차이를 지우는데, 그 차이가 공간구문론이 파는 신호다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

import json

from app.models.land_analysis import StructuredAnalysis
from app.models.schemas import (
    MapParcelOut,
    MapParcelsResponse,
    UndervaluedParcelOut,
    UndervaluedParcelsResponse,
)
from app.services.land_analysis_source import analyze_parcel
from app.services.land_narrative_llm import build_llm_narrative
from app.services.parcel_undervalued import (
    DEFAULT_LAND_CATEGORIES,
    DEFAULT_MIN_AREA_SQM,
    list_parcels_near,
    list_top_parcels_in_dong,
)

router = APIRouter(prefix="/api", tags=["undervalued-parcels"])


def _address(row: dict) -> str:
    """``ldong_name`` + ``jibun``. 지번이 없는 필지(실측 0.02%)는 동 이름만."""
    name = (row.get("ldong_name") or "").strip()
    jibun = (row.get("jibun") or "").strip()
    return f"{name} {jibun}".strip() if jibun else name


@router.get("/undervalued-parcels", response_model=UndervaluedParcelsResponse)
async def undervalued_parcels(
    adm_cd: str = Query(..., min_length=4, max_length=10, description="행정동 코드"),
    limit: int = Query(20, ge=1, le=100),
    include_all_land_categories: bool = Query(
        False,
        description=(
            "지목 필터를 끈다. 기본값은 대(垈)만 — 끄면 임야·철도용지처럼 "
            "그 용도로 쓸 수 없어 공시지가가 낮은 필지가 상위를 덮는다."
        ),
    ),
    min_area_sqm: float | None = Query(
        DEFAULT_MIN_AREA_SQM, ge=0, description="최소 면적(㎡). 0이면 제한 없음"
    ),
):
    rows = list_top_parcels_in_dong(
        adm_cd,
        limit=limit,
        land_categories=None if include_all_land_categories else DEFAULT_LAND_CATEGORIES,
        min_area_sqm=min_area_sqm or None,
    )
    return UndervaluedParcelsResponse(
        adm_cd=adm_cd,
        adm_nm=rows[0]["adm_nm"] if rows else None,
        parcels=[
            UndervaluedParcelOut(
                pnu=row["pnu"],
                address=_address(row),
                adm_cd=row["adm_cd"],
                adm_nm=row["adm_nm"],
                zone_group=row["zone_group"],
                land_category=row["land_category"],
                area_sqm=row["area_sqm"],
                road_side_name=row["road_side_name"],
                structure_percentile=row["structure_percentile"],
                price_percentile=row["price_percentile"],
                gap_score=row["gap_score"],
                official_price_per_sqm=row["price_per_sqm"],
                stratum_level=row["stratum_level"],
                stratum_size=row["stratum_size"],
                base_year=row["base_year"],
                lat=row["lat"],
                lng=row["lng"],
            )
            for row in rows
        ],
    )


@router.get("/map-parcels", response_model=MapParcelsResponse)
async def map_parcels(
    lng: float = Query(..., ge=126.0, le=128.0, description="중심 경도"),
    lat: float = Query(..., ge=37.0, le=38.0, description="중심 위도"),
    radius_m: float = Query(150, ge=20, le=600, description="반경(m)"),
    limit: int = Query(500, ge=1, le=2000),
):
    """지도에 그릴 필지 경계 + 판정.

    ``parcel_polygons``에 적재된 범위만 결과가 나온다 — 폴리곤은 동 단위로 넣으므로
    안 넣은 곳은 빈 목록이다. 반경 상한을 600m로 둔 이유: 전 서울 89만 폴리곤을
    GeoJSON으로 내리면 5초 룰을 못 지킨다. 넓히려면 벡터 타일이 선행이다.
    """
    rows = list_parcels_near(lng, lat, radius_m=radius_m, limit=limit)
    parcels = []
    for row in rows:
        geometry = row.get("geometry")
        if not geometry:
            # 기하가 없으면 지도에 그릴 수 없다 — 조용히 건너뛴다.
            continue
        name = (row.get("ldong_name") or "").strip()
        jibun = (row.get("jibun") or "").strip()
        parcels.append(
            MapParcelOut(
                pnu=row["pnu"],
                address=f"{name} {jibun}".strip() if jibun else name,
                geometry=json.loads(geometry),
                land_category=row.get("land_category"),
                zone_name=row.get("zone_name_1"),
                road_side_name=row.get("road_side_name"),
                area_sqm=row.get("area_sqm") or row.get("geom_area_sqm"),
                official_price_per_sqm=row.get("official_price_per_sqm"),
                gap_score=row.get("gap_score"),
                structure_percentile=row.get("structure_percentile"),
                price_percentile=row.get("price_percentile"),
                stratum_level=row.get("stratum_level"),
                stratum_size=row.get("stratum_size"),
                zone_group=row.get("zone_group"),
            )
        )
    return MapParcelsResponse(
        parcels=parcels,
        judged_count=sum(1 for p in parcels if p.gap_score is not None),
        # 조회가 상한만큼 돌려줬으면 바깥 테두리가 잘렸다고 본다. 정확히 limit개인
        # 경우를 잘린 것으로 오판할 수 있지만, 반대 방향(잘렸는데 안 알림)이 훨씬
        # 나쁘다 — 화면에 뚫린 구멍을 사용자가 "땅이 아님"으로 읽는다.
        truncated=len(rows) >= limit,
    )


@router.get("/parcel-analysis", response_model=StructuredAnalysis)
async def parcel_analysis(
    pnu: str = Query(..., min_length=19, max_length=19, description="필지 고유번호 19자리"),
    base_year: int = Query(2026, ge=1990, le=2100),
    narrative: str = Query(
        "static",
        description=(
            "서술 방식. static=토큰 0의 규칙 서술(기본), llm=LLM이 다시 쓴 서술. "
            "**어느 쪽이든 판정 값은 같다** — LLM이 실패하면 static으로 되돌아온다."
        ),
    ),
):
    """필지 하나의 판정 전체. 화면·에이전트·서술이 이 응답 하나만 본다.

    ⚠️ **fixture를 섞지 않는다.** 없는 필지는 404다 — 지어낸 값을 주면 화면에서
    진짜와 구분되지 않는다. 프런트가 백엔드 없이 개발할 때 쓰는 fixture는
    `public/fixtures/structured-analysis.json`이고 이 경로와 무관하다.
    """
    analysis = await run_in_threadpool(analyze_parcel, pnu, base_year=base_year)
    if analysis is None:
        raise HTTPException(
            status_code=404,
            detail="그 필지의 판정 자료가 없습니다. 필지 번호와 기준연도를 확인해 주세요.",
        )
    if narrative == "llm":
        analysis.narrative = await build_llm_narrative(analysis)
    return analysis
