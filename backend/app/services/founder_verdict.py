"""창업자 결정 카드 — 「여기 차려도 되나」를 신호등 하나로.

종전 한 줄 결론(`site_score.build_verdict_one_liner`)은 묘사였다 — "유입 조건은
좋지만 경쟁이 매우 강한 입지입니다". 사용자는 그래서 하라는 건지 말라는 건지 몰랐다.
여기서는 결정을 낸다: 🟢 해볼 만하다 / 🟡 조건이 맞으면 / 🔴 권하지 않는다.
그리고 그 결정을 바꾸는 숫자 하나 — 월세 상한 — 를 같이 낸다.
(기획: docs/SPEC_ANSWER_DECISION.md)

**문턱은 소유자가 2026-10-24에 정하고 고정했다. 결과를 돌려 보고 바꾸지 않는다**
(CONTEXT.md 「Hot」 절과 같은 이유 — 돌려 보고 잘 나오는 값을 고르면 조작이다).

    사람 흐름 \\ 경쟁 | 낮음·보통 | 높음 | 매우 높음
    좋음             |   🟢     |  🟡  |   🟡
    보통             |   🟢     |  🟡  |   🔴
    약함             |   🟡     |  🔴  |   🔴

- 사람 흐름: site_score가 이미 쓰는 문턱 그대로. 좋음 = Integration 상위 20% 이내
  그리고 Choice 상위 25% 이내, 보통 = 둘 다 상위 40% 이내.
- 경쟁: `competition_intensity_label`의 고정 구간(1㎢당 같은 업종 25곳 이상 높음,
  50곳 이상 매우 높음).
- 월세(말했을 때): 상한 이하면 그대로, 상한의 20% 이내로 넘으면 한 칸 내림, 더 넘으면 🔴.
- 상한이 0 이하(예상 매출로 인건비·고정비도 못 냄)면 🔴.
- 예상 매출 자료가 없어 상한을 못 내면 🟢를 🟡로 낮춘다.

⚠️ 이 판정은 **지금 조건 기준**이다. 앞날(개업 뒤 성패)을 맞힌다는 검증은 없다 —
카드에 그 문장을 붙인다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

GO, CONDITIONAL, NO = "go", "conditional", "no"
_ORDER = [NO, CONDITIONAL, GO]

#: 월세가 상한을 이만큼까지 넘으면 한 칸만 내린다(소유자 결정 10/24).
RENT_OVER_TOLERANCE = 0.20

_TABLE: dict[tuple[str, str], str] = {
    ("good", "low"): GO,
    ("good", "high"): CONDITIONAL,
    ("good", "very_high"): CONDITIONAL,
    ("ok", "low"): GO,
    ("ok", "high"): CONDITIONAL,
    ("ok", "very_high"): NO,
    ("weak", "low"): CONDITIONAL,
    ("weak", "high"): NO,
    ("weak", "very_high"): NO,
}

HEADLINES = {
    GO: "해볼 만합니다",
    CONDITIONAL: "조건이 맞으면 해볼 만합니다",
    NO: "권하지 않습니다",
}

FLOW_LABELS = {"good": "사람이 많이 지나가는 길", "ok": "무난한 길", "weak": "사람이 덜 지나가는 길"}
COMP_LABELS = {"low": "경쟁 보통 이하", "high": "경쟁 많은 편", "very_high": "경쟁 매우 많음"}

DISCLAIMER = "지금 조건으로 본 판정입니다 — 개업 뒤 성패를 맞힌다는 검증은 없습니다."


def flow_band(integration_top_pct: float, choice_top_pct: float) -> str:
    if integration_top_pct <= 20 and choice_top_pct <= 25:
        return "good"
    if integration_top_pct <= 40 and choice_top_pct <= 40:
        return "ok"
    return "weak"


def competition_band(intensity: str | None) -> str:
    if intensity == "매우 높음":
        return "very_high"
    if intensity == "높음":
        return "high"
    # 판단 불가(None)를 「낮음」으로 쓰지 않는다 — 판정표에서는 낮음 칸에 두되,
    # 카드 문구와 경고에서 따로 밝힌다(founder_verdict 참고).
    return "low"


def _down(signal: str) -> str:
    return _ORDER[max(_ORDER.index(signal) - 1, 0)]


@dataclass
class FounderVerdict:
    signal: str
    headline: str
    reason: str
    rent_cap_man: float | None
    rent_line: str
    flow: str
    flow_label: str
    competition: str
    competition_label: str
    integration_top_pct: float
    choice_top_pct: float
    same_category_count: int | None
    cautions: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER


def founder_verdict(
    *,
    integration_top_pct: float,
    choice_top_pct: float,
    competition_intensity: str | None,
    same_category_count: int | None,
    rent_cap_man: float | None,
    monthly_rent_man: float | None,
) -> FounderVerdict:
    flow = flow_band(integration_top_pct, choice_top_pct)
    comp = competition_band(competition_intensity)
    signal = _TABLE[(flow, comp)]
    cautions: list[str] = []

    rent_cap = round(rent_cap_man) if rent_cap_man is not None else None
    if rent_cap is None:
        if signal == GO:
            signal = CONDITIONAL
        rent_line = "이 상권의 매출 자료가 없어 월세 상한을 내지 못했어요."
    elif rent_cap <= 0:
        signal = NO
        rent_line = "예상 매출로는 인건비·고정비를 빼면 월세를 낼 여력이 없어요."
    elif monthly_rent_man is None:
        rent_line = f"월세(관리비 포함) {rent_cap:,}만원 이하면 이 판정입니다."
    else:
        rent = round(monthly_rent_man)
        if rent <= rent_cap:
            rent_line = f"월세 {rent:,}만원은 감당 상한 {rent_cap:,}만원 안이에요."
        elif rent <= rent_cap * (1 + RENT_OVER_TOLERANCE):
            signal = _down(signal)
            rent_line = (
                f"월세 {rent:,}만원은 감당 상한 {rent_cap:,}만원보다 {rent - rent_cap:,}만원 높아요 "
                f"— {rent_cap:,}만원 이하로 협상하면 한 단계 올라갑니다."
            )
        else:
            signal = NO
            rent_line = (
                f"월세 {rent:,}만원은 감당 상한 {rent_cap:,}만원을 크게 넘어요."
            )

    if competition_intensity is None:
        cautions.append("같은 업종 가게 수를 확인하지 못해 경쟁은 판단에서 빠졌어요.")

    # 「상위 0%」는 말이 안 된다 — 가장 위도 상위 1%로 쓴다.
    reason = f"{FLOW_LABELS[flow]}(서울 상위 {max(1, round(integration_top_pct))}%)"
    if competition_intensity is not None:
        reason += f", {COMP_LABELS[comp]}"
        if same_category_count is not None:
            reason += f"(같은 업종 {same_category_count}곳)"
    reason += "."

    return FounderVerdict(
        signal=signal,
        headline=HEADLINES[signal],
        reason=reason,
        rent_cap_man=rent_cap,
        rent_line=rent_line,
        flow=flow,
        flow_label=FLOW_LABELS[flow],
        competition=comp,
        competition_label=COMP_LABELS[comp] if competition_intensity is not None else "확인 못 함",
        integration_top_pct=integration_top_pct,
        choice_top_pct=choice_top_pct,
        same_category_count=same_category_count,
        cautions=cautions,
    )
