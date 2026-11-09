"""Lease feasibility / break-even analysis for commercial locations.

Pure calculation — no I/O except loading static cost defaults.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULTS_PATH = DATA_DIR / "upjong_cost_defaults.json"

# 2026 minimum wage (고용노동부)
MIN_WAGE_HOURLY = 10_320
STANDARD_MONTHLY_HOURS = 209
LABOR_OVERHEAD_FACTOR = 1.20
MAN_WON = 10_000  # 1만원 단위 → 원


@dataclass
class LeaseInputs:
    monthly_rent_man: int
    maintenance_man: int = 0
    deposit_man: int | None = None
    key_money_man: int | None = None
    area_pyeong: float | None = None
    staff_count: int = 1
    ticket_price: int = 4500
    cogs_ratio: float | None = None
    delivery_share: float = 0.0
    interior_cost_man: int | None = None
    hours_per_day: float = 8.0
    days_per_week: int = 5
    labor_cost_man: int | None = None
    misc_fixed_man: int | None = None


@dataclass
class LeaseMetrics:
    monthly_fixed_cost_man: float
    labor_cost_man: float
    misc_fixed_man: float
    variable_cost_ratio: float
    break_even_monthly_man: float
    break_even_daily_man: float
    orders_per_day: float
    rent_burden_pct: float | None
    appropriate_rent_low_man: float | None
    appropriate_rent_high_man: float | None
    rent_excess_man: float | None
    key_money_payback_months: float | None
    recommended_rent_max_man: float | None
    monthly_operating_profit_man: float | None
    rent_per_pyeong_man: float | None


@dataclass
class SiteContext:
    total_score: float | None = None
    grade: str = ""
    competition_label: str = ""
    same_category_count: int = 0
    cannibalization_risk: float | None = None
    cannibalization_label: str = ""
    business_label: str = ""
    best_restaurant_models: list[str] = field(default_factory=list)
    best_cafe_models: list[str] = field(default_factory=list)


@dataclass
class FeasibilityResult:
    verdict: str  # 위험 | 보통 | 양호
    metrics: LeaseMetrics
    explanation: str
    explanation_source: str = "rule"


def _load_defaults() -> dict[str, Any]:
    with DEFAULTS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def get_archetype_defaults(archetype: str) -> dict[str, Any]:
    data = _load_defaults()
    return data.get(archetype) or data["general"]


def compute_labor_cost_man(
    staff_count: int,
    *,
    hours_per_day: float = 8.0,
    days_per_week: int = 5,
    overhead_factor: float = LABOR_OVERHEAD_FACTOR,
) -> float:
    """Monthly labor cost in 만원 units."""
    if staff_count <= 0:
        return 0.0
    hours_per_month = hours_per_day * days_per_week * 52 / 12
    monthly_won = (
        staff_count * MIN_WAGE_HOURLY * hours_per_month * overhead_factor
    )
    return monthly_won / MAN_WON


def compute_variable_ratio(
    archetype: str,
    *,
    cogs_ratio: float | None,
    delivery_share: float,
) -> float:
    defaults = get_archetype_defaults(archetype)
    cogs = cogs_ratio if cogs_ratio is not None else float(defaults["cogs_ratio"])
    card = float(defaults["card_fee_ratio"])
    packaging = float(defaults["packaging_ratio"])
    delivery_fee = float(defaults.get("delivery_fee_ratio") or 0.0)
    delivery_component = max(0.0, min(1.0, delivery_share)) * delivery_fee
    total = cogs + card + packaging + delivery_component
    return min(0.95, max(0.05, total))


def compute_lease_metrics(
    inputs: LeaseInputs,
    archetype: str = "general",
    *,
    benchmark_monthly_man: float | None = None,
) -> LeaseMetrics:
    defaults = get_archetype_defaults(archetype)
    rent_man = inputs.monthly_rent_man
    maint_man = inputs.maintenance_man
    lease_cost_man = rent_man + maint_man

    if inputs.labor_cost_man is not None:
        labor_man = float(inputs.labor_cost_man)
    else:
        labor_man = compute_labor_cost_man(
            inputs.staff_count,
            hours_per_day=inputs.hours_per_day,
            days_per_week=inputs.days_per_week,
        )

    misc_man = (
        float(inputs.misc_fixed_man)
        if inputs.misc_fixed_man is not None
        else float(defaults["misc_fixed_man"])
    )
    fixed_man = rent_man + maint_man + labor_man + misc_man

    variable_ratio = compute_variable_ratio(
        archetype,
        cogs_ratio=inputs.cogs_ratio,
        delivery_share=inputs.delivery_share,
    )
    contribution_margin = 1.0 - variable_ratio
    break_even_monthly = fixed_man / contribution_margin
    break_even_daily = break_even_monthly / 30.0
    ticket = max(inputs.ticket_price, 1)
    orders_per_day = (break_even_daily * MAN_WON) / ticket

    reference_revenue = benchmark_monthly_man or break_even_monthly
    rent_burden = (lease_cost_man / reference_revenue * 100) if reference_revenue > 0 else None

    rent_low_ratio = float(defaults["rent_ratio_low"])
    rent_high_ratio = float(defaults["rent_ratio_high"])
    appropriate_low = None
    appropriate_high = None
    rent_excess = None
    recommended_max = None
    if benchmark_monthly_man and benchmark_monthly_man > 0:
        appropriate_low = benchmark_monthly_man * rent_low_ratio
        appropriate_high = benchmark_monthly_man * rent_high_ratio
        rent_excess = max(0.0, lease_cost_man - appropriate_high)
        # 역산: 벤치마크 매출 기준 적정 임대료율 상한
        recommended_max = appropriate_high
        # 손익분기 기준으로도 역산해 더 보수적인 값 선택
        break_even_based = break_even_monthly * rent_high_ratio
        if recommended_max is None or break_even_based < recommended_max:
            recommended_max = break_even_based

    monthly_profit = None
    key_payback = None
    if benchmark_monthly_man and benchmark_monthly_man > 0:
        gross_profit = benchmark_monthly_man * contribution_margin
        monthly_profit = gross_profit - fixed_man
        if inputs.key_money_man and monthly_profit > 0:
            key_payback = inputs.key_money_man / monthly_profit

    rent_per_pyeong = None
    if inputs.area_pyeong and inputs.area_pyeong > 0:
        rent_per_pyeong = lease_cost_man / inputs.area_pyeong

    return LeaseMetrics(
        monthly_fixed_cost_man=round(fixed_man, 1),
        labor_cost_man=round(labor_man, 1),
        misc_fixed_man=round(misc_man, 1),
        variable_cost_ratio=round(variable_ratio, 4),
        break_even_monthly_man=round(break_even_monthly, 1),
        break_even_daily_man=round(break_even_daily, 1),
        orders_per_day=round(orders_per_day, 1),
        rent_burden_pct=round(rent_burden, 1) if rent_burden is not None else None,
        appropriate_rent_low_man=round(appropriate_low, 1) if appropriate_low else None,
        appropriate_rent_high_man=round(appropriate_high, 1) if appropriate_high else None,
        rent_excess_man=round(rent_excess, 1) if rent_excess is not None else None,
        key_money_payback_months=round(key_payback, 1) if key_payback else None,
        recommended_rent_max_man=round(recommended_max, 1) if recommended_max else None,
        monthly_operating_profit_man=round(monthly_profit, 1) if monthly_profit is not None else None,
        rent_per_pyeong_man=round(rent_per_pyeong, 1) if rent_per_pyeong else None,
    )


def assess_feasibility(
    metrics: LeaseMetrics,
    site: SiteContext,
    *,
    benchmark_monthly_man: float | None = None,
    user_lease_cost_man: float,
) -> str:
    """Return verdict: 위험 | 보통 | 양호."""
    high_competition = site.competition_label in ("높음", "매우 높음")
    low_site = site.total_score is not None and site.total_score < 55
    strong_site = site.total_score is not None and site.total_score >= 60

    rent_high = False
    if metrics.appropriate_rent_high_man is not None:
        rent_high = user_lease_cost_man > metrics.appropriate_rent_high_man

    break_even_stress = False
    if benchmark_monthly_man and benchmark_monthly_man > 0:
        break_even_stress = metrics.break_even_monthly_man > benchmark_monthly_man * 0.85

    if rent_high and break_even_stress and (low_site or high_competition):
        return "위험"

    rent_ok = (
        metrics.appropriate_rent_low_man is not None
        and metrics.appropriate_rent_high_man is not None
        and metrics.appropriate_rent_low_man <= user_lease_cost_man <= metrics.appropriate_rent_high_man
    )
    break_even_ok = False
    if benchmark_monthly_man and benchmark_monthly_man > 0:
        break_even_ok = metrics.break_even_monthly_man < benchmark_monthly_man * 0.75

    if rent_ok and break_even_ok and strong_site:
        return "양호"

    if not benchmark_monthly_man:
        if rent_high and (low_site or high_competition):
            return "위험"
        if not rent_high and strong_site and not high_competition:
            return "양호"

    return "보통"


def rule_based_lease_explanation(
    metrics: LeaseMetrics,
    site: SiteContext,
    verdict: str,
    *,
    benchmark_monthly_man: float | None = None,
    benchmark_scope: str = "",
    user_lease_cost_man: float,
    business_label: str = "",
) -> str:
    label = business_label or site.business_label or "해당 업종"
    lease_str = f"{user_lease_cost_man:.0f}만 원"
    break_even_str = f"{metrics.break_even_monthly_man:,.0f}만 원"
    daily_str = f"{metrics.break_even_daily_man:,.0f}만 원"
    orders_str = f"{metrics.orders_per_day:,.0f}"

    headline = {
        "위험": f"이 임대조건은 {label} 기준으로 부담이 큰 편입니다.",
        "보통": f"이 임대조건은 {label} 기준으로 신중한 검토가 필요합니다.",
        "양호": f"이 임대조건은 {label} 기준으로 수용 가능한 범위에 가깝습니다.",
    }.get(verdict, "임대조건을 검토해 보세요.")

    why_parts: list[str] = []
    why_parts.append(
        f"월 고정비 약 {metrics.monthly_fixed_cost_man:,.0f}만 원 기준 손익분기 월매출은 약 {break_even_str}입니다."
    )
    why_parts.append(f"이를 맞추려면 일매출 약 {daily_str}, 하루 약 {orders_str}건(객단가 기준)이 필요합니다.")
    if metrics.rent_burden_pct is not None:
        why_parts.append(f"임대료 부담률은 약 {metrics.rent_burden_pct:.1f}%입니다.")

    caution_parts: list[str] = []
    if metrics.rent_excess_man and metrics.rent_excess_man > 0:
        caution_parts.append(
            f"입력 임차비({lease_str})가 적정 범위를 약 {metrics.rent_excess_man:.0f}만 원 초과합니다."
        )
    if site.competition_label in ("높음", "매우 높음"):
        caution_parts.append(
            f"동일업종 경쟁이 {site.competition_label}입니다(약 {site.same_category_count}곳)."
        )
    if site.total_score is not None and site.total_score < 55:
        caution_parts.append(f"입지 점수 {site.total_score:.0f}점으로 유입·수요 여건이 다소 약합니다.")
    if benchmark_monthly_man:
        bench_str = f"{benchmark_monthly_man:,.0f}만 원"
        caution_parts.append(
            f"상권 추정 월매출 벤치마크는 약 {bench_str}입니다({benchmark_scope or '서울 상권분석'})."
        )
    elif benchmark_scope:
        caution_parts.append(benchmark_scope)

    strategy_parts: list[str] = []
    if site.best_restaurant_models:
        strategy_parts.append(
            f"식당 모델로는 {', '.join(site.best_restaurant_models)}이 상대적으로 유리합니다."
        )
    if site.best_cafe_models:
        strategy_parts.append(
            f"카페 모델로는 {', '.join(site.best_cafe_models)}을 검토해 보세요."
        )
    if metrics.recommended_rent_max_man:
        strategy_parts.append(
            f"안정적 운영을 위해 월 임차비(월세+관리비)는 {metrics.recommended_rent_max_man:.0f}만 원 이하를 검토해 보세요."
        )
    if metrics.key_money_payback_months:
        strategy_parts.append(
            f"권리금 회수에는 약 {metrics.key_money_payback_months:.0f}개월이 예상됩니다."
        )
    if verdict == "위험":
        strategy_parts.append("객단가 상향·인건비 절감·임대료 재협상 중 하나 이상이 필요할 수 있습니다.")

    caution = " ".join(caution_parts) if caution_parts else "상권 벤치마크와 경쟁 밀도를 함께 확인하세요."
    strategy = " ".join(strategy_parts) if strategy_parts else "월세·관리비·인건비를 조정해 손익분기를 낮추는 시나리오를 비교해 보세요."

    return (
        f"**한 줄 결론** — {headline}\n\n"
        f"**손익 요약** — {' '.join(why_parts)}\n\n"
        f"**주의할 점** — {caution}\n\n"
        f"**추천 전략** — {strategy}"
    )


def analyze_lease_feasibility(
    inputs: LeaseInputs,
    archetype: str,
    site: SiteContext,
    *,
    benchmark_monthly_man: float | None = None,
    benchmark_scope: str = "",
) -> FeasibilityResult:
    user_lease = inputs.monthly_rent_man + inputs.maintenance_man
    metrics = compute_lease_metrics(
        inputs,
        archetype,
        benchmark_monthly_man=benchmark_monthly_man,
    )
    verdict = assess_feasibility(
        metrics,
        site,
        benchmark_monthly_man=benchmark_monthly_man,
        user_lease_cost_man=user_lease,
    )
    explanation = rule_based_lease_explanation(
        metrics,
        site,
        verdict,
        benchmark_monthly_man=benchmark_monthly_man,
        benchmark_scope=benchmark_scope,
        user_lease_cost_man=user_lease,
        business_label=site.business_label,
    )
    return FeasibilityResult(
        verdict=verdict,
        metrics=metrics,
        explanation=explanation,
        explanation_source="rule",
    )
