"""profitability_explain 위험 추출·설명 단위 테스트 (LLM 없이 rule-based)."""

import asyncio
from unittest.mock import patch

from app.services.profitability import (
    ExitInputs,
    LoanInputs,
    PurchaseInputs,
    RentalInputs,
    analyze_profitability,
)
from app.services.profitability_explain import (
    LocationContext,
    extract_risks,
    explain_profitability,
    suggest_levers,
)
from app.services.property_market import MarketBand


def _weak_result():
    """DSCR 낮고 매각차익 의존 높은 물건."""
    purchase = PurchaseInputs(price_manwon=100_000, acquisition_tax_manwon=0)
    rental = RentalInputs(monthly_rent_manwon=300, vacancy_rate=0.05)
    loan = LoanInputs(principal_manwon=70_000, annual_rate=0.05, term_years=20)
    exit_in = ExitInputs(hold_years=5, sale_price_growth_rate=0.03)
    return analyze_profitability(purchase, rental, loan, exit_in)


def _healthy_result():
    purchase = PurchaseInputs(price_manwon=50_000, acquisition_tax_manwon=0)
    rental = RentalInputs(monthly_rent_manwon=300, vacancy_rate=0.03)
    return analyze_profitability(purchase, rental)


def _band(subject_pps, p50, n=20, insufficient=False):
    return MarketBand(
        property_type="commercial", building_type="집합", n=n, radius_m=500,
        months_back=12, p25=p50 * 0.8, p50=p50, p75=p50 * 1.2,
        min_deal_date="2025-08-01", max_deal_date="2026-06-01",
        insufficient_sample=insufficient,
        subject_price_per_sqm=subject_pps,
    )


def test_dscr_risk_detected_and_ranked_first():
    risks = extract_risks(_weak_result())
    cats = [r.category for r in risks]
    assert "dscr" in cats
    # critical/high가 medium보다 앞에 정렬
    sev_order = [r.severity for r in risks]
    assert sev_order == sorted(
        sev_order, key=lambda s: {"critical": 0, "high": 1, "medium": 2}[s]
    )


def test_negative_leverage_detected():
    # NOI Cap이 대출금리보다 낮은 경우
    r = _weak_result()
    cats = [x.category for x in extract_risks(r)]
    assert "negative_leverage" in cats


def test_capital_gain_reliance_detected():
    r = _weak_result()
    assert r.capital_gain_contribution_pct is not None
    cats = [x.category for x in extract_risks(r)]
    assert "capital_gain_reliance" in cats


def test_overpriced_from_market_band():
    r = _healthy_result()
    # 대상 단가가 중위 대비 25% 높음
    band = _band(subject_pps=1_250, p50=1_000)
    cats = [x.category for x in extract_risks(r, band)]
    assert "overpriced" in cats
    # 20% 이상이면 high
    over = next(x for x in extract_risks(r, band) if x.category == "overpriced")
    assert over.severity == "high"


def test_not_overpriced_when_below_market():
    r = _healthy_result()
    band = _band(subject_pps=950, p50=1_000)
    cats = [x.category for x in extract_risks(r, band)]
    assert "overpriced" not in cats


def test_thin_market_flagged():
    r = _healthy_result()
    band = _band(subject_pps=1_000, p50=1_000, n=3, insufficient=True)
    cats = [x.category for x in extract_risks(r, band)]
    assert "thin_market" in cats


def test_overpriced_severity_capped_when_sample_insufficient():
    """표본이 부족하면 격차가 20%를 넘어도 high로 올리지 않는다.

    비교 기준(중위값) 자체가 소수 표본이라 불안정하므로, '고평가'라는
    확정적 high 판정을 내릴 근거가 부족하다. 이걸 안 걸러내면 severity
    정렬에서 thin_market(medium)의 실제 신뢰도 경고가 top-3 밖으로 밀려난다.
    """
    r = _healthy_result()
    # 표본 충분(n=20)한 test_overpriced_from_market_band와 동일한 25% 격차.
    thin_band = _band(subject_pps=1_250, p50=1_000, n=3, insufficient=True)
    over = next(x for x in extract_risks(r, thin_band) if x.category == "overpriced")
    assert over.severity == "medium"


