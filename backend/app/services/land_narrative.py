"""정적 서술 — 토큰 비용 0으로 판정을 한국어 문장으로 옮긴다.

**LLM은 숫자도 라벨도 만들지 않는다.** 판정은 `land_verdict`가 다 끝내 놓고,
여기서는 그 값을 인용해 문장으로만 바꾼다. LLM 서술(`land_narrative_llm`)은 이
결과를 다시 쓰는 선택적 계층이고, 실패하면 여기로 되돌아온다. 그래서 이 모듈이
서비스의 기본 경로다 — **API 키가 없어도 화면이 완성된다.**

지키는 규칙 넷:

1. **같은 요인을 두 번 말하지 않는다.** 요인 목록을 그대로 나열하면 "큰길에 접함 ·
   길목 · 도로 넓음"처럼 사실상 한 가지를 세 번 말하게 된다. key로 중복을 걷고,
   각 방향에서 가장 큰 것만 문장에 쓴다.
2. **긍정·부정의 관계를 말한다.** 나열이 아니라 "~인데도 ~", "~여서 ~"로 잇는다.
   사용자가 알고 싶은 것은 목록이 아니라 그 둘이 어떻게 이 가격을 만들었는지다.
3. **내부 용어를 쓰지 않는다.** SHAP·Integration·잔차·백분위 같은 말이 나오지
   않는다. 요인 라벨은 계약(`Factor.label`)에 이미 사람 말로 들어 있다.
4. **신뢰도가 낮으면 어조를 낮춘다.** `verdict.strength`가 유일한 기준이다 —
   서술이 자기 판단으로 세게 말하는 경로를 두지 않는다.
"""

from __future__ import annotations

from app.models.land_analysis import Factor, Narrative, StructuredAnalysis
from app.services.decision import josa

_LABEL_PHRASE = {
    "undervalued": "같은 조건 필지들에 매겨진 공시지가 수준보다 낮은 땅",
    "cheap_with_reason": "값이 낮지만 그 이유가 설명되는 땅",
    "aligned": "같은 조건 필지들과 비슷한 수준의 땅",
    "above_peers": "같은 조건 필지들보다 높게 매겨진 땅",
}


def _josa(word: str, with_final: str, without_final: str) -> str:
    """받침에 맞는 조사. **괄호 설명은 떼고 본다.**

    요인 라벨은 "맹지 (도로에 닿지 않음)"처럼 괄호로 끝나는 것이 많다. 괄호를
    그대로 넘기면 받침을 판단할 수 없어 `josa`가 "이(가)" 병기로 물러서고, 문장
    하나에 병기가 세 번 나오면 읽기 어려워진다. 조사는 소리로 결정되므로
    괄호 앞 낱말로 판단하는 것이 맞다.
    """
    base = word.split(" (")[0].strip() or word
    return josa(base, with_final, without_final)


def _tone(phrase: str, strength: str) -> str:
    """어조. `strength`가 표현 강도의 유일한 기준이다.

    앞말 받침에 따라 "~땅으로/~곳로"가 갈리므로 조사를 여기서 붙인다.
    """
    if strength == "strong":
        return "입니다"
    if strength == "moderate":
        return _josa(phrase, "으로", "로") + " 보입니다"
    return "일 수 있습니다"


def _won(value: float) -> str:
    return f"{round(value):,}원"


def _pct(value: float) -> str:
    return f"{abs(value):.0f}%"


def _dedup(factors: list[Factor], direction: str) -> list[Factor]:
    """같은 요인을 두 번 말하지 않게 key로 걷어낸다."""
    seen: set[str] = set()
    out: list[Factor] = []
    for f in factors:
        if f.direction != direction or f.key in seen:
            continue
        seen.add(f.key)
        out.append(f)
    return out


def _verdict_sentence(analysis: StructuredAnalysis) -> str:
    verdict = analysis.verdict
    peers = analysis.peer_group
    if verdict.label == "undetermined":
        # 보류 이유는 계약이 이미 사람 말로 담고 있다. 여기서 다시 쓰지 않는다.
        return f"{verdict.headline}. 비교 대상은 {peers.description}입니다."
    phrase = _LABEL_PHRASE[verdict.label]
    linker = _josa(peers.description, "과", "와")
    return f"{peers.description}{linker} 견주면 {phrase}{_tone(phrase, verdict.strength)}."


