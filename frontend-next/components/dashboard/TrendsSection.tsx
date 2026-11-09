"use client";

import type { TrendsResponse, TrendSeries } from "@/lib/api";

function TrendChart({ series }: { series: TrendSeries | null | undefined }) {
  if (!series?.quarters?.length || !series.category_counts?.length) {
    return <div className="dash-chart dash-chart--empty"><p className="demand-meta">분기 추이 데이터 없음</p></div>;
  }
  const w = 280;
  const h = 100;
  const pad = { t: 12, r: 8, b: 18, l: 8 };
  const innerW = w - pad.l - pad.r;
  const innerH = h - pad.t - pad.b;
  const cat = series.category_counts;
  const tot = series.total_counts?.length ? series.total_counts : cat;
  const all = [...cat, ...tot];
  const minV = Math.min(...all);
  const maxV = Math.max(...all);
  const span = maxV - minV || 1;
  const n = cat.length;

  const toPoints = (vals: number[]) =>
    vals
      .map((v, i) => {
        const x = pad.l + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
        const y = pad.t + innerH - ((v - minV) / span) * innerH;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");

  const catGrowth = series.category_growth_pct;
  const totGrowth = series.total_growth_pct;
  const catLabel = series.category_label || "업종";

  return (
    <div className="dash-chart">
      {catGrowth != null && <span className="dash-growth-badge">{catLabel} {catGrowth >= 0 ? "+" : ""}{catGrowth}%</span>}
      <svg viewBox={`0 0 ${w} ${h}`} className="dash-trend-svg" aria-label="분기별 점포 추이">
        <polyline points={toPoints(tot)} fill="none" stroke="#94a3b8" strokeWidth={1.5} strokeDasharray="4 3" />
        <polyline points={toPoints(cat)} fill="none" stroke="#7c3aed" strokeWidth={2.5} />
      </svg>
      <div className="dash-chart-legend">
        <span className="dash-legend-item dash-legend-item--cat">{catLabel}</span>
        <span className="dash-legend-item dash-legend-item--total">전체 점포 {totGrowth != null ? `${totGrowth >= 0 ? "+" : ""}${totGrowth}%` : ""}</span>
      </div>
    </div>
  );
}

// Ported from `renderTrendsSection`/`renderTrendChart` in frontend/js/dashboard.js.
export function TrendsSection({ trends }: { trends: TrendsResponse | null }) {
  if (!trends) return null;
  const signals = (trends.street_signals || []).slice(0, 5);
  const quarterHint = trends.series?.quarters?.length ? `${trends.series.quarters.length}분기 스냅샷` : "분기 데이터";

  return (
    <section className="dash-card dash-card--trends">
      <header className="dash-card-header">
        <span className="dash-card-icon" aria-hidden="true">📈</span>
        <h3>뜨는 거리 추적</h3>
        <span className="dash-card-badge dash-card-badge--muted">{quarterHint}</span>
      </header>
      <TrendChart series={trends.series} />
      <h4 className="dash-subtitle">이번 분기 성장 신호 상위</h4>
      <ol className="dash-signal-list">
        {signals.length ? signals.map((s, i) => (
          <li key={`${s.name}-${i}`} className="dash-signal-item">
            <span className="dash-signal-rank">{i + 1}</span>
            <span className="dash-signal-name">{s.name}</span>
            <span className={`dash-signal-label dash-signal--${s.label === "신호 강함" ? "strong" : "watch"}`}>{s.label}</span>
          </li>
        )) : <li className="demand-meta">신호 데이터 없음</li>}
      </ol>
      {(trends.warnings || []).map((w, i) => <p key={i} className="demand-meta dash-iso-warn">{w}</p>)}
      <p className="dash-footnote">※ {trends.data_scope || "데이터 출처 미상"}</p>
    </section>
  );
}
