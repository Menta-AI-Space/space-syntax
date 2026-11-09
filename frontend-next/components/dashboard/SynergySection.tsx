"use client";

import type { SynergyResponse } from "@/lib/api";
import { synergyBucketClass, synergyScoreLevel } from "./dashboardUtils";

interface SynergySectionProps {
  synergy: SynergyResponse | null;
  loading: boolean;
  error: string;
}

// Ported from `renderSynergySection` in frontend/js/dashboard.js. Per that
// source's own comment, `synergy` is not part of `/api/dashboard` — it comes
// from `/api/real-competition`'s `RealCompetitionBundleResponse.synergy`, so
// this section fetches that endpoint independently (see DashboardPanel).
export function SynergySection({ synergy, loading, error }: SynergySectionProps) {
  if (loading) {
    return (
      <section className="dash-card dash-card--synergy" data-dash="synergy">
        <header className="dash-card-header">
          <span className="dash-card-icon" aria-hidden="true">🌿</span>
          <h3>보완업체 시너지</h3>
        </header>
        <p className="demand-meta">시너지 분석 중...</p>
      </section>
    );
  }
  if (error) {
    return (
      <section className="dash-card dash-card--synergy" data-dash="synergy">
        <header className="dash-card-header">
          <span className="dash-card-icon" aria-hidden="true">🌿</span>
          <h3>보완업체 시너지</h3>
        </header>
        <p className="demand-meta">시너지 분석 실패: {error}</p>
      </section>
    );
  }
  if (!synergy) return null;

  const score = Math.round(synergy.scores.complementary_synergy ?? 0);
  const pressure = Math.round(synergy.scores.direct_competition_pressure ?? 0);
  const buckets = synergy.clusters.filter((b) => b.count > 0);

  return (
    <section className="dash-card dash-card--synergy" data-dash="synergy">
      <header className="dash-card-header">
        <span className="dash-card-icon" aria-hidden="true">🌿</span>
        <h3>보완업체 시너지 ({synergy.target_label} 기준)</h3>
        <span className="dash-card-badge dash-card-badge--synergy">+시너지 {score}점 · {synergyScoreLevel(score)}</span>
      </header>
      <p className="demand-meta">오피스·헬스장·병원·대학·환승시설처럼 배후 수요를 보내주는 보완업체를 집계합니다. (동일업종 직접 경쟁 압박은 별도 참고용 {pressure}점)</p>
      {synergy.interpretation.map((n, i) => <p key={i} className="dash-strategy">{n}</p>)}
      {(synergy.warnings || []).map((w, i) => <p key={i} className="demand-meta dash-iso-warn">{w}</p>)}
      <ul className="synergy-bucket-list">
        {buckets.length ? buckets.map((b) => (
          <li key={b.key} className={`synergy-bucket ${synergyBucketClass(b.key)}`}>
            <div className="synergy-bucket-head">
              <strong>{b.label}</strong>
              <span className="synergy-bucket-count">{b.count}곳</span>
            </div>
            <p className="demand-meta">가중점수 {b.weighted_score} · {b.intensity}</p>
          </li>
        )) : <li className="demand-meta">집계된 보완·경쟁 시설 없음</li>}
      </ul>
    </section>
  );
}
