"""필지 구조 등급 — 순위·배분 계산만 본다 (DB 접근은 제외).

이 층이 내는 숫자는 분양가 배분에 쓰이므로, 합이 1이 되는지·맹지를 0점으로
깔아뭉개지 않는지처럼 돈이 걸린 성질을 고정해 둔다.
"""

import pytest

from app.services.parcel_structure import (
    ParcelStructure,
    _grade,
    _percentiles,
    grade_parcels,
    summarize,
)


def _parcel(pnu: str, structure: float | None, area: float = 100.0) -> ParcelStructure:
    return ParcelStructure(
        pnu=pnu, area_sqm=area, structure=structure, integration=None, choice=None,
        road_distance_m=5.0 if structure is not None else None,
        frontage_count=2 if structure is not None else 0,
    )


def test_percentile_puts_ties_at_the_same_rank():
    table = _percentiles([10.0, 10.0, 20.0, 30.0])
    assert table[10.0] == table[10.0]
    assert table[30.0] == 100.0
    assert table[10.0] < table[20.0] < table[30.0]


def test_grades_run_from_one_at_the_top():
    assert _grade(100.0) == 1
    assert _grade(85.0) == 1
    assert _grade(70.0) == 2
    assert _grade(50.0) == 3
    assert _grade(30.0) == 4
    assert _grade(0.0) == 5


def test_allocation_shares_sum_to_one():
    parcels = grade_parcels([_parcel(str(i), float(i * 10)) for i in range(1, 6)])
    shares = [p.allocation_share for p in parcels]
    assert all(s is not None for s in shares)
    assert sum(shares) == pytest.approx(1.0)


def test_better_structure_gets_more_than_equal_area_neighbour():
    good, poor = _parcel("good", 90.0), _parcel("poor", 10.0)
    grade_parcels([good, poor])
    assert good.allocation_share > poor.allocation_share
    assert good.grade < poor.grade  # 1등급이 더 좋다


def test_equal_structure_falls_back_to_area_proportion():
    """구조가 같으면 기존 관행(면적 비례)과 같아야 한다 — 그래야 이 지표가
    관행에서 얼마나 움직였는지를 사람이 읽을 수 있다."""
    big, small = _parcel("big", 50.0, area=300.0), _parcel("small", 50.0, area=100.0)
    grade_parcels([big, small])
    assert big.allocation_share == pytest.approx(0.75)
    assert small.allocation_share == pytest.approx(0.25)


def test_landlocked_parcels_are_left_ungraded_not_zeroed():
    """접한 도로를 못 찾은 필지에 0등급을 주면 '최악의 자리'로 읽힌다.
    실제로는 '모른다'이므로 등급·배분을 비워 둔다."""
    graded = _parcel("road", 80.0)
    landlocked = _parcel("none", None)
    grade_parcels([graded, landlocked])
    assert landlocked.grade is None and landlocked.allocation_share is None
    assert graded.allocation_share == pytest.approx(1.0)


def test_summary_counts_landlocked_and_spread():
    parcels = [_parcel(str(i), float(i)) for i in range(1, 21)] + [_parcel("x", None)]
    grade_parcels(parcels)
    stats = summarize(parcels)
    assert stats["parcel_count"] == 21
    assert stats["graded_count"] == 20
    assert stats["landlocked_count"] == 1
    assert stats["structure_spread"] > 1.0


def test_no_road_data_at_all_returns_parcels_untouched():
    parcels = [_parcel("a", None), _parcel("b", None)]
    out = grade_parcels(parcels)
    assert all(p.grade is None for p in out)
    assert summarize(out)["graded_count"] == 0
