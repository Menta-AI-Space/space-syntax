"""real_competition 모델 적합도가 site_score.warnings(엔진 폴백·수요 조회
실패)를 무시하고 신뢰도 표시 없이 점수를 내는 버그의 회귀 테스트.

site_score가 "DepthmapX 미사용(엔진 폴백)"·"수요 데이터 조회 실패" 같은 경고를
내면, 그 site_score 결과에서 뽑아낸 metrics로 계산한 모델 적합도 점수도 같은
근거로 신뢰도가 떨어진 것이다. 이 경고가 모델 적합도 응답(model_fit.warnings)과
번들 전체 warnings에 그대로 보여야 한다 — 조용히 버려지면 화면에는 확정 점수만
남고 사용자는 신뢰도가 낮다는 걸 알 방법이 없다.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.models.schemas import RealCompetitionRequest
from app.routers import real_competition as real_competition_router
from app.services.real_competition import RealCompetitionResult
from app.services.restaurant_model_fit import ModelFitItem, ModelFitResult

ENGINE_FALLBACK_WARNING = "DepthmapX 미사용 (Python 엔진으로 대체, 느릴 수 있음): boom"
DEMAND_FAILURE_WARNING = "수요 데이터: SGIS 응답 지연"


def _restaurant_upjong() -> SimpleNamespace:
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


def _fake_comp(**overrides) -> RealCompetitionResult:
    base = dict(
        vertical="restaurant",
        total_weighted_score=1.0,
        total_intensity="보통",
        buckets=[],
        summary_one_liner="",
        reach_mode="walk",
        reach_minutes=15,
        reach_label="도보 15분 권역",
        warnings=[],
    )
    base.update(overrides)
    return RealCompetitionResult(**base)


def _patch_common_pipeline(monkeypatch, *, site_score_warnings: list[str]):
    async def fake_resolve_point(request):
        return 37.5559, 126.9370

    async def fake_resolve_upjong(request):
        return _restaurant_upjong()

    async def fake_load_context(lat, lng, radius_m, upjong):
        return [object()], object(), [], upjong, None, []

    def fake_compute_isochrones(**kwargs):
        return SimpleNamespace(warnings=[], bands=[])

    def fake_compute_real_competition(**kwargs):
        return _fake_comp()

    async def fake_site_score(score_req):
        return SimpleNamespace(
            demand=None,
            segment=SimpleNamespace(
                integration_top_pct=0.0, choice_top_pct=0.0, connectivity_top_pct=0.0
            ),
            transit=None,
            total_score=55.0,
            warnings=list(site_score_warnings),
        )

    def fake_compute_restaurant_model_fit(metrics, comp):
        return ModelFitResult(
            vertical="restaurant",
            headline="적당한 입지입니다.",
            models=[
                ModelFitItem(
                    model_id="lunch", label="점심 상권", score=55.0, verdict="보통", detail="d"
                )
            ],
            warnings=[],
        )

    monkeypatch.setattr(real_competition_router, "_resolve_point", fake_resolve_point)
    monkeypatch.setattr(real_competition_router, "_resolve_upjong", fake_resolve_upjong)
    monkeypatch.setattr(
        real_competition_router, "resolve_vertical", lambda **kwargs: "restaurant"
    )
    monkeypatch.setattr(real_competition_router, "_load_context", fake_load_context)
    monkeypatch.setattr(
        real_competition_router, "competition_fetch_radius_m", lambda *a, **k: 500
    )
    monkeypatch.setattr(
        real_competition_router, "compute_isochrones", fake_compute_isochrones
    )
    monkeypatch.setattr(
        real_competition_router, "compute_real_competition", fake_compute_real_competition
    )
    monkeypatch.setattr(real_competition_router, "site_score", fake_site_score)
    monkeypatch.setattr(
        real_competition_router,
        "compute_restaurant_model_fit",
        fake_compute_restaurant_model_fit,
    )


def test_model_fit_surfaces_site_score_engine_fallback_and_demand_failure(monkeypatch):
    _patch_common_pipeline(
        monkeypatch,
        site_score_warnings=[ENGINE_FALLBACK_WARNING, DEMAND_FAILURE_WARNING],
    )
    request = RealCompetitionRequest(lat=37.5559, lng=126.9370, radius_m=500)

    resp = asyncio.run(real_competition_router.real_competition(request))

    assert resp.model_fit is not None
    # 모델 적합도 점수는 여전히 나오지만(적합도 분석 자체는 지원되는 업종),
    # 그 점수를 뽑아낸 site_score가 엔진 폴백·수요 조회 실패를 겪었다는 사실이
    # model_fit.warnings에 남아야 한다 — 이게 없으면 화면에는 확정 점수만
    # 보이고 신뢰도가 낮다는 걸 알 길이 없다.
    assert ENGINE_FALLBACK_WARNING in resp.model_fit.warnings
    assert DEMAND_FAILURE_WARNING in resp.model_fit.warnings
    # 번들 전체 warnings에도 전달돼야 다른 화면(요약 배너 등)에서도 보인다.
    assert ENGINE_FALLBACK_WARNING in resp.warnings
    assert DEMAND_FAILURE_WARNING in resp.warnings


def test_model_fit_has_no_spurious_warnings_when_site_score_is_clean(monkeypatch):
    _patch_common_pipeline(monkeypatch, site_score_warnings=[])
    request = RealCompetitionRequest(lat=37.5559, lng=126.9370, radius_m=500)

    resp = asyncio.run(real_competition_router.real_competition(request))

    assert resp.model_fit is not None
    assert ENGINE_FALLBACK_WARNING not in resp.model_fit.warnings
    assert DEMAND_FAILURE_WARNING not in resp.model_fit.warnings
