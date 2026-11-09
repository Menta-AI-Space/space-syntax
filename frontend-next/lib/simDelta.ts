// 신설도로 시뮬레이션의 "변화" 판정 한 곳. 지도 색, 범례 문구, 왼쪽 패널의
// 증감 숫자가 각자 기준을 들고 있으면 서로 어긋난다 — 지도는 회색(변화 없음)
// 인데 옆의 숫자는 초록(+1.2%)으로 좋아졌다고 말하는 식이다.
//
// Ported from `SIM_COLORS`/`simDeltaColor` in frontend/js/simulation.js.

export const SIM_COLORS = {
  virtual: "#6d28d9",
  improved: "#059669",
  improvedStrong: "#065f46",
  worsened: "#dc2626",
  unchanged: "#9ca3af",
  draft: "#7c3aed",
  closed: "#111827",
};

/** 이만큼 넘게 좋아져야 "크게 개선"으로 본다. */
export const DELTA_STRONG_PCT = 20;
/** 이 안쪽은 계산 흔들림과 구분되지 않아 "변화 없음"으로 본다. */
export const DELTA_MIN_PCT = 2;

export type DeltaTone = "up" | "down" | "flat";

/** 값이 없으면(계산 대상 밖) 좋다고도 나쁘다고도 하지 않는다. */
export function deltaTone(deltaPct: number | null | undefined): DeltaTone {
  if (deltaPct === null || deltaPct === undefined) return "flat";
  if (deltaPct >= DELTA_MIN_PCT) return "up";
  if (deltaPct <= -DELTA_MIN_PCT) return "down";
  return "flat";
}

export function simDeltaColor(deltaPct: number | null | undefined): string {
  if (deltaPct != null && deltaPct >= DELTA_STRONG_PCT) return SIM_COLORS.improvedStrong;
  const tone = deltaTone(deltaPct);
  if (tone === "up") return SIM_COLORS.improved;
  if (tone === "down") return SIM_COLORS.worsened;
  return SIM_COLORS.unchanged;
}
