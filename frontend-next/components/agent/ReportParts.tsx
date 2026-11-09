"use client";

import { useEffect, useState, type ReactNode } from "react";
import { buildEvidenceTree, type EvidenceNode } from "@/lib/evidence";
import { bandPosition, clampPct, type VerdictTone } from "@/lib/agentFormat";

// 판정문 다섯 화면(비교·계약·매입·진단·장소 찾기)이 함께 쓰는 조각들.
// 결론 → 숫자 → 근거 순서와 색 규칙을 한 곳에서만 정의한다.

// --- 로딩: 10~20초 걸리는 요청이라 빈 스피너 대신 진행 단계를 보여준다. ---
const STAGE_MS = 4500;

export function ReportLoading({ title, subtitle, stages }: { title: string; subtitle: string; stages: string[] }) {
  const [stage, setStage] = useState(0);
  useEffect(() => {
    setStage(0);
    const timer = window.setInterval(() => setStage((s) => Math.min(s + 1, stages.length - 1)), STAGE_MS);
    return () => window.clearInterval(timer);
  }, [stages.length]);
  return (
    <div className="agent-stage">
      <div className="card agent-card" role="status" aria-live="polite">
        <h2 className="agent-title">{title}</h2>
        <p className="agent-subtitle">{subtitle}</p>
        <ol className="verdict-stages">
          {stages.map((label, index) => (
            <li key={label} className={`verdict-stage${index < stage ? " verdict-stage--done" : ""}${index === stage ? " verdict-stage--active" : ""}`}>
              <span className="verdict-stage-dot" aria-hidden="true">{index < stage ? "✓" : "•"}</span>
              {label}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

/** 실패 화면 — 백엔드가 아직 없거나(404) 죽어도 여기서 멈추고 되돌아갈 수 있다. */
export function ReportError({ title, error, onRetry, onRestart }: { title: string; error: string; onRetry: () => void; onRestart: () => void }) {
  return (
    <div className="agent-stage">
      <div className="card agent-card">
        <h2 className="agent-title">{title}</h2>
        <p className="status-banner status-banner--error">{error}</p>
        <div className="agent-step-actions">
          <button type="button" className="primary-button primary-button--compact" onClick={onRetry}>다시 시도</button>
          <button type="button" className="sim-btn" onClick={onRestart}>조건 수정</button>
        </div>
      </div>
    </div>
  );
}

// --- 근거: evidence는 스키마가 없으므로 재귀 렌더만 한다 (lib/evidence 참조). ---
function EvidenceNodes({ nodes }: { nodes: EvidenceNode[] }) {
  return (
    <ul className="evidence-list">
      {nodes.map((node) => (
        <li key={node.path} className="evidence-item">
          {node.kind === "leaf" ? (
            <div className="evidence-row">
              <span className="evidence-key">{node.label}</span>
              <span className="evidence-value">{node.value}</span>
            </div>
          ) : (
            <>
              <span className="evidence-group-label">{node.label}</span>
              <EvidenceNodes nodes={node.children} />
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

export function EvidenceDetails({ evidence }: { evidence: unknown }) {
  const nodes = buildEvidenceTree(evidence);
  return (
    <details className="evidence-details">
      <summary>근거 자세히 보기</summary>
      {nodes.length > 0 ? <EvidenceNodes nodes={nodes} /> : <p className="agent-step-hint">표시할 근거 데이터가 없습니다.</p>}
    </details>
  );
}

export function PointList({ title, items, tone }: { title: string; items: string[] | undefined | null; tone: string }) {
  if (!Array.isArray(items) || items.length === 0) return null;
  return (
    <div className={`verdict-points verdict-points--${tone}`}>
      <span className="verdict-points-title">{title}</span>
      <ul>
        {items.map((text, index) => <li key={`${index}-${text}`}>{text}</li>)}
      </ul>
    </div>
  );
}

/** 좋은 점 · 위험 · 추천 행동 세 묶음 (있는 것만 나온다). */
export function PointsGrid({ strengths, risks, actions }: { strengths?: string[] | null; risks?: string[] | null; actions?: string[] | null }) {
  const empty = !strengths?.length && !risks?.length && !actions?.length;
  if (empty) return null;
  return (
    <div className="verdict-points-grid">
      <PointList title="좋은 점" items={strengths} tone="positive" />
      <PointList title="위험" items={risks} tone="caution" />
      <PointList title="추천 행동" items={actions} tone="info" />
    </div>
  );
}

export function VerdictHeadline({ tone, eyebrow = "결론", text, chip }: { tone: VerdictTone; eyebrow?: string; text: string; chip?: string | null }) {
  return (
    <div className={`verdict-headline verdict-headline--${tone}`} role="status">
      <span className="verdict-headline-eyebrow">{eyebrow}</span>
      <p className="verdict-headline-text">{text}</p>
      {chip ? <span className={`verdict-chip verdict-chip--${tone}`}>{chip}</span> : null}
    </div>
  );
}

/** 추천도·부담률처럼 0~100으로 읽는 값의 막대. */
export function ScoreBar({ label, score, note, tone }: { label: string; score: number | null | undefined; note?: string; tone: VerdictTone }) {
  const pct = clampPct(score);
  return (
    <div className="metric-bar-row">
      <div className="metric-bar-head">
        <span className="metric-bar-label">{label}</span>
        <span className="metric-bar-value"><strong>{Math.round(pct)}점</strong>{note ? <em>{note}</em> : null}</span>
      </div>
      <div className="metric-bar-track">
        <i style={{ width: `${pct}%`, background: `var(--verdict-${tone})` }} />
      </div>
    </div>
  );
}

/** 두 금액을 같은 자로 비교하는 막대 한 줄 (예상 매출 vs 손익분기). */
export function AmountBar({ label, valueText, ratio, tone }: { label: string; valueText: string; ratio: number; tone: VerdictTone }) {
  return (
    <div className="metric-bar-row">
      <div className="metric-bar-head">
        <span className="metric-bar-label">{label}</span>
        <span className="metric-bar-value"><strong>{valueText}</strong></span>
      </div>
      <div className="metric-bar-track">
        <i style={{ width: `${clampPct(ratio)}%`, background: `var(--verdict-${tone})` }} />
      </div>
    </div>
  );
}

/**
 * 적정 구간(밴드) 안에서 지금 값이 어디에 있는지 보여주는 막대.
 * 밴드가 없으면(표본 부족 등) 아무것도 그리지 않는다.
 */
export function RangeBar({
  label, low, high, mid, value, format, lowLabel, highLabel, midLabel, valueLabel, note, tone,
}: {
  label: string;
  low: number | null | undefined;
  high: number | null | undefined;
  mid?: number | null;
  value: number | null | undefined;
  format: (value: number | null | undefined) => string;
  lowLabel?: string;
  highLabel?: string;
  midLabel?: string;
  valueLabel?: string;
  note?: ReactNode;
  tone: VerdictTone;
}) {
  const pos = bandPosition(value, low, high);
  if (!pos) return null;
  const midPos = typeof mid === "number" && Number.isFinite(mid) ? bandPosition(mid, low, high) : null;
  return (
    <div className="range-bar">
      <div className="metric-bar-head">
        <span className="metric-bar-label">{label}</span>
        {pos.valuePct >= 0 && <span className="metric-bar-value"><strong>{format(value)}</strong>{valueLabel ? <em>{valueLabel}</em> : null}</span>}
      </div>
      <div className="range-bar-track">
        <i className={`range-bar-band range-bar-band--${tone}`} style={{ left: `${pos.lowPct}%`, width: `${Math.max(1, pos.highPct - pos.lowPct)}%` }} />
        {midPos && <i className="range-bar-tick" style={{ left: `${midPos.valuePct}%` }} />}
        {pos.valuePct >= 0 && <i className={`range-bar-marker range-bar-marker--${tone}`} style={{ left: `${pos.valuePct}%` }} aria-hidden="true" />}
      </div>
      <div className="range-bar-scale">
        <span>{lowLabel ?? format(low)}</span>
        {midLabel ? <span className="range-bar-scale-mid">{midLabel}</span> : null}
        <span>{highLabel ?? format(high)}</span>
      </div>
      {note ? <p className="range-bar-note">{note}</p> : null}
    </div>
  );
}

/** 세 개 안팎의 핵심 숫자를 나란히 놓는 타일. 값이 없으면 "—"로 자리를 지킨다. */
export function TileRow({ tiles }: { tiles: { label: string; value: string; hint?: string; tone?: VerdictTone }[] }) {
  if (tiles.length === 0) return null;
  return (
    <div className="verdict-tiles">
      {tiles.map((tile) => (
        <div key={tile.label} className={`verdict-tile${tile.tone ? ` verdict-tile--${tile.tone}` : ""}`}>
          <span className="verdict-tile-label">{tile.label}</span>
          <strong className="verdict-tile-value">{tile.value}</strong>
          {tile.hint ? <span className="verdict-tile-hint">{tile.hint}</span> : null}
        </div>
      ))}
    </div>
  );
}

export function WarningsCard({ warnings }: { warnings: string[] | null | undefined }) {
  if (!Array.isArray(warnings) || warnings.length === 0) return null;
  return (
    <div className="card">
      <ul className="warnings-banner">
        {warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}
      </ul>
    </div>
  );
}

/** 결과 화면 맨 아래 「조건 바꿔서 다시」 줄. */
export function RestartRow({ label, onRestart }: { label: string; onRestart: () => void }) {
  return (
    <div className="agent-step-actions">
      <button type="button" className="sim-btn" onClick={onRestart}>{label}</button>
    </div>
  );
}
