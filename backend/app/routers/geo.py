"""행정동 이름 검색 — 자동완성 상단에 '동네(행정동)'를 올리기 위한 조회.

좌표 계산은 services/dong_center.py에 있다(에이전트 챗과 공유 — 같은 동네를
고르든 말로 묻든 같은 지점을 봐야 한다).
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services.dong_center import find_dong_centers

router = APIRouter(prefix="/api/geo", tags=["geo"])


class DongHit(BaseModel):
    name: str
    lat: float
    lng: float


@router.get("/dong-search", response_model=list[DongHit])
def dong_search(
    q: str = Query(..., min_length=1, max_length=20),
    limit: int = Query(6, ge=1, le=10),
) -> list[DongHit]:
    """이름이 q를 포함하는 동네 목록 (상권 중심 좌표 포함)."""
    return [
        DongHit(name=c.name, lat=c.lat, lng=c.lng)
        for c in find_dong_centers(q, limit)
    ]
