/**
 * 토지가치 6탭이 **무엇을 그릴 수 있는가**를 정하는 한 곳.
 *
 * 화면(`components/landvalue/`)에서 `analysis.growth_outlook &&` 같은 판단을
 * 흩어 두면, 없는 데이터가 어느 탭에서 어떻게 보이는지가 컴포넌트마다 달라진다.
 * 그래서 "이 탭에 그릴 값이 있나 / 없으면 왜 없나"를 여기 모으고 테스트한다.
 *
 * ⚠️ **없는 값을 0이나 빈 문자열로 채우지 않는다.** 지금 저장소에 추정 모형이
 * 없어서(`land_analysis_source.py`) 실서비스 응답은 `current_valuation`·
 * `growth_outlook`·`price_forecast`가 전부 `null`로 온다. 그 상태에서 Gap을
 * "0.0%"로 그리면 사용자는 "차이가 없다"로 읽는다 — 실제로는 "모른다"다.
 */

import type { StructuredAnalysis, Verdict } from "@/lib/landAnalysis";

export type LandTabId = "overview" | "future" | "value" | "hotspot" | "place" | "compare";

export interface LandTabDef {
  id: LandTabId;
  label: string;
  /** 이 탭이 답하는 질문 하나. 탭 머리에 그대로 적는다. */
  question: string;
}

/**
 * 순서는 `docs/SPEC_LAND_VALUE_TABS_UI.md` — 「미래전망」이 「가치평가」 앞이다
 * (소유자 결정 2026-08-24).
 */
export const LAND_TABS: LandTabDef[] = [
  { id: "overview", label: "종합", question: "한 화면으로 보면 어떤 땅인가?" },
  { id: "future", label: "미래전망", question: "앞으로 값이 어떻게 될 가능성이 있는가?" },
  { id: "value", label: "가치평가", question: "지금 이 땅은 같은 조건 대비 낮은가 높은가?" },
  { id: "hotspot", label: "유망입지", question: "이 자리가 주변보다 더 오를 자리인가?" },
  { id: "place", label: "입지·도시계획", question: "왜 그런가 — 어떤 자리이고 어떤 계획이 걸려 있는가?" },
  { id: "compare", label: "주변비교", question: "무엇과 견준 결과인가?" },
];

/**
 * 이 탭에 **실제 값이 하나라도** 있는가.
 *
 * 「종합」은 다른 탭의 요약이라 다른 탭이 하나라도 살아 있으면 산다.
 */
export function tabHasData(analysis: StructuredAnalysis, id: LandTabId): boolean {
  switch (id) {
    case "future":
      return (analysis.price_forecast?.horizons.length ?? 0) > 0;
    case "hotspot":
      return analysis.growth_outlook != null;
    case "value":
      // 추정 모형이 없어도 공시지가와 비교군 안 자리는 남는다 — 그래서 이 탭이
      // 통째로 비지는 않는다. `current_valuation`은 그 위에 얹히는 층이다.
      return analysis.official_price != null || analysis.peer_position != null;
    case "place":
      return analysis.attributes.length > 0 || analysis.factors.length > 0;
    case "compare":
      return analysis.peer_group.size > 0;
    case "overview":
      return LAND_TABS.some((t) => t.id !== "overview" && tabHasData(analysis, t.id));
  }
}

/**
 * 왜 비어 있는가 — 화면이 사용자에게 말할 한 줄.
 *
 * "데이터 없음"으로 뭉뚱그리지 않는다. 모형이 아직 없는 것과 이 필지에만 값이
 * 없는 것은 사용자가 할 수 있는 일이 다르다(전자는 기다리는 것, 후자는 다른
 * 필지를 눌러 보는 것).
 */
export function missingReason(id: LandTabId): string {
  switch (id) {
    case "future":
      return "앞날의 공시지가를 내다보는 모형이 아직 없습니다. 지어낸 값을 대신 보여주지 않습니다.";
    case "hotspot":
      return "주변보다 빨리 오르는 자리인지 가리는 모형이 아직 없습니다. 지어낸 값을 대신 보여주지 않습니다.";
    case "value":
      return "이 필지의 공시지가와 비교군 자료를 불러오지 못했습니다.";
    case "place":
      return "이 필지의 토지특성 자료가 없습니다.";
    case "compare":
      return "견줄 만한 같은 조건 필지를 찾지 못했습니다.";
    case "overview":
      return "이 필지에 대해 보여줄 수 있는 값이 아직 없습니다.";
  }
}

/**
 * 처음 열리는 탭.
 *
 * 스펙의 default는 「미래전망」이다 — 상단 Summary가 이미 요약이라 「종합」으로
 * 열면 같은 것을 두 번 읽는다. 그런데 **지금 그 탭이 통째로 비어 있어서**, 그대로
 * 열면 첫 화면이 "값이 없습니다" 한 줄이다. 그래서 스펙 순서를 지키되 **값이 있는
 * 첫 탭**을 연다 — 모형이 붙으면 규칙을 안 고쳐도 「미래전망」이 다시 default가 된다.
 */
export function defaultTab(analysis: StructuredAnalysis): LandTabId {
  const order: LandTabId[] = ["future", "value", "hotspot", "place", "compare"];
  return order.find((id) => tabHasData(analysis, id)) ?? "overview";
}

export type SummaryTone = "neutral" | "low" | "high";