def test_overpriced_message_reflects_actual_sample_size_when_thin():
    """표본 부족 여부에 따라 overpriced 메시지의 표본 정보가 실제 값과 일치해야 한다."""
    r = _healthy_result()

    thin_band = _band(subject_pps=1_250, p50=1_000, n=3, insufficient=True)
    over_thin = next(
        x for x in extract_risks(r, thin_band) if x.category == "overpriced"
    )
    assert "3건" in over_thin.message

    ample_band = _band(subject_pps=1_250, p50=1_000, n=20, insufficient=False)
    over_ample = next(
        x for x in extract_risks(r, ample_band) if x.category == "overpriced"
    )
    assert "20건" in over_ample.message
    # 표본이 충분할 때와 부족할 때 메시지가 서로 달라야 한다 (신뢰도 정보 유실 방지).
    assert over_thin.message != over_ample.message


def test_location_context_risks():
    r = _healthy_result()
    loc = LocationContext(
        integration_top_pct=70, competition_label="매우 높음", same_category_count=12
    )
    cats = [x.category for x in extract_risks(r, None, loc)]
    assert "location_access" in cats
    assert "location_competition" in cats


def test_healthy_result_few_risks():
    risks = extract_risks(_healthy_result())
    assert all(r.severity != "critical" for r in risks)


def test_levers_reference_detected_risks():
    r = _weak_result()
    band = _band(subject_pps=1_300, p50=1_000)
    risks = extract_risks(r, band)
    levers = suggest_levers(r, band, risks)
    assert any("매입가" in lv for lv in levers)
    assert any("대출" in lv for lv in levers)


def test_explain_rule_based_structure():
    r = _weak_result()
    band = _band(subject_pps=1_300, p50=1_000)
    # 키를 비워 rule-based 경로를 결정론적으로 검증 (네트워크 호출 없음)
    with patch("app.services.profitability_explain.settings") as mock_settings:
        mock_settings.openai_api_key = ""
        out = asyncio.run(explain_profitability(r, band))
    assert out.explanation_source == "rule"
    assert len(out.risks) <= 3
    assert "**한 줄 요약**" in out.explanation
    assert "**핵심 위험**" in out.explanation
    assert "**개선·협상 포인트**" in out.explanation
    # headline에 Cap Rate 포함
    assert "Cap Rate" in out.headline


def test_metric_lines_omit_values_that_are_missing():
    """없는 지표는 사실 블록에서 줄째로 빠져야 한다.

    전에는 'IRR: None%'가 그대로 실려, LLM이 "IRR이 None%로 표시되어 수익성
    판단의 핵심 지표가 비어 있습니다"라고 사용자에게 적었다.
    """
    from types import SimpleNamespace

    from app.services.profitability_explain import metric_lines

    lines = metric_lines(SimpleNamespace(
        cap_rate_total_cost_pct=4.2, cap_rate_noi_pct=4.6,
        cash_on_cash_pct=None, dscr=None,
        irr_pct=None, irr_no_appreciation_pct=None,
    ))
    joined = "\n".join(lines)
    assert "None" not in joined, joined
    assert "IRR" not in joined and "DSCR" not in joined

    # 값이 있으면 그대로 싣는다.
    lines = metric_lines(SimpleNamespace(
        cap_rate_total_cost_pct=4.2, cap_rate_noi_pct=4.6,
        cash_on_cash_pct=1.6, dscr=1.2,
        irr_pct=7.5, irr_no_appreciation_pct=3.1,
    ))
    joined = "\n".join(lines)
    assert "IRR: 7.5%" in joined and "매각차익 제외 3.1%" in joined
    assert "DSCR: 1.2" in joined
