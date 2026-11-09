"""주변 유사필지 가격 feature의 순수 계산부. DB 없이 돈다.

이 테스트가 지키는 것은 성능이 아니라 **누출**이다. 이 feature는 종속변수
(개별공시지가)를 이웃에서 다시 끌어오는 구조라서, 실수 두 개가 조용히
모델 성능만 올리고 눈에는 안 보인다:

1. 대상 필지 자신의 가격이 이웃 통계에 섞이는 것
2. 다른 연도의 이웃 가격이 붙는 것 (미래 정보)

둘 다 값이 그럴듯하게 나오므로 눈으로는 못 잡는다. 그래서 여기서 잡는다.
"""

from collections import Counter

import numpy as np
import pytest

from app.services.land_use_zones import RESIDENTIAL_2_GENERAL
from tools.compute_parcel_neighbor_prices import (
    EXCLUDED_DONOR_LAND_CATEGORIES,
    ParcelPoint,
    build_points,
    compute_neighbor_features,
    safe_ratio,
    summarize_prices,
)


def _pt(pnu, x, y, price, **kw):
    kw.setdefault("base_year", 2026)
    kw.setdefault("zone_group", RESIDENTIAL_2_GENERAL)
    kw.setdefault("is_donor", True)
    kw.setdefault("is_target", False)
    kw.setdefault("edge_dist_m", 5_000.0)   # 경계에서 멀다 = 절단 아님
    return ParcelPoint(pnu=pnu, x=x, y=y, price_per_sqm=price, **kw)


def _one(features, pnu):
    (f,) = [f for f in features if f.pnu == pnu]
    return f


# ── 1. 자기 제외 (이 파일에서 가장 중요한 테스트) ──────────────────────────
def test_target_own_price_is_excluded_from_its_neighbors():
    """자기 가격이 새면 feature가 종속변수를 그대로 담게 된다.

    대상의 가격만 터무니없이 크게 두고, 이웃 통계가 그 값에 전혀 물들지
    않는지 본다 — 평균은 특히 한 건만 섞여도 즉시 티가 난다.
    """
    points = [
        _pt("me", 0, 0, 1_000_000_000, is_target=True),
        _pt("n1", 10, 0, 100),
        _pt("n2", 20, 0, 200),
        _pt("n3", 30, 0, 300),
    ]
    f = _one(compute_neighbor_features(points), "me")

    assert f.by_radius[300].count == 3            # 자기 자신은 세지도 않는다
    assert f.by_radius[300].median == 200
    assert f.by_radius[300].mean == 200
    assert f.by_radius[500].count == 3
    assert f.knn_count == 3
    assert f.knn_median == 200
    assert f.same_zone_count == 3
    assert f.same_zone_median == 200
    # 자기 가격은 비율의 분자로만 쓰인다 (1e9 / 200)
    assert f.price_ratio == pytest.approx(5_000_000.0)


def test_self_exclusion_is_by_pnu_not_by_zero_distance():
    """대표점이 겹친 옆 필지는 살아야 한다.

    거리 0으로 자기를 지우면 같은 좌표에 찍힌 다른 필지까지 함께 날아간다.
    필지 대표점은 중심점 계산 결과라 겹치는 경우가 실제로 있다.
    """
    points = [
        _pt("me", 0, 0, 999, is_target=True),
        _pt("twin", 0, 0, 100),        # 좌표가 같은 다른 필지
        _pt("n1", 10, 0, 300),
    ]
    f = _one(compute_neighbor_features(points), "me")
    assert f.by_radius[300].count == 2
    assert f.by_radius[300].median == 200        # (100 + 300) / 2
    assert f.knn_count == 2


def test_every_target_excludes_only_itself():
    """대상이 여럿일 때 서로는 이웃으로 남아야 한다."""
    points = [
        _pt("a", 0, 0, 100, is_target=True),
        _pt("b", 10, 0, 200, is_target=True),
        _pt("c", 20, 0, 300, is_target=True),
    ]
    features = compute_neighbor_features(points)
    assert _one(features, "a").by_radius[300].mean == 250     # b, c
    assert _one(features, "b").by_radius[300].mean == 200     # a, c
    assert _one(features, "c").by_radius[300].mean == 150     # a, b


def test_lone_parcel_has_no_neighbors_at_all():
    """이웃이 자기 하나뿐이면 count 0 / 중위 NULL이다 (자기로 채우지 않는다)."""
    f = _one(compute_neighbor_features([_pt("me", 0, 0, 500, is_target=True)]), "me")
    assert f.by_radius[300].count == 0
    assert f.by_radius[300].median is None
    assert f.knn_count == 0
    assert f.knn_median is None
    assert f.price_ratio is None


