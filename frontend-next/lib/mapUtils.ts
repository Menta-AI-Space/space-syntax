// Shared Kakao-map rendering helpers, ported from frontend/js/map.js and
// frontend/js/simulation.js. Both components/map/KakaoMap.tsx (입지 분석 map)
// and components/map/SimMap.tsx (신설도로 시뮬레이션 map) use the same
// space-syntax color ramp / metric-key / geometry-conversion logic, so it
// lives here once instead of being duplicated in each component.

// 확장자를 붙여야 `node --test`(--experimental-strip-types)가 이 모듈을 그대로
// 불러올 수 있다 — tests/mapUtils.test.ts가 여기 순수 함수들을 직접 검증한다.
// tsconfig의 allowImportingTsExtensions가 켜져 있어 타입체크·번들 모두 통과한다.
import { METRIC_INFO } from "./metricInfo.ts";

export const METRIC_PALETTE: Record<string, { name: string; invert?: boolean }> = {
  choice: { name: METRIC_INFO.choice.easy },
  integration: { name: METRIC_INFO.integration.easy },
  mean_depth: { name: METRIC_INFO.mean_depth.easy, invert: true },
  connectivity: { name: METRIC_INFO.connectivity.easy },
};

// Space-syntax convention rainbow ramp: cold blue (low/segregated) through
// green/yellow to hot red (high/integrated).
export const HEAT_STOPS: [number, number, number][] = [
  [29, 78, 216],
  [6, 182, 212],
  [34, 197, 94],
  [234, 179, 8],
  [249, 115, 22],
  [185, 28, 28],
];

/**
 * 지도 오버레이 색 — 한 곳에서만 정한다.
 *
 * 카카오 지도 오버레이는 캔버스로 그려져서 CSS 변수를 못 받는다(색을 문자열로
 * 넘겨야 한다). 그래서 값은 tokens.css와 같은 색을 여기 한 번만 적고, 지도와
 * 범례가 **같은 상수**를 쓴다 — 범례의 색과 지도의 색이 어긋날 수 없게.
 *
 * 분석 반경이 왜 회색인가: 예전에는 파란색(#2563eb)이었는데, 지표 히트램프의
 * 가장 낮은 구간도 파란색(#1d4ed8)이다. 같은 지도에서 파란색이 '분석 반경'과
 * '지표가 낮은 길' 두 가지를 동시에 뜻해서, 사용자가 "파란색 기준이 뭔지
 * 모르겠다"고 지적했다. 반경은 지표가 아니라 분석 범위를 나타내는 보조선이므로
 * 램프에 없는 중립색(회색)으로 빼서 색의 뜻이 겹치지 않게 한다.
 */
export const MAP_OVERLAY_COLORS = {
  /** 분석 반경 보조선 — 중립 회색 (tokens: --label-alternative). */
  analysisRadius: "#87878a",
  /** 도달권 — 초록 (tokens: --status-positive). */
  reach: "#059669",
  /** 시뮬레이션. */
  simGain: "#16a34a",
  simLoss: "#dc2626",
  simClosed: "#111827",
  simVirtual: "#7c3aed",
} as const;

/** 범례용 램프 색 배열 (지표에 따라 방향이 뒤집힌다). */
export function rampColors(metric: string): string[] {
  const colors = HEAT_STOPS.map(([r, g, b]) => `rgb(${r}, ${g}, ${b})`);
  return METRIC_PALETTE[metric]?.invert === true ? [...colors].reverse() : colors;
}

export function lerpColor(stops: [number, number, number][], t: number): string {
  const clamped = Math.max(0, Math.min(1, t));
  const scaled = clamped * (stops.length - 1);
  const i = Math.min(Math.floor(scaled), stops.length - 2);
  const localT = scaled - i;
  const [r1, g1, b1] = stops[i];
  const [r2, g2, b2] = stops[i + 1];
  const r = Math.round(r1 + (r2 - r1) * localT);
  const g = Math.round(g1 + (g2 - g1) * localT);
  const b = Math.round(b1 + (b2 - b1) * localT);
  return `rgb(${r}, ${g}, ${b})`;
}

export function metricColor(value: number, min: number, max: number, metric: string): string {
  if (max <= min) return lerpColor(HEAT_STOPS, 0.5);
  const t = Math.max(0, Math.min(1, (value - min) / (max - min)));
  const invert = METRIC_PALETTE[metric]?.invert === true;
  return lerpColor(HEAT_STOPS, invert ? 1 - t : t);
}

export function percentile(sortedValues: number[], fraction: number): number {
  if (sortedValues.length === 0) return 0;
  const position = (sortedValues.length - 1) * fraction;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  if (lower === upper) return sortedValues[lower];
  return sortedValues[lower] + (sortedValues[upper] - sortedValues[lower]) * (position - lower);
}

