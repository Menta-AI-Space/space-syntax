/** 답변의 산문 섹션(배후 수요·소비력·경쟁) 밑에 붙일 미니 차트의 데이터.
 *
 * LLM 문장에서 숫자를 파싱하지 않는다 — 문장은 매번 다르게 오고, 파싱이
 * 빗나가면 그래프가 틀린 값을 그린다. 응답에 이미 실려 오는 엔진 값
 * (site.demand.seoul_rank, sales_benchmark, subscores)만 쓴다. 섹션 제목은
 * 백엔드 프롬프트가 고정한다("배후 수요"·"소비력·매출"·"경쟁").
 */

import type { SiteScoreResponse } from "./api";
import { fmtQuarter } from "./agentFormat.ts";

export type SectionVisual = (
  | { kind: "gauges"; hint: string; rows: { label: string; pct: number }[] }
  | { kind: "compare"; unit: string; rows: { label: string; value: number; display: string }[] }
  | { kind: "score"; label: string; score: number; inverse: boolean }
) & {
  /** 숫자의 출처 — "어디서 온 값인지"를 항상 병기한다(GrowthFactor의
   * traced-to-source). 지어내지 않는다: 백엔드 적재 스크립트가 실제로 쓰는
   * 데이터셋 이름만 적는다. */
  source?: string;
};

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function manwon(won: number): string {
  return `${Math.round(won / 10_000).toLocaleString()}만원`;
}

export function sectionVisual(
  title: string, site: SiteScoreResponse | null | undefined
): SectionVisual | null {
  if (!site || !title) return null;
  const t = title.replace(/\s/g, "");

  if (t.includes("배후수요")) {
    const rank = (site.demand as Record<string, unknown> | null | undefined)?.[
      "seoul_rank"
    ] as Record<string, unknown> | undefined;
    if (!rank) return null;
    const rows = (
      [
        { label: "거주인구", pct: num(rank["population_top_pct"]) },
        { label: "직장인", pct: num(rank["workers_top_pct"]) },
        { label: "청년(20대)", pct: num(rank["youth_top_pct"]) },
      ] as { label: string; pct: number | null }[]
    ).filter((r): r is { label: string; pct: number } => r.pct !== null && r.pct >= 0 && r.pct <= 100);
    return rows.length
      ? {
          kind: "gauges", hint: "막대가 길수록 서울 안에서 상위입니다.", rows,
          // demand_benchmark.py — SGIS(통계청) 인구·사업체 집계 기반 서울 순위.
          source: "통계청 SGIS 인구·사업체 집계",
        }
      : null;
  }

  if (t.includes("소비력") || t.includes("매출")) {
    const bench = site.sales_benchmark;
    const here = num(bench?.per_store_monthly_won);
    const seoul = num(bench?.citywide_median_per_store_won);
    if (here === null || seoul === null || (here <= 0 && seoul <= 0)) return null;
    return {
      kind: "compare",
      unit: "점포당 월매출",
      rows: [
        { label: "이 자리", value: here, display: manwon(here) },
        { label: "서울 중앙값", value: seoul, display: manwon(seoul) },
      ],
      source: [
        "서울시 상권분석서비스",
        bench?.quarter ? fmtQuarter(bench.quarter) : "",
        bench?.scope === "dong" ? "행정동 평균 대체" : bench?.trdar_nm ? `${bench.trdar_nm} 상권 실측` : "",
      ].filter(Boolean).join(" · "),
    };
  }

  if (t.includes("경쟁")) {
    const sub = site.subscores?.find((s) => s.key === "competition");
    const score = num(sub?.score);
    if (score === null) return null;
    // 경쟁 밀도는 높을수록 나쁘다 — 그리는 쪽이 inverse 색으로 칠하고
    // "높을수록 불리"를 함께 적어야 한다. 초록 막대로 그리면 꽉 찬 막대가
    // "아주 좋음"으로 읽힌다(SiteScorePanel과 같은 규칙).
    return {
      kind: "score", label: sub?.label || "경쟁 밀도", score, inverse: true,
      source: "서울시 상권분석서비스 점포 데이터 · 반경 내 동일업종 집계",
    };
  }

  return null;
}
