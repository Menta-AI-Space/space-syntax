"""정적 서술 테스트 — 문장 전체를 고정하지 않고 **규칙**만 본다.

문구를 통째로 assert하면 표현을 다듬을 때마다 테스트가 깨져서, 결국 문장을
고치지 않게 된다. 그래서 여기서 보는 것은 넷이다: 중복을 말하지 않는가, 긍정과
부정을 관계로 잇는가, 내부 용어가 안 나오는가, 신뢰도가 낮을 때 어조가 약해지는가.
"""

import re

from app.models.land_analysis import PeerGroup, Subject
from app.services.land_narrative import build_narrative
from app.services.land_verdict import (
    FactorInput,
    VerdictInput,
    build_structured_analysis,
)


def _analysis(**kw):
    factors = kw.pop("factors", [])
    inp_kw = dict(
        peer_count=500,
        peer_median_price_per_sqm=10_000_000,
        actual_price_per_sqm=10_000_000,
        expected_price_point=10_000_000,
        expected_price_low=9_000_000,
        expected_price_high=11_000_000,
        factors=factors,
    )
    inp_kw.update(kw)
    return build_structured_analysis(
        subject=Subject(pnu="1" * 19, base_year=2026, address="영등포구 여의도동 12-3"),
        peer_group=PeerGroup(
            description="같은 조건 500필지", size=500, level="dong_zone"
        ),
        inp=VerdictInput(**inp_kw),  # type: ignore[arg-type]
        model_version="test",
    )


def _text(analysis) -> str:
    return " ".join(analysis.narrative.paragraphs)


def test_every_analysis_ships_with_a_static_narrative():
    """LLM 없이도 화면이 완성된다 — 서술이 비어 있으면 안 된다."""
    analysis = _analysis()
    assert analysis.narrative is not None
    assert analysis.narrative.source == "static"
    assert analysis.narrative.paragraphs


def test_the_same_factor_is_not_listed_twice():
    analysis = _analysis(
        expected_price_point=9_800_000,
        actual_price_per_sqm=7_000_000,
        factors=[
            FactorInput("road", "큰길에 바로 닿음", "positive", "큰 가점"),
            FactorInput("road", "큰길에 바로 닿음", "positive", "가점"),
        ],
    )
    assert _text(analysis).count("큰길에 바로 닿음") == 1


def test_positive_and_negative_are_related_not_listed():
    """나열이 아니라 관계로 잇는다 — 사용자가 알고 싶은 건 목록이 아니다."""
    analysis = _analysis(
        expected_price_point=9_800_000,
        actual_price_per_sqm=7_000_000,   # 미설명분이 커서 저평가
        factors=[
            FactorInput("road", "큰길에 바로 닿음", "positive", "큰 가점"),
            FactorInput("depth", "깊이가 얕음", "negative", "약한 감점", True),
        ],
    )
    text = _text(analysis)
    assert "큰길에 바로 닿음" in text and "깊이가 얕음" in text
    # 두 요인을 이어 주는 접속이 있어야 한다.
    assert "데도" in text or "지만" in text


def test_explained_discount_says_the_defect_explains_the_price():
    analysis = _analysis(
        expected_price_point=6_500_000,
        actual_price_per_sqm=6_400_000,   # 단점이 값을 설명한다
        price_percentile=8.0,
        factors=[
            FactorInput("road", "큰길에 바로 닿음", "positive", "가점"),
            FactorInput("no_road", "맹지 (도로에 닿지 않음)", "negative", "큰 감점", True),
        ],
    )
    assert "설명" in _text(analysis)


def test_no_internal_jargon_reaches_the_reader():
    analysis = _analysis(
        expected_price_point=9_800_000,
        actual_price_per_sqm=7_000_000,
        factors=[FactorInput("road", "큰길에 바로 닿음", "positive", "큰 가점")],
    )
    text = _text(analysis) + analysis.narrative.headline
    for jargon in ("SHAP", "Integration", "잔차", "백분위", "헤도닉", "feature"):
        assert jargon not in text, f"내부 용어가 서술에 나왔다: {jargon}"


def test_weak_confidence_softens_the_wording():
    weak = _analysis(
        peer_count=40,
        expected_price_point=9_800_000,
        expected_price_low=5_000_000,
        expected_price_high=15_000_000,
        actual_price_per_sqm=6_000_000,
        peer_similarity=0.2,
        in_training_distribution=False,
    )
    strong = _analysis(
        peer_count=2000,
        expected_price_point=9_900_000,
        actual_price_per_sqm=6_500_000,
        peer_similarity=0.9,
        in_training_distribution=True,
    )
    assert "일 수 있습니다" in _text(weak)
    assert "일 수 있습니다" not in _text(strong)
    # 무엇이 신뢰도를 깎았는지도 문장으로 나온다.
    assert "참고로만" in _text(weak)


def test_high_confidence_has_no_hedging_tail():
    strong = _analysis(
        peer_count=2000,
        expected_price_point=9_900_000,
        # 추정 범위를 좁게 준다 — 넓으면 그것만으로 신뢰도가 깎여
        # "참고로만 보세요"가 붙는다(그 동작은 위 테스트가 본다).
        expected_price_low=9_600_000,
        expected_price_high=10_200_000,
        actual_price_per_sqm=6_500_000,
        peer_similarity=0.9,
        in_training_distribution=True,
    )
    assert strong.verdict.confidence.reasons == []
    assert "참고로만" not in _text(strong)


def test_undetermined_says_why_it_was_held():
    held = _analysis(
        peer_count=7,
        expected_price_point=None,
        expected_price_low=None,
        expected_price_high=None,
        actual_price_per_sqm=5_000_000,
    )
    assert held.verdict.undetermined_reason == "few_peers"
    assert "비교할 필지가 모자라" in _text(held)


def test_missing_estimate_is_told_apart_from_thin_peer_group():
    """'우리 사정'과 '이 필지의 사정'이 다른 문구로 나가야 한다."""
    no_model = _analysis(
        peer_count=800,
        expected_price_point=None,
        expected_price_low=None,
        expected_price_high=None,
        actual_price_per_sqm=5_000_000,
    )
    assert no_model.verdict.undetermined_reason == "no_estimate"
    assert "추정 모형" in _text(no_model)


def test_particles_are_chosen_by_the_preceding_sound():
    """"맹지 (도로에 닿지 않음)이(가)" 같은 병기가 문장에 남지 않는다."""
    analysis = _analysis(
        expected_price_point=6_500_000,
        actual_price_per_sqm=6_400_000,
        factors=[
            FactorInput("no_road", "맹지 (도로에 닿지 않음)", "negative", "큰 감점", True)
        ],
    )
    assert not re.search(r"[이은]\([가는]\)", _text(analysis))


def test_forecast_is_described_as_a_range_not_a_promise():
    analysis = _analysis(
        expected_price_point=9_800_000,
        actual_price_per_sqm=7_000_000,
    )
    from app.models.land_analysis import PriceForecast, PriceForecastHorizon, PriceRange

    analysis.price_forecast = PriceForecast(
        horizons=[
            PriceForecastHorizon(
                years=3,
                expected_official_price=PriceRange(
                    low=7_500_000, high=9_000_000, point=8_200_000
                ),
            )
        ]
    )
    text = " ".join(build_narrative(analysis).paragraphs)
    assert "3년 뒤" in text
    assert "~" in text
    assert "확정된 값이 아니라" in text