export function valueExtent(values: number[]) {
  if (values.length === 0) return { min: 0, max: 1 };
  const sorted = [...values].sort((a, b) => a - b);
  const min = percentile(sorted, 0.02);
  const max = percentile(sorted, 0.98);
  if (max <= min) {
    return { min: sorted[0], max: sorted[sorted.length - 1] || sorted[0] + 1e-9 };
  }
  return { min, max };
}

/** Build the GeoJSON property key for a metric + mode, e.g. "choice_walk_depthmapx". */
export function propKey(properties: Record<string, unknown> | undefined, metric: string, mode: string) {
  const depthmapxKey = `${metric}_${mode}_depthmapx`;
  if (typeof properties?.[depthmapxKey] === "number") return depthmapxKey;
  return `${metric}_${mode}`;
}

/** GeoJSON uses [lng, lat]; Kakao LatLng expects (lat, lng). */
export function geoJsonToKakaoPath(coordinates: [number, number][]) {
  return coordinates.map(([lng, lat]) => new window.kakao.maps.LatLng(lat, lng));
}

/* ------------------------------------------------------------------ *
 * 아래는 미니 지도(AnalysisVisuals·CandidateMiniMap)가 쓰는 순수 계산.
 * "특정 좌표·특정 반경에서만 맞는 값"을 컴포넌트에 박아 두면 새 입력에서
 * 바로 깨지므로, 입력에서 계산해 내는 함수로 빼고 테스트로 고정한다.
 * ------------------------------------------------------------------ */

export interface LatLngPoint {
  lat: number;
  lng: number;
}

/** 위도 1도의 거리(m). 경도는 위도에 따라 줄어들어 아래에서 cos 보정한다. */
const METERS_PER_DEG_LAT = 111_320;

/** 위경도 사각형. Kakao LatLngBounds에 넣기 전의 순수 값. */
export interface LatLngBox {
  south: number;
  west: number;
  north: number;
  east: number;
}

export function isFiniteLatLng(value: unknown): value is LatLngPoint {
  const p = value as LatLngPoint | null;
  return (
    !!p &&
    typeof p.lat === "number" &&
    typeof p.lng === "number" &&
    Number.isFinite(p.lat) &&
    Number.isFinite(p.lng) &&
    Math.abs(p.lat) <= 90 &&
    Math.abs(p.lng) <= 180
  );
}

/** 경도 1도의 거리(m). 극 근처에서 0으로 나누지 않도록 하한을 둔다. */
function metersPerDegLng(lat: number): number {
  const scale = Math.cos((lat * Math.PI) / 180);
  return METERS_PER_DEG_LAT * Math.max(0.01, Math.abs(scale));
}