# ── 2. 연도 교차 금지 ──────────────────────────────────────────────────────
def test_mixed_years_are_rejected_loudly():
    """2026년 이웃 가격을 2018년 관측치에 붙이면 미래 정보가 샌다."""
    points = [
        _pt("me", 0, 0, 100, base_year=2018, is_target=True),
        _pt("future", 10, 0, 999, base_year=2026),
    ]
    with pytest.raises(ValueError, match="연도"):
        compute_neighbor_features(points)


def test_single_year_input_passes_through_its_year():
    points = [
        _pt("me", 0, 0, 100, base_year=2018, is_target=True),
        _pt("n1", 10, 0, 200, base_year=2018),
    ]
    (f,) = compute_neighbor_features(points)
    assert f.base_year == 2018


# ── 3. 이웃 0개는 결측이다 (0이 아니다) ────────────────────────────────────
def test_far_away_neighbors_give_null_median_not_zero():
    """반경 밖 이웃만 있으면 300m는 결측이고 500m는 값이 있다."""
    points = [
        _pt("me", 0, 0, 100, is_target=True),
        _pt("n1", 400, 0, 700),
    ]
    f = _one(compute_neighbor_features(points), "me")
    assert f.by_radius[300].count == 0
    assert f.by_radius[300].median is None
    assert f.by_radius[300].mean is None
    assert f.by_radius[300].p25 is None and f.by_radius[300].p75 is None
    assert f.by_radius[500].count == 1 and f.by_radius[500].median == 700
    # 300m 중위가 없으면 비율도 없다 — 0으로 채워 inf를 만들지 않는다
    assert f.price_ratio is None


def test_summarize_prices_on_empty_is_all_null():
    s = summarize_prices([])
    assert s.count == 0
    assert (s.median, s.mean, s.p25, s.p75) == (None, None, None, None)
    assert summarize_prices(np.empty(0)).count == 0


def test_summarize_prices_percentiles():
    s = summarize_prices([100, 200, 300, 400])
    assert s.count == 4
    assert s.median == 250
    assert s.mean == 250
    assert s.p25 == 175 and s.p75 == 325     # numpy 선형 보간


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 20, 101, 1000])
def test_summarize_prices_matches_numpy_percentile(n):
    """백분위는 numpy와 같은 값이어야 한다.

    ``np.percentile``을 속도 때문에 손으로 구현한 인덱싱으로 바꿨으므로
    (호출당 240µs × 필지 47.7만 × 4회 = 8분), 값이 갈라지지 않는지 못박아 둔다.
    """
    rng = np.random.default_rng(n)
    arr = rng.random(n) * 1e7
    s = summarize_prices(arr)
    expected = np.percentile(arr, [25, 50, 75])
    assert s.count == n
    assert s.p25 == pytest.approx(expected[0])
    assert s.median == pytest.approx(expected[1])
    assert s.p75 == pytest.approx(expected[2])
    assert s.mean == pytest.approx(arr.mean())


def test_radius_boundary_is_inclusive():
    """정확히 300.0m는 포함이다 (ST_DWithin과 같은 경계 규칙)."""
    points = [
        _pt("me", 0, 0, 100, is_target=True),
        _pt("edge", 300, 0, 700),
        _pt("out", 300.001, 0, 900),
    ]
    f = _one(compute_neighbor_features(points), "me")
    assert f.by_radius[300].count == 1
    assert f.by_radius[300].median == 700


# ── 4. 같은 용도지역 제한은 정규화된 이름으로 ──────────────────────────────
def test_same_zone_uses_normalized_names_not_raw_strings():
    """표기가 다른 같은 용도지역이 갈라지면 통제가 깨진다.

    build_points가 normalize_zone_name을 통과시키므로, 공백이 들어간 원본
    명칭도 같은 계층으로 묶여야 한다.
    """
    rows = [
        _row("me", 0, 0, 100, zone_name="제2종일반주거지역", is_target=True),
        _row("n1", 10, 0, 200, zone_name="제2종 일반주거 지역"),
        _row("n2", 20, 0, 300, zone_name="제2종일반주거(지역)"),
    ]
    points, _stats = build_points(rows)
    assert {p.zone_group for p in points} == {RESIDENTIAL_2_GENERAL}
    f = _one(compute_neighbor_features(points), "me")
    assert f.same_zone_count == 2
    assert f.same_zone_median == 250


def test_other_zone_neighbors_are_excluded_from_same_zone_stats():
    points = [
        _pt("me", 0, 0, 100, is_target=True),
        _pt("same", 10, 0, 200),
        _pt("other", 20, 0, 9_000, zone_group="commercial_general"),
    ]
    f = _one(compute_neighbor_features(points), "me")
    assert f.by_radius[300].count == 2          # 반경 통계에는 둘 다 든다
    assert f.same_zone_count == 1               # 같은 용도지역은 하나
    assert f.same_zone_median == 200


