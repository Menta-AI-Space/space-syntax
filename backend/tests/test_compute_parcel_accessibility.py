"""DB 없이 도는 순수 함수 테스트 — tools/compute_parcel_accessibility.py.

이 프로젝트 관례대로(tests/test_ingest_*.py) DB에 붙지 않는 부분만 고정한다.
여기서 지키려는 계약은 셋이다:

1. **거리 결측을 0으로 채우지 않는다** — 채우면 "정보 없음"이 "바로 옆"이 된다.
2. **간선도로 등급 집합이 의도대로다** — 램프(_link)와 집산도로(tertiary)가
   섞여 들어오면 feature 8의 의미가 무너진다.
3. **SQL이 필요한 컬럼을 다 담고, 키가 (pnu) 하나다** — 이 표는 시불변이라
   연도 축이 없다. 연도가 키에 들어오면 leakage처럼 보이는 구조가 된다.
"""

from __future__ import annotations

from datetime import date

import pytest

from tools.compute_parcel_accessibility import (
    ARTERIAL_HIGHWAY_GRADES,
    BUS_RADII_M,
    DEFAULT_SGG_CODES,
    DISTANCE_COLUMNS,
    FEATURE_COLUMNS,
    INSERT_COLUMNS,
    STATION_RADII_M,
    _PARCEL_SQL,
    _TEMP_SQL,
    _UPSERT_SQL,
    AccessibilityRow,
    build_row,
    clean_count,
    clean_distance,
    summarize_distances,
)

AS_OF = date(2026, 6, 30)


def _raw(**over) -> dict:
    """SQL 한 행의 기본 모양. 필요한 필드만 덮어쓴다."""
    base = {
        "pnu": "1154510100100010000",
        "sgg_code": "11545",
        "nearest_station_m": 412.5,
        "nearest_station_name": "독산",
        "nearest_entrance_m": 380.1,
        "stations_within_500m": 1,
        "stations_within_1000m": 2,
        "nearest_bus_stop_m": 88.0,
        "bus_stops_within_300m": 4,
        "bus_stops_within_500m": 9,
        "nearest_arterial_m": 120.0,
        "nearest_road_m": 12.3,
    }
    base.update(over)
    return base


# --- 1. 결측 처리 ------------------------------------------------------------


def test_clean_distance_keeps_none_instead_of_zero():
    # 이 테스트가 이 파일에서 가장 중요하다. 0으로 채우면 모델이
    # "반경 안에 아무것도 없는 필지"를 "시설 바로 옆"으로 배운다.
    assert clean_distance(None) is None


def test_clean_distance_passes_real_zero_through():
    # 반대로 진짜 0m(도로 위에 걸친 필지 대표점)는 값이므로 살려야 한다.
    assert clean_distance(0) == 0.0


def test_clean_distance_casts_to_float():
    assert clean_distance(5) == 5.0
    assert isinstance(clean_distance(5), float)


def test_clean_distance_rejects_negative():
    # `<->`는 음수를 내지 않는다. 나오면 데이터 이상이므로 값으로 쓰지 않는다.
    assert clean_distance(-1.0) is None


def test_clean_count_treats_missing_as_zero():
    # 거리와 달리 개수는 0이 정답이다 ("반경 안에 없음" 자체가 정보).
    assert clean_count(None) == 0
    assert clean_count(0) == 0
    assert clean_count(3) == 3


def test_build_row_preserves_all_distance_nulls():
    raw = _raw(
        nearest_station_m=None,
        nearest_entrance_m=None,
        nearest_bus_stop_m=None,
        nearest_arterial_m=None,
        nearest_road_m=None,
        stations_within_500m=0,
        stations_within_1000m=0,
        bus_stops_within_300m=0,
        bus_stops_within_500m=0,
    )
    row = build_row(raw, AS_OF)
    for col in DISTANCE_COLUMNS:
        assert getattr(row, col) is None, f"{col}이 0으로 채워졌다"
    # 개수는 0으로 남는다 (결측이 아니다).
    assert row.stations_within_500m == 0
    assert row.bus_stops_within_500m == 0


def test_build_row_blank_station_name_becomes_none():
    # 이름 없는 역 노드가 빈 문자열로 오면 NULL로 눕힌다 — 빈 문자열은
    # "이름이 ''인 역"으로 읽혀 집계에서 별 역처럼 세어진다.
    assert build_row(_raw(nearest_station_name=""), AS_OF).nearest_station_name is None


def test_build_row_maps_fields_without_shuffling():
    row = build_row(_raw(), AS_OF)
    assert row.pnu == "1154510100100010000"
    assert row.sgg_code == "11545"
    assert row.nearest_station_name == "독산"
    assert row.nearest_station_m == 412.5
    # 출입구가 역 중심보다 가깝다는 관계가 그대로 보존돼야 한다.
    assert row.nearest_entrance_m < row.nearest_station_m
    assert row.as_of == AS_OF


def test_as_tuple_follows_insert_column_order():
    row = build_row(_raw(), AS_OF)
    values = row.as_tuple()
    assert len(values) == len(INSERT_COLUMNS)
    assert values[INSERT_COLUMNS.index("pnu")] == row.pnu
    assert values[INSERT_COLUMNS.index("nearest_arterial_m")] == 120.0
    assert values[INSERT_COLUMNS.index("as_of")] == AS_OF


# --- 2. 간선도로 등급 --------------------------------------------------------


def test_arterial_grades_are_exactly_the_four_trunk_classes():
    assert ARTERIAL_HIGHWAY_GRADES == ("motorway", "trunk", "primary", "secondary")


