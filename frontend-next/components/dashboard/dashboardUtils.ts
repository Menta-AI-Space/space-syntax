import type { SiteScoreResponse, TrendsResponse } from "@/lib/api";
// Shared constants/helpers ported from frontend/js/dashboard.js — kept as plain
// functions (no HTML escaping needed; JSX escapes text automatically).

export type TravelModeKey = "walk" | "drive" | "bike" | "moto";

export const ISO_MODE_LABELS: Record<string, string> = { walk: "도보", drive: "차도", bike: "자전거", moto: "오토바이" };
export const ISO_FILL = ["#bbf7d0", "#86efac", "#4ade80"];
export const ISO_STROKE = ["#166534", "#15803d", "#14532d"];
export const COMPETITORS_PER_PAGE = 6;

/**
 * 겹침 벤 다이어그램에서 두 원의 중심 거리.
 *
 * 예전에는 원 두 개를 고정 좌표로 그려서, 겹침이 5%든 80%든 그림이 똑같았다.
 * 옆에 적힌 숫자와 그림이 다른 말을 하는 셈인데, 눈은 그림을 먼저 읽는다.
 * 반지름이 같은 두 원의 렌즈 넓이가 한 원 넓이의 `fraction`이 되는 거리를
 * 이분법으로 찾아, 그림이 그 숫자를 그대로 보여 주게 한다.
 *
 * 렌즈 넓이 A(d) = 2r²·acos(d/2r) − (d/2)·√(4r²−d²) — d에 대해 단조 감소.
 */
export function vennCenterDistance(fraction: number, r: number): number {
  const f = Math.min(1, Math.max(0, fraction));
  if (f <= 0) return 2 * r; // 겹치지 않음 — 두 원이 맞닿는 지점
  if (f >= 1) return 0; // 완전히 포개짐
  const areaFraction = (d: number) => {
    const lens = 2 * r * r * Math.acos(d / (2 * r)) - (d / 2) * Math.sqrt(4 * r * r - d * d);
    return lens / (Math.PI * r * r);
  };
  let lo = 0;
  let hi = 2 * r;
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2;
    // areaFraction은 d가 커질수록 작아진다.
    if (areaFraction(mid) > f) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

export function formatDashNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString("ko-KR");
}

export function riskBadgeClass(label: string): string {
  if (label === "높음") return "dash-risk--high";
  if (label === "중간") return "dash-risk--medium";
  if (label === "낮음") return "dash-risk--low";
  return "dash-risk--minimal";
}

export function synergyBucketClass(key: string): string {
  const map: Record<string, string> = {
    synergy: "synergy-bucket--complementary",
    arts_synergy: "synergy-bucket--complementary",
    infra: "synergy-bucket--complementary",
    direct: "synergy-bucket--competitor",
    partial: "synergy-bucket--competitor",
    risk: "synergy-bucket--risk",
    severe_risk: "synergy-bucket--risk",
  };
  return map[key] || "";
}

export function synergyScoreLevel(score: number): string {
  const v = Number(score) || 0;
  if (v >= 60) return "높음";
  if (v >= 30) return "보통";
  return "낮음";
}

const LOW_PRICE_PATTERN = /메가|컴포즈|빽다|이디야|텐퍼|공차|저가/i;

export function isLowPriceCompetitor(name: string | undefined): boolean {
  return LOW_PRICE_PATTERN.test(name || "");
}

const COMPETITOR_CATEGORY_EMOJI: [RegExp, string][] = [
  [/카페|커피|coffee/i, "☕"],
  [/치킨|닭/i, "🍗"],
  [/피자|pizza/i, "🍕"],
  [/편의점|마트|슈퍼/i, "🏪"],
  [/음식|식당|레스토랑|restaurant/i, "🍽"],
  [/베이커|빵|제과/i, "🥐"],
  [/술|주점|바|pub/i, "🍺"],
  [/미용|헤어|네일/i, "💇"],
  [/학원|교육/i, "📚"],
  [/병원|의원|약국/i, "🏥"],
];

