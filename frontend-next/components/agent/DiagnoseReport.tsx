"use client";

import { AmountBar, EvidenceDetails, PointList, ReportError, ReportLoading, RestartRow, VerdictHeadline, WarningsCard } from "@/components/agent/ReportParts";
import { barWidth, fmtMan, fmtSignedPct, hasNumber, toneForSeverity } from "@/lib/agentFormat";
import type { DiagnoseCause, DiagnoseRequest, DiagnoseResponse } from "@/lib/api";

const STAGES = ["매장 주변 수요를 읽는 중…", "같은 업종 매출과 비교 중…", "원인 순위를 정리 중…"];

const SEVERITY_LABEL: Record<string, string> = { high: "큰 원인", medium: "보통 원인", low: "작은 원인" };

function CauseItem({ cause, index }: { cause: DiagnoseCause; index: number }) {
  const tone = toneForSeverity(cause.severity);
  return (
    <li className={`cause-item cause-item--${tone}`}>
      <div className="cause-head">
        <span className="cause-rank">{index + 1}</span>
        <strong className="cause-label">{cause.label || "원인"}</strong>
        <span className={`verdict-chip verdict-chip--${tone}`}>{SEVERITY_LABEL[String(cause.severity)] ?? "원인"}</span>
      </div>
      {cause.finding && <p className="cause-finding">{cause.finding}</p>}
      {cause.action && <p className="cause-action"><span>이렇게 해보세요</span> {cause.action}</p>}
    </li>
  );
}

export function DiagnoseReport({
  loading, error, result, request, onRetry, onRestart,
}: {
  loading: boolean;
  error: string;
  result: DiagnoseResponse | null;
  request: DiagnoseRequest | null;
  onRetry: () => void;
  onRestart: () => void;
}) {
  if (loading) return <ReportLoading title="왜 그런지 보고 있어요" subtitle="15초 정도 걸립니다." stages={STAGES} />;
  if (error) return <ReportError title="원인을 찾지 못했어요" error={error} onRetry={onRetry} onRestart={onRestart} />;
  if (!result) return null;

  const current = request?.current_monthly_sales_man ?? null;
  const expected = result.expected_monthly_sales_man;
  const gapPct = result.gap_pct;
  // 격차가 클수록 붉게 — 판정값이 따로 없으므로 격차 자체로 색을 정한다.
  const tone = !hasNumber(gapPct) ? "info" : (gapPct as number) <= -40 ? "negative" : (gapPct as number) <= -15 ? "caution" : "positive";
  const causes = Array.isArray(result.causes) ? result.causes : [];
  const scaleMax = Math.max(current ?? 0, expected ?? 0);

  return (
    <div className="agent-stage agent-stage--wide">
      <div className="verdict-report">
        <VerdictHeadline tone={tone} text={result.headline || "진단 결과가 준비됐습니다."} chip={result.verdict_label ?? undefined} />

        {/* 먼저 격차부터 — 얼마를 못 벌고 있는지가 이 화면의 질문이다. */}
        <div className={`card gap-hero gap-hero--${tone}`}>
          <span className="gap-hero-eyebrow">이 자리에서 나와야 할 매출과의 차이</span>
          <div className="gap-hero-figures">
            <div className="gap-hero-figure">
              <span>이 자리 기준</span>
              <strong>{fmtMan(expected)}</strong>
            </div>
            <span className="gap-hero-vs" aria-hidden="true">vs</span>
            <div className="gap-hero-figure">
              <span>지금 매출</span>
              <strong>{fmtMan(current)}</strong>
            </div>
            {hasNumber(gapPct) && <span className={`gap-hero-delta gap-hero-delta--${tone}`}>{fmtSignedPct(gapPct)}</span>}
          </div>
          {hasNumber(result.gap_man) && (
            <p className="gap-hero-note">
              {(result.gap_man as number) < 0
                ? <>매달 <strong>{fmtMan(Math.abs(result.gap_man as number))}</strong>을 덜 벌고 있습니다.</>
                : <>매달 <strong>{fmtMan(result.gap_man)}</strong>을 더 벌고 있습니다.</>}
            </p>
          )}
          {scaleMax > 0 && (
            <>
              <AmountBar label="이 자리 기준 매출" valueText={fmtMan(expected)} ratio={barWidth(expected, scaleMax)} tone="info" />
              <AmountBar label="지금 매출" valueText={fmtMan(current)} ratio={barWidth(current, scaleMax)} tone={tone} />
            </>
          )}
        </div>

        <div className="card report-block">
          <h3 className="report-block-title">원인은 이 순서입니다</h3>
          {causes.length > 0 ? (
            <ol className="cause-list">
              {causes.map((cause, index) => <CauseItem key={cause.key ?? `${index}-${cause.label}`} cause={cause} index={index} />)}
            </ol>
          ) : (
            <p className="agent-step-hint">뚜렷한 원인을 찾지 못했습니다. 매출·위치를 다시 확인해 주세요.</p>
          )}
        </div>

        {Array.isArray(result.strengths) && result.strengths.length > 0 && (
          <div className="card report-block">
            <PointList title="그래도 좋은 점" items={result.strengths} tone="positive" />
          </div>
        )}

        <div className="card report-block">
          <EvidenceDetails evidence={result.evidence} />
        </div>

        <WarningsCard warnings={result.warnings} />

        <RestartRow label="조건 바꿔서 다시 보기" onRestart={onRestart} />
      </div>
    </div>
  );
}
