/**
 * 도달권(reach) 공유 상태.
 *
 * 지도 위 세로 슬라이더(ReachSlider)를 드래그하면 메인 카카오 지도의 도달권
 * 원이 즉시 다시 그려진다. 슬라이더는 지도 위(AppShell), 원은 KakaoMap,
 * 컨텍스트를 세팅하는 곳은 결과 패널 안쪽(IsochroneSection)이라 셋이 컴포넌트
 * 트리상 멀리 떨어져 있다. 상위 상태를 관통시키면 무관한 리렌더가 생겨서,
 * 모듈 스토어 + useSyncExternalStore로 필요한 컴포넌트만 구독하게 한다.
 *
 * 시간은 1분 단위로 고르지만, 백엔드가 실제 도로망으로 계산한 도달거리는
 * 밴드(예: 5·10·15분)에만 있다. 그 사이 분값은 밴드 실측거리를 선형보간해서
 * "실제 반경 기반"으로 그린다(속도×시간 근사보다 정확). — interpolatedReachRadiusM.
 */

import { useSyncExternalStore } from "react";

export type ReachMode = "walk" | "bike" | "moto" | "drive";

/** 이동수단별 이동속도 (m/분) — backend/app/services/isochrone.py의 MODE_SPEED와 동일. */
export const REACH_SPEED_M_PER_MIN: Record<ReachMode, number> = {
  walk: 75,
  bike: 200,
  moto: 250,
  drive: 420,
};

export const REACH_MODE_LABELS: Record<ReachMode, string> = {
  walk: "도보",
  bike: "자전거",
  moto: "오토바이",
  drive: "차량",
};

export interface ReachState {
  lat: number;
  lng: number;
  mode: ReachMode;
  /** 밴드 실측 도달거리(m). key = 밴드 분(minutes), value = max_distance_m. */
  distancesM: Record<number, number>;
  /** 실측이 있는 앵커 밴드 분값(오름차순). 슬라이더 눈금 표시에 쓴다. */
  bandMinutes: number[];
  /** 슬라이더 범위. */
  minMinute: number;
  maxMinute: number;
  /** 현재 슬라이더 값(분). */
  minute: number;
}

/**
 * 분값 → 반경(m). 앵커 밴드 실측거리를 선형보간한다.
 * - 최소 밴드보다 작으면 원점(0,0)에서 최소 밴드까지 비례.
 * - 밴드 사이는 두 밴드 실측거리 선형보간.
 * - 최대 밴드보다 크면 마지막 구간 기울기로 외삽(없으면 비례).
 * - 앵커가 없으면 속도×시간 근사.
 */
export function interpolatedReachRadiusM(
  mode: ReachMode,
  minute: number,
  distancesM: Record<number, number> | undefined,
  bandMinutes: number[] | undefined,
): number {
  const anchors: [number, number][] = (bandMinutes || [])
    .map((m) => [m, distancesM?.[m]] as [number, number | undefined])
    .filter((pair): pair is [number, number] => Number.isFinite(pair[1]) && (pair[1] as number) > 0)
    .sort((a, b) => a[0] - b[0]);

  if (anchors.length === 0) return REACH_SPEED_M_PER_MIN[mode] * minute;

  const [m0, d0] = anchors[0];
  if (minute <= m0) return d0 * (minute / m0);

  const [mN, dN] = anchors[anchors.length - 1];
  if (minute >= mN) {
    if (anchors.length >= 2) {
      const [mp, dp] = anchors[anchors.length - 2];
      const slope = (dN - dp) / (mN - mp);
      return dN + slope * (minute - mN);
    }
    return dN * (minute / mN);
  }

  for (let i = 0; i < anchors.length - 1; i++) {
    const [aLo, dLo] = anchors[i];
    const [aHi, dHi] = anchors[i + 1];
    if (minute >= aLo && minute <= aHi) {
      return dLo + (dHi - dLo) * ((minute - aLo) / (aHi - aLo));
    }
  }
  return dN;
}

/** 반경(m)을 사람이 읽기 좋은 거리 문구로. 지도 이름표·슬라이더·범례가 공유한다. */
export function formatDistance(m: number): string {
  if (!Number.isFinite(m) || m <= 0) return "—";
  if (m >= 1000) return `${(m / 1000).toFixed(m >= 10000 ? 0 : 1)}km`;
  return `${Math.round(m / 10) * 10}m`;
}

