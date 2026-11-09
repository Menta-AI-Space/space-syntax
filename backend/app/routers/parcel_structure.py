"""필지별 길 구조 등급 — 땅을 나눠 값을 매길 때 쓰는 상대 배분 근거.

계산은 `services/parcel_structure.py`에 있고 여기는 입출력만 맞춘다. 미리
계산해 둔 `road_segment_structure`를 읽기만 하므로 런타임에 공간구문론을 다시
돌리지 않는다 (5초 룰: 논현 1,070필지 실측 3.0초).
"""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.models.schemas import (
    ParcelStructureOut,
    ParcelStructureRequest,
    ParcelStructureResponse,
    ParcelStructureSummaryOut,
)
from app.services.parcel_structure import (
    fetch_parcel_structures,
    fetch_parcel_structures_by_pnu,
    grade_parcels,
    summarize,
)

router = APIRouter(prefix="/api", tags=["parcel-structure"])
logger = logging.getLogger(__name__)

# 이 배수 아래면 블록 안 구조 차이가 작아 등급으로 값을 가를 근거가 약하다.
FLAT_BLOCK_SPREAD = 1.3


@router.post("/parcel-structure", response_model=ParcelStructureResponse)
async def parcel_structure(request: ParcelStructureRequest):
    def work():
        if request.pnus:
            parcels = fetch_parcel_structures_by_pnu(request.pnus)
        else:
            parcels = fetch_parcel_structures(request.lng, request.lat, request.radius_m)
        return grade_parcels(parcels)

    try:
        parcels = await run_in_threadpool(work)
    except Exception as exc:  # DB 장애 등
        logger.warning("필지 구조 조회 실패: %s", exc)
        raise HTTPException(status_code=503, detail="필지 데이터를 읽지 못했습니다") from exc

    if not parcels:
        raise HTTPException(
            status_code=404,
            detail=(
                "고르신 필지를 찾지 못했습니다"
                if request.pnus
                else "이 좌표 주변에는 필지 경계를 아직 적재하지 않았습니다"
            ),
        )

    stats = summarize(parcels)
    caveats = [
        "길 구조만 본 상대 등급입니다. 감정평가가 아니며 개별 필지의 가격을 단정하지 않습니다.",
        (
            f"등급은 고르신 {len(parcels)}개 필지끼리의 순위입니다 — 대상을 바꾸면 등급도 바뀝니다."
            if request.pnus
            else "등급은 요청하신 반경 안에서의 순위입니다 — 다른 동네와 직접 비교할 수 없습니다."
        ),
    ]
    if stats["structure_spread"] and stats["structure_spread"] < FLAT_BLOCK_SPREAD:
        caveats.append(
            f"이 블록은 필지 간 구조 차이가 작습니다(상·하위 10% {stats['structure_spread']}배). "
            "등급으로 값을 가르는 근거가 약하니 면적 비례에 가깝게 보셔야 합니다."
        )
    caveats.append(
        "공시지가와 대조하면 구조 상위 20% 필지가 하위 20%보다 1.9배 높았습니다(강남 논현 1,066필지). "
        "다만 중간 등급끼리는 구분되지 않았고, 공시지가 산식에 도로 조건이 이미 들어 있어 "
        "이 일치는 '같은 방향을 본다'는 정도로만 읽어야 합니다."
    )

    total = request.total_amount_manwon
    return ParcelStructureResponse(
        summary=ParcelStructureSummaryOut(**stats),
        parcels=[
            ParcelStructureOut(
                pnu=p.pnu, area_sqm=p.area_sqm, lat=p.lat, lng=p.lng, geometry=p.geometry,
                structure=p.structure, integration=p.integration, choice=p.choice,
                road_distance_m=p.road_distance_m, frontage_count=p.frontage_count,
                percentile=p.percentile, grade=p.grade,
                allocation_share=p.allocation_share,
                allocation_manwon=(
                    round(total * p.allocation_share, 1)
                    if total and p.allocation_share is not None
                    else None
                ),
            )
            for p in parcels
        ],
        caveats=caveats,
    )
