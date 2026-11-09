/**
 * 필지 폴리곤을 지도에 그리기 위한 순수 변환 — 색과 좌표만.
 *
 * 지도 컴포넌트에서 분리해 둔 이유는 **여기가 틀리면 화면이 조용히 거짓말을
 * 하기 때문**이다. 갭 점수의 부호를 뒤집어 칠하면 고평가 필지가 저평가처럼
 * 보이는데, 지도는 그걸 오류로 알려 주지 않는다. Kakao SDK 없이 테스트한다.
 */

/** 백엔드 `/api/map-parcels`가 주는 GeoJSON. Polygon과 MultiPolygon 둘 다 온다. */
export type ParcelGeometry = {
  type: string;
  coordinates: unknown;
};

/** 지도에 그릴 고리 하나 = [경도, 위도] 쌍의 배열. */
export type Ring = [number, number][];

function isRing(value: unknown): value is Ring {
  return (
    Array.isArray(value) &&
    value.length >= 3 &&
    value.every(
      (p) =>
        Array.isArray(p) &&
        p.length >= 2 &&
        Number.isFinite(p[0]) &&
        Number.isFinite(p[1]),
    )
  );
}

/**
 * GeoJSON에서 **바깥 고리만** 꺼낸다.
 *
 * 구멍(inner ring)은 버린다. 필지에 구멍이 뚫린 경우는 실질적으로 없고, Kakao의
 * Polygon은 구멍을 표현하려면 path를 배열의 배열로 넘겨야 해서 처리가 갈린다 —
 * 없는 경우를 위해 두 갈래를 두지 않는다.
 *
 * 좌표를 못 읽으면 **빈 배열을 준다.** 예외를 던지면 필지 하나 때문에 지도 전체가
 * 안 그려진다 — 그 필지만 빠지는 쪽이 낫다.
 */
export function outerRings(geometry: ParcelGeometry | null | undefined): Ring[] {
  if (!geometry) return [];
  const coords = geometry.coordinates;
  if (!Array.isArray(coords)) return [];

  if (geometry.type === "Polygon") {
    return isRing(coords[0]) ? [coords[0]] : [];
  }
  if (geometry.type === "MultiPolygon") {
    const rings: Ring[] = [];
    for (const polygon of coords) {
      if (Array.isArray(polygon) && isRing(polygon[0])) rings.push(polygon[0]);
    }
    return rings;
  }
  return [];
}

/**
 * 갭 점수 → 색.
 *
 * 갭 = 목 백분위 − 공시지가 백분위. **양수가 저평가**(길 구조는 위쪽인데
 * 공시지가는 아래쪽)이고 음수가 그 반대다.
 *
 * 판정이 없으면 **회색**이다. 회색을 "보통"으로 칠하면 안 된다 — 판정 못 한 땅과
 * 갭이 0인 땅은 다른 상태이고, 사용자가 구분할 수 있어야 한다.
 *
 * 색은 연속 그라데이션이 아니라 **구간**으로 끊는다. 갭 점수는 백분위의 차이라
 * ±3 정도의 차이에 의미를 부여할 근거가 없다. 매끄러운 그라데이션은 없는 정밀도를
 * 있는 것처럼 보이게 한다.
 */
// ⚠️ 음의 갭 쪽을 **"고평가"라고 부르지 않는다.** 목이 나쁜데 비싼 땅은 대개
// 우리가 안 보는 요인(한강뷰·학군·역세권)으로 비싸고, "고평가"는 낙인이다
// (docs/SPEC_PARCEL_LAND_VALUE_UI.md 표기 규칙). 같은 화면의 판정 카드는
// `above_peers`를 "같은 조건 대비 높음"으로 적는데 범례만 "고평가"라고 적혀
// 있어서 한 화면이 두 말을 하고 있었다 — 범례를 카드에 맞춘다.
//
// 색도 같은 이유로 빨강을 쓰지 않는다. 빨강은 "나쁘다"로 읽혀 문구만 중립으로
// 바꿔도 색이 낙인을 되살린다.
export const GAP_BANDS = [
  { min: 30, label: "많이 낮음", color: "#1d4ed8" },
  { min: 10, label: "낮음", color: "#60a5fa" },
  { min: -10, label: "비슷", color: "#e2e8f0" },
  { min: -30, label: "높음", color: "#fbbf24" },
  { min: -Infinity, label: "많이 높음", color: "#d97706" },
] as const;

/** 판정이 없는 필지. 색과 뜻을 한 군데 묶어 둔다. */
export const UNJUDGED = { label: "판정 없음", color: "#cbd5e1" } as const;

export function gapBand(gapScore: number | null | undefined) {
  if (gapScore == null || !Number.isFinite(gapScore)) return UNJUDGED;
  return GAP_BANDS.find((b) => gapScore >= b.min) ?? UNJUDGED;
}

/** 채우기 투명도 — 판정 없는 필지는 옅게 둬서 시선을 뺏지 않는다. */
export function fillOpacity(gapScore: number | null | undefined): number {
  return gapScore == null ? 0.18 : 0.55;
}

/**
 * 지도를 어디에 맞출까 — 볼 필지들의 좌표 평균.
 *
 * 동 경계의 중심이 아니라 **목록에 오른 필지들의 중심**을 쓴다. 동은 길게
 * 늘어진 모양이 흔해서(여의도동은 동서 4km) 경계 중심이 정작 볼 필지들과 한참
 * 떨어질 수 있고, 서버 반경 상한이 600m라 그러면 첫 화면에 아무것도 안 뜬다.
 *
 * 좌표가 하나도 없으면 null이다 — 호출자가 지도를 띄우지 않는다. 없는 좌표를
 * 0으로 채우면 지도가 아프리카 앞바다로 날아간다.
 */
export function centerOf(
  points: { lat: number | null; lng: number | null }[],
): { lat: number; lng: number } | null {
  const ok = points.filter(
    (p): p is { lat: number; lng: number } =>
      Number.isFinite(p.lat) && Number.isFinite(p.lng),
  );
  if (ok.length === 0) return null;
  return {
    lat: ok.reduce((s, p) => s + p.lat, 0) / ok.length,
    lng: ok.reduce((s, p) => s + p.lng, 0) / ok.length,
  };
}

/**
 * 백분위를 사람이 읽는 말로.
 *
 * `상위 ${100 - pct}%`만 쓰면 백분위 0이 **"상위 100%"** 로 나온다 — 실제로
 * 독산동 필지 화면에 그렇게 찍혔고, 문장으로 뜻이 없다. 아래쪽 절반은 "하위"로
 * 뒤집어 말한다.
 */
export function percentileText(pct: number): string {
  const p = Math.round(pct);
  return p >= 50 ? `상위 ${Math.max(1, 100 - p)}%` : `하위 ${Math.max(1, p)}%`;
}
