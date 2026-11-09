"""Tests for academy model fit with cluster scores."""

from app.services.academy_cluster_analysis import (
    AcademyClusterBucket,
    AcademyClusterResult,
    AcademyScores,
    ParentSafetyDetail,
    compute_academy_cluster,
)
from app.services.academy_model_fit import compute_academy_model_fit
from app.services.restaurant_model_fit import SiteMetrics
from app.services.sbiz_stores import StoreRecord


def _store(name: str, lat: float, lon: float, scls_nm: str) -> StoreRecord:
    return StoreRecord(
        bizes_id=name,
        bizes_nm=name,
        inds_lcls_cd="P1",
        inds_lcls_nm="교육",
        inds_mcls_cd="P105",
        inds_mcls_nm="일반 교육기관",
        inds_scls_cd="P10501",
        inds_scls_nm=scls_nm,
        lon=lon,
        lat=lat,
        bld_mng_no="",
        flr_no="",
        rdnm_adr="",
    )


def test_synergy_cluster_scores_better_than_pressure_only():
    site = SiteMetrics(school_age_pct=22, parent_pct=34, apartment_pct=50)
    pressure_only = AcademyClusterResult(
        target_model="math",
        target_label="수학학원",
        clusters=[],
        scores=AcademyScores(
            direct_competition_pressure=70,
            cluster_synergy=10,
            parent_safety=85,
        ),
        parent_safety_detail=ParentSafetyDetail(level="양호", sensitivity_note=""),
        headline="",
        interpretation=[],
    )
    synergy_rich = AcademyClusterResult(
        target_model="math",
        target_label="수학학원",
        clusters=[
            AcademyClusterBucket(
                key="synergy",
                label="보완 시너지",
                relation_group="synergy",
                count=5,
                weighted_score=3.0,
                intensity="보통",
            )
        ],
        scores=AcademyScores(
            direct_competition_pressure=70,
            cluster_synergy=65,
            parent_safety=85,
        ),
        parent_safety_detail=ParentSafetyDetail(level="양호", sensitivity_note=""),
        headline="",
        interpretation=[],
    )
    low_synergy = compute_academy_model_fit(site, pressure_only)
    high_synergy = compute_academy_model_fit(site, synergy_rich)
    exam_low = next(m for m in low_synergy.models if m.model_id == "exam_hs")
    exam_high = next(m for m in high_synergy.models if m.model_id == "exam_hs")
    assert exam_high.score >= exam_low.score


def test_integrated_cluster_math_english_fixture():
    stores = [
        _store("수학학원A", 37.5601, 126.9401, "수학학원"),
        _store("영어학원B", 37.5602, 126.9402, "영어학원"),
    ]
    cluster = compute_academy_cluster(
        lat=37.56,
        lng=126.94,
        stores=stores,
        target_model="math",
        radius_m=500,
    )
    result = compute_academy_model_fit(
        SiteMetrics(school_age_pct=24, parent_pct=35, apartment_pct=48),
        cluster,
    )
    assert result.vertical == "academy"
    assert any(m.model_id == "english_math" for m in result.models)
