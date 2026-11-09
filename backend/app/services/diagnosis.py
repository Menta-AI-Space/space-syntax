"""기존 매장 매출 부진 진단 — "왜 안 나오는가"를 결정론적으로 순위 매긴다.

설계 원칙(decision.py / verdicts.py와 동일, 타협 없음):
**원인 목록·심각도·순서는 전부 여기(파이썬)에서 실측값과 명명된 임계값으로
결정한다.** LLM은 이미 정해진 원인을 한국어 문장으로 옮길 뿐이고, 꺼져 있으면
같은 결론이 규칙 템플릿으로 나온다. 같은 입력은 언제나 같은 원인 순서를 낸다.

원인은 "LLM이 그럴듯하다고 생각한 것"이 아니라, 이미 이 저장소가 실제로
측정하고 있는 신호에서만 나온다:
  - 접근성 백분위          (space syntax integration/choice)
  - 경쟁 밀도·잠식 위험     (cannibalization.py)
  - 생활인구 시간대 프로파일 vs 업종 핵심 시간대 (living_pop.py)
  - 임대료 부담률           (upjong_cost_defaults.json)
  - 상권 점포당 매출 추세    (trdar_sales_local.py)
  - 배후 수요 점수          (site_score.py)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.living_pop import LivingPopProfile

# ---------------------------------------------------------------------------
# 판정 임계값 — 전부 이름을 붙여 한 곳에 모은다
# ---------------------------------------------------------------------------

#: 예상 매출 대비 이 %p 이상 낮으면 '입지 대비 매출 부진'.
#: 상권 벤치마크 자체가 분기 평균이고 입지보정도 ±0.35 폭이라, 그보다 작은 차이는
#: 신호가 아니라 잡음으로 본다. 반대쪽도 같은 폭으로 대칭 적용한다.
DIAGNOSE_BAND_PCT = 15.0

VERDICT_UNDER = "입지 대비 매출 부진"
VERDICT_ON_PAR = "입지 수준의 매출"
VERDICT_OVER = "입지 대비 선전"

#: 접근성(찾아오기 쉬움) 백분위 — decision.build_risks가 '상위 60% 초과'를 이미
#: 위험으로, build_strengths가 '상위 40% 이내'를 강점으로 쓰고 있어 그 경계를 그대로 쓴다.
ACCESS_HIGH_TOP_PCT = 60.0
ACCESS_MEDIUM_TOP_PCT = 40.0

#: 잠식 위험 점수 — decision.py가 45를 강점/위험의 경계로 쓴다. 60은 그 위 단계.
COMPETITION_HIGH_RISK = 60.0
COMPETITION_MEDIUM_RISK = 45.0

#: 시간대 적합 지수 = (업종 핵심 시간대 평균 체류인구 / 하루 평균 체류인구) × 100.
#: 100이면 "핵심 시간대에 딱 평균만큼 사람이 있다". 그 아래면 정작 팔아야 할
#: 시간에 사람이 빠지는 자리라는 뜻이다.
TIME_FIT_HIGH_INDEX = 90.0
TIME_FIT_MEDIUM_INDEX = 97.0

#: 임대료 부담률 — 업종별 rent_ratio_high(음식 15%, 소매 8% …)를 기준으로,
#: 그 배수로 심각도를 나눈다.
RENT_BURDEN_HIGH_MULTIPLE = 1.5
RENT_BURDEN_MEDIUM_MULTIPLE = 1.0

#: 상권 점포당 매출 추세(적재 분기 전 구간 변화율, %).
TREND_HIGH_PCT = -10.0
TREND_MEDIUM_PCT = -3.0

#: 배후 수요 subscore — site_score.score_qualitative_label의 '약함(40)'·'무난(55)' 경계.
DEMAND_HIGH_SCORE = 40.0
DEMAND_MEDIUM_SCORE = 55.0

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

#: 같은 심각도 안에서의 고정 순서. 매출에 직접적으로 작용하는 순서
#: (사람이 오는가 → 그 중 우리에게 오는가 → 팔 시간에 있는가 → 시장이 커지는가
#: → 남는가)로 고정했다. 심각도가 같을 때 순서가 흔들리지 않게 하려는 목적이며,
#: LLM이 개입하지 않는다.
CAUSE_PRIORITY = [
    "accessibility",
    "competition",
    "time_mismatch",
    "demand",
    "trend",
    "rent_burden",
]

CAUSE_LABELS = {
    "accessibility": "찾아오기 어려운 자리",
    "competition": "경쟁 과밀·고객 잠식",
    "time_mismatch": "장사 시간대에 사람이 없음",
    "demand": "배후 수요 부족",
    "trend": "상권 매출 자체가 하락",
    "rent_burden": "임대료 부담 과다",
}

#: 업종(프로파일)별 핵심 영업 시간대. 생활인구 시간대 프로파일과 맞춰 보기 위한
#: 것으로, living_pop.py가 이미 쓰는 시간대 구분(출근/점심/저녁/심야/주말낮)과
#: 같은 사고방식이다.
CORE_HOURS_BY_PROFILE: dict[str, tuple[int, ...]] = {
    "cafe": (7, 8, 9, 10, 11, 12, 13, 14, 15, 16),
    "restaurant": (11, 12, 13, 17, 18, 19, 20),
    "convenience": (7, 8, 12, 13, 18, 19, 20, 21, 22, 23),
    "clinic": (9, 10, 11, 12, 14, 15, 16, 17, 18),
    "pharmacy": (9, 10, 11, 12, 14, 15, 16, 17, 18, 19),
    "academy": (15, 16, 17, 18, 19, 20, 21),
    "beauty": (11, 12, 13, 14, 15, 16, 17, 18, 19),
    "fitness": (6, 7, 8, 18, 19, 20, 21, 22),
    "retail_specialty": (11, 12, 13, 14, 15, 16, 17, 18, 19, 20),
    "life_service": (9, 10, 11, 12, 14, 15, 16, 17, 18),
    "leisure": (14, 15, 16, 17, 18, 19, 20, 21, 22),
    "lodging": (18, 19, 20, 21, 22, 23),
    "professional_office": (9, 10, 11, 12, 14, 15, 16, 17, 18),
}

CORE_HOURS_BY_ARCHETYPE: dict[str, tuple[int, ...]] = {
    "food_drink": (11, 12, 13, 17, 18, 19, 20),
    "daily_retail": (11, 12, 13, 14, 15, 16, 17, 18, 19, 20),
    "medical": (9, 10, 11, 12, 14, 15, 16, 17, 18),
    "education": (15, 16, 17, 18, 19, 20, 21),
    "beauty_service": (11, 12, 13, 14, 15, 16, 17, 18, 19),
    "general": (10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20),
}


def core_hours_for(profile_id: str | None, archetype: str) -> tuple[int, ...]:
    if profile_id and profile_id in CORE_HOURS_BY_PROFILE:
        return CORE_HOURS_BY_PROFILE[profile_id]
    return CORE_HOURS_BY_ARCHETYPE.get(archetype, CORE_HOURS_BY_ARCHETYPE["general"])


def core_hour_fit_index(
    profile: LivingPopProfile | None, hours: tuple[int, ...]
) -> float | None:
    """핵심 시간대 평균 체류인구 ÷ 하루 평균 × 100. 데이터 없으면 None."""
    if profile is None or not profile.weekday_hourly:
        return None
    hourly = profile.weekday_hourly
    core = [hourly[h] for h in hours if h in hourly]
    if not core or not hourly:
        return None
    day_avg = sum(hourly.values()) / len(hourly)
    if day_avg <= 0:
        return None
    return round((sum(core) / len(core)) / day_avg * 100.0, 1)


# ---------------------------------------------------------------------------
# 자료구조
# ---------------------------------------------------------------------------


@dataclass
class DiagnosisSignals:
    """진단이 보는 실측값 전부. 없는 값은 None — 지어내지 않는다."""

    expected_monthly_sales_man: float | None = None
    current_monthly_sales_man: float = 0.0
    integration_top_pct: float | None = None
    choice_top_pct: float | None = None
    cannibalization_risk: float | None = None
    competitor_count: int | None = None
    self_store_count: int | None = None
    core_hour_index: float | None = None
    core_hours: tuple[int, ...] = ()
    monthly_rent_man: float | None = None
    rent_burden_cap_pct: float | None = None
    trend_change_pct: float | None = None
    trend_quarters: int | None = None
    trdar_nm: str = ""
    #: trdar_for_point()의 contains — False면 폴리곤 안이 아니라 300m 이내
    #: 가장 가까운 상권으로 대체한 값이라, "이 상권의" 라고 단정할 수 없다.
    trdar_contains: bool = True
    trdar_distance_m: float | None = None
    demand_score: float | None = None
    demand_population: int | None = None
    station_name: str | None = None
    station_distance_m: float | None = None


@dataclass
class Cause:
    key: str
    label: str
    severity: str
    finding: str
    action: str


@dataclass
class DiagnosisResult:
    expected_monthly_sales_man: float | None
    gap_man: float | None
    gap_pct: float | None
    verdict_label: str
    causes: list[Cause] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------


def sales_gap(
    *, expected_man: float | None, current_man: float
) -> tuple[float | None, float | None]:
    """(절대 차이 만원, 상대 차이 %). 예상 매출이 없으면 (None, None)."""
    if not expected_man or expected_man <= 0:
        return None, None
    gap = current_man - expected_man
    return round(gap, 1), round(gap / expected_man * 100.0, 1)


def diagnose_verdict_label(gap_pct: float | None) -> str:
    if gap_pct is None:
        return "예상 매출 근거 없음 — 비교 불가"
    if gap_pct <= -DIAGNOSE_BAND_PCT:
        return VERDICT_UNDER
    if gap_pct >= DIAGNOSE_BAND_PCT:
        return VERDICT_OVER
    return VERDICT_ON_PAR


def _trdar_desc(s: DiagnosisSignals) -> str:
    """상권 추세를 가리키는 문구 — 실제로 그 폴리곤 안인지에 따라 단정 정도를 다르게 한다.

    trdar_for_point()가 폴리곤 안(contains=True)이 아니라 300m 이내 가장 가까운
    상권으로 대체한 값이면, "이 자리는 이 상권이다"라고 단정하지 않고 인접
    상권의 추세로 참고한다는 것을 문구에 남긴다.
    """
    if not s.trdar_nm:
        return "해당 상권"
    if s.trdar_contains:
        return f"{s.trdar_nm} 상권"
    if s.trdar_distance_m is not None:
        return f"가장 가까운 {s.trdar_nm} 상권(약 {s.trdar_distance_m:.0f}m 인근, 추정)"
    return f"가장 가까운 {s.trdar_nm} 상권(추정)"


def _fmt_hours(hours: tuple[int, ...]) -> str:
    if not hours:
        return ""
    runs: list[str] = []
    start = prev = hours[0]
    for h in hours[1:]:
        if h == prev + 1:
            prev = h
            continue
        runs.append(f"{start}~{prev + 1}시" if prev != start else f"{start}시")
        start = prev = h
    runs.append(f"{start}~{prev + 1}시" if prev != start else f"{start}시")
    return "·".join(runs)


# ---------------------------------------------------------------------------
# 원인 도출 — 신호 하나당 규칙 하나, 전부 실측값으로 문장을 만든다
# ---------------------------------------------------------------------------


def build_causes(s: DiagnosisSignals) -> list[Cause]:
    causes: list[Cause] = []

    # 1) 접근성
    if s.integration_top_pct is not None and s.integration_top_pct > ACCESS_MEDIUM_TOP_PCT:
        severity = "high" if s.integration_top_pct > ACCESS_HIGH_TOP_PCT else "medium"
        finding = f"찾아오기 쉬움이 분석 반경 내 상위 {s.integration_top_pct:.0f}%"
        if s.choice_top_pct is not None:
            finding += f", 지나가는 길은 상위 {s.choice_top_pct:.0f}%"
        finding += "로, 사람이 지나다 들르기 어려운 안쪽 자리입니다."
        causes.append(
            Cause(
                "accessibility",
                CAUSE_LABELS["accessibility"],
                severity,
                finding,
                "간판·돌출사인으로 큰길에서의 시인성을 먼저 확보하고, "
                "배달·예약처럼 위치를 덜 타는 채널의 매출 비중을 늘리세요.",
            )
        )

    # 2) 경쟁
    if s.cannibalization_risk is not None and s.cannibalization_risk >= COMPETITION_MEDIUM_RISK:
        severity = (
            "high" if s.cannibalization_risk >= COMPETITION_HIGH_RISK else "medium"
        )
        finding = (
            f"도보 5분 내 동일업종 {s.competitor_count or 0}곳, "
            f"고객 잠식 위험 {s.cannibalization_risk:.0f}/100입니다."
        )
        if s.self_store_count:
            finding += f" 같은 브랜드 점포도 {s.self_store_count}곳 있어 자기잠식이 겹칩니다."
        causes.append(
            Cause(
                "competition",
                CAUSE_LABELS["competition"],
                severity,
                finding,
                "최근접 경쟁점 3곳의 가격·회전율·주력 메뉴를 실사해 "
                "겹치지 않는 시간대나 메뉴로 포지션을 옮기세요.",
            )
        )

    # 3) 시간대 미스매치
    if s.core_hour_index is not None and s.core_hour_index < TIME_FIT_MEDIUM_INDEX:
        severity = "high" if s.core_hour_index < TIME_FIT_HIGH_INDEX else "medium"
        causes.append(
            Cause(
                "time_mismatch",
                CAUSE_LABELS["time_mismatch"],
                severity,
                f"이 업종의 핵심 영업시간({_fmt_hours(s.core_hours)}) 체류인구가 "
                f"하루 평균의 {s.core_hour_index:.0f}% 수준입니다. "
                "정작 팔아야 할 시간에 사람이 빠지는 자리입니다.",
                "체류인구가 몰리는 시간대로 영업시간을 옮기거나, "
                "그 시간대에 팔 수 있는 상품을 추가하세요.",
            )
        )

    # 4) 배후 수요
    if s.demand_score is not None and s.demand_score < DEMAND_MEDIUM_SCORE:
        severity = "high" if s.demand_score < DEMAND_HIGH_SCORE else "medium"
        finding = f"배후 수요 점수가 {s.demand_score:.0f}/100입니다."
        if s.demand_population:
            finding += f" 분석 반경 내 추정 배후인구는 {s.demand_population:,}명입니다."
        causes.append(
            Cause(
                "demand",
                CAUSE_LABELS["demand"],
                severity,
                finding,
                "반경 안 고정수요만으로는 부족합니다. "
                "배달 반경 확대나 단체·기업 고객 확보로 수요권을 넓히세요.",
            )
        )

    # 5) 상권 추세
    if s.trend_change_pct is not None and s.trend_change_pct <= TREND_MEDIUM_PCT:
        severity = "high" if s.trend_change_pct <= TREND_HIGH_PCT else "medium"
        causes.append(
            Cause(
                "trend",
                CAUSE_LABELS["trend"],
                severity,
                f"{_trdar_desc(s)}의 동일업종 점포당 월매출이 "
                f"최근 {s.trend_quarters or 0}개 분기 동안 {s.trend_change_pct:.1f}% "
                "변했습니다. 내 매장만의 문제가 아니라 상권 전체가 줄고 있습니다.",
                "상권 회복을 기다리기보다 고정비를 먼저 낮추고, "
                "재계약 시점에 임대료 인하 근거로 이 수치를 쓰세요.",
            )
        )

    # 6) 임대료 부담 (현재 매출 기준 — 매출이 낮으니 부담률이 올라간 것)
    if (
        s.monthly_rent_man
        and s.current_monthly_sales_man > 0
        and s.rent_burden_cap_pct
    ):
        burden = s.monthly_rent_man / s.current_monthly_sales_man * 100.0
        if burden >= s.rent_burden_cap_pct * RENT_BURDEN_MEDIUM_MULTIPLE:
            severity = (
                "high"
                if burden >= s.rent_burden_cap_pct * RENT_BURDEN_HIGH_MULTIPLE
                else "medium"
            )
            causes.append(
                Cause(
                    "rent_burden",
                    CAUSE_LABELS["rent_burden"],
                    severity,
                    f"월세 {s.monthly_rent_man:,.0f}만원이 현재 매출 "
                    f"{s.current_monthly_sales_man:,.0f}만원의 {burden:.1f}%로, "
                    f"이 업종의 적정 상한 {s.rent_burden_cap_pct:.0f}%를 넘습니다.",
                    f"매출을 {s.monthly_rent_man / (s.rent_burden_cap_pct / 100.0):,.0f}만원까지 "
                    "끌어올리거나, 그게 어려우면 임대료 재협상이 먼저입니다.",
                )
            )

    causes.sort(
        key=lambda c: (
            SEVERITY_ORDER.get(c.severity, 9),
            CAUSE_PRIORITY.index(c.key) if c.key in CAUSE_PRIORITY else 99,
        )
    )
    return causes


def build_diagnosis_strengths(s: DiagnosisSignals) -> list[str]:
    """원인의 반대쪽 — 이미 잘 되고 있어 건드리면 안 되는 것들."""
    out: list[str] = []
    if s.integration_top_pct is not None and s.integration_top_pct <= ACCESS_MEDIUM_TOP_PCT:
        out.append(f"찾아오기 쉬운 자리 · 분석 반경 내 상위 {s.integration_top_pct:.0f}%")
    if s.cannibalization_risk is not None and s.cannibalization_risk < COMPETITION_MEDIUM_RISK:
        out.append(
            f"동일업종 경쟁 {s.competitor_count or 0}곳 · "
            f"잠식위험 {s.cannibalization_risk:.0f}/100으로 낮은 편"
        )
    if s.core_hour_index is not None and s.core_hour_index >= TIME_FIT_MEDIUM_INDEX:
        out.append(
            f"핵심 영업시간 체류인구가 하루 평균의 {s.core_hour_index:.0f}%로 시간대는 맞음"
        )
    if s.trend_change_pct is not None and s.trend_change_pct > TREND_MEDIUM_PCT:
        out.append(
            f"{_trdar_desc(s)} 점포당 매출 추세 {s.trend_change_pct:+.1f}% — 상권 자체는 버팀"
        )
    if s.station_distance_m is not None and s.station_distance_m <= 400:
        out.append(f"{s.station_name or '지하철역'} 도보 {s.station_distance_m:.0f}m")
    if not out:
        out.append("수치상 유지할 만한 강점이 뚜렷하지 않습니다 — 원인 항목부터 하나씩 제거하세요.")
    return out[:3]


def diagnose(s: DiagnosisSignals) -> DiagnosisResult:
    gap_man, gap_pct = sales_gap(
        expected_man=s.expected_monthly_sales_man,
        current_man=s.current_monthly_sales_man,
    )
    return DiagnosisResult(
        expected_monthly_sales_man=s.expected_monthly_sales_man,
        gap_man=gap_man,
        gap_pct=gap_pct,
        verdict_label=diagnose_verdict_label(gap_pct),
        causes=build_causes(s),
        strengths=build_diagnosis_strengths(s),
        facts={
            "integration_top_pct": s.integration_top_pct,
            "choice_top_pct": s.choice_top_pct,
            "cannibalization_risk": s.cannibalization_risk,
            "competitor_count": s.competitor_count,
            "self_store_count": s.self_store_count,
            "core_hour_index": s.core_hour_index,
            "core_hours": list(s.core_hours),
            "demand_score": s.demand_score,
            "trend_change_pct": s.trend_change_pct,
            "trend_quarters": s.trend_quarters,
            "trdar_nm": s.trdar_nm,
            "trdar_contains": s.trdar_contains,
            "trdar_distance_m": s.trdar_distance_m,
            "rent_burden_cap_pct": s.rent_burden_cap_pct,
        },
    )


# ---------------------------------------------------------------------------
# 규칙 기반 서술 (LLM이 없어도 같은 결론)
# ---------------------------------------------------------------------------


def rule_diagnosis_headline(result: DiagnosisResult, *, current_man: float) -> str:
    parts: list[str] = []
    if result.expected_monthly_sales_man is None:
        parts.append(
            f"현재 월매출은 {current_man:,.0f}만원입니다. "
            "상권 매출 근거가 없어 입지 대비 적정 매출을 계산하지 못했습니다."
        )
    else:
        parts.append(
            f"이 자리의 입지·상권 조건이면 월 {result.expected_monthly_sales_man:,.0f}만원이 "
            f"기대치인데 실제는 {current_man:,.0f}만원으로, "
            f"{abs(result.gap_man or 0):,.0f}만원({abs(result.gap_pct or 0):.0f}%) "
            f"{'적습니다' if (result.gap_man or 0) < 0 else '많습니다'}. "
            f"판정은 '{result.verdict_label}'입니다."
        )
    if result.causes:
        top = result.causes[0]
        parts.append(f"가장 큰 원인은 '{top.label}'입니다. {top.finding}")
        parts.append(f"먼저 할 일: {top.action}")
        if len(result.causes) > 1:
            rest = ", ".join(f"'{c.label}'" for c in result.causes[1:3])
            parts.append(f"그다음으로 {rest}을(를) 확인하세요.")
    else:
        parts.append(
            "측정 가능한 신호 중 임계값을 넘긴 원인은 없습니다. "
            "입지 요인보다 메뉴·가격·운영 쪽을 먼저 점검하세요."
        )
    return " ".join(parts)


__all__ = [
    "ACCESS_HIGH_TOP_PCT",
    "ACCESS_MEDIUM_TOP_PCT",
    "CAUSE_LABELS",
    "CAUSE_PRIORITY",
    "COMPETITION_HIGH_RISK",
    "COMPETITION_MEDIUM_RISK",
    "CORE_HOURS_BY_ARCHETYPE",
    "CORE_HOURS_BY_PROFILE",
    "DEMAND_HIGH_SCORE",
    "DEMAND_MEDIUM_SCORE",
    "DIAGNOSE_BAND_PCT",
    "RENT_BURDEN_HIGH_MULTIPLE",
    "RENT_BURDEN_MEDIUM_MULTIPLE",
    "SEVERITY_ORDER",
    "TIME_FIT_HIGH_INDEX",
    "TIME_FIT_MEDIUM_INDEX",
    "TREND_HIGH_PCT",
    "TREND_MEDIUM_PCT",
    "VERDICT_ON_PAR",
    "VERDICT_OVER",
    "VERDICT_UNDER",
    "Cause",
    "DiagnosisResult",
    "DiagnosisSignals",
    "build_causes",
    "build_diagnosis_strengths",
    "core_hour_fit_index",
    "core_hours_for",
    "diagnose",
    "diagnose_verdict_label",
    "rule_diagnosis_headline",
    "sales_gap",
]
