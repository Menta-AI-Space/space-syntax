"""profitability 계산 엔진 단위 테스트 (손계산 대조)."""

import pytest

from app.services.profitability import (
    ExitInputs,
    LoanInputs,
    PurchaseInputs,
    RentalInputs,
    analyze_profitability,
    irr,
    loan_balance_after,
    monthly_payment,
)


def test_monthly_payment_amortizing():
    # 4억 @4.5% 20년 원리금균등 ≈ 253.06만/월
    pmt = monthly_payment(40_000, 0.045, 20, interest_only=False)
    assert pmt == pytest.approx(253.06, rel=1e-3)


def test_monthly_payment_interest_only():
    assert monthly_payment(40_000, 0.045, 20, interest_only=True) == pytest.approx(150.0)


def test_loan_balance_full_term_zero():
    bal = loan_balance_after(40_000, 0.045, 20, False, 240)
    assert bal == pytest.approx(0.0, abs=1e-6)


def test_loan_balance_interest_only_constant():
    assert loan_balance_after(40_000, 0.045, 20, True, 120) == 40_000


def test_irr_simple():
    # -100 → +110 = 10%
    assert irr([-100.0, 110.0]) == pytest.approx(0.10, abs=1e-6)
    assert irr([-100.0, -10.0]) is None
    assert irr([100.0, 10.0]) is None


def _base_case():
    purchase = PurchaseInputs(
        price_manwon=100_000,          # 10억
        acquisition_tax_manwon=None,   # → 4,600 자동
        broker_fee_manwon=900,
        legal_fee_manwon=100,
        renovation_manwon=3_000,
        other_initial_manwon=400,
    )
    rental = RentalInputs(
        deposit_manwon=10_000,
        monthly_rent_manwon=400,
        vacancy_rate=0.05,
        credit_loss_rate=0.01,
        owner_opex_monthly_manwon=50,
        annual_repair_reserve_manwon=240,
    )
    loan = LoanInputs(principal_manwon=40_000, annual_rate=0.045, term_years=20)
    return purchase, rental, loan


def test_first_year_metrics_hand_computed():
    purchase, rental, loan = _base_case()
    r = analyze_profitability(purchase, rental, loan)

    assert r.total_cost_manwon == pytest.approx(109_000)
    assert r.equity_manwon == pytest.approx(59_000)  # 보증금 10,000이 자기자본 차감
    assert r.gross_potential_income_manwon == pytest.approx(4_800)
    assert r.effective_gross_income_manwon == pytest.approx(4_512)
    assert r.operating_expenses_manwon == pytest.approx(840)
    assert r.noi_manwon == pytest.approx(3_672)
    assert r.annual_debt_service_manwon == pytest.approx(3_036.7, rel=1e-3)
    assert r.btcf_manwon == pytest.approx(635.3, rel=1e-2)
    assert r.gross_yield_pct == pytest.approx(4.8)
    assert r.cap_rate_noi_pct == pytest.approx(3.67, abs=0.01)
    assert r.cap_rate_total_cost_pct == pytest.approx(3.37, abs=0.01)
    assert r.cash_on_cash_pct == pytest.approx(1.08, abs=0.01)
    assert r.dscr == pytest.approx(1.21, abs=0.01)
    assert r.break_even_occupancy_pct == pytest.approx(80.8, abs=0.1)
    assert any("취득세" in a for a in r.assumptions)
    assert r.warnings == []


def test_scenarios_and_sensitivity_shape():
    purchase, rental, loan = _base_case()
    r = analyze_profitability(purchase, rental, loan)

    assert [s.label for s in r.scenarios] == ["기본", "보수", "낙관"]
    base, cons, opt = r.scenarios
    assert base.cash_on_cash_pct == r.cash_on_cash_pct
    assert cons.vacancy_rate_pct == pytest.approx(10.0)
    assert cons.loan_rate_pct == pytest.approx(5.5)
    # 보수 시나리오는 항상 기본보다 나쁘다
    assert cons.btcf_manwon < base.btcf_manwon < opt.btcf_manwon

    assert len(r.sensitivity_coc_pct) == 3
    assert all(len(row) == 3 for row in r.sensitivity_coc_pct)
    # 공실↑·금리↑ 방향으로 단조 감소
    assert r.sensitivity_coc_pct[0][0] > r.sensitivity_coc_pct[2][2]


