"""dashboard_bundle의 territory 분기(모토 광역 재조회)가 store/demand 조회 실패
경고를 통째로 버리는 버그의 회귀 테스트.

territory_radius가 core_radius보다 넓으면(walk/bike 등 대부분의 경우, moto 배달권은
항상 더 넓다) dashboard_bundle은 `_load_context_light`로 광역 재조회를 한 번 더
한다. 이 재조회에서 가맹점/수요 조회가 실패하면 경고가 나오는데, 그 경고가
응답 어디에도 나타나지 않으면 화면에는 확정된 territory 결과만 보이고 사용자는
일부 데이터가 빠졌다는 걸 알 방법이 없다.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.models.schemas import (
    CannibalizationResponse,
    DashboardPointRequest,
    TerritoryAnalysisResponse,
    TerritoryDemandOut,
)
from app.routers import dashboard as dashboard_router

STORE_FETCH_FAILURE = "가맹점 조회 실패: SbizError timeout"
DEMAND_FETCH_FAILURE = "수요 데이터 조회 실패: SGIS 응답 지연"


def _upjong() -> SimpleNamespace:
    return SimpleNamespace(
        archetype="restaurant",
        inds_lcls_cd="I2",
        inds_mcls_cd="I201",
        inds_scls_cd="I20101",
        label="한식 음식점",
        breadcrumb="음식점업 > 한식 음식점",
        business_type="custom",
        match_method="scls",
    )


def _fake_territory_response(**overrides) -> TerritoryAnalysisResponse:
    base = dict(
        delivery_mode="moto",
        delivery_minutes=15,
        model_fit_mode="moto",
        model_fit_minutes=15,
        before_territories=[],
        after_territories=[],
        zones=[],
        demand=TerritoryDemandOut(),
        brand_fit_headline="",
        brand_fit_bullets=[],
        narrative=[],
        warnings=[],
    )
    base.update(overrides)
    return TerritoryAnalysisResponse(**base)


def _patch_common_pipeline(monkeypatch, *, territory_light_warnings: list[str]):
    async def fake_resolve_point(request):
        return 37.5559, 126.9370

    async def fake_resolve_upjong(request):
        return _upjong()

    async def fake_load_reach_context(lat, lng, radius_m, upjong, *, mode):
        # Core context is clean — only the territory's wider re-fetch fails.
        return [object()], object(), [], upjong, None, []

    def fake_compute_isochrones(**kwargs):
        return SimpleNamespace(
            lat=37.5559, lng=126.9370, mode="walk", start_connected=True,
            warnings=[], bands=[],
        )

    async def fake_cannibalization_response(**kwargs):
        return CannibalizationResponse(
            risk_score=0.0, risk_label="낮음", competitors=[], detail="",
        )

    async def fake_compute_trends(*args, **kwargs):
        return SimpleNamespace(
            warnings=[], category_label="한식 음식점", series=None,
            street_signals=[], data_scope="dong",
        )

    async def fake_load_context_light(lat, lng, radius_m, upjong):
        return [], None, [], upjong, None, list(territory_light_warnings)

    async def fake_territory_analysis_response(**kwargs):
        return _fake_territory_response()

    monkeypatch.setattr(dashboard_router, "_resolve_point", fake_resolve_point)
    monkeypatch.setattr(dashboard_router, "_resolve_upjong", fake_resolve_upjong)
    monkeypatch.setattr(
        dashboard_router, "_load_reach_context", fake_load_reach_context
    )
    monkeypatch.setattr(
        dashboard_router, "compute_isochrones", fake_compute_isochrones
    )
    monkeypatch.setattr(
        dashboard_router, "_cannibalization_response", fake_cannibalization_response
    )
    monkeypatch.setattr(dashboard_router, "compute_trends", fake_compute_trends)
    monkeypatch.setattr(
        dashboard_router, "_load_context_light", fake_load_context_light
    )
    monkeypatch.setattr(
        dashboard_router,
        "_territory_analysis_response",
        fake_territory_analysis_response,
    )


def _wide_territory_request() -> DashboardPointRequest:
    # walk/500m: core fetch radius (1350m) < territory delivery radius (2953m),
    # so dashboard_bundle takes the wide re-fetch branch for territory.
    return DashboardPointRequest(
        lat=37.5559, lng=126.9370, radius_m=500, mode="walk",
        minutes=[5, 10, 15], competitor_minutes=5, include_territory=True,
    )


def test_territory_wide_refetch_failure_surfaces_in_territory_warnings(monkeypatch):
    _patch_common_pipeline(
        monkeypatch,
        territory_light_warnings=[STORE_FETCH_FAILURE, DEMAND_FETCH_FAILURE],
    )
    request = _wide_territory_request()

    resp = asyncio.run(dashboard_router.dashboard_bundle(request))

    assert resp.territory is not None
    # 버그 상태에서는 이 경고가 지역 `warnings` 리스트에만 append되고, 그 리스트는
    # territory 응답 생성 이후 어디에도 쓰이지 않아 통째로 사라졌다.
    assert STORE_FETCH_FAILURE in resp.territory.warnings
    assert DEMAND_FETCH_FAILURE in resp.territory.warnings


def test_territory_wide_refetch_has_no_spurious_warnings_when_clean(monkeypatch):
    _patch_common_pipeline(monkeypatch, territory_light_warnings=[])
    request = _wide_territory_request()

    resp = asyncio.run(dashboard_router.dashboard_bundle(request))

    assert resp.territory is not None
    assert resp.territory.warnings == []
