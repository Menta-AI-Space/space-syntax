import type { LeaseSiteContext } from "./api.ts";

// LeaseAnalysisSection.tsx(대시보드)와 AgentChat.tsx(에이전트 채팅)가
// LeaseFeasibilityResponse.site_context에서 같은 항목을 뽑아 쓰도록 순수
// 함수로 분리한다 — 두 화면이 각자 파싱하면 한쪽만 고치고 다른 쪽을 놓치기
// 쉽다(실제로 지금까지 둘 다 site_context를 통째로 안 보여주고 있었다).
//
// 각 화면은 자기 색 톤 체계(AgentChat의 toneForKoreanLabel, 대시보드의
// intensityClass)로 toneLabel을 직접 매핑한다 — 여기서 톤을 미리 정해버리면
// 두 체계 중 하나에 맞춰야 해서 재사용이 깨진다.

export interface LeaseSiteContextTile {
  label: string;
  value: string;
  /** 하이라이트용 원본 한글 라벨(경쟁·잠식 강도). 없으면 중립적인 값(점수 등). */
  toneLabel?: string;
}

/** 종합점수·주변 경쟁·잠식 위험 — 값이 있는 항목만 만든다. */
export function buildLeaseSiteContextTiles(site: LeaseSiteContext | null | undefined): LeaseSiteContextTile[] {
  if (!site) return [];
  const tiles: LeaseSiteContextTile[] = [];
  if (site.total_score != null) {
    tiles.push({
      label: "입지 종합점수",
      value: site.grade ? `${Math.round(site.total_score)}점 · ${site.grade}` : `${Math.round(site.total_score)}점`,
    });
  }
  if (site.competition_label) {
    const count = site.same_category_count != null ? ` · 동일업종 ${site.same_category_count}곳` : "";
    tiles.push({ label: "주변 경쟁", value: `${site.competition_label}${count}`, toneLabel: site.competition_label });
  }
  if (site.cannibalization_label) {
    const risk = site.cannibalization_risk != null ? ` (${Math.round(site.cannibalization_risk)}점)` : "";
    tiles.push({ label: "잠식 위험", value: `${site.cannibalization_label}${risk}`, toneLabel: site.cannibalization_label });
  }
  return tiles;
}

/** 업종별(식당/카페) 적합 모델 상위 목록 — 둘 중 실제로 계산된 쪽만 채워져 있다. */
export function leaseBestModels(site: LeaseSiteContext | null | undefined): string[] {
  if (!site) return [];
  return [...(site.best_restaurant_models || []), ...(site.best_cafe_models || [])];
}
