"use client";

import type { AnalogsResponse } from "@/lib/api";
import { fmtQuarter } from "@/lib/agentFormat";

/** "이 자리랑 비슷한 동네" 카드.
 *
 * 읽는 순서를 강제한다: 이 상권의 성과 → 구조가 닮은 곳들의 성과. 같은
 * 구조인데 성과가 다르면 그 차이가 곧 질문거리다("성산중학교 상권은 같은
 * 구조로 888만원을 버는데 여긴 왜 245만원인가"). 유사도의 근거(무엇으로
 * 쟀는지)와 출처를 항상 병기한다 — 근거 없는 순위는 신뢰를 깎는다.
 */
export function AnalogsToolCard({ data }: { data: AnalogsResponse }) {
  const rows = data.analogs ?? [];
  if (rows.length === 0) return null;
  const man = (won: number | null | undefined) =>
    typeof won === "number" ? `${Math.round(won / 10_000).toLocaleString()}만원` : "—";
  const maxPer = Math.max(
    ...[data.subject, ...rows].map((r) => r.per_store_monthly_won ?? 0), 1,
  );

  return (
    <div className="card report-block analogs-card">
      <h3>구조가 비슷한 상권과 성과 비교</h3>
      <p className="site-panel-meta">
        유사도 기준: {data.basis} · 점포당 월매출은 성과 비교용(유사도에 미포함)
      </p>

      <div className="analogs-rows">
        {[{ ...data.subject, similarity: null }, ...rows].map((r, i) => (
          <div key={r.trdar_cd} className={`analogs-row${i === 0 ? " analogs-row--subject" : ""}`}>
            <div className="analogs-row__head">
              <strong>{i === 0 ? `${r.trdar_nm} (이 자리)` : r.trdar_nm}</strong>
              {typeof r.similarity === "number" && (
                <span className="analogs-row__sim">유사도 {r.similarity}</span>
              )}
            </div>
            <div className="analogs-row__bar">
              <span style={{ width: `${Math.max(2, ((r.per_store_monthly_won ?? 0) / maxPer) * 100)}%` }} />
            </div>
            <div className="analogs-row__meta">
              점포당 {man(r.per_store_monthly_won)} · 동일업종 {r.store_count ?? "—"}곳
              {typeof r.weekend_share_pct === "number" ? ` · 주말 ${r.weekend_share_pct}%` : ""}
            </div>
          </div>
        ))}
      </div>

      <p className="answer-viz__hint">{data.data_source} · {fmtQuarter(data.quarter)} 기준</p>
    </div>
  );
}
