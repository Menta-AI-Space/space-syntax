// 투자 수익성 탭 입력 상태·기본값·persistence·포맷 헬퍼.
// 저장 키/shape는 이 기능 전용(다른 탭과 분리).

export type NumOrEmpty = number | "";

export interface ProfitabilityInputs {
  // 물건/시장
  property_type: "commercial" | "officetel";
  building_type: "집합" | "일반";
  floor: string;
  area_sqm: NumOrEmpty;
  // 1) 매입
  price_manwon: NumOrEmpty;
  acquisition_tax_manwon: NumOrEmpty; // 비우면 4.6% 자동
  broker_fee_manwon: NumOrEmpty;
  legal_fee_manwon: NumOrEmpty;
  renovation_manwon: NumOrEmpty;
  other_initial_manwon: NumOrEmpty;
  // 2) 임대
  deposit_manwon: NumOrEmpty;
  monthly_rent_manwon: NumOrEmpty;
  other_monthly_income_manwon: NumOrEmpty;
  vacancy_rate_pct: NumOrEmpty;
  credit_loss_rate_pct: NumOrEmpty;
  owner_opex_monthly_manwon: NumOrEmpty;
  annual_repair_reserve_manwon: NumOrEmpty;
  // 3) 대출
  loan_principal_manwon: NumOrEmpty;
  loan_annual_rate_pct: NumOrEmpty;
  loan_term_years: NumOrEmpty;
  loan_interest_only: boolean;
  // 4) 보유·매각
  hold_years: NumOrEmpty;
  rent_growth_rate_pct: NumOrEmpty;
  expense_growth_rate_pct: NumOrEmpty;
  sale_price_growth_rate_pct: NumOrEmpty;
  sale_cost_rate_pct: NumOrEmpty;
  discount_rate_pct: NumOrEmpty;
}

const STORAGE_KEY = "space_syntax_profitability_inputs";

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export function defaultProfitabilityInputs(): ProfitabilityInputs {
  const base: ProfitabilityInputs = {
    property_type: "commercial",
    building_type: "집합",
    floor: "1",
    area_sqm: 60,
    price_manwon: 90000,
    acquisition_tax_manwon: "",
    broker_fee_manwon: 810,
    legal_fee_manwon: 100,
    renovation_manwon: 2000,
    other_initial_manwon: 0,
    deposit_manwon: 10000,
    monthly_rent_manwon: 350,
    other_monthly_income_manwon: 0,
    vacancy_rate_pct: 5,
    credit_loss_rate_pct: 0,
    owner_opex_monthly_manwon: 30,
    annual_repair_reserve_manwon: 200,
    loan_principal_manwon: 45000,
    loan_annual_rate_pct: 4.5,
    loan_term_years: 20,
    loan_interest_only: false,
    hold_years: 5,
    rent_growth_rate_pct: 2,
    expense_growth_rate_pct: 2,
    sale_price_growth_rate_pct: 2,
    sale_cost_rate_pct: 1,
    discount_rate_pct: 5,
  };
  if (!isBrowser()) return base;
  try {
    const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
    return { ...base, ...saved };
  } catch {
    return base;
  }
}

export function saveProfitabilityInputs(data: ProfitabilityInputs): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    /* ignore */
  }
}

/** NumOrEmpty → number (빈 값은 fallback). */
export function num(v: NumOrEmpty, fallback = 0): number {
  return v === "" || v === null || v === undefined ? fallback : Number(v);
}

/** 만원 → 사람이 읽는 억/만 표기. */
export function fmtManwon(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const v = Math.round(n);
  const eok = Math.trunc(Math.abs(v) / 10000);
  const man = Math.abs(v) % 10000;
  const sign = v < 0 ? "-" : "";
  if (eok > 0) return `${sign}${eok}억${man > 0 ? ` ${man.toLocaleString("ko-KR")}만` : ""}`;
  return `${sign}${man.toLocaleString("ko-KR")}만`;
}

export function fmtPct(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined) return "—";
  return `${Number(n).toFixed(digits)}%`;
}

/** ₩/㎡ → 백만원/㎡ 요약. */
export function fmtPricePerSqm(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `${(Number(n) / 1_000_000).toFixed(1)}백만/㎡`;
}

export function severityLabel(sev: string): string {
  if (sev === "critical") return "심각";
  if (sev === "high") return "주의";
  return "참고";
}

export function severityClass(sev: string): string {
  if (sev === "critical") return "prof-risk--critical";
  if (sev === "high") return "prof-risk--high";
  return "prof-risk--medium";
}

/** DSCR 색상 힌트: <1.0 위험, <1.2 주의, 그 외 양호. */
export function dscrClass(dscr: number | null | undefined): string {
  if (dscr === null || dscr === undefined) return "";
  if (dscr < 1.0) return "prof-kpi--danger";
  if (dscr < 1.2) return "prof-kpi--warn";
  return "prof-kpi--good";
}

/**
 * 검토 매입 단가가 주변 실거래 표본에서 어디쯤인지 문장으로 말한다.
 *
 * 백엔드의 `subject_percentile`은 **표본 중 이보다 싼 거래의 비율**이다
 * (property_market.py: `below / total * 100`). 화면에는 "표본 상위 P25"라고만
 * 적혀 있었는데, 이건 "위에서 25%(=비싼 축)"으로 읽힌다 — 실제로는 정반대인
 * 싼 축이다. 매입 판단이 뒤집히는 오해라 숫자만 두지 않고 방향을 못 박는다.
 */
export function pricePercentileText(pct: number): string {
  const p = Math.max(0, Math.min(100, Math.round(pct)));
  if (p <= 25) return `주변 실거래의 ${100 - p}%가 이보다 비쌉니다 — 싼 편`;
  if (p >= 75) return `주변 실거래의 ${p}%가 이보다 쌉니다 — 비싼 편`;
  return `주변 실거래의 ${p}%가 이보다 쌉니다 — 중간 가격대`;
}
