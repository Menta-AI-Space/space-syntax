"""토지특성 파생 테스트 — 원문 표기를 실제로 쪼개는지 본다.

여기서 가장 중요한 것은 **결측과 맹지를 섞지 않는 것**이다. 섞이면 도로에 닿는
땅이 화면에 맹지로 표시되고, 모델에서는 "정보 없음"이 "가장 나쁨"으로 학습된다.
"""

import pytest

from app.services.parcel_land_features import (
    UNFAVORABLE_SHAPES,
    height_display,
    is_unfavorable_height,
    is_unfavorable_shape,
    parse_road_frontage,
    shape_display,
)


@pytest.mark.parametrize(
    "raw,grade,corner",
    [
        ("광대한면", 4, False),
        ("광대소각", 4, True),
        ("광대세각", 4, True),
        ("중로한면", 3, False),
        ("중로각지", 3, True),
        ("소로한면", 2, False),
        ("소로각지", 2, True),
        ("세로한면(가)", 1, False),
        ("세로각지(불)", 1, True),
    ],
)
def test_width_grade_and_corner_are_split_out(raw, grade, corner):
    f = parse_road_frontage(raw)
    assert f.width_grade == grade
    assert f.corner is corner
    assert f.landlocked is False


def test_width_grade_is_ordinal():
    """등급이 순서를 가져야 모델이 순서를 쓸 수 있다."""
    grades = [parse_road_frontage(r).width_grade for r in ("세로한면(가)", "소로한면", "중로한면", "광대한면")]
    assert grades == sorted(grades)


def test_landlocked_has_no_width_grade():
    """맹지에 폭 등급 0을 주면 '가장 좁은 도로'와 섞인다."""
    f = parse_road_frontage("맹지")
    assert f.landlocked is True
    assert f.width_grade is None
    assert f.car_accessible is False


@pytest.mark.parametrize("raw", [None, "", "지정되지않음", "지정되지 않음", "미지정"])
def test_missing_is_missing_not_landlocked(raw):
    """실측 표본의 63%가 여기 해당한다 — 맹지로 읽으면 안 된다."""
    f = parse_road_frontage(raw)
    assert f.landlocked is None
    assert f.width_grade is None
    assert f.known is False
    assert f.display() == "확인되지 않음"


def test_car_access_is_read_from_the_suffix():
    assert parse_road_frontage("세로한면(가)").car_accessible is True
    assert parse_road_frontage("세로한면(불)").car_accessible is False
    # 광대·중로 표기에는 (가)/(불)이 없다 — 지어내지 않는다.
    assert parse_road_frontage("광대한면").car_accessible is None


def test_display_is_readable_and_has_no_raw_code():
    assert "광대" in parse_road_frontage("광대소각").display()
    assert "각지" in parse_road_frontage("광대소각").display()
    assert "차량 진입 어려움" in parse_road_frontage("세로한면(불)").display()
    assert parse_road_frontage("맹지").display() == "맹지 (도로에 닿지 않음)"


def test_unknown_frontage_wording_still_says_it_touches_a_road():
    """폭은 모르지만 도로에 닿는다는 표기 — 맹지로 넘기지 않는다."""
    f = parse_road_frontage("도로접함")
    assert f.landlocked is False
    assert f.width_grade is None


def test_shape_and_height_labels_avoid_raw_terms():
    assert shape_display("부정형") == "부정형 (모양이 고르지 않음)"
    assert shape_display("지정되지않음") is None
    assert height_display("급경사") == "급경사"
    assert height_display(None) is None
    # 모르는 표기는 지어내지 않고 원문을 보여준다.
    assert shape_display("새로운표기") == "새로운표기"


def test_unfavorable_flags_match_the_documented_set():
    for shape in UNFAVORABLE_SHAPES:
        assert is_unfavorable_shape(shape)
    assert not is_unfavorable_shape("정방형")
    assert is_unfavorable_height("급경사")
    assert not is_unfavorable_height("평지")
