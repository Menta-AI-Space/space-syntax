"use client";

import { type AreaInsightsResponse, type FootfallBreakdown, type FootfallProfile, type FootfallSlice } from "@/lib/api";

// 시리즈 색 — 고정 배정(주중=파랑, 주말=주황), tokens.css의 --blue-50/--orange-39.
// dataviz 검증 스크립트로 CVD/대비 통과 확인(2026-07-19).
const SERIES = {
  weekday: { label: "주중", color: "#2563eb" },
  weekend: { label: "주말", color: "#d97706" },
} as const;

export function fmtManwon(value?: number | null): string {
  if (value === null || value === undefined) return "—";
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)}억`;
  return `${Math.round(value).toLocaleString()}만원`;
}

function fmtPeople(value: number): string {
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)}만명`;
  return `${Math.round(value).toLocaleString()}명`;
}

// 24시간 생활인구 곡선 — 주중/주말 2계열 라인 차트 (한 축, 얇은 2px 라인,
// 시간별 네이티브 <title> 툴팁, 범례 + 계열색 마커는 색이 아니라 텍스트 토큰
// 옆에 두는 방식).
function HourlyChart({ footfall }: { footfall: FootfallProfile }) {
  const W = 300;
  const H = 88;
  const PAD = { top: 6, right: 6, bottom: 16, left: 6 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const max = Math.max(...footfall.weekday_hourly, ...footfall.weekend_hourly, 1);
  const x = (h: number) => PAD.left + (h / 23) * plotW;
  const y = (v: number) => PAD.top + plotH - (v / max) * plotH;
  const path = (values: number[]) => values.map((v, h) => `${h === 0 ? "M" : "L"}${x(h).toFixed(1)},${y(v).toFixed(1)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="insights-chart" role="img" aria-label="시간대별 생활인구 (주중·주말)">
      {/* recessive 기준선 */}
      <line x1={PAD.left} y1={PAD.top + plotH} x2={W - PAD.right} y2={PAD.top + plotH} stroke="var(--line-normal-neutral)" strokeWidth="1" />
      {[0, 6, 12, 18, 23].map((h) => (
        <text key={h} x={x(h)} y={H - 4} textAnchor="middle" className="insights-chart-tick">{h}시</text>
      ))}
      <path d={path(footfall.weekday_hourly)} fill="none" stroke={SERIES.weekday.color} strokeWidth="2" strokeLinejoin="round" />
      <path d={path(footfall.weekend_hourly)} fill="none" stroke={SERIES.weekend.color} strokeWidth="2" strokeLinejoin="round" />
      {/* 시간별 히트 영역 + 네이티브 툴팁 */}
      {footfall.weekday_hourly.map((v, h) => (
        <rect key={h} x={x(h) - plotW / 46} y={0} width={plotW / 23} height={H} fill="transparent">
          <title>{`${h}시 — 주중 ${fmtPeople(v)} · 주말 ${fmtPeople(footfall.weekend_hourly[h] ?? 0)}`}</title>
        </rect>
      ))}
    </svg>
  );
}

// 세로 막대 미니 차트 (단일 색상 시리즈 + 막대 위 직접 라벨) — 연령대·요일
// 분포처럼 항목 수가 적은 분포에 쓴다. pct가 있으면 %, 없으면 값 축약 표기.
function SliceBars({ slices, color }: { slices: FootfallSlice[]; color: string }) {
  const W = 300;
  const H = 92;
  const PAD = { top: 14, bottom: 16 };
  const plotH = H - PAD.top - PAD.bottom;
  const max = Math.max(...slices.map((s) => s.value), 1);
  const slot = W / slices.length;
  const barW = Math.min(28, slot - 8);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="insights-chart" role="img">
      {slices.map((s, i) => {
        const h = Math.max(2, (s.value / max) * plotH);
        const x = slot * i + (slot - barW) / 2;
        const y = PAD.top + plotH - h;
        return (
          <g key={s.label}>
            <rect x={x} y={y} width={barW} height={h} rx={4} fill={color}>
              <title>{`${s.label} — ${fmtPeople(s.value)}${s.pct != null ? ` (${s.pct}%)` : ""}`}</title>
            </rect>
            <text x={x + barW / 2} y={y - 4} textAnchor="middle" className="insights-chart-tick">
              {s.pct != null ? `${Math.round(s.pct)}%` : fmtPeople(s.value)}
            </text>
            <text x={x + barW / 2} y={H - 4} textAnchor="middle" className="insights-chart-tick">{s.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

// 성별 분할 가로 막대 — 남(파랑)/여(주황), 2px 간격, 라벨은 텍스트 토큰.
function GenderBar({ malePct, femalePct }: { malePct: number; femalePct: number }) {
  return (
    <div className="insights-gender">
      <span className="insights-gender-label">남 {malePct}%</span>
      <div className="insights-gender-bar" role="img" aria-label={`남성 ${malePct}%, 여성 ${femalePct}%`}>
        <i style={{ width: `${malePct}%`, background: SERIES.weekday.color }} />
        <i style={{ width: `${femalePct}%`, background: SERIES.weekend.color }} />
      </div>
      <span className="insights-gender-label">여 {femalePct}%</span>
    </div>
  );
}

function BreakdownBlock({ breakdown }: { breakdown: FootfallBreakdown }) {
  return (
    <div className="insights-block">
      <p className="insights-block-title">
        유동인구 구성 <span className="site-panel-meta">({breakdown.adm_nm} · {breakdown.quarter} · 분기 {fmtPeople(breakdown.total)})</span>
      </p>
      {breakdown.male_pct != null && breakdown.female_pct != null && (
        <GenderBar malePct={breakdown.male_pct} femalePct={breakdown.female_pct} />
      )}
      {breakdown.ages.length > 0 && (
        <>
          <p className="insights-sub-title">연령대별</p>
          <SliceBars slices={breakdown.ages} color={SERIES.weekday.color} />
        </>
      )}
      {breakdown.days.length > 0 && (
        <>
          <p className="insights-sub-title">요일별</p>
          <SliceBars slices={breakdown.days} color={SERIES.weekday.color} />
        </>
      )}
      <p className="insights-source">{breakdown.source}</p>
    </div>
  );
}

// 분석 위치 주변의 아파트 실거래 시세 + 생활인구(유동·체류) — 입지 분석
// 결과 패널 전용 섹션. 데이터는 AppShell이 한 번만 불러 요약 타일과 공유한다.
export function AreaInsightsSection({ data, loading }: { data: AreaInsightsResponse | null; loading: boolean }) {
  if (!loading && !data?.apartment && !data?.footfall && !data?.footfall_breakdown) return null;

  const apt = data?.apartment;
  const footfall = data?.footfall;
  const breakdown = data?.footfall_breakdown;

  return (
    <div className="card">
      <h3>동네 시세 · 유동인구</h3>
      {loading && !data && <p className="site-panel-meta">불러오는 중…</p>}

      {apt && apt.median_per_pyeong_manwon != null && (
        <div className="insights-block">
          <p className="insights-block-title">
            {apt.scope_kind === "gu" ? "구 전체 아파트 시세" : "주변 아파트 시세"}{" "}
            <span className="site-panel-meta">({apt.scope} · 최근 1년 {apt.trade_count.toLocaleString()}건)</span>
          </p>
          <p className="insights-headline">평당 {fmtManwon(apt.median_per_pyeong_manwon)} <span className="site-panel-meta">(중앙값 · ㎡당 {fmtManwon(apt.median_per_sqm_manwon)})</span></p>
          {apt.scope_kind === "gu" && (
            /* 반경 안에 좌표가 찍힌 실거래가 없어 구 전체로 물러난 값이다.
               제목만 "주변"이라고 두면 이 동네 시세로 읽힌다 — 동네마다
               평당가가 배로 갈리는데 그대로 믿고 판단하게 된다. */
            <p className="site-panel-meta">
              이 반경 안에는 좌표가 있는 아파트 실거래가 없어 {apt.scope} 중앙값을 대신 보여 줍니다 — 이 동네 시세와 다를 수 있습니다.
            </p>
          )}
          {apt.recent.length > 0 && (
            <ul className="insights-trades">
              {apt.recent.map((t, i) => (
                <li key={`${t.bldg_nm}-${t.deal_date}-${i}`}>
                  <span className="insights-trade-name">{t.bldg_nm} {Math.round(t.area_sqm)}㎡{t.floor != null ? ` ${t.floor}층` : ""}</span>
                  <strong>{fmtManwon(t.deal_amount_manwon)}</strong>
                  <span className="site-panel-meta">{t.deal_date.slice(5)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {apt && apt.median_per_pyeong_manwon == null && (
        <p className="site-panel-meta">주변 아파트 실거래 데이터가 없습니다.</p>
      )}

      {footfall && (
        <div className="insights-block">
          <p className="insights-block-title">시간대별 생활인구 <span className="site-panel-meta">(반경 내 체류 추정)</span></p>
          <div className="insights-legend">
            <span><i className="insights-dot" style={{ background: SERIES.weekday.color }} />{SERIES.weekday.label}</span>
            <span><i className="insights-dot" style={{ background: SERIES.weekend.color }} />{SERIES.weekend.label}</span>
            {footfall.weekday_peak_hour != null && (
              <span className="site-panel-meta">주중 피크 {footfall.weekday_peak_hour}시 · {fmtPeople(footfall.weekday_hourly[footfall.weekday_peak_hour] ?? 0)}</span>
            )}
          </div>
          <HourlyChart footfall={footfall} />
          <p className="insights-source">{footfall.source}</p>
        </div>
      )}

      {breakdown && <BreakdownBlock breakdown={breakdown} />}
    </div>
  );
}