def test_unknown_zone_target_gets_null_same_zone_not_a_fake_group():
    """용도지역을 모르는 필지를 '모름끼리 같은 계층'으로 묶지 않는다."""
    points = [
        _pt("me", 0, 0, 100, zone_group=None, is_target=True),
        _pt("n1", 10, 0, 200, zone_group=None),
    ]
    f = _one(compute_neighbor_features(points), "me")
    assert f.by_radius[300].count == 1          # 반경 통계에는 남는다
    assert f.same_zone_count == 0
    assert f.same_zone_median is None


# ── 5. 도너 필터 / 비율 / 경계 플래그 ──────────────────────────────────────
def _row(pnu, x, y, price, **kw):
    base = {
        "pnu": pnu,
        "base_year": 2026,
        "x": x,
        "y": y,
        "price_per_sqm": price,
        "zone_name": "제2종일반주거지역",
        "land_category": "대",
        "is_target": False,
        "edge_dist_m": 5_000.0,
    }
    base.update(kw)
    return base


def test_road_parcels_are_not_donors_but_can_still_be_targets():
    rows = [
        _row("road", 0, 0, 50, land_category="도로", is_target=True),
        _row("lot", 10, 0, 400),
    ]
    points, stats = build_points(rows)
    road = next(p for p in points if p.pnu == "road")
    assert road.is_donor is False and road.is_target is True
    assert stats["도너 제외(도로·하천 등 지목)"] == 1

    f = _one(compute_neighbor_features(points), "road")
    assert f.by_radius[300].count == 1          # 대지 이웃 하나만
    assert f.by_radius[300].median == 400


def test_all_excluded_categories_are_dropped_from_donors():
    rows = [_row(c, i * 10, 0, 100, land_category=c)
            for i, c in enumerate(sorted(EXCLUDED_DONOR_LAND_CATEGORIES))]
    points, _stats = build_points(rows)
    assert all(p.is_donor is False for p in points)


def test_null_land_category_is_still_a_donor():
    """토지특성이 안 붙은 필지를 '도로일지도 모른다'고 버리지 않는다."""
    points, _stats = build_points([_row("x", 0, 0, 100, land_category=None)])
    assert points[0].is_donor is True


def test_no_donors_at_all_yields_missing_not_zero():
    rows = [_row("road", 0, 0, 50, land_category="도로", is_target=True)]
    points, _stats = build_points(rows)
    (f,) = compute_neighbor_features(points)
    assert f.by_radius[300].count == 0 and f.by_radius[300].median is None
    assert f.knn_count == 0 and f.knn_median is None
    assert f.price_ratio is None


def test_safe_ratio_is_null_safe():
    assert safe_ratio(100, 50) == 2.0
    assert safe_ratio(None, 50) is None
    assert safe_ratio(100, None) is None
    assert safe_ratio(100, 0) is None          # 0으로 나누지 않는다
    assert safe_ratio(100, -5) is None


def test_edge_flag_follows_distance_to_coverage_boundary():
    near = _pt("near", 0, 0, 100, is_target=True, edge_dist_m=299.9)
    far = _pt("far", 10, 0, 100, is_target=True, edge_dist_m=300.0)
    features = compute_neighbor_features([near, far])
    assert _one(features, "near").edge_truncated is True
    assert _one(features, "far").edge_truncated is False


def test_unknown_edge_distance_is_treated_as_truncated():
    """경계를 못 구한 것을 '안전하다'고 표시하지 않는다."""
    (f,) = compute_neighbor_features(
        [_pt("me", 0, 0, 100, is_target=True, edge_dist_m=None)]
    )
    assert f.edge_truncated is True


def test_knn_falls_back_to_fewer_than_k_donors():
    """도너가 k보다 적으면 있는 만큼만 쓰고 count로 알린다."""
    points = [_pt("me", 0, 0, 100, is_target=True)] + [
        _pt(f"n{i}", 10 + i, 0, 100 * (i + 1)) for i in range(3)
    ]
    f = _one(compute_neighbor_features(points), "me")
    assert f.knn_count == 3
    assert f.knn_median == 200


def test_knn_ignores_parcels_beyond_the_radius():
    """반경 통계는 비어도 knn은 값이 남는다 — 밀도 낮은 곳을 살리는 목적."""
    points = [
        _pt("me", 0, 0, 100, is_target=True),
        _pt("faraway", 5_000, 0, 800),
    ]
    f = _one(compute_neighbor_features(points), "me")
    assert f.by_radius[500].count == 0 and f.by_radius[500].median is None
    assert f.knn_count == 1 and f.knn_median == 800


def test_non_target_rows_produce_no_features():
    """도너 전용 필지(옆 구 필지)는 결과 행을 만들지 않는다."""
    points = [_pt("donor_only", 0, 0, 100), _pt("t", 10, 0, 200, is_target=True)]
    features = compute_neighbor_features(points)
    assert [f.pnu for f in features] == ["t"]


def test_empty_input_is_empty_output():
    assert compute_neighbor_features([]) == []
    assert build_points([]) == ([], Counter())
