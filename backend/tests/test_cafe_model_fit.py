"""Tests for cafe model fit scoring."""

from app.services.cafe_model_fit import compute_cafe_model_fit
from app.services.real_competition import compute_real_competition
from app.services.restaurant_model_fit import SiteMetrics
from app.services.sbiz_stores import StoreRecord


def _cafe_store(name: str) -> StoreRecord:
    return StoreRecord(
        bizes_id=name,
        bizes_nm=name,
        inds_lcls_cd="I2",
        inds_lcls_nm="음식",
        inds_mcls_cd="I212",
        inds_mcls_nm="비알코올 음료점업",
        inds_scls_cd="I21201",
        inds_scls_nm="카페",
        lon=126.94,
        lat=37.56,
        bld_mng_no="",
        flr_no="",
        rdnm_adr="",
    )


def test_office_takeout_higher_with_workers():
    site = SiteMetrics(workers=9000, transit_distance_m=280, choice_top_pct=30)
    result = compute_cafe_model_fit(site, None)
    office = next(m for m in result.models if m.model_id == "office_takeout")
    large = next(m for m in result.models if m.model_id == "large_stay")
    assert office.score > large.score


def test_low_price_takeout_penalized_by_brands():
    stores = [_cafe_store("메가엠지씨커피"), _cafe_store("더벤티")]
    comp = compute_real_competition(
        lat=37.56, lng=126.94, stores=stores, vertical="cafe", radius_m=500
    )
    site = SiteMetrics(workers=6000, transit_distance_m=300)
    result = compute_cafe_model_fit(site, comp)
    low = next(m for m in result.models if m.model_id == "low_price_takeout")
    assert low.verdict in ("보통", "낮음")