export interface SummaryRow {
  key: string;
  value: string;
  tone: SummaryTone;
  /** 금액을 시세로 읽지 않게 하는 꼬리표. 없으면 붙이지 않는다. */
  note?: string;
}

export function wonPerSqm(won: number): string {
  return `${Math.round(won / 10_000).toLocaleString("ko-KR")}만원/㎡`;
}

/** 부호가 뜻의 전부인 값에만 쓴다. */
export function signedPct(pct: number, digits = 1): string {
  return `${pct >= 0 ? "+" : "−"}${Math.abs(pct).toFixed(digits)}%`;
}

/** 백분위 0이 "상위 100%"로 읽히지 않게 아래쪽 절반은 뒤집어 말한다. */
export function percentileText(percentile: number): string {
  const p = Math.round(percentile);
  return p >= 50 ? `상위 ${Math.max(1, 100 - p)}%` : `하위 ${Math.max(1, p)}%`;
}

/**
 * 예상 공시지가는 **범위로만** 쓴다. `point`를 단독으로 찍지 않는다
 * (`lib/landAnalysis.ts`의 계약 3항).
 */
export function rangeText(low: number, high: number): string {
  return `${wonPerSqm(low)} ~ ${wonPerSqm(high)}`;
}

/**
 * 상단 Summary — **값이 없는 칸은 만들지 않는다.**
 *
 * 빈 칸을 "—"로 남겨 두면 다섯 칸 자리가 유지돼 "측정했는데 0"으로 읽힌다.
 */
export function summaryRows(analysis: StructuredAnalysis): SummaryRow[] {
  const rows: SummaryRow[] = [];
  const { official_price: official, current_valuation: valuation } = analysis;

  if (official) {
    rows.push({
      key: `${official.base_year} ${official.label}`,
      value: wonPerSqm(official.per_sqm),
      tone: "neutral",
      note: "시세가 아닙니다",
    });
  }

  if (valuation) {
    const expected = valuation.expected_official_price;
    rows.push({
      key: "조건이 비슷한 필지로 미루어 본 값",
      value: rangeText(expected.low, expected.high),
      tone: "neutral",
      note: "확정값이 아닌 범위",
    });
    const labels = discountLabels(valuation.discount.total_pct);
    rows.push({
      key: labels.unexplained,
      value: `${Math.abs(valuation.discount.unexplained_pct).toFixed(1)}%`,
      tone: labels.tone,
    });
  }

  const forecast = analysis.price_forecast?.horizons.find((h) => h.years === 3);
  if (forecast?.annualized_change_pct != null) {
    rows.push({
      key: "3년 전망 (연 환산)",
      value: signedPct(forecast.annualized_change_pct),
      tone: "neutral",
    });
  }

  const excess = analysis.growth_outlook?.excess_growth_pct;
  if (excess != null) {
    rows.push({
      key: `같은 조건 대비 초과 상승 (${analysis.growth_outlook?.horizon_years}년)`,
      value: signedPct(excess),
      tone: excess >= 0 ? "low" : "high",
    });
  }

  const pricePercentile = analysis.peer_position?.price_percentile;
  if (pricePercentile != null) {
    rows.push({
      key: "비교군 안 공시지가 자리",
      value: percentileText(pricePercentile),
      tone: "neutral",
    });
  }

  return rows;
}

/**
 * 판정 한 줄의 어조.
 *
 * `strength`가 약하거나 판정을 보류했으면 단정하지 않는다
 * (`allowsAssertiveWording`과 같은 기준을 화면 쪽 표현으로 옮긴 것).
 */
export function verdictTone(verdict: Verdict): SummaryTone {
  if (verdict.label === "undervalued" || verdict.label === "cheap_with_reason") return "low";
  if (verdict.label === "above_peers") return "high";
  return "neutral";
}

export interface DiscountLabels {
  /** 이 필지가 미루어 본 값보다 싼가. 문구·부호가 전부 여기서 갈린다. */
  isBelow: boolean;
  heading: string;
  total: string;
  explained: string;
  unexplained: string;
  tone: SummaryTone;
}

/**
 * 할인분 문구를 **방향에 따라** 바꾼다.
 *
 * `DiscountBreakdown`은 "싼 쪽"을 양수로 재는 하나의 축이다. 그래서 미루어 본
 * 값보다 **비싼** 필지는 `total_pct`가 음수로 오고, 그대로 그리면 화면에
 * `단점으로 설명되는 몫 −5.3%`처럼 뜻이 없는 말이 나온다. 값이 음수라는 것은
 * 오류가 아니라 **반대 방향**이라는 뜻이므로, 부호 대신 말을 뒤집는다.
 *
 * ⚠️ 비싼 쪽에 낙인 표현("고평가")을 쓰지 않는다 — 우리가 안 보는 요인(한강뷰·
 * 학군·역세권)으로 비쌀 수 있다.
 */
export function discountLabels(totalPct: number): DiscountLabels {
  if (totalPct >= 0) {
    return {
      isBelow: true,
      heading: "낮은 값을 무엇이 설명하는가",
      total: "미루어 본 값보다 낮은 폭",
      explained: "단점으로 설명되는 몫",
      unexplained: "단점으로 설명되지 않는 몫",
      tone: "low",
    };
  }
  return {
    isBelow: false,
    heading: "높은 값을 무엇이 설명하는가",
    total: "미루어 본 값보다 높은 폭",
    explained: "장점으로 설명되는 몫",
    unexplained: "장점으로 설명되지 않는 몫",
    tone: "high",
  };
}
