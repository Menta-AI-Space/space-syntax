import type { SiteScoreResponse } from "@/lib/api";

/**
 * SiteScoreResponse.warnings — 기본 site_score 응답에 실려 오는 경고
 * (엔진 폴백·교통/상가 API 실패 등, backend/app/routers/site_score.py 참고).
 * AnalysisVisuals가 이 값을 한 번도 화면에 그리지 않아 폴백 상태가 조용히
 * 묻혔다. 문자열이 아니거나 빈 항목은 걸러내고, 지어내지 않는다.
 */
export function siteScoreWarnings(site: SiteScoreResponse | null | undefined): string[] {
  if (!site || !Array.isArray(site.warnings)) return [];
  return site.warnings.filter((w): w is string => typeof w === "string" && w.trim().length > 0);
}
