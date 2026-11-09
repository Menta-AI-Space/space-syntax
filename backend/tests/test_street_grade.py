"""지도 분석 길 등급 — 백분위·등급 경계만 본다 (DB 접근은 제외).

화면이 「상위 10%」라고 말하는 선이 여기서 정해지므로, 경계가 한 칸 밀리면
지도에 칠해지는 길이 통째로 바뀐다.
"""

from app.services.street_grade import (
    _lines,
    bbox_span_m,
    grade_of,
    percentile_of,
    top_pct_of,
)

QUANTILES = [float(i) for i in range(1, 100)]  # 1~99 → 백분위 경계가 값과 같다


def test_percentile_counts_cuts_at_or_below_value():
    assert percentile_of(0.5, QUANTILES) == 0
    assert percentile_of(90.0, QUANTILES) == 90
    assert percentile_of(1000.0, QUANTILES) == 99


def test_grades_split_top_ten_and_median():
    assert grade_of(99) == 1
    assert grade_of(90) == 1
    assert grade_of(89) == 2
    assert grade_of(50) == 2
    assert grade_of(49) == 3
    assert grade_of(0) == 3


def test_top_pct_never_says_zero():
    assert top_pct_of(99) == 1
    assert top_pct_of(90) == 10
    assert top_pct_of(0) == 100


def test_lines_flip_to_lat_lng_and_split_multilines():
    line = {"type": "LineString", "coordinates": [[127.0, 37.5], [127.001, 37.501]]}
    assert _lines(line) == [[[37.5, 127.0], [37.501, 127.001]]]
    multi = {"type": "MultiLineString", "coordinates": [[[127.0, 37.5], [127.1, 37.6]], [[127.2, 37.7]]]}
    assert len(_lines(multi)) == 1  # 점 하나짜리 조각은 버린다
    assert _lines({"type": "Point", "coordinates": [127.0, 37.5]}) == []


def test_span_uses_longer_side():
    # 강남역 한 화면 ≈ 1.5km, 서울 대부분 ≈ 19km
    assert 1000 < bbox_span_m(127.020, 37.493, 127.037, 37.503) < 2000
    assert bbox_span_m(126.90, 37.45, 127.10, 37.62) > 15000
