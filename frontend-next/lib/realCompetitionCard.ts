import type { RealCompetitionResponse } from "@/lib/api";

/**
 * reach_label은 backend(app/services/real_competition.py)가 reach_mode·
 * reach_minutes를 합쳐 이미 "도보 5분"처럼 만들어 보낸 문자열이다 — 프런트에서
 * mode/minutes를 따로 조합하면 라벨 매핑이 backend와 어긋날 수 있으니 그대로
 * 쓴다. reach_label이 없으면(권역 계산이 안 된 응답) 권역을 지어내지 않고
 * null을 돌려준다.
 */
export function competitionReachLine(comp: RealCompetitionResponse | null | undefined): string | null {
  if (!comp || !comp.reach_label) return null;
  return `분석 권역: ${comp.reach_label}`;
}
