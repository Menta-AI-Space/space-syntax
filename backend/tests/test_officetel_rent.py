"""오피스텔 월세 밴드 순수 로직 테스트 (DB 없이)."""

from app.services.officetel_rent import (
    AREA_TOLERANCE,
    OfficetelRentBand,
    area_bounds_for,
    build_rent_where,
)


def test_area_bounds_for():
    assert area_bounds_for(None) is None
    lo, hi = area_bounds_for(30.0)
    assert lo == 30.0 * (1 - AREA_TOLERANCE)
    assert hi == 30.0 * (1 + AREA_TOLERANCE)


def test_build_rent_where_excludes_jeonse_and_bad_geo():
    where, params = build_rent_where(12, None)
    assert "monthly_rent_manwon > 0" in where  # 전세(0) 제외
    assert "geo_precision IN ('exact', 'parcel')" in where  # approx 제외
    assert "make_interval(months => %s)" in where
    assert params == [12]
    assert "area_sqm" not in where


def test_build_rent_where_with_area():
    where, params = build_rent_where(24, (24.0, 36.0))
    assert "area_sqm BETWEEN %s AND %s" in where
    assert params == [24, 24.0, 36.0]


def test_band_dataclass_defaults():
    b = OfficetelRentBand(
        n=0, radius_m=500, months_back=12,
        monthly_rent_p25_manwon=None, monthly_rent_p50_manwon=None,
        monthly_rent_p75_manwon=None, deposit_median_manwon=None,
        min_deal_date=None, max_deal_date=None,
    )
    assert b.relaxations == []
    assert b.insufficient_sample is False
    assert "오피스텔 전월세 실거래" in b.data_source
