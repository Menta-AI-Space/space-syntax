import type { ProfitabilityResult } from "@/lib/api";

/**
 * calculation.warnings(자기자본 0 이하, 대출원금이 매입가 초과 등 계산 자체의
 * 위험 — backend/app/services/profitability.py의 analyze_profitability가 채움)와
 * 최상위 warnings(시장·임대료 참고치 조회 실패 등 부가 데이터 경고 —
 * backend/app/routers/profitability.py가 채움)는 서로 다른 경로에서 채워지는
 * 별개의 목록이다. 하나만 보여주면 계산 자체의 위험 경고가 화면에서 사라진다.
 */
export function profitabilityWarnings(
  data: { calculation: Pick<ProfitabilityResult, "warnings">; warnings: string[] },
): string[] {
  const calcWarnings = Array.isArray(data.calculation?.warnings) ? data.calculation.warnings : [];
  const topWarnings = Array.isArray(data.warnings) ? data.warnings : [];
  return Array.from(new Set([...calcWarnings, ...topWarnings]));
}
