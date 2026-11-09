// 지도 분석 길 색 5단계 (2026-10-27). top_pct = 서울 길 구간 가운데 「상위 몇 %」(1~100).
export type Tier = 1 | 2 | 3 | 4 | 5;

export function tierOf(topPct: number): Tier {
  if (topPct <= 2) return 1;
  if (topPct <= 10) return 2;
  if (topPct <= 25) return 3;
  if (topPct <= 50) return 4;
  return 5;
}

// 길을 눌렀을 때 쓰는 순위 문구 (2026-11-06, UX 자동 개선). 범례가 아래 절반을 「하위 50%」로
// 부르는데 누르면 「상위 80%」라고 나와 서로 다른 말처럼 읽혔다 — 아래 절반은 「하위」로 말한다.
export function rankLabel(topPct: number): string {
  if (topPct > 50) return `하위 ${Math.max(1, Math.round(100 - topPct))}%`;
  return `상위 ${topPct}%`;
}
