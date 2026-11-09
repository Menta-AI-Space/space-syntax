import type { SiteScoreResponse } from "./api";

/**
 * 지표마다 "높은 게 좋은가"를 한 곳에서 정한다.
 *
 * 대부분의 지표는 점수가 높을수록 좋지만, 경쟁·포화·잠식은 반대다. 이 방향을
 * 화면마다 따로 알고 있으면 한쪽만 맞고 다른 쪽은 틀린다 — 실제로 후보지
 * 비교표는 경쟁 칸에서 **가장 경쟁이 심한 후보**에 "항목 최고" 표시를 달고
 * 있었다(내림차순 최댓값을 승자로 골랐다).
 *
 * 이 목록은 backend/app/services/industry_kpis.py의 _KPI_REGISTRY와 맞춰야
 * 한다. 거기서 _kpi_saturation·_kpi_cafe_saturation·_kpi_cannibalization_risk로
 * 계산되는 키는 높을수록 나쁘다(포화·잠식). 반면 같은 이름에 "경쟁"이 들어가도
 * _kpi_competition_distance로 계산되는 키(competition_distance,
 * service_competition)는 "최근접 동일업종까지 거리"라 높을수록 좋다 — 절대
 * 여기 넣지 말 것.
 *
 * 예전엔 같은 업종 키를 "same_specialty"·"same_subject_academies"처럼
 * 추측해서 넣어 실제 응답 키(same_specialty_supply·same_subject_competition)와
 * 어긋났고, restaurant·lodging·professional_office·fitness·leisure·
 * retail_specialty·pharmacy 6개 업종의 경쟁 지표와 franchise 모드의
 * market_overlap은 아예 목록에 없어 9개 업종에서 방향 반전이 전혀 적용되지
 * 않았다 — 경쟁이 극심한 자리가 초록 막대로 표시됐다.
 */
export const HIGHER_IS_WORSE = new Set([
  "competition",
  "cafe_saturation",
  "cannibalization_risk",
  "competition_pressure", // restaurant
  "same_specialty_supply", // clinic
  "same_subject_competition", // academy
  "lodging_competition", // lodging
  "professional_competition", // professional_office
  "fitness_competition", // fitness
  "leisure_competition", // leisure
  "retail_competition", // retail_specialty
  "pharmacy_competition", // pharmacy
  "market_overlap", // franchise (cannibalization_risk의 별칭)
]);

export function isInverseMetric(key: string): boolean {
  return HIGHER_IS_WORSE.has(key);
}

/**
 * 0~100 점수를 서울 전체 대비 위치로 옮긴 "상위 N%" 문구.
 *
 * 경쟁·포화처럼 높을수록 나쁜 지표는 방향을 밝히지 않으면 정반대로 읽힌다 —
 * 빨간 막대 옆에 "상위 0.1%"만 적혀 있으면 아주 좋은 자리로 보인다.
 */
export function rankTopPctText(
  score: number,
  rankTopPct: number | null | undefined,
  inverse = false,
): string {
  const pct = rankTopPct != null ? rankTopPct : Math.max(0.1, 100 - score);
  const shown = pct < 10 ? pct.toFixed(1) : String(Math.round(pct));
  return inverse ? `불리한 쪽 상위 ${shown}%` : `상위 ${shown}%`;
}

/**
 * 이 지표가 "얼마나 좋은가"(0~100). 경쟁·포화처럼 높을수록 나쁜 지표는 뒤집는다.
 * 점수끼리 크기를 비교하려면 방향을 먼저 맞춰야 한다.
 */
export function metricGoodness(key: string, score: number): number {
  return isInverseMetric(key) ? 100 - score : score;
}

export interface FactorChip { key: string; label: string; value: string }

/** 핵심 근거로 쓸 만한 최소 수준. 이보다 낮으면 좋은 점수의 근거가 아니다. */
const FACTOR_MIN_GOODNESS = 50;

/**
 * 종합점수 옆의 "핵심 근거" 칩.
 *
 * 예전에는 세부 지표를 **점수 크기순**으로 잘라 썼다. 경쟁 밀도는 높을수록
 * 나쁜데 그 점수가 크다는 이유로 언제나 상위에 올라, 서울 주요 상권 다섯
 * 곳을 재 보니 전부 "핵심 근거: 경쟁 밀도 100점 · 매우 높음"이 붙었다 —
 * 경쟁이 극심한 것이 점수가 좋은 이유처럼 보였다.
 *
 * 방향을 맞춘 뒤(metricGoodness) 고르고, 값은 지표가 스스로 쓰는 표현
 * (display: "상위 8.6%", "매우 낮음")을 우선 보여 준다.
 */
export function topFactorChips(
  subscores: { key: string; label: string; score?: number | null; display?: string; rank_top_pct?: number | null }[],
  limit = 3,
): FactorChip[] {
  return subscores
    .filter((s): s is typeof s & { score: number } => typeof s.score === "number")
    .map((s) => ({ s, goodness: metricGoodness(s.key, s.score) }))
    .filter((x) => x.goodness >= FACTOR_MIN_GOODNESS)
    .sort((a, b) => b.goodness - a.goodness)
    .slice(0, limit)
    .map(({ s }) => ({
      key: s.key,
      label: s.label,
      value:
        s.display ||
        (s.rank_top_pct != null ? `상위 ${s.rank_top_pct}%` : `${Math.round(s.score)}점`),
    }));
}

export type CompareRow = { candidate: { id: string; lat: number; lng: number }; data: SiteScoreResponse };

/**
 * 비교 칸 하나의 값과 "어느 쪽이 이기는가".
 *
 * 예전에는 rank_top_pct가 있으면 낮을수록 좋고, 없으면 점수가 높을수록 좋다고
 * 봤다. 경쟁 밀도는 rank_top_pct가 없고 점수가 높을수록 **나쁜** 지표라, 이
 * 규칙 아래에서는 가장 경쟁이 심한 후보가 "항목 최고"로 표시됐다. 방향은
 * 어느 필드가 왔는지가 아니라 지표가 무엇인지로 정해야 한다.
 */
export function subscoreValue(
  data: SiteScoreResponse,
  key: string,
): { v: number; better: "high" | "low" } | null {
  const ss = data.subscores?.find((s) => s.key === key);
  if (!ss) return null;
  if (ss.rank_top_pct != null) return { v: ss.rank_top_pct, better: "low" };
  if (ss.score != null) return { v: ss.score, better: isInverseMetric(key) ? "low" : "high" };
  return null;
}

/**
 * 이 칸에서 가장 앞서는 후보(들). 동점이면 둘 다, 전부 같거나 비교 가능한 후보가
 * 둘 미만이면 아무도 표시하지 않는다 — 강조는 늘 "이게 눈에 띈다"는 뜻이어야 한다.
 */
export function leadIdsForMetric(rows: CompareRow[], key: string): Set<string> {
  const vals = rows
    .map((r) => ({ id: r.candidate.id, val: subscoreValue(r.data, key) }))
    .filter((x): x is { id: string; val: { v: number; better: "high" | "low" } } => x.val != null);
  if (vals.length < 2) return new Set();
  const better = vals[0].val.better;
  const homogeneous = vals.filter((x) => x.val.better === better);
  if (new Set(homogeneous.map((x) => x.val.v)).size <= 1) return new Set();
  const best =
    better === "low"
      ? Math.min(...homogeneous.map((x) => x.val.v))
      : Math.max(...homogeneous.map((x) => x.val.v));
  return new Set(homogeneous.filter((x) => x.val.v === best).map((x) => x.id));
}
