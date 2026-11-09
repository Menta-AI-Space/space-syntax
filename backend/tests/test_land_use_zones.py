from app.services.land_use_zones import (
    COMMERCIAL_GENERAL,
    GREEN_NATURAL,
    RESIDENTIAL_1_GENERAL,
    RESIDENTIAL_2_GENERAL,
    RESIDENTIAL_SEMI,
    RESTRICTED_DEVELOPMENT,
    is_buildable,
    normalize_zone_name,
)


def test_normalizes_plain_names():
    assert normalize_zone_name("제2종일반주거지역") == RESIDENTIAL_2_GENERAL
    assert normalize_zone_name("일반상업지역") == COMMERCIAL_GENERAL


def test_normalizes_despite_spacing_and_brackets():
    """출처마다 표기가 다르다 — 같은 용도지역이 여러 계층으로 쪼개지면 안 된다."""
    variants = [
        "제2종일반주거지역",
        "제2종 일반주거지역",
        "제2종일반주거(지역)",
        " 제2종  일반 주거 지역 ",
        "제2종·일반주거지역",
    ]
    assert {normalize_zone_name(v) for v in variants} == {RESIDENTIAL_2_GENERAL}


def test_semi_residential_is_not_swallowed_by_residential():
    """준주거지역은 주거지역 패턴에 먹히면 안 된다 (용적률·성격이 다르다)."""
    assert normalize_zone_name("준주거지역") == RESIDENTIAL_SEMI


def test_exclusive_vs_general_residential_are_distinct():
    assert normalize_zone_name("제1종전용주거지역") != normalize_zone_name("제1종일반주거지역")
    assert normalize_zone_name("제1종일반주거지역") == RESIDENTIAL_1_GENERAL


def test_unknown_name_returns_none_rather_than_a_guess():
    """모르는 용도지역을 아무 계층에 넣으면 통제가 깨진다 — 빼는 게 맞다."""
    assert normalize_zone_name("우주정거장지역") is None
    assert normalize_zone_name("") is None
    assert normalize_zone_name(None) is None


def test_restricted_development_zone_is_recognized():
    assert normalize_zone_name("개발제한구역") == RESTRICTED_DEVELOPMENT


# ── 개발 가능성 ────────────────────────────────────────────────────────

def test_greenbelt_is_not_buildable():
    assert not is_buildable(RESTRICTED_DEVELOPMENT)


def test_green_zones_are_excluded_conservatively():
    assert not is_buildable(GREEN_NATURAL)


def test_ordinary_zones_are_buildable():
    assert is_buildable(RESIDENTIAL_2_GENERAL)
    assert is_buildable(COMMERCIAL_GENERAL)
    assert is_buildable(RESIDENTIAL_1_GENERAL)


def test_unknown_zone_is_not_assumed_buildable():
    """판정 근거가 없는 필지를 '지을 수 있다'고 가정하면 안 된다."""
    assert not is_buildable(None)
