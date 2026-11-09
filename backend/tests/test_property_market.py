"""Unit tests for property_market pure logic (no DB)."""

from app.services.property_market import (
    AREA_TOLERANCE,
    _build_where,
    _filter_levels,
    floor_bucket,
)


def test_floor_bucket():
    assert floor_bucket("1") == "first"
    assert floor_bucket("2") == "upper"
    assert floor_bucket("15") == "upper"
    assert floor_bucket("-1") == "basement"
    assert floor_bucket("-2") == "basement"
    assert floor_bucket("") == "unknown"
    assert floor_bucket(None) == "unknown"
    assert floor_bucket("B1") == "unknown"


def test_filter_levels_commercial_full():
    levels = _filter_levels("commercial", "first", 85.0)
    # 엄격 → 면적 해제 → 층 해제 순
    assert [(f, a) for f, a, _ in levels] == [
        (True, True), (True, False), (False, False)
    ]
    assert levels[0][2] == []
    assert "면적 ±20% 필터 해제" in levels[1][2]
    assert "층 버킷 필터 해제" in levels[2][2]


def test_filter_levels_officetel_no_floor():
    levels = _filter_levels("officetel", "upper", 32.0)
    # 오피스텔은 층 필터 없음: 면적 → 없음
    assert [(f, a) for f, a, _ in levels] == [(False, True), (False, False)]


def test_filter_levels_no_inputs():
    levels = _filter_levels("commercial", "unknown", None)
    assert [(f, a) for f, a, _ in levels] == [(False, False)]


def test_build_where_strict():
    where, params = _build_where(
        "commercial", "집합", 12, True, "first", True, 100.0
    )
    assert "building_type = %s" in where
    assert "cdeal_type <> 'O'" in where
    assert "share_dealing_type = ''" in where
    assert "직거래" in where
    assert "geo_precision IN ('exact', 'parcel')" in where
    assert "floor = '1'" in where
    assert "area_sqm BETWEEN %s AND %s" in where
    assert params == [
        "commercial", "집합", 12,
        100.0 * (1 - AREA_TOLERANCE), 100.0 * (1 + AREA_TOLERANCE),
    ]


def test_build_where_basement_and_upper():
    where_b, _ = _build_where("commercial", "집합", 12, True, "basement", False, None)
    assert "floor ~ '^-'" in where_b
    where_u, _ = _build_where("commercial", "집합", 12, True, "upper", False, None)
    assert "floor <> '1'" in where_u


def test_build_where_no_optional_filters():
    where, params = _build_where("officetel", "집합", 24, False, "unknown", False, None)
    assert "floor" not in where.replace("share_dealing", "")
    assert "area_sqm" not in where
    assert params == ["officetel", "집합", 24]
