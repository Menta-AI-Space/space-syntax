"""판정 규칙 테스트 — 외부에서 관찰되는 것만 본다.

관찰되는 행동은 셋이다: **어떤 입력에 어떤 라벨이 붙는가**, **할인분이 어떻게
쪼개지는가**, **신뢰도가 낮을 때 표현이 약해지는가**. 임계값 숫자 자체를 고정하지
않는다 — G7에서 캘리브레이션하면 바뀌는 게 정상이고, 고정하면 그 작업을 막는다.
그래서 테스트는 상수를 import해 그것을 기준으로 입력을 만든다.
"""

import json
import re

import pytest

from app.models.land_analysis import PeerGroup, Subject
from app.services import land_verdict as lv
from app.services.land_verdict import (
    FactorInput,
    VerdictInput,
    build_structured_analysis,
    classify,
    discount_breakdown,
)


def _subject() -> Subject:
    return Subject(pnu="1" * 19, base_year=2026, address="영등포구 여의도동 12-3")


def _peers(size: int = 500) -> PeerGroup:
    return PeerGroup(description=f"같은 조건 {size}필지", size=size, level="dong_zone")


def _inp(**kw) -> VerdictInput:
    base = dict(
        peer_count=500,
        peer_median_price_per_sqm=10_000_000,
        actual_price_per_sqm=10_000_000,
        expected_price_point=10_000_000,
        expected_price_low=9_000_000,
        expected_price_high=11_000_000,
    )
    base.update(kw)
    return VerdictInput(**base)  # type: ignore[arg-type]


# ── 할인분 분해: total = explained + unexplained ─────────────────────────

def test_discount_parts_add_up_to_total():
    b = discount_breakdown(peer_median=10_000_000, expected=8_000_000, actual=6_000_000)
    assert b is not None
    assert b.explained_pct == pytest.approx(20.0)
    assert b.unexplained_pct == pytest.approx(20.0)
    assert b.total_pct == pytest.approx(b.explained_pct + b.unexplained_pct)


def test_premium_side_gives_negative_parts():
    b = discount_breakdown(peer_median=10_000_000, expected=10_500_000, actual=13_000_000)
    assert b is not None
    assert b.explained_pct < 0
    assert b.unexplained_pct < 0
    assert b.total_pct == pytest.approx(b.explained_pct + b.unexplained_pct)


def test_no_peer_median_means_no_breakdown():
    assert discount_breakdown(peer_median=None, expected=1.0, actual=1.0) is None
    assert discount_breakdown(peer_median=0, expected=1.0, actual=1.0) is None


# ── "싼 땅"과 "저평가된 땅"이 갈린다 ───────────────────────────────────────

def test_cheap_but_fully_explained_is_not_undervalued():
    """맹지·부정형이 값을 다 설명하면 싸지만 저평가가 아니다."""
    label, breakdown, _ = classify(
        _inp(
            peer_median_price_per_sqm=10_000_000,
            expected_price_point=6_500_000,   # 단점이 35%를 설명한다
            actual_price_per_sqm=6_400_000,   # 남는 몫은 1%
        )
    )
    assert label == "cheap_with_reason"
    assert breakdown is not None
    assert breakdown.total_pct >= lv.CHEAP_MIN_TOTAL_DISCOUNT_PCT
    assert breakdown.unexplained_pct < lv.UNDERVALUED_MIN_UNEXPLAINED_PCT


def test_unexplained_discount_is_what_makes_it_undervalued():
    label, breakdown, _ = classify(
        _inp(
            peer_median_price_per_sqm=10_000_000,
            expected_price_point=9_800_000,
            actual_price_per_sqm=7_000_000,   # 남는 몫 28%
        )
    )
    assert label == "undervalued"
    assert breakdown is not None
    assert breakdown.unexplained_pct >= lv.UNDERVALUED_MIN_UNEXPLAINED_PCT


def test_cheap_and_undervalued_are_separate_fields():
    """두 개념이 코드에 별도로 존재한다 — 하나가 다른 하나를 함의하지 않는다."""
    sa = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(),
        inp=_inp(
            peer_median_price_per_sqm=10_000_000,
            expected_price_point=6_500_000,
            actual_price_per_sqm=6_400_000,
            price_percentile=8.0,
        ),
        model_version="test",
    )
    assert sa.current_valuation is not None
    assert sa.current_valuation.is_cheap is True
    assert sa.current_valuation.is_undervalued is False