export function competitorCategoryTail(category?: string, kakaoCategory?: string | null): string {
  const raw = (kakaoCategory || category || "").trim();
  if (!raw) return "";
  const parts = raw.split(">").map((s) => s.trim()).filter(Boolean);
  return parts[parts.length - 1] || raw;
}

export function competitorAvatarEmoji(category?: string, kakaoCategory?: string | null): string {
  const label = competitorCategoryTail(category, kakaoCategory);
  for (const [pattern, emoji] of COMPETITOR_CATEGORY_EMOJI) {
    if (pattern.test(label)) return emoji;
  }
  return "";
}

export const TERRITORY_ZONE_COLORS: Record<string, { stroke: string; fill: string }> = {
  absorbed: { stroke: "#dc2626", fill: "#fecaca" },
  overlap: { stroke: "#b45309", fill: "#fde68a" },
  gap: { stroke: "#059669", fill: "#a7f3d0" },
};
export const TERRITORY_BRAND_PALETTE = ["#7c3aed", "#0891b2", "#ea580c", "#65a30d", "#db2777", "#4338ca", "#0d9488", "#a16207"];
export const TERRITORY_NEW_ENTRANT_COLOR = "#2563eb";

/**
 * 권역 면적 표기. 예전에는 늘 `Math.round(m2 / 10000)만㎡`이라, 1만㎡보다 작은
 * 권역은 전부 "0만㎡"로 보였다 — 실제로 있는 권역을 없는 것처럼 적는 셈이다.
 */
export function fmtTerritoryArea(areaM2: number): string {
  if (!Number.isFinite(areaM2) || areaM2 <= 0) return "—";
  if (areaM2 >= 10_000) return `${(areaM2 / 10_000).toFixed(1)}만㎡`;
  return `${Math.round(areaM2).toLocaleString("ko-KR")}㎡`;
}

export function territoryBrandColor(brand: string | undefined): string {
  const str = String(brand || "");
  let hash = 0;
  for (let i = 0; i < str.length; i++) hash = (hash * 31 + str.charCodeAt(i)) >>> 0;
  return TERRITORY_BRAND_PALETTE[hash % TERRITORY_BRAND_PALETTE.length];
}

export interface RecommendationCard { name: string; primary: string; secondary: string }

function fmtGrowth(pct: number | null | undefined): string | null {
  if (pct == null) return null;
  return `${pct >= 0 ? "+" : ""}${pct}%`;
}

/**
 * Ported from `buildRecommendationCards` in frontend/js/dashboard.js — 다만
 * 지어낸 숫자를 걷어냈다.
 *
 * 예전 카드는 거리 이름 옆에 "점수 72 · 임대 240–290"을 적었는데, 그 점수는
 * **현재 지점 점수에서 목록 순번만큼 뺀 값**이었고 임대료는 순번으로 고른
 * 하드코딩 문자열이었다(["240–290","210–260","180–230"]). 그 거리에서 잰 값이
 * 아닌데 화면에서는 분석 결과와 구분되지 않았다 — 임대료는 이 앱이 실거래로
 * 실제 계산하는 값이라 더 그렇다.
 *
 * 이제는 가진 실측값만 적는다: 현재 지점은 그 지점의 점수·등급, 성장 신호
 * 거리는 그 거리의 신호 등급과 분기 증감률.
 */
export function buildRecommendationCards(trends: TrendsResponse | null, siteScore: SiteScoreResponse | null): RecommendationCard[] {
  const cards: RecommendationCard[] = [];
  if (siteScore) {
    cards.push({
      name: siteScore.verdict_one_liner || "선택 지점",
      primary: `입지 점수 ${Math.round(siteScore.total_score)}점`,
      secondary: siteScore.grade ? `등급 ${siteScore.grade}` : "",
    });
  }
  (trends?.street_signals || []).slice(0, 3 - cards.length).forEach((s) => {
    const catGrowth = fmtGrowth(s.category_growth_pct);
    const storeGrowth = fmtGrowth(s.store_growth_pct);
    const secondary = catGrowth
      ? `${trends?.category_label || "동일업종"} ${catGrowth}`
      : storeGrowth
        ? `전체 점포 ${storeGrowth}`
        : s.detail || "";
    cards.push({ name: s.name, primary: s.label, secondary });
  });
  return cards;
}
