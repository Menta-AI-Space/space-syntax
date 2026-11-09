"""compute_parcel_undervalued의 SQL 결과 → 입력 변환. DB 없이 도는 부분."""

from collections import Counter

from app.services.land_use_zones import RESIDENTIAL_2_GENERAL
from tools.compute_parcel_undervalued import MAX_SEGMENT_DISTANCE_M, build_inputs


def _row(**kw):
    base = {
        "pnu": "1111010100100010000",
        "price_per_sqm": 5_000_000,
        "adm_cd": "11110510",
        "zone_name": "제2종일반주거지역",
        "seg_key": "123:abc",
        "structure_pct_in_tile": 74.2,
        "dist_m": 8.4,
    }
    base.update(kw)
    return base


def test_normal_row_becomes_judgeable_input():
    inputs, seg_by_pnu, _reasons = build_inputs(
        [_row()], 2026, unknown_zones=Counter()
    )
    (p,) = inputs
    assert p.zone_code == RESIDENTIAL_2_GENERAL
    assert p.structure_score == 74.2
    assert p.price_per_sqm == 5_000_000.0
    assert p.base_year == 2026
    assert p.buildable is True
    assert seg_by_pnu[p.pnu] == "123:abc"


def test_far_segment_is_not_treated_as_frontage():
    """대표점에서 너무 먼 세그먼트의 구조를 이 필지의 접근성이라 할 수 없다."""
    inputs, seg_by_pnu, reasons = build_inputs(
        [_row(dist_m=MAX_SEGMENT_DISTANCE_M + 1)], 2026, unknown_zones=Counter()
    )
    assert inputs[0].structure_score is None
    assert inputs[0].pnu not in seg_by_pnu       # 근거 세그먼트도 남기지 않는다
    assert reasons["접면 도로 없음/너무 멂"] == 1


def test_segment_exactly_at_the_limit_is_kept():
    inputs, _seg, _reasons = build_inputs(
        [_row(dist_m=MAX_SEGMENT_DISTANCE_M)], 2026, unknown_zones=Counter()
    )
    assert inputs[0].structure_score == 74.2


def test_missing_segment_yields_none_not_zero():
    """0으로 채우면 '구조가 나쁜 필지'와 '값을 못 붙인 필지'가 섞인다."""
    inputs, _seg, reasons = build_inputs(
        [_row(seg_key=None, structure_pct_in_tile=None, dist_m=None)],
        2026, unknown_zones=Counter(),
    )
    assert inputs[0].structure_score is None
    assert reasons["접면 도로 없음/너무 멂"] == 1


def test_unknown_zone_name_is_counted_not_silently_dropped():
    unknown: Counter = Counter()
    inputs, _seg, reasons = build_inputs(
        [_row(zone_name="정체불명지역")], 2026, unknown_zones=unknown
    )
    assert inputs[0].zone_code is None
    assert inputs[0].buildable is False          # 근거 없는 필지를 지을 수 있다고 보지 않는다
    assert unknown["정체불명지역"] == 1
    assert reasons["용도지역 미확인"] == 1


def test_null_zone_name_is_not_counted_as_unknown_label():
    """용도지역 폴리곤이 아예 안 걸린 것과 '명칭을 못 알아본 것'은 다르다."""
    unknown: Counter = Counter()
    _inputs, _seg, reasons = build_inputs(
        [_row(zone_name=None)], 2026, unknown_zones=unknown
    )
    assert unknown == Counter()
    assert reasons["용도지역 미확인"] == 1


def test_greenbelt_parcel_is_marked_unbuildable():
    inputs, _seg, reasons = build_inputs(
        [_row(zone_name="개발제한구역")], 2026, unknown_zones=Counter()
    )
    assert inputs[0].buildable is False
    assert reasons["개발 제한 용도지역"] == 1


def test_zone_name_spacing_variants_normalize_to_one_group():
    inputs, _seg, _reasons = build_inputs(
        [
            _row(pnu="a", zone_name="제2종일반주거지역"),
            _row(pnu="b", zone_name="제2종 일반주거 지역"),
        ],
        2026, unknown_zones=Counter(),
    )
    assert {p.zone_code for p in inputs} == {RESIDENTIAL_2_GENERAL}


def test_empty_rows_produce_empty_inputs():
    inputs, seg, reasons = build_inputs([], 2026, unknown_zones=Counter())
    assert inputs == [] and seg == {} and reasons == Counter()