def test_expensive_side_is_never_labelled_overvalued():
    """낙인 표현 금지 — 라벨도 문구도 '고평가'가 아니다."""
    sa = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(),
        inp=_inp(
            peer_median_price_per_sqm=10_000_000,
            expected_price_point=10_200_000,
            actual_price_per_sqm=13_000_000,
        ),
        model_version="test",
    )
    assert sa.verdict.label == "above_peers"
    assert "고평가" not in sa.verdict.headline
    # 우리가 안 보는 요인으로 비쌀 수 있다는 주의가 함께 나간다.
    assert any("한강뷰" in c or "보지 않는" in c for c in sa.caveats)


def test_similar_price_is_aligned():
    label, _, _ = classify(_inp())
    assert label == "aligned"


# ── 비교군이 모자라면 판정하지 않는다 ─────────────────────────────────────

def test_too_few_peers_is_undetermined():
    label, _, _ = classify(
        _inp(
            peer_count=lv.MIN_PEERS_FOR_VERDICT - 1,
            expected_price_point=6_000_000,
            actual_price_per_sqm=4_000_000,
        )
    )
    assert label == "undetermined"


def test_missing_model_estimate_is_undetermined_not_undervalued():
    """예상값이 없으면 '남는 몫'을 알 수 없다 — 저평가라고 말할 수 없다."""
    label, _, _ = classify(
        _inp(
            expected_price_point=None,
            expected_price_low=None,
            expected_price_high=None,
            actual_price_per_sqm=5_000_000,
        )
    )
    assert label == "undetermined"


def test_undetermined_has_no_valuation_section():
    sa = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(size=7),
        inp=_inp(
            peer_count=7,
            expected_price_point=None,
            expected_price_low=None,
            expected_price_high=None,
            actual_price_per_sqm=5_000_000,
        ),
        model_version="test",
    )
    assert sa.verdict.label == "undetermined"
    assert sa.current_valuation is None
    assert sa.verdict.strength == "weak"


# ── 신뢰도가 표현 강도의 상한이다 ─────────────────────────────────────────

def test_low_confidence_forces_weak_wording():
    sa = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(size=40),
        inp=_inp(
            peer_count=40,
            expected_price_point=9_800_000,
            expected_price_low=5_000_000,
            expected_price_high=15_000_000,   # 추정 범위가 매우 넓다
            actual_price_per_sqm=6_000_000,
            peer_similarity=0.2,
            in_training_distribution=False,
        ),
        model_version="test",
    )
    assert sa.verdict.label == "undervalued"
    assert sa.verdict.confidence.level == "low"
    assert sa.verdict.strength == "weak"
    assert sa.verdict.confidence.reasons, "무엇이 신뢰도를 깎았는지 남아야 한다"


def test_confidence_is_derived_from_named_inputs_not_a_constant():
    strong = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(size=2000),
        inp=_inp(
            peer_count=2000,
            expected_price_point=9_900_000,
            actual_price_per_sqm=6_500_000,
            peer_similarity=0.9,
            in_training_distribution=True,
        ),
        model_version="test",
    )
    weak = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(size=35),
        inp=_inp(
            peer_count=35,
            expected_price_point=9_900_000,
            actual_price_per_sqm=6_500_000,
            peer_similarity=0.3,
            in_training_distribution=False,
        ),
        model_version="test",
    )
    assert strong.verdict.confidence.score > weak.verdict.confidence.score
    assert strong.verdict.confidence.peer_count == 2000


def test_strong_wording_needs_both_big_gap_and_many_peers():
    small_gap = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(size=2000),
        inp=_inp(
            peer_count=2000,
            expected_price_point=9_900_000,
            actual_price_per_sqm=8_200_000,   # 미설명 17% — strong 문턱 아래
        ),
        model_version="test",
    )
    assert small_gap.verdict.strength == "moderate"


# ── 두 순위가 어긋나면 감추지 않는다 ─────────────────────────────────────

