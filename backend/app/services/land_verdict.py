"""판정 규칙 — "싼 땅"과 "저평가된 땅"을 가른다.

**싼 것과 저평가된 것은 다르다.** 싼 땅은 이유가 있어서 싼 것일 수 있다 — 맹지,
형상 불량, 급경사. 그걸 구분하지 않으면 "도로에 닿지도 않는 땅"을 저평가로
추천하게 된다. 그래서 이 모듈에는 두 개념이 **별도 필드로** 존재한다
(`is_cheap`, `is_undervalued`).

가르는 방법은 기준선을 두 개 두는 것이다::

    비교군 중앙값 ────(단점이 설명하는 몫)──── 모델 예상값 ────(남는 몫)──── 실제 공시지가
                       = explained_discount                      = unexplained_discount

- 비교군 중앙값 → 모델 예상값: 이 필지의 단점(맹지·형상·급경사·좁은 도로)을
  **통제한 뒤** 얼마쯤이 적정인가. 이 거리는 "싼 이유가 있다"의 크기다.
- 모델 예상값 → 실제 공시지가: 단점을 다 감안했는데도 남는 거리.
  **이것이 저평가의 정의다.**

⚠️ **잔차만으로 저평가를 판정하지 않는다.** 강동구 42,957필지 실측에서 헤도닉
잔차 상위 20개와 갭 점수 상위 20개가 **하나도 겹치지 않았다**(Spearman 0.35).
어느 쪽이 옳은지 사용자가 판별할 수단이 없으므로, 두 순위가 어긋나면 감추지 않고
`RankCheck`에 남기고 표현 강도를 한 단계 낮춘다.

⚠️ **음의 갭을 "고평가"라고 부르지 않는다.** 목이 나쁜데 비싼 땅은 대개 우리가
안 보는 요인(한강뷰·학군·역세권)으로 비싸다. 라벨은 `above_peers`이고 문구는
"같은 조건 대비 높음"이다. 낙인 표현은 이 모듈 어디에도 없다.

⚠️ **임계값은 전부 이름 붙인 상수다.** 지금 값은 문헌·실측에서 온 잠정치이고
**G7에서 실제 분포로 캘리브레이션한다.** 그래서 매직넘버를 함수 안에 박지 않고
모듈 상수로 올려 `AuditTrail.thresholds`에 그대로 실어 보낸다 — 나중에 어떤
임계값으로 나온 판정인지 되짚을 수 있어야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.land_analysis import (
    Attribute,
    AuditTrail,
    Confidence,
    ConfidenceLevel,
    CurrentValuation,
    DiscountBreakdown,
    Factor,
    GrowthOutlook,
    OfficialPrice,
    PeerGroup,
    PeerPosition,
    PriceForecast,
    PriceRange,
    RankCheck,
    Strength,
    StructuredAnalysis,
    Subject,
    UndeterminedReason,
    Verdict,
    VerdictLabel,
)

# 이 파일의 규칙 판(version). 판정 결과에 실려 나가므로, 규칙을 바꾸면 올린다.
RULES_VERSION = "g5.1"

# ── 임계값 (G7에서 캘리브레이션한다 — 지금은 잠정치) ─────────────────────────

# 설명되지 않는 할인분이 이 이상이면 저평가로 본다.
# 잠정 근거: 확인된 국내 실측에서 지하철 개통 후 600m 내 최대 +12.7%였다.
# 그보다 큰 미설명 할인이면 "우리가 보는 요인으로는 설명이 안 된다"고 말할 만하다.
UNDERVALUED_MIN_UNEXPLAINED_PCT = 15.0

# 같은 크기의 반대쪽. 비대칭으로 둘 이유가 아직 없다.
ABOVE_PEERS_MIN_UNEXPLAINED_PCT = 15.0

# 비교군 중앙값 대비 이만큼 싸면 (이유를 따지지 않고) "싸다"고 부른다.
CHEAP_MIN_TOTAL_DISCOUNT_PCT = 10.0

# 또는 비교군 안 가격 백분위가 이 아래면 싸다.
CHEAP_MAX_PRICE_PERCENTILE = 30.0

# 비교군이 이보다 작으면 판정하지 않는다(undetermined).
# 기존 `parcel_undervalued.MIN_STRATUM_SIZE`와 같은 값 — 한두 필지가 순위를
# 지배하는 구간에서 백분위와 중앙값이 둘 다 불안정해진다.
MIN_PEERS_FOR_VERDICT = 30

# 이보다 커야 "강하게" 말할 수 있다.
MIN_PEERS_FOR_STRONG = 200

# 표현 강도를 올리려면 미설명 할인분이 이만큼은 돼야 한다.
STRONG_UNEXPLAINED_PCT = 25.0

# 예측구간 폭 / 예측값. 이 이상이면 모델이 모른다는 뜻이라 신뢰도를 깎는다.
MODEL_UNCERTAINTY_WARN = 0.20
MODEL_UNCERTAINTY_HEAVY = 0.40

# 비교군 유사도가 이 아래면 "같은 조건"이라고 부르기 어렵다.
PEER_SIMILARITY_WARN = 0.5

# 신뢰도 점수 → 등급 경계.
CONFIDENCE_HIGH_MIN = 0.70
CONFIDENCE_MEDIUM_MIN = 0.40

# 두 순위가 이 백분위 안에 들면 "상위"로 보고, 한쪽만 상위면 어긋난 것으로 본다.
RANK_TOP_PCT = 10.0

THRESHOLDS: dict[str, float] = {
    "undervalued_min_unexplained_pct": UNDERVALUED_MIN_UNEXPLAINED_PCT,
    "above_peers_min_unexplained_pct": ABOVE_PEERS_MIN_UNEXPLAINED_PCT,
    "cheap_min_total_discount_pct": CHEAP_MIN_TOTAL_DISCOUNT_PCT,
    "cheap_max_price_percentile": CHEAP_MAX_PRICE_PERCENTILE,
    "min_peers_for_verdict": float(MIN_PEERS_FOR_VERDICT),
    "min_peers_for_strong": float(MIN_PEERS_FOR_STRONG),
    "strong_unexplained_pct": STRONG_UNEXPLAINED_PCT,
    "model_uncertainty_warn": MODEL_UNCERTAINTY_WARN,
    "model_uncertainty_heavy": MODEL_UNCERTAINTY_HEAVY,
    "peer_similarity_warn": PEER_SIMILARITY_WARN,
    "confidence_high_min": CONFIDENCE_HIGH_MIN,
    "confidence_medium_min": CONFIDENCE_MEDIUM_MIN,
    "rank_top_pct": RANK_TOP_PCT,
}

# 화면 제목. 라벨마다 하나씩, 여기서 완성해 내보낸다 — 프런트가 문구를 고르면
# 규칙이 두 곳에 생긴다.
HEADLINES: dict[VerdictLabel, str] = {
    "undervalued": "같은 조건 필지들보다 낮게 매겨진 땅",
    "cheap_with_reason": "싸지만 그 이유가 설명되는 땅",
    "aligned": "같은 조건 필지들과 비슷한 수준",
    # 낙인 표현 금지 — "고평가"라고 쓰지 않는다.
    "above_peers": "같은 조건 대비 높게 매겨진 땅",
    "undetermined": "판정을 보류했습니다",
}

# 판정을 보류한 이유별 제목. "비교군이 얇다"(이 필지의 사정)와 "추정 모형이 아직
# 없다"(우리 사정)를 한 문구로 뭉치면 사용자가 데이터가 없는 줄 안다.
UNDETERMINED_HEADLINES: dict[UndeterminedReason, str] = {
    "few_peers": "비교할 필지가 모자라 판정하지 않았습니다",
    "no_estimate": "추정 모형이 아직 붙지 않아 판정을 보류했습니다",
    "missing_price": "이 필지의 공시지가를 확인하지 못했습니다",
}

# 어떤 판정에든 함께 나가는 주의. 화면이 이걸 빼고 그릴 수 없게 계약에 담는다.
BASE_CAVEATS: tuple[str, ...] = (
    "개별공시지가 기준입니다. 시세·감정평가액과 다릅니다.",
    "같은 조건 필지들에 매겨진 공시지가 수준과의 비교이며, 시세 대비 저평가를 뜻하지 않습니다.",
    "인과 추정이 아닙니다. 개통 전후 비교로 검증하지 않았습니다.",
)

# 음의 갭 쪽에만 붙는다. 낙인으로 읽히지 않게 하는 문장이다.
ABOVE_PEERS_CAVEAT = (
    "우리가 보지 않는 요인(한강뷰·학군·역세권 등)으로 높을 수 있습니다. "
    "고평가라는 뜻이 아닙니다."
)


@dataclass
class FactorInput:
    """요인 하나. `explains_discount=True`면 '싼 이유'로 쓰인 단점이다."""

    key: str
    label: str
    direction: str  # "positive" | "negative"
    magnitude_label: str
    explains_discount: bool = False
    detail: str | None = None


@dataclass
class VerdictInput:
    """판정에 필요한 입력 전부. 모델 출력과 비교군 통계를 여기서 합친다.

    ⚠️ 값이 없으면 0을 넣지 말고 `None`으로 둔다 — 0은 "정보 없음"이 아니라
    "최악"으로 읽힌다.
    """

    peer_count: int
    peer_median_price_per_sqm: float | None = None
    actual_price_per_sqm: float | None = None
    expected_price_point: float | None = None
    expected_price_low: float | None = None
    expected_price_high: float | None = None

    peer_similarity: float | None = None
    in_training_distribution: bool | None = None

    price_percentile: float | None = None
    structure_percentile: float | None = None
    gap_score: float | None = None

    unexplained_discount_rank_pct: float | None = None
    gap_score_rank_pct: float | None = None

    factors: list[FactorInput] = field(default_factory=list)


# ── 할인분 분해 ──────────────────────────────────────────────────────────

def discount_breakdown(
    *,
    peer_median: float | None,
    expected: float | None,
    actual: float | None,
) -> DiscountBreakdown | None:
    """비교군 중앙값을 100%로 놓고 할인분을 두 몫으로 쪼갠다.

    ``total = explained + unexplained``가 **정확히** 성립한다. 둘을 따로 계산해
    빼면 부호가 어긋나는 사고가 난다(선례: `fitted_after − actual_before`로
    계산해 15필지 중 4개가 "도로를 냈는데 지가 하락"으로 나온 일).

    셋 중 하나가 없으면 **아무 몫도 계산하지 않는다** → None. 특히 모델 예상값이
    없을 때 전체를 "설명됨"이나 "설명 안 됨" 한쪽으로 밀지 않는다 — 어느 쪽으로
    밀어도 화면에 거짓이 찍힌다. 그 상태는 판정 자체가 `undetermined`다.
    """
    if peer_median is None or peer_median <= 0 or actual is None or expected is None:
        return None
    total = (peer_median - actual) / peer_median * 100.0
    explained = (peer_median - expected) / peer_median * 100.0
    unexplained = (expected - actual) / peer_median * 100.0
    return DiscountBreakdown(
        total_pct=round(total, 2),
        explained_pct=round(explained, 2),
        unexplained_pct=round(unexplained, 2),
    )


# ── 신뢰도 ───────────────────────────────────────────────────────────────

def assess_confidence(inp: VerdictInput) -> Confidence:
    """임의 숫자를 쓰지 않는다 — 네 근거에서 깎아 내려간다.

    비교군 수 · 비교군 유사도 · 모델 불확실성 · 학습분포 내 여부.
    무엇이 깎았는지 `reasons`에 사람이 읽는 문장으로 남긴다.
    """
    score = 1.0
    reasons: list[str] = []

    if inp.peer_count < MIN_PEERS_FOR_VERDICT:
        score -= 0.35
        reasons.append(f"비교할 필지가 {inp.peer_count}개뿐입니다.")
    elif inp.peer_count < MIN_PEERS_FOR_STRONG:
        score -= 0.15
        reasons.append(f"비교 필지가 {inp.peer_count}개로 넉넉하지 않습니다.")

    if inp.peer_similarity is not None and inp.peer_similarity < PEER_SIMILARITY_WARN:
        score -= 0.20
        reasons.append("비교 필지들과 조건이 많이 다릅니다.")

    uncertainty = model_uncertainty_pct(inp)
    if uncertainty is not None:
        if uncertainty >= MODEL_UNCERTAINTY_HEAVY:
            score -= 0.20
            reasons.append("추정 범위가 넓습니다.")
        elif uncertainty >= MODEL_UNCERTAINTY_WARN:
            score -= 0.10
            reasons.append("추정 범위가 다소 넓습니다.")

    if inp.in_training_distribution is False:
        score -= 0.25
        reasons.append("비슷한 조건의 필지로 학습한 적이 없는 유형입니다.")

    score = max(0.0, min(1.0, round(score, 3)))
    return Confidence(
        score=score,
        level=_confidence_level(score),
        peer_count=inp.peer_count,
        peer_similarity=inp.peer_similarity,
        model_uncertainty_pct=uncertainty,
        in_training_distribution=inp.in_training_distribution,
        reasons=reasons,
    )


def model_uncertainty_pct(inp: VerdictInput) -> float | None:
    """예측구간 폭 / 예측값. 구간이나 예측값이 없으면 None."""
    if inp.expected_price_low is None or inp.expected_price_high is None:
        return None
    base = inp.expected_price_point
    if base is None:
        base = (inp.expected_price_low + inp.expected_price_high) / 2.0
    if not base:
        return None
    return round((inp.expected_price_high - inp.expected_price_low) / base, 3)


def _confidence_level(score: float) -> ConfidenceLevel:
    if score >= CONFIDENCE_HIGH_MIN:
        return "high"
    if score >= CONFIDENCE_MEDIUM_MIN:
        return "medium"
    return "low"


# ── 두 순위가 어긋나는가 ─────────────────────────────────────────────────

def check_ranks(inp: VerdictInput) -> RankCheck:
    """잔차(미설명 할인) 순위와 갭 점수 순위의 일치 여부.

    한쪽만 상위 10%면 어긋난 것으로 본다. 둘 중 하나가 없으면 `unknown`이고,
    그때는 강도를 깎지 않는다 — 없는 것을 불일치로 세면 모든 판정이 약해진다.
    """
    a = inp.unexplained_discount_rank_pct
    b = inp.gap_score_rank_pct
    if a is None or b is None:
        agreement = "unknown"
    else:
        a_top = a <= RANK_TOP_PCT
        b_top = b <= RANK_TOP_PCT
        agreement = "agree" if a_top == b_top else "disagree"
    return RankCheck(
        unexplained_discount_rank_pct=a,
        gap_score_rank_pct=b,
        agreement=agreement,
    )


# ── 판정 ─────────────────────────────────────────────────────────────────

def is_cheap(breakdown: DiscountBreakdown | None, price_percentile: float | None) -> bool:
    """싸다 — 이유를 따지지 않은 사실."""
    if breakdown is not None and breakdown.total_pct >= CHEAP_MIN_TOTAL_DISCOUNT_PCT:
        return True
    return price_percentile is not None and price_percentile <= CHEAP_MAX_PRICE_PERCENTILE


def is_undervalued(breakdown: DiscountBreakdown | None) -> bool:
    """저평가 — 단점을 다 감안해도 남는 할인분이 크다. cheap과 별개 개념이다."""
    return (
        breakdown is not None
        and breakdown.unexplained_pct >= UNDERVALUED_MIN_UNEXPLAINED_PCT
    )


def classify(
    inp: VerdictInput,
) -> tuple[VerdictLabel, DiscountBreakdown | None, UndeterminedReason | None]:
    """라벨 · 할인분 분해 · (보류라면) 보류 이유를 함께 돌려준다."""
    breakdown = discount_breakdown(
        peer_median=inp.peer_median_price_per_sqm,
        expected=inp.expected_price_point,
        actual=inp.actual_price_per_sqm,
    )

    # 비교군이 모자라거나 모델 예상값이 없으면 판정하지 않는다.
    # 여기서 억지로 라벨을 붙이면 화면이 "저평가"라고 단정하고, 사용자는 그게
    # 비교군 3개에서 나온 말인지 알 수 없다.
    if inp.actual_price_per_sqm is None:
        return "undetermined", breakdown, "missing_price"
    if inp.peer_count < MIN_PEERS_FOR_VERDICT:
        return "undetermined", breakdown, "few_peers"
    if breakdown is None or inp.expected_price_point is None:
        return "undetermined", breakdown, "no_estimate"

    if breakdown.unexplained_pct >= UNDERVALUED_MIN_UNEXPLAINED_PCT:
        return "undervalued", breakdown, None
    if breakdown.unexplained_pct <= -ABOVE_PEERS_MIN_UNEXPLAINED_PCT:
        return "above_peers", breakdown, None
    if is_cheap(breakdown, inp.price_percentile):
        # 싸긴 싼데 미설명분이 작다 = 단점이 그 값을 설명한다.
        return "cheap_with_reason", breakdown, None
    return "aligned", breakdown, None


def decide_strength(
    *,
    label: VerdictLabel,
    breakdown: DiscountBreakdown | None,
    confidence: Confidence,
    rank_check: RankCheck,
) -> Strength:
    """표현 강도. **신뢰도가 상한을 정한다.**

    낮은 신뢰도에서 강한 말을 쓰지 못하게 하는 것이 이 함수의 목적이다.
    두 순위가 어긋나면 한 단계 더 낮춘다.
    """
    if label == "undetermined":
        return "weak"

    magnitude = abs(breakdown.unexplained_pct) if breakdown else 0.0
    base: Strength = "moderate"
    if magnitude >= STRONG_UNEXPLAINED_PCT and confidence.peer_count >= MIN_PEERS_FOR_STRONG:
        base = "strong"
    elif label == "aligned":
        base = "weak"

    order: list[Strength] = ["weak", "moderate", "strong"]
    cap: Strength = {"high": "strong", "medium": "moderate", "low": "weak"}[confidence.level]
    level = min(order.index(base), order.index(cap))

    if rank_check.agreement == "disagree":
        level = max(0, level - 1)
    return order[level]


def build_caveats(label: VerdictLabel, rank_check: RankCheck) -> list[str]:
    out = list(BASE_CAVEATS)
    if label == "above_peers":
        out.append(ABOVE_PEERS_CAVEAT)
    if rank_check.agreement == "disagree":
        out.append(
            "판정 방식 두 가지(설명되지 않는 할인분 · 구조 대비 가격 갭)의 순위가 "
            "엇갈립니다. 참고로만 보세요."
        )
    return out


# ── 조립 ─────────────────────────────────────────────────────────────────

def build_structured_analysis(
    *,
    subject: Subject,
    peer_group: PeerGroup,
    inp: VerdictInput,
    attributes: list[Attribute] | None = None,
    model_version: str,
    growth_outlook: GrowthOutlook | None = None,
    price_forecast: PriceForecast | None = None,
    base_year: int | None = None,
) -> StructuredAnalysis:
    """입력을 계약 하나로 조립한다. 여기서 나온 것만 밖으로 나간다."""
    label, breakdown, undetermined_reason = classify(inp)
    confidence = assess_confidence(inp)
    rank_check = check_ranks(inp)
    strength = decide_strength(
        label=label, breakdown=breakdown, confidence=confidence, rank_check=rank_check
    )

    year = base_year if base_year is not None else subject.base_year

    # 금액은 사실이다 — 추정 모형이 없어도 화면에 남는다.
    official = (
        OfficialPrice(per_sqm=inp.actual_price_per_sqm, base_year=year)
        if inp.actual_price_per_sqm is not None
        else None
    )
    position = PeerPosition(
        price_percentile=inp.price_percentile,
        structure_percentile=inp.structure_percentile,
        gap_score=inp.gap_score,
    )

    valuation: CurrentValuation | None = None
    expected_range = _expected_range(inp)
    if expected_range is not None and breakdown is not None:
        valuation = CurrentValuation(
            expected_official_price=expected_range,
            discount=breakdown,
            is_cheap=is_cheap(breakdown, inp.price_percentile),
            is_undervalued=is_undervalued(breakdown),
        )

    analysis = StructuredAnalysis(
        subject=subject,
        attributes=attributes or [],
        peer_group=peer_group,
        factors=[
            Factor(
                key=f.key,
                label=f.label,
                direction=f.direction,  # type: ignore[arg-type]
                magnitude_label=f.magnitude_label,
                explains_discount=f.explains_discount,
                detail=f.detail,
            )
            for f in inp.factors
        ],
        verdict=Verdict(
            label=label,
            headline=(
                UNDETERMINED_HEADLINES[undetermined_reason]
                if undetermined_reason is not None
                else HEADLINES[label]
            ),
            strength=strength,
            confidence=confidence,
            undetermined_reason=undetermined_reason,
        ),
        official_price=official,
        peer_position=position,
        current_valuation=valuation,
        growth_outlook=growth_outlook,
        price_forecast=price_forecast,
        caveats=build_caveats(label, rank_check),
        audit=AuditTrail(
            model_version=model_version,
            rules_version=RULES_VERSION,
            thresholds=dict(THRESHOLDS),
            rank_check=rank_check,
        ),
    )
    # 정적 서술은 토큰 비용이 0이므로 **항상** 채워 보낸다 — 화면이 LLM을 기다릴
    # 이유가 없고, LLM이 없는 환경에서도 설명이 나온다. 순환 import를 피하려고
    # 여기서 늦게 들여온다(서술은 완성된 판정을 읽는 쪽이다).
    from app.services.land_narrative import build_narrative

    analysis.narrative = build_narrative(analysis)
    return analysis


def _expected_range(inp: VerdictInput) -> PriceRange | None:
    """예상값은 범위로만 담는다. 구간이 없으면 판정 자체가 성립하지 않는다."""
    if inp.expected_price_low is None or inp.expected_price_high is None:
        return None
    return PriceRange(
        low=inp.expected_price_low,
        high=inp.expected_price_high,
        point=inp.expected_price_point,
    )