def test_zero_equity_warns_and_skips_ratios():
    purchase = PurchaseInputs(price_manwon=100_000, acquisition_tax_manwon=0)
    rental = RentalInputs(deposit_manwon=20_000, monthly_rent_manwon=300)
    loan = LoanInputs(principal_manwon=90_000, annual_rate=0.05, term_years=30)
    r = analyze_profitability(purchase, rental, loan)
    assert r.equity_manwon <= 0
    assert r.cash_on_cash_pct is None
    assert any("자기자본" in w for w in r.warnings)


def test_no_loan_dscr_none():
    purchase = PurchaseInputs(price_manwon=10_000, acquisition_tax_manwon=0)
    rental = RentalInputs(monthly_rent_manwon=50, vacancy_rate=0.0)
    r = analyze_profitability(purchase, rental)
    assert r.dscr is None
    assert r.annual_debt_service_manwon == 0
    assert r.cash_on_cash_pct == pytest.approx(6.0)


def test_hold_analysis_irr_decomposition():
    # 무대출·무성장·매각비 0·보증금 0, 1년 보유: IRR = NOI/자기자본 = 6%
    purchase = PurchaseInputs(price_manwon=10_000, acquisition_tax_manwon=0)
    rental = RentalInputs(monthly_rent_manwon=50, vacancy_rate=0.0)
    exit_in = ExitInputs(
        hold_years=1, sale_price_growth_rate=0.0, sale_cost_rate=0.0,
        discount_rate=0.05,
    )
    r = analyze_profitability(purchase, rental, exit_in=exit_in)
    assert r.irr_pct == pytest.approx(6.0, abs=0.01)
    assert r.irr_no_appreciation_pct == pytest.approx(6.0, abs=0.01)
    assert r.capital_gain_contribution_pct == pytest.approx(0.0, abs=0.01)
    assert r.sale_price_manwon == pytest.approx(10_000)
    assert r.net_sale_proceeds_manwon == pytest.approx(10_000)
    assert r.npv_manwon == pytest.approx(-10_000 + 10_600 / 1.05, abs=0.5)

    # 매각가 5% 상승 가정 → IRR 11%, 자본수익 기여 5%p
    exit_up = ExitInputs(hold_years=1, sale_price_growth_rate=0.05, sale_cost_rate=0.0)
    r2 = analyze_profitability(purchase, rental, exit_in=exit_up)
    assert r2.irr_pct == pytest.approx(11.0, abs=0.01)
    assert r2.capital_gain_contribution_pct == pytest.approx(5.0, abs=0.01)


def test_deposit_returned_at_sale():
    # 보증금은 매각 정산에서 차감된다
    purchase = PurchaseInputs(price_manwon=10_000, acquisition_tax_manwon=0)
    rental = RentalInputs(deposit_manwon=2_000, monthly_rent_manwon=50, vacancy_rate=0.0)
    exit_in = ExitInputs(hold_years=1, sale_price_growth_rate=0.0, sale_cost_rate=0.0)
    r = analyze_profitability(purchase, rental, exit_in=exit_in)
    assert r.equity_manwon == pytest.approx(8_000)
    assert r.net_sale_proceeds_manwon == pytest.approx(8_000)  # 10,000 - 보증금 2,000


def test_yearly_rows_growth():
    purchase, rental, loan = _base_case()
    exit_in = ExitInputs(
        hold_years=3, rent_growth_rate=0.02, expense_growth_rate=0.03,
        sale_price_growth_rate=0.01,
    )
    r = analyze_profitability(purchase, rental, loan, exit_in)
    assert len(r.yearly) == 3
    assert r.yearly[1].effective_gross_income == pytest.approx(
        r.yearly[0].effective_gross_income * 1.02, rel=1e-3
    )
    assert r.yearly[1].operating_expenses == pytest.approx(
        r.yearly[0].operating_expenses * 1.03, rel=1e-3
    )
    # 대출 잔액은 해마다 감소
    assert r.yearly[0].loan_balance_end > r.yearly[1].loan_balance_end
    assert r.total_operating_cashflow_manwon == pytest.approx(
        sum(row.btcf for row in r.yearly), abs=0.5
    )
