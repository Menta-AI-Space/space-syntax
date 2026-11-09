"""``parcel_panel`` 적재 배치 — 층 구분·누출·예측 보존을 고정한다. DB 없이 돈다.

이 테스트가 지키는 것은 성능이 아니라 **네 가지 사고**다. 넷 다 값이 그럴듯하게
나오므로 눈으로는 못 잡는다:

1. Layer A / Layer B 목록이 표 컬럼과 어긋나는 것 — 증분 설명력 주장이 조용히
   무너진다(비준표 변수가 Layer B에 끼면 "우리 feature"의 기여가 부풀고, 반대면
   통제가 빠진다).
2. 누출 컬럼(gap_score·price_percentile·structure_percentile·
   price_ratio_to_neighbor_300m)이 다시 들어오는 것 — feature로 요긴해 보인다.
3. UPSERT가 모델 출력 컬럼을 덮어써서 feature 재적재가 예측을 지우는 것.
4. 이력·도너 범위가 없는데 성장률이 계산되는 것 — 특히 2026년 이웃 성장률은
   "가격 변화"가 아니라 "적재 범위 변화"가 되어 부호까지 뒤집힌다.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from tools import build_parcel_panel as bp

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "migrations" / "022_parcel_panel.sql"
)


def migration_columns() -> list[str]:
    """마이그레이션의 CREATE TABLE에서 컬럼 이름을 뽑는다.

    실제 표를 정본으로 삼기 위해서다 — 모듈의 목록만 서로 맞춰 봐도 둘이 같이
    틀리면 못 잡는다.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    body = sql.split("CREATE TABLE IF NOT EXISTS parcel_panel (", 1)[1]
    body = body.split("PRIMARY KEY", 1)[0]
    cols: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        m = re.match(r"^([a-z_][a-z0-9_]*)\s+[a-z]", line)
        if m:
            cols.append(m.group(1))
    return cols


# ── 1. 층 구분이 표와 어긋날 수 없게 고정한다 ──────────────────────────────
def test_migration_columns_are_parsed():
    """파서가 실제로 컬럼을 뽑았는지부터 본다 — 빈 목록이면 아래 테스트가 다 통과한다."""
    cols = migration_columns()
    assert len(cols) == 48, cols
    assert cols[0] == "pnu"
    assert cols[-1] == "computed_at"
    assert "expected_price" in cols


def test_column_groups_partition_the_table_exactly():
    """모든 컬럼이 정확히 한 묶음에 속한다 — 빠뜨림도 겹침도 없다."""
    groups = {
        "identity": bp.IDENTITY_COLUMNS,
        "target": (bp.TARGET_COLUMN,),
        "layer_a": bp.LAYER_A_COLUMNS,
        "layer_b": bp.LAYER_B_COLUMNS,
        "growth": bp.GROWTH_COLUMNS,
        "provenance": bp.PROVENANCE_COLUMNS,
        "model_output": bp.MODEL_OUTPUT_COLUMNS,
        "bookkeeping": bp.BOOKKEEPING_COLUMNS,
    }
    table = migration_columns()
    assert len(table) == len(set(table))

    seen: Counter = Counter()
    for cols in groups.values():
        seen.update(cols)
    duplicated = [c for c, n in seen.items() if n > 1]
    assert duplicated == [], f"두 묶음에 동시에 든 컬럼: {duplicated}"

    assert set(seen) == set(table), {
        "표에만 있다": sorted(set(table) - set(seen)),
        "목록에만 있다": sorted(set(seen) - set(table)),
    }


def test_layer_a_is_exactly_the_비준표_variables():
    """Layer A는 토지가격비준표가 쓰는 변수 그대로다.

    여기에 무언가 더하면 통제가 과해져 Layer B 증분이 사라지고, 빼면 순환성이
    Layer B의 성과로 잘못 잡힌다. 특히 ``road_side_name``(도로접면)은 순환성의
    최대 원천이라 절대 Layer B로 넘어가면 안 된다.
    """
    assert set(bp.LAYER_A_COLUMNS) == {
        "zone_name",
        "zone_group",
        "land_category",
        "use_situation",
        "area_sqm",
        "topograph_height",
        "topograph_form",
        "road_side_name",
    }