@pytest.mark.parametrize(
    "grade",
    [
        # 램프는 본선이 아니다 — 폭·통행량이 달라 "간선에 가깝다"의 근거로 못 쓴다.
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
        # tertiary부터는 집산도로다. 서울에 5,734개나 있어 넣으면 변별력이 사라진다.
        "tertiary",
        "residential",
        "service",
        "unclassified",
        "footway",
    ],
)
def test_non_arterial_grades_excluded(grade):
    assert grade not in ARTERIAL_HIGHWAY_GRADES


def test_temp_sql_filters_arterial_by_parameter_not_literal():
    # 등급 목록을 SQL에 문자열로 박으면 상수와 갈라진다. 파라미터로 넘겨야
    # ARTERIAL_HIGHWAY_GRADES 한 곳만 고치면 된다.
    assert "%(arterial)s" in _TEMP_SQL
    assert "'primary'" not in _TEMP_SQL


def test_temp_sql_transforms_osm_geometry_to_5179():
    # ⚠️ planet_osm_*.way는 4326이다. 변환을 빼면 오류 없이 거리가 엉뚱해진다.
    assert _TEMP_SQL.count("ST_Transform(way, 5179)") >= 3
    # roads_clean은 이미 5179 컬럼이 있으므로 변환하지 않는다.
    assert "ST_Transform(geom_5179" not in _TEMP_SQL


def test_temp_sql_dedupes_polygon_stations_against_node_stations():
    # 폴리곤 역 8개를 무조건 합치면 노드로도 있는 역이 두 번 세어져
    # stations_within_500m가 부풀려진다.
    assert "NOT EXISTS" in _TEMP_SQL
    assert "ST_Centroid" in _TEMP_SQL


# --- 3. SQL 컬럼·키 계약 -----------------------------------------------------


def test_parcel_sql_selects_every_feature_column():
    for col in FEATURE_COLUMNS:
        assert col in _PARCEL_SQL, f"{col}을 조회하지 않는다"


def test_upsert_sql_writes_every_feature_column():
    for col in FEATURE_COLUMNS:
        assert f"{col} = EXCLUDED.{col}" in _UPSERT_SQL, f"{col}을 갱신하지 않는다"


def test_upsert_is_keyed_on_pnu_only_because_table_is_time_invariant():
    # OSM 스냅샷이 하나뿐이라 연도 축이 없다. (pnu, base_year)로 만들면
    # 같은 값이 연도마다 복제되어 패널에서 leakage처럼 보인다.
    assert "ON CONFLICT (pnu) DO UPDATE" in _UPSERT_SQL
    assert "base_year" not in _UPSERT_SQL
    assert "base_year" not in _PARCEL_SQL


def test_upsert_records_snapshot_date_so_time_invariance_is_auditable():
    assert "as_of" in INSERT_COLUMNS
    assert "as_of = EXCLUDED.as_of" in _UPSERT_SQL


def test_parcel_sql_uses_knn_and_dwithin_for_index_use():
    # `<->` + LIMIT 1이 GIST index-scan을 타는 형태, ST_DWithin이 반경 개수용.
    assert "<->" in _PARCEL_SQL
    assert "ST_DWithin" in _PARCEL_SQL
    assert _PARCEL_SQL.count("LIMIT 1") == 5  # 최근접 5종


def test_parcel_sql_counts_use_declared_radii():
    for r in STATION_RADII_M + BUS_RADII_M:
        assert f", {r})" in _PARCEL_SQL


def test_nearest_road_uses_drivable_filter():
    # 맹지 판정 보조라 보행로(footway)가 섞이면 안 된다.
    assert "r.drivable" in _PARCEL_SQL


def test_distance_columns_exclude_counts():
    # 결측률 보고에서 개수 컬럼이 섞이면 "결측 0%"가 거리 품질을 가린다.
    assert set(DISTANCE_COLUMNS) == {
        "nearest_station_m",
        "nearest_entrance_m",
        "nearest_bus_stop_m",
        "nearest_arterial_m",
        "nearest_road_m",
    }
    assert all(not c.startswith(("stations_", "bus_stops_")) for c in DISTANCE_COLUMNS)


def test_default_targets_are_the_two_requested_districts():
    # 금천구(외곽·역 드묾) + 강남구(도심·조밀) — 양극단 검산 표본.
    assert DEFAULT_SGG_CODES == ("11545", "11680")


# --- 4. 요약 통계 ------------------------------------------------------------


def _row(**over) -> AccessibilityRow:
    return build_row(_raw(**over), AS_OF)


def test_summarize_distances_reports_missing_separately_from_zero():
    rows = [
        _row(nearest_station_m=100.0),
        _row(nearest_station_m=300.0),
        _row(nearest_station_m=None),
        _row(nearest_station_m=0.0),
    ]
    s = summarize_distances(rows)["nearest_station_m"]
    assert s["n"] == 4
    assert s["missing"] == 1
    assert s["missing_pct"] == 25.0
    # 0m는 값이므로 중위 계산에 들어간다: [0, 100, 300] → 100
    assert s["median"] == 100.0
    assert s["max"] == 300.0


def test_summarize_distances_all_missing_gives_none_not_zero():
    s = summarize_distances([_row(nearest_arterial_m=None)])["nearest_arterial_m"]
    assert s["missing"] == 1
    assert s["median"] is None
    assert s["max"] is None


def test_summarize_distances_empty_input_is_safe():
    s = summarize_distances([])
    assert set(s) == set(DISTANCE_COLUMNS)
    assert s["nearest_bus_stop_m"]["n"] == 0
    assert s["nearest_bus_stop_m"]["missing_pct"] == 0.0
