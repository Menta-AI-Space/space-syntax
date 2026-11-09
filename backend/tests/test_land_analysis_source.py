"""실제 행 → 판정. DB 없이 `build_from_row`의 순수 계산만 본다.

가장 중요한 두 가지:

1. **추정 모형이 없을 때 저평가라고 말하지 않는다.** 지금 상태가 그렇고, 여기가
   새면 사용자가 근거 없는 판정을 받는다.
2. **그런데도 화면이 비지 않는다.** 금액·비교군·갭 점수·속성·요인은 실제 값으로
   채워져야 한다.
"""

from app.services.land_analysis_source import NO_MODEL_VERSION, build_from_row


def _row(**kw) -> dict:
    row = {
        "pnu": "1156011000100120003",
        "base_year": 2026,
        "adm_cd": "1156055000",
        "zone_group": "commercial_general",
        "structure_percentile": 81.2,
        "price_percentile": 18.4,
        "gap_score": 62.8,
        "stratum_level": "dong_zone",
        "stratum_key": "1156055000|commercial_general",
        "stratum_size": 412,
        "price_per_sqm": 8_300_000.0,
        "ldong_name": "여의도동",
        "jibun": "12-3",
        "land_category": "대",
        "area_sqm": 331.4,
        "road_side_name": "광대한면",
        "zone_name_1": "일반상업지역",
        "topograph_form": "정방형",
        "topograph_height": "평지",
        "use_situation": "상업용",
        "lat": 37.5202,
        "lng": 126.9317,
    }
    row.update(kw)
    return row


def _build(**kw):
    peer_count = kw.pop("peer_count", 412)
    median = kw.pop("median_price", 12_000_000.0)
    return build_from_row(_row(**kw), peer_count=peer_count, median_price=median)


# ── 근거 없는 판정을 내지 않는다 ─────────────────────────────────────────

def test_without_an_estimator_the_verdict_is_held_not_undervalued():
    sa = _build()
    assert sa.verdict.label == "undetermined"
    assert sa.verdict.undetermined_reason == "no_estimate"
    assert sa.current_valuation is None
    assert sa.audit.model_version == NO_MODEL_VERSION


def test_the_screen_is_still_filled_with_real_values():
    sa = _build()
    assert sa.official_price is not None
    assert sa.official_price.per_sqm == 8_300_000.0
    assert sa.peer_position is not None
    assert sa.peer_position.gap_score == 62.8
    assert sa.peer_group.size == 412
    assert sa.peer_group.median_official_price_per_sqm == 12_000_000.0
    assert sa.attributes
    assert sa.factors
    assert sa.narrative is not None and sa.narrative.paragraphs


def test_missing_official_price_is_told_apart_from_a_missing_estimator():
    sa = _build(price_per_sqm=None)
    assert sa.verdict.undetermined_reason == "missing_price"
    assert sa.official_price is None


# ── 속성·요인이 원문 코드가 아니라 사람 말로 나간다 ──────────────────────

def test_attributes_are_display_strings():
    sa = _build()
    by_key = {a.key: a for a in sa.attributes}
    assert by_key["zoning"].value == "일반상업지역"
    assert by_key["area"].value == "331.4㎡"
    assert "광대" in by_key["road_frontage"].value
    assert by_key["shape"].value == "정방형 (거의 정사각형)"


def test_missing_road_frontage_is_marked_not_guessed():
    sa = _build(road_side_name="지정되지않음")
    by_key = {a.key: a for a in sa.attributes}
    assert by_key["road_frontage"].value == "확인되지 않음"
    assert by_key["road_frontage"].note  # 왜 비었는지 화면에 알린다
    # 맹지로 오인해 감점 요인을 만들지 않는다.
    assert not any(f.key == "no_road_access" for f in sa.factors)


def test_landlocked_parcel_gets_the_defect_factor():
    sa = _build(road_side_name="맹지")
    landlocked = [f for f in sa.factors if f.key == "no_road_access"]
    assert landlocked and landlocked[0].explains_discount is True


def test_corner_and_wide_road_are_positive_factors():
    sa = _build(road_side_name="광대소각")
    keys = {f.key for f in sa.factors}
    assert "wide_road" in keys
    assert "corner" in keys
    assert all(f.direction == "positive" for f in sa.factors if f.key in ("wide_road", "corner"))


def test_unfavorable_shape_and_height_explain_the_discount():
    sa = _build(topograph_form="부정형", topograph_height="급경사")
    explaining = {f.key for f in sa.factors if f.explains_discount}
    assert "irregular_shape" in explaining
    assert "unfavorable_height" in explaining


def test_structure_percentile_becomes_a_plain_language_factor():
    strong = _build(structure_percentile=88.0)
    weak = _build(structure_percentile=12.0)
    assert any(f.key == "through_traffic" for f in strong.factors)
    assert any(f.key == "off_path" for f in weak.factors)
    # 내부 용어가 라벨에 새지 않는다.
    blob = " ".join(f.label for f in strong.factors + weak.factors)
    for jargon in ("percentile", "백분위", "Integration", "Choice"):
        assert jargon not in blob


# ── 비교군 ───────────────────────────────────────────────────────────────

def test_peer_group_description_carries_the_size():
    sa = _build(peer_count=1586)
    assert "1,586필지" in sa.peer_group.description
    assert "같은 동" in sa.peer_group.description


def test_thin_peer_group_is_held_for_that_reason():
    sa = _build(peer_count=5)
    assert sa.verdict.undetermined_reason == "few_peers"


def test_address_falls_back_to_the_identifier_when_names_are_missing():
    sa = _build(ldong_name=None, jibun=None)
    assert sa.subject.address == "1156011000100120003"