def test_layer_b_is_exactly_our_four_groups():
    assert set(bp.LAYER_B_COLUMNS) == {
        # 접근성 (parcel_accessibility — pnu만으로 조인하는 시불변 스냅샷)
        "nearest_station_m",
        "nearest_entrance_m",
        "stations_within_500m",
        "stations_within_1000m",
        "nearest_bus_stop_m",
        "bus_stops_within_300m",
        "bus_stops_within_500m",
        "nearest_arterial_m",
        "nearest_road_m",
        # 길 구조
        "structure_score",
        # 이웃 가격 (연도별)
        "neighbor_median_300m",
        "neighbor_count_300m",
        "neighbor_median_500m",
        "neighbor_count_500m",
        "neighbor_knn20_median",
        "same_zone_median_300m",
        "same_zone_count_300m",
        "edge_truncated",
        "donor_sgg_count",
        # 재개발
        "in_redev_zone",
        "redev_zone_class",
        "redev_notice_year",
    }
    assert not set(bp.LAYER_A_COLUMNS) & set(bp.LAYER_B_COLUMNS)


def test_growth_is_a_control_not_a_layer_b_claim():
    """성장률은 Layer B가 아니다 — target의 과거값이라 통제군에 들어간다.

    Layer B에 끼면 "우리 feature의 증분"이 자기회귀 성분으로 부풀어, 팔 수 없는
    숫자가 된다.
    """
    assert not set(bp.GROWTH_COLUMNS) & set(bp.LAYER_B_COLUMNS)
    assert not set(bp.GROWTH_COLUMNS) & set(bp.LAYER_A_COLUMNS)
    assert set(bp.ABLATION_CONTROL_COLUMNS) == set(bp.LAYER_A_COLUMNS) | set(
        bp.GROWTH_COLUMNS
    )


# ── 2. 누출 컬럼은 SQL에도 컬럼 목록에도 없다 ──────────────────────────────
def module_sql_strings() -> dict[str, str]:
    """모듈의 SQL 상수 전부. 이름이 ``_SQL``로 끝나는 문자열 속성을 모은다."""
    out = {
        name: value
        for name, value in vars(bp).items()
        if name.endswith("_SQL") and isinstance(value, str)
    }
    assert "FEATURES_SQL" in out and "UPSERT_SQL" in out, sorted(out)
    return out


@pytest.mark.parametrize("forbidden", bp.FORBIDDEN_SOURCE_COLUMNS)
def test_leakage_columns_appear_in_no_sql(forbidden):
    """가격 백분위·target 비율은 어떤 SQL에도 등장하지 않는다."""
    for name, sql in module_sql_strings().items():
        assert forbidden not in sql, f"{name}에 누출 컬럼 {forbidden}이 들어 있다"


@pytest.mark.parametrize("forbidden", bp.FORBIDDEN_SOURCE_COLUMNS)
def test_leakage_columns_appear_in_no_column_list(forbidden):
    lists = (
        bp.IDENTITY_COLUMNS,
        (bp.TARGET_COLUMN,),
        bp.LAYER_A_COLUMNS,
        bp.LAYER_B_COLUMNS,
        bp.GROWTH_COLUMNS,
        bp.PROVENANCE_COLUMNS,
        bp.WRITTEN_COLUMNS,
        bp.FEATURE_COLUMNS,
        bp.ABLATION_CONTROL_COLUMNS,
    )
    for cols in lists:
        assert forbidden not in cols


def test_forbidden_list_names_the_four_known_traps():
    """이름을 박아 두는 것이 목적이다 — 목록이 조용히 줄어들면 안 된다."""
    assert set(bp.FORBIDDEN_SOURCE_COLUMNS) == {
        "gap_score",
        "price_percentile",
        "structure_percentile",
        "price_ratio_to_neighbor_300m",
    }


