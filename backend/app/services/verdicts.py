"""계약·매입 의사결정 엔진 — "그래서 계약해도 되나 / 사도 되나"에 답한다.

설계 원칙(decision.py와 동일, 타협 없음):
**판정·임계값·금액은 전부 여기(파이썬)에서 결정론적으로 계산한다.**
LLM은 이미 결정된 결론을 한국어 문장으로 옮겨 적을 뿐이고, 실패하거나
꺼져 있으면 같은 결론을 내는 규칙 기반 템플릿이 그대로 쓰인다.
같은 입력은 언제나 같은 판정·같은 숫자를 낸다.

이 모듈은 새로 계산하지 않아도 되는 것은 새로 계산하지 않는다:
- 입지·예상매출·적정월세·강점/위험/권고 → app.services.decision
- 손익분기                              → app.services.lease_feasibility
- NOI·Cap rate·DSCR·현금흐름            → app.services.profitability
- 주변 실거래 비교                       → app.services.property_market
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.services.decision import (
    VERDICT_LABELS,
    CandidateInput,
    evaluate_candidate,
    _prewarm_transit,
)
from app.services.lease_feasibility import LeaseInputs, LeaseMetrics, compute_lease_metrics
from app.services.llm_client import complete_json
from app.services.profitability import (
    ExitInputs,
    LoanInputs,
    ProfitabilityResult,
    PurchaseInputs,
    RentalInputs,
    analyze_profitability,
)
from app.services.property_market import MarketBand, fetch_market_band
from app.services.sbiz_upjong import UpjongSelection, get_upjong_tree, resolve_upjong_selection

PYEONG_TO_SQM = 3.305785


# ---------------------------------------------------------------------------
# 계약 협상 조건 상수
# ---------------------------------------------------------------------------

#: 렌트프리(초기 무상임대) 환산에 쓰는 기준 기간(개월).
#: 렌트프리는 "첫 해에 낼 초과 임차비를 임대인이 면제해 주는 것"으로 본다.
#: 그래서 격차 × 12개월치를 월 임차비로 나누면 필요한 무상 개월 수가 된다.
RENT_FREE_HORIZON_MONTHS = 12

#: 렌트프리로 요구할 수 있는 현실적 상한(개월). 서울 상가 신규 임대차에서
#: 통상 제시되는 렌트프리는 1~3개월, 공실이 긴 물건도 6개월 선이 한계다.
#: 이보다 더 필요하다는 것은 "렌트프리로는 못 메운다 = 월세 자체를 내려야 한다"는
#: 신호이므로, 상한에 걸리면 위험/권고 문구가 월세 인하를 먼저 말하게 한다.
#: 또한 렌트프리는 첫 해만 상쇄할 뿐 2년차부터 격차가 그대로 돌아온다.
RENT_FREE_MAX_MONTHS = 6

#: 권리금 회수 기간(개월). 상가건물임대차보호법상 기본 임대차 기간이 2년이라,
#: "최소한 첫 계약기간 안에는 회수 가능해야 한다"를 지급 상한의 근거로 삼는다.
#: (lease_feasibility.key_money_payback_months가 쓰는 회수 개념과 같은 방향)
KEY_MONEY_PAYBACK_MONTHS = 24


# ---------------------------------------------------------------------------
# 매입 판정 상수
# ---------------------------------------------------------------------------
#
# 이 저장소에는 시장 Cap Rate 상수가 이미 들어와 있지 않다(reb_rent_stats 테이블도
# 임대료·공실률만 있고 수익률 컬럼이 없다). 그래서 아래 값은 새로 만든 벤치마크이며,
# 근거를 함께 적어 둔다. 나중에 한국부동산원 수익률 시계열을 적재하면 이 상수를
# 데이터 조회로 교체하면 된다.

#: 서울 상가 시장 평균 소득수익률(연 %). 한국부동산원 상업용부동산 임대동향조사가
#: 발표하는 '소득수익률'(임대소득 ÷ 자산가치, 운영비 차감 후)의 서울 상가 최근
#: 수준이 연 3% 초반대다. 이 아래면 "시장 평균만도 못한 매물"로 본다.
PURCHASE_CAP_RATE_MARKET_PCT = 3.2

#: 추천 하한(연 %). 상가담보대출 금리가 4%대인 시장에서 Cap Rate가 그보다
#: 낮으면 대출을 낄수록 자기자본 수익이 나빠진다(역레버리지). 대출금리 위에
#: 여유를 두려면 4.5%는 넘어야 한다는 뜻.
PURCHASE_CAP_RATE_RECOMMEND_PCT = 4.5

#: 비추천 상한(연 %). 이 아래는 운영수익이 보유비용(재산세·수선·공실)조차
#: 못 덮어, 매각차익에만 기대는 투자가 된다.
PURCHASE_CAP_RATE_AVOID_PCT = 2.0

#: 주변 실거래 중위 단가 대비 허용 프리미엄(%). 이 이내면 '시세 수준'.
PURCHASE_PRICE_PREMIUM_OK_PCT = 5.0

#: 주변 중위 대비 이만큼 비싸면 수익률과 무관하게 비추천.
#: profitability_explain.py가 +20%를 'high' 위험으로 쓰고 있어, 판정을 뒤집는
#: 선은 그보다 한 단계 위인 +25%로 잡았다.
PURCHASE_PRICE_PREMIUM_AVOID_PCT = 25.0

#: 추천에 요구하는 최소 입지 종합점수 (decision.VERDICT_CONDITIONAL_MIN보다
#: 한 단계 낮게 — 매입은 임차와 달리 '내가 장사할 입지'가 아니라 '임대가 되는
#: 입지'면 되므로 임차 기준을 그대로 적용하지 않는다).
PURCHASE_SITE_SCORE_MIN = 55.0

#: 공실률 기본 가정. profitability.RentalInputs의 기본값과 동일(5%).
DEFAULT_VACANCY_RATE = 0.05

#: 임대인 부담 운영비를 월 임대료의 몇 %로 볼지. 재산세·건물보험·공용부
#: 수선충당·공실 관리비를 합친 통상 수준이다. Cap Rate 벤치마크(소득수익률)가
#: 운영비 차감 후 값이라, NOI를 운영비 0으로 잡으면 비교가 한쪽으로 기운다.
OWNER_OPEX_RATIO_OF_RENT = 0.10

#: 보유 기간(년) — 연도별 현금흐름/IRR 계산용 기본값.
DEFAULT_HOLD_YEARS = 5

#: 대출 만기(년) 기본값 — 사용자가 금리만 주고 만기를 안 줄 때.
DEFAULT_LOAN_TERM_YEARS = 20

MONTHS = 12


def _round1(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


# ---------------------------------------------------------------------------
# 1. 계약 협상 조건 (순수 계산)
# ---------------------------------------------------------------------------


@dataclass
class NegotiationTerms:
    target_rent_man: float | None
    rent_free_months: int | None
    max_key_money_man: float | None


def negotiation_terms(
    *,
    monthly_rent_man: float,
    maintenance_man: float,
    max_lease_cost_man: float | None,
) -> NegotiationTerms:
    """격차를 '계약 가능 조건'으로 환산한다. 전부 계산값, 추정 없음.

    max_lease_cost_man은 decision.max_acceptable_rent()가 준 **월 임차비
    (월세+관리비) 상한**이다. 협상 대상은 월세이므로 관리비를 빼서 목표 월세를 낸다.

    - target_rent_man   = 상한 − 관리비 (이미 상한 이하면 현재 월세 그대로)
    - rent_free_months  = ceil(격차 × 12 / 월임차비), 0~6개월로 절단
    - max_key_money_man = (상한 − 현재 임차비) × 24개월 (여유가 없으면 0)
    """
    if max_lease_cost_man is None or max_lease_cost_man <= 0:
        return NegotiationTerms(None, None, None)

    lease_cost = monthly_rent_man + maintenance_man
    gap = lease_cost - max_lease_cost_man

    if gap <= 0:
        # 이미 감당 가능한 조건 — 낮출 이유가 없으니 현재 월세가 곧 목표다.
        surplus = -gap
        return NegotiationTerms(
            target_rent_man=round(monthly_rent_man, 1),
            rent_free_months=0,
            max_key_money_man=round(surplus * KEY_MONEY_PAYBACK_MONTHS, 1),
        )

    target_rent = max(0.0, max_lease_cost_man - maintenance_man)
    rent_free = 0
    if lease_cost > 0:
        rent_free = math.ceil(gap * RENT_FREE_HORIZON_MONTHS / lease_cost)
        rent_free = max(0, min(rent_free, RENT_FREE_MAX_MONTHS))
    return NegotiationTerms(
        target_rent_man=round(target_rent, 1),
        rent_free_months=rent_free,
        # 임차비가 이미 상한을 넘으면 권리금을 회수할 여유가 없다.
        max_key_money_man=0.0,
    )


#: 권리금 회수가 이 개월 수를 넘으면 계약 판정을 한 단계 낮춘다.
#: 기준은 KEY_MONEY_PAYBACK_MONTHS(=상가임대차보호법 기본 임대차 2년)와 같다 —
#: 첫 계약기간 안에 못 뽑는 권리금은 '조건부'로 봐야 한다는 뜻.
LEASE_KEY_MONEY_DOWNGRADE_MONTHS = KEY_MONEY_PAYBACK_MONTHS

#: 예상 매출로도 영업이익이 마이너스면(=이 자리에서 이 조건은 적자) 무조건 비추천.
#: 점수·부담률과 무관하게 결론이 뒤집히는 유일한 하드 게이트다.
LEASE_LOSS_MAKING_VERDICT = "avoid"

_VERDICT_ORDER = ["recommend", "conditional", "hold", "avoid"]


def _downgrade(verdict: str, steps: int = 1) -> str:
    idx = _VERDICT_ORDER.index(verdict) if verdict in _VERDICT_ORDER else 0
    return _VERDICT_ORDER[min(idx + steps, len(_VERDICT_ORDER) - 1)]


def adjust_lease_verdict(
    *,
    base_verdict: str,
    monthly_operating_profit_man: float | None,
    key_money_payback_months: float | None,
) -> tuple[str, list[str]]:
    """decision.decide_verdict의 결론에 계약 고유 요인을 반영한다.

    decision.py의 판정은 '입지 점수 + 월세 격차'만 본다. 계약은 거기에 두 가지가
    더 붙는다: 실제로 이익이 남는지, 그리고 권리금을 회수할 수 있는지.
    두 규칙 모두 판정을 **낮추기만** 한다 — 어떤 경우에도 올리지 않는다.
    """
    reasons: list[str] = []
    verdict = base_verdict
    if monthly_operating_profit_man is not None and monthly_operating_profit_man < 0:
        verdict = LEASE_LOSS_MAKING_VERDICT
        reasons.append(
            f"예상 매출 기준 월 영업이익이 {monthly_operating_profit_man:,.0f}만원으로 "
            "적자라 판정을 '비추천'으로 내렸습니다."
        )
    if (
        key_money_payback_months is not None
        and key_money_payback_months > LEASE_KEY_MONEY_DOWNGRADE_MONTHS
    ):
        downgraded = _downgrade(verdict)
        if downgraded != verdict:
            reasons.append(
                f"권리금 회수에 {key_money_payback_months:,.0f}개월이 걸려 "
                f"첫 임대차 기간({LEASE_KEY_MONEY_DOWNGRADE_MONTHS}개월)을 넘기므로 "
                "판정을 한 단계 낮췄습니다."
            )
        verdict = downgraded
    return verdict, reasons


def lease_break_even(
    *,
    monthly_rent_man: float,
    maintenance_man: float,
    area_pyeong: float | None,
    key_money_man: float | None,
    archetype: str,
    expected_sales_man: float | None,
) -> LeaseMetrics:
    """손익분기·임대료 부담률 — 전부 lease_feasibility가 계산한다.

    decision.max_acceptable_rent()가 상한을 역산할 때 쓰는 운영 가정
    (직원 1명·하루 10시간·주 6일)을 그대로 써서, 두 숫자가 같은 전제 위에 있게 한다.
    """
    return compute_lease_metrics(
        LeaseInputs(
            monthly_rent_man=int(round(monthly_rent_man)),
            maintenance_man=int(round(maintenance_man)),
            area_pyeong=area_pyeong,
            key_money_man=int(round(key_money_man)) if key_money_man else None,
            staff_count=1,
            hours_per_day=10.0,
            days_per_week=6,
        ),
        archetype,
        benchmark_monthly_man=expected_sales_man,
    )


def build_lease_risks(
    *,
    metrics: LeaseMetrics,
    adjustments: list[str],
    base_risks: list[str],
    expected_sales_man: float | None,
) -> list[str]:
    """계약 고유 위험을 앞세우고, 입지 위험(decision.build_risks)을 뒤에 붙인다."""
    out: list[str] = list(adjustments)
    if (
        expected_sales_man
        and metrics.break_even_monthly_man > expected_sales_man
    ):
        out.append(
            f"손익분기 월매출 {metrics.break_even_monthly_man:,.0f}만원이 "
            f"예상 월매출 {expected_sales_man:,.0f}만원보다 높습니다 — 현재 조건은 적자 구조입니다."
        )
    if metrics.rent_excess_man:
        out.append(
            f"임차비가 적정 범위 상한({metrics.appropriate_rent_high_man:,.0f}만원)을 "
            f"{metrics.rent_excess_man:,.0f}만원 초과합니다."
        )
    for risk in base_risks:
        if risk not in out:
            out.append(risk)
    # 고정 개수로 자르지 않는다 — base_risks(decision.build_risks)에는
    # sales_benchmark_scope=dong처럼 신뢰도를 알리는 캐비어트가 들어 있는데,
    # 계약 고유 항목(adjustments·손익분기·임차비 초과)이 먼저 채워지면 잘려서
    # 화면에서 사라진다. 이 함수로 들어오는 입력은 이미 각자 상한이 있어
    # (adjustments 최대 2, base_risks 최대 3 등) 무제한으로 불어나지 않는다.
    return out


def build_lease_actions(
    *,
    terms: NegotiationTerms,
    metrics: LeaseMetrics,
    current_rent_man: float,
    key_money_man: float | None,
    base_actions: list[str],
    verdict: str,
) -> list[str]:
    """협상 조건을 먼저, 그다음 입지 권고(decision.build_actions)."""
    out: list[str] = []
    if terms.target_rent_man is not None and terms.target_rent_man < current_rent_man:
        out.append(
            f"월세 {terms.target_rent_man:,.0f}만원 이하로 협상 "
            f"(현재 대비 {current_rent_man - terms.target_rent_man:,.0f}만원 인하)"
        )
        if terms.rent_free_months:
            out.append(
                f"월세를 못 내리면 최소 {terms.rent_free_months}개월 무상임대(렌트프리)를 "
                "받아야 첫 해 초과분이 상쇄됩니다"
            )
    elif terms.target_rent_man is not None and verdict == "recommend":
        # 임대료가 예산 안이어도 판정이 추천이 아니면 "확정 진행"이라 하면 안 된다.
        # 보류·비추천은 경쟁이나 수요 때문에 나온 것이라, 그 상태에서 계약을
        # 진행하라고 하면 판정과 정면으로 어긋나는 조언이 나란히 뜬다.
        out.append(
            f"현재 월세 {current_rent_man:,.0f}만원은 감당 범위 안 — 계약 조건 확정 진행"
        )
    if terms.max_key_money_man is not None:
        if key_money_man and key_money_man > terms.max_key_money_man:
            out.append(
                f"권리금은 {terms.max_key_money_man:,.0f}만원까지가 회수 가능한 선 — "
                f"요구액 {key_money_man:,.0f}만원은 {key_money_man - terms.max_key_money_man:,.0f}만원 과다"
            )
        elif not key_money_man:
            out.append(
                f"권리금이 있다면 {terms.max_key_money_man:,.0f}만원 이하로만 지급하세요"
            )
    if metrics.orders_per_day:
        out.append(
            f"손익분기까지 하루 약 {metrics.orders_per_day:,.0f}건 필요 — "
            "현장에서 경쟁점 회전율로 달성 가능한지 확인"
        )
    # 판정을 행동으로 닫아 준다. 이 줄이 없으면 "보류"라고만 하고 무엇을
    # 해결해야 하는지 알려주지 않아, 사용자는 다음에 뭘 할지 모른 채 남는다.
    if verdict == "avoid":
        out.append("현 조건으로는 계약 보류 — 다른 매물을 우선 검토")
    elif verdict == "hold":
        out.append("임대료 외 항목(경쟁·수요)이 걸림 — 아래 위험을 확인한 뒤 결정하세요")
    elif verdict == "conditional":
        out.append("위 협상 조건이 받아들여지면 계약 진행 — 안 되면 보류")

    for action in base_actions:
        if action not in out:
            out.append(action)
    return out[:5]


# ---------------------------------------------------------------------------
# 2. 매입 판정 (순수 계산)
# ---------------------------------------------------------------------------


def decide_purchase_verdict(
    *,
    cap_rate_pct: float | None,
    price_premium_pct: float | None,
    site_total_score: float | None,
    dscr: float | None,
) -> str:
    """Cap Rate(시장 벤치마크 대비) + 실거래 대비 가격 + 입지로 판정.

    사다리 순서는 고정이다 — 같은 입력은 언제나 같은 판정.
      avoid       : 수익률이 보유비용도 못 덮거나, 시세보다 25% 이상 비싸거나,
                    임대료로 원리금을 못 갚는데 수익률까지 시장 평균 미만
      recommend   : 수익률 ≥ 4.5% + 시세 대비 +5% 이내 + 입지 55점 이상
      conditional : 수익률이 시장 평균(3.2%)은 넘지만 위 조건 중 하나 이상 미달
      hold        : 그 외 (수익률이 시장 평균 미만이지만 비추천 선은 아님)
    """
    if cap_rate_pct is None:
        return "hold"

    over_market = price_premium_pct is not None and (
        price_premium_pct >= PURCHASE_PRICE_PREMIUM_AVOID_PCT
    )
    cannot_service_debt = (
        dscr is not None
        and dscr < 1.0
        and cap_rate_pct < PURCHASE_CAP_RATE_MARKET_PCT
    )
    if cap_rate_pct < PURCHASE_CAP_RATE_AVOID_PCT or over_market or cannot_service_debt:
        return "avoid"

    price_ok = (
        price_premium_pct is None or price_premium_pct <= PURCHASE_PRICE_PREMIUM_OK_PCT
    )
    site_ok = site_total_score is None or site_total_score >= PURCHASE_SITE_SCORE_MIN
    if cap_rate_pct >= PURCHASE_CAP_RATE_RECOMMEND_PCT and price_ok and site_ok:
        return "recommend"
    if cap_rate_pct >= PURCHASE_CAP_RATE_MARKET_PCT:
        return "conditional"
    return "hold"


def max_acceptable_price(
    *,
    annual_noi_man: float | None,
    market_p50_per_sqm: float | None,
    area_sqm: float | None,
) -> tuple[float | None, str]:
    """추천 판정이 나오는 매입가 상한 (만원). 두 근거 중 보수적인 쪽.

    1) 수익률 기준: 연 NOI ÷ 4.5% — 이 가격이면 Cap Rate가 추천 하한과 같아진다.
    2) 시세 기준:   주변 동종 실거래 중위 단가 × 전용면적 — 시세 수준의 가격.
    """
    candidates: list[tuple[float, str]] = []
    if annual_noi_man and annual_noi_man > 0:
        candidates.append(
            (
                annual_noi_man / (PURCHASE_CAP_RATE_RECOMMEND_PCT / 100.0),
                f"연 NOI {annual_noi_man:,.0f}만원 ÷ 추천 하한 수익률 "
                f"{PURCHASE_CAP_RATE_RECOMMEND_PCT}%",
            )
        )
    if market_p50_per_sqm and area_sqm and area_sqm > 0:
        candidates.append(
            (
                market_p50_per_sqm * area_sqm / 10_000.0,
                f"주변 실거래 중위 단가 {market_p50_per_sqm:,.0f}원/㎡ × {area_sqm:.1f}㎡",
            )
        )
    if not candidates:
        return None, "NOI·주변 실거래 어느 쪽도 없어 매입가 상한을 계산하지 못함"
    value, basis = min(candidates, key=lambda c: c[0])
    return round(value, 1), basis


def price_premium_pct(band: MarketBand | None) -> float | None:
    """대상 매입 단가가 주변 중위 대비 몇 % 비싼지(+)/싼지(-)."""
    if band is None or band.subject_price_per_sqm is None or not band.p50:
        return None
    return round((band.subject_price_per_sqm - band.p50) / band.p50 * 100, 1)


# ---------------------------------------------------------------------------
# 3. 입지 분석 (decision.evaluate_candidate 재사용)
# ---------------------------------------------------------------------------


@dataclass
class SiteAnalysis:
    row: dict[str, Any]
    upjong: UpjongSelection
    warnings: list[str] = field(default_factory=list)


async def analyze_site(
    *,
    lat: float,
    lng: float,
    label: str,
    inds_lcls_cd: str | None,
    inds_mcls_cd: str | None,
    inds_scls_cd: str | None,
    radius_m: int,
    brand: str | None = None,
    monthly_cost_man: float | None = None,
    area_pyeong: float | None = None,
    floor: int | None = None,
) -> SiteAnalysis:
    """단일 후보지를 /api/decide와 **동일한 경로**로 평가한다.

    monthly_cost_man에는 월세+관리비(월 임차비)를 넣는다 — decision.py의
    적정 월세 상한이 임차비 기준이기 때문이다.
    """
    cand = CandidateInput(
        id="subject",
        label=label or "대상 물건",
        lat=lat,
        lng=lng,
        monthly_rent_man=monthly_cost_man,
        area_pyeong=area_pyeong,
        floor=floor,
    )
    # /api/site-score의 지하철역 조회는 2.5s 타임아웃이라, 캐시가 비어 있으면
    # 같은 좌표에서도 결과가 달라질 수 있다. 먼저 캐시를 채워 재현성을 고정한다.
    await _prewarm_transit([cand])

    tree = await get_upjong_tree()
    upjong = resolve_upjong_selection(
        inds_lcls_cd=inds_lcls_cd,
        inds_mcls_cd=inds_mcls_cd,
        inds_scls_cd=inds_scls_cd,
        business_type="general",
        tree=tree,
    )
    row, warnings = await evaluate_candidate(
        cand,
        upjong=upjong,
        inds_lcls_cd=inds_lcls_cd,
        inds_mcls_cd=inds_mcls_cd,
        inds_scls_cd=inds_scls_cd,
        radius_m=radius_m,
        brand=brand,
        priority="balanced",
        budget_max_rent_man=None,
    )
    # evaluate_candidate가 붙이는 "[subject] " 접두사는 사용자에게 의미가 없다.
    cleaned = [w.replace("[subject] ", "", 1) for w in warnings]
    return SiteAnalysis(row=row, upjong=upjong, warnings=cleaned)


# ---------------------------------------------------------------------------
# 4. 매입 수익성 (profitability 재사용)
# ---------------------------------------------------------------------------


@dataclass
class PurchaseEconomics:
    result: ProfitabilityResult
    monthly_rent_man: float
    rent_basis: str
    cap_rate_pct: float | None
    monthly_noi_man: float | None
    payback_years: float | None
    assumptions: list[str]


def compute_purchase_economics(
    *,
    purchase_price_man: float,
    monthly_rent_man: float,
    rent_basis: str,
    loan_amount_man: float | None,
    loan_rate_pct: float | None,
    loan_term_years: int | None = None,
    loan_interest_only: bool = False,
    acquisition_tax_man: float | None = None,
    deposit_man: float | None = None,
) -> PurchaseEconomics:
    """NOI·Cap Rate·회수기간. 계산은 전부 profitability.analyze_profitability."""
    owner_opex_monthly = monthly_rent_man * OWNER_OPEX_RATIO_OF_RENT
    loan = LoanInputs(
        principal_manwon=float(loan_amount_man or 0.0),
        annual_rate=(loan_rate_pct or 0.0) / 100.0,
        term_years=(
            int(loan_term_years or DEFAULT_LOAN_TERM_YEARS) if loan_amount_man else 0
        ),
        interest_only=bool(loan_interest_only),
    )
    result = analyze_profitability(
        PurchaseInputs(
            price_manwon=float(purchase_price_man),
            acquisition_tax_manwon=(
                float(acquisition_tax_man) if acquisition_tax_man is not None else None
            ),
        ),
        RentalInputs(
            deposit_manwon=float(deposit_man or 0.0),
            monthly_rent_manwon=float(monthly_rent_man),
            vacancy_rate=DEFAULT_VACANCY_RATE,
            owner_opex_monthly_manwon=owner_opex_monthly,
        ),
        loan,
        ExitInputs(hold_years=DEFAULT_HOLD_YEARS),
    )

    cap = result.cap_rate_noi_pct if purchase_price_man > 0 else None
    monthly_noi = result.noi_manwon / MONTHS if result.noi_manwon else None
    payback = (
        round(result.total_cost_manwon / result.noi_manwon, 1)
        if result.noi_manwon and result.noi_manwon > 0
        else None
    )

    assumptions = [
        f"공실률 {DEFAULT_VACANCY_RATE:.0%} 가정",
        f"임대인 부담 운영비(재산세·보험·수선충당)를 월세의 "
        f"{OWNER_OPEX_RATIO_OF_RENT:.0%}로 가정",
        f"취득 부대비용은 매입가의 4.6%(취득세 등) 자동 반영",
    ]
    assumptions.extend(result.assumptions)
    return PurchaseEconomics(
        result=result,
        monthly_rent_man=round(monthly_rent_man, 1),
        rent_basis=rent_basis,
        cap_rate_pct=cap,
        monthly_noi_man=_round1(monthly_noi),
        payback_years=payback,
        assumptions=assumptions,
    )


async def fetch_comparables(
    *,
    lat: float,
    lng: float,
    floor: int | None,
    area_sqm: float | None,
    purchase_price_man: int,
    property_type: str = "commercial",
    building_type: str = "집합",
) -> MarketBand:
    return await run_in_threadpool(
        fetch_market_band,
        lat,
        lng,
        property_type,
        building_type=building_type,
        floor=str(floor) if floor is not None else None,
        area_sqm=area_sqm,
        subject_price_manwon=int(purchase_price_man),
    )


def comparables_payload(band: MarketBand | None) -> dict[str, Any] | None:
    if band is None:
        return None
    return {
        "n": band.n,
        "radius_m": band.radius_m,
        "months_back": band.months_back,
        "p25": band.p25,
        "p50": band.p50,
        "p75": band.p75,
        "min_deal_date": band.min_deal_date,
        "max_deal_date": band.max_deal_date,
        "subject_price_per_sqm": band.subject_price_per_sqm,
        "subject_percentile": band.subject_percentile,
        "price_premium_pct": price_premium_pct(band),
        "relaxations": band.relaxations,
        "insufficient_sample": band.insufficient_sample,
        "data_source": band.data_source,
        "recent": [
            {
                "deal_date": c.deal_date,
                "bldg_nm": c.bldg_nm,
                "deal_amount_manwon": c.deal_amount_manwon,
                "area_sqm": c.area_sqm,
                "price_per_sqm": c.price_per_sqm,
                "floor": c.floor,
                "distance_m": c.distance_m,
            }
            for c in band.comparables[:5]
        ],
    }


# ---------------------------------------------------------------------------
# 5. 매입 강점/위험/권고 (결정론적 문장)
# ---------------------------------------------------------------------------


def _fmt_man(v: float) -> str:
    return f"{v:,.0f}만원"


def build_purchase_strengths(ev: dict[str, Any]) -> list[str]:
    out: list[str] = []
    cap = ev.get("cap_rate_pct")
    if cap is not None and cap >= PURCHASE_CAP_RATE_MARKET_PCT:
        out.append(
            f"연 수익률(Cap Rate) {cap:.2f}% — 서울 상가 시장 평균 "
            f"{PURCHASE_CAP_RATE_MARKET_PCT}%를 웃돔"
        )
    premium = ev.get("price_premium_pct")
    n = ev.get("comparable_count")
    if premium is not None and premium <= 0:
        out.append(f"매입 단가가 주변 실거래 중위보다 {abs(premium):.1f}% 저렴 (표본 {n}건)")
    elif premium is not None and premium <= PURCHASE_PRICE_PREMIUM_OK_PCT:
        out.append(f"매입 단가가 주변 실거래 중위 대비 +{premium:.1f}%로 시세 수준 (표본 {n}건)")
    payback = ev.get("payback_years")
    if payback is not None and payback <= 25:
        out.append(f"운영수익만으로 투자원금 회수에 약 {payback:.0f}년")
    dscr = ev.get("dscr")
    if dscr is not None and dscr >= 1.2:
        out.append(f"DSCR {dscr:.2f} — 임대수익으로 원리금을 갚고도 여유")
    site = ev.get("site_total_score")
    if site is not None and site >= PURCHASE_SITE_SCORE_MIN:
        out.append(f"입지 종합 {site:.0f}점 — 임차 수요를 받쳐 줄 수 있는 자리")
    if not out:
        out.append("수치상 두드러진 강점은 없음 — 가격 협상 여지가 유일한 지렛대")
    return out[:3]


def build_purchase_risks(ev: dict[str, Any]) -> list[str]:
    out: list[str] = []
    cap = ev.get("cap_rate_pct")
    if cap is not None and cap < PURCHASE_CAP_RATE_AVOID_PCT:
        out.append(
            f"연 수익률 {cap:.2f}% — 보유비용도 못 덮는 수준이라 매각차익에만 기대는 투자"
        )
    elif cap is not None and cap < PURCHASE_CAP_RATE_MARKET_PCT:
        out.append(
            f"연 수익률 {cap:.2f}%로 서울 상가 시장 평균 "
            f"{PURCHASE_CAP_RATE_MARKET_PCT}%에 못 미침"
        )
    premium = ev.get("price_premium_pct")
    n = ev.get("comparable_count")
    if premium is not None and premium >= PURCHASE_PRICE_PREMIUM_OK_PCT:
        out.append(f"매입 단가가 주변 실거래 중위보다 {premium:.1f}% 비쌈 (표본 {n}건)")
    dscr = ev.get("dscr")
    loan_rate = ev.get("loan_rate_pct")
    if dscr is not None and dscr < 1.0:
        out.append(f"DSCR {dscr:.2f} — 임대수익만으로 원리금을 못 갚아 추가 자금 필요")
    if cap is not None and loan_rate and cap < loan_rate:
        out.append(
            f"대출금리 {loan_rate:.2f}%가 수익률 {cap:.2f}%보다 높음 — "
            "대출을 늘릴수록 자기자본 수익이 나빠짐(역레버리지)"
        )
    if ev.get("insufficient_sample"):
        out.append(f"비교 가능한 주변 실거래가 {n}건뿐이라 시세 검증이 제한적")
    if ev.get("rent_estimated"):
        out.append("기대 월세를 입력하지 않아 추정치로 계산 — 실제 임대 조건 확인 필요")
    site = ev.get("site_total_score")
    if site is not None and site < PURCHASE_SITE_SCORE_MIN:
        out.append(f"입지 종합 {site:.0f}점 — 임차인 확보·공실 위험을 함께 봐야 함")
    if not out:
        out.append("치명적 위험은 확인되지 않음 — 임차인 구성과 관리비 부담 주체만 확인")
    # 고정 개수로 자르지 않는다 — 수익률·프리미엄 위험이 먼저 채워지면
    # insufficient_sample·rent_estimated·낮은 입지점수 같은 캐비어트가
    # 화면에서 밀려난다. 이 함수가 만들 수 있는 항목은 아래 조건들로 이미
    # 상한이 있어(최대 7개) 무제한으로 불어나지 않는다.
    return out


def build_purchase_actions(ev: dict[str, Any], verdict: str) -> list[str]:
    out: list[str] = []
    max_price = ev.get("max_acceptable_price_man")
    gap = ev.get("price_gap_man")
    if max_price and gap is not None and gap > 0:
        out.append(
            f"매입가 {_fmt_man(max_price)} 이하로 협상 (현재 대비 {_fmt_man(gap)} 인하)"
        )
    elif max_price and gap is not None:
        out.append(f"매입가 {_fmt_man(max_price)}까지는 추천 수익률 유지 — 조건 확정 진행")
    if ev.get("rent_estimated"):
        out.append("현 임차인 임대차계약서(월세·보증금·잔여기간) 확인 후 재계산")
    dscr = ev.get("dscr")
    if dscr is not None and dscr < 1.2:
        out.append("대출 비율 축소 또는 금리 재협상으로 DSCR 1.2 이상 확보")
    if ev.get("insufficient_sample"):
        out.append("동일 건물·인접 블록 실거래를 추가로 확인해 시세 근거 보강")
    if verdict == "avoid":
        out.append("현 조건으로는 매입 보류 — 다른 물건을 우선 검토")
    if not out:
        out.append("등기부·임대차 현황·관리비 정산 내역을 확인하고 계약 진행")
    return out[:4]


# ---------------------------------------------------------------------------
# 6. 서술 (LLM은 결론을 못 바꾼다 / 없어도 같은 결론)
# ---------------------------------------------------------------------------

_NARRATIVE_SYSTEM = (
    "당신은 한국의 상가 컨설턴트입니다. 이미 분석이 끝난 결과를 창업·투자 초보자도 "
    "이해할 수 있는 쉬운 한국어로 설명하세요.\n"
    "규칙:\n"
    "1. 주어진 JSON에 있는 숫자만 그대로 인용하세요. 새로운 숫자를 계산하거나 "
    "추정하지 마세요.\n"
    "2. 판정은 이미 정해져 있습니다. 뒤집거나 다르게 해석하지 마세요.\n"
    "3. 전문용어 금지: '통합도', 'Integration', 'Cap Rate' 대신 '찾아오기 쉬움', "
    "'연 수익률' 같은 일상어를 쓰세요.\n"
    "4. 금액은 '만원' 단위로 그대로 씁니다.\n"
    "5. JSON의 키 이름(예: 감당가능_월임차비상한_만원)을 문장에 그대로 쓰지 마세요. "
    "'감당 가능한 월 임차비'처럼 사람이 쓰는 말로 바꿔 쓰세요.\n"
    '6. 반드시 {"headline": "..."} 형식의 JSON으로만 답하세요.\n'
    "headline: 2~3문장. 결론이 무엇이고 왜인지, 어떤 조건이면 되는지."
)

_NARRATIVE_MAX_TOKENS = 500
_NARRATIVE_TIMEOUT_S = 20.0


async def narrate(payload: dict[str, Any], fallback: str) -> tuple[str, str]:
    """(headline, source). LLM이 꺼져 있거나 실패하면 규칙 문장을 그대로 쓴다."""
    data = await complete_json(
        system=_NARRATIVE_SYSTEM,
        user="분석 결과:\n" + json.dumps(payload, ensure_ascii=False, indent=1),
        max_tokens=_NARRATIVE_MAX_TOKENS,
        timeout_s=_NARRATIVE_TIMEOUT_S,
    )
    if data:
        headline = str(data.get("headline") or "").strip()
        if headline:
            return headline, "llm"
    return fallback, "rule"


def rule_lease_headline(
    *,
    label: str,
    verdict: str,
    expected_sales_man: float | None,
    lease_cost_man: float,
    max_lease_cost_man: float | None,
    rent_gap_man: float | None,
    break_even_man: float | None,
    terms: NegotiationTerms,
) -> str:
    """LLM 없이도 같은 결론을 내는 계약 판정 문장."""
    # 라벨에 이미 "매물"이 들어 있으면 "'신촌 매물' 매물은"처럼 겹친다.
    subject = (label or "").strip()
    if subject and "매물" not in subject:
        subject = f"{subject} 매물"
    parts = [
        f"{subject or '이 매물'}은 현재 조건 기준 '{VERDICT_LABELS[verdict]}'입니다."
    ]
    if expected_sales_man:
        # 이 숫자는 상권 평균 점포에서 출발한 추정이다. 매장 크기·운영에 따라
        # 크게 갈리므로 단정적으로 "이 자리의 매출"이라고 쓰지 않는다.
        parts.append(
            f"이 상권 평균 점포 기준으로는 월 {expected_sales_man:,.0f}만원 수준입니다."
        )
    if break_even_man:
        parts.append(
            f"지금 임차비 {lease_cost_man:,.0f}만원 기준 손익분기 월매출은 "
            f"약 {break_even_man:,.0f}만원입니다."
        )
    if max_lease_cost_man is None:
        parts.append(
            "상권 매출 근거가 없어 적정 임차비 상한을 계산하지 못했으니, "
            "인근 동일업종 실매출을 확인한 뒤 다시 판단하세요."
        )
    elif rent_gap_man is not None and rent_gap_man > 0:
        parts.append(
            f"감당 가능한 월 임차비 상한은 {max_lease_cost_man:,.0f}만원이라 "
            f"{rent_gap_man:,.0f}만원 비쌉니다."
        )
        if terms.target_rent_man is not None:
            cond = f"월세를 {terms.target_rent_man:,.0f}만원까지 낮추면 계약할 만합니다"
            if terms.rent_free_months:
                cond += (
                    f". 월세를 못 내린다면 최소 {terms.rent_free_months}개월 "
                    "무상임대(렌트프리)를 받아야 첫 해 손실을 메울 수 있습니다"
                )
            parts.append(cond + ".")
    else:
        parts.append(
            f"감당 가능한 월 임차비 상한이 {max_lease_cost_man:,.0f}만원이라 "
            f"지금 조건은 상한 안에 들어옵니다."
        )
        if terms.max_key_money_man:
            parts.append(
                f"권리금은 {terms.max_key_money_man:,.0f}만원까지가 "
                "2년 안에 회수 가능한 선입니다."
            )
    return " ".join(parts)


def rule_purchase_headline(
    *,
    label: str,
    verdict: str,
    purchase_price_man: float,
    cap_rate_pct: float | None,
    monthly_noi_man: float | None,
    payback_years: float | None,
    max_price_man: float | None,
    price_gap_man: float | None,
    premium_pct: float | None,
    rent_estimated: bool,
) -> str:
    """LLM 없이도 같은 결론을 내는 매입 판정 문장."""
    parts = [
        f"'{label}' 상가는 매입가 {purchase_price_man:,.0f}만원 기준 "
        f"'{VERDICT_LABELS[verdict]}'입니다."
    ]
    if cap_rate_pct is not None:
        sentence = f"연 수익률은 {cap_rate_pct:.2f}%로"
        if cap_rate_pct >= PURCHASE_CAP_RATE_RECOMMEND_PCT:
            sentence += f" 추천 기준 {PURCHASE_CAP_RATE_RECOMMEND_PCT}%를 넘습니다."
        elif cap_rate_pct >= PURCHASE_CAP_RATE_MARKET_PCT:
            sentence += (
                f" 서울 상가 시장 평균 {PURCHASE_CAP_RATE_MARKET_PCT}%는 넘지만 "
                f"추천 기준 {PURCHASE_CAP_RATE_RECOMMEND_PCT}%에는 못 미칩니다."
            )
        else:
            sentence += f" 시장 평균 {PURCHASE_CAP_RATE_MARKET_PCT}%에도 못 미칩니다."
        parts.append(sentence)
    if monthly_noi_man is not None:
        parts.append(f"공실·운영비를 뺀 월 순수익은 약 {monthly_noi_man:,.0f}만원입니다.")
    if payback_years is not None:
        parts.append(f"임대수익만으로 원금을 되찾는 데 약 {payback_years:.0f}년 걸립니다.")
    if premium_pct is not None:
        if premium_pct > 0:
            parts.append(f"매입 단가는 주변 실거래 중위보다 {premium_pct:.1f}% 비쌉니다.")
        else:
            parts.append(f"매입 단가는 주변 실거래 중위보다 {abs(premium_pct):.1f}% 쌉니다.")
    if max_price_man is not None:
        if price_gap_man is not None and price_gap_man > 0:
            parts.append(
                f"{max_price_man:,.0f}만원 이하로 낮추면 추천할 만한 조건이 되므로 "
                f"{price_gap_man:,.0f}만원 인하를 목표로 협상하세요."
            )
        else:
            parts.append(f"{max_price_man:,.0f}만원까지는 추천 수익률이 유지됩니다.")
    if rent_estimated:
        parts.append(
            "기대 월세를 입력하지 않아 이 자리에서 임차인이 감당 가능한 상한으로 "
            "추정했으니, 실제 임대차계약서를 확인한 뒤 다시 계산하세요."
        )
    return " ".join(parts)


__all__ = [
    "LEASE_KEY_MONEY_DOWNGRADE_MONTHS",
    "adjust_lease_verdict",
    "build_lease_actions",
    "build_lease_risks",
    "KEY_MONEY_PAYBACK_MONTHS",
    "MONTHS",
    "OWNER_OPEX_RATIO_OF_RENT",
    "PURCHASE_CAP_RATE_AVOID_PCT",
    "PURCHASE_CAP_RATE_MARKET_PCT",
    "PURCHASE_CAP_RATE_RECOMMEND_PCT",
    "PURCHASE_PRICE_PREMIUM_AVOID_PCT",
    "PURCHASE_PRICE_PREMIUM_OK_PCT",
    "PURCHASE_SITE_SCORE_MIN",
    "PYEONG_TO_SQM",
    "RENT_FREE_HORIZON_MONTHS",
    "RENT_FREE_MAX_MONTHS",
    "NegotiationTerms",
    "PurchaseEconomics",
    "SiteAnalysis",
    "analyze_site",
    "build_purchase_actions",
    "build_purchase_risks",
    "build_purchase_strengths",
    "comparables_payload",
    "compute_purchase_economics",
    "decide_purchase_verdict",
    "fetch_comparables",
    "max_acceptable_price",
    "narrate",
    "negotiation_terms",
    "price_premium_pct",
    "rule_lease_headline",
    "rule_purchase_headline",
]
