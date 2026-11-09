"""reb_rent 순수 로직 테스트 (DB 없이)."""

from app.services.reb_rent import (
    BUILDING_TYPE_TO_REB,
    DATA_SOURCE,
    RebRentReference,
    RebRentType,
    compute_rent_band,
    monthly_rent_band_manwon,
)


def test_building_type_mapping():
    assert BUILDING_TYPE_TO_REB["집합"] == ("집합",)
    assert BUILDING_TYPE_TO_REB["일반"] == ("중대형", "소규모")


def test_monthly_rent_band_manwon():
    ref = RebRentReference(
        matched=True, sanggwon_nm="신촌/이대", quarter="2026.1Q",
        rent_low_krw_per_sqm=74875.0, rent_high_krw_per_sqm=74875.0,
    )
    band = monthly_rent_band_manwon(ref, 60.0)
    assert band == (449.0, 449.0)  # 74875 × 60 / 10000 = 449.25 → 449


def test_monthly_rent_band_range_for_ilban():
    ref = RebRentReference(
        matched=True, rent_low_krw_per_sqm=40000.0, rent_high_krw_per_sqm=58000.0,
    )
    low, high = monthly_rent_band_manwon(ref, 100.0)
    assert low == 400.0
    assert high == 580.0


def test_monthly_rent_band_none_when_no_rent():
    ref = RebRentReference(matched=True)
    assert monthly_rent_band_manwon(ref, 60.0) is None


def test_compute_rent_band_direct_type():
    # 집합 데이터가 있으면 그대로 사용, fallback 없음
    types = [
        RebRentType("집합", 74875.0, 12.4),
        RebRentType("중대형", 57856.0, 12.3),
        RebRentType("소규모", 40731.0, 15.1),
    ]
    low, high, vac, src = compute_rent_band(types, ("집합",), "집합")
    assert low == 74875.0 and high == 74875.0
    assert vac == 12.4
    assert src == DATA_SOURCE  # fallback 아님


def test_compute_rent_band_fallback_when_type_missing():
    # 명동처럼 집합 데이터가 없으면 가용 유형(소규모/중대형)으로 대체 + 출처 명시
    types = [
        RebRentType("중대형", 215256.0, 5.0),
        RebRentType("소규모", 147578.0, 5.3),
    ]
    low, high, vac, src = compute_rent_band(types, ("집합",), "집합")
    assert low == 147578.0 and high == 215256.0
    assert vac == round((5.0 + 5.3) / 2, 1)
    assert "집합상가 조사 없어" in src
    assert "소규모" in src and "중대형" in src


def test_compute_rent_band_empty():
    low, high, vac, src = compute_rent_band([], ("집합",), "집합")
    assert low is None and high is None and vac is None
    assert src == DATA_SOURCE


def test_compute_rent_band_ilban_uses_mid_and_small():
    types = [
        RebRentType("집합", 90000.0, 10.0),
        RebRentType("중대형", 60000.0, 8.0),
        RebRentType("소규모", 40000.0, 12.0),
    ]
    # 일반(통건물) → 중대형+소규모 범위, 집합 제외
    low, high, vac, src = compute_rent_band(types, ("중대형", "소규모"), "일반")
    assert low == 40000.0 and high == 60000.0
    assert src == DATA_SOURCE


def test_reb_rent_type_dataclass():
    t = RebRentType("중대형", 57856.0, 12.3)
    assert t.property_type == "중대형"
    assert t.rent_krw_per_sqm == 57856.0
    assert t.vacancy_pct == 12.3
