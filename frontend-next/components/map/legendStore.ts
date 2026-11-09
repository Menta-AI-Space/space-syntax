/**
 * 지도가 **실제로 그린 것**을 범례에 알려 주는 공유 상태.
 *
 * 범례가 색을 설명하려면 지도가 그때 쓴 값 범위를 알아야 한다. 그런데 범위는
 * KakaoMap 안쪽 이펙트에서 계산되고(valueExtent), 범례는 AppShell이 지도 밖에
 * 그린다 — 둘이 컴포넌트 트리상 떨어져 있고 AppShell은 이 에이전트 소유가
 * 아니라 props를 새로 뚫을 수 없다. reachCircle.ts와 같은 방식으로
 * 모듈 스토어 + useSyncExternalStore를 쓴다.
 *
 * 이 방향이 중요한 이유: 범례가 자기 나름대로 다시 계산하면 지도와 어긋날 수
 * 있다. 지도가 그린 값을 그대로 받아 적으면 어긋날 수가 없다.
 */

import { useSyncExternalStore } from "react";

export interface MetricLayerState {
  metric: string;
  mode: string;
  /** 색 램프 양 끝에 해당하는 실제 지표 값 (2~98 백분위). */
  min: number;
  max: number;
  /** 색칠된 도로 구간 수. 0이면 범례에 램프를 보여 줄 이유가 없다. */
  segmentCount: number;
}

let current: MetricLayerState | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((fn) => fn());
}

function same(a: MetricLayerState | null, b: MetricLayerState | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return (
    a.metric === b.metric &&
    a.mode === b.mode &&
    a.min === b.min &&
    a.max === b.max &&
    a.segmentCount === b.segmentCount
  );
}

/** 지도가 도로를 색칠한 뒤 호출한다. 같은 값이면 리렌더를 만들지 않는다. */
export function publishMetricLayer(next: MetricLayerState | null): void {
  if (same(current, next)) return;
  current = next;
  emit();
}

export function clearMetricLayer(): void {
  publishMetricLayer(null);
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

// 지도는 클라이언트 전용이라 서버 렌더에는 아무것도 없다.
function getServerSnapshot(): MetricLayerState | null {
  return null;
}

export function useMetricLayer(): MetricLayerState | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
