"use client";

import { useEffect, useId, useState } from "react";
import type { CannibalizationResponse, CompetitorOut } from "@/lib/api";
import { COMPETITORS_PER_PAGE, competitorAvatarEmoji, isLowPriceCompetitor, ISO_MODE_LABELS, riskBadgeClass, vennCenterDistance } from "./dashboardUtils";

const MINUTE_OPTIONS = [3, 5, 10, 15];

function CompetitorTag({ name, category }: { name: string; category: string }) {
  if (isLowPriceCompetitor(name)) return <span className="dash-tag dash-tag--warn">저가 경쟁</span>;
  if (category) return <span className="dash-tag">{category}</span>;
  return null;
}

function CompetitorCard({ c }: { c: CompetitorOut }) {
  const emoji = competitorAvatarEmoji(c.category, c.kakao_category);
  const initial = (c.category || "업").charAt(0);
  return (
    <li className="dash-comp-item dash-comp-item--card">
      {emoji ? <span className="dash-comp-avatar" aria-hidden="true">{emoji}</span> : <span className="dash-comp-avatar dash-comp-avatar--initial" aria-hidden="true">{initial}</span>}
      <div className="dash-comp-body">
        <div className="dash-comp-head">
          <span className="dash-comp-name">{c.name}</span>
          <span className="dash-comp-dist">{Math.round(c.distance_m)}m</span>
        </div>
        <div className="dash-comp-meta">
          <CompetitorTag name={c.name} category={c.category} />
          {c.place_url && <a className="dash-comp-link" href={c.place_url} target="_blank" rel="noopener noreferrer">지도에서 보기</a>}
        </div>
      </div>
    </li>
  );
}

function VennOverlap({ cann }: { cann: CannibalizationResponse }) {
  // clipPath id는 문서 전역이다. 고정 문자열로 두면 이 카드가 한 화면에 둘 이상
  // 뜰 때 서로의 클립을 덮어써 겹침 부분이 엉뚱하게 잘린다.
  const clipId = useId().replace(/:/g, "");
  const pct = cann.overlap_pct;
  if (pct == null) {
    return <div className="dash-venn dash-venn--empty"><p className="demand-meta">겹침 분석: 동일업종 경쟁점 없음</p></div>;
  }
  const overRec = pct > (cann.overlap_recommended_max_pct ?? 25);
  // 원 사이 거리를 겹침 비율에서 역산해, 그림이 옆에 적힌 %를 그대로 보여 준다.
  // 반지름은 두 원이 안 겹칠 때(d=2r)도 뷰박스 안에 들어오도록 잡는다.
  const R = 26;
  const d = vennCenterDistance(pct / 100, R);
  const cxLeft = 60 - d / 2;
  const cxRight = 60 + d / 2;
  return (
    <div className="dash-venn">
      <svg viewBox="0 0 120 80" className="dash-venn-svg" role="img" aria-label={`상권 중복 ${pct}%`}>
        <defs>
          <clipPath id={clipId}>
            <circle cx={cxLeft} cy={40} r={R} />
          </clipPath>
        </defs>
        <circle cx={cxLeft} cy={40} r={R} fill="#dbeafe" stroke="#3b82f6" strokeWidth={1.5} opacity={0.9} />
        <circle cx={cxRight} cy={40} r={R} fill="#fed7aa" stroke="#f97316" strokeWidth={1.5} opacity={0.85} />
        {/* 겹치는 부분만 진하게 — 두 원의 교집합을 clip으로 정확히 잘라낸다. */}
        <circle cx={cxRight} cy={40} r={R} fill="#7c3aed" opacity={0.45} clipPath={`url(#${clipId})`} />
      </svg>
      <p className={`dash-venn-stat${overRec ? " dash-venn-stat--warn" : ""}`}>
        <strong>{pct}%</strong> {cann.overlap_with || "최근접"}과 상권 중복
      </p>
      <p className="demand-meta">권장 기준 {cann.overlap_recommended_max_pct ?? 25}% 이하</p>
    </div>
  );
}