# ── 3. UPSERT가 예측을 지우지 않는다 ──────────────────────────────────────
def update_clause() -> str:
    return bp.UPSERT_SQL.split("DO UPDATE SET", 1)[1]


@pytest.mark.parametrize("col", bp.MODEL_OUTPUT_COLUMNS)
def test_upsert_never_writes_model_output_columns(col):
    """feature 재적재가 G2의 예측을 지우면 안 된다 — INSERT에도 UPDATE에도 없다."""
    assert col not in bp.UPSERT_SQL
    assert col not in bp.WRITTEN_COLUMNS


def test_upsert_update_clause_covers_every_feature_column():
    """반대 사고도 막는다 — feature가 UPDATE에서 빠지면 재적재해도 옛 값이 남는다."""
    clause = update_clause()
    for col in bp.FEATURE_COLUMNS + bp.PROVENANCE_COLUMNS:
        assert f"{col} = EXCLUDED.{col}" in clause, col
    assert "computed_at = NOW()" in clause
    # 기본키는 갱신 대상이 아니다.
    assert "pnu = EXCLUDED.pnu" not in clause
    assert "base_year = EXCLUDED.base_year" not in clause


def test_upsert_placeholder_count_matches_written_columns():
    """자리표 개수가 어긋나면 컬럼이 한 칸씩 밀려 들어간다 — 조용히 틀린다."""
    assert bp.UPSERT_TEMPLATE.count("%s") == len(bp.WRITTEN_COLUMNS)
    assert bp.UPSERT_TEMPLATE.endswith(", NOW())")


def test_to_tuple_follows_written_column_order():
    row = {c: i for i, c in enumerate(bp.WRITTEN_COLUMNS)}
    assert bp.to_tuple(row) == tuple(range(len(bp.WRITTEN_COLUMNS)))
    # 없는 값은 None으로 — 채워 넣지 않는다.
    assert bp.to_tuple({"pnu": "x"})[1] is None


# ── 4. 성장률 — 이력 없으면 NULL, 도너 범위 다르면 NULL ────────────────────
def test_price_growth_basic():
    assert bp.price_growth(110.0, 100.0) == pytest.approx(0.1)
    assert bp.price_growth(90.0, 100.0) == pytest.approx(-0.1)


@pytest.mark.parametrize(
    "current,past",
    [(100.0, None), (None, 100.0), (None, None), (100.0, 0.0), (100.0, -5.0)],
)
def test_price_growth_is_none_without_usable_history(current, past):
    """이력이 없으면 0이 아니라 None이다 — 0은 "성장 0%"으로 학습된다."""
    assert bp.price_growth(current, past) is None


def test_neighbor_growth_requires_equal_donor_scope():
    """도너 범위가 다르면 계산하지 않는다.

    2026년 실측 상황: 이웃 도너가 2개 구 → 25개 구로 바뀌었다. 그냥 차분하면
    "가격이 내렸다"가 나오지만 실제로는 이웃 집합이 강남·금천에서 서울 전역으로
    넓어진 것뿐이다.
    """
    assert bp.neighbor_growth(120.0, 25, 100.0, 2) is None
    assert bp.neighbor_growth(120.0, 2, 100.0, 25) is None
    assert bp.neighbor_growth(120.0, 2, 100.0, 2) == pytest.approx(0.2)


@pytest.mark.parametrize(
    "cur_donor,past_donor", [(None, 2), (2, None), (None, None)]
)
def test_neighbor_growth_is_none_when_donor_scope_unknown(cur_donor, past_donor):
    """범위를 모르면 같은지도 모른다 — 같다고 가정하지 않는다."""
    assert bp.neighbor_growth(120.0, cur_donor, 100.0, past_donor) is None


def test_neighbor_growth_is_none_without_past_median():
    assert bp.neighbor_growth(120.0, 2, None, 2) is None


