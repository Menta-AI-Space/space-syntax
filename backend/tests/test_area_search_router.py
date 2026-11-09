"""AreaSearchResponse.region_label이 실제로 채워지는지 — 화면 지역 칩의 값 원천.

/api/area-search는 지역명을 두 군데(center.label, region_label)에 담아 내려야
하고 둘은 항상 같은 값이어야 한다. 도로망·역지오코딩·후보 채점처럼 무거운 I/O는
여기서 검증할 대상이 아니므로 goals 모듈의 경계 함수들을 목으로 대체한다.
"""

from __future__ import annotations

import asyncio

from app.routers import goals as goals_router
from app.services.area_search import CandidateSpot
from app.services.decision import DecisionResult, RankedCandidate


def _fake_ranked(candidate_id: str, label: str) -> RankedCandidate:
    return RankedCandidate(
        id=candidate_id,
        label=label,
        rank=1,
        recommend_score=77.0,
        verdict="on_par",
        verdict_label="적정",
        expected_monthly_sales_man=1200.0,
        expected_sales_basis="trdar",
        max_acceptable_rent_man=300.0,
        current_rent_man=None,
        rent_gap_man=None,
        strengths=[],
        risks=[],
        actions=[],
        evidence={},
    )


def test_area_search_response_region_label_matches_center_label(monkeypatch):
    """region_label은 지어낸 값이 아니라 center.label과 같은 값이어야 한다."""

    spots = [CandidateSpot(lat=37.556, lng=126.925, osm_id=1, integration_top_pct=90.0, highway="secondary")]

    def fake_extract_spots(lat, lng, search_radius_m, max_candidates):
        return spots, []

    async def fake_reverse_geocode(lat, lng):
        return "테스트동 123"

    async def fake_compare_candidates(candidates, **kwargs):
        return DecisionResult(
            ranked=[_fake_ranked(candidates[0].id, candidates[0].label)],
            warnings=[],
            weights_used={},
        )

    monkeypatch.setattr(goals_router, "_extract_spots", fake_extract_spots)
    monkeypatch.setattr(goals_router, "reverse_geocode", fake_reverse_geocode)
    monkeypatch.setattr(goals_router, "compare_candidates", fake_compare_candidates)

    request = goals_router.AreaSearchRequest(region_query="신촌동", lat=37.556, lng=126.925)

    response = asyncio.run(goals_router.area_search(request))

    assert response.region_label
    assert response.region_label == response.center.label
    assert response.region_label == "신촌동"


def test_area_search_falls_back_to_default_label_without_region_query(monkeypatch):
    """region_query 없이 lat/lng만 준 경우에도 region_label이 비어 있으면 안 된다."""

    spots = [CandidateSpot(lat=37.556, lng=126.925, osm_id=1, integration_top_pct=90.0, highway="secondary")]

    def fake_extract_spots(lat, lng, search_radius_m, max_candidates):
        return spots, []

    async def fake_reverse_geocode(lat, lng):
        return "테스트동 123"

    async def fake_compare_candidates(candidates, **kwargs):
        return DecisionResult(
            ranked=[_fake_ranked(candidates[0].id, candidates[0].label)],
            warnings=[],
            weights_used={},
        )

    monkeypatch.setattr(goals_router, "_extract_spots", fake_extract_spots)
    monkeypatch.setattr(goals_router, "reverse_geocode", fake_reverse_geocode)
    monkeypatch.setattr(goals_router, "compare_candidates", fake_compare_candidates)

    request = goals_router.AreaSearchRequest(lat=37.556, lng=126.925)

    response = asyncio.run(goals_router.area_search(request))

    assert response.region_label == response.center.label
    assert response.region_label != ""