/** 두 점 사이 거리(m). 하버사인 — 서울 안이든 밖이든 같은 식으로 맞는다. */
export function haversineM(a: LatLngPoint, b: LatLngPoint): number {
  const R = 6_371_000;
  const dLat = ((b.lat - a.lat) * Math.PI) / 180;
  const dLng = ((b.lng - a.lng) * Math.PI) / 180;
  const lat1 = (a.lat * Math.PI) / 180;
  const lat2 = (b.lat * Math.PI) / 180;
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

/**
 * 중심 + 반경(m)을 감싸는 사각형.
 *
 * 미니 지도에 zoom level을 상수로 박으면 반경 100m짜리 분석은 점처럼 보이고
 * 반경 2km짜리는 원이 화면 밖으로 나간다. 반경에서 사각형을 만들어 setBounds에
 * 넘기면 어떤 반경이 와도 원이 화면에 딱 들어온다.
 */
export function boundsForRadius(lat: number, lng: number, radiusM: number): LatLngBox | null {
  if (!isFiniteLatLng({ lat, lng })) return null;
  const r = Number.isFinite(radiusM) && radiusM > 0 ? radiusM : 0;
  const dLat = r / METERS_PER_DEG_LAT;
  const dLng = r / metersPerDegLng(lat);
  return {
    south: lat - dLat,
    west: lng - dLng,
    north: lat + dLat,
    east: lng + dLng,
  };
}

/**
 * 여러 점을 감싸는 사각형.
 *
 * 후보가 하나뿐이거나 모두 같은 건물이면 사각형이 한 점으로 찌그러지고,
 * 그대로 setBounds하면 지도가 최대 배율까지 확대돼 주변이 안 보인다.
 * 그래서 최소 폭(minSpanM)을 보장한다. 반대로 후보가 멀리 흩어지면 사각형이
 * 그대로 커져서 전부 들어온다 — 양쪽 다 같은 코드로 처리된다.
 */
export function boundsForPoints(points: LatLngPoint[], minSpanM = 400): LatLngBox | null {
  const valid = points.filter(isFiniteLatLng);
  if (valid.length === 0) return null;

  let south = valid[0].lat;
  let north = valid[0].lat;
  let west = valid[0].lng;
  let east = valid[0].lng;
  for (const p of valid) {
    south = Math.min(south, p.lat);
    north = Math.max(north, p.lat);
    west = Math.min(west, p.lng);
    east = Math.max(east, p.lng);
  }

  const midLat = (south + north) / 2;
  const midLng = (west + east) / 2;
  const half = Math.max(0, minSpanM) / 2;
  const minDLat = half / METERS_PER_DEG_LAT;
  const minDLng = half / metersPerDegLng(midLat);

  if (north - south < minDLat * 2) {
    south = midLat - minDLat;
    north = midLat + minDLat;
  }
  if (east - west < minDLng * 2) {
    west = midLng - minDLng;
    east = midLng + minDLng;
  }
  return { south, west, north, east };
}

/** 사각형의 대각선 길이(m) — 화면 축척(m/px)을 가늠하는 데 쓴다. */
export function boxSpanM(box: LatLngBox): number {
  return haversineM({ lat: box.south, lng: box.west }, { lat: box.north, lng: box.east });
}

/**
 * "화면에서 겹쳐 보이는" 기준 거리(m).
 *
 * 겹침 판정을 미터 상수로 두면 축척에 따라 틀린다 — 후보가 한 동네에 몰리면
 * 200m도 화면에서 멀고, 서울 전역으로 흩어지면 200m는 완전히 겹친다. 지도에
 * 실제로 보이는 범위(spanM)와 지도 폭(px)에서 m/px를 구해 배지 크기만큼을
 * 겹침 기준으로 삼는다.
 */
export function overlapThresholdM(spanM: number, viewportPx: number, badgePx = 46): number {
  const span = Number.isFinite(spanM) && spanM > 0 ? spanM : 0;
  const px = Number.isFinite(viewportPx) && viewportPx > 0 ? viewportPx : 1;
  return (span / px) * Math.max(1, badgePx);
}

/**
 * 겹치는 마커에 계단 번호를 매긴다.
 *
 * 좌표를 옮겨 흩뿌리면 지도가 거짓말을 하게 되므로 앵커 좌표는 그대로 두고,
 * 배지만 슬롯 번호만큼 위로 올려 서로 가리지 않게 한다. 앞 순위(입력 순서가
 * 빠른 것)가 슬롯 0 = 원래 자리를 갖는다.
 *
 * 반환: 입력과 같은 길이의 슬롯 번호 배열. 좌표가 유효하지 않으면 0.
 */
export function assignOverlapSlots(points: LatLngPoint[], thresholdM: number): number[] {
  const threshold = Number.isFinite(thresholdM) && thresholdM > 0 ? thresholdM : 0;
  const slots: number[] = [];
  // 이미 자리를 잡은 점들을 보고, 겹치는 것 중 가장 큰 슬롯 다음 번호를 준다.
  const placed: { point: LatLngPoint; slot: number }[] = [];

  for (const point of points) {
    if (!isFiniteLatLng(point) || threshold === 0) {
      slots.push(0);
      if (isFiniteLatLng(point)) placed.push({ point, slot: 0 });
      continue;
    }
    let slot = 0;
    for (const other of placed) {
      if (haversineM(point, other.point) <= threshold) {
        slot = Math.max(slot, other.slot + 1);
      }
    }
    slots.push(slot);
    placed.push({ point, slot });
  }
  return slots;
}

/**
 * 두 비율을 합계 100으로 되돌린다.
 *
 * 백엔드가 반올림해서 남 49 / 여 50을 주거나(합 99), 한쪽만 100을 주면 막대
 * 폭이 어긋난다. 합이 0이거나 값이 없으면 null — 그리지 않는다.
 */
export function normalizedSplit(
  a: number | null | undefined,
  b: number | null | undefined,
): { a: number; b: number } | null {
  const left = typeof a === "number" && Number.isFinite(a) && a >= 0 ? a : null;
  const right = typeof b === "number" && Number.isFinite(b) && b >= 0 ? b : null;
  if (left === null && right === null) return null;
  const l = left ?? 0;
  const r = right ?? 0;
  const total = l + r;
  if (total <= 0) return null;
  return { a: (l / total) * 100, b: (r / total) * 100 };
}

/** 거리 표기 — 1km 미만은 m, 그 이상은 km. 반경 캡션·범례에 쓴다. */
export function formatDistanceM(meters: number): string {
  if (!Number.isFinite(meters) || meters <= 0) return "—";
  if (meters < 1000) return `${Math.round(meters)}m`;
  const km = meters / 1000;
  return `${km >= 10 ? Math.round(km) : Number(km.toFixed(1))}km`;
}

/**
 * CSS 토큰의 실제 색 값을 읽는다.
 *
 * Kakao의 Circle·Polyline은 `var(--blue-50)`을 못 받고 실제 색 문자열만
 * 받는다. 그렇다고 컴포넌트에 hex를 박으면 토큰을 바꿔도 지도만 옛 색으로
 * 남는다. 런타임에 토큰을 읽어 쓰고, 못 읽으면(SSR·테스트) fallback을 쓴다.
 */
export function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined" || typeof document === "undefined") return fallback;
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  } catch {
    return fallback;
  }
}