# ── 5. build_rows — 파생값이 실제 행에 붙는 방식 ───────────────────────────
def _raw(pnu="1154510200100010000", year=2021, price=1000.0, **kw):
    row = {
        "pnu": pnu,
        "base_year": year,
        "sgg_code": pnu[:5],
        "ldong_code": pnu[:10],
        "official_price_per_sqm": price,
        "zone_name": "제2종일반주거지역",
        "neighbor_median_300m": 900.0,
        "donor_sgg_count": 2,
    }
    row.update(kw)
    return row


def test_build_rows_computes_growth_from_same_pnu_history_only():
    """다른 필지의 과거 가격이 새면 성장률이 조용히 엉뚱해진다."""
    rows = bp.build_rows(
        [_raw(price=1200.0)],
        base_year=2021,
        lag_prices={
            ("1154510200100010000", 2020): 1000.0,   # t-1
            ("1154510200100010000", 2018): 800.0,    # t-3
            ("9999999999999999999", 2016): 1.0,      # 남의 필지 — 무시돼야 한다
        },
        lag_neighbors={},
        redev={},
    )
    (r,) = rows
    assert r["price_growth_1y"] == pytest.approx(0.2)
    assert r["price_growth_3y"] == pytest.approx(0.5)
    assert r["price_growth_5y"] is None       # 2016년 자기 이력이 없다


def test_build_rows_leaves_growth_null_when_history_absent():
    rows = bp.build_rows(
        [_raw()], base_year=2018, lag_prices={}, lag_neighbors={}, redev={}
    )
    (r,) = rows
    for col in ("price_growth_1y", "price_growth_3y", "price_growth_5y",
                "neighbor_growth_3y"):
        assert r[col] is None, col


def test_build_rows_applies_donor_scope_guard_to_neighbor_growth():
    pnu = "1154510200100010000"
    same_scope = bp.build_rows(
        [_raw(donor_sgg_count=2)],
        base_year=2021,
        lag_prices={},
        lag_neighbors={pnu: (750.0, 2)},
        redev={},
    )
    assert same_scope[0]["neighbor_growth_3y"] == pytest.approx(0.2)

    changed_scope = bp.build_rows(
        [_raw(donor_sgg_count=25)],
        base_year=2026,
        lag_prices={},
        lag_neighbors={pnu: (750.0, 2)},
        redev={},
    )
    assert changed_scope[0]["neighbor_growth_3y"] is None


def test_build_rows_sets_zone_group_from_normalize_helper():
    from app.services.land_use_zones import normalize_zone_name

    rows = bp.build_rows(
        [_raw(zone_name="제3종일반주거지역")],
        base_year=2021, lag_prices={}, lag_neighbors={}, redev={},
    )
    assert rows[0]["zone_group"] == normalize_zone_name("제3종일반주거지역")
    assert rows[0]["zone_group"] is not None


def test_build_rows_reports_unknown_zone_names_instead_of_guessing():
    unknown: Counter = Counter()
    rows = bp.build_rows(
        [_raw(zone_name="알수없는지역")],
        base_year=2021, lag_prices={}, lag_neighbors={}, redev={},
        unknown_zones=unknown,
    )
    assert rows[0]["zone_group"] is None
    assert unknown["알수없는지역"] == 1


def test_build_rows_redev_flag_defaults_to_false():
    rows = bp.build_rows(
        [_raw()], base_year=2021, lag_prices={}, lag_neighbors={}, redev={}
    )
    (r,) = rows
    assert r["in_redev_zone"] is False
    assert r["redev_zone_class"] is None
    assert r["redev_notice_year"] is None


def test_build_rows_redev_flag_when_matched():
    pnu = "1154510200100010000"
    rows = bp.build_rows(
        [_raw()], base_year=2021, lag_prices={}, lag_neighbors={},
        redev={pnu: ("UQ1221", 2015)},
    )
    (r,) = rows
    assert r["in_redev_zone"] is True
    assert r["redev_zone_class"] == "UQ1221"
    assert r["redev_notice_year"] == 2015


