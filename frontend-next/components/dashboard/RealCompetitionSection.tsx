"use client";

import { useState } from "react";
import type {
  AcademyClusterBucket,
  AcademyClusterResponse,
  ClinicSubject,
  CompetitionBucket,
  ModelFitResponse,
  RealCompetitionResponse,
} from "@/lib/api";
import {
  ACADEMY_SUBJECTS,
  COMPETITION_MINUTES_BY_MODE,
  COMPETITION_MODE_LABELS,
  competitionReachLabel,
  intensityClass,
  modelVerdictClass,
  scoreBarLevel,
  VERTICAL_UI,
} from "./leaseUtils";

const PAGE_SIZE = 6;

export interface ClinicExtras {
  has_parking: boolean;
  has_elevator: boolean;
  has_pharmacy_nearby: boolean;
  floor_level: number | null;
}

// Most-frequently-selected specialties for a clinic-vertical business
// (matches the codes verified against the live GET /api/clinic-subjects
// response, not the SBIZ Q-code labels — e.g. 이비인후과=13/안과=12,
// not the other way around). Order controls the "default visible" chip row;
// everything else is behind "더보기".
const COMMON_CLINIC_SUBJECT_CODES = [
  "01", // 내과
  "11", // 소아청소년과
  "05", // 정형외과
  "14", // 피부과
  "13", // 이비인후과
  "12", // 안과
  "10", // 산부인과
  "03", // 정신건강의학과
  "21", // 재활의학과
  "23", // 가정의학과
  "04", // 외과
  "09", // 마취통증의학과
];

// Ported from `renderRealCompetitionZoneControls` in frontend/js/lease_analysis.js.
export function ReachControls({
  mode,
  minutes,
  onChange,
}: {
  mode: string;
  minutes: number;
  onChange: (mode: string, minutes: number) => void;
}) {
  const minuteOpts = COMPETITION_MINUTES_BY_MODE[mode] || COMPETITION_MINUTES_BY_MODE.walk;
  return (
    <div className="real-comp-reach-controls">
      <div className="dash-mode-toggles" role="group" aria-label="경쟁 권역 이동 수단">
        {["walk", "drive", "moto"].map((m) => (
          <button
            key={m}
            type="button"
            className={`dash-mode-btn${m === mode ? " dash-mode-btn--active" : ""}`}
            // Keep the current minutes when switching mode if the new mode
            // allows it; otherwise fall back to the 5-minute default (not the
            // first option, which is now the fine-grained 3-minute band).
            onClick={() => onChange(m, COMPETITION_MINUTES_BY_MODE[m].includes(minutes) ? minutes : 5)}
          >
            {COMPETITION_MODE_LABELS[m]}
          </button>
        ))}
      </div>
      <div className="dash-minute-toggles" role="group" aria-label="경쟁 권역 시간">
        {minuteOpts.map((m) => (
          <button
            key={m}
            type="button"
            className={`dash-minute-btn${m === minutes ? " dash-minute-btn--active" : ""}`}
            onClick={() => onChange(mode, m)}
          >
            {m}분
          </button>
        ))}
      </div>
      <p className="demand-meta">도로망 기준 {COMPETITION_MODE_LABELS[mode]} {minutes}분 도달권 안의 경쟁 점포를 집계합니다.</p>
    </div>
  );
}