def test_rank_disagreement_downgrades_and_is_recorded():
    agree = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(size=2000),
        inp=_inp(
            peer_count=2000,
            expected_price_point=9_900_000,
            actual_price_per_sqm=6_500_000,
            unexplained_discount_rank_pct=2.0,
            gap_score_rank_pct=5.0,
        ),
        model_version="test",
    )
    disagree = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(size=2000),
        inp=_inp(
            peer_count=2000,
            expected_price_point=9_900_000,
            actual_price_per_sqm=6_500_000,
            unexplained_discount_rank_pct=2.0,
            gap_score_rank_pct=70.0,   # 갭 순위로는 상위가 아니다
        ),
        model_version="test",
    )
    assert agree.audit.rank_check.agreement == "agree"
    assert disagree.audit.rank_check.agreement == "disagree"
    order = ["weak", "moderate", "strong"]
    assert order.index(disagree.verdict.strength) < order.index(agree.verdict.strength)
    assert any("엇갈" in c for c in disagree.caveats)


def test_missing_rank_is_unknown_and_does_not_downgrade():
    sa = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(size=2000),
        inp=_inp(
            peer_count=2000,
            expected_price_point=9_900_000,
            actual_price_per_sqm=6_500_000,
        ),
        model_version="test",
    )
    assert sa.audit.rank_check.agreement == "unknown"
    assert sa.verdict.strength == "strong"


# ── 감사 가능성·표기 규칙 ────────────────────────────────────────────────

def test_thresholds_travel_with_the_verdict():
    """어떤 임계값으로 나온 판정인지 나중에 되짚을 수 있어야 한다."""
    sa = build_structured_analysis(
        subject=_subject(), peer_group=_peers(), inp=_inp(), model_version="test-v9"
    )
    assert sa.audit.model_version == "test-v9"
    assert sa.audit.rules_version == lv.RULES_VERSION
    assert (
        sa.audit.thresholds["undervalued_min_unexplained_pct"]
        == lv.UNDERVALUED_MIN_UNEXPLAINED_PCT
    )


def test_expected_price_is_a_range_and_official_price_carries_a_disclaimer():
    sa = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(),
        inp=_inp(expected_price_point=9_800_000, actual_price_per_sqm=7_000_000),
        model_version="test",
    )
    assert sa.current_valuation is not None
    rng = sa.current_valuation.expected_official_price
    assert rng.low < rng.high
    assert sa.official_price is not None
    assert "시세" in sa.official_price.disclaimer
    assert any("인과" in c for c in sa.caveats)


def test_factors_carry_display_labels_only():
    sa = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(),
        inp=_inp(
            factors=[
                FactorInput(
                    key="no_road_access",
                    label="맹지 (도로에 닿지 않음)",
                    direction="negative",
                    magnitude_label="큰 감점",
                    explains_discount=True,
                )
            ]
        ),
        model_version="test",
    )
    blob = json.dumps(sa.model_dump(mode="json"), ensure_ascii=False)
    # 단어 경계로 본다 — `shape`(형상) 같은 정상 단어까지 잡으면 테스트가
    # 쓸모없는 곳에서 깨진다.
    for internal in ("shap", "integration", "choice", "lightgbm", "catboost"):
        assert not re.search(rf"\b{internal}\b", blob, re.IGNORECASE), (
            f"내부 용어가 계약에 새어 나갔다: {internal}"
        )
    assert sa.factors[0].explains_discount is True


def test_price_and_peer_position_survive_a_held_verdict():
    """판정을 보류해도 **금액과 비교군 안 자리는 남는다.**

    한 덩어리로 묶었다가 되돌린 지점이다 — 추정 모형이 없는 동안 화면에서
    공시지가 금액과 갭 점수까지 사라졌다. 그 둘은 모델 산물이 아니라 DB에 있는
    사실이다.
    """
    sa = build_structured_analysis(
        subject=_subject(),
        peer_group=_peers(size=800),
        inp=_inp(
            peer_count=800,
            expected_price_point=None,
            expected_price_low=None,
            expected_price_high=None,
            actual_price_per_sqm=7_400_000,
            price_percentile=22.0,
            structure_percentile=78.0,
            gap_score=56.0,
        ),
        model_version="test",
    )
    assert sa.verdict.label == "undetermined"
    assert sa.verdict.undetermined_reason == "no_estimate"
    assert sa.current_valuation is None          # 모델 산물은 없다
    assert sa.official_price is not None         # 사실은 남는다
    assert sa.official_price.per_sqm == 7_400_000
    assert sa.peer_position is not None
    assert sa.peer_position.gap_score == 56.0
