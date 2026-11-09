import asyncio
from types import SimpleNamespace

from app.models.schemas import LeaseFeasibilityRequest
from app.routers import lease_feasibility as lease_feasibility_router
from app.services.real_competition import compute_real_competition
from app.services.sbiz_stores import StoreRecord


def _store(bizes_id: str, lng: float, lat: float) -> StoreRecord:
    return StoreRecord(
        bizes_id=bizes_id,
        bizes_nm=f"매장{bizes_id}",
        inds_lcls_cd="I2",
        inds_lcls_nm="음식",
        inds_mcls_cd="I212",
        inds_mcls_nm="비알코올 음료점업",
        inds_scls_cd="I21201",
        inds_scls_nm="커피 전문점",
        lon=lng,
        lat=lat,
        bld_mng_no="",
        flr_no="1",
        rdnm_adr="서울 어딘가",
    )


def test_lease_feasibility_call_contract_with_real_competition():
    """Pin the exact kwargs lease_feasibility.py passes to compute_real_competition.

    The service dropped `walk5_count` in a refactor while this caller kept
    passing it, turning every /api/lease-feasibility request into a 500
    TypeError. This calls the service with lease's exact kwargs so a future
    signature change breaks here (fast) instead of only at runtime.
    """
    result = compute_real_competition(
        lat=37.5559,
        lng=126.9370,
        stores=[_store("1", 126.9372, 37.5560), _store("2", 126.9380, 37.5555)],
        vertical="cafe",
        radius_m=500,
    )
    assert result is not None
    assert hasattr(result, "warnings")


ENGINE_FALLBACK_WARNING = "DepthmapX 미사용 (Python 엔진으로 대체, 느릴 수 있음): boom"
DEMAND_FAILURE_WARNING = "수요 데이터: SGIS 응답 지연"


def _lease_upjong() -> SimpleNamespace:
    return SimpleNamespace(
        archetype="food_drink",
        inds_lcls_cd="I2",
        inds_mcls_cd="I212",
        inds_scls_cd="I21201",
        label="카페",
        breadcrumb="음식점업 > 카페",
        business_type="custom",
        match_method="scls",
    )


def _patch_lease_pipeline(monkeypatch, *, site_score_warnings: list[str]):
    async def fake_resolve_point(request):
        return 37.5559, 126.9370

    async def fake_resolve_upjong(request):
        return _lease_upjong()

    async def fake_site_score(score_req):
        return SimpleNamespace(
            total_score=55.0,
            grade="B",
            competition=None,
            demand=None,
            segment=SimpleNamespace(
                integration_top_pct=0.0, choice_top_pct=0.0, connectivity_top_pct=0.0
            ),
            transit=None,
            warnings=list(site_score_warnings),
        )

    async def fake_cannibalization(dash_req):
        return SimpleNamespace(risk_score=None, risk_label="")

    async def fake_fetch_demand_profile(lat, lng):
        return SimpleNamespace(adm_name="서울 서대문구 신촌동")

    async def fake_fetch_sales_benchmark(*args, **kwargs):
        return SimpleNamespace(
            monthly_revenue_man=2000.0,
            per_store_monthly_man=1800.0,
            scope="서울시 서대문구 신촌동",
            data_source="seoul_open_api",
            warnings=[],
        )

    monkeypatch.setattr(lease_feasibility_router, "_resolve_point", fake_resolve_point)
    monkeypatch.setattr(lease_feasibility_router, "_resolve_upjong", fake_resolve_upjong)
    monkeypatch.setattr(lease_feasibility_router, "site_score", fake_site_score)
    monkeypatch.setattr(lease_feasibility_router, "cannibalization", fake_cannibalization)
    monkeypatch.setattr(
        lease_feasibility_router, "fetch_demand_profile", fake_fetch_demand_profile
    )
    monkeypatch.setattr(
        lease_feasibility_router, "fetch_sales_benchmark", fake_fetch_sales_benchmark
    )
    # vertical=None short-circuits the real_competition/model-fit branch, which
    # isn't what this bug is about — keep the fixture focused on warnings.
    monkeypatch.setattr(lease_feasibility_router, "resolve_vertical", lambda **kwargs: None)


def _lease_request() -> LeaseFeasibilityRequest:
    return LeaseFeasibilityRequest(
        lat=37.5559,
        lng=126.9370,
        monthly_rent_man=350,
        maintenance_man=40,
        labor_cost_man=500,
        misc_fixed_man=100,
    )


def test_lease_feasibility_surfaces_site_score_warnings(monkeypatch):
    _patch_lease_pipeline(
        monkeypatch,
        site_score_warnings=[ENGINE_FALLBACK_WARNING, DEMAND_FAILURE_WARNING],
    )

    resp = asyncio.run(lease_feasibility_router.lease_feasibility(_lease_request()))

    # total_score/grade/경쟁 카운트가 전부 이 site_score 결과에서 나오므로,
    # site_score가 엔진 폴백·수요 조회 실패를 겪었다는 사실이 lease-feasibility
    # 응답의 warnings에도 남아야 한다 — 없으면 화면엔 확정치만 보인다.
    assert ENGINE_FALLBACK_WARNING in resp.warnings
    assert DEMAND_FAILURE_WARNING in resp.warnings


def test_lease_feasibility_has_no_spurious_warnings_when_site_score_is_clean(monkeypatch):
    _patch_lease_pipeline(monkeypatch, site_score_warnings=[])

    resp = asyncio.run(lease_feasibility_router.lease_feasibility(_lease_request()))

    assert ENGINE_FALLBACK_WARNING not in resp.warnings
    assert DEMAND_FAILURE_WARNING not in resp.warnings