def _price_sentence(analysis: StructuredAnalysis) -> str | None:
    """금액 문장. 추정 모형이 없어도 **공시지가 자체는 말한다.**"""
    official = analysis.official_price
    if official is None:
        return None
    valuation = analysis.current_valuation
    if valuation is None:
        return (
            f"{official.base_year}년 개별공시지가는 ㎡당 {_won(official.per_sqm)}입니다."
        )
    expected = valuation.expected_official_price
    head = (
        f"{official.base_year}년 개별공시지가는 ㎡당 {_won(official.per_sqm)}이고, "
        f"조건이 비슷한 필지들로 미루어 본 값은 "
        f"{_won(expected.low)}~{_won(expected.high)} 범위입니다."
    )
    gap = valuation.discount.unexplained_pct
    if valuation.is_undervalued:
        return head + f" 설명되지 않는 차이가 {_pct(gap)}쯤 남습니다."
    if gap <= -1:
        return head + f" 미루어 본 값보다 {_pct(gap)}쯤 높습니다."
    return head


def _factor_sentence(analysis: StructuredAnalysis) -> str | None:
    """긍정·부정을 나열하지 않고 **관계로** 잇는다."""
    positives = _dedup(analysis.factors, "positive")
    negatives = _dedup(analysis.factors, "negative")
    if not positives and not negatives:
        return None

    top_pos = positives[0].label if positives else None
    top_neg = negatives[0].label if negatives else None

    if top_pos and top_neg:
        valuation = analysis.current_valuation
        explained = valuation is not None and not valuation.is_undervalued
        if explained:
            # 단점이 값을 설명하는 경우 — 그래서 싼 것이다.
            return (
                f"{top_pos}{_josa(top_pos, '은', '는')} 값을 올리는 쪽이지만, "
                f"{top_neg}{_josa(top_neg, '이', '가')} 그보다 크게 작용해 지금 값이 "
                f"낮게 매겨진 것으로 설명됩니다."
            )
        return (
            f"{top_neg}{_josa(top_neg, '이', '가')} 값을 끌어내리는데도 "
            f"{top_pos}{_josa(top_pos, '이', '')}라는 점이 남아 있어, 단점만으로는 "
            f"지금 값이 다 설명되지 않습니다."
        )
    if top_pos:
        return (
            f"값을 올리는 쪽으로는 {top_pos}{_josa(top_pos, '이', '가')} 가장 크게 "
            f"작용합니다. 눈에 걸리는 단점은 따로 잡히지 않았습니다."
        )
    return (
        f"값을 끌어내리는 쪽으로는 {top_neg}{_josa(top_neg, '이', '가')} 가장 크게 "
        f"작용합니다."
    )


def _outlook_sentence(analysis: StructuredAnalysis) -> str | None:
    """미래 전망. 방법론 A·B가 붙었을 때만 나온다."""
    parts: list[str] = []
    growth = analysis.growth_outlook
    if growth is not None and growth.excess_growth_pct is not None:
        direction = "빠르게" if growth.excess_growth_pct > 0 else "느리게"
        parts.append(
            f"지난 {growth.horizon_years}년 동안 이 땅의 공시지가는 비슷한 조건 "
            f"필지들보다 {_pct(growth.excess_growth_pct)}쯤 {direction} 올랐습니다."
        )
    forecast = analysis.price_forecast
    if forecast is not None and forecast.horizons:
        far = forecast.horizons[-1]
        rng = far.expected_official_price
        parts.append(
            f"{far.years}년 뒤 공시지가는 ㎡당 {_won(rng.low)}~{_won(rng.high)} "
            f"범위로 내다봅니다 — 확정된 값이 아니라 지금 조건이 이어질 때의 "
            f"범위입니다."
        )
    return " ".join(parts) if parts else None


def _confidence_sentence(analysis: StructuredAnalysis) -> str | None:
    """무엇이 신뢰도를 깎았는지 그대로 옮긴다. 없으면 문장을 만들지 않는다."""
    reasons = analysis.verdict.confidence.reasons
    if not reasons:
        return None
    return "다만 " + " ".join(reasons) + " 참고로만 보세요."


def build_narrative(analysis: StructuredAnalysis) -> Narrative:
    """판정 하나 → 정적 서술. 외부 호출도 토큰 비용도 없다."""
    paragraphs = [
        text
        for text in (
            _verdict_sentence(analysis),
            _price_sentence(analysis),
            _factor_sentence(analysis),
            _outlook_sentence(analysis),
            _confidence_sentence(analysis),
        )
        if text
    ]
    return Narrative(
        source="static",
        headline=analysis.verdict.headline,
        paragraphs=paragraphs,
    )
