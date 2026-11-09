import type { CannibalizationResponse } from "./api.ts";
import { fmtPct, hasNumber, type VerdictTone } from "./agentFormat.ts";

// AgentChat.tsx의 CannibalizationToolCard가 그리는 값을 순수 함수로 뽑아
// 테스트 가능하게 한다(그 파일은 JSX라 이 저장소의 테스트 러너로 직접
// import할 수 없다 — node --experimental-strip-types는 .tsx를 못 읽는다).
//
// self_total_count는 브랜드가 주어져 자기 매장을 실제로 찾았을 때만 0보다
// 크다(backend/app/routers/agent.py의 같은 주석 참고) — 0/undefined일 땐
// "자기잠식 없음"이 아니라 "브랜드를 몰라 안 찾음"일 수 있으므로 그 경우
// 자기잠식 관련 값은 아무것도 만들지 않는다.

export interface CannibalizationTile {
  label: string;
  value: string;
  tone?: VerdictTone;
}

export interface CannibalizationView {
  tiles: CannibalizationTile[];
  competitors: string[];
  hasSelfData: boolean;
  selfTotal: number;
  selfCompetitors: string[];
  selfOverRecommended: boolean;
  demandFactor: number | null;
}

export function buildCannibalizationView(data: CannibalizationResponse, modeLabel: string): CannibalizationView {
  const selfTotal = data.self_total_count ?? 0;
  const hasSelfData = selfTotal > 0;
  const recMax = data.overlap_recommended_max_pct ?? 25;
  const selfOverRecommended = hasSelfData && hasNumber(data.self_overlap_pct) && (data.self_overlap_pct as number) > recMax;

  const tiles: CannibalizationTile[] = [
    { label: "잠식 위험", value: `${Math.round(data.risk_score)}점` },
  ];
  if (hasNumber(data.overlap_pct)) {
    tiles.push({ label: "상권 중복", value: fmtPct(data.overlap_pct, 0) });
  }
  tiles.push({ label: `${modeLabel} ${data.minutes}분 동일업종`, value: `${data.total_count}곳` });
  if (hasSelfData) {
    tiles.push({ label: "내 브랜드 매장", value: `${selfTotal}곳`, tone: "caution" });
    if (hasNumber(data.self_overlap_pct)) {
      tiles.push({
        label: "자기잠식 겹침",
        value: fmtPct(data.self_overlap_pct, 0),
        tone: selfOverRecommended ? "negative" : "positive",
      });
    }
  }

  const competitors = (data.competitors ?? []).slice(0, 5).map((c) => `${c.name} · ${Math.round(c.distance_m)}m`);
  const selfCompetitors = hasSelfData
    ? (data.self_competitors ?? []).slice(0, 5).map((c) => `${c.name} · ${Math.round(c.distance_m)}m`)
    : [];

  return {
    tiles,
    competitors,
    hasSelfData,
    selfTotal,
    selfCompetitors,
    selfOverRecommended,
    demandFactor: hasSelfData && hasNumber(data.demand_factor) ? (data.demand_factor as number) : null,
  };
}
