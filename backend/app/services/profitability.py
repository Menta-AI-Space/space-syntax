"""부동산 투자 수익성 계산 엔진 (매수자·임대인 관점, 순수 계산 · DB/외부호출 없음).

원칙:
- 모든 금액은 만원, 모든 비율 입력은 소수(0.05 = 5%), 비율 출력은 %값.
- 임차보증금은 수익이 아니라 반환 의무: 자기자본을 줄이는 금융원천으로만 반영하고
  NOI에 넣지 않으며 매각 정산에서 차감한다.
- 운영수익과 자본수익(매각차익)을 분리해 보고한다: IRR을 매각가 상승 가정 포함/제외
  두 번 계산해 자본수익 기여분을 드러낸다.
- 판단(위험 평가·설명)은 여기서 하지 않는다. 숫자와 구조적 경고만 반환한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 상가·오피스텔(주택 외) 유상취득 표준 취득세율: 취득세 4% + 농특세 0.2% + 지방교육세 0.4%
DEFAULT_ACQUISITION_TAX_RATE = 0.046

MONTHS = 12


@dataclass
class PurchaseInputs:
    price_manwon: float
    acquisition_tax_manwon: float | None = None  # None → 4.6% 자동 추정
    broker_fee_manwon: float = 0.0
    legal_fee_manwon: float = 0.0
    renovation_manwon: float = 0.0
    other_initial_manwon: float = 0.0


@dataclass
class RentalInputs:
    deposit_manwon: float = 0.0
    monthly_rent_manwon: float = 0.0
    other_monthly_income_manwon: float = 0.0
    vacancy_rate: float = 0.05
    credit_loss_rate: float = 0.0
    owner_opex_monthly_manwon: float = 0.0  # 소유자 부담 관리비 등
    annual_repair_reserve_manwon: float = 0.0


@dataclass
class LoanInputs:
    principal_manwon: float = 0.0
    annual_rate: float = 0.0
    term_years: int = 0
    interest_only: bool = False


@dataclass
class ExitInputs:
    hold_years: int = 5
    rent_growth_rate: float = 0.0
    expense_growth_rate: float = 0.0
    sale_price_growth_rate: float = 0.0  # 연복리 매각가 상승 가정
    sale_cost_rate: float = 0.01
    discount_rate: float | None = None  # NPV 할인율 (None이면 NPV 생략)


@dataclass
class YearRow:
    year: int
    effective_gross_income: float
    operating_expenses: float
    noi: float
    debt_service: float
    btcf: float
    loan_balance_end: float


@dataclass
class ScenarioMetrics:
    label: str
    vacancy_rate_pct: float
    loan_rate_pct: float
    noi_manwon: float
    btcf_manwon: float
    cash_on_cash_pct: float | None
    dscr: float | None


@dataclass
class ProfitabilityResult:
    # 투자 구조
    total_cost_manwon: float
    equity_manwon: float
    # 1차년도 손익
    gross_potential_income_manwon: float
    effective_gross_income_manwon: float
    operating_expenses_manwon: float
    noi_manwon: float
    annual_debt_service_manwon: float
    btcf_manwon: float
    monthly_payment_manwon: float
    # 지표 (%)
    gross_yield_pct: float
    cap_rate_noi_pct: float
    cap_rate_total_cost_pct: float
    cash_on_cash_pct: float | None
    dscr: float | None
    break_even_occupancy_pct: float | None
    # 시나리오·민감도
    scenarios: list[ScenarioMetrics] = field(default_factory=list)
    sensitivity_vacancy_pct: list[float] = field(default_factory=list)
    sensitivity_rate_pct: list[float] = field(default_factory=list)
    sensitivity_coc_pct: list[list[float | None]] = field(default_factory=list)
    # 장기 보유 (exit 입력 시)
    yearly: list[YearRow] = field(default_factory=list)
    sale_price_manwon: float | None = None
    net_sale_proceeds_manwon: float | None = None
    total_operating_cashflow_manwon: float | None = None
    irr_pct: float | None = None
    irr_no_appreciation_pct: float | None = None  # 매각가=매입가 가정 (운영수익만)
    capital_gain_contribution_pct: float | None = None  # irr - irr_no_appreciation
    npv_manwon: float | None = None
    # 메타
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def monthly_payment(
    principal: float, annual_rate: float, term_years: int, interest_only: bool
) -> float:
    """월 상환액. 원리금균등 또는 이자만."""
    if principal <= 0:
        return 0.0
    if interest_only or term_years <= 0:
        return principal * annual_rate / MONTHS
    r = annual_rate / MONTHS
    n = term_years * MONTHS
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** -n)


def loan_balance_after(
    principal: float, annual_rate: float, term_years: int,
    interest_only: bool, months_paid: int,
) -> float:
    """months_paid 회 납부 후 잔액."""
    if principal <= 0:
        return 0.0
    if interest_only or term_years <= 0:
        return principal
    r = annual_rate / MONTHS
    n = term_years * MONTHS
    m = min(months_paid, n)
    if r == 0:
        return principal * (1 - m / n)
    pmt = monthly_payment(principal, annual_rate, term_years, False)
    return principal * (1 + r) ** m - pmt * (((1 + r) ** m - 1) / r)


def irr(cashflows: list[float]) -> float | None:
    """이분법 IRR. 부호 변화가 없으면 None."""
    if not cashflows or all(cf >= 0 for cf in cashflows) or all(
        cf <= 0 for cf in cashflows
    ):
        return None

    def npv(rate: float) -> float:
        return sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def _first_year(
    rental: RentalInputs, loan: LoanInputs, rate_override: float | None = None,
    vacancy_override: float | None = None,
) -> tuple[float, float, float, float, float, float]:
    """(GPI, EGI, opex, NOI, ADS, BTCF) — 만원/년."""
    vacancy = rental.vacancy_rate if vacancy_override is None else vacancy_override
    rate = loan.annual_rate if rate_override is None else rate_override
    gpi = (rental.monthly_rent_manwon + rental.other_monthly_income_manwon) * MONTHS
    loss = min(max(vacancy + rental.credit_loss_rate, 0.0), 1.0)
    egi = gpi * (1 - loss)
    opex = (
        rental.owner_opex_monthly_manwon * MONTHS
        + rental.annual_repair_reserve_manwon
    )
    noi = egi - opex
    ads = monthly_payment(
        loan.principal_manwon, rate, loan.term_years, loan.interest_only
    ) * MONTHS
    return gpi, egi, opex, noi, ads, noi - ads


def _hold_analysis(
    result: ProfitabilityResult,
    purchase: PurchaseInputs,
    rental: RentalInputs,
    loan: LoanInputs,
    exit_in: ExitInputs,
) -> None:
    """연도별 현금흐름 + 매각 정산 + IRR(운영/자본 분리)."""
    equity = result.equity_manwon
    years = max(1, exit_in.hold_years)

    btcfs: list[float] = []
    for y in range(1, years + 1):
        rent_f = (1 + exit_in.rent_growth_rate) ** (y - 1)
        exp_f = (1 + exit_in.expense_growth_rate) ** (y - 1)
        gpi = (
            rental.monthly_rent_manwon + rental.other_monthly_income_manwon
        ) * MONTHS * rent_f
        loss = min(max(rental.vacancy_rate + rental.credit_loss_rate, 0.0), 1.0)
        egi = gpi * (1 - loss)
        opex = (
            rental.owner_opex_monthly_manwon * MONTHS
            + rental.annual_repair_reserve_manwon
        ) * exp_f
        noi = egi - opex
        ads = result.annual_debt_service_manwon
        balance = loan_balance_after(
            loan.principal_manwon, loan.annual_rate, loan.term_years,
            loan.interest_only, y * MONTHS,
        )
        btcf = noi - ads
        btcfs.append(btcf)
        result.yearly.append(
            YearRow(
                year=y,
                effective_gross_income=round(egi, 1),
                operating_expenses=round(opex, 1),
                noi=round(noi, 1),
                debt_service=round(ads, 1),
                btcf=round(btcf, 1),
                loan_balance_end=round(balance, 1),
            )
        )

    def sale_flow(sale_price: float) -> float:
        costs = sale_price * exit_in.sale_cost_rate
        balance = loan_balance_after(
            loan.principal_manwon, loan.annual_rate, loan.term_years,
            loan.interest_only, years * MONTHS,
        )
        return sale_price - costs - balance - rental.deposit_manwon

    sale_price = purchase.price_manwon * (
        (1 + exit_in.sale_price_growth_rate) ** years
    )
    net_sale = sale_flow(sale_price)
    result.sale_price_manwon = round(sale_price, 1)
    result.net_sale_proceeds_manwon = round(net_sale, 1)
    result.total_operating_cashflow_manwon = round(sum(btcfs), 1)

    flows = [-equity] + btcfs[:-1] + [btcfs[-1] + net_sale]
    irr_full = irr(flows)

    # 자본수익 분리: 매각가 = 매입가(상승 0) 가정의 IRR과 비교
    net_sale_flat = sale_flow(purchase.price_manwon)
    flows_flat = [-equity] + btcfs[:-1] + [btcfs[-1] + net_sale_flat]
    irr_flat = irr(flows_flat)

    result.irr_pct = round(irr_full * 100, 2) if irr_full is not None else None
    result.irr_no_appreciation_pct = (
        round(irr_flat * 100, 2) if irr_flat is not None else None
    )
    if irr_full is not None and irr_flat is not None:
        result.capital_gain_contribution_pct = round((irr_full - irr_flat) * 100, 2)

    if exit_in.discount_rate is not None:
        r = exit_in.discount_rate
        result.npv_manwon = round(
            sum(cf / (1 + r) ** t for t, cf in enumerate(flows)), 1
        )


def analyze_profitability(
    purchase: PurchaseInputs,
    rental: RentalInputs,
    loan: LoanInputs | None = None,
    exit_in: ExitInputs | None = None,
) -> ProfitabilityResult:
    loan = loan or LoanInputs()
    assumptions: list[str] = []
    warnings: list[str] = []

    tax = purchase.acquisition_tax_manwon
    if tax is None:
        tax = purchase.price_manwon * DEFAULT_ACQUISITION_TAX_RATE
        assumptions.append(
            f"취득세 미입력 → 매입가의 {DEFAULT_ACQUISITION_TAX_RATE:.1%} 자동 추정"
        )

    total_cost = (
        purchase.price_manwon + tax + purchase.broker_fee_manwon
        + purchase.legal_fee_manwon + purchase.renovation_manwon
        + purchase.other_initial_manwon
    )
    # 보증금은 수익이 아닌 반환 의무 — 자기자본을 줄이는 금융원천으로만 반영
    equity = total_cost - loan.principal_manwon - rental.deposit_manwon
    if equity <= 0:
        warnings.append(
            "대출+보증금이 총투자원가 이상입니다 — 자기자본이 0 이하라 "
            "CoC·IRR을 계산할 수 없습니다."
        )
    if loan.principal_manwon > purchase.price_manwon:
        warnings.append("대출원금이 매입가를 초과합니다.")

    gpi, egi, opex, noi, ads, btcf = _first_year(rental, loan)
    pmt = monthly_payment(
        loan.principal_manwon, loan.annual_rate, loan.term_years, loan.interest_only
    )

    price = purchase.price_manwon
    rent_annual = rental.monthly_rent_manwon * MONTHS

    def pct(num: float, den: float) -> float:
        return round(num / den * 100, 2) if den > 0 else 0.0

    result = ProfitabilityResult(
        total_cost_manwon=round(total_cost, 1),
        equity_manwon=round(equity, 1),
        gross_potential_income_manwon=round(gpi, 1),
        effective_gross_income_manwon=round(egi, 1),
        operating_expenses_manwon=round(opex, 1),
        noi_manwon=round(noi, 1),
        annual_debt_service_manwon=round(ads, 1),
        btcf_manwon=round(btcf, 1),
        monthly_payment_manwon=round(pmt, 1),
        gross_yield_pct=pct(rent_annual, price),
        cap_rate_noi_pct=pct(noi, price),
        cap_rate_total_cost_pct=pct(noi, total_cost),
        cash_on_cash_pct=round(btcf / equity * 100, 2) if equity > 0 else None,
        dscr=round(noi / ads, 2) if ads > 0 else None,
        break_even_occupancy_pct=(
            round(
                (opex + ads - rental.other_monthly_income_manwon * MONTHS)
                / rent_annual * 100,
                1,
            )
            if rent_annual > 0
            else None
        ),
        assumptions=assumptions,
        warnings=warnings,
    )

    # 기본/보수/낙관 시나리오 (공실률·금리 조정)
    v, r = rental.vacancy_rate, loan.annual_rate
    for label, dv, dr in (
        ("기본", 0.0, 0.0),
        ("보수", +0.05, +0.01),
        ("낙관", -0.03, -0.005),
    ):
        sv = min(max(v + dv, 0.0), 1.0)
        sr = max(r + dr, 0.0) if loan.principal_manwon > 0 else 0.0
        _, _, _, s_noi, s_ads, s_btcf = _first_year(
            rental, loan, rate_override=sr, vacancy_override=sv
        )
        result.scenarios.append(
            ScenarioMetrics(
                label=label,
                vacancy_rate_pct=round(sv * 100, 1),
                loan_rate_pct=round(sr * 100, 2),
                noi_manwon=round(s_noi, 1),
                btcf_manwon=round(s_btcf, 1),
                cash_on_cash_pct=(
                    round(s_btcf / equity * 100, 2) if equity > 0 else None
                ),
                dscr=round(s_noi / s_ads, 2) if s_ads > 0 else None,
            )
        )

    # 공실률 × 금리 CoC 민감도
    vacancies = [min(max(v + d, 0.0), 1.0) for d in (0.0, 0.04, 0.09)]
    rates = (
        [max(r + d, 0.0) for d in (-0.01, 0.0, 0.01)]
        if loan.principal_manwon > 0
        else [0.0]
    )
    result.sensitivity_vacancy_pct = [round(x * 100, 1) for x in vacancies]
    result.sensitivity_rate_pct = [round(x * 100, 2) for x in rates]
    for sv in vacancies:
        row: list[float | None] = []
        for sr in rates:
            _, _, _, _, s_ads, s_btcf = _first_year(
                rental, loan, rate_override=sr, vacancy_override=sv
            )
            row.append(round(s_btcf / equity * 100, 2) if equity > 0 else None)
        result.sensitivity_coc_pct.append(row)

    if exit_in is not None and equity > 0:
        _hold_analysis(result, purchase, rental, loan, exit_in)

    return result
