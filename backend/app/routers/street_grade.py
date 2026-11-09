"""지도 분석 — 화면 범위의 길을 「목 좋은 길」 3단계로 내준다.

계산은 `services/street_grade.py`에 있고 여기는 입출력만 맞춘다. 미리 계산해 둔
`road_segment_structure`를 읽기만 한다 (5초 룰: 강남역 한 화면 실측 0.4초).
"""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.models.schemas import StreetGradeRequest, StreetGradeResponse, StreetSegmentOut
from app.services.street_grade import fetch_street_grades

router = APIRouter(prefix="/api", tags=["street-grade"])
logger = logging.getLogger(__name__)

CAVEATS = [
    "진한 길은 서울 길 구간 가운데 사람이 오가며 지나가기 쉬운 상위 10%입니다(길 구조만 본 값).",
    "길 구조 상위 20% 상권은 하위 20% 상권보다 분기 매출이 약 3.3배였습니다(서울 상권 1,648개, 16억 → 54억).",
    "옅은 길과 회색 길의 차이는 크지 않습니다 — 가르는 건 맨 위 한 칸입니다.",
    "임대료·업종·현재 매출은 반영하지 않았습니다. 가격이나 오를지를 말하는 지도가 아닙니다.",
]


@router.post("/street-grades", response_model=StreetGradeResponse)
async def street_grades(request: StreetGradeRequest):
    if request.east <= request.west or request.north <= request.south:
        raise HTTPException(status_code=422, detail="화면 범위가 뒤집혀 있습니다")
    try:
        overview, segments = await run_in_threadpool(
            fetch_street_grades, request.west, request.south, request.east, request.north
        )
    except Exception as exc:  # DB 장애 등
        logger.warning("길 등급 조회 실패: %s", exc)
        raise HTTPException(status_code=503, detail="길 데이터를 읽지 못했습니다") from exc

    caveats = list(CAVEATS)
    if overview:
        caveats.insert(0, "멀리서 보는 중이라 상위 10% 길만 표시합니다. 확대하면 나머지 길도 나옵니다.")
    return StreetGradeResponse(
        overview=overview,
        segments=[StreetSegmentOut(**vars(s)) for s in segments],
        caveats=caveats,
    )
