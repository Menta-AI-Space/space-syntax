"""후보지 의사결정 엔진 — 순위·판정·적정 월세를 결정론적으로 계산한다.

설계 원칙: **모든 숫자·순위·임계값은 이 모듈(파이썬)에서 계산한다.**
LLM은 이미 결정된 결과를 한국어 산문으로 옮겨 적을 뿐이며
(decision_narrative.py), 점수·순위·월세 상한을 만들지 않는다.
같은 입력은 언제나 같은 순위를 낸다.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from app.models.schemas import SiteScoreRequest
from app.services.lease_feasibility import (
    LeaseInputs,
    compute_lease_metrics,
    get_archetype_defaults,
)
from app.services.sbiz_upjong import UpjongSelection, get_upjong_tree, resolve_upjong_selection
from app.services.trdar_sales_local import per_store_recent_band

logger = logging.getLogger(__name__)

MAN_WON = 10_000


# ---------------------------------------------------------------------------
# 상수 (모든 임계값은 이름을 붙여 한 곳에 모은다)
# ---------------------------------------------------------------------------

#: 입지 접근성 백분위 → 매출 보정계수.
#: multiplier = clamp(BASE + (100 - integration_top_pct)/100 * SPAN, MIN, MAX)
#: 상위 0% → 1.35, 상위 10% → 1.29, 상위 50% → 1.03(상권 평균), 하위권 → 0.75.
#: 상권 점포당 평균 매출이 "상위 50% 지점의 매출"에 해당한다는 가정에서
#: 중앙값 부근이 1.0이 되도록 BASE/SPAN을 잡았다.
LOCATION_MULT_BASE = 0.70
LOCATION_MULT_SPAN = 0.65
LOCATION_MULT_MIN = 0.75
LOCATION_MULT_MAX = 1.35

#: 임대료 부담률 상한. data/upjong_cost_defaults.json 의 업종별
#: ``rent_ratio_high`` 를 1순위로 사용한다 (음식·음료 0.15 = 매출의 15%,
#: 일상소매 0.08, 의료 0.12 …). 이 값은 소상공인 손익 관행(월세+관리비가
#: 매출의 10~15%를 넘으면 위험)에 맞춰 이미 프로젝트에 들어와 있던 상수다.
#: 파일에 값이 없을 때만 아래 폴백을 쓴다.
FALLBACK_RENT_BURDEN_CAP = 0.12

#: 적정 월세 역산 시 남겨야 할 최소 영업이익률 (매출 대비).
#: 부담률 상한으로 구한 월세가 이 마진을 못 남기면 더 보수적인 쪽을 쓴다.
MIN_OPERATING_MARGIN = 0.05

#: 적정 월세 역산에 쓰는 기본 운영 가정 (lease_feasibility 기본값과 동일).
DEFAULT_STAFF_COUNT = 1
DEFAULT_HOURS_PER_DAY = 10.0
DEFAULT_DAYS_PER_WEEK = 6

#: 판정 임계값
VERDICT_RECOMMEND_MIN = 75.0
VERDICT_CONDITIONAL_MIN = 60.0
VERDICT_AVOID_MAX = 45.0
#: 적정 월세 대비 이만큼(비율) 넘게 비싸면 조건부가 아니라 비추천.
VERDICT_RENT_GAP_AVOID_RATIO = 0.35

#: 예산 초과 후보에게 매기는 감점 (순위에서 아래로 밀기 위함).
OVER_BUDGET_PENALTY = 15.0

#: 월세 여유도 점수 매핑: rent == max_rent → 50점,
#: max_rent보다 20% 싸면 100점, 20% 비싸면 0점.
RENT_HEADROOM_GAIN = 250.0

#: priority별 가중치 (site / demand / competition / rent).
#: 사용 불가능한 항목(예: 월세 미입력)은 빼고 나머지를 재정규화한다.
PRIORITY_WEIGHTS: dict[str, dict[str, float]] = {
    "revenue": {"site": 0.40, "demand": 0.35, "competition": 0.15, "rent": 0.10},
    "stability": {"site": 0.30, "demand": 0.15, "competition": 0.30, "rent": 0.25},
    "balanced": {"site": 0.35, "demand": 0.25, "competition": 0.20, "rent": 0.20},
}

VERDICT_LABELS = {
    "recommend": "추천",
    "conditional": "조건부 추천",
    "hold": "보류",
    "avoid": "비추천",
}


@dataclass
class CandidateInput:
    id: str
    label: str
    lat: float
    lng: float
    monthly_rent_man: float | None = None
    deposit_man: float | None = None
    area_pyeong: float | None = None
    floor: int | None = None


@dataclass
class RankedCandidate:
    id: str
    label: str
    rank: int
    recommend_score: float
    verdict: str
    verdict_label: str
    expected_monthly_sales_man: float | None
    expected_sales_basis: str
    max_acceptable_rent_man: float | None
    current_rent_man: float | None
    rent_gap_man: float | None
    strengths: list[str]
    risks: list[str]
    actions: list[str]
    evidence: dict[str, Any]
    #: 최근 분기들의 변동폭에 같은 입지보정을 곱한 범위 [low, high] (만원).
    #: 점 하나로 말하면 확정치처럼 읽힌다 — 분기 출렁임이 정직한 불확실성이다.
    #: (기본값이 있어야 해서 끝에 둔다 — 기존 위치 인자 생성부를 안 깬다.)
    expected_monthly_sales_range_man: list[float] | None = None


@dataclass
class DecisionResult:
    ranked: list[RankedCandidate]
    warnings: list[str] = field(default_factory=list)
    weights_used: dict[str, float] = field(default_factory=dict)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# 1. 예상 매출
# ---------------------------------------------------------------------------


def location_multiplier(integration_top_pct: float | None) -> float:
    """접근성 백분위(낮을수록 좋음) → 상권 평균 대비 매출 보정계수."""
    if integration_top_pct is None:
        return 1.0
    raw = LOCATION_MULT_BASE + (100.0 - integration_top_pct) / 100.0 * LOCATION_MULT_SPAN
    return round(_clamp(raw, LOCATION_MULT_MIN, LOCATION_MULT_MAX), 3)


def expected_monthly_sales(
    *,
    per_store_monthly_won: int | None,
    trdar_nm: str,
    integration_top_pct: float | None,
    scope: str = "trdar",
) -> tuple[float | None, str]:
    """상권(또는 지정 상권 밖이면 행정동) 점포당 월매출 × 입지보정.

    벤치마크가 없으면 (None, 사유). ``scope=="dong"``이면 지정 상권 데이터가
    없어 행정동 평균으로 메운 값이다 — 골목상권 매출과 정밀도가 다르므로
    "상권"이라고 부르면 안 되고 문구에서도 그대로 밝힌다.
    """
    if not per_store_monthly_won or per_store_monthly_won <= 0:
        return None, "상권 매출 벤치마크 없음 — 예상 매출을 추정하지 않음"
    base_man = per_store_monthly_won / MAN_WON
    mult = location_multiplier(integration_top_pct)
    expected = base_man * mult
    unit = "행정동" if scope == "dong" else "상권"
    basis = (
        f"{trdar_nm or '해당'} {unit} 점포당 {base_man:,.0f}만원 × 입지보정 {mult:.2f}"
    )
    return round(expected, 1), basis


# ---------------------------------------------------------------------------
# 2. 감당 가능한 최대 월세
# ---------------------------------------------------------------------------


def max_acceptable_rent(
    *,
    expected_sales_man: float | None,
    archetype: str,
    area_pyeong: float | None = None,
) -> tuple[float | None, dict[str, Any]]:
    """예상 매출에서 역산한 월 임차비(월세+관리비) 상한.

    두 가지를 구해 **보수적인 쪽**을 택한다.
      1) 업종별 임대료 부담률 상한: expected_sales × rent_ratio_high
      2) 손익 역산: 공헌이익 - 인건비 - 기타고정비 - 최소영업이익 이 남기는 월세
    """
    if not expected_sales_man or expected_sales_man <= 0:
        return None, {}

    defaults = get_archetype_defaults(archetype)
    burden_cap = float(defaults.get("rent_ratio_high") or FALLBACK_RENT_BURDEN_CAP)
    burden_based = expected_sales_man * burden_cap

    # lease_feasibility 의 원가 구조를 그대로 재사용한다 (월세 0으로 조회해
    # 인건비·기타고정비·변동비율만 뽑아낸 뒤 월세에 대해 역산).
    probe = compute_lease_metrics(
        LeaseInputs(
            monthly_rent_man=0,
            area_pyeong=area_pyeong,
            staff_count=DEFAULT_STAFF_COUNT,
            hours_per_day=DEFAULT_HOURS_PER_DAY,
            days_per_week=DEFAULT_DAYS_PER_WEEK,
        ),
        archetype,
        benchmark_monthly_man=expected_sales_man,
    )
    contribution = 1.0 - probe.variable_cost_ratio
    break_even_based = (
        expected_sales_man * contribution
        - probe.labor_cost_man
        - probe.misc_fixed_man
        - expected_sales_man * MIN_OPERATING_MARGIN
    )

    max_rent = max(0.0, min(burden_based, break_even_based))
    detail = {
        "burden_cap_ratio": burden_cap,
        "burden_based_man": round(burden_based, 1),
        "break_even_based_man": round(break_even_based, 1),
        "labor_cost_man": probe.labor_cost_man,
        "misc_fixed_man": probe.misc_fixed_man,
        "variable_cost_ratio": probe.variable_cost_ratio,
        "binding_constraint": (
            "부담률 상한" if burden_based <= break_even_based else "손익분기 역산"
        ),
    }
    return round(max_rent, 1), detail


# ---------------------------------------------------------------------------
# 3. 추천 점수
# ---------------------------------------------------------------------------


def rent_affordability_score(
    current_rent_man: float | None, max_rent_man: float | None
) -> float | None:
    """월세 여유도 0-100. 적정 상한과 같으면 50점.

    None은 **'월세를 안 적었다'는 뜻으로만** 쓴다 — recommend_score가 None인 항목을
    빼고 남은 것만으로 재정규화하기 때문이다("월세 미입력이 점수를 깎지 않게").

    상한이 0이라는 건 '이 매출로는 월세를 한 푼도 낼 수 없다'는 최악의 정보인데,
    그걸 None으로 돌려주면 정보 없음과 똑같이 취급돼 감점을 피한다. 실제로
    연남동(상한 0·월세 300만원)이 신촌(상한 192만원·월세 250만원, 예상매출은
    두 배)을 제치고 1위로 올라왔다. 그래서 0점(최악)을 준다.

    **상한 0은 월세를 적었는지와 무관한 '자리의 성질'이다.** 상한을 먼저 보는
    이유가 이것이다. 상한 0인데 월세를 입력하지 않았다는 이유로 None을 주면,
    똑같이 월세를 낼 수 없는 두 자리가 입력칸 하나 때문에 갈린다 — 실제로
    연남동(상한 0·월세 미입력) 64.1점이 망원동(상한 0·월세 0 입력) 50.4점을
    이겼고, 신촌(상한 221만원·월세 250만원) 53.0점보다도 위였다. 예상 매출이
    인건비·고정비도 못 덮는다는 사실은 월세를 적든 안 적든 그대로다.
    """
    if max_rent_man is not None and max_rent_man <= 0:
        return 0.0
    if current_rent_man is None or max_rent_man is None:
        return None
    headroom = (max_rent_man - current_rent_man) / max_rent_man
    return round(_clamp(50.0 + headroom * RENT_HEADROOM_GAIN, 0.0, 100.0), 1)


def recommend_score(
    *,
    site_score_total: float | None,
    demand_score: float | None,
    cannibalization_risk: float | None,
    rent_score: float | None,
    priority: str,
) -> tuple[float, dict[str, float]]:
    weights = PRIORITY_WEIGHTS.get(priority, PRIORITY_WEIGHTS["balanced"])
    components: dict[str, float | None] = {
        "site": site_score_total,
        "demand": demand_score,
        "competition": (
            round(100.0 - cannibalization_risk, 1)
            if cannibalization_risk is not None
            else None
        ),
        "rent": rent_score,
    }
    weighted = 0.0
    total_w = 0.0
    used: dict[str, float] = {}
    for key, value in components.items():
        if value is None:
            continue
        w = weights[key]
        weighted += value * w
        total_w += w
        used[key] = w
    if total_w <= 0:
        return 0.0, {}
    # 사용 가능한 항목만으로 재정규화 — 월세 미입력이 점수를 깎지 않게.
    used = {k: round(v / total_w, 3) for k, v in used.items()}
    return round(weighted / total_w, 1), used


# ---------------------------------------------------------------------------
# 4. 판정
# ---------------------------------------------------------------------------


def decide_verdict(
    *,
    score: float,
    rent_gap_man: float | None,
    max_rent_man: float | None,
    over_budget: bool,
) -> str:
    # 상한 0 = '예상 매출로는 인건비·고정비를 빼면 월세를 한 푼도 못 낸다'.
    # 이 자리는 어떤 월세에서도 성립하지 않으므로 그냥 '추천'이 될 수 없고,
    # 실제로 월세를 내고 있다면 초과폭이 상한 대비 무한대라 비추천이다.
    # (비율 검사 gap > max_rent × 0.35 는 max_rent > 0 을 요구해서 이 경우를
    #  통째로 놓쳤다 — 가장 나쁜 자리만 관문을 빠져나가고 있었다.)
    cannot_carry_rent = max_rent_man is not None and max_rent_man <= 0
    gap_is_huge = (
        rent_gap_man is not None
        and max_rent_man
        and max_rent_man > 0
        and rent_gap_man > max_rent_man * VERDICT_RENT_GAP_AVOID_RATIO
    )
    if cannot_carry_rent and rent_gap_man is not None and rent_gap_man > 0:
        return "avoid"
    if score < VERDICT_AVOID_MAX or gap_is_huge:
        return "avoid"
    if cannot_carry_rent:
        # 월세를 아직 안 적었거나 0원인 경우 — 추천까지 갈 수는 없다.
        return "conditional" if score >= VERDICT_CONDITIONAL_MIN else "hold"
    rent_over = over_budget or (rent_gap_man is not None and rent_gap_man > 0)
    if score >= VERDICT_RECOMMEND_MIN and not rent_over:
        return "recommend"
    if score >= VERDICT_CONDITIONAL_MIN and rent_over:
        return "conditional"
    if score >= VERDICT_RECOMMEND_MIN and rent_over:
        return "conditional"
    return "hold"


# ---------------------------------------------------------------------------
# 5. 근거 문장 (LLM이 아니라 f-string으로 — 숫자가 곧 문장이다)
# ---------------------------------------------------------------------------


def _fmt_man(v: float) -> str:
    return f"{v:,.0f}만원"


#: 숫자를 한국어로 읽었을 때 받침이 있는지. 영(ㅇ)·일(ㄹ)·삼(ㅁ)·육(ㄱ)·
#: 칠(ㄹ)·팔(ㄹ)은 받침이 있고 이·사·오·구는 없다. 후보 이름이 "연남동 481-16"
#: 처럼 지번으로 끝나는 일이 흔해서 숫자까지 봐야 조사가 맞는다.
_DIGIT_HAS_FINAL_CONSONANT = {
    "0": True, "1": True, "3": True, "6": True, "7": True, "8": True,
    "2": False, "4": False, "5": False, "9": False,
}


def _has_final_consonant(word: str) -> bool | None:
    """마지막 한글 음절(없으면 마지막 숫자)의 받침 유무. 판단 불가면 None."""
    for ch in reversed((word or "").strip()):
        if "가" <= ch <= "힣":
            return (ord(ch) - 0xAC00) % 28 != 0
        if ch.isdigit():
            return _DIGIT_HAS_FINAL_CONSONANT[ch]
    return None


def josa(word: str, with_final: str, without_final: str) -> str:
    """앞말 받침에 맞는 조사를 고른다.

    "'신촌'을(를) 검토하세요"처럼 병기하면 읽기 나쁘고, 아무거나 붙이면
    "'연남동'를"처럼 틀린다. 정말 판단할 수 없을 때만 병기로 물러선다.
    """
    final = _has_final_consonant(word)
    if final is None:
        return f"{with_final}({without_final})"
    return with_final if final else without_final


def _fmt_top_pct(v: float) -> str:
    """접근성 백분위 표기.

    ``f"{v:.0f}%"``는 0.2%를 **"상위 0%"**로 적는다 — '상위 0%'는 아무것도
    가리키지 않는 말이라 뜻이 사라진다. 동네 단위 후보 탐색에서는 뽑히는 지점이
    대부분 1% 미만이라 다섯 장이 전부 "상위 0%"로 똑같이 보였다.
    """
    if v < 1:
        return "상위 1% 이내"
    return f"상위 {v:.0f}%"


def build_strengths(ev: dict[str, Any]) -> list[str]:
    out: list[str] = []
    integ = ev.get("integration_top_pct")
    if integ is not None and integ <= 40:
        out.append(f"찾아오기 쉬운 길 · 분석 반경 내 {_fmt_top_pct(integ)}")
    choice = ev.get("choice_top_pct")
    if choice is not None and choice <= 40:
        out.append(f"지나가는 사람이 많은 길 · {_fmt_top_pct(choice)}")
    sales = ev.get("expected_monthly_sales_man")
    bench = ev.get("per_store_monthly_man")
    if sales and bench:
        # scope=="dong"이면 지정 상권이 아니라 행정동 평균으로 메운 값이다.
        # "상권 평균보다 높음"이라고 쓰면 실제보다 더 정밀한 근거인 것처럼
        # 읽혀서 화면 문구와 실제 계산이 어긋난다.
        unit = "행정동" if ev.get("sales_benchmark_scope") == "dong" else "상권"
        diff_pct = (sales / bench - 1.0) * 100.0
        if diff_pct >= 3:
            out.append(f"예상 월매출 {_fmt_man(sales)} · {unit} 평균보다 {diff_pct:.0f}% 높음")
        else:
            out.append(f"예상 월매출 {_fmt_man(sales)} ({unit} 점포당 평균 {_fmt_man(bench)})")
    station = ev.get("station_distance_m")
    if station is not None and station <= 400:
        out.append(f"{ev.get('station_name') or '지하철역'} 도보 {station:.0f}m")
    footfall = ev.get("footfall_total")
    if footfall:
        # dong_footfall.total은 **분기 합계**다. 기간을 안 적으면 294만명이
        # 지금 그 동네에 있는 사람 수처럼 읽힌다.
        # quarter는 이미 "2024년 4분기" 꼴이다 (area_insights가 yyq를 풀어 준다).
        period = ev.get("footfall_quarter") or "최근 분기"
        out.append(
            f"{ev.get('footfall_dong') or '행정동'} {period} 유동인구 {footfall:,}명"
        )
    pop = ev.get("population")
    if pop:
        out.append(f"반경 {ev.get('radius_m')}m 배후인구 {pop:,}명")
    risk = ev.get("cannibalization_risk")
    count = ev.get("competitor_count")
    if risk is not None and risk < 45:
        out.append(f"동일업종 경쟁 {count}곳 · 잠식위험 {risk:.0f}/100로 낮은 편")
    gap = ev.get("rent_gap_man")
    max_rent = ev.get("max_acceptable_rent_man")
    if gap is not None and gap < 0 and max_rent:
        out.append(f"월세가 적정 상한({_fmt_man(max_rent)})보다 {_fmt_man(-gap)} 저렴")
    if not out:
        total = ev.get("site_total_score")
        out.append(f"입지 종합 {total:.0f}점 — 두드러진 강점은 없지만 평균권")
    return out[:3]


def build_risks(ev: dict[str, Any]) -> list[str]:
    out: list[str] = []
    gap = ev.get("rent_gap_man")
    max_rent = ev.get("max_acceptable_rent_man")
    current_rent = ev.get("current_rent_man")
    # `and max_rent`가 0을 걸러내는 바람에 **가장 나쁜 경우**(월세를 한 푼도 낼 수
    # 없는데 월세를 내고 있는 자리)만 위험 목록에서 빠졌다. 연남동은 상한 0에
    # 월세 300만원인데 월세 위험이 하나도 안 떴고, 58만원 초과인 신촌만 떴다.
    #
    # 그 뒤 `and current_rent`가 같은 실수를 한 겹 더 하고 있었다: 월세를 아직
    # 안 적었거나 0으로 적으면 falsy라서, 상한 0(=이 자리는 월세를 낼 수 없다)
    # 이라는 사실 자체가 위험 목록에서 사라졌다. 상한 0은 월세 입력 여부와
    # 무관한 자리의 성질이므로 언제나 알려야 한다.
    if max_rent is not None and max_rent <= 0:
        if current_rent:
            out.append(
                f"예상 매출로는 인건비·고정비를 빼면 월세 낼 여력이 없는데 "
                f"월세가 {_fmt_man(current_rent)}"
            )
        else:
            out.append(
                "예상 매출로는 인건비·고정비를 빼면 월세 낼 여력이 남지 않음"
            )
    elif gap is not None and gap > 0 and max_rent:
        out.append(
            f"월세 {_fmt_man(current_rent)} — 적정 상한 "
            f"{_fmt_man(max_rent)}보다 {_fmt_man(gap)} 비쌈"
        )
    if ev.get("over_budget"):
        out.append(
            f"입력하신 예산 상한 {_fmt_man(ev['budget_max_rent_man'])}을 초과"
        )
    # scope=="dong"은 지정 상권 데이터가 없어 행정동 평균으로 메운 값이라는
    # 뜻이다. 예상 매출·적정 월세·순위·판정이 전부 이 값에서 나오는데, 이
    # 사실을 알리지 않으면 화면은 다른 후보와 똑같이 "상권 벤치마크 기반"인
    # 것처럼 보인다 — 정밀도가 낮다는 걸 순위표 자체에서 밀어내면 안 되므로
    # 예산 초과 다음, 경쟁 관련 위험보다 앞에 둔다.
    if ev.get("sales_benchmark_scope") == "dong" and ev.get("expected_monthly_sales_man"):
        out.append(
            "예상 매출·적정 월세는 지정 상권이 아니라 행정동 평균으로 추정한 값 "
            "— 실제 상권 매출과 정밀도가 다를 수 있음"
        )
    risk = ev.get("cannibalization_risk")
    count = ev.get("competitor_count")
    if risk is not None and risk >= 45:
        out.append(f"도보 5분 내 동일업종 {count}곳 · 잠식위험 {risk:.0f}/100")
    self_count = ev.get("self_store_count")
    if self_count:
        out.append(f"같은 브랜드 점포가 도보권에 {self_count}곳 — 자기잠식 우려")
    integ = ev.get("integration_top_pct")
    if integ is not None and integ > 60:
        out.append(f"찾아오기 어려운 안쪽 길 · 상위 {integ:.0f}%에 그침")
    if ev.get("expected_monthly_sales_man") is None:
        out.append("상권 매출 데이터가 없어 예상 매출·적정 월세를 계산하지 못함")
    if ev.get("current_rent_man") is None:
        out.append("월세 미입력 — 수익성 판단은 매물 조건 확인 후 재계산 필요")
    if not out:
        out.append("치명적 위험 요인은 확인되지 않음 — 계약 조건만 점검 필요")
    return out[:3]


def build_actions(ev: dict[str, Any], verdict: str) -> list[str]:
    out: list[str] = []
    max_rent = ev.get("max_acceptable_rent_man")
    gap = ev.get("rent_gap_man")
    if max_rent is not None and max_rent <= 0:
        # 상한 0에서는 `if max_rent`가 전부 falsy로 떨어져 월세에 관한 권고가
        # 한 줄도 안 나왔다. 정작 사용자가 알아야 할 게 이 경우다 — 낮출 월세가
        # 있는 게 아니라 조건 자체를 바꿔야 한다.
        out.append(
            "월세를 깎아서 될 조건이 아님 — 매출이 더 나오는 자리를 찾거나 "
            "면적·인건비를 줄여 고정비를 낮춰야 함"
        )
    elif max_rent and gap is not None and gap > 0:
        out.append(f"월세 {max_rent:,.0f}만원 이하로 협상 (현재 대비 {gap:,.0f}만원 인하)")
    elif max_rent and ev.get("current_rent_man") is None:
        out.append(f"월세 {max_rent:,.0f}만원 이하 매물 위주로 탐색")
    elif max_rent:
        # 임대료가 예산 안이라는 사실이 곧 "계약해도 된다"는 뜻은 아니다.
        # 경쟁·수요 때문에 판정이 보류인데 여기서 "확정 진행"이라고 하면
        # 판정과 정반대 조언이 나란히 뜬다.
        if verdict == "recommend":
            out.append(f"월세 {max_rent:,.0f}만원까지 감당 가능 — 조건 확정 진행")
        else:
            out.append(f"월세는 {max_rent:,.0f}만원까지 감당 가능 — 임대료는 걸림돌이 아님")
    floor = ev.get("floor")
    if floor is not None and floor != 1:
        out.append("1층 매물 우선 검토 (지상층은 유입 손실이 큼)")
    risk = ev.get("cannibalization_risk")
    if risk is not None and risk >= 45:
        out.append("최근접 경쟁점 3곳 현장 방문 후 회전율·객단가 확인")
    if verdict == "avoid":
        out.append("다른 후보지를 우선 검토 권장")
    if ev.get("expected_monthly_sales_man") is None:
        out.append("인근 동일업종 실매출 청취 후 재분석")
    return out[:3]


# ---------------------------------------------------------------------------
# 5-2. 순위를 가른 요인
# ---------------------------------------------------------------------------
#
# '어디가 좋을지' 후보 5곳은 같은 동네에서 뽑히기 때문에 같은 상권 벤치마크를
# 쓰고, 접근성 백분위도 모두 상위권이라 location_multiplier가 같은 구간에
# 걸린다. 그 결과 **예상 월매출이 다섯 장 모두 똑같은 값**으로 찍히는데
# 점수는 67.5 / 67.2 / 66.7 / 66.4 / 65.2로 다르다. 화면만 보면 "매출도 같고
# 강점 문구도 같은데 왜 순위가 다르지?"에 답할 방법이 없다.
#
# 추천점수는 항목 점수의 가중평균이므로, 어떤 후보가 다른 후보들보다 높거나
# 낮은 이유는 항목별로 정확히 쪼갤 수 있다. 그 분해를 그대로 보여 준다.

#: 후보 카드에 보여 줄 '순위를 가른 요인' 최대 개수.
RANK_DRIVER_MAX = 3

#: 추천점수에 이만큼(점) 미만으로 기여한 항목은 순위를 갈랐다고 하지 않는다.
#: 0.3점짜리 차이를 "이것 때문에 순위가 갈렸다"고 쓰면 없는 신호를 만들어 낸다.
RANK_DRIVER_MIN_WEIGHTED_DELTA = 0.3

COMPONENT_LABELS: dict[str, str] = {
    "site": "입지 접근성",
    "demand": "배후 수요",
    "competition": "경쟁 여유",
    "rent": "월세 여유",
}


def _driver_detail(key: str, facts: dict[str, Any]) -> str | None:
    """항목 점수 뒤에 있는 '사람이 읽는 숫자'. 없으면 None."""
    if key == "site":
        integ = facts.get("integration_top_pct")
        return f"찾아오기 쉬움 {_fmt_top_pct(integ)}" if integ is not None else None
    if key == "demand":
        pop = facts.get("population")
        if not pop:
            return None
        return f"반경 {facts.get('radius_m')}m 배후인구 {pop:,}명"
    if key == "competition":
        risk = facts.get("cannibalization_risk")
        if risk is None:
            return None
        count = facts.get("competitor_count")
        head = f"동일업종 {count}곳 · " if count is not None else ""
        return f"{head}잠식위험 {risk:.0f}/100"
    if key == "rent":
        max_rent = facts.get("max_acceptable_rent_man")
        if max_rent is None:
            return None
        if max_rent <= 0:
            return "월세 낼 여력 없음"
        return f"감당 가능 월세 {_fmt_man(max_rent)}"
    return None


def compute_rank_drivers(rows: list[dict[str, Any]]) -> None:
    """각 후보의 evidence에 ``rank_drivers``를 채운다 (제자리 수정).

    후보가 1곳뿐이면 비교 대상이 없으므로 빈 목록을 넣는다.

    항목별 기여도 = 그 후보에 실제로 쓰인 가중치 × (그 후보 항목점수 −
    같은 항목을 가진 다른 후보들의 평균). 월세처럼 일부 후보에만 있는 항목은
    **그 항목을 가진 후보끼리만** 평균을 낸다 — 없는 값을 0으로 치면 월세를
    안 적은 후보가 자동으로 유리해지기 때문이다.
    """
    if len(rows) < 2:
        for row in rows:
            row["evidence"]["rank_drivers"] = []
        return

    comps: list[dict[str, float | None]] = [
        (r.get("evidence", {}).get("score_components") or {}) for r in rows
    ]
    field_avg: dict[str, float] = {}
    for key in COMPONENT_LABELS:
        values = [c.get(key) for c in comps]
        present = [v for v in values if isinstance(v, (int, float))]
        if len(present) >= 2:
            field_avg[key] = sum(present) / len(present)

    for row, comp in zip(rows, comps):
        weights = row.get("_weights", {}) or {}
        facts = row.get("_facts", {}) or {}
        drivers: list[dict[str, Any]] = []
        for key, label in COMPONENT_LABELS.items():
            value = comp.get(key)
            if not isinstance(value, (int, float)):
                continue
            weight = weights.get(key)
            if not weight:
                continue
            detail = _driver_detail(key, facts)
            avg = field_avg.get(key)

            # (a) 다른 후보 평균과의 차이. 항목을 다 같이 가지고 있을 때 가장
            #     읽기 쉬운 설명이다.
            weighted = weight * (value - avg) if avg is not None else 0.0
            delta = value - avg if avg is not None else None

            # (b) **이 항목이 나에게만 채점된 경우**, 이 항목을 빼면 내 점수가
            #     어떻게 되는지.
            #     (a)로는 설명되지 않는 큰 경우가 있다: 어떤 후보는 월세를 적어
            #     월세 항목이 채점되고, 어떤 후보는 미입력이라 그 항목이 빠진 채
            #     가중치가 재정규화된다. 그러면 월세 점수를 가진 후보끼리는 값이
            #     모두 같아(예: 전부 0점) 평균 차이가 0이 되어, 정작 순위를 12점
            #     가른 요인이 "차이 없음"으로 나온다(라이브 연남동).
            #     이 계산은 **채점 여부가 후보마다 다른 항목에만** 쓴다. 모든
            #     후보가 가진 항목에까지 쓰면 자기 평균에서 벗어난 항목이 전부
            #     크게 잡혀(0점짜리 경쟁 항목 등) 진짜 차이를 덮어 버린다.
            scored_by_others = sum(
                1 for c in comps if isinstance(c.get(key), (int, float))
            )
            only_scored_here = scored_by_others < len(rows)

            self_impact = 0.0
            if only_scored_here:
                others = {k: v for k, v in weights.items() if k != key}
                other_total = sum(others.values())
                total = sum(weights.values())
                if other_total > 0 and total > 0:
                    without = (
                        sum((comp.get(k) or 0.0) * w for k, w in others.items())
                        / other_total
                    )
                    with_all = (
                        sum((comp.get(k) or 0.0) * w for k, w in weights.items())
                        / total
                    )
                    self_impact = with_all - without

            # 더 크게 설명하는 쪽을 쓴다.
            use_self = abs(self_impact) > abs(weighted)
            impact = self_impact if use_self else weighted
            if abs(impact) < RANK_DRIVER_MIN_WEIGHTED_DELTA:
                continue

            direction = "up" if impact > 0 else "down"
            if use_self:
                verb = "올림" if impact > 0 else "깎음"
                text = f"{label}{josa(label, '이', '가')} 점수를 {abs(impact):.0f}점 {verb}"
            else:
                comparison = "높음" if impact > 0 else "낮음"
                text = (
                    f"{label}{josa(label, '이', '가')} 다른 후보 평균보다 "
                    f"{abs(delta or 0):.0f}점 {comparison}"
                )
            if detail:
                text += f" ({detail})"
            drivers.append(
                {
                    "key": key,
                    "label": label,
                    "score": round(float(value), 1),
                    "field_avg": round(avg, 1) if avg is not None else None,
                    "delta": round(delta, 1) if delta is not None else None,
                    "impact": round(impact, 1),
                    "direction": direction,
                    "detail": detail,
                    "text": text,
                }
            )

        if facts.get("over_budget"):
            budget = facts.get("budget_max_rent_man")
            drivers.append(
                {
                    "key": "budget",
                    "label": "예산 초과",
                    "score": None,
                    "field_avg": None,
                    "delta": None,
                    "impact": -OVER_BUDGET_PENALTY,
                    "direction": "down",
                    "detail": (
                        f"예산 상한 {_fmt_man(budget)}" if budget is not None else None
                    ),
                    "text": (
                        f"입력한 예산 상한을 넘겨 {OVER_BUDGET_PENALTY:.0f}점 감점"
                    ),
                }
            )

        drivers.sort(key=lambda d: -abs(d["impact"]))
        if not drivers:
            # 정말로 우열이 없을 때 "이래서 1위"라고 지어내지 않는다.
            drivers = [
                {
                    "key": "tie",
                    "label": "차이 없음",
                    "score": None,
                    "field_avg": None,
                    "delta": None,
                    "impact": 0.0,
                    "direction": "flat",
                    "detail": None,
                    "text": "다른 후보와 항목별 차이가 거의 없음 — 순위는 소수점 차이",
                }
            ]
        row["evidence"]["rank_drivers"] = drivers[:RANK_DRIVER_MAX]


# ---------------------------------------------------------------------------
# 6. 후보지 1곳 평가 (I/O 포함)
# ---------------------------------------------------------------------------


async def evaluate_candidate(
    cand: CandidateInput,
    *,
    upjong: UpjongSelection,
    inds_lcls_cd: str | None,
    inds_mcls_cd: str | None,
    inds_scls_cd: str | None,
    radius_m: int,
    brand: str | None,
    priority: str,
    budget_max_rent_man: float | None,
) -> tuple[dict[str, Any], list[str]]:
    """한 후보지의 원자료를 모으고 결정론적 지표를 계산한다."""
    # 지연 import — 라우터 ↔ 서비스 순환 import 방지.
    from app.routers.dashboard import _load_context
    from app.routers.site_score import site_score as site_score_endpoint
    from app.services.area_insights import fetch_footfall_breakdown
    from app.services.cannibalization import compute_cannibalization
    from app.services.isochrone import competition_fetch_radius_m

    warnings: list[str] = []
    prefix = f"[{cand.id}] "

    score_task = site_score_endpoint(
        SiteScoreRequest(
            lat=cand.lat,
            lng=cand.lng,
            radius_m=radius_m,
            inds_lcls_cd=inds_lcls_cd,
            inds_mcls_cd=inds_mcls_cd,
            inds_scls_cd=inds_scls_cd,
            brand=brand,
            include_ai_explanation=False,
        )
    )
    fetch_radius = competition_fetch_radius_m(radius_m, "walk", 5)
    ctx_task = _load_context(cand.lat, cand.lng, fetch_radius, upjong, mode="walk")
    footfall_task = run_in_threadpool(fetch_footfall_breakdown, cand.lat, cand.lng)

    score_res, ctx_res, footfall_res = await asyncio.gather(
        score_task, ctx_task, footfall_task, return_exceptions=True
    )

    if isinstance(score_res, BaseException):
        detail = (
            score_res.detail
            if isinstance(score_res, HTTPException)
            else (str(score_res) or type(score_res).__name__)
        )
        raise HTTPException(
            status_code=502, detail=f"{cand.label} 입지 분석 실패: {detail}"
        )
    score = score_res
    warnings.extend(prefix + w for w in score.warnings)

    cann = None
    if isinstance(ctx_res, BaseException):
        warnings.append(prefix + f"경쟁 잠식 분석 생략: {ctx_res or type(ctx_res).__name__}")
    else:
        segments, G, stores, _, _, ctx_warnings = ctx_res
        warnings.extend(prefix + w for w in ctx_warnings)
        try:
            cann = await run_in_threadpool(
                compute_cannibalization,
                G=G,
                segments=segments,
                lat=cand.lat,
                lng=cand.lng,
                inds_mcls_cd=upjong.inds_mcls_cd,
                inds_scls_cd=upjong.inds_scls_cd,
                stores=stores,
                radius_m=fetch_radius,
                mode="walk",
                minutes=5,
                brand=brand,
            )
        except Exception as exc:
            warnings.append(prefix + f"경쟁 잠식 분석 실패: {exc}")

    footfall = None if isinstance(footfall_res, BaseException) else footfall_res

    # ---- 결정론적 계산 ------------------------------------------------
    bench = score.sales_benchmark
    integ = score.segment.integration_top_pct
    bench_scope = bench.scope if bench else None
    expected, basis = expected_monthly_sales(
        per_store_monthly_won=bench.per_store_monthly_won if bench else None,
        trdar_nm=bench.trdar_nm if bench else "",
        integration_top_pct=integ,
        scope=bench_scope or "trdar",
    )
    # 예측 범위 — 이 상권의 최근 분기 점포당 변동폭 × 같은 입지보정.
    # 행정동 대체값(scope=dong)에는 안 만든다: 이미 정밀도 경고가 붙는 값에
    # 범위까지 얹으면 정밀해 보이는 역효과가 난다.
    expected_range: list[float] | None = None
    if expected is not None and bench and bench_scope != "dong" and score.industry_profile_id:
        try:
            band = per_store_recent_band(bench.trdar_cd, score.industry_profile_id)
        except Exception:
            band = None
        if band is not None:
            band_mult = location_multiplier(integ)
            candidate_range = [
                round(band.low_won / MAN_WON * band_mult, 1),
                round(band.high_won / MAN_WON * band_mult, 1),
            ]
            # 2026-11-06: 라이브에서 「예상 월매출 405만원 (113만원~289만원)」이 나왔다 —
            # 점추정(상권 벤치마크)과 범위(분기별 점포당, 보조 업종 코드 포함)가 다른
            # 계열이라 범위가 점을 담지 못한다(사용자 테스트 §9-2). 원인 질의를 맞추기
            # 전까지, 점을 담지 않는 범위는 내보내지 않는다 — 모순된 숫자가 더 해롭다.
            if candidate_range[0] <= expected <= candidate_range[1]:
                expected_range = candidate_range
                basis += f" · 최근 {band.quarters}분기 변동 반영"
    if expected is None:
        warnings.append(prefix + "상권 매출 벤치마크가 없어 예상 매출을 계산하지 못했습니다.")
    elif bench_scope == "dong":
        # 지정 상권(golmok_sales trdar) 밖이라 행정동 평균으로 메운 값이다.
        # 이 값 하나에서 예상 매출·적정 월세·순위·판정이 전부 나오므로,
        # 정밀도가 낮다는 걸 조용히 삼키면 화면은 다른 후보와 똑같이
        # 확정치처럼 보인다.
        warnings.append(
            prefix
            + "상권 매출 벤치마크가 지정 상권 밖이라 행정동 평균으로 대체됨 "
            "— 예상 매출·적정 월세 정밀도가 낮음"
        )

    max_rent, rent_detail = max_acceptable_rent(
        expected_sales_man=expected,
        archetype=upjong.archetype,
        area_pyeong=cand.area_pyeong,
    )
    rent_gap = (
        round(cand.monthly_rent_man - max_rent, 1)
        if cand.monthly_rent_man is not None and max_rent is not None
        else None
    )
    over_budget = (
        budget_max_rent_man is not None
        and cand.monthly_rent_man is not None
        and cand.monthly_rent_man > budget_max_rent_man
    )

    subscores = {s.key: s for s in score.subscores}
    demand_sub = subscores.get("demand")
    rent_score = rent_affordability_score(cand.monthly_rent_man, max_rent)
    raw_score, weights_used = recommend_score(
        site_score_total=score.total_score,
        demand_score=demand_sub.score if demand_sub else None,
        cannibalization_risk=cann.risk_score if cann else None,
        rent_score=rent_score,
        priority=priority,
    )
    final_score = round(max(0.0, raw_score - (OVER_BUDGET_PENALTY if over_budget else 0.0)), 1)
    verdict = decide_verdict(
        score=final_score,
        rent_gap_man=rent_gap,
        max_rent_man=max_rent,
        over_budget=bool(over_budget),
    )

    facts: dict[str, Any] = {
        "site_total_score": score.total_score,
        "integration_top_pct": integ,
        "choice_top_pct": score.segment.choice_top_pct,
        "connectivity_top_pct": score.segment.connectivity_top_pct,
        "station_name": score.transit.station_name if score.transit else None,
        "station_distance_m": score.transit.distance_m if score.transit else None,
        "population": score.demand.population if score.demand else None,
        "radius_m": radius_m,
        "expected_monthly_sales_man": expected,
        "expected_monthly_sales_range_man": expected_range,
        "per_store_monthly_man": (
            round(bench.per_store_monthly_won / MAN_WON, 1)
            if bench and bench.per_store_monthly_won
            else None
        ),
        "sales_benchmark_scope": bench_scope,
        "cannibalization_risk": cann.risk_score if cann else None,
        "competitor_count": cann.total_count if cann else None,
        "self_store_count": cann.self_total_count if cann else None,
        "footfall_total": footfall.total if footfall else None,
        "footfall_dong": footfall.adm_nm if footfall else None,
        "footfall_quarter": footfall.quarter if footfall else None,
        "max_acceptable_rent_man": max_rent,
        "current_rent_man": cand.monthly_rent_man,
        "rent_gap_man": rent_gap,
        "over_budget": bool(over_budget),
        "budget_max_rent_man": budget_max_rent_man,
        "floor": cand.floor,
    }

    evidence = {
        "site_score": {
            "total_score": score.total_score,
            "grade": score.grade,
            "verdict_one_liner": score.verdict_one_liner,
            "business_label": score.business_label,
            "subscores": [
                {
                    "key": s.key,
                    "label": s.label,
                    "score": s.score,
                    "detail": s.detail,
                    "rank_top_pct": s.rank_top_pct,
                    "display": s.display,
                }
                for s in score.subscores
            ],
            "segment": {
                "integration_top_pct": score.segment.integration_top_pct,
                "choice_top_pct": score.segment.choice_top_pct,
                "mean_depth_top_pct": score.segment.mean_depth_top_pct,
                "connectivity_top_pct": score.segment.connectivity_top_pct,
            },
            "transit": (
                {
                    "station_name": score.transit.station_name,
                    "distance_m": score.transit.distance_m,
                }
                if score.transit
                else None
            ),
            "demand": (
                {
                    "adm_name": score.demand.adm_name,
                    "population": score.demand.population,
                    "workers": score.demand.workers,
                    "households": score.demand.households,
                }
                if score.demand
                else None
            ),
            "competition": {
                "same_category_count": score.competition.same_category_count,
                "total_store_count": score.competition.total_store_count,
                "density_per_km2": score.competition.density_per_km2,
                "intensity_label": score.competition.intensity_label,
            },
        },
        "sales_benchmark": (
            {
                "trdar_nm": bench.trdar_nm,
                "trdar_se_nm": bench.trdar_se_nm,
                "quarter": bench.quarter,
                "per_store_monthly_won": bench.per_store_monthly_won,
                "store_count": bench.store_count,
                "citywide_median_per_store_won": bench.citywide_median_per_store_won,
                "location_multiplier": location_multiplier(integ),
                # "trdar"(지정 상권 안) 또는 "dong"(상권 밖이라 행정동 평균으로
                # 대체) — 화면에서 정밀도를 구분해 보여줘야 한다.
                "scope": bench.scope,
            }
            if bench
            else None
        ),
        "cannibalization": (
            {
                "risk_score": cann.risk_score,
                "risk_label": cann.risk_label,
                "total_count": cann.total_count,
                "self_total_count": cann.self_total_count,
                "overlap_pct": cann.overlap_pct,
                "overlap_with": cann.overlap_with,
                "detail": cann.detail,
                "nearest": [
                    {"name": c.name, "distance_m": c.distance_m}
                    for c in cann.competitors[:5]
                ],
            }
            if cann
            else None
        ),
        "area_insights": (
            {
                "footfall_dong": footfall.adm_nm,
                "footfall_quarter": footfall.quarter,
                "footfall_total": footfall.total,
                "footfall_ages": [{"band": b, "value": v} for b, v in footfall.ages],
            }
            if footfall
            else None
        ),
        "rent_model": rent_detail,
        "score_components": {
            "site": score.total_score,
            "demand": demand_sub.score if demand_sub else None,
            "competition": (
                round(100.0 - cann.risk_score, 1) if cann else None
            ),
            "rent": rent_score,
            "weights_used": weights_used,
            "raw_score": raw_score,
            "over_budget_penalty": OVER_BUDGET_PENALTY if over_budget else 0.0,
        },
    }

    return (
        {
            "id": cand.id,
            "label": cand.label,
            "recommend_score": final_score,
            "verdict": verdict,
            "verdict_label": VERDICT_LABELS[verdict],
            "expected_monthly_sales_man": expected,
            "expected_monthly_sales_range_man": expected_range,
            "expected_sales_basis": basis,
            "max_acceptable_rent_man": max_rent,
            "current_rent_man": cand.monthly_rent_man,
            "rent_gap_man": rent_gap,
            "strengths": build_strengths(facts),
            "risks": build_risks(facts),
            "actions": build_actions(facts, verdict),
            "evidence": evidence,
            "_weights": weights_used,
            # compute_rank_drivers가 항목 점수 뒤의 실제 숫자(접근성 백분위·
            # 경쟁 점포 수 등)를 인용할 때 쓴다. 응답에는 나가지 않는다.
            "_facts": facts,
        },
        warnings,
    )


# ---------------------------------------------------------------------------
# 7. 오케스트레이션
# ---------------------------------------------------------------------------


#: 후보지 지하철역 사전 조회 타임아웃 (site-score 내부의 2.5s보다 넉넉하게).
TRANSIT_PREWARM_TIMEOUT_S = 10.0

#: 동시에 분석할 후보지 수. 후보지 분석은 GIL에 묶인 그래프 연산이 지배적이라
#: 무제한 동시 실행이 오히려 느렸다(3곳 동시 12.9s vs 순차 8.8s).
CANDIDATE_CONCURRENCY = 2


async def _prewarm_transit(candidates: list[CandidateInput]) -> None:
    """각 후보지의 최근접 지하철역을 미리 캐시에 채운다 (실패해도 무시)."""
    from app.services.transit import find_nearest_subway

    await asyncio.gather(
        *[
            asyncio.wait_for(
                find_nearest_subway(c.lat, c.lng), timeout=TRANSIT_PREWARM_TIMEOUT_S
            )
            for c in candidates
        ],
        return_exceptions=True,
    )


async def compare_candidates(
    candidates: list[CandidateInput],
    *,
    inds_lcls_cd: str | None,
    inds_mcls_cd: str | None,
    inds_scls_cd: str | None,
    radius_m: int = 500,
    priority: str = "balanced",
    brand: str | None = None,
    budget_max_rent_man: float | None = None,
) -> DecisionResult:
    # /api/site-score wraps the Kakao station lookup in a 2.5s timeout. With
    # several candidates in flight that call can lose the race, which silently
    # drops the transit subscore and changes total_score for the *same* point —
    # i.e. the ranking would not be reproducible. Resolve every candidate's
    # station first, with a generous timeout, so the per-candidate lookups all
    # hit transit.py's cache and return the same value every run.
    await _prewarm_transit(candidates)

    tree = await get_upjong_tree()
    upjong = resolve_upjong_selection(
        inds_lcls_cd=inds_lcls_cd,
        inds_mcls_cd=inds_mcls_cd,
        inds_scls_cd=inds_scls_cd,
        business_type="general",
        tree=tree,
    )

    # Candidate analysis is dominated by GIL-bound graph work (road network,
    # isochrones), so unbounded fan-out measurably *slowed* the request —
    # 3 concurrent site-scores took 12.9s against 8.8s run back-to-back.
    # A small semaphore keeps the I/O phases overlapping without letting the
    # CPU phases thrash each other.
    gate = asyncio.Semaphore(CANDIDATE_CONCURRENCY)

    async def _guarded(c: CandidateInput):
        async with gate:
            return await evaluate_candidate(
                c,
                upjong=upjong,
                inds_lcls_cd=inds_lcls_cd,
                inds_mcls_cd=inds_mcls_cd,
                inds_scls_cd=inds_scls_cd,
                radius_m=radius_m,
                brand=brand,
                priority=priority,
                budget_max_rent_man=budget_max_rent_man,
            )

    results = await asyncio.gather(*[_guarded(c) for c in candidates])

    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    for row, warns in results:
        rows.append(row)
        warnings.extend(warns)

    # 동점 시 id 사전순으로 고정 — 같은 입력이 같은 순위를 내도록.
    rows.sort(key=lambda r: (-r["recommend_score"], r["id"]))

    # 순위가 정해진 뒤에 "무엇이 순위를 갈랐는지"를 항목별로 분해해 붙인다.
    compute_rank_drivers(rows)

    weights_used = rows[0].get("_weights", {}) if rows else {}
    ranked = [
        RankedCandidate(
            id=r["id"],
            label=r["label"],
            rank=i + 1,
            recommend_score=r["recommend_score"],
            verdict=r["verdict"],
            verdict_label=r["verdict_label"],
            expected_monthly_sales_man=r["expected_monthly_sales_man"],
            expected_monthly_sales_range_man=r.get("expected_monthly_sales_range_man"),
            expected_sales_basis=r["expected_sales_basis"],
            max_acceptable_rent_man=r["max_acceptable_rent_man"],
            current_rent_man=r["current_rent_man"],
            rent_gap_man=r["rent_gap_man"],
            strengths=r["strengths"],
            risks=r["risks"],
            actions=r["actions"],
            evidence=r["evidence"],
        )
        for i, r in enumerate(rows)
    ]

    # 중복 경고 제거 (순서 유지).
    seen: set[str] = set()
    deduped = [w for w in warnings if not (w in seen or seen.add(w))]
    return DecisionResult(ranked=ranked, warnings=deduped, weights_used=weights_used)