// Ported from `renderFoodAnalysisKpiSummary`.
export function FoodAnalysisKpiSummary({
  competition,
  modelFit,
  mode,
  minutes,
}: {
  competition: RealCompetitionResponse | null | undefined;
  modelFit: ModelFitResponse | null | undefined;
  mode: string;
  minutes: number;
}) {
  if (!competition && !modelFit) return null;
  const buckets = (competition?.buckets || []).filter((b) => b.count > 0);
  const totalStores = buckets.reduce((sum, b) => sum + b.count, 0);
  const direct = buckets.find((b) => b.key === "direct");
  const topModel = [...(modelFit?.models || [])].sort((a, b) => b.score - a.score)[0];
  const reach = competition?.reach_label || competitionReachLabel(mode, minutes);
  const max = Math.max(...buckets.map((b) => b.count), 1);

  return (
    <>
      <div className="commercial-summary food-analysis-kpi">
        <div className="metric-card">
          <div className="metric-title">경쟁 점포</div>
          <div className="metric-value">{totalStores.toLocaleString("ko-KR")}</div>
          <div className="metric-detail">{competition?.total_intensity || "—"} · {reach} 권역</div>
        </div>
        <div className="metric-card">
          <div className="metric-title">직접 경쟁</div>
          <div className="metric-value">{direct ? direct.count.toLocaleString("ko-KR") : "0"}</div>
          <div className="metric-detail">{direct ? `${direct.intensity} · ${direct.label}` : "동일·유사 업종 없음"}</div>
        </div>
        <div className="metric-card metric-card--accent">
          <div className="metric-title">모델 적합</div>
          <div className="metric-value">{topModel ? `${topModel.score}점` : "—"}</div>
          <div className="metric-detail">{topModel ? `${topModel.label} · ${topModel.verdict}` : "모델 적합도 없음"}</div>
        </div>
      </div>
      {buckets.length > 0 && (
        <div className="food-analysis-density">
          <p className="demand-meta">경쟁 유형별 점포 수</p>
          <div className="density-bars">
            {buckets.slice(0, 6).map((b) => (
              <div className="density-bar-row" key={b.key}>
                <span className="density-label">{b.label}</span>
                <div className="density-bar-track"><div className="density-bar-fill" style={{ width: `${Math.round((b.count / max) * 100)}%` }} /></div>
                <span className="density-count">{b.count}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

function BucketRow({ bucket, items }: { bucket: { key: string; label: string; intensity: string; count: number }; items: { name: string; distance_m: number; subjects?: string[]; specialist_count?: number | null }[] }) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const slice = items.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  return (
    <li className="real-comp-row real-comp-row--expandable">
      <button type="button" className="real-comp-row-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="real-comp-label">{bucket.label}</span>
        <span className={`real-comp-meta ${intensityClass(bucket.intensity)}`}>{bucket.intensity} · {bucket.count}곳</span>
      </button>
      {open && (
        <div className="real-comp-brand-panel">
          <ul className="real-comp-brand-list">
            {slice.length ? slice.map((n, i) => (
              <li className="real-comp-brand-item" key={`${n.name}-${i}`}>
                <div className="real-comp-brand-info">
                  <strong>{n.name}</strong>
                  {n.subjects && n.subjects.length > 0 && (
                    <span className="real-comp-brand-subjects">
                      {n.subjects.join("·")}
                      {n.specialist_count ? ` · 전문의 ${n.specialist_count}명` : ""}
                    </span>
                  )}
                </div>
                <span>{Math.round(n.distance_m)}m</span>
              </li>
            )) : <li className="demand-meta">브랜드 목록 없음</li>}
          </ul>
          {pageCount > 1 && (
            <nav className="real-comp-brand-pager">
              <button type="button" className="dash-page-btn" disabled={safePage === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>이전</button>
              <span className="dash-page-info">{safePage + 1}/{pageCount}</span>
              <button type="button" className="dash-page-btn" disabled={safePage >= pageCount - 1} onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}>다음</button>
            </nav>
          )}
          {bucket.count > items.length && <p className="demand-meta">가까운 {items.length}곳 표시 · 총 {bucket.count}곳</p>}
        </div>
      )}
    </li>
  );
}

// Ported from `renderRealCompetitionSection`.
export function CompetitionSection({
  competition,
  mode,
  minutes,
  modelFit,
  onReachChange,
  warnings,
}: {
  competition: RealCompetitionResponse;
  mode: string;
  minutes: number;
  modelFit: ModelFitResponse | null | undefined;
  onReachChange: (mode: string, minutes: number) => void;
  warnings?: string[];
}) {
  const ui = VERTICAL_UI[competition.vertical] || VERTICAL_UI.restaurant;
  const buckets = (competition.buckets || []).filter((b: CompetitionBucket) => b.count > 0);
  return (
    <section className="dash-card dash-card--real-comp" data-dash="real-competition">
      <header className="dash-card-header">
        <span className="dash-card-icon" aria-hidden="true">{ui.icon}</span>
        <h3>{ui.compTitle}</h3>
        <span className={`dash-card-badge ${intensityClass(competition.total_intensity)}`}>{competition.total_intensity}</span>
      </header>
      <ReachControls mode={mode} minutes={minutes} onChange={onReachChange} />
      <FoodAnalysisKpiSummary competition={competition} modelFit={modelFit} mode={mode} minutes={minutes} />
      <p className="dash-strategy">{competition.summary_one_liner}</p>
      <p className="demand-meta">경쟁 유형을 누르면 브랜드·점포별 목록을 볼 수 있습니다.</p>
      <ul className="real-comp-list real-comp-list--expandable">
        {buckets.length ? buckets.map((b) => (
          <BucketRow
            key={b.key}
            bucket={b}
            items={(b.nearest || []).map((n) => ({ name: n.name, distance_m: n.distance_m, subjects: n.subjects, specialist_count: n.specialist_count }))}
          />
        )) : <li className="demand-meta">집계된 경쟁 없음</li>}
      </ul>
      {(warnings || []).map((w, i) => <p key={i} className="demand-meta dash-iso-warn">{w}</p>)}
    </section>
  );
}

// Ported from `renderAcademyScoreBars`.
function AcademyScoreBars({ scores }: { scores: AcademyClusterResponse["scores"] }) {
  const items = [
    { key: "pressure", label: "직접 경쟁 압박", value: scores.direct_competition_pressure, invert: true },
    { key: "synergy", label: "학원가 시너지", value: scores.cluster_synergy, invert: false },
    { key: "safety", label: "학부모 안심도", value: scores.parent_safety, invert: false },
  ];
  return (
    <div className="academy-score-bars">
      {items.map((item) => {
        const lvl = scoreBarLevel(item.value, item.invert);
        const pct = Math.round(Math.min(100, Math.max(0, item.value)));
        return (
          <div className={`academy-score-row ${lvl.cls}`} key={item.key}>
            <div className="academy-score-head"><span>{item.label}</span><strong>{lvl.label} · {pct}</strong></div>
            <div className="academy-score-track"><div className="academy-score-fill" style={{ width: `${pct}%` }} /></div>
          </div>
        );
      })}
    </div>
  );
}

// Ported from `renderAcademyClusterSection`.
export function AcademyClusterSection({
  cluster,
  mode,
  minutes,
  onReachChange,
}: {
  cluster: AcademyClusterResponse;
  mode: string;
  minutes: number;
  onReachChange: (mode: string, minutes: number) => void;
}) {
  const rows = (cluster.clusters || []).filter((b: AcademyClusterBucket) => b.count > 0);
  const safety = cluster.parent_safety_detail;
  const safetyRisks = [...(safety?.same_building_risks ?? []), ...(safety?.nearby_risks ?? [])];
  return (
    <section className="dash-card dash-card--academy-cluster" data-dash="academy-cluster">
      <header className="dash-card-header">
        <span className="dash-card-icon" aria-hidden="true">🎓</span>
        <h3>학원 클러스터 분석 ({cluster.target_label} 기준)</h3>
      </header>
      <ReachControls mode={mode} minutes={minutes} onChange={onReachChange} />
      <p className="dash-strategy">{cluster.headline}</p>
      <AcademyScoreBars scores={cluster.scores} />
      <p className="demand-meta academy-safety-note">
        학부모 안심도: <strong>{safety?.level || "—"}</strong>
        {safety?.same_building_risks?.length ? ` · 동일건물 유해업소 ${safety.same_building_risks.length}곳` : " · 동일건물 유해업소 없음"}
        {safety?.nearby_risks?.length ? ` · 인근 유해업소 ${safety.nearby_risks.length}곳` : ""}
      </p>
      <p className="demand-meta">{safety?.sensitivity_note || ""}</p>
      {safetyRisks.length > 0 && (
        <ul className="real-comp-list">
          {safetyRisks.map((r, i) => (
            <li key={i} className="demand-meta">{r}</li>
          ))}
        </ul>
      )}
      <ul className="real-comp-list real-comp-list--expandable">
        {rows.length ? rows.map((b) => (
          <BucketRow
            key={b.key}
            bucket={b}
            items={(b.stores || []).map((n) => ({ name: `${n.name}${n.same_building ? " (동일건물)" : ""}`, distance_m: n.distance_m }))}
          />
        )) : <li className="demand-meta">집계된 클러스터 없음</li>}
      </ul>
      {(cluster.interpretation || []).map((p, i) => <p key={i} className="dash-strategy">{p}</p>)}
      {(cluster.warnings || []).map((w, i) => <p key={i} className="demand-meta dash-iso-warn">{w}</p>)}
    </section>
  );
}

// Ported from `renderModelFitSection`.
export function ModelFitSection({ modelFit }: { modelFit: ModelFitResponse }) {
  const ui = VERTICAL_UI[modelFit.vertical] || VERTICAL_UI.restaurant;
  return (
    <section className="dash-card dash-card--model-fit" data-dash="model-fit">
      <header className="dash-card-header">
        <span className="dash-card-icon" aria-hidden="true">{ui.icon}</span>
        <h3>{ui.fitTitle}</h3>
      </header>
      <p className="dash-strategy">{modelFit.headline}</p>
      {modelFit.reach_label && <p className="demand-meta">분석 권역: {modelFit.reach_label}</p>}
      <ul className="model-fit-list">
        {modelFit.models.map((item) => (
          <li className={`model-fit-row ${modelVerdictClass(item.verdict)}`} key={item.model_id}>
            <div className="model-fit-head"><strong>{item.label}</strong><span>{item.verdict} · {item.score}점</span></div>
            <p className="model-fit-detail">{item.detail}</p>
          </li>
        ))}
      </ul>
      {(modelFit.warnings || []).map((w, i) => <p key={i} className="demand-meta dash-iso-warn">{w}</p>)}
    </section>
  );
}

// Ported from `renderClinicExtrasForm`/`readClinicExtras`.
export function ClinicExtrasForm({ value, onChange }: { value: ClinicExtras; onChange: (next: ClinicExtras) => void }) {
  const [floorText, setFloorText] = useState(value.floor_level != null ? String(value.floor_level) : "");
  return (
    <details className="clinic-extras" open>
      <summary>병원 운영 조건 (선택)</summary>
      <div className="lease-form-grid clinic-extras-grid">
        <label>
          층수
          <input
            type="number"
            min={1}
            max={50}
            placeholder="예: 3"
            value={floorText}
            onChange={(e) => setFloorText(e.target.value)}
            onBlur={() => onChange({ ...value, floor_level: floorText === "" ? null : Number(floorText) })}
          />
        </label>
        <label><input type="checkbox" checked={value.has_parking} onChange={(e) => onChange({ ...value, has_parking: e.target.checked })} /> 주차 가능</label>
        <label><input type="checkbox" checked={value.has_elevator} onChange={(e) => onChange({ ...value, has_elevator: e.target.checked })} /> 엘리베이터</label>
        <label><input type="checkbox" checked={value.has_pharmacy_nearby} onChange={(e) => onChange({ ...value, has_pharmacy_nearby: e.target.checked })} /> 인근 약국 있음</label>
      </div>
    </details>
  );
}

// Single-select 진료과목 chip picker — narrows the clinic direct-competition
// bucket (and lease benchmark) from the ambiguous SBIZ Q-code (e.g. Q10202
// lumps 외과+정형외과) down to one HIRA subject_code. "전체" (no filter, the
// default) keeps existing Q-code-derived behavior.
export function ClinicSubjectPicker({
  options,
  value,
  onChange,
  loading,
  error,
}: {
  options: ClinicSubject[];
  value: string[] | null;
  onChange: (next: string[] | null) => void;
  loading?: boolean;
  error?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const common = COMMON_CLINIC_SUBJECT_CODES
    .map((code) => options.find((o) => o.subject_code === code))
    .filter((o): o is ClinicSubject => Boolean(o));
  const rest = options.filter((o) => !COMMON_CLINIC_SUBJECT_CODES.includes(o.subject_code));
  const shown = expanded ? [...common, ...rest] : common;
  const selected = value && value.length ? value[0] : null;

  return (
    <section className="dash-section clinic-subject-picker">
      <label className="business-picker-label">진료과목 선택</label>
      {loading && <p className="demand-meta">진료과목 목록을 불러오는 중...</p>}
      {error && <p className="demand-meta">{error}</p>}
      {!loading && !error && options.length > 0 && (
        <>
          <div className="clinic-subject-chip-row">
            <button
              type="button"
              className={`clinic-subject-chip${selected === null ? " clinic-subject-chip--active" : ""}`}
              onClick={() => onChange(null)}
            >
              전체
            </button>
            {shown.map((opt) => (
              <button
                type="button"
                key={opt.subject_code}
                className={`clinic-subject-chip${selected === opt.subject_code ? " clinic-subject-chip--active" : ""}`}
                onClick={() => onChange([opt.subject_code])}
              >
                {opt.subject_name}
              </button>
            ))}
            {!expanded && rest.length > 0 && (
              <button type="button" className="clinic-subject-more-btn" onClick={() => setExpanded(true)}>
                더보기 (+{rest.length})
              </button>
            )}
          </div>
          <p className="demand-meta">
            과목을 선택하면 HIRA 진료과목 기준으로 직접 경쟁 병의원을 더 정확히 집계합니다. 선택하지 않으면(전체) 기존 업종 기준 집계를 사용합니다.
          </p>
        </>
      )}
    </section>
  );
}

// Ported from `academy-subject` select in `renderBusinessPickerUI` (the part
// lease_analysis.js's `loadRealCompetitionCards` depends on being set before
// it will fetch academy cluster/synergy data).
export function AcademySubjectPrompt({ value, onChange }: { value: string | null; onChange: (next: string | null) => void }) {
  return (
    <section className="dash-section">
      <label className="business-picker-label" htmlFor="academy-subject">학원 과목 <span className="required-mark">*</span></label>
      <select
        id="academy-subject"
        className="academy-subject-select"
        value={value || ""}
        onChange={(e) => onChange(e.target.value || null)}
      >
        {ACADEMY_SUBJECTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
      </select>
      {!value && <p className="upjong-subject-hint">과목을 선택해야 학원 클러스터·시너지 분석을 실행할 수 있습니다.</p>}
    </section>
  );
}
