// 주소 주변 길 분석(/api/analyze)에서 「목이 좋은 길」 몇 곳을 고른다.
//
// 2026-10-24: 첫 화면을 「주소 → 길 색칠 → 목 좋은 길 3곳」으로 바꿨다(소유자 결정).
// AI 서술 없이 엔진 숫자만으로 2초 안에 답한다.
//
// 「목」(CONTEXT.md) = 지나가는 길(Choice) + 모이기 쉬운 정도(Integration). 둘을 이
// 반경 안의 백분위로 바꿔 평균한다 — 절대값은 반경마다 크기가 달라 비교가 안 된다.
// 순위는 **이 반경 안의 상대 순위**다. 서울 전체 순위나 매출 예측이 아니다.
//
// 같은 길의 이웃 구간 셋이 1·2·3등을 다 차지하면 「3곳」이 아니라 한 곳이다 —
// 앞에서 고른 곳과 MIN_GAP_M 안이면 건너뛴다.

import type { AnalyzeResponse, GeoJsonFeature } from "./api.ts";

export interface TopStreet {
  rank: number;
  lat: number;
  lng: number;
  /** 이 반경 안에서 위에서 몇 %인지(1 = 맨 위). */
  topPct: number;
  /** 순위에 든 길 중 몇 번째인지와 그 길 수. topPct는 1% 단위로 올림해서 길이 1,000개면
   *  1위와 8위가 둘 다 「상위 1%」로 보였다 — 화면은 이 순번으로 구분한다. */
  order?: number;
  total?: number;
  /** 사람이 읽는 길 종류 — "큰길", "골목" 등. */
  kind: string;
  lengthM: number;
  osmId: number | null;
}

/** 사람이 걷는 상점가가 아닌 길 — 순위에서 뺀다(색칠은 그대로 한다). */
const EXCLUDED_HIGHWAYS = new Set([
  "service", "footway", "steps", "path", "cycleway", "track", "corridor",
  "motorway", "motorway_link", "trunk", "trunk_link", "construction", "proposed",
]);

const KIND_LABELS: Record<string, string> = {
  primary: "큰길", primary_link: "큰길", secondary: "큰길", secondary_link: "큰길",
  tertiary: "중간 길", tertiary_link: "중간 길",
  residential: "골목", living_street: "골목", unclassified: "작은 길",
  pedestrian: "보행자 길",
};

export const MIN_GAP_M = 120;
const MIN_LENGTH_M = 15;

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function midpoint(feature: GeoJsonFeature): [number, number] | null {
  const g = feature.geometry;
  if (!g) return null;
  let coords: unknown = g.coordinates;
  if (g.type === "MultiLineString" && Array.isArray(coords)) coords = coords[0];
  if (!Array.isArray(coords) || coords.length === 0) return null;
  const mid = coords[Math.floor(coords.length / 2)];
  if (!Array.isArray(mid) || mid.length < 2) return null;
  const [lng, lat] = mid as [number, number];
  return Number.isFinite(lat) && Number.isFinite(lng) ? [lat, lng] : null;
}

function distanceM(a: [number, number], b: [number, number]): number {
  const dLat = (a[0] - b[0]) * 111_320;
  const dLng = (a[1] - b[1]) * 111_320 * Math.cos((a[0] * Math.PI) / 180);
  return Math.hypot(dLat, dLng);
}

/** 값 → 0~1 백분위(높을수록 위). 같은 값은 같은 백분위. */
function percentiles(values: number[]): Map<number, number> {
  const sorted = [...values].sort((a, b) => a - b);
  const out = new Map<number, number>();
  const n = sorted.length;
  sorted.forEach((v, i) => {
    if (!out.has(v)) out.set(v, n > 1 ? i / (n - 1) : 1);
  });
  return out;
}

const DIRECTIONS = ["북쪽", "북동쪽", "동쪽", "남동쪽", "남쪽", "남서쪽", "서쪽", "북서쪽"];

/** 검색한 곳에서 본 방향과 거리 — "북동쪽 180m". 지번은 큰 땅(역·학교)에 붙은 길 둘이
 *  같은 번지로 나와 구분이 안 됐다(성수역 실측, 10/24). 30m 안이면 「바로 앞」. */
export function directionFrom(center: { lat: number; lng: number }, point: { lat: number; lng: number }): string {
  const dy = (point.lat - center.lat) * 111_320;
  const dx = (point.lng - center.lng) * 111_320 * Math.cos((center.lat * Math.PI) / 180);
  const dist = Math.hypot(dx, dy);
  if (dist < 30) return "바로 앞";
  const bearing = ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360;
  return `${DIRECTIONS[Math.round(bearing / 45) % 8]} ${Math.round(dist / 10) * 10}m`;
}

export function pickTopStreets(data: AnalyzeResponse, count = 3, mode: "walk" | "drive" = "walk"): TopStreet[] {
  const choiceKey = `choice_${mode}`;
  const integKey = `integration_${mode}`;
  const flag = mode === "walk" ? "walkable" : "drivable";

  const rows = (data.segments?.features || [])
    .map((f) => {
      const p = f.properties || {};
      const choice = num(p[choiceKey]);
      const integ = num(p[integKey]);
      const mid = midpoint(f);
      if (choice === null || integ === null || !mid || p[flag] === false) return null;
      return {
        mid,
        choice,
        integ,
        highway: typeof p.highway === "string" ? p.highway : "",
        lengthM: num(p.length_m) ?? 0,
        osmId: num(p.osm_id),
      };
    })
    .filter((r): r is NonNullable<typeof r> => r !== null);
  if (!rows.length) return [];

  // 백분위는 칠한 길 전체로 낸다 — 순위에서 빼는 길도 사람 흐름에는 들어 있다.
  const choicePct = percentiles(rows.map((r) => r.choice));
  const integPct = percentiles(rows.map((r) => r.integ));
  const eligible = rows
    .filter((r) => !EXCLUDED_HIGHWAYS.has(r.highway) && r.lengthM >= MIN_LENGTH_M)
    .map((r) => ({ ...r, score: ((choicePct.get(r.choice) ?? 0) + (integPct.get(r.integ) ?? 0)) / 2 }))
    .sort((a, b) => b.score - a.score);

  const picked: TopStreet[] = [];
  for (let i = 0; i < eligible.length && picked.length < count; i += 1) {
    const r = eligible[i];
    if (picked.some((p) => distanceM([p.lat, p.lng], r.mid) < MIN_GAP_M)) continue;
    picked.push({
      rank: picked.length + 1,
      lat: r.mid[0],
      lng: r.mid[1],
      topPct: Math.max(1, Math.ceil(((i + 1) / eligible.length) * 100)),
      order: i + 1,
      total: eligible.length,
      kind: KIND_LABELS[r.highway] ?? "길",
      lengthM: Math.round(r.lengthM),
      osmId: r.osmId,
    });
  }
  return picked;
}
