"""투자 수익성 결과 설명 + 위험 추출.

원칙(llm_explain.py와 동일): LLM은 계산하지 않는다. 위험 탐지·순위·협상 포인트는
전부 이 모듈이 결정론적으로 뽑고, LLM은 완성된 항목을 한국어 문장으로 번역만 한다.
OPENAI_API_KEY가 없으면 rule-based 설명이 그대로 동작한다.

입력 원칙: 재무수치는 사용자 입력 기반(ProfitabilityResult), 시장가격은 실거래 밴드
(MarketBand), 입지는 위험 context(LocationContext)로만 결합한다 — 입지점수를 수익률에
곱하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.config import settings
from app.services.llm_client import narrative_model_name
from app.services.profitability import ProfitabilityResult
from app.services.property_market import MarketBand

Severity = Literal["critical", "high", "medium"]
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2}


@dataclass
class LocationContext:
    """site_score에서 채우는 입지 위험 맥락 (선택). 낮을수록 좋은 상위%."""

    integration_top_pct: float | None = None
    transit_distance_m: float | None = None
    competition_label: str = ""
    same_category_count: int | None = None


@dataclass
class RiskItem:
    category: str
    severity: Severity
    message: str  # 결정론적으로 작성된 사실 진술 (숫자 포함)


@dataclass
class ProfitabilityExplainResult:
    headline: str
    risks: list[RiskItem] = field(default_factory=list)
    levers: list[str] = field(default_factory=list)
    explanation: str = ""
    explanation_source: str = "rule"  # 'llm' | 'rule'


def _loan_rate_pct(result: ProfitabilityResult) -> float | None:
    for s in result.scenarios:
        if s.label == "기본":
            return s.loan_rate_pct
    return None


def _market_diff_pct(band: MarketBand | None) -> float | None:
    """대상 매입단가가 주변 중위 대비 몇 % 높은지(+)/낮은지(-)."""
    if band is None or band.subject_price_per_sqm is None or not band.p50:
        return None
    return round((band.subject_price_per_sqm - band.p50) / band.p50 * 100, 1)


def metric_lines(result: ProfitabilityResult) -> list[str]:
    """LLM에 넘길 지표 줄 — 값이 없는 항목은 줄째로 뺀다.

    전에는 값 유무와 무관하게 찍어서 'IRR: None%'가 사실 블록에 실렸고, LLM이
    그걸 받아 "IRR이 None%로 표시되어 수익성 판단의 핵심 지표가 비어 있습니다"
    라고 사용자에게 적었다. 없는 값은 말하지 않는 편이 낫다 — 위험 판정부는
    이미 `is not None`으로 거르고 있었고 여기만 빠져 있었다.
    """
    lines = [
        f"총원가 Cap Rate: {result.cap_rate_total_cost_pct}%",
        f"NOI Cap Rate: {result.cap_rate_noi_pct}%",
    ]
    if result.cash_on_cash_pct is not None:
        lines.append(f"Cash-on-Cash: {result.cash_on_cash_pct}%")
    if result.dscr is not None:
        lines.append(f"DSCR: {result.dscr}")
    if result.irr_pct is not None:
        irr = f"IRR: {result.irr_pct}%"
        if result.irr_no_appreciation_pct is not None:
            irr += f" (매각차익 제외 {result.irr_no_appreciation_pct}%)"
        lines.append(irr)
    return lines


def extract_risks(
    result: ProfitabilityResult,
    band: MarketBand | None = None,
    location: LocationContext | None = None,
) -> list[RiskItem]:
    """결정론적 위험 탐지. 심각도 순 정렬해 반환 (상위 N개 절단은 호출자 몫)."""
    risks: list[RiskItem] = []

    # 1) DSCR — 대출 상환 여력
    if result.dscr is not None:
        if result.dscr < 1.0:
            risks.append(RiskItem(
                "dscr", "critical",
                f"DSCR이 {result.dscr}로 1.0 미만입니다 — 임대수익만으로 "
                "원리금을 감당하지 못해 추가 자금이 필요합니다.",
            ))
        elif result.dscr < 1.2:
            risks.append(RiskItem(
                "dscr", "high",
                f"DSCR이 {result.dscr}로 낮아 금리 인상·공실에 취약합니다.",
            ))

    # 2) 역레버리지 — 대출금리 > NOI Cap Rate
    loan_rate = _loan_rate_pct(result)
    if (
        loan_rate is not None and loan_rate > 0
        and result.cap_rate_noi_pct < loan_rate
    ):
        risks.append(RiskItem(
            "negative_leverage", "high",
            f"운영수익률(NOI Cap {result.cap_rate_noi_pct}%)이 대출금리 "
            f"{loan_rate}%보다 낮아, 대출을 늘릴수록 자기자본 수익이 나빠집니다.",
        ))

    # 3) IRR의 매각차익 과의존
    if (
        result.irr_pct is not None
        and result.capital_gain_contribution_pct is not None
        and result.irr_pct > 0
        and result.capital_gain_contribution_pct / result.irr_pct >= 0.5
    ):
        share = round(result.capital_gain_contribution_pct / result.irr_pct * 100)
        risks.append(RiskItem(
            "capital_gain_reliance", "high",
            f"IRR {result.irr_pct}%의 약 {share}%가 매각가 상승 가정에서 나옵니다 "
            "— 운영수익만으로는 기대수익이 크게 낮아집니다.",
        ))

    # 4) 손익분기 점유율 — 공실 여력
    beo = result.break_even_occupancy_pct
    if beo is not None:
        if beo >= 95:
            risks.append(RiskItem(
                "break_even", "critical",
                f"손익분기 점유율이 {beo}%로, 거의 만실이어야 현금흐름이 유지됩니다.",
            ))
        elif beo >= 85:
            risks.append(RiskItem(
                "break_even", "medium",
                f"손익분기 점유율이 {beo}%로 공실 여력이 크지 않습니다.",
            ))

    # 5) 보수 시나리오에서 현금흐름 적자
    for s in result.scenarios:
        if s.label == "보수" and s.cash_on_cash_pct is not None:
            if s.cash_on_cash_pct < 0:
                risks.append(RiskItem(
                    "conservative_negative", "high",
                    f"보수 시나리오(공실 {s.vacancy_rate_pct}%·금리 {s.loan_rate_pct}%)"
                    f"에서 현금흐름이 적자(CoC {s.cash_on_cash_pct}%)로 전환됩니다.",
                ))
            elif s.cash_on_cash_pct < 1.0:
                risks.append(RiskItem(
                    "conservative_thin", "medium",
                    f"보수 시나리오에서 Cash-on-Cash가 {s.cash_on_cash_pct}%로 얇아집니다.",
                ))
            break

    # 6) 주변 실거래 대비 고평가
    diff = _market_diff_pct(band)
    if diff is not None and diff >= 10:
        thin = band is not None and band.insufficient_sample
        # 표본이 부족하면 비교 기준값(중위) 자체가 불안정하므로, 격차가 커도
        # severity를 high로 올리지 않는다 — 그래야 아래 8번 thin_market 위험이
        # 잘못 부풀려진 overpriced에 밀려 top-3 절단에서 빠지지 않는다.
        sev: Severity = "high" if (diff >= 20 and not thin) else "medium"
        sample_note = (
            f" (표본 {band.n}건{', 표본 부족으로 참고용' if thin else ''})"
            if band else ""
        )
        risks.append(RiskItem(
            "overpriced", sev,
            f"매입 단가가 주변 동종 실거래 중위값보다 {diff}% 높습니다{sample_note}.",
        ))

    # 7) NPV 음수
    if result.npv_manwon is not None and result.npv_manwon < 0:
        risks.append(RiskItem(
            "npv_negative", "medium",
            f"할인율 기준 NPV가 {result.npv_manwon:,.0f}만원으로 음수입니다.",
        ))

    # 8) 시장 표본 부족 — 가격 검증 신뢰도
    if band is not None and band.insufficient_sample:
        risks.append(RiskItem(
            "thin_market", "medium",
            f"비교 가능한 주변 실거래가 {band.n}건뿐이라 시장가격 검증이 제한적입니다.",
        ))

    # 9) 입지 위험 context (재무와 분리해 제시)
    if location is not None:
        if (
            location.integration_top_pct is not None
            and location.integration_top_pct >= 60
        ):
            risks.append(RiskItem(
                "location_access", "medium",
                f"도로 접근성이 분석 반경 상위 {location.integration_top_pct}%로 약한 편이라 "
                "공실 위험 시나리오(공실률 상향)를 함께 확인하는 것이 좋습니다.",
            ))
        if location.competition_label in ("높음", "매우 높음"):
            cnt = (
                f" (동일업종 {location.same_category_count}곳)"
                if location.same_category_count else ""
            )
            risks.append(RiskItem(
                "location_competition", "medium",
                f"주변 경쟁 강도가 {location.competition_label}입니다{cnt} — "
                "임차 수요·임대료 하방 압력을 감안하세요.",
            ))

    risks.sort(key=lambda r: _SEVERITY_RANK[r.severity])
    return risks


def suggest_levers(
    result: ProfitabilityResult,
    band: MarketBand | None,
    risks: list[RiskItem],
) -> list[str]:
    """위험을 근거로 한 협상·개선 포인트 (결정론적)."""
    levers: list[str] = []
    categories = {r.category for r in risks}
    diff = _market_diff_pct(band)

    if "overpriced" in categories and diff is not None:
        levers.append(
            f"매입가 조정: 주변 중위 대비 +{diff}% → 협상으로 격차를 줄이면 "
            "모든 수익률 지표가 직접 개선됩니다."
        )
    if categories & {"dscr", "negative_leverage"}:
        levers.append("대출 비율 축소 또는 금리 협상: DSCR과 자기자본 수익을 함께 개선합니다.")
    if categories & {"break_even", "conservative_negative", "conservative_thin"}:
        levers.append("임대조건 재검토: 월세·보증금 구성 또는 관리비 부담 주체를 조정하세요.")
    if "capital_gain_reliance" in categories:
        levers.append(
            "매각가 상승 가정을 낮춰 재계산: 운영수익만으로도 목표수익이 나오는지 확인하세요."
        )
    return levers


def _headline(result: ProfitabilityResult, risks: list[RiskItem]) -> str:
    cap = result.cap_rate_total_cost_pct
    coc = result.cash_on_cash_pct
    coc_txt = f", Cash-on-Cash {coc}%" if coc is not None else ""
    if any(r.severity == "critical" for r in risks):
        tone = "구조적 위험이 있어 조건 재검토가 필요합니다"
    elif any(r.severity == "high" for r in risks):
        tone = "몇 가지 개선 포인트를 확인해야 합니다"
    else:
        tone = "입력 조건 기준 큰 결격은 보이지 않습니다"
    return f"총원가 Cap Rate {cap}%{coc_txt} — {tone}."


def _rule_based_text(
    headline: str, risks: list[RiskItem], levers: list[str]
) -> str:
    parts = [f"**한 줄 요약**\n{headline}"]
    if risks:
        lines = "\n".join(f"- {r.message}" for r in risks)
        parts.append(f"**핵심 위험**\n{lines}")
    else:
        parts.append("**핵심 위험**\n입력 조건에서 두드러진 위험은 탐지되지 않았습니다.")
    if levers:
        lines = "\n".join(f"- {lv}" for lv in levers)
        parts.append(f"**개선·협상 포인트**\n{lines}")
    parts.append(
        "**참고**\n임대 조건(월세·공실률 등)은 사용자 입력 기준이며, 실제 계약·세금·"
        "권리금 등 개별 조건에 따라 달라질 수 있습니다."
    )
    return "\n\n".join(parts)


SYSTEM_PROMPT = (
    "당신은 한국 상업용 부동산 투자 분석가입니다. 백엔드가 계산·탐지한 수치와 위험을 "
    "받아, 매수 검토자가 바로 이해할 한국어 해석을 씁니다.\n\n"
    "규칙:\n"
    "- 절대 새로운 숫자를 만들지 마세요. 제공된 값만 사용하고, 없는 지표는 언급하지 마세요.\n"
    "- 아래 4개 섹션을 굵은 제목으로 작성하세요: **한 줄 요약**, **핵심 위험**, "
    "**개선·협상 포인트**, **참고**.\n"
    "- 핵심 위험은 제공된 항목만, 가장 중요한 3개 이내로. 위험을 과장하거나 축소하지 마세요.\n"
    "- 확정적 투자 조언·매수 권유 금지. 존댓말, 과장 금지."
)


async def explain_profitability(
    result: ProfitabilityResult,
    band: MarketBand | None = None,
    location: LocationContext | None = None,
    max_risks: int = 3,
) -> ProfitabilityExplainResult:
    risks_all = extract_risks(result, band, location)
    risks = risks_all[:max_risks]
    levers = suggest_levers(result, band, risks)
    headline = _headline(result, risks)

    out = ProfitabilityExplainResult(
        headline=headline, risks=risks, levers=levers,
        explanation=_rule_based_text(headline, risks, levers),
        explanation_source="rule",
    )

    if not settings.openai_api_key.strip():
        return out

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        risk_block = "\n".join(f"- [{r.severity}] {r.message}" for r in risks) or "- 없음"
        lever_block = "\n".join(f"- {lv}" for lv in levers) or "- 없음"
        band_block = ""
        if band is not None and band.p50:
            band_block = (
                f"\n주변 실거래 밴드(₩/㎡): P25 {band.p25:,.0f} / P50 {band.p50:,.0f} / "
                f"P75 {band.p75:,.0f}, 표본 {band.n}건"
            )
        user_prompt = (
            f"한 줄 요약(참고): {headline}\n"
            + "\n".join(metric_lines(result))
            + "\n"
            f"{band_block}\n"
            f"탐지된 위험:\n{risk_block}\n"
            f"개선·협상 포인트:\n{lever_block}\n\n"
            "위 항목만으로 4섹션 해석을 작성하세요. 새 숫자 생성 금지."
        )
        response = await client.chat.completions.create(
            model=narrative_model_name(),  # 서사 전용 모델 (파싱과 분리)
            max_completion_tokens=1024,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        choice = response.choices[0]
        if choice.finish_reason != "content_filter":
            text = (choice.message.content or "").strip()
            if text:
                out.explanation = text
                out.explanation_source = "llm"
    except Exception:
        pass  # rule-based 결과 유지

    return out