// 프랜차이즈 브랜드 입력 시 자사 매장(자기잠식)을 경쟁 목록에서 분리해 위쪽에
// 보여준다. self_overlap_pct는 최근접 자사 매장과의 도달권 겹침으로, 권장
// 기준(overlap_recommended_max_pct, 기본 25%) 이하면 green, 초과면 red.
function SelfCannibalBlock({ cann }: { cann: CannibalizationResponse }) {
  const selfTotal = cann.self_total_count ?? 0;
  if (selfTotal <= 0) return null;
  const stores = cann.self_competitors || [];
  const pct = cann.self_overlap_pct;
  const recMax = cann.overlap_recommended_max_pct ?? 25;
  const over = pct != null && pct > recMax;
  return (
    <div className="dash-self-block">
      <h4 className="dash-subtitle">내 브랜드 자기잠식 <span className="dash-comp-total">({selfTotal}곳)</span></h4>
      <ul className="dash-comp-list">
        {stores.slice(0, COMPETITORS_PER_PAGE).map((c, i) => (
          <li className="dash-comp-item dash-comp-item--card" key={`self-${c.name}-${i}`}>
            <span className="dash-comp-avatar dash-comp-avatar--initial" aria-hidden="true">自</span>
            <div className="dash-comp-body">
              <div className="dash-comp-head">
                <span className="dash-comp-name">{c.name}</span>
                <span className="dash-comp-dist">{Math.round(c.distance_m)}m</span>
              </div>
            </div>
          </li>
        ))}
      </ul>
      {selfTotal > Math.min(stores.length, COMPETITORS_PER_PAGE) && (
        <p className="demand-meta">가까운 {Math.min(stores.length, COMPETITORS_PER_PAGE)}곳 표시 · 총 {selfTotal}곳</p>
      )}
      {pct != null && (
        <p className={`status-banner ${over ? "status-banner--error" : "status-banner--success"}`}>
          최근접 자사 매장과 도달권 <strong>{pct}%</strong> 중복 — 권장 기준 {recMax}% {over ? "초과" : "이하"}
        </p>
      )}
      {cann.demand_factor != null && <p className="site-panel-meta">유동인구 보정 ×{cann.demand_factor.toFixed(2)}</p>}
    </div>
  );
}

interface CannibalizationSectionProps {
  cann: CannibalizationResponse | null;
  loading: boolean;
  onMinutesChange: (minutes: number) => void;
}

// Ported from `renderCannibalizationSection`/`renderCompetitorCard`/`renderVennOverlap`
// in frontend/js/dashboard.js. The academy-specific placeholder branch is not
// ported (out of Stage 2 scope — academy subject clustering is Stage 3).
export function CannibalizationSection({ cann, loading, onMinutesChange }: CannibalizationSectionProps) {
  const [page, setPage] = useState(0);

  useEffect(() => {
    setPage(0);
  }, [cann?.minutes, cann?.mode]);

  if (!cann) return null;

  const mode = cann.mode || "walk";
  const modeLabel = ISO_MODE_LABELS[mode] || mode;
  const minutes = cann.minutes ?? 5;
  const total = cann.total_count ?? cann.competitors.length;
  const all = cann.competitors || [];
  const pageCount = Math.max(1, Math.ceil(all.length / COMPETITORS_PER_PAGE));
  const safePage = Math.max(0, Math.min(page, pageCount - 1));
  const start = safePage * COMPETITORS_PER_PAGE;
  const pageItems = all.slice(start, start + COMPETITORS_PER_PAGE);
  const score = Math.round(cann.risk_score);

  return (
    <section className={`dash-card dash-card--cannibal${loading ? " dash-card--loading" : ""}`} data-dash="cannibal">
      <header className="dash-card-header">
        <span className="dash-card-icon" aria-hidden="true">⚔</span>
        <h3>동일업종 잠식 분석</h3>
        <span className={`dash-risk ${riskBadgeClass(cann.risk_label)}`}>잠식 위험 {cann.risk_label} {score}/100</span>
      </header>
      <SelfCannibalBlock cann={cann} />
      {(cann.self_total_count ?? 0) > 0 && <h4 className="dash-subtitle">동종 경쟁</h4>}
      <div className="dash-cannibal-grid">
        <div className="dash-cannibal-col">
          <h4 className="dash-subtitle">{modeLabel} {minutes}분 도달권 동일업종 <span className="dash-comp-total">({total}곳)</span></h4>
          <div className="dash-minute-tabs" role="group" aria-label={`${modeLabel} 도달 시간`}>
            {MINUTE_OPTIONS.map((m) => (
              <button key={m} type="button" className={`dash-minute-btn${m === minutes ? " dash-minute-btn--active" : ""}`} onClick={() => onMinutesChange(m)}>
                {m}분
              </button>
            ))}
          </div>
          <ul className="dash-comp-list">
            {pageItems.length ? pageItems.map((c, i) => <CompetitorCard key={`${c.name}-${start + i}`} c={c} />) : <li className="dash-comp-item"><span className="demand-meta">해당 도달권 내 동일업종 없음</span></li>}
          </ul>
          {total > all.length && (
            <p className="demand-meta">가까운 {all.length}곳 표시 · 총 {total}곳</p>
          )}
          {pageCount > 1 && (
            <nav className="dash-comp-pagination" aria-label="동일업종 목록 페이지">
              <button type="button" className="dash-page-btn" disabled={safePage === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>이전</button>
              <span className="dash-page-info">{safePage + 1} / {pageCount}</span>
              <button type="button" className="dash-page-btn" disabled={safePage >= pageCount - 1} onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}>다음</button>
            </nav>
          )}
        </div>
        <div className="dash-cannibal-col">
          <h4 className="dash-subtitle">도달권 겹침 (같은 브랜드 출점 시)</h4>
          <VennOverlap cann={cann} />
        </div>
      </div>
      <p className="dash-strategy">{cann.detail}</p>
    </section>
  );
}
