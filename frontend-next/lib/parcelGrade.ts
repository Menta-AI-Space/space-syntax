/**
 * 필지·획지 등급 화면이 공유하는 표시 규칙.
 *
 * A안(`ParcelStructurePanel`, 이미 있는 동네)과 B안(`PlannedSitePanel`, 계획
 * 단계 택지)은 서버에서 **같은 등급·배분 함수**를 통과해 온다. 화면 색과 표기가
 * 갈리면 "계획일 때 몇 등급이었는데 지어지고 몇 등급이 됐다"는 비교가 눈으로
 * 성립하지 않으므로, 표시 쪽도 한 곳에서만 정한다.
 */

export const GRADE_COLORS: Record<number, string> = {
  1: "#dc2626",
  2: "#ea580c",
  3: "#eab308",
  4: "#38bdf8",
  5: "#2563eb",
};

export const UNGRADED_COLOR = "#9ca3af";

export const GRADE_LEGEND = [1, 2, 3, 4, 5] as const;

/** GeoJSON Polygon/MultiPolygon에서 바깥 링만 뽑는다 (구멍은 그리지 않는다). */
export function outerRings(geometry: {
  type: string;
  coordinates: unknown;
}): [number, number][][] {
  const coords = geometry.coordinates as number[][][][] | number[][][];
  if (geometry.type === "MultiPolygon") {
    return (coords as number[][][][]).map((poly) => poly[0] as [number, number][]);
  }
  if (geometry.type === "Polygon") {
    return [(coords as number[][][])[0] as [number, number][]];
  }
  return [];
}

/** 만원 단위 금액을 사람이 읽는 말로. 억을 넘으면 억으로 접는다. */
export function money(manwon: number): string {
  if (manwon >= 10000) return `${(manwon / 10000).toFixed(1)}억`;
  return `${Math.round(manwon).toLocaleString()}만`;
}

/** 백분위(높을수록 좋음) → "상위 N%". 0%는 없으므로 최소 1%로 읽는다. */
export function topPercentText(percentile: number): string {
  return `상위 ${Math.max(1, Math.round(100 - percentile))}%`;
}
