import type { PropertyMarketResult } from "@/lib/api";

/**
 * time_adjusted/as_of는 backend(property_market.py)가 "이 밴드는 시점보정 없이
 * 최근 N개월 원자료를 그대로 쓴다"는 걸 명시하려고 넣은 필드다. 지금은 backend가
 * 항상 time_adjusted=false를 주지만(시점보정 로직이 아직 없다), 나중에 보정이
 * 붙어 true가 와도 프런트가 그대로 따라가도록 값을 고정 문구로 지어내지 않고
 * 실제로 내려온 값을 읽는다. as_of가 비어 있으면(구버전 응답 등) 아무것도
 * 만들지 않는다 — 없는 기준일을 지어내지 않는다.
 */
export function marketTimingNote(
  market: Pick<PropertyMarketResult, "time_adjusted" | "as_of">,
): string | null {
  if (!market.as_of) return null;
  const basis = market.time_adjusted ? "시점보정 적용" : "시점보정 없음(원자료)";
  return `${basis} · ${market.as_of} 기준 조회`;
}