# ── 6. 조인 키 — 마이그레이션이 정한 대로인지 SQL에서 확인한다 ─────────────
def test_accessibility_joins_on_pnu_only():
    """base_year 조인이 **없는 것이 의도**다 — 스냅샷이 하나라 연도별 값이 없다.

    무심코 base_year를 붙이면 2018~2025 행의 접근성이 통째로 NULL이 되고,
    반대로 연도별 값이 있는 척 복사하면 "2018년 필지가 2026년의 역을 안다"가 된다.
    """
    sql = bp.FEATURES_SQL
    access = sql.split("LEFT JOIN parcel_accessibility ac", 1)[1].split("LEFT JOIN", 1)[0]
    assert "ac.pnu = ch.pnu" in access
    assert "base_year" not in access


def test_neighbor_prices_join_on_pnu_and_base_year():
    """이웃 가격은 반드시 같은 해만 본다 — 연도 교차는 미래 정보 누출이다."""
    sql = bp.FEATURES_SQL
    nb = sql.split("LEFT JOIN parcel_neighbor_prices np", 1)[1].split("LEFT JOIN", 1)[0]
    assert "np.pnu = ch.pnu" in nb
    assert "np.base_year = ch.base_year" in nb


def test_redev_sql_requires_notice_date_before_january_first():
    """공시지가 기준일이 1월 1일이라 그해 중 고시된 구역은 그해 가격에 못 들어간다.

    고시일이 없는 구역은 시점을 모르므로 아예 세지 않는다.
    """
    sql = bp.REDEV_SQL
    assert "z.notice_date IS NOT NULL" in sql
    assert "z.notice_date < make_date(%(year)s, 1, 1)" in sql
    assert "ORDER BY pc.pnu, z.notice_date DESC" in sql   # 겹치면 최근 고시


def test_structure_score_comes_alone_with_its_as_of():
    """같은 표의 백분위·gap_score는 안 가져오고, 시점만 함께 가져온다."""
    lateral = bp.FEATURES_SQL.split("LEFT JOIN LATERAL", 1)[1].split(") st ON TRUE", 1)[0]
    assert "us.structure_score" in lateral
    assert "structure_as_of" in lateral
    assert "percentile" not in lateral
    assert "gap_score" not in lateral


# ── 7. CLI 연도 파싱 ──────────────────────────────────────────────────────
def test_parse_years_defaults_to_full_panel_range():
    assert bp.parse_years(None) == list(range(2018, 2027))
    assert bp.parse_years([]) == list(range(2018, 2027))


@pytest.mark.parametrize(
    "given,expected",
    [
        (["2026"], [2026]),
        (["2018", "2026"], [2018, 2026]),
        (["2018-2020"], [2018, 2019, 2020]),
        (["2018,2020"], [2018, 2020]),
        (["2019-2020", "2026"], [2019, 2020, 2026]),
        (["2026", "2026"], [2026]),
    ],
)
def test_parse_years_accepts_repeats_and_ranges(given, expected):
    assert bp.parse_years(given) == expected


def test_parse_years_rejects_reversed_range():
    with pytest.raises(ValueError):
        bp.parse_years(["2026-2018"])


# ── 8. NULL률 보고 ────────────────────────────────────────────────────────
def test_null_rates_reports_missing_instead_of_hiding_it():
    rows = [
        {"nearest_station_m": 100.0, "structure_score": None,
         "neighbor_median_300m": 5.0, "price_growth_1y": 0.1,
         "price_growth_3y": None, "price_growth_5y": None,
         "neighbor_growth_3y": None, "zone_group": "x"},
        {"nearest_station_m": None, "structure_score": None,
         "neighbor_median_300m": None, "price_growth_1y": None,
         "price_growth_3y": None, "price_growth_5y": None,
         "neighbor_growth_3y": None, "zone_group": "x"},
    ]
    rates = bp.null_rates(rows)
    assert rates["접근성"] == pytest.approx(50.0)
    assert rates["길구조"] == pytest.approx(100.0)
    assert rates["이웃성장3y"] == pytest.approx(100.0)
    assert rates["용도지역군"] == pytest.approx(0.0)
    assert bp.null_rates([]) == {}
