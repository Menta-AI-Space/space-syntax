"""Tests for academy cluster classification and scoring."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.models.schemas import AcademyClusterRequest
from app.routers import academy_cluster as academy_cluster_router
from app.services.academy_cluster_analysis import (
    AcademyClusterResult,
    AcademyScores,
    ParentSafetyDetail,
    classify_store,
    compute_academy_cluster,
    resolve_relation,
)
from app.services.restaurant_model_fit import ModelFitItem, ModelFitResult
from app.services.sbiz_stores import StoreRecord


def _store(
    name: str,
    mcls: str = "P105",
    mcls_nm: str = "일반 교육기관",
    scls: str = "P10501",
    scls_nm: str = "입시·교과",
    lat: float = 37.5601,
    lon: float = 126.9401,
    bld_mng_no: str = "",
) -> StoreRecord:
    return StoreRecord(
        bizes_id=name,
        bizes_nm=name,
        inds_lcls_cd="P1",
        inds_lcls_nm="교육",
        inds_mcls_cd=mcls,
        inds_mcls_nm=mcls_nm,
        inds_scls_cd=scls,
        inds_scls_nm=scls_nm,
        lon=lon,
        lat=lat,
        bld_mng_no=bld_mng_no,
        flr_no="",
        rdnm_adr="",
    )


def test_classify_math_and_english():
    math_store = _store("청담수학학원", scls_nm="입시·교과학원")
    english_store = _store(
        "YBM영어학원",
        mcls="P106",
        mcls_nm="기타 교육기관",
        scls="P10615",
        scls_nm="외국어학원",
    )
    pc_store = _store(
        "PC존",
        mcls="R104",
        mcls_nm="유원지 및 기타 오락관련 서비스업",
        scls="R10406",
        scls_nm="PC방",
    )

    assert classify_store(math_store) == "math"
    assert classify_store(english_store) == "english"
    assert classify_store(pc_store) == "risk_pc"


def test_math_target_english_is_synergy_not_direct():
    assert resolve_relation("math", "math")[0] == "direct_competitor"
    assert resolve_relation("math", "english")[0] == "synergy"
    assert resolve_relation("english", "math")[0] == "synergy"


def test_math_cluster_separates_direct_synergy_risk():
    stores = [
        _store("수학의정석", scls_nm="입시·교과학원", lat=37.5602, lon=126.9402),
        _store(
            "영어의정석",
            mcls="P106",
            mcls_nm="기타 교육기관",
            scls="P10615",
            scls_nm="외국어학원",
            lat=37.5603,
            lon=126.9403,
        ),
        _store(
            "옆집PC방",
            mcls="R104",
            mcls_nm="유원지 및 기타 오락관련 서비스업",
            scls="R10406",
            scls_nm="PC방",
            lat=37.5604,
            lon=126.9404,
        ),
    ]
    result = compute_academy_cluster(
        lat=37.56,
        lng=126.94,
        stores=stores,
        target_model="math",
        radius_m=500,
    )
    direct = next(c for c in result.clusters if c.key == "direct")
    synergy = next(c for c in result.clusters if c.key == "synergy")
    risk = next(c for c in result.clusters if c.key == "risk")

    assert direct.count == 1
    assert synergy.count == 1
    assert risk.count == 1
    assert result.scores.cluster_synergy > 0
    assert result.scores.direct_competition_pressure > 0


def test_same_building_pc_doubles_risk_impact():
    site_bld = "BLD123"
    stores = [
        _store(
            "건물내PC방",
            mcls="R104",
            mcls_nm="유원지 및 기타 오락관련 서비스업",
            scls="R10406",
            scls_nm="PC방",
            lat=37.56005,
            lon=126.94005,
            bld_mng_no=site_bld,
        ),
        _store(
            "멀리PC방",
            mcls="R104",
            mcls_nm="유원지 및 기타 오락관련 서비스업",
            scls="R10406",
            scls_nm="PC방",
            lat=37.5615,
            lon=126.942,
            bld_mng_no="OTHER",
        ),
    ]
    result = compute_academy_cluster(
        lat=37.56,
        lng=126.94,
        stores=stores,
        target_model="math",
        radius_m=500,
        bld_mng_no=site_bld,
    )
    assert "건물내PC방" in result.parent_safety_detail.same_building_risks
    assert result.parent_safety_detail.level == "주의"
    assert result.scores.parent_safety < 80


# ─────── site_bld_mng_no 추정(최근접 점포 건물번호로 대체)이 warnings에 안 남는 버그 ───────
#
# 후보지의 건물관리번호(bld_mng_no)를 입력하지 않으면 엔진은 반경 내 최근접 점포의
# 건물번호를 후보지 건물번호로 대체 추정한다. 이 추정치로 "동일건물" 여부를 판정해
# 유해시설 가중치·학부모 안심도를 계산하는데, 추정을 썼다는 사실이 warnings에 전혀
# 남지 않으면 화면에는 확정치처럼 보인다.


def test_missing_bld_mng_no_is_estimated_and_warned():
    site_bld = "BLD123"
    stores = [
        _store(
            "건물내PC방",
            mcls="R104",
            mcls_nm="유원지 및 기타 오락관련 서비스업",
            scls="R10406",
            scls_nm="PC방",
            lat=37.56005,
            lon=126.94005,
            bld_mng_no=site_bld,
        ),
    ]
    result = compute_academy_cluster(
        lat=37.56,
        lng=126.94,
        stores=stores,
        target_model="math",
        radius_m=500,
        # bld_mng_no is omitted -> falls back to nearest-store inference.
    )
    risk = next(c for c in result.clusters if c.key == "risk")
    assert risk.stores[0].same_building is True
    assert "건물내PC방" in result.parent_safety_detail.same_building_risks
    assert any("추정" in w and ("건물" in w) for w in result.warnings)


def test_explicit_bld_mng_no_produces_no_estimation_warning():
    site_bld = "BLD123"
    stores = [
        _store(
            "건물내PC방",
            mcls="R104",
            mcls_nm="유원지 및 기타 오락관련 서비스업",
            scls="R10406",
            scls_nm="PC방",
            lat=37.56005,
            lon=126.94005,
            bld_mng_no=site_bld,
        ),
    ]
    result = compute_academy_cluster(
        lat=37.56,
        lng=126.94,
        stores=stores,
        target_model="math",
        radius_m=500,
        bld_mng_no=site_bld,
    )
    risk = next(c for c in result.clusters if c.key == "risk")
    assert risk.stores[0].same_building is True
    assert result.warnings == []


def test_missing_bld_mng_no_with_no_candidate_produces_no_estimation_warning():
    stores = [
        _store(
            "건물내PC방",
            mcls="R104",
            mcls_nm="유원지 및 기타 오락관련 서비스업",
            scls="R10406",
            scls_nm="PC방",
            lat=37.56005,
            lon=126.94005,
            bld_mng_no="",
        ),
    ]
    result = compute_academy_cluster(
        lat=37.56,
        lng=126.94,
        stores=stores,
        target_model="math",
        radius_m=500,
    )
    risk = next(c for c in result.clusters if c.key == "risk")
    assert risk.stores[0].same_building is False
    assert result.warnings == []


# ─────────────────── model_fit이 site_score 경고를 삼키는 버그 ───────────────────
#
# academy_cluster의 모델 적합도(model_fit)는 site_score를 다시 호출해 인구·접근성
# metrics를 뽑아낸다. 그 site_score가 "DepthmapX 미사용(엔진 폴백)"이나 "수요 데이터
# 조회 실패" 같은 경고를 내면, 그 metrics로 계산한 모델 적합도 점수도 같은 이유로
# 신뢰도가 떨어진 것이다. 이 경고가 model_fit.warnings와 번들 전체 warnings에 그대로
# 보여야 한다 — 조용히 버려지면 화면에는 확정 점수만 남고 사용자는 신뢰도가 낮다는
# 걸 알 방법이 없다.

ENGINE_FALLBACK_WARNING = "DepthmapX 미사용 (Python 엔진으로 대체, 느릴 수 있음): boom"
DEMAND_FAILURE_WARNING = "수요 데이터: SGIS 응답 지연"


def _fake_cluster_result() -> AcademyClusterResult:
    return AcademyClusterResult(
        target_model="math",
        target_label="수학학원",
        clusters=[],
        scores=AcademyScores(
            direct_competition_pressure=10.0, cluster_synergy=20.0, parent_safety=80.0
        ),
        parent_safety_detail=ParentSafetyDetail(level="양호"),
        headline="",
        interpretation=[],
        warnings=[],
    )


def _academy_upjong() -> SimpleNamespace:
    return SimpleNamespace(archetype="academy", inds_mcls_cd="P105")


def _patch_common_pipeline(monkeypatch, *, site_score_warnings: list[str]):
    async def fake_resolve_point(request):
        return 37.5601, 126.9401

    async def fake_resolve_upjong(request):
        return _academy_upjong()

    async def fake_load_context(lat, lng, radius_m, upjong):
        return [object()], object(), [], upjong, None, []

    def fake_compute_isochrones(**kwargs):
        return SimpleNamespace(warnings=[], bands=[])

    def fake_compute_academy_cluster(**kwargs):
        return _fake_cluster_result()

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

    def fake_compute_academy_model_fit(metrics, cluster):
        return ModelFitResult(
            vertical="academy",
            headline="적당한 입지입니다.",
            models=[
                ModelFitItem(
                    model_id="elem_tutor", label="초등 보습학원", score=55.0,
                    verdict="보통", detail="d",
                ),
                ModelFitItem(
                    model_id="exam_hs", label="중고등 입시학원", score=50.0,
                    verdict="보통", detail="d",
                ),
            ],
            warnings=[],
        )

    monkeypatch.setattr(academy_cluster_router, "_resolve_point", fake_resolve_point)
    monkeypatch.setattr(academy_cluster_router, "_resolve_upjong", fake_resolve_upjong)
    monkeypatch.setattr(
        academy_cluster_router, "is_academy_upjong", lambda **kwargs: True
    )
    monkeypatch.setattr(academy_cluster_router, "_load_context", fake_load_context)
    monkeypatch.setattr(
        academy_cluster_router, "compute_isochrones", fake_compute_isochrones
    )
    monkeypatch.setattr(
        academy_cluster_router, "compute_academy_cluster", fake_compute_academy_cluster
    )
    monkeypatch.setattr(academy_cluster_router, "site_score", fake_site_score)
    monkeypatch.setattr(
        academy_cluster_router,
        "compute_academy_model_fit",
        fake_compute_academy_model_fit,
    )


def test_model_fit_surfaces_site_score_engine_fallback_and_demand_failure(monkeypatch):
    _patch_common_pipeline(
        monkeypatch,
        site_score_warnings=[ENGINE_FALLBACK_WARNING, DEMAND_FAILURE_WARNING],
    )
    request = AcademyClusterRequest(
        lat=37.5601, lng=126.9401, radius_m=500, target_model="math"
    )

    resp = asyncio.run(academy_cluster_router.run_academy_cluster_analysis(request))

    assert resp.model_fit is not None
    assert ENGINE_FALLBACK_WARNING in resp.model_fit.warnings
    assert DEMAND_FAILURE_WARNING in resp.model_fit.warnings
    assert ENGINE_FALLBACK_WARNING in resp.warnings
    assert DEMAND_FAILURE_WARNING in resp.warnings


def test_model_fit_has_no_spurious_warnings_when_site_score_is_clean(monkeypatch):
    _patch_common_pipeline(monkeypatch, site_score_warnings=[])
    request = AcademyClusterRequest(
        lat=37.5601, lng=126.9401, radius_m=500, target_model="math"
    )

    resp = asyncio.run(academy_cluster_router.run_academy_cluster_analysis(request))

    assert resp.model_fit is not None
    assert ENGINE_FALLBACK_WARNING not in resp.model_fit.warnings
    assert DEMAND_FAILURE_WARNING not in resp.model_fit.warnings