/** 이동수단 표시 순서 — 반경이 작은 것부터(도보 → 차량). */
export const REACH_MODE_ORDER: ReachMode[] = ["walk", "bike", "moto", "drive"];

export interface ModeReach {
  mode: ReachMode;
  radiusM: number;
  /** true면 백엔드가 도로망으로 잰 값, false면 속도 기준 근사. */
  measured: boolean;
}

/**
 * 같은 시간에 **이동수단별로 얼마나 갈 수 있는지**를 한 번에 낸다.
 *
 * 사용자 지적: "도보랑 자전거랑 차랑 오토바이가 다르잖아. 그것도 같이 잘
 * 표시해줘야 하는 거고. 지금은 도보 몇 미터밖에 안 되니까." — 지도에 도보
 * 반경 하나만 그리면 차량 상권을 판단할 수 없다.
 *
 * 실측이 있는 건 지금 백엔드에서 받아 온 **한 가지 이동수단뿐**이다. 나머지는
 * 직선 속도×시간으로 그리면 실제보다 크게 나온다(도로는 돌아간다). 그래서
 * 실측 모드에서 관측한 우회계수 = 실측거리 ÷ (속도×시간) 를 구해 다른
 * 이동수단에도 적용한다. 같은 동네의 길이 돌아가는 정도를 물려받으므로
 * 맨 속도 근사보다 낫고, 그래도 근사이므로 measured=false로 표시해
 * 지도·범례에서 실선(실측)과 점선(근사)으로 구분한다.
 */
export function reachByMode(state: ReachState): ModeReach[] {
  const measuredRadius = interpolatedReachRadiusM(
    state.mode,
    state.minute,
    state.distancesM,
    state.bandMinutes,
  );
  const straight = REACH_SPEED_M_PER_MIN[state.mode] * state.minute;
  const hasMeasurement = state.bandMinutes.length > 0 && measuredRadius > 0 && straight > 0;
  const detour = hasMeasurement ? measuredRadius / straight : 1;

  return REACH_MODE_ORDER.map((mode) => {
    if (mode === state.mode) {
      return { mode, radiusM: measuredRadius, measured: hasMeasurement };
    }
    return {
      mode,
      radiusM: REACH_SPEED_M_PER_MIN[mode] * state.minute * detour,
      measured: false,
    };
  })
    .filter((r) => Number.isFinite(r.radiusM) && r.radiusM > 0)
    .sort((a, b) => a.radiusM - b.radiusM);
}

let current: ReachState | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((fn) => fn());
}

/**
 * 도달권 컨텍스트를 세팅한다. 같은 위치·이동수단이면 사용자가 드래그해 둔
 * 현재 분값을 (범위 안에서) 유지한다 — 무관한 리렌더로 슬라이더가 튀지 않게.
 */
export function publishReachContext(ctx: {
  lat: number;
  lng: number;
  mode: ReachMode;
  distancesM: Record<number, number>;
  bandMinutes: number[];
}): void {
  const bands = [...ctx.bandMinutes].sort((a, b) => a - b);
  const maxMinute = bands.length ? bands[bands.length - 1] : 15;
  const minMinute = 1;
  const sameSpot =
    current != null && current.lat === ctx.lat && current.lng === ctx.lng && current.mode === ctx.mode;
  const keep = sameSpot ? Math.min(maxMinute, Math.max(minMinute, current!.minute)) : maxMinute;
  current = { ...ctx, bandMinutes: bands, minMinute, maxMinute, minute: keep };
  emit();
}

/** 슬라이더 드래그 — 분값만 바꾼다(범위로 클램프, 정수). */
export function setReachMinute(minute: number): void {
  if (!current) return;
  const m = Math.min(current.maxMinute, Math.max(current.minMinute, Math.round(minute)));
  if (m === current.minute) return;
  current = { ...current, minute: m };
  emit();
}

export function clearReach(): void {
  if (current === null) return;
  current = null;
  emit();
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function getSnapshot() {
  return current;
}

// SSR에서는 지도(및 오버레이)가 없다 — 지도 자체가 클라이언트 전용.
function getServerSnapshot(): ReachState | null {
  return null;
}

export function useReachState(): ReachState | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
