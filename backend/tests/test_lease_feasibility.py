"""Tests for lease feasibility calculations."""

from app.services.lease_feasibility import (
    LeaseInputs,
    SiteContext,
    analyze_lease_feasibility,
    assess_feasibility,
    compute_labor_cost_man,
    compute_lease_metrics,
    compute_variable_ratio,
)


def test_variable_ratio_cafe_defaults():
    ratio = compute_variable_ratio("food_drink", cogs_ratio=0.30, delivery_share=0.0)
    # 0.30 + 0.02 + 0.03 = 0.35
    assert abs(ratio - 0.35) < 0.001


def test_break_even_spec_example():
    """Spec: fixed 990만, variable 38% → break-even ~1,596만."""
    inputs = LeaseInputs(
        monthly_rent_man=350,
        maintenance_man=40,
        staff_count=2,
        labor_cost_man=500,
        misc_fixed_man=100,
        cogs_ratio=0.30,
        ticket_price=4500,
        delivery_share=0.0,
    )
    # Force 38% variable: 0.30 + 0.02 + 0.03 + extra — use explicit cogs
    # food_drink default gives 35%; override to hit 38% via delivery
    inputs.delivery_share = 0.2  # 0.35 + 0.2*0.15 = 0.38
    metrics = compute_lease_metrics(inputs, "food_drink")
    assert abs(metrics.monthly_fixed_cost_man - 990) < 1
    assert abs(metrics.variable_cost_ratio - 0.38) < 0.01
    assert 1580 <= metrics.break_even_monthly_man <= 1610


def test_labor_cost_two_staff():
    cost = compute_labor_cost_man(2, hours_per_day=8, days_per_week=5)
    assert 400 <= cost <= 450


def test_appropriate_rent_band():
    inputs = LeaseInputs(monthly_rent_man=350, maintenance_man=40, labor_cost_man=500, misc_fixed_man=100)
    metrics = compute_lease_metrics(inputs, "food_drink", benchmark_monthly_man=2000)
    assert metrics.appropriate_rent_low_man == 200  # 10%
    assert metrics.appropriate_rent_high_man == 300  # 15%
    assert metrics.rent_excess_man == 90  # 390 - 300


def test_verdict_risk():
    metrics = compute_lease_metrics(
        LeaseInputs(monthly_rent_man=350, maintenance_man=40, labor_cost_man=500, misc_fixed_man=100),
        "food_drink",
        benchmark_monthly_man=1500,
    )
    site = SiteContext(total_score=48, competition_label="높음", same_category_count=34)
    verdict = assess_feasibility(
        metrics, site, benchmark_monthly_man=1500, user_lease_cost_man=390
    )
    assert verdict == "위험"


def test_verdict_good():
    inputs = LeaseInputs(monthly_rent_man=200, maintenance_man=30, labor_cost_man=400, misc_fixed_man=80)
    metrics = compute_lease_metrics(inputs, "food_drink", benchmark_monthly_man=2500)
    site = SiteContext(total_score=72, competition_label="보통", same_category_count=8)
    verdict = assess_feasibility(
        metrics, site, benchmark_monthly_man=2500, user_lease_cost_man=230
    )
    assert verdict in ("양호", "보통")


def test_analyze_returns_explanation():
    inputs = LeaseInputs(monthly_rent_man=350, maintenance_man=40, staff_count=2, cogs_ratio=0.30)
    site = SiteContext(
        total_score=49,
        competition_label="높음",
        same_category_count=9,
        business_label="카페",
    )
    result = analyze_lease_feasibility(
        inputs,
        "food_drink",
        site,
        benchmark_monthly_man=2000,
        benchmark_scope="서울시 서대문구 신촌동",
    )
    assert result.verdict in ("위험", "보통", "양호")
    assert "손익분기" in result.explanation or "월매출" in result.explanation
    assert result.metrics.break_even_monthly_man > 0


def test_key_money_payback():
    inputs = LeaseInputs(
        monthly_rent_man=200,
        maintenance_man=30,
        labor_cost_man=300,
        misc_fixed_man=70,
        key_money_man=3000,
        cogs_ratio=0.30,
    )
    metrics = compute_lease_metrics(inputs, "food_drink", benchmark_monthly_man=3000)
    assert metrics.key_money_payback_months is not None
    assert metrics.key_money_payback_months > 0
